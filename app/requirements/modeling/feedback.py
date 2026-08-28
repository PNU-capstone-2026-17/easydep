"""Modeling stage가 supervisor 지시를 읽는 순수 feedback 경계다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.contracts.request import FeedbackEdit
from app.requirements.modeling.contracts import StructuredProposalCall
from app.requirements.runtime import telemetry
from app.requirements.runtime.structured_llm import invoke_structured
from app.requirements.schemas import FeedbackIntent

_log = telemetry.get_logger("feedback")


def feedback_for(
    state: dict[str, object], key: str, given: str = ""
) -> str:
    """명시적 사용자 지시를 우선해 modeling stage feedback을 반환한다.

    Args:
        state: 선택적 ``stage_feedback``을 포함하는 현재 graph state다.
        key: 지시를 받을 modeling stage key다.
        given: 함수 인자로 직접 전달된 사용자 feedback이다.

    Returns:
        사용자 feedback, supervisor feedback 또는 빈 문자열이다.

    Notes:
        우선순위만 결정하며 supervisor의 재실행 owner·cascade 범위를 변경하지 않는다.
    """
    if given:
        return given
    feedback = state.get("stage_feedback") or {}
    if not isinstance(feedback, dict):
        return ""
    return str(feedback.get(key) or "")


def _artifact_summary(state: Mapping[str, object]) -> str:
    """의도 분류에 필요한 현재 산출물의 최소 요약을 만든다."""
    actors = cast(list[dict[str, object]], state.get("actors") or [])
    use_cases = cast(list[dict[str, object]], state.get("use_cases") or [])
    relationships = cast(dict[str, object], state.get("relationships") or {})
    actor_names = [str(actor.get("name") or "") for actor in actors]
    use_case_names = [
        f"{use_case.get('id')}:{use_case.get('name')}" for use_case in use_cases
    ]
    includes = cast(list[object], relationships.get("includes") or [])
    extends = cast(list[object], relationships.get("extends") or [])
    generalizations = cast(list[object], relationships.get("generalizations") or [])
    return (
        f"actors: {actor_names}\n"
        f"use_cases: {use_case_names}\n"
        f"relationships: includes={len(includes)} "
        f"extends={len(extends)} generalizations={len(generalizations)}"
    )


def classify_feedback(
    feedback: str,
    state: Mapping[str, object],
    *,
    proposal_call: StructuredProposalCall | None = None,
) -> FeedbackIntent:
    """자연어 feedback을 구조화된 재생성 의도로 분류한다.

    Args:
        feedback: 사용자가 제공한 자연어 수정 지시다.
        state: 의도 분류에 필요한 현재 modeling 산출물이다.
        proposal_call: 테스트나 runtime adapter가 주입하는 structured proposal 호출이다.

    Returns:
        stage, scope, target ID와 instruction이 정규화된 feedback 의도다.

    Notes:
        기존 ``FeedbackIntent`` schema와 prompt/settings를 그대로 사용하므로 logical LLM
        호출 수와 telemetry operation은 바뀌지 않는다.
    """
    propose = proposal_call or invoke_structured
    return propose(
        FeedbackIntent,
        [
            SystemMessage(content=prompts.FEEDBACK_CLASSIFY_SYSTEM),
            HumanMessage(
                content=(
                    f"[CURRENT ARTIFACTS]\n{_artifact_summary(state)}"
                    f"\n\n[USER FEEDBACK]\n{feedback}"
                )
            ),
        ],
    )


def resolve_intent(
    feedback: str | FeedbackEdit,
    state: Mapping[str, object],
    *,
    proposal_call: StructuredProposalCall | None = None,
) -> FeedbackIntent:
    """구조화 edit은 그대로, 자연어 feedback은 한 번 분류해 재생성 의도로 만든다."""
    if isinstance(feedback, FeedbackEdit):
        _log.debug("feedback routed structurally", extra={"stage": feedback.stage})
        return FeedbackIntent(
            stage=feedback.stage,
            scope=feedback.scope,
            target_ids=feedback.target_ids if feedback.scope == "local" else [],
            instruction=feedback.instruction,
        )
    return classify_feedback(feedback, state, proposal_call=proposal_call)


__all__ = ["classify_feedback", "feedback_for", "resolve_intent"]
