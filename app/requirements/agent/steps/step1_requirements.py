"""Requirement refinement stage의 기존 import 경로를 보존하는 얇은 facade다."""

from __future__ import annotations

from functools import wraps

from app.requirements.classifier import bert_available, classify_bert
from app.requirements.config import settings  # noqa: F401 - legacy configuration seam
from app.requirements.contracts.state import AgentState
from app.requirements.modeling import refinement as _canonical
from app.requirements.runtime.structured_llm import invoke_structured

intake = _canonical.intake
normalize_expansion = _canonical.normalize_expansion
normalize_refinement = _canonical.normalize_refinement


@wraps(_canonical.expand_requirements)
def expand_requirements(state: AgentState) -> dict[str, object]:
    return _canonical.expand_requirements(state, proposal_call=invoke_structured)


@wraps(_canonical.clarify)
def clarify(state: AgentState) -> dict[str, object]:
    return _canonical.clarify(state, proposal_call=invoke_structured)


@wraps(_canonical.classify)
def classify(state: AgentState, feedback: str = "") -> dict[str, object]:
    return _canonical.classify(
        state,
        feedback,
        availability_call=bert_available,
        classifier_call=classify_bert,
    )


__all__ = [
    "clarify",
    "classify",
    "expand_requirements",
    "intake",
    "normalize_expansion",
    "normalize_refinement",
]
