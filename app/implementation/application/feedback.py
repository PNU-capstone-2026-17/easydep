"""구현 결과에 대한 피드백을 구현 단계에서 처리할 수 있는지 확인한다."""
from __future__ import annotations

from typing import Any


def assess_feedback_eligibility(
    feedback: str,
    design: dict[str, Any] | None = None,
    rtm_map: dict[str, Any] | None = None,
) -> dict[str, object]:
    """추적표와 설계 계약을 기준으로 피드백을 구현 단계에서 처리할지 판단한다."""
    from app.implementation.workflows.traceability import (
        evaluate_feedback_rtm_traceability,
    )

    return evaluate_feedback_rtm_traceability(feedback, design=design, rtm_map=rtm_map)
