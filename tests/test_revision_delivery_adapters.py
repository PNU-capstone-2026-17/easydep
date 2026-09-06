"""Delivery adapters keep one revision contract bounded across all stages."""

from __future__ import annotations

import pytest

from app.workspace.conversation.contracts import (
    RevisionInterpretation,
    RevisionPlan,
    RevisionTarget,
)
from app.workspace.conversation.delivery import (
    RevisionDeliveryError,
    design_revision_payload,
    implementation_revision_payload,
    repair_payload_from_testing_evidence,
    requirements_feedback_edit,
)
from app.workspace.conversation.revision_planner import RevisionPlanner


def _target(ref: str, kind: str, owner: str) -> RevisionTarget:
    return RevisionTarget(
        ref=ref,
        kind=kind,
        element_id=ref.partition(":")[2],
        owner=owner,  # type: ignore[arg-type]
        artifact_type=f"{owner}-artifact",
        artifact_version_id=1,
        display_label=ref,
    )


def _plan(
    owner: str,
    requested: list[RevisionTarget],
    authority: list[RevisionTarget] | None = None,
    downstream: list[RevisionTarget] | None = None,
) -> RevisionPlan:
    return RevisionPlan(
        plan_digest="a" * 64,
        status="ready_local",
        requested_targets=requested,
        authority_targets=authority if authority is not None else requested,
        upstream_candidates=[],
        downstream_targets=downstream or [],
        execution_mode="targeted_revision",
        reason_codes=["test"],
        explanation=f"{owner} plan",
        artifact_versions={"SOURCE": 1},
        trace_digest="b" * 64,
    )


def test_requirements_adapter_uses_catalog_kind_and_element_id_not_ref_parsing() -> None:
    target = _target("use_case:UC:with:colon", "use_case", "requirements")

    edit = requirements_feedback_edit([target], "Split the actor responsibilities.")

    assert edit.stage == "use_cases"
    assert edit.scope == "local"
    assert edit.target_ids == ["UC:with:colon"]


def test_requirements_adapter_rejects_mixed_modeling_stages() -> None:
    with pytest.raises(RevisionDeliveryError, match="one modeling stage"):
        requirements_feedback_edit(
            [
                _target("use_case:UC-1", "use_case", "requirements"),
                _target("use_case_spec:UC-1", "use_case_spec", "requirements"),
            ],
            "Change the model.",
        )


def test_requirements_broad_stage_adapter_requires_explicit_stage_authority() -> None:
    marker = _target("requirements_stage:actors", "requirements_stage", "requirements")

    edit = requirements_feedback_edit([marker], "Revise the actor model.")

    assert edit.stage == "actors"
    assert edit.scope == "broad"
    assert edit.target_ids == []


def test_design_adapter_carries_explicit_frozen_scope() -> None:
    authority = _target("class_diagram:Order", "class", "design")
    projection = _target("entity:Order", "entity", "design")
    payload = design_revision_payload(
        _plan("design", [authority], downstream=[projection]), "Add a status field."
    )

    assert payload.approved_authority_targets == ("class_diagram:Order",)
    assert payload.approved_downstream_targets == ("entity:Order",)
    revision = payload.batch_request().revisions[0]
    assert revision.target == "class_diagram:Order"
    assert revision.approved_authority_targets == ["class_diagram:Order"]
    assert revision.approved_downstream_targets == ["entity:Order"]


def test_design_adapter_accepts_a_cross_delivery_requested_projection() -> None:
    requested = _target("file:application/src/Order.java", "file", "implementation")
    authority = _target("class_diagram:Order", "class", "design")

    payload = design_revision_payload(
        _plan("design", [requested], authority=[authority]),
        "Change the linked contract.",
    )

    assert payload.revisions[0].target == authority.ref
    assert payload.approved_authority_targets == (authority.ref,)


def test_implementation_adapter_never_emits_a_broad_or_non_implementation_ref() -> None:
    payload = implementation_revision_payload(
        [
            _target("file:application/src/Order.java", "file", "implementation"),
            _target("task:implement-order", "task", "implementation"),
        ]
    )
    assert payload.confirmed_target_refs == (
        "file:application/src/Order.java",
        "task:implement-order",
    )

    with pytest.raises(RevisionDeliveryError, match="file or task"):
        implementation_revision_payload(
            [_target("api_spec:createOrder", "api", "design")]
        )


def test_testing_bridge_requires_implementation_owner_but_rtm_hints_are_optional() -> None:
    file_target = _target("file:application/src/Order.java", "file", "implementation")
    payload = repair_payload_from_testing_evidence(
        {
            "repair_owner": "implementation",
            "trace_refs": ["task:implement-order"],
            "file_hints": ["application/src/Order.java"],
        },
        [file_target],
    )
    assert payload.confirmed_target_refs == ("file:application/src/Order.java",)

    untraced = repair_payload_from_testing_evidence(
        {"repair_owner": "implementation", "trace_refs": [], "file_hints": []},
        [],
    )
    assert untraced.confirmed_target_refs == ()
    assert untraced.repair_file_hints == ()

    with pytest.raises(RevisionDeliveryError, match="assign repair"):
        repair_payload_from_testing_evidence(
            {"repair_owner": "testing", "trace_refs": [], "file_hints": []},
            [file_target],
        )


class _PlanningTools:
    app_id = "11111111-1111-4111-8111-111111111111"

    def __init__(self) -> None:
        self.targets = {
            "class_diagram:Order": _target("class_diagram:Order", "class", "design"),
            "entity:Order": _target("entity:Order", "entity", "design"),
            "test:case-1": _target("test:case-1", "test", "testing"),
            "requirement:REQ-1": _target("requirement:REQ-1", "requirement", "requirements"),
            "api_spec:createOrder": _target("api_spec:createOrder", "api", "design"),
        }
        self.direct: dict[str, list[str]] = {}
        self.downstream: dict[str, list[str]] = {}

    def normalize_revision_targets(self, refs, *, require_editable: bool = True):
        values = [ref.ref if isinstance(ref, RevisionTarget) else str(ref) for ref in refs]
        result = [self.targets[value] for value in values]
        if require_editable and any(target.ref.startswith(("entity:", "test:")) for target in result):
            raise ValueError("target is not editable")
        return result

    def revision_snapshot(self, *, refresh: bool = False):
        return {"artifact_versions": {"SOURCE": 1}, "trace_digest": "d" * 64}

    def revision_relations(self, targets):
        refs = [target.ref for target in targets]
        return {
            **self.revision_snapshot(),
            "design_links": [],
            "relations": {
                ref: {
                    "upstream": self.direct.get(ref, []),
                    "direct_upstream": self.direct.get(ref, []),
                    "downstream": self.downstream.get(ref, []),
                }
                for ref in refs
            },
        }


def test_planner_reports_noneditable_erd_as_downstream_but_never_authority() -> None:
    tools = _PlanningTools()
    tools.downstream["class_diagram:Order"] = ["entity:Order"]
    plan = RevisionPlanner(tools).plan(  # type: ignore[arg-type]
        RevisionInterpretation(
            targets=["class_diagram:Order"],
            semantic_scope="contract",
            requested_effect="Add a field.",
        )
    )

    assert [target.ref for target in plan.authority_targets] == ["class_diagram:Order"]
    assert [target.ref for target in plan.downstream_targets] == ["entity:Order"]


def test_planner_requires_class_confirmation_for_erd_projection_request() -> None:
    tools = _PlanningTools()
    tools.direct["entity:Order"] = ["class_diagram:Order"]
    plan = RevisionPlanner(tools).plan(  # type: ignore[arg-type]
        RevisionInterpretation(
            targets=["entity:Order"],
            semantic_scope="contract",
            requested_effect="Add a field.",
        )
    )

    assert plan.status == "needs_confirmation"
    assert [target.ref for target in plan.requested_targets] == ["entity:Order"]
    assert [target.ref for target in plan.authority_targets] == ["class_diagram:Order"]


def test_testing_expectation_uses_only_one_exact_requirement_or_api_source() -> None:
    tools = _PlanningTools()
    tools.direct["test:case-1"] = ["requirement:REQ-1"]
    unsupported = RevisionPlanner(tools).plan(  # type: ignore[arg-type]
        RevisionInterpretation(
            targets=["test:case-1"],
            semantic_scope="test_expectation",
            requested_effect="Expect the rejected order response.",
        )
    )
    assert unsupported.status == "unsupported"

    tools.direct["test:case-1"] = ["requirement:REQ-1", "api_spec:createOrder"]
    one = RevisionPlanner(tools).plan(  # type: ignore[arg-type]
        RevisionInterpretation(
            targets=["test:case-1"],
            semantic_scope="test_expectation",
            requested_effect="Expect the rejected order response.",
        )
    )
    assert one.status == "needs_confirmation"
    assert [target.ref for target in one.authority_targets] == [
        "api_spec:createOrder"
    ]

    tools.direct["test:case-1"] = []
    none = RevisionPlanner(tools).plan(  # type: ignore[arg-type]
        RevisionInterpretation(
            targets=["test:case-1"],
            semantic_scope="test_expectation",
            requested_effect="Expect the rejected order response.",
        )
    )
    assert none.status == "unsupported"
