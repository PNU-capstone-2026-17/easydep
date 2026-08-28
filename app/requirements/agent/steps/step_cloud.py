"""Capability extraction의 기존 import 경로를 보존하는 얇은 facade다."""

from __future__ import annotations

from functools import wraps

from app.requirements.config import settings
from app.requirements.contracts.state import AgentState
from app.requirements.resources import capability_extraction as _canonical
from app.requirements.runtime.structured_llm import invoke_structured
from app.requirements.schemas import DeploymentNeedsResult


def _legacy_proposal(
    requirements: list[dict[str, object]], seed: int
) -> DeploymentNeedsResult:
    return invoke_structured(
        DeploymentNeedsResult,
        _canonical.deployment_need_messages(requirements),
        seed_override=seed,
    )


@wraps(_canonical.derive_deployment_needs)
def derive_deployment_needs(
    state: AgentState, *, sample_count: int | None = None
) -> dict[str, object]:
    """기존 structured adapter seam을 canonical capability service에 연결한다."""
    return _canonical.derive_deployment_needs(
        state,
        sample_count=sample_count,
        proposal_call=_legacy_proposal,
    )


__all__ = ["derive_deployment_needs", "invoke_structured", "settings"]
