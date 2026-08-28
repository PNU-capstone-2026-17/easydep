"""Cloud input 병렬 단계의 기존 import 경로를 보존하는 얇은 facade다."""

from __future__ import annotations

from functools import wraps

from app.requirements.contracts.state import AgentState
from app.requirements.resources.capability_extraction import derive_deployment_needs
from app.requirements.resources.cloud_inputs import analyze_cloud_inputs as _analyze
from app.requirements.resources.service import extract_resource_constraints


@wraps(_analyze)
def analyze_cloud_inputs(state: AgentState) -> dict[str, object]:
    """기존 module-level branch 주입 seam을 canonical 병렬 서비스에 연결한다."""
    return _analyze(
        state,
        deployment_call=derive_deployment_needs,
        constraint_call=extract_resource_constraints,
    )


analyze_cloud_inputs._easydep_emits_progress = True  # type: ignore[attr-defined]

__all__ = [
    "analyze_cloud_inputs",
    "derive_deployment_needs",
    "extract_resource_constraints",
]
