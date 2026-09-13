from __future__ import annotations

from copy import deepcopy

from app.design import cascade
from app.design.schemas.class_model import BCEModel
from app.design.service import ReviseRequest
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.sequence_diagram.projection import project_sequence_model
from app.requirements.orchestration import feedback as requirements_feedback
from app.workspace.conversation.contracts import RevisionTarget
from app.workspace.conversation.delivery import requirements_feedback_edit
from app.workspace.conversation.feedback_envelope import (
    DecisionMeaning,
    DecisionPayload,
    DecisionPolicy,
    Question,
    QuestionOption,
    answer_option,
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
    uc = _target("use_case_spec:UC1", "use_case_spec", "requirements", "USECASE_SPEC")
    question = Question(
        question_id="q",
        question_version=1,
        app_id="app",
        source_execution_id="run",
        detected_at={"stage": "design", "artifact_ref": "class_diagram:OrderController"},
        base_revisions=[{"artifact_type": "USECASE_SPEC", "version_id": 3}],
        trigger={"category": "specification_gap"},
        authority_candidates=[uc],
        prompt="Should the order be approved before it is recorded?",
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
    assert decision.normalized_meaning is not None
    edit = requirements_feedback_edit(
        decision.authoritative_targets,
        decision.normalized_meaning.requested_effect,
    )
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
    revision = ReviseRequest(
        target="class_diagram:OrderController",
        feedback=decision.normalized_meaning.requested_effect,
        approved_authority_targets=["class_diagram:OrderController"],
        approved_downstream_targets=None,
    )
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
