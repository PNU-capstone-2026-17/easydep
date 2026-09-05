from __future__ import annotations

import pytest

from app.workspace.actions import (
    ACTION_REGISTRY,
    action_is_offered,
    action_spec,
    offered_actions,
    result_with_contract,
    validate_payload,
)
from app.workspace.contracts import WorkspaceAction


def command(*, status: str, stage: str = "requirements", result=None, **extra):
    return {
        "command_id": "command-1",
        "app_id": "app-1",
        "action": "message",
        "stage": stage,
        "status": status,
        "payload": {},
        "result": result,
        **extra,
    }


def test_registry_covers_the_public_action_enum_once() -> None:
    assert set(ACTION_REGISTRY) == {action.value for action in WorkspaceAction}
    assert action_spec("advance").required_payload == ("action_id",)


def test_registry_validates_transition_payloads() -> None:
    with pytest.raises(ValueError, match="action_id"):
        validate_payload("start_design", {})
    validate_payload("start_design", {"action_id": "command-1"})


def test_every_awaiting_result_gets_a_reason_and_real_actions() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT"),
        {"kind": "question", "questions": ["Which region?"]},
    )

    assert shaped["wait_reason"] == "question"
    assert shaped["actions"] == [
        {
            "action": "message",
            "label": "Send answer",
            "payload": {"action_id": "command-1"},
            "auto_selectable": False,
        }
    ]


def test_choice_actions_carry_the_answer_in_their_payload() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT"),
        {
            "kind": "question",
            "resource_question": {
                "choices": [
                    {
                        "value": "ap-northeast-2",
                        "label": "Seoul",
                        "description": "AWS Seoul region",
                    }
                ]
            },
        },
    )

    assert shaped["actions"][0]["payload"] == {
        "action_id": "command-1",
        "text": "ap-northeast-2",
    }
    assert shaped["actions"][0]["description"] == "AWS Seoul region"


def test_deployment_configuration_wait_does_not_offer_early_advance() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="design"),
        {"deployment_configuration_required": True},
    )

    assert shaped["wait_reason"] == "review"
    assert [item["action"] for item in shaped["actions"]] == ["message"]


def test_repair_action_is_the_only_auto_selectable_repair_offer() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="design"),
        {
            "requires_revision": True,
            "can_delegate_repair": True,
            "blocking_findings": [{"message": "missing call", "repairable": True}],
        },
    )

    assert shaped["wait_reason"] == "repair"
    assert [item["action"] for item in shaped["actions"]] == [
        "message",
        "delegate_repair",
    ]
    assert [item["auto_selectable"] for item in shaped["actions"]] == [False, True]


def test_status_not_a_stale_result_flag_controls_terminal_actions() -> None:
    shaped = result_with_contract(
        command(
            status="COMPLETED",
            result={"awaiting_input": True, "kind": "question"},
        ),
        {"awaiting_input": True, "kind": "question"},
    )

    assert "wait_reason" not in shaped
    assert [item["action"] for item in shaped["actions"]] == [
        "message",
        "start_design",
    ]


def test_reference_validation_accepts_only_a_published_payload() -> None:
    prior = command(
        status="AWAITING_INPUT",
        result={
            "resource_question": {
                "choices": [{"value": "aws", "label": "AWS"}]
            }
        },
    )

    assert action_is_offered(
        "message", {"action_id": "command-1", "text": "aws"}, prior
    )
    assert not action_is_offered(
        "message", {"action_id": "command-1", "text": "gcp"}, prior
    )


def test_reference_validation_rejects_unoffered_execution_options() -> None:
    prior = command(
        status="COMPLETED",
        stage="requirements",
        result={"message": "Requirements completed."},
    )

    assert action_is_offered(
        "start_design",
        {
            "action_id": "command-1",
            "text": "",
            "retry_failed": False,
        },
        prior,
    )
    assert not action_is_offered(
        "start_design",
        {"action_id": "command-1", "retry_failed": True},
        prior,
    )


def test_validated_conversation_scope_does_not_expand_a_message_offer() -> None:
    prior = command(
        status="COMPLETED",
        stage="design",
        result={"message": "Design completed."},
    )

    assert action_is_offered(
        "message",
        {
            "action_id": "command-1",
            "text": "OrderService를 수정해줘",
            "conversation_intent": {
                "intent": "revise",
                "targets": ["class_diagram:OrderService"],
                "instruction": "OrderService를 수정해줘",
            },
            "validated_targets": [{"ref": "class_diagram:OrderService"}],
            "validated_impact": {"refs": ["api_spec:createOrder"]},
        },
        prior,
    )


def test_reply_preserves_the_same_actions_for_rendering_and_followup_routing() -> None:
    reply = command(
        status="COMPLETED",
        stage="design",
        payload={
            "_conversation_actions": [
                {
                    "action": "delegate_repair",
                    "label": "Delegate repair to LLM",
                    "payload": {"action_id": "repair-command"},
                }
            ]
        },
    )

    assert [offer.action for offer in offered_actions(reply)] == ["delegate_repair"]
    assert action_is_offered(
        "delegate_repair", {"action_id": "repair-command"}, reply
    )
