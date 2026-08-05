from __future__ import annotations

from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.common.validation import validate_puml_artifact
from app.design.services.sequence_diagram.extractor import (
    extract_sequence_elements_from_scenario,
)
from app.design.services.sequence_diagram.plantuml import (
    generate_plantuml_from_sequence_json,
)
from app.design.services.sequence_diagram.reviser import (
    revise_sequence_elements as revise_seq_elements,
)


def extract_sequence_elements(state: ArchitectureState) -> ArchitectureState:
    """Derive sequence elements from the use case specification and class diagram."""
    elements = extract_sequence_elements_from_scenario(
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
    )
    return {"extracted_sequence_elements": elements}


def revise_sequence_elements(state: ArchitectureState) -> ArchitectureState:
    """Apply user feedback to the sequence diagram elements model (source of truth).

    The diagram is regenerated deterministically from the revised elements by the
    convert node, so feedback never edits PlantUML text directly.
    """
    revised = revise_seq_elements(
        current_elements=state.get("extracted_sequence_elements", {}),
        feedback=state.get("sequence_diagram_feedback", ""),
        scenario_text=usecase_spec_text(state),
        class_diagram_puml=state.get("class_diagram_puml", ""),
    )
    return {"extracted_sequence_elements": revised}


def convert_to_sequence_diagram_code(state: ArchitectureState) -> ArchitectureState:
    """Render sequence elements to PlantUML. Deterministic and valid by construction."""
    elements = state.get("extracted_sequence_elements", {})
    return {"sequence_diagram_puml": generate_plantuml_from_sequence_json(elements)}


def validate_sequence_diagram_syntax(state: ArchitectureState) -> ArchitectureState:
    """Record diagram validity for the UI."""
    validation = validate_puml_artifact(state.get("sequence_diagram_puml", ""))
    return {
        "sequence_diagram_syntax_valid": validation["syntax_valid"],
        "sequence_diagram_syntax_errors": validation["syntax_errors"],
    }
