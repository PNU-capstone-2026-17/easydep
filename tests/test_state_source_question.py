from __future__ import annotations

import json
import pytest

from app.requirements.contracts.request import ResourceAnswer
from app.requirements.modeling import specifications
from app.requirements.orchestration import feedback_gates
from app.requirements.orchestration.graph import build_graph, result_payload
from app.requirements.schemas import (
    StateConditionCandidates,
    StateSourceReview,
    StateSourceReviews,
)
from app.workspace import repository
from app.workspace.service import WorkspaceService


def _state() -> dict:
    condition = "the waitlist is enabled"
    requirement = "When the offering is full and the waitlist is enabled, the learner may join it."
    return {
        "use_case_specs": [
            {
                "use_case_id": "UC7", "name": "Join waitlist", "requirement_ids": ["RR9"],
                "main_scenario": [{"step_number": 1, "covered_req_ids": ["RR9"]}],
                "extensions": [{"branch_step": 1, "condition": condition}],
                "generated": True,
            },
            {
                "use_case_id": "UC11", "name": "Manage offering", "requirement_ids": ["RR13"],
                "main_scenario": [{"step_number": 1, "sentence": "Create offering."}],
                "extensions": [], "generated": True,
            },
        ],
        "classified": [
            {"id": "RR9", "text": requirement},
            {"id": "RR13", "text": "The instructor can create an offering."},
        ],
    }


def test_unresolved_condition_asks_value_question_from_consumer_evidence_only() -> None:
    state = _state()
    requirement = state["classified"][0]["text"]

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        assert schema is StateSourceReviews
        assert '"useCaseId": "UC11"' in messages[1].content
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="unresolved", consumerUseCaseId="UC7", requirementIds=["RR9"],
            evidenceSpans=[requirement],
        )])

    question = specifications.state_source_question(state, proposal_call=review)

    assert question is not None
    assert question["kind"] == "state_source"
    assert question["useCaseId"] == "UC7"
    assert question["requirementIds"] == ["RR9"]
    assert question["evidenceSpans"] == [requirement]
    assert "UC11" not in question["question"]
    assert "options" not in question
    assert question["field"] == question["stateRef"]


def test_explicit_setter_in_independent_domain_suppresses_question() -> None:
    consumer_req = "A patron may borrow a copy only when it is loanable."
    producer_req = "A librarian can set the copy's loanable value."
    producer_step = "A librarian sets a copy as loanable or non-loanable."
    state = {
        "use_case_specs": [
            {
                "use_case_id": "LIB5", "name": "Borrow copy", "requirement_ids": ["LR9"],
                "main_scenario": [{"step_number": 1, "covered_req_ids": ["LR9"]}],
                "extensions": [{"branch_step": 1, "condition": "the copy is loanable"}],
                "generated": True,
            },
            {
                "use_case_id": "LIB2", "name": "Catalog copy", "requirement_ids": ["LR4"],
                "main_scenario": [{"step_number": 1, "sentence": producer_step}],
                "extensions": [], "generated": True,
            },
        ],
        "classified": [
            {"id": "LR9", "text": consumer_req},
            {"id": "LR4", "text": producer_req},
        ],
    }
    review_prompts: list[str] = []

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        review_prompts.append(messages[1].content)
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="explicit_setter", consumerUseCaseId="LIB5",
            requirementIds=["LR9"],
            evidenceSpans=[consumer_req, producer_req],
            producerUseCaseId="LIB2", sourceEvidenceSpan=producer_req,
        )])

    question, status, unreviewed = specifications.review_state_sources(
        state, proposal_call=review
    )

    assert question is None
    assert status == "reviewed"
    assert unreviewed == []
    assert len(review_prompts) == 1
    assert (
        "For explicit_setter, sourceEvidenceSpan must be a verbatim quote from the selected "
        "producer use case specification or one of its linked requirement texts, not from "
        "the consumer condition."
    ) in review_prompts[0]


def test_external_or_derived_state_source_suppresses_question() -> None:
    state = _state()
    state["classified"][1]["text"] = "The system derives waitlist access from the offering's configured enrollment policy."
    state["use_case_specs"][1]["requirement_ids"] = ["RR13"]
    state["use_case_specs"][1]["main_scenario"] = [{
        "step_number": 1,
        "sentence": "The system derives waitlist access from the offering's configured enrollment policy.",
    }]

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="derived", consumerUseCaseId="UC7", requirementIds=["RR9", "RR13"],
            evidenceSpans=[state["classified"][0]["text"], state["classified"][1]["text"]],
            sourceEvidenceSpan=state["classified"][1]["text"],
        )])

    assert specifications.state_source_question(state, proposal_call=review) is None


def test_unverified_derived_claim_uses_exact_consumer_source_quote_to_ask() -> None:
    state = _state()
    requirement = state["classified"][0]["text"]

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="derived", consumerUseCaseId="UC7", requirementIds=["RR9"],
            evidenceSpans=[requirement], sourceEvidenceSpan=requirement,
        )])

    question = specifications.state_source_question(state, proposal_call=review)

    assert question is not None
    assert question["requirementIds"] == ["RR9"]
    assert question["evidenceSpans"] == [requirement]


def test_distinct_source_quote_in_same_requirement_is_accepted() -> None:
    state = _state()
    condition_evidence = "When the waitlist is enabled, the learner may join it."
    source_evidence = "The system derives eligibility from enrollment policy."
    state["classified"][0]["text"] = f"{condition_evidence} {source_evidence}"

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="derived", consumerUseCaseId="UC7", requirementIds=["RR9"],
            evidenceSpans=[condition_evidence], sourceEvidenceSpan=source_evidence,
        )])

    assert specifications.state_source_question(state, proposal_call=review) is None


def test_unresolved_duplicate_consumer_source_quote_still_asks() -> None:
    state = _state()
    requirement = state["classified"][0]["text"]

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="unresolved", consumerUseCaseId="UC7", requirementIds=["RR9"],
            evidenceSpans=[requirement], sourceEvidenceSpan=requirement,
        )])

    question = specifications.state_source_question(state, proposal_call=review)

    assert question is not None
    assert question["evidenceSpans"] == [requirement]


def test_invalid_source_claim_without_exact_consumer_quote_stays_unreviewed() -> None:
    state = _state()

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="derived", consumerUseCaseId="UC7", requirementIds=["RR9"],
            evidenceSpans=["invented quote"], sourceEvidenceSpan="invented source",
        )])

    question, status, unreviewed = specifications.review_state_sources(
        state, proposal_call=review
    )

    assert question is None
    assert status == "unreviewed"
    assert unreviewed


def test_incomplete_candidate_refs_are_reported_unreviewed_not_clean() -> None:
    state = _state()

    def review(schema, messages):
        if schema is StateConditionCandidates:
            return StateConditionCandidates(stateRefs=["made_up_state_ref"])
        raise AssertionError("invalid retrieval refs must stop before condition review")

    question, status, unreviewed = specifications.review_state_sources(
        state, proposal_call=review
    )

    assert question is None
    assert status == "unreviewed"
    assert {item["useCaseId"] for item in unreviewed} == {"UC7"}


def test_state_source_answer_is_ref_bound_and_regenerates_specs(monkeypatch) -> None:
    question = {
        "kind": "state_source", "stateRef": "state_123", "field": "state_123",
        "useCaseId": "UC7", "condition": "waitlist enabled", "requirementIds": ["RR9"],
    }
    monkeypatch.setattr(feedback_gates, "_ask", lambda *args, **kwargs: ResourceAnswer(
        free_text="The offering manager sets it during creation; default is disabled.",
        expected_field="state_123",
    ))
    generated = {}

    def generate(state):
        generated.update(state)
        return {"use_case_specs": [{"use_case_id": "UC7", "generated": True}]}

    monkeypatch.setattr(feedback_gates, "generate_specs", generate)
    monkeypatch.setattr(feedback_gates, "check_specs", lambda state: {
        "use_case_specs": state["use_case_specs"], "spec_report": {},
        "state_source_question": None,
    })

    result = feedback_gates.gate_specs({
        "use_case_specs": [{"use_case_id": "UC7"}],
        "semantic_ambiguity_question": None,
        "identity_source_question": None,
        "state_source_question": question,
    })

    assert generated["state_source_answers"]["state_123"]["answer"].startswith("The offering")
    assert result["state_source_answers"]["state_123"]["answer"].startswith("The offering")
    assert "resource_answers" not in result
    assert result["state_source_question"] is None
    assert result["gate_route"] == "loop"


def test_state_source_answer_rejects_mismatched_ref(monkeypatch) -> None:
    monkeypatch.setattr(feedback_gates, "_ask", lambda *args, **kwargs: ResourceAnswer(
        free_text="enabled", expected_field="another-state",
    ))
    with pytest.raises(ValueError, match="pending condition question"):
        feedback_gates.gate_specs({
            "use_case_specs": [], "semantic_ambiguity_question": None,
            "identity_source_question": None,
            "state_source_question": {
                "kind": "state_source", "stateRef": "state_current", "field": "state_current",
            },
        })


def test_workspace_state_question_is_namespaced_and_not_deployment_resource() -> None:
    question = WorkspaceService._state_source_resource_question({
        "kind": "state_source", "stateRef": "state_abc", "field": "state_abc",
        "question": "How should this condition be supplied?", "requirementIds": ["RR9"],
        "evidenceSpans": ["the waitlist is enabled"],
    })
    assert question is not None
    assert question["kind"] == "state_source"
    assert question["field"] == "state_abc"
    assert question["allowFreeText"] is True
    assert question["requirementIds"] == ["RR9"]
    assert WorkspaceService._state_source_resource_question({
        "kind": "resource", "stateRef": "state_abc", "field": "deployment.region",
        "question": "Pick a cloud region", "requirementIds": ["RR9"],
        "evidenceSpans": ["region"],
    }) is None


def test_checkpoint_question_round_trips_to_workspace_answer_field(monkeypatch) -> None:
    """The real stage/gate checkpoint exposes the stateRef Workspace must answer."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Interrupt

    state = _state()
    requirement = state["classified"][0]["text"]

    def review(schema, messages):
        if schema is StateConditionCandidates:
            inventory = json.loads(messages[1].content)
            return StateConditionCandidates(stateRefs=[item["stateRef"] for item in inventory])
        assert schema is StateSourceReviews
        return StateSourceReviews(reviews=[StateSourceReview(
            disposition="unresolved", consumerUseCaseId="UC7", requirementIds=["RR9"],
            evidenceSpans=[requirement],
        )])

    monkeypatch.setattr(specifications, "invoke_structured", review)
    monkeypatch.setattr(specifications, "identity_source_question", lambda _state: None)
    monkeypatch.setattr(specifications, "find_source_grounded_semantic_ambiguity", lambda _state: None)
    checked = specifications.check_specs(state)
    pending = checked["state_source_question"]
    assert pending is not None

    thread_id = "state-source-cross-layer-test"
    graph = build_graph(feedback_gates=True, saver=MemorySaver())
    config = {"configurable": {"thread_id": thread_id}}
    graph.update_state(config, {**state, **checked}, as_node="write_specifications")
    waiting = graph.invoke(None, config)
    interrupts = waiting.get("__interrupt__") or []
    assert interrupts
    interrupt = interrupts[0]
    assert isinstance(interrupt, Interrupt)
    assert interrupt.value["state_source_question"] == pending

    visible = result_payload(waiting, thread_id)
    assert visible["state_source_question"] == pending
    service = WorkspaceService()
    workspace_result = service._requirements_result({
        **visible, "app_id": "app-state-source", "status": "need_feedback"
    })
    resource_question = workspace_result["resource_question"]
    assert resource_question["field"] == pending["stateRef"]
    assert workspace_result["awaiting_input"] is True

    # A saved Workspace continuation must pin the answer to the same stateRef.
    prior = {
        "command_id": "pending-state-question", "app_id": "app-state-source",
        "stage": "requirements", "status": "AWAITING_INPUT",
        "result": workspace_result,
    }
    monkeypatch.setattr(repository, "get_command", lambda _command_id: prior)
    captured = {}
    monkeypatch.setattr(
        "app.workspace.service.analyze_requirements",
        lambda request: (
            captured.setdefault("request", request)
            and {"status": "need_feedback", "phase": "specs"}
        ),
    )
    try:
        service._stage_message({
            "command_id": "state-answer", "app_id": "app-state-source",
            "stage": "requirements", "action": "message",
            "payload": {"action_id": prior["command_id"], "text": "manager sets it"},
        }, advance=False)
    finally:
        service.shutdown()
    request = captured["request"]
    assert request.resource_answers == {pending["stateRef"]: "manager sets it"}
    assert request.thread_id == "app-state-source"
