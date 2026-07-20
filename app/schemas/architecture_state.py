from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict


class ArchitectureState(TypedDict, total=False):
    app_id: str
    source: str
    scenario_file_path: str
    scenario_text: str

    extracted_bce_classes: dict[str, Any]
    class_diagram_puml: str
    class_diagram_output_path: str
    plantuml_jar_path: str
    class_diagram_feedback_requested: bool
    class_diagram_requested_feedback: str
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
