from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.plantuml_class_diagram import (
    compile_plantuml_to_image,
    save_plantuml_file,
)
from app.services.plantuml_error import extract_plantuml_error_hint


def validate_puml_artifact(
    puml_text: str,
    output_path: str,
    plantuml_jar_path: str,
) -> dict[str, Any]:
    if not puml_text.strip():
        return {
            "compile_result": {
                "success": False,
                "error_message": "PlantUML code is empty.",
            },
            "syntax_valid": False,
            "syntax_errors": ["PlantUML code is empty."],
        }

    save_plantuml_file(puml_text, output_path)
    compile_result = compile_plantuml_to_image(output_path, plantuml_jar_path)
    error_message = compile_result.get("error_message")
    syntax_errors = [error_message] if error_message else []

    if syntax_errors:
        try:
            hint = extract_plantuml_error_hint(puml_text, plantuml_jar_path)
        except Exception:
            hint = ""
        if hint:
            syntax_errors.append(hint)

    return {
        "compile_result": compile_result,
        "syntax_valid": not syntax_errors,
        "syntax_errors": syntax_errors,
    }


def validate_api_spec(api_spec: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(api_spec, dict) or not api_spec:
        errors.append("API specification is empty.")
    if "openapi" not in api_spec:
        errors.append("API specification must include an 'openapi' field.")
    if "paths" not in api_spec:
        errors.append("API specification must include a 'paths' object.")

    return {
        "syntax_valid": not errors,
        "syntax_errors": errors,
    }


def artifact_output_path(output_dir: str | Path, filename: str) -> str:
    output_directory = Path(output_dir)
    output_directory.mkdir(exist_ok=True)
    return str(output_directory / filename)


def write_json_artifact(data: dict[str, Any], output_path: str) -> None:
    Path(output_path).parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
