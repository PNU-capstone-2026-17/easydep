"""요구사항에 명시된 JSON 필드와 OpenAPI 스키마의 독립 추적성 검사."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["request", "response", "unspecified"]
FIELD_START = re.compile(r"\bfields?\s+(?:named\s+)?", re.IGNORECASE)
MARKED_IDENTIFIER = re.compile(
    r"(?:\*\*|`)(?P<name>[A-Za-z][A-Za-z0-9_]*)(?:\*\*|`)"
)
FIELD_TOKEN = re.compile(
    r"\s*(?:(?P<comma>,)|(?P<and>\band\b)|(?P<name>[A-Za-z][A-Za-z0-9_]*))",
    re.IGNORECASE,
)
CONTEXT = {
    # payload는 request/response 양쪽에 쓰이므로 방향 근거로 사용하지 않는다.
    "request": re.compile(r"\b(?:request|accepts?)\b", re.IGNORECASE),
    "response": re.compile(r"\b(?:response|returns?|responds?)\b", re.IGNORECASE),
}
STOPWORDS = {
    "a", "an", "and", "containing", "field", "fields", "following", "json", "the",
}
BOUNDARY_WORDS = {
    "for", "including", "returns", "return", "responds", "respond",
    "supporting", "using", "when", "where", "which", "while", "with",
}


@dataclass(frozen=True)
class FieldClaim:
    field: str
    direction: Direction
    requirement_id: str
    evidence: str


def _direction(prefix: str) -> Direction:
    positions = {
        name: max((match.start() for match in pattern.finditer(prefix)), default=-1)
        for name, pattern in CONTEXT.items()
    }
    if positions["request"] == positions["response"] == -1:
        return "unspecified"
    return "response" if positions["response"] > positions["request"] else "request"


def _field_names(text: str) -> list[str]:
    """쉼표와 and로 연결된 명시적 식별자 목록만 소비한다."""
    fields: list[str] = []
    position = 0
    expect_name = True
    while match := FIELD_TOKEN.match(text, position):
        position = match.end()
        if expect_name:
            if match.group("and"):
                continue
            name = match.group("name")
            if not name or name.lower() in STOPWORDS | BOUNDARY_WORDS:
                break
            fields.append(name)
            expect_name = False
            continue
        if not (match.group("comma") or match.group("and")):
            break
        expect_name = True
    return fields


def explicit_field_claims(requirements: list[dict[str, Any]]) -> list[FieldClaim]:
    """명시적 fields 절만 읽고 암시적 이름은 추론하지 않는다."""
    claims: list[FieldClaim] = []
    for item in requirements:
        text = str(item.get("text") or "")
        requirement_id = str(item.get("id") or "unknown")
        for match in FIELD_START.finditer(text):
            direction = _direction(text[: match.start()])
            clause = MARKED_IDENTIFIER.sub(r"\g<name>", text[match.end():])
            for field in _field_names(clause):
                claims.append(FieldClaim(field, direction, requirement_id, text))
    return sorted(set(claims), key=lambda item: (
        item.requirement_id, item.direction, item.field
    ))


def _resolve_schema(api_spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        return ((api_spec.get("components") or {}).get("schemas") or {}).get(name) or {}
    return schema


def _schema_fields(api_spec: dict[str, Any], schema: Any) -> set[str]:
    resolved = _resolve_schema(api_spec, schema)
    if resolved.get("type") == "array":
        return _schema_fields(api_spec, resolved.get("items"))
    fields = set((resolved.get("properties") or {}).keys())
    for branch in ("allOf", "oneOf", "anyOf"):
        for nested in resolved.get(branch) or []:
            fields.update(_schema_fields(api_spec, nested))
    return fields


def openapi_fields(api_spec: dict[str, Any]) -> dict[Direction, set[str]]:
    result: dict[Direction, set[str]] = {
        "request": set(), "response": set(), "unspecified": set()
    }
    for path_item in (api_spec.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue
            request_body = operation.get("requestBody") or {}
            for media in (request_body.get("content") or {}).values():
                result["request"].update(_schema_fields(api_spec, media.get("schema")))
            for response in (operation.get("responses") or {}).values():
                if not isinstance(response, dict):
                    continue
                for media in (response.get("content") or {}).values():
                    result["response"].update(_schema_fields(api_spec, media.get("schema")))
    result["unspecified"] = result["request"] | result["response"]
    return result


def missing_explicit_fields(
    requirements: list[dict[str, Any]], api_spec: dict[str, Any]
) -> list[FieldClaim]:
    available = openapi_fields(api_spec)
    return [
        claim for claim in explicit_field_claims(requirements)
        if claim.field not in available[claim.direction]
    ]
