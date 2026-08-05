from __future__ import annotations

from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.api_spec.extractor import (
    extract_api_elements_from_diagrams,
)
from app.design.services.api_spec.openapi_builder import (
    generate_openapi_spec_from_json,
)
from app.design.services.api_spec.reviser import (
    revise_api_spec_elements as revise_api_elements_fn,
)
from app.design.services.common.validation import validate_api_spec


def extract_api_elements(state: ArchitectureState) -> ArchitectureState:
    """Derive API spec elements from class diagram and sequence diagram."""
    elements = extract_api_elements_from_diagrams(
        state.get("class_diagram_puml", ""),
        state.get("sequence_diagram_puml", ""),
    )
    return {"extracted_api_elements": elements}


def revise_api_elements(state: ArchitectureState) -> ArchitectureState:
    """Apply user feedback to the API elements model (source of truth)."""
    revised = revise_api_elements_fn(
        current_elements=state.get("extracted_api_elements", {}),
        feedback=state.get("api_spec_feedback", ""),
        scenario_text=usecase_spec_text(state),
        class_diagram_puml=state.get("class_diagram_puml", ""),
        sequence_diagram_puml=state.get("sequence_diagram_puml", ""),
    )
    return {"extracted_api_elements": revised}


def convert_to_api_spec_code(state: ArchitectureState) -> ArchitectureState:
    """Render API elements to OpenAPI dict. Deterministic and valid by construction."""
    elements = state.get("extracted_api_elements", {})
    return {"api_spec": generate_openapi_spec_from_json(elements)}


def validate_api_spec_syntax(state: ArchitectureState) -> ArchitectureState:
    """Record API spec validity for the UI."""
    validation = validate_api_spec(state.get("api_spec", {}))
    return {
        "api_spec_syntax_valid": validation["syntax_valid"],
        "api_spec_syntax_errors": validation["syntax_errors"],
    }
