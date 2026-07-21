from __future__ import annotations

from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.artifact_validation import validate_api_spec, validate_puml_artifact
from app.design.services.llm_artifacts import (
    generate_api_spec_with_llm,
    generate_deployment_diagram_with_llm,
    generate_erd_with_llm,
    generate_sequence_diagram_with_llm,
)


def generate_sequence_diagram(state: ArchitectureState) -> ArchitectureState:
    sequence_puml = generate_sequence_diagram_with_llm(
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
    )
    validation = validate_puml_artifact(sequence_puml)
    return {
        "sequence_diagram_puml": sequence_puml,
        "sequence_diagram_syntax_valid": validation["syntax_valid"],
        "sequence_diagram_syntax_errors": validation["syntax_errors"],
        "artifact_status": mark_status(state, "sequence_diagram", "implemented"),
    }


def generate_api_spec(state: ArchitectureState) -> ArchitectureState:
    api_spec = generate_api_spec_with_llm(
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
        state.get("sequence_diagram_puml", ""),
    )
    validation = validate_api_spec(api_spec)
    return {
        "api_spec": api_spec,
        "api_spec_syntax_valid": validation["syntax_valid"],
        "api_spec_syntax_errors": validation["syntax_errors"],
        "artifact_status": mark_status(state, "api_spec", "implemented"),
    }


def generate_erd(state: ArchitectureState) -> ArchitectureState:
    erd_puml = generate_erd_with_llm(
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
        state.get("api_spec", {}),
    )
    validation = validate_puml_artifact(erd_puml)
    return {
        "erd_puml": erd_puml,
        "erd_syntax_valid": validation["syntax_valid"],
        "erd_syntax_errors": validation["syntax_errors"],
        "artifact_status": mark_status(state, "erd", "implemented"),
    }


def generate_deployment_diagram(state: ArchitectureState) -> ArchitectureState:
    deployment_puml = generate_deployment_diagram_with_llm(
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
        state.get("sequence_diagram_puml", ""),
        state.get("api_spec", {}),
        state.get("erd_puml", ""),
    )
    validation = validate_puml_artifact(deployment_puml)
    return {
        "deployment_diagram_puml": deployment_puml,
        "deployment_diagram_syntax_valid": validation["syntax_valid"],
        "deployment_diagram_syntax_errors": validation["syntax_errors"],
        "artifact_status": mark_status(state, "deployment_diagram", "implemented"),
    }


def mark_status(
    state: ArchitectureState,
    artifact_name: str,
    status: str,
) -> dict[str, str]:
    current = dict(state.get("artifact_status", {}))
    current[artifact_name] = status
    return current
