from __future__ import annotations

from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.class_diagram.extractor import extract_bce_classes_from_scenario
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.common.revision import revise_puml_with_llm
from app.design.services.common.validation import validate_puml_artifact


def extract_class_elements(state: ArchitectureState) -> ArchitectureState:
    """Derive BCE elements from the use case specification."""
    return {
        "extracted_bce_classes": extract_bce_classes_from_scenario(
            usecase_spec_text(state)
        ),
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
        context=usecase_spec_text(state),
    )
    return {"class_diagram_puml": revised}


def validate_class_diagram_syntax(state: ArchitectureState) -> ArchitectureState:
    validation = validate_puml_artifact(state.get("class_diagram_puml", ""))
    return {
        "class_diagram_syntax_valid": validation["syntax_valid"],
        "class_diagram_syntax_errors": validation["syntax_errors"],
    }


