from __future__ import annotations

from typing import Any

import pytest

from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.actions import WorkspaceAction, action_is_offered, offered_actions
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


def _class_binding_question() -> Question:
    target = _target()
    return Question(
        question_id="class-binding-source:UC1:question-1",
        question_version=1,
        app_id="app-1",
        source_execution_id="design-run-1",
        detected_at={"stage": "design", "artifact_ref": target.ref},
        base_revisions=[{"artifact_type": "USECASE_SPEC", "version_id": 7}],
        trigger={
            "category": "class_binding_source",
            "binding_slot": {
                "useCaseId": "UC1",
                "actorEntryIndex": 0,
                "callIndex": 1,
                "parameterIndex": 0,
                "receiverOperationId": "CourseControl.register",
                "parameterName": "courseId",
            },
        },
        authority_candidates=[target],
        prompt="Where should this value come from?",
        decision_policy={
            "allowed_semantic_scopes": ["contract"],
            "allowed_change_types": ["modify"],
            "required_preserved_constraints": ["Keep the existing behavior."],
        },
        options=[
            QuestionOption(
                option_id="use_case_input",
                label="Use case input",
                description="Supply it when the use case begins.",
                decision_payload=DecisionPayload(
                    normalized_meaning={
                        "semantic_scope": "contract",
                        "requested_effect": "Supply this value when the use case begins.",
                        "change_type": "modify",
                    },
                    authoritative_target_refs=(target.ref,),
                    preserved_constraints=("Keep the existing behavior.",),
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

    def validate_revision_selections(self, targets):
        return {
            'valid': self.valid,
            'valid_refs': [
                item['ref'] if isinstance(item, dict) else item for item in targets
            ],
        }

    def __init__(self, app_id: str) -> None:
        assert app_id == "app-1"

    def validate_targets(self, targets):
        return {"valid": self.valid, "valid_refs": [item["ref"] for item in targets]}


def _identity_source_candidate() -> dict[str, Any]:
    return {
        "useCaseId": "UC1",
        "obligationRef": "identify-student",
        "requirementIds": ["FR-1"],
        "prompt": "Where does the student identity come from?",
        "options": [
            {
                "id": "caller_input",
                "label": "Supplied by the caller",
                "description": "The caller supplies the identity.",
                "identitySourceKind": "caller_input",
            },
            {
                "id": "authenticated_context:authenticate-student",
                "label": "Authenticated student context",
                "description": "Use the established authenticated student.",
                "identitySourceKind": "authenticated_context",
                "sourceAuthenticateObligationRef": "authenticate-student",
            },
        ],
    }


class _IdentityTools(_Tools):
    version = 7

    def normalize_revision_targets(self, refs, *, require_editable=True):
        assert refs == ["use_case_spec:UC1"]
        assert require_editable is False
        target = _target()
        return [target.model_copy(update={"artifact_version_id": self.version})]

    def current_revision_target(self, target):
        assert isinstance(target, RevisionTarget)
        assert target.ref == "use_case_spec:UC1"
        return target.model_copy(update={"artifact_version_id": self.version})


def _identity_source_command() -> dict[str, Any]:
    candidate = _identity_source_candidate()
    service = WorkspaceService()
    try:
        question = service._identity_source_question("app-1", candidate)
    finally:
        service.shutdown()
    assert question is not None
    return {
        "command_id": "identity-question",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {
            "feedback_question": question.model_dump(mode="json"),
            "identity_source_question": candidate,
        },
    }


def test_identity_source_option_resumes_typed_answer_without_revision_plan(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "ProjectTools", _IdentityTools)
    source = _identity_source_command()
    service = WorkspaceService()
    try:
        action, payload, stage = service._route_identity_source_answer(
            "app-1",
            {"action_id": "identity-question", "feedback_option_id": "authenticated_context:authenticate-student"},
            None,
            source,
            source,
            source["result"]["identity_source_question"],
        )
    finally:
        service.shutdown()
    assert (action, stage) == ("message", "requirements")
    assert payload["identity_source_answer"] == {
        "use_case_id": "UC1",
        "obligation_ref": "identify-student",
        "identity_source_kind": "authenticated_context",
        "source_authenticate_obligation_ref": "authenticate-student",
    }
    assert payload["_conversation_outcome"] == {"kind": "identity_source_retry"}
    assert "revision_plan" not in payload
    assert "feedback_decision" not in payload


def test_offered_identity_option_is_accepted_and_client_answer_is_replaced(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "ProjectTools", _IdentityTools)
    source = _identity_source_command()
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: source)
    monkeypatch.setattr(repository, "get_command", lambda _command_id: source)
    offer = offered_actions(source)[0]
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action=offer.action.value,
            payload={
                **offer.payload,
                "identity_source_answer": {
                    "use_case_id": "UC999",
                    "obligation_ref": "forged",
                    "identity_source_kind": "caller_input",
                },
            },
            stage=None,
        )
    finally:
        service.shutdown()

    assert (action, stage) == ("message", "requirements")
    assert payload["identity_source_answer"] == {
        "use_case_id": "UC1",
        "obligation_ref": "identify-student",
        "identity_source_kind": "authenticated_context",
        "source_authenticate_obligation_ref": "authenticate-student",
    }
    assert action_is_offered(action, payload, source)


def test_identity_source_rejects_stale_artifact_or_foreign_question(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "ProjectTools", _IdentityTools)
    source = _identity_source_command()
    _IdentityTools.version = 8
    service = WorkspaceService()
    try:
        _, stale, _ = service._route_identity_source_answer(
            "app-1", {"feedback_option_id": "caller_input"}, None, source, source,
            source["result"]["identity_source_question"],
        )
        foreign = {**source, "app_id": "other-app"}
        _, wrong_app, _ = service._route_identity_source_answer(
            "app-1", {"feedback_option_id": "caller_input"}, None, source, foreign,
            source["result"]["identity_source_question"],
        )
    finally:
        _IdentityTools.version = 7
        service.shutdown()
    assert stale["_conversation_outcome"]["kind"] == "clarification"
    assert wrong_app["_conversation_outcome"]["kind"] == "clarification"


def test_identity_source_rejects_tampered_option_and_nonexact_free_text(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "ProjectTools", _IdentityTools)
    source = _identity_source_command()
    service = WorkspaceService()
    try:
        _, tampered, _ = service._route_identity_source_answer(
            "app-1", {"feedback_option_id": "system_result"}, None, source, source,
            source["result"]["identity_source_question"],
        )
        _, prose, _ = service._route_identity_source_answer(
            "app-1", {"text": "use the logged-in student"}, None, source, source,
            source["result"]["identity_source_question"],
        )
    finally:
        service.shutdown()
    assert tampered["_conversation_outcome"]["kind"] == "clarification"
    assert prose["_conversation_outcome"]["kind"] == "clarification"


def test_identity_source_answer_refreshes_existing_downstream_handoff(monkeypatch) -> None:
    source = {
        "command_id": "identity-question",
        "app_id": "app-1",
        "status": "AWAITING_INPUT",
        "result": {
            "downstream_revision_handoff": {
                "source_targets": [
                    {"ref": "use_case_spec:UC8", "artifact_version_id": 7}
                ],
                "semantic_scope": "contract",
                "requested_effect": "Use authenticated session identity.",
                "change_type": "modify",
            }
        },
    }

    class CurrentSpecTools:
        def __init__(self, app_id):
            assert app_id == "app-1"

        def normalize_revision_targets(self, refs, *, require_editable):
            assert refs == ["use_case_spec:UC8"]
            assert require_editable is False
            return [_target().model_copy(
                update={
                    "ref": "use_case_spec:UC8",
                    "element_id": "UC8",
                    "artifact_version_id": 8,
                }
            )]

    monkeypatch.setattr(workspace_module, "ProjectTools", CurrentSpecTools)
    monkeypatch.setattr(repository, "get_command", lambda _command_id: source)
    command = {
        "app_id": "app-1",
        "payload": {
            "action_id": "identity-question",
            "identity_source_answer": {
                "use_case_id": "UC8",
                "obligation_ref": "ob_identify",
                "identity_source_kind": "authenticated_context",
                "source_authenticate_obligation_ref": "ob_authenticate",
            },
        },
    }

    result = WorkspaceService._refresh_identity_source_downstream_handoff(
        command, {"awaiting_input": True, "kind": "action_required"}
    )

    assert result["downstream_revision_handoff"]["source_targets"] == [
        {"ref": "use_case_spec:UC8", "artifact_version_id": 8}
    ]
    assert result["downstream_revision_handoff"]["requested_effect"] == (
        "Use authenticated session identity."
    )


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


def test_design_validation_option_routes_through_the_pinned_plan(monkeypatch) -> None:
    question = _question().model_dump(mode="json")
    question["trigger"] = {"category": "design_validation_input"}
    source = _source_command()
    source["result"]["feedback_question"] = question
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: _plan())
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "interpret_revision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a grounded option answer must not call the LLM")
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
    assert payload["feedback_decision"]["status"] == "NORMALIZED"
    assert payload["revision_plan"]["authority_targets"] == [_target().model_dump(mode="json")]


def test_class_binding_answer_retries_the_same_design_session(monkeypatch) -> None:
    source = _source_command()
    source["result"]["feedback_question"] = _class_binding_question().model_dump(
        mode="json"
    )
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)

    service = WorkspaceService()
    try:
        offered_payload = dict(offered_actions(source)[0].payload)
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload=offered_payload,
            stage=None,
        )
        service._validate_action_reference("app-1", action, payload)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            workspace_module,
            "retry_design_session",
            lambda app_id, *, repair_guidance, binding_source_decision=None: captured.update(
                app_id=app_id,
                repair_guidance=repair_guidance,
                binding_source_decision=binding_source_decision,
            ) or {"status": "completed"},
        )
        monkeypatch.setattr(
            service,
            "_run_design_operation",
            lambda _command, *, operation, **_kwargs: operation(),
        )
        monkeypatch.setattr(service, "_design_result", lambda result: result)
        result = service._dispatch(
            {
                "command_id": "class-binding-retry-1",
                "app_id": "app-1",
                "action": action,
                "stage": stage,
                "payload": payload,
            }
        )
    finally:
        service.shutdown()

    assert (action, stage) == ("message", "design")
    assert payload["_conversation_outcome"] == {
        "kind": "class_binding_retry",
        "binding_source_decision": {
            "useCaseId": "UC1",
            "actorEntryIndex": 0,
            "callIndex": 1,
            "parameterIndex": 0,
            "receiverOperationId": "CourseControl.register",
            "parameterName": "courseId",
            "sourceKind": "use_case_input",
        },
    }
    source_decision = payload["_conversation_outcome"]["binding_source_decision"]
    assert captured == {
        "app_id": "app-1",
        "repair_guidance": offered_payload["text"],
        "binding_source_decision": source_decision,
    }
    assert result == {"status": "completed"}


def test_semantic_ambiguity_option_routes_pinned_spec_effect_to_requirements(monkeypatch) -> None:
    question = _question().model_dump(mode="json")
    question["detected_at"]["stage"] = "requirements"
    question["trigger"] = {"category": "semantic_ambiguity"}
    question["decision_policy"]["allowed_semantic_scopes"] = ["behavior"]
    question["options"][0]["decision_payload"]["normalized_meaning"][
        "semantic_scope"
    ] = "behavior"
    requested_effect = "Specify that a full course places the student on the waitlist."
    question["options"][0]["decision_payload"]["normalized_meaning"][
        "requested_effect"
    ] = requested_effect
    source = _source_command()
    source["stage"] = "requirements"
    source["result"]["feedback_question"] = question
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: source if command_id == "design-question" else None,
    )
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: _plan())
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)

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
    assert payload["text"] == requested_effect
    assert payload["feedback_decision"]["selected_option_id"] == "reject_when_full"
    assert payload["validated_targets"] == [_target().model_dump(mode="json")]
    assert payload["validated_targets"][0]["artifact_version_id"] == 7


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


def test_free_text_question_seals_the_supplied_authority(monkeypatch) -> None:
    source = _source_command()
    observed: dict[str, Any] = {}
    raw_answer = 'Keep the student on a waiting list instead.'
    monkeypatch.setattr(repository, 'latest_command', lambda *_args, **_kwargs: source)
    monkeypatch.setattr(repository, 'get_command', lambda *_args, **_kwargs: source)
    monkeypatch.setattr(workspace_module, 'ProjectTools', _Tools)
    monkeypatch.setattr(workspace_module, 'plan_revision', lambda *_args: _plan())
    monkeypatch.setattr(workspace_module, 'validate_plan', lambda *_args: True)

    def interpret(_text, refs, **kwargs):
        observed['refs'] = refs
        observed['sealed_targets'] = kwargs.get('sealed_targets')
        revision = RevisionInterpretation(
            targets=['use_case_spec:UC1'],
            semantic_scope='contract',
            requested_effect=raw_answer,
        )
        return CommandIntent(
            intent='revise',
            targets=list(revision.targets),
            instruction=raw_answer,
            revision=revision,
        )

    monkeypatch.setattr(
        workspace_module.conversation_agent,
        'interpret_revision',
        interpret,
    )
    request = dict(offered_actions(source)[1].payload)
    request['text'] = raw_answer

    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            'app-1', action='message', payload=request, stage=None
        )
    finally:
        service.shutdown()

    assert (action, stage) == ('message', 'requirements')
    assert observed == {
        'refs': ['use_case_spec:UC1'],
        'sealed_targets': True,
    }
    assert payload['feedback_decision']['status'] == 'NORMALIZED'


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
    monkeypatch.setattr(repository, "append_progress_event", lambda *_args, **_kwargs: None)

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
    monkeypatch.setattr(repository, "append_progress_event", lambda *_args, **_kwargs: None)

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


def test_design_finding_detail_becomes_free_text_question_without_advance(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_module.ProjectTools,
        "normalize_revision_targets",
        lambda _self, _refs: [_target()],
    )
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "app_id": "app-1",
                "stage": "sequence_diagram",
                "validation": {
                    "sequence_diagram": {
                        "findings": ["UC1 needs a business decision."],
                        "finding_details": [
                            {
                                "rule_id": "sequence.decision",
                                "message": "Should UC1 keep the current behavior?",
                                "location": "UC1",
                                "requires_user_input": True,
                                "origin": "semantic",
                                "authority_ref": "sequence_diagram:UC1",
                            }
                        ],
                    }
                },
            }
        )
    finally:
        service.shutdown()

    assert result["kind"] == "question"
    assert result["feedback_question"]["allow_free_text"] is True
    assert result["feedback_question"]["options"] == []
    assert result["design_can_advance"] is False
    assert WorkspaceAction.ADVANCE not in {
        offer.action
        for offer in offered_actions(
            {
                "command_id": "design-question",
                "stage": "design",
                "status": "AWAITING_INPUT",
                "result": result,
            }
        )
    }


def test_design_technical_repair_offers_no_generic_revision_message() -> None:
    actions = offered_actions(
        {
            "command_id": "design-repair",
            "stage": "design",
            "status": "AWAITING_INPUT",
            "result": {
                "awaiting_input": True,
                "kind": "action_required",
                "requires_revision": True,
                "blocking_findings": [{"message": "Missing required step."}],
                "repair_state": {"status": "STALLED"},
            },
        }
    )

    assert actions == []


def test_stalled_targeted_revision_keeps_accepted_artifact_readiness(monkeypatch) -> None:
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "_design_result",
        lambda result: {
            "awaiting_input": True,
            "kind": "action_required",
            "message": "Review the current design artifacts.",
            "requires_revision": False,
            "findings": list(result["validation"]["class_diagram"]["findings"]),
        },
    )
    try:
        result = service._targeted_design_result(
            "app-1",
            {
                "revision_status": "stalled",
                "changed": [],
                "validation": {"class_diagram": {"findings": []}},
                "revision_validation": {
                    "findings": ["Rejected candidate is incomplete."]
                },
            },
            {"stage": "class_diagram"},
            message="Revised the selected element.",
        )
    finally:
        service.shutdown()

    assert result["message"] == (
        "Requested revision could not be validated; the previous artifact is unchanged."
    )
    assert result["changed"] == []
    assert result["findings"] == []
    assert result["revision_validation"] == {
        "findings": ["Rejected candidate is incomplete."]
    }
