from __future__ import annotations

import json
from typing import Any
from typing_extensions import TypedDict


class ArchitectureState(TypedDict, total=False):
    app_id: str

    # User input, stored on the app row.
    requirements_text: str
    resource_constraints_text: str

    # Requirements analysis artifacts.
    refined_requirements: dict[str, Any]
    usecase_spec: dict[str, Any]
    usecase_diagram_puml: str
    usecase_diagram_syntax_valid: bool
    usecase_diagram_syntax_errors: list[str]
    resource_spec: dict[str, Any]

    extracted_bce_classes: dict[str, Any]
    # Transient: carries user feedback into the class diagram feedback graph so it
    # edits the BCE model (extracted_bce_classes), not the derived PlantUML.
    class_diagram_feedback: str
    class_diagram_puml: str
    class_diagram_syntax_valid: bool
    class_diagram_syntax_errors: list[str]

    sequence_diagram_puml: str
    sequence_diagram_syntax_valid: bool
    sequence_diagram_syntax_errors: list[str]

    api_spec: dict[str, Any]
    api_spec_syntax_valid: bool
    api_spec_syntax_errors: list[str]

    erd_puml: str
    erd_syntax_valid: bool
    erd_syntax_errors: list[str]

    deployment_diagram_puml: str
    deployment_diagram_syntax_valid: bool
    deployment_diagram_syntax_errors: list[str]

    artifact_status: dict[str, str]


def usecase_spec_text(state: ArchitectureState) -> str:
    """The use case specification as prompt text.

    Every design artifact is derived from the use case specification produced by
    the requirements analysis agent, so this is the context the LLM gets.
    """
    spec = state.get("usecase_spec")
    if not spec:
        return ""
    if isinstance(spec, str):
        return spec
    return json.dumps(spec, ensure_ascii=False, indent=2)
