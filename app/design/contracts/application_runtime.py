"""요구사항과 OpenAPI에서 애플리케이션 실행 보안 요구를 읽는다."""

from __future__ import annotations

import re
from typing import Any

_SECURITY_WORDS = re.compile(
    r"\b(?:authenticat(?:e|ed|ion)|authoriz(?:e|ed|ation))\b|인증|인가|접근\s*권한",
    re.IGNORECASE,
)


def application_security_source_refs(
    api_spec: dict[str, Any] | None,
    refined_requirements: Any,
) -> list[str]:
    """명시적인 인증·인가 요구가 있는 설계 주소를 반환한다."""

    document = api_spec if isinstance(api_spec, dict) else {}
    components = document.get("components")
    schemes = components.get("securitySchemes") if isinstance(components, dict) else None
    paths = document.get("paths")
    api_security = bool(document.get("security") or schemes) or (
        isinstance(paths, dict)
        and any(
            operation.get("security")
            for path_item in paths.values()
            if isinstance(path_item, dict)
            for operation in path_item.values()
            if isinstance(operation, dict)
        )
    )
    refs = ["apiSpec:security"] if api_security else []
    requirements = refined_requirements if isinstance(refined_requirements, list) else []
    for index, item in enumerate(requirements):
        if not isinstance(item, dict) or not _SECURITY_WORDS.search(str(item.get("text") or "")):
            continue
        requirement_id = str(item.get("id") or item.get("draft_ref") or index + 1)
        refs.append(f"requirement:{requirement_id}")
    return list(dict.fromkeys(refs))


def application_security_required(
    api_spec: dict[str, Any] | None,
    refined_requirements: Any,
) -> bool:
    """Spring 보안 설정이 필요한 명시 근거가 하나라도 있는지 반환한다."""

    return bool(application_security_source_refs(api_spec, refined_requirements))


__all__ = ["application_security_required", "application_security_source_refs"]
