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
    class_diagram_compile_result: dict[str, Any]
    class_diagram_feedback_requested: bool
    class_diagram_requested_feedback: str
    class_diagram_feedback: str
    class_diagram_feedback_attempts: int
    class_diagram_max_feedback_attempts: int
    class_diagram_syntax_valid: bool
    class_diagram_syntax_errors: list[str]
    class_diagram_saved_to_db: bool

    sequence_diagram_puml: str
    sequence_diagram_feedback_requested: bool
    sequence_diagram_requested_feedback: str
    sequence_diagram_feedback: str
    sequence_diagram_syntax_valid: bool
    sequence_diagram_syntax_errors: list[str]
    sequence_diagram_compile_result: dict[str, Any]

    api_spec: dict[str, Any]
    api_spec_feedback_requested: bool
    api_spec_requested_feedback: str
    api_spec_feedback: str
    api_spec_syntax_valid: bool
    api_spec_syntax_errors: list[str]

    erd_puml: str
    erd_feedback_requested: bool
    erd_requested_feedback: str
    erd_feedback: str
    erd_syntax_valid: bool
    erd_syntax_errors: list[str]
    erd_compile_result: dict[str, Any]

    deployment_diagram_puml: str
    deployment_diagram_feedback_requested: bool
    deployment_diagram_requested_feedback: str
    deployment_diagram_feedback: str
    deployment_diagram_syntax_valid: bool
    deployment_diagram_syntax_errors: list[str]
    deployment_diagram_compile_result: dict[str, Any]

    artifact_status: dict[str, str]
    warnings: list[str]
