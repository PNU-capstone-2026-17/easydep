"""Actor/use-case modeling stage의 기존 import 경로를 보존하는 얇은 facade다."""

from __future__ import annotations

from functools import wraps

from app.requirements.config import settings  # noqa: F401 - legacy configuration seam
from app.requirements.contracts.state import AgentState
from app.requirements.modeling import use_cases as _canonical
from app.requirements.modeling import validation as validator  # noqa: F401
from app.requirements.runtime.structured_llm import invoke_structured

MissingUseCaseCandidate = _canonical.MissingUseCaseCandidate
RequirementTraceSlice = _canonical.RequirementTraceSlice
normalize_actors = _canonical.normalize_actors
normalize_use_case = _canonical.normalize_use_case
normalize_use_cases = _canonical.normalize_use_cases
_uc_dict = normalize_use_case


@wraps(_canonical.identify_actors)
def identify_actors(
    state: AgentState, feedback: str = ""
) -> dict[str, object]:
    return _canonical.identify_actors(
        state,
        feedback,
        proposal_call=invoke_structured,
    )


@wraps(_canonical.identify_use_cases)
def identify_use_cases(
    state: AgentState,
    feedback: str = "",
    target_ids: list[str] | None = None,
) -> dict[str, object]:
    return _canonical.identify_use_cases(
        state,
        feedback,
        target_ids,
        proposal_call=invoke_structured,
    )


@wraps(_canonical.review_model)
def review_model(state: AgentState) -> dict[str, object]:
    return _canonical.review_model(state, proposal_call=invoke_structured)


check_coverage = _canonical.check_coverage

__all__ = [
    "MissingUseCaseCandidate",
    "RequirementTraceSlice",
    "check_coverage",
    "identify_actors",
    "identify_use_cases",
    "normalize_actors",
    "normalize_use_case",
    "normalize_use_cases",
    "review_model",
]
