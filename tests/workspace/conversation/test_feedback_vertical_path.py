from __future__ import annotations

from copy import deepcopy

from app.artifact_trace import ArtifactTrace, TraceNode, TraceRef
from app.design import cascade
from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.sequence_diagram.projection import project_sequence_model
from app.requirements.orchestration import feedback as requirements_feedback
from app.workspace.conversation.contracts import RevisionTarget
from app.workspace.conversation.feedback_change_plan import (
    ArtifactSnapshotEntry,
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
    adapt_execution_unit,
)
from tests.design_validation_fixtures import CLEAN, CLEAN_STATE


def _target(ref: str, kind: str, owner: str, artifact_type: str) -> RevisionTarget:
    return RevisionTarget(
        ref=ref,
        kind=kind,
        element_id=ref.split(":", 1)[1],
        owner=owner,
        artifact_type=artifact_type,
        artifact_version_id=3,
        display_label=ref,
    )


def test_feedback_decision_to_requirements_class_and_sequence_cascade(monkeypatch) -> None:
    uc = _target("use_case_spec:UC1", "use_case_spec", "requirements", "usecase")
    cls = _target("class_diagram:OrderController", "class", "design", "class_diagram")
    seq = _target("sequence_diagram:UC1", "sequence", "design", "sequence_diagram")
    question = Question(
        question_id="q",
        question_version=1,
        app_id="app",
        source_execution_id="run",
        detected_at={"stage": "design", "artifact_ref": cls.ref},
        base_revisions=[{"artifact_type": "usecase", "version_id": 3}],
        trigger={"category": "gap"},
        authority_candidates=[uc],
        prompt="p",
        decision_policy=DecisionPolicy(
            allowed_semantic_scopes=("contract",), allowed_change_types=("modify",)
        ),
        options=[
            QuestionOption(
                option_id="ok",
                label="OK",
                decision_payload=DecisionPayload(
                    normalized_meaning=DecisionMeaning(
                        semantic_scope="contract", requested_effect="add approval"
                    ),
                    authoritative_target_refs=(uc.ref,),
                ),
            )
        ],
    )
    decision = answer_option(question, option_id="ok", decision_id="d", source_user_message_id="m")
    trace = RtmSnapshot(
        trace=ArtifactTrace(
            (
                TraceNode(TraceRef("use_case_spec", "UC1")),
                TraceNode(
                    TraceRef("class", "OrderController"),
                    (TraceRef("use_case_spec", "UC1"),),
                ),
                TraceNode(
                    TraceRef("sequence", "UC1"),
                    (TraceRef("class", "OrderController"),),
                ),
            )
        ),
        projection_contracts=(
            ProjectionContract(
                consumer=TraceRef("sequence", "UC1"),
                producer_refs=(TraceRef("class", "OrderController"),),
                adapter="class_to_sequence",
                version="v1",
            ),
        ),
    )
    change_set = plan_change_set(
        change_set_id="cs",
        question=question,
        decision=decision,
        artifact_snapshot=tuple(
            ArtifactSnapshotEntry(target=x, digest=str(i) * 64)
            for i, x in enumerate((uc, cls, seq), 1)
        ),
        pre_change_trace=trace,
    )
    requirements_unit = next(
        x for x in change_set.execution_units if x.artifact.owner == "requirements"
    )
    requirements_input = adapt_execution_unit(change_set, requirements_unit.execution_unit_id)
    assert isinstance(requirements_input, RequirementsStageInput)
    edit = requirements_input.edit
    req_state = deepcopy(CLEAN_STATE["usecase_spec"])

    def generate_specs(state, *, feedback, target_ids):
        assert feedback == "add approval"
        assert target_ids == ["UC1"]
        specs = deepcopy(state["use_case_specs"])
        specs[0]["main_scenario"][1]["sentence"] = "System approves and records the order."
        return {"use_case_specs": specs}

    monkeypatch.setattr(
        requirements_feedback,
        "generate_specs",
        generate_specs,
    )
    monkeypatch.setattr(
        requirements_feedback, "identify_relationships", lambda _state: {"relationships": {}}
    )
    monkeypatch.setattr(
        requirements_feedback, "render_diagram", lambda _state: {"use_case_diagram": "ok"}
    )
    revised_requirements, _ = requirements_feedback.apply_feedback(req_state, edit)
    assert (
        revised_requirements["use_case_specs"][0]["main_scenario"][1]["sentence"]
        == "System approves and records the order."
    )

    class_unit = next(x for x in change_set.execution_units if x.artifact.kind == "class")
    design_input = adapt_execution_unit(change_set, class_unit.execution_unit_id)
    assert isinstance(design_input, DesignStageInput)
    monkeypatch.setattr(
        cascade,
        "build_design_rtm",
        lambda _state: {
            "rows": [
                {"stage": "class_diagram", "element": "OrderController"},
                {"stage": "sequence_diagram", "element": "UC1"},
            ],
            "links": [],
            "impact": {"class:OrderController": ["sequence_diagram:UC1"]},
        },
    )
    monkeypatch.setattr(cascade, "affected_by_element", lambda *_: ["sequence_diagram:UC1"])
    monkeypatch.setattr(cascade, "linked_elements", lambda *_: [])
    revised_class = deepcopy(CLEAN)
    controller = next(
        item for item in revised_class["Classes"] if item["className"] == "OrderController"
    )
    controller["operations"][0]["returnType"] = "Boolean"

    def apply(spec, state, _feedback, targets, **_kwargs):
        if spec.stage == "class_diagram":
            assert (
                state["usecase_spec"]["use_case_specs"][0]["main_scenario"][1]["sentence"]
                == "System approves and records the order."
            )
            assert targets == {"OrderController"}
            return {spec.model_key: revised_class}
        raise AssertionError("sequence must use deterministic projection")

    monkeypatch.setattr(cascade, "_apply", apply)
    revision = design_input.revision
    current_sequence = project_sequence_model(
        build_scenario_index(revised_requirements),
        BCEModel.model_validate(CLEAN),
    ).model_dump(mode="json")
    result = cascade.revise_and_cascade(
        {
            "usecase_spec": revised_requirements,
            "extracted_bce_classes": deepcopy(CLEAN),
            "sequence_diagram_model": current_sequence,
        },
        revision.target,
        revision.feedback,
        approved_authority_targets=set(revision.approved_authority_targets or ()),
        approved_downstream_targets=(
            set(revision.approved_downstream_targets)
            if revision.approved_downstream_targets is not None
            else None
        ),
    )
    assert result["changed"] == ["class_diagram", "sequence_diagram"]
    messages = result["state"]["sequence_diagram_model"]["Diagrams"][0]["Messages"]
    assert (
        next(message for message in messages if message.get("call_id") == "UC1::call:2")["label"]
        == "placeOrder(request:String)"
    )
    assert (
        next(message for message in messages if message.get("reply_to") == "UC1::call:2")["label"]
        == "boolean"
    )
