"""Specification modeling stage의 기존 import 경로를 보존하는 얇은 facade다."""

from __future__ import annotations

from functools import wraps

from app.requirements.config import settings  # noqa: F401 - legacy configuration seam
from app.requirements.contracts.state import AgentState
from app.requirements.modeling import specifications as _canonical
from app.requirements.modeling import validation as validator  # noqa: F401
from app.requirements.runtime.structured_llm import invoke_structured

normalize_specification = _canonical.normalize_specification
normalize_text = _canonical.normalize_text
requirement_view = _canonical.requirement_view
spec_review_payload = _canonical.spec_review_payload
validate_specification = _canonical.validate_specification
_assemble = normalize_specification
_clean = normalize_text
_spec_for = _canonical.generate_specification
_validate_spec = validate_specification


@wraps(_canonical.generate_specs)
def generate_specs(
    state: AgentState,
    feedback: str = "",
    target_ids: list[str] | None = None,
) -> dict[str, object]:
    return _canonical.generate_specs(
        state,
        feedback,
        target_ids,
        proposal_call=invoke_structured,
    )


check_specs = _canonical.check_specs

__all__ = [
    "check_specs",
    "generate_specs",
    "normalize_specification",
    "normalize_text",
    "requirement_view",
    "spec_review_payload",
    "validate_specification",
]
