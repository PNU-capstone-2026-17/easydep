"""산출물 검증. PlantUML은 jar 문법 검사로, API 명세는 필수 필드 검사로 확인한다.

두 검증 모두 여러 산출물이 공유하는 판정 형식({syntax_valid, syntax_errors})을
돌려주므로 common에 둔다.
"""
from __future__ import annotations

from typing import Any

from app.design.services.common.plantuml import check_plantuml_syntax


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
