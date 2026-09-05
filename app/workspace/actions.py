"""Workspace action registry와 공개 다음-action 정책이다.

repository를 호출하지 않는 순수 모듈이다. command snapshot만으로 client가 다음에 보낼 수 있는
action을 결정한다. service의 payload 검증, 단계 routing과 dispatch도 같은 registry를 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .contracts import ActionOffer, AwaitingOutcome, WaitReason, WorkspaceAction


class StagePolicy(StrEnum):
    CURRENT = "current"
    REFERENCE = "reference"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    RETRY_IMPLEMENTATION = "retry_implementation"


@dataclass(frozen=True, slots=True)
class ActionSpec:
    action: WorkspaceAction
    handler: str
    stage_policy: StagePolicy
    required_payload: tuple[str, ...] = ()


_SPECS = (
    ActionSpec(WorkspaceAction.MESSAGE, "stage_message", StagePolicy.CURRENT),
    ActionSpec(
        WorkspaceAction.ADVANCE,
        "stage_message",
        StagePolicy.REFERENCE,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.DELEGATE_REPAIR,
        "delegate_repair",
        StagePolicy.REFERENCE,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.CONFIRM_CHANGE,
        "confirm_change",
        StagePolicy.REFERENCE,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.DISMISS_CHANGE,
        "dismiss_change",
        StagePolicy.REFERENCE,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.START_DESIGN,
        "stage_message",
        StagePolicy.DESIGN,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.RETRY_REQUIREMENTS,
        "retry_requirements",
        StagePolicy.REQUIREMENTS,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.RETRY_DESIGN,
        "retry_design",
        StagePolicy.DESIGN,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.START_IMPLEMENTATION,
        "start_implementation",
        StagePolicy.IMPLEMENTATION,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.RETRY_IMPLEMENTATION,
        "retry_implementation",
        StagePolicy.RETRY_IMPLEMENTATION,
        ("action_id", "job_id"),
    ),
    ActionSpec(
        WorkspaceAction.RERUN_IMPLEMENTATION,
        "start_implementation",
        StagePolicy.IMPLEMENTATION,
        ("action_id",),
    ),
    ActionSpec(
        WorkspaceAction.START_TESTING,
        "start_testing",
        StagePolicy.TESTING,
        ("action_id", "implementation_job_id"),
    ),
    ActionSpec(
        WorkspaceAction.APPLY_DEPLOYMENT_PREFERENCES,
        "stage_message",
        StagePolicy.REQUIREMENTS,
        ("action_id", "deployment_preferences"),
    ),
    ActionSpec(
        WorkspaceAction.BRANCH_CHECKPOINT,
        "branch_checkpoint",
        StagePolicy.CURRENT,
        ("checkpoint_stage",),
    ),
    ActionSpec(
        WorkspaceAction.RERUN_FROM_STAGE,
        "rerun_from_stage",
        StagePolicy.CURRENT,
        ("restart_stage",),
    ),
)

ACTION_REGISTRY = {spec.action.value: spec for spec in _SPECS}

# HTTP request model이 채우는 수동 실행 기본값이다. 공개 offer에 없는 값이라도 이 값과
# 같으면 실행 의미를 바꾸지 않으므로 허용한다. 반대로 retry처럼 기본값에서 벗어난
# 옵션은 offer가 명시한 경우에만 받을 수 있다.
_PASSIVE_REQUEST_DEFAULTS: dict[str, Any] = {
    "text": "",
    "base_package": "com.easydep.app",
    "allow_assumptions": True,
    "retry_failed": False,
    "auto_approve_method_proposals": False,
}
_INTERNAL_CONVERSATION_FIELDS = {
    "_conversation_actions",
    "_conversation_outcome",
    "conversation_intent",
    "validated_impact",
    "validated_target_feedbacks",
    "validated_targets",
}


def action_spec(action: str | WorkspaceAction) -> ActionSpec:
    try:
        return ACTION_REGISTRY[str(action)]
    except KeyError as error:
        raise ValueError(f"Unknown workspace action: {action}") from error


def validate_payload(action: str | WorkspaceAction, payload: dict[str, Any]) -> None:
    spec = action_spec(action)
    missing = [name for name in spec.required_payload if not payload.get(name)]
    if missing:
        raise ValueError(
            f"Missing values for the {spec.action.value} command: {', '.join(missing)}"
        )


def _offer(
    action: WorkspaceAction,
    label: str,
    payload: dict[str, Any],
    *,
    auto: bool = False,
    description: str | None = None,
) -> ActionOffer:
    return ActionOffer(
        action=action,
        label=label,
        payload=payload,
        auto_selectable=auto,
        description=description,
    )


def _answer_offers(command_id: str, result: dict[str, Any]) -> list[ActionOffer]:
    question = result.get("resource_question") or {}
    choices = question.get("choices") or [] if isinstance(question, dict) else []
    offers = [
        _offer(
            WorkspaceAction.MESSAGE,
            str(choice.get("label") or choice.get("value") or "Select"),
            {"action_id": command_id, "text": str(choice.get("value") or "")},
            description=str(choice.get("description") or "") or None,
        )
        for choice in choices
        if isinstance(choice, dict) and choice.get("value") is not None
    ]
    if offers:
        return offers
    return [
        _offer(
            WorkspaceAction.MESSAGE,
            "Send answer",
            {"action_id": command_id},
        )
    ]


def awaiting_outcome(command: dict[str, Any]) -> AwaitingOutcome:
    """대기 중인 명령의 필수 상호작용 계약을 만든다."""

    command_id = str(command.get("command_id") or "")
    result = dict(command.get("result") or {})
    stage = str(result.get("routing_stage") or command.get("stage") or "requirements")
    common = {"action_id": command_id}

    if result.get("action") == WorkspaceAction.CONFIRM_CHANGE.value:
        return AwaitingOutcome(
            wait_reason=WaitReason.REVIEW,
            actions=[
                _offer(WorkspaceAction.CONFIRM_CHANGE, "Apply change", common),
                _offer(WorkspaceAction.DISMISS_CHANGE, "Dismiss change", common),
            ],
        )

    if result.get("deployment_configuration_required"):
        return AwaitingOutcome(
            wait_reason=WaitReason.REVIEW,
            actions=[
                _offer(
                    WorkspaceAction.MESSAGE,
                    "Ask about deployment options",
                    common,
                )
            ],
        )

    if result.get("resource_question") or result.get("resource_questions"):
        question = result.get("resource_question") or {}
        if isinstance(question, dict) and question.get("kind") == "suggested":
            return AwaitingOutcome(
                wait_reason=WaitReason.REVIEW,
                actions=[
                    *_answer_offers(command_id, result),
                    _offer(
                        WorkspaceAction.ADVANCE,
                        "Skip suggestion and continue",
                        common,
                        auto=True,
                    ),
                ],
            )
        return AwaitingOutcome(
            wait_reason=WaitReason.QUESTION,
            actions=_answer_offers(command_id, result),
        )

    blockers = [item for item in result.get("blocking_findings") or [] if isinstance(item, dict)]
    environment_wait = bool(blockers) and all(
        item.get("repairable") is False
        or item.get("defect_class") == "ENVIRONMENT_DEFECT"
        for item in blockers
    )
    repair_job_id = str(
        result.get("job_id") or (command.get("payload") or {}).get("job_id") or ""
    )
    if environment_wait and repair_job_id:
        return AwaitingOutcome(
            wait_reason=WaitReason.EXTERNAL_WAIT,
            actions=[
                _offer(
                    WorkspaceAction.RETRY_IMPLEMENTATION,
                    "Retry after environment recovery",
                    {**common, "job_id": repair_job_id},
                )
            ],
        )

    if result.get("requires_revision"):
        actions = [
            _offer(WorkspaceAction.MESSAGE, "Send revision feedback", common),
        ]
        if result.get("can_delegate_repair"):
            actions.append(
                _offer(
                    WorkspaceAction.DELEGATE_REPAIR,
                    "Delegate repair to LLM",
                    common,
                    auto=True,
                )
            )
        if stage == "design" and result.get("method_proposals"):
            actions.append(
                _offer(
                    WorkspaceAction.ADVANCE,
                    "Approve method proposals and continue",
                    {**common, "auto_approve_method_proposals": True},
                )
            )
        return AwaitingOutcome(wait_reason=WaitReason.REPAIR, actions=actions)

    if result.get("kind") == "question" or result.get("questions"):
        return AwaitingOutcome(
            wait_reason=WaitReason.QUESTION,
            actions=_answer_offers(command_id, result),
        )

    next_action = (
        WorkspaceAction.ADVANCE
        if stage in {"requirements", "design"}
        else WorkspaceAction.MESSAGE
    )
    next_label = "Continue to next stage" if next_action == WorkspaceAction.ADVANCE else "Send feedback"
    next_payload: dict[str, Any] = dict(common)
    if stage == "design" and result.get("method_proposals"):
        next_label = "Approve method proposals and continue"
        next_payload["auto_approve_method_proposals"] = True
    return AwaitingOutcome(
        wait_reason=WaitReason.REVIEW,
        actions=[
            _offer(WorkspaceAction.MESSAGE, "Send revision feedback", common),
            *(
                [_offer(next_action, next_label, next_payload, auto=True)]
                if next_action != WorkspaceAction.MESSAGE
                else []
            ),
        ],
    )


def terminal_actions(command: dict[str, Any]) -> list[ActionOffer]:
    """종료된 명령에서 가능한 단계 전환 또는 재시도를 반환한다."""

    status = str(command.get("status") or "")
    stage = str(command.get("stage") or "")
    command_id = str(command.get("command_id") or "")
    result = dict(command.get("result") or {})
    common = {"action_id": command_id}
    if status in {"FAILED", "INTERRUPTED"}:
        discuss = _offer(WorkspaceAction.MESSAGE, "Ask about this error", common)
        if stage == "testing" and command.get("action") == "delegate_repair":
            repair_job_id = str((command.get("payload") or {}).get("job_id") or "")
            if repair_job_id:
                return [
                    discuss,
                    _offer(
                        WorkspaceAction.RETRY_IMPLEMENTATION,
                        "Retry implementation repair checkpoint",
                        {**common, "job_id": repair_job_id},
                    )
                ]
        if stage == "requirements":
            return [
                discuss,
                _offer(
                    WorkspaceAction.RETRY_REQUIREMENTS,
                    "Retry requirements",
                    common,
                    auto=True,
                ),
            ]
        if stage == "design":
            return [
                discuss,
                _offer(
                    WorkspaceAction.RETRY_DESIGN,
                    "Retry design",
                    common,
                    auto=True,
                ),
            ]
        if stage == "implementation":
            job_id = str(result.get("job_id") or (command.get("payload") or {}).get("job_id") or "")
            if job_id:
                return [
                    discuss,
                    _offer(
                        WorkspaceAction.RETRY_IMPLEMENTATION,
                        "Retry implementation checkpoint",
                        {**common, "job_id": job_id},
                    )
                ]
            return [
                discuss,
                _offer(WorkspaceAction.RERUN_IMPLEMENTATION, "Rerun implementation", common),
            ]
        if stage == "testing":
            implementation_job_id = str(
                (command.get("payload") or {}).get("implementation_job_id") or ""
            )
            if implementation_job_id:
                return [
                    discuss,
                    _offer(
                        WorkspaceAction.START_TESTING,
                        "Rerun tests",
                        {**common, "implementation_job_id": implementation_job_id},
                    )
                ]
        return [discuss]
    if status != "COMPLETED":
        return []
    discuss = _offer(WorkspaceAction.MESSAGE, "Continue conversation", common)
    if stage == "requirements":
        return [
            discuss,
            _offer(WorkspaceAction.START_DESIGN, "Start design", common, auto=True),
        ]
    if stage == "design":
        return [
            discuss,
            _offer(
                WorkspaceAction.START_IMPLEMENTATION,
                "Start implementation",
                common,
                auto=True,
            ),
        ]
    if stage == "implementation":
        job_id = str(result.get("job_id") or "")
        actions = [
            discuss,
            _offer(WorkspaceAction.RERUN_IMPLEMENTATION, "Rerun implementation", common),
        ]
        if job_id:
            actions.append(
                _offer(
                    WorkspaceAction.START_TESTING,
                    "Start testing",
                    {**common, "implementation_job_id": job_id},
                    auto=True,
                )
            )
        return actions
    return [discuss]


def offered_actions(command: dict[str, Any]) -> list[ActionOffer]:
    if command.get("status") == "AWAITING_INPUT":
        return awaiting_outcome(command).actions
    preserved = (command.get("payload") or {}).get("_conversation_actions")
    if isinstance(preserved, list):
        return [ActionOffer.model_validate(action) for action in preserved]
    return terminal_actions(command)


def result_with_contract(command: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """입력을 바꾸지 않고 status에서 계산한 계약을 붙인다."""

    enriched = dict(result)
    enriched.pop("awaiting_input", None)
    snapshot = {**command, "result": enriched}
    if snapshot.get("status") == "AWAITING_INPUT":
        outcome = awaiting_outcome(snapshot)
        enriched.update(outcome.model_dump(mode="json", exclude_none=True))
    else:
        enriched["actions"] = [
            action.model_dump(mode="json", exclude_none=True)
            for action in offered_actions(snapshot)
        ]
        enriched.pop("wait_reason", None)
    return enriched


def action_is_offered(action: str, payload: dict[str, Any], prior: dict[str, Any]) -> bool:
    """제출 action과 payload 전체가 공개 offer의 실행 범위 안인지 확인한다."""

    for offer in offered_actions(prior):
        if offer.action != action:
            continue
        expected = offer.payload
        if not all(payload.get(key) == value for key, value in expected.items()):
            continue
        extras_are_passive = True
        for key, value in payload.items():
            if key in expected or key in _INTERNAL_CONVERSATION_FIELDS:
                continue
            if action == WorkspaceAction.MESSAGE.value and key in {"text", "context"}:
                # 자유 답변과 UI에서 명시적으로 고른 artifact target은 message offer의
                # 입력 영역이다. 고정 선택 text는 위 expected 비교에서 이미 고정됐다.
                continue
            if key in _PASSIVE_REQUEST_DEFAULTS and value == _PASSIVE_REQUEST_DEFAULTS[key]:
                continue
            extras_are_passive = False
            break
        if extras_are_passive:
            return True
    return False
