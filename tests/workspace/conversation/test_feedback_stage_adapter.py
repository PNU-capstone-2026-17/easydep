from __future__ import annotations

import pytest

from app.artifact_trace import ArtifactTrace, TraceNode, TraceRef
from app.workspace.conversation.contracts import RevisionTarget
from app.workspace.conversation.feedback_change_plan import (
    ArtifactSnapshotEntry,
    ExecutionAction,
    ProjectionContract,
    RtmSnapshot,
    plan_change_set,
)
from app.workspace.conversation.feedback_envelope import (
    DecisionMeaning,
    DecisionPayload,
    DecisionPolicy,
    Question,
    QuestionOption,
    answer_option,
)
from app.workspace.conversation.feedback_stage_adapter import (
    DesignStageInput,
    RequirementsStageInput,
    SequenceProjectionInput,
    StageAdapterError,
    adapt_execution_unit,
)


def target(ref, kind, owner, artifact_type):
    return RevisionTarget(
        ref=ref,
        kind=kind,
        element_id=ref.split(":", 1)[1],
        owner=owner,
        artifact_type=artifact_type,
        artifact_version_id=3,
        display_label=ref,
    )


def plan():
    root = target("use_case_spec:UC1", "use_case_spec", "requirements", "usecase")
    bundle = target("class_diagram:Enrollment", "class", "design", "class_diagram")
    sequence = target("sequence_diagram:Flow", "sequence", "design", "sequence_diagram")
    api = target("api_spec:enroll", "api", "design", "api_spec")
    question = Question(
        question_id="q1",
        question_version=1,
        app_id="app",
        source_execution_id="run",
        detected_at={"stage": "design", "artifact_ref": bundle.ref},
        base_revisions=[{"artifact_type": root.artifact_type, "version_id": 3}],
        trigger={"category": "gap"},
        authority_candidates=[root],
        prompt="Clarify",
        options=(
            QuestionOption(
                option_id="apply",
                label="Apply",
                decision_payload=DecisionPayload(
                    normalized_meaning=DecisionMeaning(
                        semantic_scope="contract", requested_effect="retain"
                    ),
                    authoritative_target_refs=(root.ref,),
                ),
            ),
        ),
        decision_policy=DecisionPolicy(
            allowed_semantic_scopes=("contract",), allowed_change_types=("modify",)
        ),
    )
    decision = answer_option(
        question, option_id="apply", decision_id="d1", source_user_message_id="m1"
    )
    trace = RtmSnapshot(
        trace=ArtifactTrace(
            (
                TraceNode(TraceRef("use_case_spec", "UC1")),
                TraceNode(TraceRef("class", "Enrollment"), (TraceRef("use_case_spec", "UC1"),)),
                TraceNode(TraceRef("sequence", "Flow"), (TraceRef("class", "Enrollment"),)),
                TraceNode(TraceRef("api", "enroll"), (TraceRef("class", "Enrollment"),)),
            )
        ),
        projection_contracts=(
            ProjectionContract(
                consumer=TraceRef("sequence", "Flow"),
                producer_refs=(TraceRef("class", "Enrollment"),),
                adapter="class_to_sequence",
                version="v1",
            ),
        ),
    )
    catalog = (root, bundle, sequence, api)
    snapshot = tuple(
        ArtifactSnapshotEntry(target=item, digest=str(i) * 64) for i, item in enumerate(catalog, 1)
    )
    return plan_change_set(
        change_set_id="cs1",
        question=question,
        decision=decision,
        artifact_snapshot=snapshot,
        pre_change_trace=trace,
    )


def unit_id(change_set, action):
    return next(
        unit.execution_unit_id for unit in change_set.execution_units if unit.action is action
    )


def test_real_plan_adapts_requirements_class_bundle_and_sequence():
    change_set = plan()
    requirements = adapt_execution_unit(
        change_set,
        next(
            u.execution_unit_id
            for u in change_set.execution_units
            if u.artifact.owner == "requirements"
        ),
    )
    assert isinstance(requirements, RequirementsStageInput)
    assert requirements.edit.stage == "specs"
    assert requirements.edit.target_ids == ["UC1"]

    class_unit = next(u for u in change_set.execution_units if u.artifact.kind == "class")
    design = adapt_execution_unit(change_set, class_unit.execution_unit_id)
    assert isinstance(design, DesignStageInput)
    assert design.revision.target == "class_diagram:Enrollment"
    assert design.revision.approved_authority_targets == ["class_diagram:Enrollment"]

    sequence = adapt_execution_unit(change_set, unit_id(change_set, ExecutionAction.REPROJECT))
    assert isinstance(sequence, SequenceProjectionInput)
    assert sequence.source_refs == ("class:Enrollment",)
    assert (sequence.adapter, sequence.version) == ("class_to_sequence", "v1")


def test_missing_unit_and_stale_action_fail_closed():
    change_set = plan()
    with pytest.raises(StageAdapterError, match="not a member"):
        adapt_execution_unit(change_set, "unit:missing")

    stale_unit = next(
        unit for unit in change_set.execution_units if unit.action is ExecutionAction.STALE
    )
    with pytest.raises(StageAdapterError, match="rebuild only"):
        adapt_execution_unit(change_set, stale_unit.execution_unit_id)
