"""승인된 API 모델을 OpenAPI 3.1 JSON으로 결정론적으로 투영한다."""
from __future__ import annotations

import re
from typing import Any

from app.design.contracts.api_spec import ApiSpecModel

OPENAPI_VERSION = "3.1.0"
_PRIMITIVES = {"string", "integer", "number", "boolean", "array", "object"}


def sanitize_schema_name(name: str) -> str:
    """component key와 reference에 안전한 문자만 남긴다."""

    if not name:
        return ""
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name.strip())


def sanitize_path(path: str) -> str:
    """path를 한 줄의 absolute OpenAPI path로 정규화한다."""

    cleaned = re.sub(r"\s+", "", str(path or "")).strip()
    if not cleaned:
        return "/"
    return cleaned if cleaned.startswith("/") else f"/{cleaned}"


def _field_schema(field: dict[str, Any], known: set[str]) -> dict[str, Any]:
    raw = str(field.get("type", "string")).strip()
    if raw.endswith("[]"):
        item = _field_schema({"type": raw[:-2]}, known)
        return {"type": "array", "items": item}
    lowered = raw.lower()
    if lowered in _PRIMITIVES:
        schema: dict[str, Any] = {"type": lowered}
        if lowered == "array":
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
    primitive = ref_name.lower()
    inner: dict[str, Any] = (
        {"type": primitive}
        if primitive in _PRIMITIVES - {"array", "object"}
        else (
            {"$ref": f"#/components/schemas/{ref_name}"}
            if ref_name in known
            else {"type": "object"}
        )
    )
    return {"type": "array", "items": inner} if is_array else inner


def _parameters(endpoint: dict[str, Any], known: set[str]) -> list[dict[str, Any]]:
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
                    "required": True
                    if location == "path"
                    else bool(field.get("required")),
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
    return responses or {"200": {"description": "Response"}}


def _control_binding(endpoint: dict[str, Any]) -> dict[str, Any] | None:
    raw = endpoint.get("control_binding")
    if not isinstance(raw, dict):
        return None
    control = str(raw.get("control", "")).strip()
    method = str(raw.get("method", "")).strip()
    if not control or not method:
        return None
    arguments = {
        str(item.get("name", "")).strip(): str(item.get("source", "")).strip()
        for item in raw.get("arguments", [])
        if isinstance(item, dict)
        and str(item.get("name", "")).strip()
        and str(item.get("source", "")).strip()
    }
    outcomes = {
        str(item.get("status")): str(item.get("outcome", "")).strip()
        for item in raw.get("outcomes", [])
        if isinstance(item, dict)
        and str(item.get("status", "")).strip()
        and str(item.get("outcome", "")).strip()
    }
    return {
        "control": control,
        "method": method,
        "arguments": arguments,
        "outcomes": outcomes,
    }


def build_openapi_from_model(model: ApiSpecModel) -> dict[str, Any]:
    """타입 endpoint 모델을 기존 OpenAPI JSON shape로 투영한다.

    Args:
        model: 승인된 API endpoint 모델이다.

    Returns:
        OpenAPI 3.1 mapping이며 완전히 빈 모델이면 빈 mapping이다.

    Notes:
        순수 함수이며 endpoint·schema·field·response 순서를 유지한다.
    """

    return build_openapi_from_payload(model.model_dump())


def build_openapi_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """호환 facade를 위해 느슨한 기존 dict 입력을 같은 방식으로 투영한다."""

    endpoints = payload.get("Endpoints", [])
    schemas = payload.get("Schemas", [])
    if not endpoints and not schemas:
        return {}
    known = {
        sanitize_schema_name(schema.get("name", ""))
        for schema in schemas
        if sanitize_schema_name(schema.get("name", ""))
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
        if method not in (
            "get",
            "post",
            "put",
            "patch",
            "delete",
            "head",
            "options",
        ):
            method = "get"
        operation: dict[str, Any] = {"responses": _responses(endpoint, known)}
        summary = str(endpoint.get("summary", "")).strip()
        if summary:
            operation["summary"] = summary
        operation_id = str(endpoint.get("operation_id", "")).strip()
        if operation_id:
            operation["operationId"] = operation_id
        control_binding = _control_binding(endpoint)
        if control_binding:
            operation["x-easydep-control"] = control_binding
        parameters = _parameters(endpoint, known)
        if parameters:
            operation["parameters"] = parameters
        request_schema = str(endpoint.get("request_schema", "")).strip()
        if request_schema and method in ("post", "put", "patch"):
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": _body_schema(request_schema, known)
                    }
                },
            }
        paths.setdefault(path, {})[method] = operation

    document: dict[str, Any] = {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": str(payload.get("title", "")).strip() or "API",
            "version": str(payload.get("version", "")).strip() or "1.0.0",
        },
        "paths": paths,
    }
    if components:
        document["components"] = {"schemas": components}
    return document
