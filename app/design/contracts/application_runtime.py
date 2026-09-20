"""요구사항과 OpenAPI에서 애플리케이션 실행 보안 요구를 읽는다."""

from __future__ import annotations

import re
from typing import Any

_SECURITY_WORDS = re.compile(
    r"\b(?:authenticat(?:e|ed|ion)|authoriz(?:e|ed|ation))\b|인증|인가|접근\s*권한",
    re.IGNORECASE,
)
_EXPLICIT_NO_AUTH = re.compile(
    r"\b(?:shall|must|do|does)\s+not\s+(?:require|need)\s+(?:any\s+)?"
    r"(?:authentication|authorization)\b"
    r"|\b(?:authentication|authorization)\s+is\s+not\s+(?:required|needed)\b"
    r"|\bno\s+(?:authentication|authorization)\s+(?:is\s+)?(?:required|needed)\b"
    r"|(?<!not\s)(?<!never\s)\b(?:allow|allows|permit|permits)\s+(?:all\s+)?"
    r"(?:requests?|access|use)\s+without\s+(?:authentication|authorization)\b"
    r"|\b(?:access|requests?|use)\s+without\s+(?:authentication|authorization)\s+"
    r"(?:is|are)\s+(?:allowed|permitted)\b"
    r"|(?:인증|인가)(?:을|를|이|가|은|는)?\s*(?:요구|필요(?:로)?)하지\s*않"
    r"|(?:인증|인가)(?:이|가|은|는)?\s*필요(?:가)?\s*없"
    r"|(?:인증|인가)(?:이|가|은|는)?\s*요구되지\s*않"
    r"|(?:인증|인가)\s*없이\s*(?:모든\s*)?(?:요청|접근|이용|사용)(?:을|이|은)?\s*"
    r"(?:허용(?:한다|된다|해야\s*한다)|가능(?:하다|해야\s*한다)|할\s*수\s*있)",
    re.IGNORECASE,
)
_STATEMENT_BOUNDARY = re.compile(r"(?:[.!?;。！？；]+|\r?\n+)\s*")


def _has_positive_security_statement(text: str) -> bool:
    """Keep positive security evidence while ignoring explicit no-auth statements."""

    statements = (item.strip() for item in _STATEMENT_BOUNDARY.split(text))
    return any(
        _SECURITY_WORDS.search(_EXPLICIT_NO_AUTH.sub("", statement))
        for statement in statements
        if statement
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
        if not isinstance(item, dict) or not _has_positive_security_statement(
            str(item.get("text") or "")
        ):
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
