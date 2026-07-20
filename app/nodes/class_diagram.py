from __future__ import annotations

from app.schemas.architecture_state import ArchitectureState
from app.services.bce_class_extractor import (
    extract_bce_classes_from_scenario,
    load_scenario_from_json,
)
from app.services.artifact_validation import validate_puml_artifact
from app.services.plantuml_class_diagram import generate_plantuml_from_bce_json
from app.services.llm_artifacts import revise_puml_with_llm


def extract_class_elements(state: ArchitectureState) -> ArchitectureState:
    scenario_text = state.get("scenario_text") or state.get("source", "")
    scenario_file_path = state.get("scenario_file_path", "")

    if not scenario_text and scenario_file_path:
        scenario_text = load_scenario_from_json(scenario_file_path)

    return {
        "scenario_text": scenario_text,
        "extracted_bce_classes": extract_bce_classes_from_scenario(scenario_text),
    }


def convert_to_class_diagram_code(state: ArchitectureState) -> ArchitectureState:
    class_json = state.get("extracted_bce_classes", {})
    diagram_puml = generate_plantuml_from_bce_json(class_json)

    return {
        "class_diagram_puml": diagram_puml,
        "class_diagram_syntax_valid": False,
        "class_diagram_syntax_errors": [],
    }


def repair_class_diagram_syntax(state: ArchitectureState) -> ArchitectureState:
    """Ask the LLM to fix the syntax errors the validator reported."""
    syntax_errors = state.get("class_diagram_syntax_errors", [])
    puml_text = state.get("class_diagram_puml", "").strip()

    if not puml_text:
        return {"class_diagram_puml": puml_text}

    revised = revise_puml_with_llm(
        artifact_name="class diagram",
        current_puml=puml_text,
        feedback="Fix syntax errors: " + "; ".join(syntax_errors),
        syntax_errors=syntax_errors,
        context=state.get("scenario_text", ""),
    )
    return {"class_diagram_puml": revised}


def validate_class_diagram_syntax(state: ArchitectureState) -> ArchitectureState:
    validation = validate_puml_artifact(state.get("class_diagram_puml", ""))
    return {
        "class_diagram_syntax_valid": validation["syntax_valid"],
        "class_diagram_syntax_errors": validation["syntax_errors"],
    }


