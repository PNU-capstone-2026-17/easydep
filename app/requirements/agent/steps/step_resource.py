"""Resource stage의 기존 import 경로를 보존하는 얇은 facade다."""

from __future__ import annotations

from functools import wraps

from app.requirements.config import settings
from app.requirements.contracts.state import AgentState
from app.requirements.resources import extraction as _extraction
from app.requirements.resources import service as _canonical
from app.requirements.runtime.structured_llm import invoke_structured
from app.requirements.schemas import CloudConstraintExtraction

Candidate = _canonical.Candidate
ResourceIntakeSession = _canonical.ResourceIntakeSession
SCHEMA_VERSION = _canonical.SCHEMA_VERSION


def _legacy_proposal(briefing: str) -> CloudConstraintExtraction:
    return invoke_structured(
        CloudConstraintExtraction,
        _extraction.resource_constraint_messages(briefing),
    )


@wraps(_canonical.extract_resource_constraints)
def extract_resource_constraints(state: AgentState) -> dict[str, object]:
    """기존 structured adapter seam을 canonical proposal service에 연결한다."""
    return _canonical.extract_resource_constraints(
        state,
        proposal_call=_legacy_proposal,
    )


@wraps(_canonical.build_resource_spec)
def build_resource_spec(state: AgentState) -> dict[str, object]:
    """기존 structured adapter seam을 canonical RESOURCE_SPEC service에 연결한다."""
    return _canonical.build_resource_spec(state, proposal_call=_legacy_proposal)


perceive_resource_inputs = _canonical.perceive_resource_inputs
normalize_resource_extraction = _canonical.normalize_resource_extraction
normalize_initial_cloud_constraints = _canonical.normalize_initial_cloud_constraints

__all__ = [
    "SCHEMA_VERSION",
    "Candidate",
    "ResourceIntakeSession",
    "build_resource_spec",
    "extract_resource_constraints",
    "invoke_structured",
    "normalize_initial_cloud_constraints",
    "normalize_resource_extraction",
    "perceive_resource_inputs",
    "settings",
]
