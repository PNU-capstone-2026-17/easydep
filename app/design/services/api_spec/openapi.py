"""API 엔드포인트 모델(JSON)을 OpenAPI 3.1 명세로 조립한다.

클래스 다이어그램의 PlantUML 변환과 같은 자리다 — 다만 결과가 그림이 아니라 JSON일 뿐,
성질은 같다: 결정론적이고, 구성에 의해 유효하다. `openapi`와 `paths`가 여기서 반드시
채워지므로 validate_api_spec은 트립와이어일 뿐 수리를 부르는 게이트가 아니다.

$ref는 모델이 실제로 돌려준 스키마만 가리킨다. 모델이 없는 이름을 참조하면 그 자리는
느슨한 object로 떨어진다 — 깨진 $ref가 있는 문서보다 낫다.
"""
from __future__ import annotations

import re
from typing import Any

OPENAPI_VERSION = "3.1.0"

#: OpenAPI가 아는 원시 타입. 이 밖의 값은 스키마 이름으로 본다.
_PRIMITIVES = {"string", "integer", "number", "boolean", "array", "object"}


def sanitize_schema_name(name: str) -> str:
    """components 키와 $ref에 안전한 단어 문자만 남긴다."""
    if not name:
        return ""
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name.strip())


def sanitize_path(path: str) -> str:
    """경로를 '/'로 시작하는 한 줄로 정규화한다."""
    cleaned = re.sub(r"\s+", "", str(path or "")).strip()
    if not cleaned:
        return "/"
    return cleaned if cleaned.startswith("/") else f"/{cleaned}"


def _field_schema(field: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """필드 하나의 타입 표현. 알려진 스키마 이름이면 $ref로 잇는다."""
    raw = str(field.get("type", "string")).strip()
    lowered = raw.lower()
    if lowered in _PRIMITIVES:
        schema: dict[str, Any] = {"type": lowered}
        if lowered == "array":
            # 원소 타입을 따로 안 받으므로 느슨하게 둔다 — 거짓 정보를 적는 것보다 낫다.
            schema["items"] = {}
    elif sanitize_schema_name(raw) in known:
        schema = {"$ref": f"#/components/schemas/{sanitize_schema_name(raw)}"}
    else:
        schema = {"type": "object"}

    description = str(field.get("description", "")).strip()
    if description and "$ref" not in schema:
        schema["description"] = description
    return schema


def _body_schema(name: str, known: set[str], is_array: bool = False) -> dict[str, Any]:
    ref_name = sanitize_schema_name(name)
    inner: dict[str, Any] = (
        {"$ref": f"#/components/schemas/{ref_name}"}
        if ref_name in known
        else {"type": "object"}
    )
    return {"type": "array", "items": inner} if is_array else inner


def _parameters(
    endpoint: dict[str, Any], known: set[str]
) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    for location, key in (("path", "path_params"), ("query", "query_params")):
        for field in endpoint.get(key, []):
            name = str(field.get("name", "")).strip()
            if not name:
                continue
            parameters.append(
                {
                    "name": name,
                    "in": location,
                    # 경로 변수는 OpenAPI 규약상 항상 required다.
                    "required": True if location == "path" else bool(field.get("required")),
                    "schema": _field_schema(field, known),
                    **(
                        {"description": str(field["description"]).strip()}
                        if str(field.get("description", "")).strip()
                        else {}
                    ),
                }
            )
    return parameters


def _responses(endpoint: dict[str, Any], known: set[str]) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for response in endpoint.get("responses", []):
        status = str(response.get("status", 200))
        body: dict[str, Any] = {
            "description": str(response.get("description", "")).strip() or "Response"
        }
        schema_name = str(response.get("schema_name", "")).strip()
        if schema_name:
            body["content"] = {
                "application/json": {
                    "schema": _body_schema(
                        schema_name, known, bool(response.get("is_array"))
                    )
                }
            }
        responses[status] = body

    # 응답이 하나도 없는 오퍼레이션은 OpenAPI에서 유효하지 않다.
    return responses or {"200": {"description": "Response"}}


def build_openapi_from_model(model: dict[str, Any]) -> dict[str, Any]:
    """엔드포인트 모델을 OpenAPI 3.1 문서(dict)로 조립한다.

    엔드포인트도 스키마도 없으면 빈 dict를 반환한다(조립할 대상 없음) — 클래스
    다이어그램의 변환이 빈 모델에 빈 문자열을 돌려주는 것과 같은 규칙이다.
    """
    if not model:
        return {}

    endpoints = model.get("Endpoints", [])
    schemas = model.get("Schemas", [])
    if not endpoints and not schemas:
        return {}

    known = {
        sanitize_schema_name(s.get("name", ""))
        for s in schemas
        if sanitize_schema_name(s.get("name", ""))
    }

    components: dict[str, Any] = {}
    for schema in schemas:
        name = sanitize_schema_name(schema.get("name", ""))
        if not name:
            continue
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field in schema.get("fields", []):
            field_name = str(field.get("name", "")).strip()
            if not field_name:
                continue
            properties[field_name] = _field_schema(field, known)
            if field.get("required"):
                required.append(field_name)

        entry: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            entry["required"] = required
        description = str(schema.get("description", "")).strip()
        if description:
            entry["description"] = description
        components[name] = entry

    paths: dict[str, Any] = {}
    for endpoint in endpoints:
        path = sanitize_path(endpoint.get("path", "/"))
        method = str(endpoint.get("method", "get")).strip().lower()
        if method not in ("get", "post", "put", "patch", "delete", "head", "options"):
            method = "get"

        operation: dict[str, Any] = {"responses": _responses(endpoint, known)}

        summary = str(endpoint.get("summary", "")).strip()
        if summary:
            operation["summary"] = summary
        operation_id = str(endpoint.get("operation_id", "")).strip()
        if operation_id:
            operation["operationId"] = operation_id

        parameters = _parameters(endpoint, known)
        if parameters:
            operation["parameters"] = parameters

        request_schema = str(endpoint.get("request_schema", "")).strip()
        if request_schema and method in ("post", "put", "patch"):
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": _body_schema(request_schema, known)}
                },
            }

        paths.setdefault(path, {})[method] = operation

    document: dict[str, Any] = {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": str(model.get("title", "")).strip() or "API",
            "version": str(model.get("version", "")).strip() or "1.0.0",
        },
        "paths": paths,
    }
    if components:
        document["components"] = {"schemas": components}
    return document
