from __future__ import annotations

from typing import Any

from app.workspace import service as workspace_module
from app.workspace.conversation.agent import ConversationAgent
from app.workspace.conversation.context import ConversationContext
from app.workspace.conversation.contracts import Clarification, CommandIntent, Reply
from app.workspace.service import WorkspaceService


def context(*, actions: list[dict[str, Any]] | None = None) -> ConversationContext:
    return ConversationContext(
        app_id="app-1",
        workspace={"stage": "design", "status": "AWAITING_INPUT"},
        actions=actions or [],
    )


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.matches = [
            {
                "ref": "class_diagram:OrderService",
                "label": "OrderService",
                "owner": "design",
                "editable": True,
            }
        ]

    def read_workspace(self):
        self.calls.append(("read_workspace", None))
        return {"stage": "design", "status": "AWAITING_INPUT"}

    def search_elements(self, query: str):
        self.calls.append(("search_elements", query))
        return list(self.matches)

    def validate_targets(self, refs):
        refs = list(refs)
        self.calls.append(("validate_targets", refs))
        return {
            "valid": bool(refs),
            "valid_refs": refs,
            "existing_refs": refs,
        }

    def read_element(self, ref: str):
        self.calls.append(("read_element", ref))
        return {"ref": ref, "content": {"operations": ["placeOrder"]}}


def test_general_reply_does_not_read_project_state() -> None:
    def propose(schema, _messages):
        assert schema.__name__ == "_ConversationPlan"
        return schema(kind="reply", reply="안녕하세요. 무엇을 함께 살펴볼까요?")

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1", "안녕", context(), tools=tools
    )

    assert isinstance(result, Reply)
    assert result.text.startswith("안녕하세요")
    assert tools.calls == []


def test_project_question_is_answered_from_read_only_tool_evidence() -> None:
    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="project_question", query="OrderService operations")
        return schema(text="OrderService에는 placeOrder 연산이 있습니다.")

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1", "주문 서비스에 어떤 연산이 있어?", context(), tools=tools
    )

    assert isinstance(result, Reply)
    assert "placeOrder" in result.text
    assert [name for name, _ in tools.calls] == [
        "read_workspace",
        "search_elements",
        "validate_targets",
        "read_element",
    ]


def test_revision_can_only_select_a_finite_validated_ref() -> None:
    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="OrderService")
        return schema(targets=["invented:ref", "class_diagram:OrderService"])

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1",
        "OrderService의 주문 메서드를 바꿔줘",
        context(),
        tools=tools,
    )

    assert isinstance(result, CommandIntent)
    assert result.intent == "revise"
    assert result.targets == ["class_diagram:OrderService"]
    assert ("validate_targets", ["class_diagram:OrderService"]) in tools.calls


def test_ambiguous_revision_returns_clarification_without_execution() -> None:
    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="unknown")
        raise AssertionError("target selection must not run without candidates")

    tools = FakeTools()
    tools.matches = []
    result = ConversationAgent(propose).respond(
        "app-1", "저 부분을 고쳐줘", context(), tools=tools
    )

    assert isinstance(result, Clarification)
    assert result.candidates == []


def _completed_command(stage: str = "requirements") -> dict[str, Any]:
    return {
        "command_id": f"{stage}-command",
        "app_id": "app-1",
        "action": "message",
        "stage": stage,
        "status": "COMPLETED",
        "payload": {},
        "result": {},
    }


def test_natural_advance_uses_the_same_published_transition(monkeypatch) -> None:
    latest = _completed_command()
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: CommandIntent(intent="advance", instruction="다음으로 가자"),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "다음으로 가자", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "start_design"
    assert payload["action_id"] == latest["command_id"]
    assert payload["conversation_intent"]["intent"] == "advance"
    assert stage is None


def test_general_reply_preserves_the_underlying_workflow_actions(monkeypatch) -> None:
    latest = _completed_command("design")
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: Reply(text="현재 설계를 함께 살펴볼 수 있습니다."),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "도와줄 수 있어?", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "message"
    assert payload["_conversation_outcome"]["kind"] == "reply"
    assert [item["action"] for item in payload["_conversation_actions"]] == [
        "message",
        "start_implementation",
    ]
    assert stage == "design"


def test_conversation_failure_becomes_a_retryable_persisted_clarification(
    monkeypatch,
) -> None:
    latest = _completed_command("design")
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())

    def fail(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(workspace_module.conversation_agent, "respond", fail)
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Can you help?", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "message"
    assert payload["text"] == "Can you help?"
    assert payload["_conversation_outcome"]["kind"] == "clarification"
    assert "retry" in payload["_conversation_outcome"]["question"].lower()
    assert stage == "design"


def test_natural_followup_uses_actions_preserved_by_a_reply(monkeypatch) -> None:
    latest = {
        **_completed_command("design"),
        "command_id": "reply-command",
        "payload": {
            "_conversation_actions": [
                {
                    "action": "delegate_repair",
                    "label": "Delegate repair to LLM",
                    "payload": {"action_id": "repair-command"},
                }
            ]
        },
    }
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: CommandIntent(
            intent="delegate_repair", instruction="Please repair it."
        ),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Please repair it.", "action_id": "reply-command"},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "delegate_repair"
    assert payload["action_id"] == "repair-command"
    assert stage is None
