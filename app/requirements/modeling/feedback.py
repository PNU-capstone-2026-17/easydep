"""Modeling stage가 supervisor 지시를 읽는 순수 feedback 경계다."""

from __future__ import annotations


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


__all__ = ["feedback_for"]
