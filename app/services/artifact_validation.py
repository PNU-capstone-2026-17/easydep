from __future__ import annotations

from typing import Any

from app.services.plantuml_error import check_plantuml_syntax


def validate_puml_artifact(puml_text: str) -> dict[str, Any]:
    errors = check_plantuml_syntax(puml_text)
    return {
        "syntax_valid": not errors,
        "syntax_errors": errors,
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
