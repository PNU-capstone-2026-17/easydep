from __future__ import annotations

from typing import Any

from app.workspace import service as workspace_module
from app.workspace.conversation.agent import ConversationAgent
from app.workspace.conversation.context import ConversationContext, ConversationTurn
from app.workspace.conversation.contracts import (
    Clarification,
    CommandIntent,
    ConversationIntent,
    Reply,
    RevisionPlan,
    RevisionTarget,
    RevisionInterpretation,
)
from app.workspace.service import WorkspaceService


def _context(*, pending_question: str | None = None) -> ConversationContext:
    return ConversationContext(
        app_id="app-1",
        workspace={"stage": "design", "status": "AWAITING_INPUT"},
        pending_question=pending_question,
    )


class _FakeTools:
    def __init__(self, matches: list[dict[str, Any]] | None = None) -> None:
        self.matches = matches or [
            {
                "ref": "class_diagram:OrderService",
                "label": "OrderService",
                "owner": "design",
                "editable": True,
            }
        ]
        self.calls: list[tuple[str, object]] = []

    def search_elements(self, query: str) -> list[dict[str, Any]]:
        self.calls.append(("search_elements", query))
        return list(self.matches)

    def validate_targets(self, refs: list[str]) -> dict[str, Any]:
        self.calls.append(("validate_targets", list(refs)))
        return {
            "valid": bool(refs),
            "valid_refs": list(refs),
            "existing_refs": list(refs),
        }

    validate_revision_selections = validate_targets


def test_followup_executes_resolved_request_instead_of_only_target_name() -> None:
    context = _context(pending_question="Which class should change?")
    context.turns = [
        ConversationTurn(role="user", text="Require a customer id when placing orders.", command_id="request"),
        ConversationTurn(role="assistant", text="Which class should change?", command_id="request"),
    ]
    instruction = "Require a customer id when OrderService places orders."

    def propose(schema, messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="OrderService")
        assert "Require a customer id" in messages[-1].content
        return schema(targets=["class_diagram:OrderService"], semantic_scope="behavior", requested_effect=instruction)

    result = ConversationAgent(propose).respond(
        "app-1", "OrderService", context, tools=_FakeTools()
    )
    assert isinstance(result, CommandIntent)
    assert result.instruction == instruction
    assert result.revision.requested_effect == instruction


def test_repair_linked_message_uses_repair_stage_as_conversation_context(monkeypatch) -> None:
    repair = {
        "command_id": "repair-command",
        "app_id": "app-1",
        "stage": "design",
        "status": "AWAITING_INPUT",
        "result": {"repair_state": {"status": "WAITING_INPUT"}},
    }
    current = {
        "command_id": "requirements-command",
        "app_id": "app-1",
        "stage": "requirements",
        "status": "COMPLETED",
        "payload": {},
        "result": {},
    }
    monkeypatch.setattr(
        workspace_module.repository,
        "latest_command",
        lambda *_a, **_k: current,
    )
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        lambda command_id: repair if command_id == "repair-command" else None,
    )
    context = _context()
    context.workspace["stage"] = "requirements"
    monkeypatch.setattr(
        workspace_module, "build_conversation_context", lambda _app: context,
    )
    observed: dict[str, Any] = {}

    def respond(_app, _text, conversation_context, **_kwargs):
        observed["stage"] = conversation_context.workspace["stage"]
        return Reply(text="Acknowledged.")

    monkeypatch.setattr(workspace_module.conversation_agent, "respond", respond)
    service = WorkspaceService()
    try:
        service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Please fix the review feedback.", "action_id": "repair-command"},
            stage=None,
        )
    finally:
        service.shutdown()

    assert observed["stage"] == "design"


def test_repair_linked_revision_requires_explicit_cross_stage_target(monkeypatch) -> None:
    repair = {
        "command_id": "repair-command",
        "app_id": "app-1",
        "stage": "design",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {
            "repair_state": {"status": "WAITING_INPUT"},
            "actions": [
                {
                    "action": "message",
                    "label": "Send feedback",
                    "payload": {"action_id": "repair-command"},
                }
            ],
        },
    }
    requirement_target = RevisionTarget(
        ref="use_case_spec:UC5",
        kind="use_case_spec",
        element_id="UC5",
        owner="requirements",
        artifact_type="USECASE_SPEC",
        display_label="UC5",
    )

    class _Tools:
        def __init__(self, _app_id: str) -> None:
            pass

        def trace_impact(self, _refs, *, view):
            return {"view": view}

    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(
        workspace_module,
        "plan_revision",
        lambda *_a: RevisionPlan(
            plan_digest="a" * 64,
            status="needs_confirmation",
            explanation="Review and confirm this bounded revision.",
            upstream_candidates=[],
            authority_targets=[requirement_target],
            requested_targets=[requirement_target],
            execution_mode="targeted_revision",
            reason_codes=["free_text_revision_requires_confirmation"],
            artifact_versions={},
            trace_digest="b" * 64,
        ),
    )
    intent = CommandIntent(
        intent=ConversationIntent.REVISE,
        instruction="Change this behavior.",
        targets=[requirement_target.ref],
        revision=RevisionInterpretation(
            targets=[requirement_target.ref],
            semantic_scope="behavior",
            requested_effect="Change this behavior.",
        ),
    )
    service = WorkspaceService()
    try:
        blocked = service._route_conversation_intent(
            "app-1",
            {
                "text": "Please fix the review feedback.",
                "action_id": "repair-command",
            },
            intent,
            repair,
        )
        ambiguous = service._route_conversation_intent(
            "app-1",
            {"text": "Please change UC5.", "action_id": "repair-command"},
            intent,
            repair,
        )
        negated_stage = service._route_conversation_intent(
            "app-1",
            {
                "text": "Keep the requirements unchanged.",
                "action_id": "repair-command",
            },
            intent,
            repair,
        )
        explicit = service._route_conversation_intent(
            "app-1",
            {
                "text": "Please change use_case_spec:UC5.",
                "action_id": "repair-command",
            },
            intent,
            repair,
        )
    finally:
        service.shutdown()

    assert blocked[0] == "message"
    assert blocked[2] == "design"
    assert blocked[1]["_conversation_outcome"]["kind"] == "clarification"
    assert ambiguous[2] == "design"
    assert ambiguous[1]["_conversation_outcome"]["kind"] == "clarification"
    assert negated_stage[2] == "design"
    assert negated_stage[1]["_conversation_outcome"]["kind"] == "clarification"
    assert explicit[0] == "message"
    assert explicit[2] == "requirements"
    assert explicit[1]["_conversation_outcome"]["kind"] == "revision_plan"
    assert explicit[1]["revision_plan"]["status"] == "needs_confirmation"


def test_revision_uses_one_structured_selection_call_for_full_interpretation() -> None:
    calls: list[type] = []

    def propose(schema, _messages):
        calls.append(schema)
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="OrderService")
        assert schema is RevisionInterpretation
        return schema(
            targets=["class_diagram:OrderService"],
            semantic_scope="behavior",
            requested_effect="Change the order placement behavior.",
            clarification="",
        )

    result = ConversationAgent(propose).respond(
        "app-1",
        "Change how OrderService places an order.",
        _context(),
        tools=_FakeTools(),
    )

    assert len(calls) == 2
    assert calls[0].__name__ == "_ConversationPlan"
    assert calls[1] is RevisionInterpretation
    assert isinstance(result, CommandIntent)
    assert result.intent == ConversationIntent.REVISE
    assert result.targets == ["class_diagram:OrderService"]
    assert result.revision.semantic_scope == "behavior"
    assert result.revision.requested_effect == "Change how OrderService places an order."
    assert result.revision.clarification == ""


def test_revision_discards_refs_that_are_not_in_the_candidate_set() -> None:
    calls: list[type] = []

    def propose(schema, _messages):
        calls.append(schema)
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="OrderService")
        return schema(
            targets=["invented:ref", "class_diagram:OrderService"],
            semantic_scope="presentation",
            requested_effect="Rename the displayed service label.",
        )

    tools = _FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1",
        "Rename OrderService's displayed label.",
        _context(),
        tools=tools,
    )

    assert len(calls) == 2
    assert isinstance(result, CommandIntent)
    assert result.targets == ["class_diagram:OrderService"]
    assert ("validate_targets", ["class_diagram:OrderService"]) in tools.calls
    assert ("validate_targets", ["invented:ref", "class_diagram:OrderService"]) not in tools.calls


def test_ambiguous_scope_or_target_returns_one_clarification_without_execution() -> None:
    calls: list[type] = []
    matches = [
        {
            "ref": "class_diagram:OrderService",
            "label": "OrderService class",
            "owner": "design",
            "editable": True,
        },
        {
            "ref": "sequence_diagram:PlaceOrder",
            "label": "PlaceOrder sequence",
            "owner": "design",
            "editable": True,
        },
    ]

    def propose(schema, _messages):
        calls.append(schema)
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="place order")
        return schema(
            targets=[],
            semantic_scope="unknown",
            requested_effect="Make it work the way the user expects.",
            clarification="Which artifact and which behavior should change?",
        )

    tools = _FakeTools(matches)
    result = ConversationAgent(propose).respond(
        "app-1",
        "Make the place-order behavior better.",
        _context(),
        tools=tools,
    )

    assert len(calls) == 2
    assert isinstance(result, Clarification)
    assert result.question == "Which artifact and which behavior should change?"
    assert result.candidates == ["OrderService class", "PlaceOrder sequence"]
    assert not any(name == "read_element" for name, _ in tools.calls)


def test_revision_command_preserves_interpretation_metadata() -> None:
    interpretation = {
        "targets": ["class_diagram:OrderService"],
        "semantic_scope": "contract",
        "requested_effect": "Require a customer id in the operation.",
        "clarification": "",
    }

    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="OrderService")
        return schema(**interpretation)

    result = ConversationAgent(propose).respond(
        "app-1",
        "Require a customer id in OrderService.",
        _context(),
        tools=_FakeTools(),
    )

    assert isinstance(result, CommandIntent)
    assert result.revision == RevisionInterpretation(
        **{
            **interpretation,
            "requested_effect": "Require a customer id in OrderService.",
        }
    )
    assert result.revision.targets == result.targets


def test_plain_affirmative_without_pending_plan_is_not_an_answer_approval(monkeypatch) -> None:
    latest = {
        "command_id": "completed-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "design",
        "status": "COMPLETED",
        "payload": {},
        "result": {},
    }
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        lambda *_a, **_k: latest,
    )
    monkeypatch.setattr(
        workspace_module,
        "build_conversation_context",
        lambda _app: _context(pending_question=None),
    )
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: CommandIntent(
            intent=ConversationIntent.CONFIRM_REVISION,
            instruction="yes",
        ),
    )

    service = WorkspaceService()
    try:
        action, payload, _stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "yes", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "message"
    assert payload["_conversation_outcome"]["kind"] == "clarification"
    assert "not available" in payload["_conversation_outcome"]["question"]


def test_clarification_after_reply_reuses_its_preserved_message_action() -> None:
    reply = {
        "command_id": "reply-command",
        "app_id": "app-1",
        "stage": "requirements",
        "status": "COMPLETED",
        "payload": {
            "_conversation_actions": [
                {
                    "action": "message",
                    "label": "Send revision feedback",
                    "payload": {"action_id": "stage-gate"},
                }
            ]
        },
        "result": {},
    }

    action, payload, stage = WorkspaceService._clarification_message(
        {"text": "Change UC2.", "action_id": "reply-command"},
        Clarification(question="Which UC2 artifact?"),
        None,
        reply,
    )

    assert (action, stage) == ("message", "requirements")
    assert payload["action_id"] == "stage-gate"


def test_single_selected_feedback_pins_the_ref_and_interprets_only_effect(
    monkeypatch,
) -> None:
    latest = {
        "command_id": "stage-gate",
        "app_id": "app-1",
        "action": "advance",
        "stage": "design",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {
            "wait_reason": "review",
            "actions": [
                {
                    "action": "message",
                    "label": "Send revision feedback",
                    "payload": {"action_id": "stage-gate"},
                    "auto_selectable": False,
                }
            ],
        },
    }
    selected_ref = "sequence_diagram:UC4"
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        workspace_module.repository, "latest_command", lambda *_a, **_k: latest,
    )
    monkeypatch.setattr(
        workspace_module, "build_conversation_context", lambda _app: _context(),
    )

    def interpret(text, refs, **_kwargs):
        observed["text"] = text
        observed["refs"] = refs
        observed["sealed_targets"] = _kwargs.get("sealed_targets")
        revision = RevisionInterpretation(
            targets=[
                "class_diagram:MemberBoundary::cancelReservation()",
                "sequence_diagram:UC4",
            ],
            semantic_scope="behavior",
            requested_effect=text,
        )
        return CommandIntent(
            intent=ConversationIntent.REVISE,
            targets=list(revision.targets),
            instruction=text,
            revision=revision,
        )

    monkeypatch.setattr(
        workspace_module.conversation_agent, "interpret_revision", interpret,
    )

    def route(_self, _app_id, payload, intent, _latest):
        observed["payload"] = payload
        observed["intent"] = intent
        return "message", payload, "design"

    monkeypatch.setattr(WorkspaceService, "_route_conversation_intent", route)
    service = WorkspaceService()
    try:
        service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={
                "text": "Rename MemberBoundary.cancelReservation.",
                "action_id": "stage-gate",
                "context": {
                    "artifact_stage": "sequence_diagram",
                    "element_ref": selected_ref,
                },
            },
            stage=None,
        )
    finally:
        service.shutdown()

    assert observed["text"] == "Rename MemberBoundary.cancelReservation."
    assert observed["refs"] == [selected_ref]
    assert observed["sealed_targets"] is True
    assert observed["payload"]["revision_instructions"] == {
        selected_ref: "Rename MemberBoundary.cancelReservation."
    }
    assert observed["intent"].targets == [selected_ref]
    assert observed["intent"].revision.targets == [selected_ref]
