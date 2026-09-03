"""Workspace 대화형 project tool의 읽기·버전·owner 경계를 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.db.models import (
    TYPE_API_SPEC,
    TYPE_CLASS,
    TYPE_REFINE_REQ,
    TYPE_SEQUENCE,
    TYPE_SOURCE_CODE,
    TYPE_USECASE_SPEC,
)
from app.workspace.conversation import project_tools as project_tools_module
from app.workspace.conversation.context import build_conversation_context
from app.workspace.conversation.contracts import Clarification, CommandIntent, Reply
from app.workspace.conversation.project_tools import ProjectTools

APP_ID = "11111111-1111-4111-8111-111111111111"


def _state() -> dict[str, Any]:
    return {
        "artifact_versions": {
            TYPE_REFINE_REQ: {"version_id": 11, "version_no": 1},
            TYPE_USECASE_SPEC: {"version_id": 12, "version_no": 2},
            TYPE_CLASS: {"version_id": 21, "version_no": 3},
            TYPE_SEQUENCE: {"version_id": 22, "version_no": 3},
            TYPE_API_SPEC: {"version_id": 23, "version_no": 4},
        },
        "refined_requirements": {
            "requirements": [
                {"id": "REQ-ORDER", "text": "The member can place an order."}
            ]
        },
        "usecase_spec": {
            "actors": [{"name": "Member", "description": "Places orders."}],
            "use_cases": [
                {
                    "id": "UC-ORDER",
                    "name": "Place order",
                    "primary_actor": "Member",
                    "requirement_ids": ["REQ-ORDER"],
                }
            ],
            "use_case_specs": [
                {
                    "use_case_id": "UC-ORDER",
                    "name": "Place order",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "Member submits an order."}
                    ],
                }
            ],
            "relationships": {
                "associations": [
                    {"actor": "Member", "use_case_id": "UC-ORDER"}
                ]
            },
        },
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC-ORDER"],
                    "operations": [
                        {
                            "operationId": "OrderControl::placeOrder()",
                            "name": "placeOrder",
                        }
                    ],
                }
            ],
            "Collaborations": [],
        },
        "sequence_diagram_model": {
            "Diagrams": [
                {
                    "use_case_id": "UC-ORDER",
                    "Participants": [],
                    "Messages": [],
                }
            ]
        },
        "api_spec_model": {
            "Endpoints": [
                {
                    "operation_id": "placeOrder",
                    "method": "post",
                    "path": "/orders",
                    "source_classes": ["OrderControl"],
                    "use_case_ids": ["UC-ORDER"],
                }
            ],
            "Schemas": [],
        },
        "erd_bce_classes": {
            "Classes": [
                {"className": "Order", "stereotype": "Entity", "fields": []}
            ]
        },
    }


def _snapshot() -> dict[str, Any]:
    return {
        "version_id": 31,
        "version_no": 5,
        "snapshot_digest": "source-digest",
        "metadata": {
            "implementation_traceability": {
                "mappings": [
                    {
                        "taskId": "implement-order",
                        "target_file": "application/src/OrderService.java",
                        "requirementIds": ["REQ-ORDER"],
                        "useCaseIds": ["UC-ORDER"],
                        "sourceRefs": ["api:placeOrder"],
                    }
                ]
            }
        },
        "files": {
            "application/src/OrderService.java": {
                "content": "class OrderService {}",
                "sha256": "digest",
            }
        },
    }


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> ProjectTools:
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_repository.load_state",
        lambda app_id: _state() if app_id == APP_ID else {},
    )
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_repository.load_file_snapshot",
        lambda app_id, artifact_type: (
            _snapshot() if app_id == APP_ID and artifact_type == TYPE_SOURCE_CODE else None
        ),
    )
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.workspace_repository.latest_command",
        lambda _app_id, **_kwargs: None,
    )
    return ProjectTools(APP_ID)


def test_conversation_contracts_reject_execution_fields_and_invalid_revision() -> None:
    assert Reply(text="안녕하세요").text == "안녕하세요"
    assert Clarification(question="어느 주문인가요?").candidates == []
    with pytest.raises(ValidationError):
        Reply(text="ok", stage="design")
    with pytest.raises(ValidationError):
        CommandIntent(intent="revise", targets=[], instruction="바꿔줘")
    command = CommandIntent(
        intent="revise",
        targets=["class_diagram:OrderControl"],
        instruction="이름을 바꿔줘",
    )
    assert command.model_dump(mode="json") == {
        "intent": "revise",
        "targets": ["class_diagram:OrderControl"],
        "instruction": "이름을 바꿔줘",
    }


def test_search_uses_latest_editing_catalog_and_returns_owner_and_version(
    tools: ProjectTools,
) -> None:
    matches = tools.search_elements("OrderControl")

    target = next(item for item in matches if item["ref"] == "class_diagram:OrderControl")
    assert target["owner"] == "design"
    assert target["editable"] is True
    assert target["artifact_type"] == TYPE_CLASS
    assert target["artifact_version_id"] == 21

    implementation = tools.search_elements("OrderService")
    source = next(item for item in implementation if item["ref"].startswith("file:"))
    assert source["owner"] == "implementation"
    assert source["artifact_version_id"] == 31


def test_read_element_reads_only_the_selected_current_element(tools: ProjectTools) -> None:
    item = tools.read_element("requirement:REQ-ORDER")

    assert item["app_id"] == APP_ID
    assert item["artifact_version_id"] == 11
    assert item["content"] == {
        "id": "REQ-ORDER",
        "text": "The member can place an order.",
    }


def test_catalog_is_built_once_per_tool_instance(
    tools: ProjectTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = project_tools_module._build_catalog
    calls = 0

    def counted(app_id: str):
        nonlocal calls
        calls += 1
        return original(app_id)

    monkeypatch.setattr(project_tools_module, "_build_catalog", counted)

    tools.search_elements("Order")
    tools.read_element("requirement:REQ-ORDER")
    tools.validate_targets(["class_diagram:OrderControl"])

    assert calls == 1


def test_project_content_is_redacted_and_bounded_before_llm_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["files"]["application/src/OrderService.java"]["content"] = (
        "api_key = supersecret\n" + "x" * 5_000
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: _state(),
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda _app_id, _artifact_type: snapshot,
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )

    item = ProjectTools(APP_ID).read_element(
        "file:application/src/OrderService.java"
    )
    content = item["content"]["file"]

    assert "supersecret" not in content
    assert "[REDACTED]" in content
    assert len(content) <= 4_000


def test_validate_targets_requires_canonical_edit_ref_and_matching_version(
    tools: ProjectTools,
) -> None:
    canonical = tools.validate_targets(
        [
            {
                "app_id": APP_ID,
                "ref": "class_diagram:OrderControl",
                "artifact_version_id": 21,
            }
        ]
    )
    alias = tools.validate_targets(["class:OrderControl"])
    stale = tools.validate_targets(
        [{"ref": "class_diagram:OrderControl", "artifact_version_id": 20}]
    )
    entity = tools.validate_targets(["entity:Order"])

    assert canonical["valid_refs"] == ["class_diagram:OrderControl"]
    assert alias["valid"] is False
    assert alias["targets"][0]["canonical_ref"] == "class_diagram:OrderControl"
    assert stale["targets"][0]["version_matches"] is False
    assert entity["targets"][0]["editable"] is False


def test_trace_views_do_not_mix_latest_editing_and_frozen_testing_evidence(
    tools: ProjectTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_calls: list[str] = []

    def frozen_trace(_app_id: str, ref: str) -> dict[str, Any]:
        frozen_calls.append(ref)
        return {"ref": ref, "trace_scope": "testing-input", "evidence": ["test:t:1"]}

    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_trace_response",
        frozen_trace,
    )

    editing = tools.trace_impact(["requirement:REQ-ORDER"], view="editing")
    assert editing["view"] == "editing"
    assert editing["impacts"][0]["evidence"] == []
    assert "class:OrderControl" in editing["impacts"][0]["downstream"]
    assert frozen_calls == []

    evidence = tools.trace_impact(
        ["requirement:REQ-ORDER"], view="testing-evidence"
    )
    assert evidence["impacts"][0]["trace_scope"] == "testing-input"
    assert frozen_calls == ["requirement:REQ-ORDER"]


def test_context_is_rebuilt_from_message_commands_and_status_not_stale_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        {
            "command_id": "m1",
            "action": "message",
            "payload": {"text": "현재 상태를 알려줘"},
            "status": "COMPLETED",
            "result": {"conversation": {"reply": "설계를 검토 중입니다."}},
        }
    ]
    latest = {
        "command_id": "m1",
        "action": "message",
        "stage": "design",
        "status": "COMPLETED",
        "payload": messages[0]["payload"],
        "result": {"awaiting_input": True},
    }
    monkeypatch.setattr(
        "app.workspace.conversation.context._recent_message_commands",
        lambda _app_id, _limit: messages,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.repository.latest_command",
        lambda _app_id: latest,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.offered_actions",
        lambda _command: [],
    )

    context = build_conversation_context(APP_ID)

    assert [turn.role for turn in context.turns] == ["user", "assistant"]
    assert context.turns[1].text == "설계를 검토 중입니다."
    assert context.pending_question is None


def test_context_keeps_recent_turns_within_a_total_character_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        {
            "command_id": f"m{index}",
            "action": "message",
            "payload": {"text": f"user-{index}-" + "u" * 10_000},
            "status": "COMPLETED",
            "result": {"conversation": {"reply": f"assistant-{index}-" + "a" * 10_000}},
        }
        for index in range(4)
    ]
    latest = {**messages[-1], "stage": "design"}
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context._recent_message_commands",
        lambda _app_id, _limit: messages,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.repository.latest_command",
        lambda _app_id: latest,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.offered_actions",
        lambda _command: [],
    )

    context = build_conversation_context(APP_ID)

    assert sum(len(turn.text) for turn in context.turns) <= 24_000
    assert context.turns[-1].command_id == "m3"
    assert all(len(turn.text) <= 8_000 for turn in context.turns)
