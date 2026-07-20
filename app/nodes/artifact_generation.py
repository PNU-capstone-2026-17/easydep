from __future__ import annotations

from app.schemas.architecture_state import ArchitectureState
from app.services.artifact_validation import (
    artifact_output_path,
    validate_api_spec,
    validate_puml_artifact,
    write_json_artifact,
)
from app.services.llm_artifacts import (
    generate_api_spec_with_llm,
    generate_deployment_diagram_with_llm,
    generate_erd_with_llm,
    generate_sequence_diagram_with_llm,
)


def generate_class_diagram(state: ArchitectureState) -> ArchitectureState:
    from app.graphs.class_diagram_graph import class_diagram_graph

    # The validation loop runs until the diagram compiles, so the graph needs a
    # recursion budget far above LangGraph's default of 25 steps.
    result = class_diagram_graph.invoke(state, {"recursion_limit": 1000})
    result["artifact_status"] = mark_status(result, "class_diagram", "implemented")
    return result


def generate_sequence_diagram(state: ArchitectureState) -> ArchitectureState:
    sequence_puml = generate_sequence_diagram_with_llm(
        state.get("scenario_text", ""),
        state.get("class_diagram_puml", ""),
    )
    validation = validate_puml_artifact(
        sequence_puml,
        artifact_output_path("outputs", "sequence_diagram.puml"),
        state.get("plantuml_jar_path", "plantuml.jar"),
    )
    return {
        "sequence_diagram_puml": sequence_puml,
        "sequence_diagram_syntax_valid": validation["syntax_valid"],
        "sequence_diagram_syntax_errors": validation["syntax_errors"],
        "artifact_status": mark_status(state, "sequence_diagram", "implemented"),
    }


def generate_api_spec(state: ArchitectureState) -> ArchitectureState:
    api_spec = generate_api_spec_with_llm(
        state.get("scenario_text", ""),
        state.get("class_diagram_puml", ""),
        state.get("sequence_diagram_puml", ""),
    )
    validation = validate_api_spec(api_spec)
    write_json_artifact(api_spec, artifact_output_path("outputs", "api_spec.json"))
    return {
        "api_spec": api_spec,
        "api_spec_syntax_valid": validation["syntax_valid"],
        "api_spec_syntax_errors": validation["syntax_errors"],
        "artifact_status": mark_status(state, "api_spec", "implemented"),
    }


def generate_erd(state: ArchitectureState) -> ArchitectureState:
    erd_puml = generate_erd_with_llm(
        state.get("scenario_text", ""),
        state.get("class_diagram_puml", ""),
        state.get("api_spec", {}),
    )
    validation = validate_puml_artifact(
        erd_puml,
        artifact_output_path("outputs", "erd_diagram.puml"),
        state.get("plantuml_jar_path", "plantuml.jar"),
    )
    return {
        "erd_puml": erd_puml,
        "erd_syntax_valid": validation["syntax_valid"],
        "erd_syntax_errors": validation["syntax_errors"],
        "artifact_status": mark_status(state, "erd", "implemented"),
    }


def generate_deployment_diagram(state: ArchitectureState) -> ArchitectureState:
    deployment_puml = generate_deployment_diagram_with_llm(
        state.get("scenario_text", ""),
        state.get("class_diagram_puml", ""),
        state.get("sequence_diagram_puml", ""),
        state.get("api_spec", {}),
        state.get("erd_puml", ""),
    )
    validation = validate_puml_artifact(
        deployment_puml,
        artifact_output_path("outputs", "deployment_diagram.puml"),
        state.get("plantuml_jar_path", "plantuml.jar"),
    )
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
