from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.artifact_trace import ArtifactTrace, TraceNode, TraceRef
from app.workspace.conversation.contracts import RevisionTarget
from app.workspace.conversation.feedback_change_plan import (
    ArtifactSnapshotEntry,
    ChangePlanError,
    ExecutionAction,
    ExecutionUnit,
    ProjectionContract,
    RtmEvidence,
    RtmSnapshot,
    _change_set_digest,
    plan_change_set,
)
from app.workspace.conversation.feedback_envelope import (
    BaseRevision,
    Decision,
    DecisionMeaning,
    DecisionPayload,
    DecisionPolicy,
    Question,
    QuestionOption,
    answer_option,
)


def _target(ref: str, kind: str, owner: str, artifact_type: str) -> RevisionTarget:
    return RevisionTarget(
        ref=ref,
        kind=kind,
        element_id=ref.partition(":")[2],
        owner=owner,
        artifact_type=artifact_type,
        artifact_version_id=3,
        display_label=ref,
    )


def _question(target: RevisionTarget, *, category: str = "specification_gap") -> Question:
    return Question(
        question_id="q1",
        question_version=1,
        app_id="app",
        source_execution_id="run",
        detected_at={"stage": "design", "artifact_ref": "class:Enrollment"},
        base_revisions=[{"artifact_type": target.artifact_type, "version_id": 3}],
        trigger={"category": category, "finding_refs": ["finding:1"]},
        authority_candidates=[target],
        prompt="Clarify the requirement",
        allow_free_text=True,
        decision_policy=DecisionPolicy(
            allowed_semantic_scopes=("contract", "behavior"),
            allowed_change_types=("modify",),
        ),
    )


def _decision(question: Question):
    option = QuestionOption(
        option_id="apply",
        label="Apply",
        decision_payload=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="retain entry"
            ),
            authoritative_target_refs=(question.authority_candidates[0].ref,),
        ),
    )
    question = Question(**{**question.model_dump(), "options": (option,)})
    return question, answer_option(
        question, option_id="apply", decision_id="d1", source_user_message_id="m1"
    )


def _trace() -> RtmSnapshot:
    use_case = TraceRef("use_case_spec", "UC1")
    bundle = TraceRef("class", "Enrollment")
    sequence = TraceRef("sequence", "EnrollmentFlow")
    return RtmSnapshot(
        trace=ArtifactTrace(
            (
                TraceNode(use_case),
                TraceNode(bundle, (use_case,)),
                TraceNode(sequence, (bundle,)),
            )
        ),
        projection_contracts=(
            ProjectionContract(
                consumer=sequence,
                producer_refs=(bundle,),
                adapter="class_to_sequence",
                version="v1",
            ),
        ),
    )


def _catalog() -> tuple[RevisionTarget, ...]:
    return (
        _target("use_case_spec:UC1", "use_case_spec", "requirements", "usecase"),
        _target("class:Enrollment", "class", "design", "class_diagram"),
        _target("sequence:EnrollmentFlow", "sequence", "design", "sequence_diagram"),
    )


def _snapshot(
    catalog: tuple[RevisionTarget, ...] | None = None,
) -> tuple[ArtifactSnapshotEntry, ...]:
    return tuple(
        ArtifactSnapshotEntry(target=item, digest=str(index) * 64)
        for index, item in enumerate(catalog or _catalog(), start=1)
    )


def _recompute_plan_digest(payload: dict) -> None:
    units = tuple(ExecutionUnit.model_validate(item) for item in payload["execution_units"])
    artifacts = tuple(
        ArtifactSnapshotEntry.model_validate(item) for item in payload["artifact_snapshot"]
    )
    payload["plan_digest"] = _change_set_digest(
        app_id=payload["app_id"],
        question_id=payload["question_id"],
        question_version=payload["question_version"],
        decision_id=payload["decision_id"],
        decision_digest=payload["decision_digest"],
        revisions=tuple(BaseRevision.model_validate(item) for item in payload["base_revisions"]),
        owner=RevisionTarget.model_validate(payload["authoritative_owner"]),
        trace=RtmSnapshot.model_validate(payload["pre_change_trace"]),
        impact=tuple(TraceRef(**item) for item in payload["pre_change_impact"]),
        units=units,
        artifact_snapshot=artifacts,
    )


def test_specification_gap_plan_freezes_impact_and_projects_sequence() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    plan = plan_change_set(
        change_set_id="cs1",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    assert plan.authoritative_owner == target
    assert [unit.action for unit in plan.execution_units] == [
        ExecutionAction.REBUILD,
        ExecutionAction.REBUILD,
        ExecutionAction.REPROJECT,
    ]
    assert [unit.execution_unit_id for unit in plan.execution_units] == [
        "unit:use_case_spec:UC1",
        "unit:class:Enrollment",
        "unit:sequence:EnrollmentFlow",
    ]
    assert plan.execution_units[1].depends_on_unit_ids == ("unit:use_case_spec:UC1",)
    assert plan.execution_units[2].depends_on_unit_ids == ("unit:class:Enrollment",)
    assert tuple(ref.format() for ref in plan.pre_change_impact) == (
        "class:Enrollment",
        "sequence:EnrollmentFlow",
        "use_case_spec:UC1",
    )
    with pytest.raises(ValidationError, match="plan digest"):
        type(plan).model_validate({**plan.model_dump(), "plan_digest": "0" * 64})
    assert type(plan).model_validate_json(plan.model_dump_json()) == plan
    with pytest.raises(ValidationError, match="frozen"):
        plan.artifact_snapshot[0].digest = "changed"


def test_plan_is_canonical_under_shuffled_inputs_and_rejects_duplicate_catalog() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    first = plan_change_set(
        change_set_id="cs",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    second = plan_change_set(
        change_set_id="cs",
        question=question,
        decision=decision,
        artifact_snapshot=tuple(reversed(_snapshot())),
        pre_change_trace=_trace(),
    )
    assert first.plan_digest == second.plan_digest
    with pytest.raises(ChangePlanError, match="incomplete or ambiguous"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=decision,
            artifact_snapshot=(*_snapshot(), ArtifactSnapshotEntry(target=target, digest="x" * 64)),
            pre_change_trace=_trace(),
        )
    newer_bundle = _target("class:Enrollment", "class", "design", "class_diagram").model_copy(
        update={"artifact_version_id": 4}
    )
    changed = plan_change_set(
        change_set_id="cs",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot((target, newer_bundle, _catalog()[2])),
        pre_change_trace=_trace(),
    )
    assert changed.plan_digest != first.plan_digest


def test_stale_question_revision_and_non_normalized_decision_are_not_plannable() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    stale = decision.model_copy(update={"question_version": 2})
    with pytest.raises(ChangePlanError, match="version is stale"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=stale,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )
    pending = decision.model_copy(update={"status": "RECEIVED"})
    with pytest.raises(ChangePlanError, match="NORMALIZED"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=pending,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )


def test_unsupported_and_ambiguous_ownership_fail_closed() -> None:
    sequence = _target("sequence:EnrollmentFlow", "sequence", "design", "sequence_diagram")
    question, decision = _decision(_question(sequence, category="design_choice"))
    with pytest.raises(ChangePlanError, match="unsupported"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=decision,
            artifact_snapshot=_snapshot((sequence,)),
            pre_change_trace=RtmSnapshot(
                trace=ArtifactTrace((TraceNode(TraceRef("sequence", "EnrollmentFlow")),))
            ),
        )


def test_execution_unit_rejects_illegal_dependency_and_nested_models_are_frozen() -> None:
    target, *_ = _catalog()
    evidence = RtmEvidence(
        pre_trace_digest="a" * 64, impacted_refs=(TraceRef("use_case_spec", "UC1"),), reason="test"
    )
    with pytest.raises(ValidationError, match="depend on itself"):
        ExecutionUnit(
            execution_unit_id="u",
            artifact=target,
            owner="requirements",
            action="rebuild",
            reason="x",
            rtm_evidence=evidence,
            depends_on_unit_ids=("u",),
        )
    with pytest.raises(ValidationError, match="frozen"):
        evidence.reason = "changed"


def test_catalog_maps_project_target_by_kind_and_element_not_display_ref() -> None:
    catalog = list(_catalog())
    catalog[1] = _target("class_diagram:Enrollment", "class", "design", "class_diagram")
    target = catalog[0]
    question, decision = _decision(_question(target))
    plan = plan_change_set(
        change_set_id="cs",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(tuple(catalog)),
        pre_change_trace=_trace(),
    )
    assert plan.execution_units[1].artifact.ref == "class_diagram:Enrollment"
    duplicate_identity = _target("other-display:Enrollment", "class", "design", "class_diagram")
    with pytest.raises(ChangePlanError, match="incomplete or ambiguous"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=decision,
            artifact_snapshot=_snapshot((*catalog, duplicate_identity)),
            pre_change_trace=_trace(),
        )


def test_change_set_rejects_unknown_and_cyclic_dependencies() -> None:
    target, *_ = _catalog()
    evidence = RtmEvidence(
        pre_trace_digest="a" * 64,
        impacted_refs=(TraceRef("use_case_spec", "UC1"),),
        reason="test",
    )
    first = ExecutionUnit(
        execution_unit_id="a",
        artifact=target,
        owner="requirements",
        action="rebuild",
        reason="root",
        rtm_evidence=evidence,
        depends_on_unit_ids=("b",),
    )
    second = ExecutionUnit(
        execution_unit_id="b",
        artifact=target,
        owner="requirements",
        action="rebuild",
        reason="child",
        rtm_evidence=evidence,
        depends_on_unit_ids=("a",),
    )
    question, decision = _decision(_question(target))
    plan = plan_change_set(
        change_set_id="cs",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    payload = plan.model_dump()
    payload["execution_units"] = (first, second)
    payload["artifact_snapshot"] = (_snapshot((target,))[0], _snapshot((target,))[0])
    with pytest.raises(ValidationError, match="cycle"):
        type(plan).model_validate(payload)
    payload["execution_units"] = (first.model_copy(update={"depends_on_unit_ids": ("missing",)}),)
    payload["artifact_snapshot"] = (_snapshot((target,))[0],)
    with pytest.raises(ValidationError, match="not in this ChangeSet"):
        type(plan).model_validate(payload)


def test_terminal_question_and_policy_bypass_are_rejected() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    with pytest.raises(ChangePlanError, match="terminal"):
        plan_change_set(
            change_set_id="cs",
            question=question.model_copy(update={"status": "STALE"}),
            decision=decision,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )
    forbidden = decision.model_copy(
        update={
            "normalized_meaning": DecisionMeaning(
                semantic_scope="behavior", requested_effect="change"
            )
        }
    )
    restricted_question = question.model_copy(
        update={
            "decision_policy": DecisionPolicy(
                allowed_semantic_scopes=("contract",), allowed_change_types=("modify",)
            )
        }
    )
    with pytest.raises(ChangePlanError, match="option decision"):
        plan_change_set(
            change_set_id="cs",
            question=restricted_question,
            decision=forbidden,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )
    answered = question.model_copy(update={"status": "ANSWERED"})
    assert (
        plan_change_set(
            change_set_id="answered",
            question=answered,
            decision=decision,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        ).status
        == "PLANNED"
    )
    presentation = decision.model_copy(
        update={
            "normalized_meaning": DecisionMeaning(
                semantic_scope="presentation", requested_effect="change"
            )
        }
    )
    presentation_question = question.model_copy(
        update={
            "decision_policy": DecisionPolicy(
                allowed_semantic_scopes=("contract", "presentation"),
                allowed_change_types=("modify",),
            )
        }
    )
    with pytest.raises(ChangePlanError, match="option decision"):
        plan_change_set(
            change_set_id="cs",
            question=presentation_question,
            decision=presentation,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )
    second = _target("use_case_spec:UC2", "use_case_spec", "requirements", "usecase")
    two_target_question = question.model_copy(update={"authority_candidates": (target, second)})
    two_target_decision = Decision.model_validate(
        {
            **decision.model_dump(),
            "answer_mode": "free_text",
            "selected_option_id": None,
            "raw_answer": "both",
            "authoritative_targets": (target, second),
        }
    )
    with pytest.raises(ChangePlanError, match="exactly one UC specification"):
        plan_change_set(
            change_set_id="cs",
            question=two_target_question,
            decision=two_target_decision,
            artifact_snapshot=_snapshot((*_catalog(), second)),
            pre_change_trace=_trace(),
        )


def test_stale_producer_propagates_through_downstream_actions_independent_of_ref_order() -> None:
    root = _target("use_case_spec:UC1", "use_case_spec", "requirements", "usecase")
    unsupported = _target("api_spec:Gateway", "api", "design", "api_spec")
    bundle = _target("class_diagram:Enrollment", "class", "design", "class_diagram")
    sequence = _target("sequence_diagram:EnrollmentFlow", "sequence", "design", "sequence_diagram")
    root_ref = TraceRef("use_case_spec", "UC1")
    api_ref = TraceRef("api", "Gateway")
    class_ref = TraceRef("class", "Enrollment")
    sequence_ref = TraceRef("sequence", "EnrollmentFlow")
    trace = RtmSnapshot(
        trace=ArtifactTrace(
            (
                TraceNode(root_ref),
                TraceNode(api_ref, (root_ref,)),
                TraceNode(class_ref, (api_ref,)),
                TraceNode(sequence_ref, (class_ref,)),
            )
        )
    )
    question, decision = _decision(_question(root))
    plan = plan_change_set(
        change_set_id="cs",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot((sequence, bundle, unsupported, root)),
        pre_change_trace=trace,
    )
    actions = {unit.artifact.kind: unit.action for unit in plan.execution_units}
    assert actions == {
        "use_case_spec": ExecutionAction.REBUILD,
        "api": ExecutionAction.STALE,
        "class": ExecutionAction.STALE,
        "sequence": ExecutionAction.STALE,
    }


def test_answer_payload_and_free_text_permission_are_revalidated() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    unknown_option = Decision.model_validate(
        {**decision.model_dump(), "selected_option_id": "missing"}
    )
    with pytest.raises(ChangePlanError, match="absent from the question"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=unknown_option,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )
    spoofed = decision.model_copy(update={"preserved_constraints": ("not-in-option",)})
    with pytest.raises(ChangePlanError, match="option decision"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=spoofed,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )
    free = Decision.model_validate(
        {
            **decision.model_dump(),
            "answer_mode": "free_text",
            "selected_option_id": None,
            "raw_answer": "retain this entry",
            "normalized_meaning": {
                "semantic_scope": "contract",
                "requested_effect": "retain this entry",
            },
        }
    )
    locked = question.model_copy(update={"allow_free_text": False})
    with pytest.raises(ChangePlanError, match="free-text"):
        plan_change_set(
            change_set_id="cs",
            question=locked,
            decision=free,
            artifact_snapshot=_snapshot(),
            pre_change_trace=_trace(),
        )


def test_decision_snapshot_and_projection_contract_are_pinned() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    changed = Decision.model_validate(
        {
            **decision.model_dump(),
            "answer_mode": "free_text",
            "selected_option_id": None,
            "raw_answer": "different",
            "normalized_meaning": {"semantic_scope": "contract", "requested_effect": "different"},
        }
    )
    first = plan_change_set(
        change_set_id="a",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    second = plan_change_set(
        change_set_id="b",
        question=question,
        decision=changed,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    assert first.decision_digest != second.decision_digest
    no_contract = RtmSnapshot(trace=_trace().trace)
    stale = plan_change_set(
        change_set_id="c",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(),
        pre_change_trace=no_contract,
    )
    assert stale.execution_units[-1].action is ExecutionAction.STALE


def test_digest_canonicalizes_decision_sets_and_includes_evidence_reason() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    reordered = Decision.model_validate(
        {
            **decision.model_dump(),
            "answer_mode": "free_text",
            "selected_option_id": None,
            "raw_answer": "same",
            "preserved_constraints": ("z", "a"),
        }
    )
    reordered_again = reordered.model_copy(update={"preserved_constraints": ("a", "z")})
    first = plan_change_set(
        change_set_id="a",
        question=question,
        decision=reordered,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    second = plan_change_set(
        change_set_id="b",
        question=question,
        decision=reordered_again,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    assert first.decision_digest == second.decision_digest
    payload = first.model_dump()
    payload["execution_units"][0]["rtm_evidence"]["reason"] = "changed"
    with pytest.raises(ValidationError, match="plan digest"):
        type(first).model_validate(payload)
    with pytest.raises(ValidationError, match="blank"):
        ArtifactSnapshotEntry(target=target, digest=" ")


def test_changeset_reload_rejects_decision_unit_evidence_and_dependency_tampering() -> None:
    target, *_ = _catalog()
    question, decision = _decision(_question(target))
    plan = plan_change_set(
        change_set_id="a",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )
    payload = plan.model_dump()
    payload["decision_snapshot"] = plan.decision_snapshot.model_copy(update={"status": "RECEIVED"})
    with pytest.raises(ValidationError, match="NORMALIZED"):
        type(plan).model_validate(payload)
    payload = plan.model_dump()
    payload["execution_units"][1]["rtm_evidence"]["pre_trace_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="RTM evidence"):
        type(plan).model_validate(payload)
    payload = plan.model_dump()
    payload["execution_units"][2]["dependencies"][0]["producer_revision_or_digest"] = "version:999"
    with pytest.raises(ValidationError, match="dependency producer"):
        type(plan).model_validate(payload)
    payload = plan.model_dump()
    payload["execution_units"][1]["dependencies"] = ()
    with pytest.raises(ValidationError, match="RTM direct sources"):
        type(plan).model_validate(payload)
    payload = plan.model_dump()
    payload["execution_units"][2]["depends_on_unit_ids"] = ()
    with pytest.raises(ValidationError, match="impacted producers"):
        type(plan).model_validate(payload)


def test_snapshot_contracts_are_deduplicated_and_json_roundtrip_stays_frozen() -> None:
    snapshot = _trace()
    duplicate_contracts = RtmSnapshot(
        trace=snapshot.trace,
        projection_contracts=(
            snapshot.projection_contracts[0],
            snapshot.projection_contracts[0],
        ),
    )
    assert duplicate_contracts.projection_contracts == snapshot.projection_contracts
    assert type(snapshot).model_validate_json(snapshot.model_dump_json()) == snapshot
    with pytest.raises(ValidationError, match="frozen"):
        duplicate_contracts.projection_contracts += snapshot.projection_contracts


def test_external_direct_producer_version_is_pinned_without_becoming_an_execution_unit() -> None:
    root, bundle, sequence = _catalog()
    external = _target("policy:Eligibility", "policy", "requirements", "POLICY")
    root_ref = TraceRef("use_case_spec", "UC1")
    external_ref = TraceRef("policy", "Eligibility")
    bundle_ref = TraceRef("class", "Enrollment")
    sequence_ref = TraceRef("sequence", "EnrollmentFlow")
    trace = RtmSnapshot(
        trace=ArtifactTrace(
            (
                TraceNode(root_ref),
                TraceNode(external_ref),
                TraceNode(bundle_ref, (root_ref, external_ref)),
                TraceNode(sequence_ref, (bundle_ref,)),
            )
        ),
        projection_contracts=(
            ProjectionContract(
                consumer=sequence_ref,
                producer_refs=(bundle_ref,),
                adapter="class_to_sequence",
                version="v1",
            ),
        ),
    )
    question, decision = _decision(_question(root))
    current = _snapshot((root, bundle, sequence, external))
    plan = plan_change_set(
        change_set_id="external",
        question=question,
        decision=decision,
        artifact_snapshot=current,
        pre_change_trace=trace,
    )
    class_unit = next(unit for unit in plan.execution_units if unit.artifact.kind == "class")
    assert {item.producer_ref for item in class_unit.dependencies} == {
        root_ref,
        external_ref,
    }
    assert external_ref not in {
        TraceRef.parse(unit.execution_unit_id.removeprefix("unit:"))
        for unit in plan.execution_units
    }
    changed_external = external.model_copy(update={"artifact_version_id": 99})
    changed = plan_change_set(
        change_set_id="external",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot((root, bundle, sequence, changed_external)),
        pre_change_trace=trace,
    )
    assert changed.plan_digest != plan.plan_digest
    with pytest.raises(ChangePlanError, match="no current artifact snapshot"):
        plan_change_set(
            change_set_id="external",
            question=question,
            decision=decision,
            artifact_snapshot=_snapshot((root, bundle, sequence)),
            pre_change_trace=trace,
        )


def test_changeset_reload_recomputes_impact_and_registered_actions() -> None:
    root, *_ = _catalog()
    question, decision = _decision(_question(root))
    plan = plan_change_set(
        change_set_id="reload",
        question=question,
        decision=decision,
        artifact_snapshot=_snapshot(),
        pre_change_trace=_trace(),
    )

    artifact_mismatch = plan.model_dump()
    artifact_mismatch["execution_units"][1]["artifact"]["artifact_version_id"] = 99
    _recompute_plan_digest(artifact_mismatch)
    with pytest.raises(ValidationError, match="artifact must match"):
        type(plan).model_validate(artifact_mismatch)

    shortened = plan.model_dump()
    sequence_ref = TraceRef("sequence", "EnrollmentFlow")
    shortened["pre_change_impact"] = [
        item for item in shortened["pre_change_impact"] if TraceRef(**item) != sequence_ref
    ]
    shortened["execution_units"] = shortened["execution_units"][:-1]
    for unit in shortened["execution_units"]:
        unit["rtm_evidence"]["impacted_refs"] = shortened["pre_change_impact"]
    _recompute_plan_digest(shortened)
    with pytest.raises(ValidationError, match="all RTM downstream"):
        type(plan).model_validate(shortened)

    stale_producer = plan.model_dump()
    stale_producer["execution_units"][1]["action"] = "stale"
    _recompute_plan_digest(stale_producer)
    with pytest.raises(ValidationError, match="registered planning policy"):
        type(plan).model_validate(stale_producer)

    duplicate_artifact = plan.model_dump()
    duplicate_unit = dict(duplicate_artifact["execution_units"][-1])
    duplicate_unit["execution_unit_id"] = "zz_duplicate_sequence"
    duplicate_artifact["execution_units"] = (
        *duplicate_artifact["execution_units"],
        duplicate_unit,
    )
    _recompute_plan_digest(duplicate_artifact)
    with pytest.raises(ValidationError, match="unit artifacts must be unique"):
        type(plan).model_validate(duplicate_artifact)


def test_planner_rejects_stale_current_base_and_actual_rtm_cycle() -> None:
    target, bundle, sequence = _catalog()
    question, decision = _decision(_question(target))
    stale_bundle = bundle.model_copy(update={"artifact_version_id": 99})
    base_class = BaseRevision(artifact_type="class_diagram", version_id=3)
    question = question.model_copy(
        update={"base_revisions": (*question.base_revisions, base_class)}
    )
    decision = decision.model_copy(
        update={"base_revisions": (*decision.base_revisions, base_class)}
    )
    with pytest.raises(ChangePlanError, match="base revision version is stale"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=decision,
            artifact_snapshot=_snapshot((target, stale_bundle, sequence)),
            pre_change_trace=_trace(),
        )
    root_ref = TraceRef("use_case_spec", "UC1")
    class_ref = TraceRef("class", "Enrollment")
    cyclic = RtmSnapshot(
        trace=ArtifactTrace((TraceNode(root_ref, (class_ref,)), TraceNode(class_ref, (root_ref,))))
    )
    with pytest.raises(ChangePlanError, match="cycle"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=decision,
            artifact_snapshot=_snapshot(),
            pre_change_trace=cyclic,
        )
    unknown = RtmSnapshot(
        trace=ArtifactTrace((TraceNode(root_ref, (TraceRef("precondition", "missing"),)),))
    )
    with pytest.raises(ChangePlanError, match="unknown direct producer"):
        plan_change_set(
            change_set_id="cs",
            question=question,
            decision=decision,
            artifact_snapshot=_snapshot(),
            pre_change_trace=unknown,
        )
