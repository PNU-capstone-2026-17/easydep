"""Regression tests for the read-only revision planning boundary."""

from __future__ import annotations

from copy import deepcopy

from app.workspace.conversation.contracts import RevisionInterpretation, RevisionTarget
from app.workspace.conversation.revision_planner import RevisionPlanner


def _target(ref: str, kind: str, owner: str, version: int = 1) -> RevisionTarget:
    return RevisionTarget(
        ref=ref,
        kind=kind,
        element_id=ref.partition(":")[2],
        owner=owner,  # type: ignore[arg-type]
        artifact_type=f"{owner.upper()}_ARTIFACT",
        artifact_version_id=version,
        display_label=ref,
    )


class _Tools:
    """A minimal read-only catalog/trace fixture with a write tripwire."""

    app_id = "11111111-1111-4111-8111-111111111111"

    def __init__(self) -> None:
        self.targets = {
            "class_diagram:Order": _target("class_diagram:Order", "class", "design", 21),
            "file:src/order.py": _target("file:src/order.py", "file", "implementation", 31),
            "requirement:REQ-1": _target("requirement:REQ-1", "requirement", "requirements", 11),
            "api_spec:createOrder": _target("api_spec:createOrder", "api", "design", 23),
        }
        self.versions = {"CLASS": 21, "SOURCE_CODE": 31, "REQUIREMENTS": 11, "API": 23}
        self.trace_digest = "a" * 64
        self.links: list[dict[str, str]] = []
        self.relations: dict[str, dict[str, list[str]]] = {
            "class_diagram:Order": {"upstream": [], "downstream": ["api_spec:createOrder"]},
            "file:src/order.py": {"upstream": [], "downstream": []},
            "requirement:REQ-1": {"upstream": [], "downstream": ["class_diagram:Order"]},
            "api_spec:createOrder": {"upstream": ["class_diagram:Order"], "downstream": []},
        }
        self.write_calls = 0
        self.current_stage = "design"

    def read_workspace(self):
        return {"stage": self.current_stage}

    def normalize_revision_targets(self, refs):
        result = []
        for ref in refs:
            canonical = ref.ref if isinstance(ref, RevisionTarget) else str(ref)
            if canonical not in self.targets:
                raise ValueError(f"invalid revision targets: {canonical}")
            result.append(self.targets[canonical])
        if len({target.ref for target in result}) != len(result):
            raise ValueError("revision targets must resolve to unique canonical refs")
        return sorted(result, key=lambda target: target.ref)

    def revision_snapshot(self, *, refresh: bool = False):
        return {"artifact_versions": deepcopy(self.versions), "trace_digest": self.trace_digest}

    def revision_relations(self, targets):
        refs = [target.ref if isinstance(target, RevisionTarget) else str(target) for target in targets]
        return {
            **self.revision_snapshot(),
            "design_links": deepcopy(self.links),
            "relations": {ref: deepcopy(self.relations.get(ref, {"upstream": [], "downstream": []})) for ref in refs},
        }


def test_same_snapshot_and_interpretation_produce_identical_read_only_plan() -> None:
    tools = _Tools()
    planner = RevisionPlanner(tools)  # type: ignore[arg-type]
    intent = RevisionInterpretation(
        targets=["class_diagram:Order"], semantic_scope="contract", requested_effect="add a field"
    )

    first = planner.plan(intent)
    second = planner.plan(intent)

    assert first.plan_digest == second.plan_digest
    assert first.status == "ready_local"
    assert [target.ref for target in first.downstream_targets] == ["api_spec:createOrder"]
    assert tools.write_calls == 0


def test_plan_digest_freezes_the_exact_requested_effect() -> None:
    tools = _Tools()
    planner = RevisionPlanner(tools)  # type: ignore[arg-type]
    first_intent = RevisionInterpretation(
        targets=["class_diagram:Order"],
        semantic_scope="contract",
        requested_effect="Add a field named total.",
    )
    changed_intent = first_intent.model_copy(
        update={"requested_effect": "Remove the field named total."}
    )

    first = planner.plan(first_intent)
    changed = planner.plan(changed_intent)

    assert first.plan_digest != changed.plan_digest
    assert planner.validate_plan(first, first_intent) is True
    assert planner.validate_plan(first, changed_intent) is False


def test_invented_or_stale_target_is_never_an_authority_target() -> None:
    tools = _Tools()
    planner = RevisionPlanner(tools)  # type: ignore[arg-type]

    plan = planner.plan(
        RevisionInterpretation(
            targets=["class_diagram:Invented"], semantic_scope="contract", requested_effect="change"
        )
    )

    assert plan.status == "needs_clarification"
    assert plan.authority_targets == []
    assert plan.reason_codes == ["invalid_or_stale_target"]


def test_behavior_authority_ignores_a_raw_requirement_without_a_mutation_adapter() -> None:
    tools = _Tools()
    tools.relations["file:src/order.py"]["upstream"] = [
        "requirement:REQ-1",
        "api_spec:createOrder",
    ]
    planner = RevisionPlanner(tools)  # type: ignore[arg-type]

    plan = planner.plan(
        RevisionInterpretation(
            targets=["file:src/order.py"], semantic_scope="behavior", requested_effect="remove behavior"
        )
    )

    assert plan.status == "needs_confirmation"
    assert [target.ref for target in plan.upstream_candidates] == [
        "api_spec:createOrder",
    ]
    assert [target.ref for target in plan.authority_targets] == [
        "api_spec:createOrder",
    ]


def test_stage_order_does_not_invent_missing_upstream_authority() -> None:
    tools = _Tools()
    # Requirements exists and comes earlier in a delivery pipeline, but no
    # trace edge joins it to this file.
    planner = RevisionPlanner(tools)  # type: ignore[arg-type]

    plan = planner.plan(
        RevisionInterpretation(
            targets=["file:src/order.py"], semantic_scope="behavior", requested_effect="remove behavior"
        )
    )

    assert plan.status == "needs_clarification"
    assert plan.upstream_candidates == []
    assert plan.reason_codes == ["missing_exact_contract_link"]


def test_local_revision_of_an_earlier_delivery_stage_requires_confirmation() -> None:
    tools = _Tools()
    tools.current_stage = "implementation"

    plan = RevisionPlanner(tools).plan(  # type: ignore[arg-type]
        RevisionInterpretation(
            targets=["class_diagram:Order"],
            semantic_scope="contract",
            requested_effect="Add an operation.",
        )
    )

    assert plan.status == "needs_confirmation"
    assert "earlier_delivery_stage_requires_confirmation" in plan.reason_codes


def test_version_or_trace_change_makes_approved_plan_stale() -> None:
    tools = _Tools()
    planner = RevisionPlanner(tools)  # type: ignore[arg-type]
    plan = planner.plan(
        RevisionInterpretation(
            targets=["class_diagram:Order"], semantic_scope="contract", requested_effect="add field"
        )
    )
    assert planner.validate_plan(plan) is True

    tools.versions["CLASS"] = 22
    tools.trace_digest = "b" * 64

    assert planner.validate_plan(plan) is False
    assert planner.plan_is_stale(plan) is True
