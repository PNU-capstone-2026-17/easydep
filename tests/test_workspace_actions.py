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


def test_reviewed_local_requirements_revision_offers_fresh_plan_not_advance() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT"),
        {
            "kind": "action_required",
            "downstream_revision_handoff": {
                "source_targets": [
                    {"ref": "use_case_spec:UC1", "artifact_version_id": 8}
                ],
                "semantic_scope": "behavior",
                "requested_effect": "Add the accepted exception.",
                "change_type": "modify",
            },
        },
    )

    assert [item["action"] for item in shaped["actions"]] == [
        "message",
        "plan_downstream_revision",
    ]
    assert [item["auto_selectable"] for item in shaped["actions"]] == [False, False]
    assert "advance" not in {item["action"] for item in shaped["actions"]}


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


def test_class_choice_copies_pinned_context_and_offers_free_text() -> None:
    context = {
        "element_ref": "class_diagram:Registration",
        "validated_target": {
            "ref": "class_diagram:Registration",
            "kind": "class",
            "element_id": "Registration",
            "owner": "design",
            "artifact_type": "CLASS",
            "artifact_version_id": 7,
            "display_label": "Registration",
        },
    }
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="design"),
        {
            "resource_question": {
                "choices": [{"value": "Add swap operation", "label": "Add operation"}],
                "allowFreeText": True,
                "context": context,
            }
        },
    )

    assert shaped["actions"][0]["payload"] == {
        "action_id": "command-1",
        "text": "Add swap operation",
        "context": context,
    }
    assert shaped["actions"][1]["label"] == "Provide another answer"
    assert shaped["actions"][1]["payload"] == {
        "action_id": "command-1",
        "context": context,
    }

    with pytest.raises(ValueError, match="must pin one validated target"):
        result_with_contract(
            command(status="AWAITING_INPUT", stage="design"),
            {
                "current_stage": "class_diagram",
                "resource_question": {
                    "choices": [{"value": "Regenerate the class diagram"}]
                },
            },
        )


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


@pytest.mark.parametrize(
    ("finding", "wait_reason", "label", "action"),
    [
        (
            {
                "repairable": False,
                "defect_class": "ENVIRONMENT_DEFECT",
                "repair_owner": "environment",
            },
            "external_wait",
            "Retry after environment recovery",
            "retry_implementation",
        ),
        (
            {
                "repairable": False,
                "defect_class": "PLATFORM_DEFECT",
                "repair_owner": "platform",
            },
            "external_wait",
            "Ask about this EasyDep platform issue",
            "message",
        ),
        (
            {
                "repairable": False,
                "defect_class": "PLATFORM_OR_DESIGN_DEFECT",
                "repair_owner": "platform-or-design",
            },
            "repair",
            "Review deployment design or platform issue",
            "message",
        ),
    ],
)
def test_unrepairable_testing_findings_use_explicit_owner_route(
    finding: dict,
    wait_reason: str,
    label: str,
    action: str,
) -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="testing"),
        {
            "requires_revision": True,
            "can_delegate_repair": False,
            "job_id": "testing-1",
            "blocking_findings": [finding],
        },
    )

    assert shaped["wait_reason"] == wait_reason
    assert shaped["actions"] == [
        {
            "action": action,
            "label": label,
            "payload": {
                "action_id": "command-1",
                **({"job_id": "testing-1"} if action == "retry_implementation" else {}),
            },
            "auto_selectable": False,
        }
    ]


def test_testing_environment_retry_reuses_the_implementation_job() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="testing"),
        {
            "requires_revision": True,
            "can_delegate_repair": False,
            "job_id": "testing-1",
            "job": {"implementation_job_id": "implementation-1"},
            "blocking_findings": [
                {
                    "repairable": False,
                    "defect_class": "ENVIRONMENT_DEFECT",
                    "repair_owner": "environment",
                }
            ],
        },
    )

    assert shaped["wait_reason"] == "external_wait"
    assert shaped["actions"] == [
        {
            "action": "start_testing",
            "label": "Retry testing after environment recovery",
            "payload": {
                "action_id": "command-1",
                "implementation_job_id": "implementation-1",
            },
            "auto_selectable": False,
        }
    ]


def test_unclassified_unrepairable_finding_is_not_treated_as_environment() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="testing"),
        {
            "requires_revision": True,
            "can_delegate_repair": False,
            "job_id": "testing-1",
            "blocking_findings": [{"repairable": False}],
        },
    )

    assert shaped["wait_reason"] == "repair"
    assert [item["action"] for item in shaped["actions"]] == ["message"]
    assert shaped["actions"][0]["label"] == "Send revision feedback"


def test_upstream_testing_ambiguity_offers_review_without_automatic_repair() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="testing"),
        {
            "requires_revision": True,
            "can_delegate_repair": False,
            "blocking_findings": [
                {
                    "repairable": True,
                    "defect_class": "UPSTREAM_AMBIGUITY",
                    "repair_owner": "requirements-or-design",
                }
            ],
        },
    )

    assert shaped["wait_reason"] == "repair"
    assert [item["action"] for item in shaped["actions"]] == ["message"]
    assert shaped["actions"][0]["label"] == "Send design revision feedback"


def test_exhausted_testing_plan_defect_is_an_easydep_platform_issue() -> None:
    shaped = result_with_contract(
        command(status="AWAITING_INPUT", stage="testing"),
        {
            "requires_revision": True,
            "can_delegate_repair": False,
            "job": {"implementation_job_id": "implementation-1"},
            "blocking_findings": [
                {
                    "repairable": True,
                    "defect_class": "TEST_DEFECT",
                    "repair_owner": "testing",
                }
            ],
        },
    )

    assert shaped["wait_reason"] == "external_wait"
    assert [item["action"] for item in shaped["actions"]] == ["message", "start_testing"]
    assert shaped["actions"][0]["label"] == "Ask about this EasyDep platform issue"
    assert shaped["actions"][1] == {
        "action": "start_testing",
        "label": "Retry testing after EasyDep update",
        "payload": {
            "action_id": "command-1",
            "implementation_job_id": "implementation-1",
        },
        "auto_selectable": False,
    }


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
