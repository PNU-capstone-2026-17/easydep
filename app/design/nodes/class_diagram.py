from __future__ import annotations

from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.class_diagram.extractor import extract_bce_classes_from_scenario
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.reviser import revise_bce_classes
from app.design.services.common.validation import validate_puml_artifact


def extract_class_elements(state: ArchitectureState) -> ArchitectureState:
    """Derive BCE elements from the use case specification."""
    return {
        "extracted_bce_classes": extract_bce_classes_from_scenario(
            usecase_spec_text(state)
        ),
    }


def revise_class_elements(state: ArchitectureState) -> ArchitectureState:
    """Apply user feedback to the BCE class model (the source of truth).

    The diagram is regenerated deterministically from the revised elements by the
    convert node, so feedback never edits the PlantUML text directly and the two
    representations never drift.
    """
    revised = revise_bce_classes(
        current_bce=state.get("extracted_bce_classes", {}),
        feedback=state.get("class_diagram_feedback", ""),
        scenario_text=usecase_spec_text(state),
    )
    return {"extracted_bce_classes": revised}


def convert_to_class_diagram_code(state: ArchitectureState) -> ArchitectureState:
    """Render the BCE model to PlantUML. Deterministic and valid by construction."""
    class_json = state.get("extracted_bce_classes", {})
    return {"class_diagram_puml": generate_plantuml_from_bce_json(class_json)}


def validate_class_diagram_syntax(state: ArchitectureState) -> ArchitectureState:
    """Record diagram validity for the UI. A tripwire, not a repair trigger."""
    validation = validate_puml_artifact(state.get("class_diagram_puml", ""))
    return {
        "class_diagram_syntax_valid": validation["syntax_valid"],
        "class_diagram_syntax_errors": validation["syntax_errors"],
    }
