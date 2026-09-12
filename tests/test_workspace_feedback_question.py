from __future__ import annotations

from typing import Any

import pytest

from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.actions import offered_actions
from app.workspace.api import WorkspaceCommandRequest
from app.workspace.conversation.contracts import (
    CommandIntent,
    RevisionInterpretation,
    RevisionPlan,
    RevisionTarget,
)
from app.workspace.conversation.feedback_envelope import (
    DecisionPayload,
    Question,
    QuestionOption,
    answer_option,
)
from app.workspace.service import WorkspaceService


def _target() -> RevisionTarget:
    return RevisionTarget(
        ref="use_case_spec:UC1",
        kind="use_case_spec",
        element_id="UC1",
        owner="requirements",
        artifact_type="USECASE_SPEC",
        artifact_version_id=7,
        display_label="UC1 specification",
    )


def _question() -> Question:
    return Question(
        question_id="question-1",
        question_version=1,
        app_id="app-1",
        source_execution_id="design-run-1",
        detected_at={
            "stage": "design",
            "artifact_ref": "class_diagram:EnrollmentControl",
        },
        base_revisions=[{"artifact_type": "USECASE_SPEC", "version_id": 7}],
        trigger={"category": "specification_gap"},
        authority_candidates=[_target()],
        prompt="Should UC1 reject an enrollment when the course is full?",
        decision_policy={
            "allowed_semantic_scopes": ["contract"],
            "allowed_change_types": ["modify"],
        },
        options=[
            QuestionOption(
                option_id="reject_when_full",
                label="Reject enrollment",
                description="Keep the current enrollment unchanged.",
                decision_payload=DecisionPayload(
                    normalized_meaning={
                        "semantic_scope": "contract",
                        "requested_effect": (
                            "Add a UC1 extension that rejects enrollment when the course is full."
                        ),
                    },
                    authoritative_target_refs=("use_case_spec:UC1",),
                ),
            )
        ],
        allow_free_text=True,
    )


def _source_command(*, status: str = "AWAITING_INPUT") -> dict[str, Any]:
    return {
        "command_id": "design-question",
        "app_id": "app-1",
        "action": "start_design",
        "stage": "design",
        "status": status,
        "payload": {},
        "result": {
            "kind": "question",
            "message": _question().prompt,
            "feedback_question": _question().model_dump(mode="json"),
        },
    }


def _plan() -> RevisionPlan:
    target = _target()
    return RevisionPlan(
        plan_digest="a" * 64,
        status="needs_confirmation",
        requested_targets=[target],
        authority_targets=[target],
        upstream_candidates=[],
        downstream_targets=[],
        execution_mode="targeted_revision",
        reason_codes=[],
        explanation="Revise the selected UC specification.",
        artifact_versions={"USECASE_SPEC": 7},
        trace_digest="b" * 64,
    )


class _Tools:
    valid = True

    def __init__(self, app_id: str) -> None:
        assert app_id == "app-1"

    def validate_targets(self, targets):
        return {"valid": self.valid, "valid_refs": [item["ref"] for item in targets]}


def test_feedback_question_offers_stable_option_and_free_text() -> None:
    offers = offered_actions(_source_command())

    assert [offer.label for offer in offers] == [
        "Reject enrollment",
        "Provide another answer",
    ]
    assert offers[0].action == "message"
    assert offers[0].payload == {
        "action_id": "design-question",
        "feedback_option_id": "reject_when_full",
        "text": "Add a UC1 extension that rejects enrollment when the course is full.",
    }
    assert offers[1].payload == {
        "action_id": "design-question",
        "feedback_free_text": True,
        "context": {"element_ref": "use_case_spec:UC1"},
    }


def test_implementation_gap_question_accepts_behavior_or_contract_feedback(
    monkeypatch,
) -> None:
    class GapTools(_Tools):
        def validate_revision_selections(self, refs):
            return {"valid": True, "valid_refs": list(refs)}

        def normalize_revision_targets(self, _refs, *, require_editable):
            assert require_editable is False
            return [_target()]

        def revision_snapshot(self):
            return {"artifact_versions": {"USECASE_SPEC": 7}}

    monkeypatch.setattr(workspace_module, "ProjectTools", GapTools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args, **_kwargs: _plan())
    service = WorkspaceService()
    try:
        result = service._implementation_needs_input_result(
            {
                "app_id": "app-1",
                "workflow": {
                    "blockingDetails": [
                        {
                            "kind": "upstream_contract_gap",
                            "taskId": "implementation-task-1",
                            "summary": "An upstream operation contract is incomplete.",
                            "sourceRef": "use_case_spec:UC1",
                            "options": [
                                {
                                    "id": "record_assignment",
                                    "label": "Record assignment",
                                    "description": "Model the actor-to-offering assignment.",
                                    "requestedEffect": (
                                        "Record the actor-to-offering assignment in the design."
                                    ),
                                },
                                {
                                    "id": "derive_assignment",
                                    "label": "Derive assignment",
                                    "description": "Declare an existing source for the assignment.",
                                    "requestedEffect": (
                                        "Declare the existing source that determines assignment."
                                    ),
                                },
                            ],
                        }
                    ]
                },
            },
            "implementation-job-1",
        )
    finally:
        service.shutdown()

    assert result["feedback_question"]["decision_policy"][
        "allowed_semantic_scopes"
    ] == ["behavior", "contract"]
    assert result["feedback_question"]["decision_policy"][
        "allowed_change_types"
    ] == ["modify", "add"]
    assert result["feedback_question"]["allow_free_text"] is True
    assert [option["option_id"] for option in result["feedback_question"]["options"]] == [
        "record_assignment",
        "derive_assignment",
    ]
    assert result["feedback_question"]["options"][0]["decision_payload"][
        "normalized_meaning"
    ]["requested_effect"] == "Record the actor-to-offering assignment in the design."


def test_feedback_answer_fields_survive_http_request_validation() -> None:
    request = WorkspaceCommandRequest.model_validate(
        {
            "action": "message",
            "action_id": "design-question",
            "feedback_option_id": "reject_when_full",
            "feedback_free_text": False,
        }
    )

    assert request.feedback_option_id == "reject_when_full"
    assert request.feedback_free_text is False


def test_option_answer_routes_without_llm_to_requirements(monkeypatch) -> None:
    source = _source_command()
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: source if command_id == "design-question" else None,
    )
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: _plan())
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "interpret_revision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an option answer must not call the LLM")
        ),
    )

    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload=dict(offered_actions(source)[0].payload),
            stage=None,
        )
    finally:
        service.shutdown()

    assert (action, stage) == ("message", "requirements")
    assert payload["feedback_decision"]["selected_option_id"] == "reject_when_full"
    assert payload["feedback_decision"]["status"] == "NORMALIZED"
    assert payload["revision_plan"]["status"] == "needs_confirmation"
    assert payload["validated_targets"][0]["ref"] == "use_case_spec:UC1"
    assert payload["_conversation_outcome"] == {"kind": "revision_plan"}


def test_free_text_answer_uses_existing_normalizer_and_same_decision(monkeypatch) -> None:
    source = _source_command()
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: _plan())
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)
    raw_answer = "Keep the student on a waiting list instead."
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "interpret_revision",
        lambda *_args, **_kwargs: CommandIntent(
            intent="revise",
            targets=["use_case_spec:UC1"],
            instruction=raw_answer,
            revision=RevisionInterpretation(
                targets=["use_case_spec:UC1"],
                semantic_scope="contract",
                requested_effect=raw_answer,
            ),
        ),
    )
    request = dict(offered_actions(source)[1].payload)
    request["text"] = raw_answer

    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1", action="message", payload=request, stage=None
        )
    finally:
        service.shutdown()

    assert (action, stage) == ("message", "requirements")
    assert payload["feedback_decision"]["answer_mode"] == "free_text"
    assert payload["feedback_decision"]["raw_answer"] == raw_answer
    assert payload["feedback_decision"]["status"] == "NORMALIZED"


def test_ambiguous_free_text_keeps_the_original_question_actions(monkeypatch) -> None:
    source = _source_command()
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "interpret_revision",
        lambda *_args, **_kwargs: workspace_module.Clarification(
            question="Should a full course reject the request or use a waitlist?"
        ),
    )
    request = dict(offered_actions(source)[1].payload)
    request["text"] = "Handle it another way."

    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1", action="message", payload=request, stage=None
        )
        result = service._dispatch(
            {
                "command_id": "clarification-1",
                "app_id": "app-1",
                "action": action,
                "stage": stage,
                "payload": payload,
            }
        )
    finally:
        service.shutdown()

    assert "awaiting_input" not in result
    assert result["conversation"]["clarification"]
    assert [item["payload"]["action_id"] for item in payload["_conversation_actions"]] == [
        "design-question",
        "design-question",
    ]


def test_decision_constraints_reach_the_requirements_instruction(monkeypatch) -> None:
    raw_question = _question().model_dump(mode="json")
    raw_question["decision_policy"]["required_preserved_constraints"] = [
        "Keep the existing enrollment unchanged on rejection."
    ]
    raw_question["options"][0]["decision_payload"]["preserved_constraints"] = [
        "Keep the existing enrollment unchanged on rejection."
    ]
    source = _source_command()
    source["result"]["feedback_question"] = raw_question
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: _plan())
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)

    service = WorkspaceService()
    try:
        _, payload, _ = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload=dict(offered_actions(source)[0].payload),
            stage=None,
        )
    finally:
        service.shutdown()

    assert payload["feedback_decision"]["preserved_constraints"] == [
        "Keep the existing enrollment unchanged on rejection."
    ]
    assert (
        "Preserve constraint: Keep the existing enrollment unchanged on rejection."
        in payload["text"]
    )


def test_stale_feedback_question_returns_clarification_before_planning(monkeypatch) -> None:
    source = _source_command()
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(
        workspace_module,
        "plan_revision",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("a stale question must not be planned")
        ),
    )
    _Tools.valid = False
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload=dict(offered_actions(source)[0].payload),
            stage=None,
        )
    finally:
        _Tools.valid = True
        service.shutdown()

    assert (action, stage) == ("message", "design")
    assert payload["_conversation_outcome"]["kind"] == "clarification"
    assert "stale" in payload["_conversation_outcome"]["question"]


def test_cross_stage_requirement_decision_uses_revision_entrypoint(monkeypatch) -> None:
    source = _source_command()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        workspace_module,
        "analyze_requirements",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cross-stage feedback must not start requirements from scratch")
        ),
    )

    def revise(edit, thread_id, *, app_id):
        captured.update(edit=edit, thread_id=thread_id, app_id=app_id)
        return {"status": "completed", "saved_stages": ["usecase_spec"]}

    monkeypatch.setattr(workspace_module, "revise_requirements_analysis", revise)
    service = WorkspaceService()
    try:
        result = service._stage_message(
            {
                "command_id": "requirements-revision",
                "app_id": "app-1",
                "action": "message",
                "stage": "requirements",
                "payload": {
                    "action_id": "design-question",
                    "text": (
                        "Add a UC1 extension that rejects enrollment when the course is full."
                    ),
                    "conversation_intent": {"intent": "revise"},
                    "validated_targets": [_target().model_dump(mode="json")],
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert captured["edit"].stage == "specs"
    assert captured["edit"].target_ids == ["UC1"]
    assert captured["thread_id"] == captured["app_id"] == "app-1"
    assert result["message"] == "Requirements analysis completed."


def test_stale_target_is_rechecked_before_requirements_revision(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(
        workspace_module,
        "revise_requirements_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a stale Decision must not enter Requirements")
        ),
    )
    _Tools.valid = False
    service = WorkspaceService()
    try:
        with pytest.raises(ValueError, match="stale"):
            service._stage_message(
                {
                    "command_id": "requirements-revision",
                    "app_id": "app-1",
                    "action": "message",
                    "stage": "requirements",
                    "payload": {
                        "text": "Apply the Decision.",
                        "feedback_decision": {"decision_id": "decision-1"},
                        "conversation_intent": {"intent": "revise"},
                        "validated_targets": [_target().model_dump(mode="json")],
                    },
                },
                advance=False,
            )
    finally:
        _Tools.valid = True
        service.shutdown()


def test_decision_closes_only_its_matching_open_question(monkeypatch) -> None:
    source = _source_command()
    decision = answer_option(
        _question(),
        option_id="reject_when_full",
        decision_id="decision-1",
        source_user_message_id="design-question",
    )
    updates: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda command_id, **changes: updates.append((command_id, changes)),
    )

    command = {
        "command_id": "requirements-revision",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {
            "action_id": "design-question",
            "feedback_decision": decision.model_dump(mode="json"),
        },
    }
    WorkspaceService._complete_feedback_question_source(command)

    assert updates[0][0] == "design-question"
    assert updates[0][1]["status"] == "COMPLETED"
    answered = {**source, **updates[0][1]}
    assert [offer.label for offer in offered_actions(answered)] == ["Continue conversation"]

    mismatched = decision.model_copy(update={"question_version": 2})
    command["payload"]["feedback_decision"] = mismatched.model_dump(mode="json")
    with pytest.raises(ValueError, match="does not match"):
        WorkspaceService._complete_feedback_question_source(command)

    command["payload"]["feedback_decision"] = decision.model_dump(mode="json")
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: answered)
    WorkspaceService._complete_feedback_question_source(command)
    assert len(updates) == 1


def test_failed_dispatch_does_not_close_the_source_question(monkeypatch) -> None:
    decision = answer_option(
        _question(),
        option_id="reject_when_full",
        decision_id="decision-1",
        source_user_message_id="design-question",
    )
    command = {
        "command_id": "requirements-revision",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "status": "QUEUED",
        "payload": {
            "action_id": "design-question",
            "feedback_decision": decision.model_dump(mode="json"),
        },
        "result": {},
    }
    closed: list[dict[str, Any]] = []
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: command)
    monkeypatch.setattr(repository, "update_command", lambda *_args, **_kwargs: command)
    monkeypatch.setattr(repository, "append_event", lambda *_args, **_kwargs: None)

    def record_closed(source: dict[str, Any]) -> None:
        closed.append(source)

    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "_dispatch",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("revision failed")),
    )
    monkeypatch.setattr(
        service,
        "_complete_feedback_question_source",
        record_closed,
    )
    try:
        with pytest.raises(RuntimeError, match="revision failed"):
            service._execute_command("requirements-revision", command)
    finally:
        service.shutdown()

    assert closed == []


def test_stale_dispatch_does_not_close_the_source_question(monkeypatch) -> None:
    decision = answer_option(
        _question(),
        option_id="reject_when_full",
        decision_id="decision-1",
        source_user_message_id="design-question",
    )
    command = {
        "command_id": "requirements-revision",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "status": "QUEUED",
        "payload": {
            "action_id": "design-question",
            "feedback_decision": decision.model_dump(mode="json"),
        },
        "result": {},
    }
    closed: list[dict[str, Any]] = []
    generic_completed: list[dict[str, Any]] = []
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: command)
    monkeypatch.setattr(repository, "update_command", lambda *_args, **_kwargs: command)
    monkeypatch.setattr(repository, "append_event", lambda *_args, **_kwargs: None)

    def record_closed(source: dict[str, Any]) -> None:
        closed.append(source)

    def record_generic_completion(source: dict[str, Any]) -> None:
        generic_completed.append(source)

    service = WorkspaceService()
    monkeypatch.setattr(service, "_dispatch", lambda *_args: service._stale_revision_result(_plan()))
    monkeypatch.setattr(
        service,
        "_complete_feedback_question_source",
        record_closed,
    )
    monkeypatch.setattr(
        service,
        "_complete_referenced_action",
        record_generic_completion,
    )
    try:
        service._execute_command("requirements-revision", command)
    finally:
        service.shutdown()

    assert closed == []
    assert generic_completed == []


def test_repeated_requirements_retry_replays_the_saved_feedback_decision(monkeypatch) -> None:
    failed = {
        "command_id": "requirements-revision",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "status": "FAILED",
        "payload": {
            "action_id": "design-question",
            "text": "Apply the saved Decision.",
            "feedback_decision": {"decision_id": "decision-1"},
            "conversation_intent": {"intent": "revise"},
            "validated_targets": [_target().model_dump(mode="json")],
        },
    }
    retry = {
        "command_id": "requirements-retry-1",
        "app_id": "app-1",
        "action": "retry_requirements",
        "stage": "requirements",
        "payload": {"action_id": "requirements-revision"},
    }
    second_retry = {
        "command_id": "requirements-retry-2",
        "app_id": "app-1",
        "action": "retry_requirements",
        "stage": "requirements",
        "payload": {"action_id": "requirements-retry-1"},
    }
    captured: dict[str, Any] = {}
    commands = {
        "requirements-revision": failed,
        "requirements-retry-1": {**retry, "status": "FAILED"},
    }

    def get_command(command_id: str) -> dict[str, Any] | None:
        return commands.get(command_id)

    monkeypatch.setattr(repository, "get_command", get_command)

    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "_stage_message",
        lambda replay, *, advance: captured.update(replay=replay, advance=advance)
        or {"message": "replayed"},
    )
    try:
        result = service._dispatch(retry)
        second_result = service._dispatch(second_retry)
    finally:
        service.shutdown()

    assert result == {"message": "replayed"}
    assert second_result == {"message": "replayed"}
    assert captured["advance"] is False
    assert captured["replay"]["payload"] == failed["payload"]


def test_feedback_retry_depth_fails_closed(monkeypatch) -> None:
    commands: dict[str, dict[str, Any]] = {}
    previous_id = "requirements-revision"
    for index in range(1, 14):
        command_id = f"requirements-retry-{index}"
        commands[command_id] = {
            "command_id": command_id,
            "app_id": "app-1",
            "action": "retry_requirements",
            "stage": "requirements",
            "status": "FAILED",
            "payload": {"action_id": previous_id},
        }
        previous_id = command_id

    def get_command(command_id: str) -> dict[str, Any] | None:
        return commands.get(command_id)

    monkeypatch.setattr(repository, "get_command", get_command)

    with pytest.raises(ValueError, match="too deep"):
        WorkspaceService._feedback_question_command(commands[previous_id])


def test_design_result_exposes_only_supported_feedback_question() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "app_id": "app-1",
                "status": "need_feedback",
                "stage": "class_diagram",
                "feedback_question": _question().model_dump(mode="json"),
            }
        )
        unsupported = _question().model_dump(mode="json")
        unsupported["trigger"] = {"category": "design_choice"}
        with pytest.raises(ValueError, match="unsupported feedback question"):
            service._design_result(
                {
                    "app_id": "app-1",
                    "status": "need_feedback",
                    "stage": "class_diagram",
                    "feedback_question": unsupported,
                }
            )
    finally:
        service.shutdown()

    assert result["kind"] == "question"
    assert result["feedback_question"]["question_id"] == "question-1"
