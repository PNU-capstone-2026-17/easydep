"""고정 OpenAPI를 읽어 작은 기능 계획을 HTTP로 직접 실행한다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx
import jsonschema

from app.testing.schemas.functional_plan import FunctionalTestCase

_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_EXAMPLE = "generated-example"


class UpstreamAmbiguity(ValueError):
    """고정 산출물에 실행을 정할 정보가 없을 때 사용한다."""


@dataclass(frozen=True)
class Operation:
    operation_id: str
    path: str
    method: str
    value: dict[str, Any]
    path_item: dict[str, Any]


def _ambiguity(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "gateStatus": "INCONCLUSIVE",
        "reason": reason,
        "defectClass": "UPSTREAM_AMBIGUITY",
        "failureClass": "UPSTREAM_AMBIGUITY",
        **extra,
    }


def _ref(
    document: dict[str, Any], schema: Any, seen: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """입력 예시와 leaf 탐색에 필요한 local $ref 하나만 푼다."""
    if not isinstance(schema, dict):
        raise UpstreamAmbiguity("OpenAPI schema가 object가 아닙니다.")
    pointer = schema.get("$ref")
    if not pointer:
        return schema
    if not isinstance(pointer, str) or not pointer.startswith("#/") or pointer in seen:
        raise UpstreamAmbiguity(f"OpenAPI schema 참조를 해석할 수 없습니다: {pointer}")
    target: Any = document
    for part in pointer[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            raise UpstreamAmbiguity(f"OpenAPI schema 참조가 없습니다: {pointer}")
        target = target[part]
    return {
        **_ref(document, target, seen | {pointer}),
        **{key: value for key, value in schema.items() if key != "$ref"},
    }


def _inline_refs(document: dict[str, Any], value: Any, seen: frozenset[str] = frozenset()) -> Any:
    """독립 response schema 안의 local ref를 실제 선언으로 펼친다."""
    if isinstance(value, list):
        return [_inline_refs(document, item, seen) for item in value]
    if not isinstance(value, dict):
        return value
    pointer = value.get("$ref")
    if pointer:
        if not isinstance(pointer, str) or not pointer.startswith("#/") or pointer in seen:
            raise UpstreamAmbiguity(f"OpenAPI schema 참조를 펼칠 수 없습니다: {pointer}")
        target: Any = document
        for part in pointer[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                raise UpstreamAmbiguity(f"OpenAPI schema 참조가 없습니다: {pointer}")
            target = target[part]
        merged = {
            **target,
            **{key: child for key, child in value.items() if key != "$ref"},
        }
        return _inline_refs(document, merged, seen | {pointer})
    return {key: _inline_refs(document, child, seen) for key, child in value.items()}


def _type(document: dict[str, Any], schema: Any) -> str:
    value = _ref(document, schema)
    kind = value.get("type")
    if isinstance(kind, str) and kind:
        return kind
    if isinstance(value.get("properties"), dict):
        return "object"
    if "items" in value:
        return "array"
    if value.get("enum"):
        return "string"
    raise UpstreamAmbiguity("OpenAPI schema type이 비어 있습니다.")


def _fields(document: dict[str, Any], schema: Any) -> list[tuple[str, dict[str, Any], bool]]:
    value = _ref(document, schema)
    if _type(document, value) != "object":
        return []
    properties = value.get("properties")
    # ``type: object``만 선언한 응답도 유효한 OpenAPI다. 검사할 field가 없다는
    # 뜻으로 받아들이되, 잘못된 properties 타입은 계약 오류로 구분한다.
    if properties is None:
        return []
    if not isinstance(properties, dict):
        raise UpstreamAmbiguity("object OpenAPI schema에 properties가 없습니다.")
    required = set(value.get("required") or [])
    return [
        (str(name), _ref(document, child), name in required)
        for name, child in properties.items()
        if isinstance(child, dict)
    ]


def _sample(document: dict[str, Any], schema: Any) -> Any:
    """실재 데이터를 뜻하지 않는 타입 기반 안정 예시값을 만든다."""
    value = _ref(document, schema)
    if isinstance(value.get("enum"), list) and value["enum"]:
        return value["enum"][0]
    if "example" in value:
        return value["example"]
    kind, fmt = _type(document, value), str(value.get("format") or "")
    if kind == "string":
        return {
            "date": "2026-01-02",
            "date-time": "2026-01-02T03:04:05Z",
            "email": "easydep@example.test",
            "uuid": "00000000-0000-4000-8000-000000000001",
        }.get(fmt, "easydep-example")
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        items = value.get("items")
        if not isinstance(items, dict):
            raise UpstreamAmbiguity("array OpenAPI schema에 items가 없습니다.")
        return [_sample(document, items)]
    if kind == "object":
        return {
            name: _sample(document, child)
            for name, child, required in _fields(document, value)
            if required
        }
    raise UpstreamAmbiguity(f"안정 예시값을 만들 수 없는 OpenAPI type입니다: {kind}")


def _index(document: dict[str, Any]) -> dict[str, list[Operation]]:
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise UpstreamAmbiguity("고정 OpenAPI paths가 비어 있습니다.")
    result: dict[str, list[Operation]] = {}
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, value in path_item.items():
            operation_id = (
                value.get("operationId")
                if isinstance(value, dict) and str(method).lower() in _METHODS
                else None
            )
            if isinstance(operation_id, str) and operation_id.strip():
                item = Operation(operation_id.strip(), path, str(method).upper(), value, path_item)
                result.setdefault(item.operation_id, []).append(item)
    return result


def operation_for_id(document: dict[str, Any], operation_id: str, *, use_case_id: str) -> Operation:
    """부분 이름 비교 없이 operationId 하나를 OpenAPI 호출 하나로 확정한다."""
    matches = _index(document).get(operation_id, [])
    if len(matches) != 1:
        detail = "찾을 수 없습니다" if not matches else "중복됩니다"
        raise UpstreamAmbiguity(f"OpenAPI operationId가 {detail}: {operation_id}")
    operation = matches[0]
    trace = operation.value.get("x-easydep-use-case-ids")
    if not isinstance(trace, list) or not all(
        isinstance(item, str) and item.strip() for item in trace
    ):
        raise UpstreamAmbiguity(f"OpenAPI operation trace가 비어 있습니다: {operation_id}")
    if use_case_id not in trace:
        raise UpstreamAmbiguity(
            f"OpenAPI operation trace가 유스케이스와 맞지 않습니다: {operation_id} → {use_case_id}"
        )
    return operation


def _response_schema(
    document: dict[str, Any], operation: Operation, status: int | None = None
) -> dict[str, Any]:
    responses = operation.value.get("responses")
    if not isinstance(responses, dict):
        raise UpstreamAmbiguity(f"OpenAPI responses가 비어 있습니다: {operation.operation_id}")
    if status is None:
        candidates = [
            value
            for key, value in responses.items()
            if str(key).startswith("2") and isinstance(value, dict)
        ]
        if len(candidates) != 1:
            raise UpstreamAmbiguity(
                f"OpenAPI success response schema를 하나로 정할 수 없습니다: {operation.operation_id}"
            )
        response = candidates[0]
    else:
        response = (
            responses.get(str(status))
            or responses.get(f"{str(status)[0]}XX")
            or responses.get("default")
        )
    content = response.get("content") if isinstance(response, dict) else None
    json_content = content.get("application/json") if isinstance(content, dict) else None
    schema = json_content.get("schema") if isinstance(json_content, dict) else None
    if not isinstance(schema, dict):
        raise UpstreamAmbiguity(
            f"OpenAPI response schema가 비어 있습니다: {operation.operation_id}"
        )
    _type(document, schema)
    return schema


def _leaves(
    document: dict[str, Any], schema: Any, value: Any = None, path: str = ""
) -> list[tuple[str, str, Any, str]]:
    """schema leaf와 실제 값을 같은 name/type 키로 비교할 수 있게 평탄화한다."""
    resolved, kind = _ref(document, schema), _type(document, schema)
    if kind == "object":
        return [
            leaf
            for name, child, required in _fields(document, resolved)
            if required or (isinstance(value, dict) and name in value)
            for leaf in _leaves(
                document,
                child,
                value.get(name) if isinstance(value, dict) else None,
                f"{path}.{name}".strip("."),
            )
        ]
    if kind == "array":
        items = resolved.get("items")
        if not isinstance(items, dict):
            raise UpstreamAmbiguity("array OpenAPI schema에 items가 없습니다.")
        first = value[0] if isinstance(value, list) and value else None
        return _leaves(document, items, first, path + "[]")
    return [(path.rsplit(".", 1)[-1].replace("[]", ""), kind, value, path)]


def _inputs(
    document: dict[str, Any], operation: Operation, previous: dict[tuple[str, str], list[Any]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], Any, dict[str, str]]:
    """필수 입력은 예시로 채우고 유일한 이전 response leaf만 덮어쓴다."""
    path_values: dict[str, Any] = {}
    query: dict[str, Any] = {}
    headers: dict[str, str] = {}
    sources: dict[str, str] = {}

    def transferred(name: str, schema: dict[str, Any], location: str) -> Any:
        values = previous.get((name, _type(document, schema)), [])
        if len(values) == 1:
            sources[location] = "previous-response"
            return values[0]
        sources[location] = _EXAMPLE
        return _sample(document, schema)

    seen: set[tuple[str, str]] = set()
    for owner in (operation.path_item, operation.value):
        for parameter in owner.get("parameters") or []:
            if not isinstance(parameter, dict):
                continue
            name, where, schema = (
                str(parameter.get("name") or ""),
                str(parameter.get("in") or ""),
                parameter.get("schema"),
            )
            required = bool(parameter.get("required")) or where == "path"
            if not required or (where, name) in seen:
                continue
            seen.add((where, name))
            if where not in {"path", "query", "header"} or not name or not isinstance(schema, dict):
                raise UpstreamAmbiguity(
                    f"OpenAPI parameter schema가 불완전합니다: {operation.operation_id}"
                )
            value = transferred(name, schema, f"{where}.{name}")
            if where == "path":
                path_values[name] = value
            elif where == "query":
                query[name] = value
            else:
                headers[name] = str(value)
    body = operation.value.get("requestBody")
    body_schema = (
        ((body.get("content") or {}).get("application/json") or {}).get("schema")
        if isinstance(body, dict) and body.get("required")
        else None
    )
    if not isinstance(body_schema, dict):
        return path_values, query, headers, None, sources
    sample = _sample(document, body_schema)
    for name, kind, _value, path in _leaves(document, body_schema):
        values = previous.get((name, kind), [])
        if len(values) != 1:
            sources["body." + path] = _EXAMPLE
            continue
        cursor: Any = sample
        for part in path.replace("[]", ".0").split(".")[:-1]:
            cursor = cursor[int(part)] if part.isdigit() else cursor[part]
        last = path.replace("[]", ".0").split(".")[-1]
        if last.isdigit():
            cursor[int(last)] = values[0]
        else:
            cursor[last] = values[0]
        sources["body." + path] = "previous-response"
    return path_values, query, headers, sample, sources


def _basic_auth() -> tuple[str, str]:
    """로컬 Testing 앱의 공통 Basic 테스트 계정을 모든 호출에 보낸다."""
    return os.environ.get("EASYDEP_TEST_USERNAME", "easydep-test"), os.environ.get(
        "EASYDEP_TEST_PASSWORD", "easydep-test"
    )


def _request_fields(document: dict[str, Any], operation: Operation) -> list[dict[str, str]]:
    """LLM이 값 대신 순서를 판단할 수 있도록 필수 field 이름과 type만 보인다."""
    result: list[dict[str, str]] = []
    for owner in (operation.path_item, operation.value):
        for parameter in owner.get("parameters") or []:
            if (
                isinstance(parameter, dict)
                and (parameter.get("required") or parameter.get("in") == "path")
                and isinstance(parameter.get("schema"), dict)
            ):
                result.append(
                    {
                        "name": str(parameter.get("name") or ""),
                        "type": _type(document, parameter["schema"]),
                    }
                )
    body = operation.value.get("requestBody")
    schema = (
        ((body.get("content") or {}).get("application/json") or {}).get("schema")
        if isinstance(body, dict) and body.get("required")
        else None
    )
    if isinstance(schema, dict):
        result.extend(
            {"name": name, "type": kind} for name, kind, _value, _path in _leaves(document, schema)
        )
    return result


def _url(
    target_url: str, operation: Operation, values: dict[str, Any], query: dict[str, Any]
) -> str:
    path = operation.path
    for name, value in values.items():
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    if "{" in path or "}" in path:
        raise UpstreamAmbiguity(f"필수 path parameter를 채울 수 없습니다: {operation.path}")
    return urljoin(target_url.rstrip("/") + "/", path.lstrip("/")) + (
        ("?" + urlencode(query, doseq=True)) if query else ""
    )


def _finding(
    step_id: str,
    operation_id: str,
    code: str,
    message: str,
    *,
    status: int | None = None,
    body: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "stepId": step_id,
        "operationId": operation_id,
        "code": code,
        "message": message,
    }
    if status is not None:
        result["statusCode"] = status
    if body:
        result["responseBody"] = body[-2000:]
    return result


def execute_functional_plan(
    plan: FunctionalTestCase,
    *,
    openapi: dict[str, Any],
    target_url: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """계획 순서대로 HTTP를 호출하고 첫 정확한 실패를 기록한다."""
    try:
        operations = [
            (step, operation_for_id(openapi, step.operation_id, use_case_id=plan.use_case_id))
            for step in plan.steps
        ]
        for _, operation in operations:
            _response_schema(openapi, operation)
    except UpstreamAmbiguity as error:
        return _ambiguity(str(error))
    previous: dict[tuple[str, str], list[Any]] = {}
    reports: list[dict[str, Any]] = []
    for step, operation in operations:
        try:
            paths, query, headers, body, sources = _inputs(openapi, operation, previous)
            response = httpx.request(
                operation.method,
                _url(target_url, operation, paths, query),
                headers={"Accept": "application/json", **headers},
                json=body,
                auth=_basic_auth(),
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        except UpstreamAmbiguity as error:
            return _ambiguity(
                str(error),
                steps=reports,
                finding=_finding(step.step_id, step.operation_id, "UPSTREAM_AMBIGUITY", str(error)),
            )
        except httpx.RequestError as error:
            message = f"HTTP request could not reach {operation.method} {operation.path}: {error}"
            return {
                "status": "unavailable",
                "gateStatus": "INCONCLUSIVE",
                "reason": message,
                "defectClass": "ENVIRONMENT_DEFECT",
                "steps": reports,
                "finding": _finding(
                    step.step_id, step.operation_id, "HTTP_TRANSPORT_ERROR", message
                ),
            }
        report = {
            "stepId": step.step_id,
            "operationId": step.operation_id,
            "method": operation.method,
            "path": operation.path,
            "statusCode": response.status_code,
            "inputSources": sources,
        }
        if not 200 <= response.status_code < 300:
            generated_inputs = sorted(
                location for location, source in sources.items() if source == _EXAMPLE
            )
            needs_fixture = 400 <= response.status_code < 500 and bool(generated_inputs)
            message = (
                f"{operation.method} {operation.path} could not reach its success path with "
                "schema-generated values; the test profile needs valid prerequisite data."
                if needs_fixture
                else f"{operation.method} {operation.path} returned HTTP "
                f"{response.status_code}; 2xx success was required."
            )
            finding = _finding(
                step.step_id,
                step.operation_id,
                "TEST_PROFILE_DATA_UNAVAILABLE"
                if needs_fixture
                else "HTTP_STATUS_NOT_SUCCESS",
                message,
                status=response.status_code,
                body=response.text,
            )
            if needs_fixture:
                finding["generatedInputs"] = generated_inputs
            return {
                "status": "failed",
                "gateStatus": "FAIL",
                "reason": finding["message"],
                "defectClass": "SUT_DEFECT",
                "steps": reports + [report],
                "finding": finding,
            }
        try:
            schema = _response_schema(openapi, operation, response.status_code)
            payload = response.json()
            errors = sorted(
                jsonschema.Draft202012Validator(_inline_refs(openapi, schema)).iter_errors(payload),
                key=str,
            )
        except UpstreamAmbiguity as error:
            return _ambiguity(
                str(error),
                steps=reports + [report],
                finding=_finding(step.step_id, step.operation_id, "UPSTREAM_AMBIGUITY", str(error)),
            )
        except (json.JSONDecodeError, ValueError, jsonschema.SchemaError) as error:
            errors = [error]
        if errors:
            finding = _finding(
                step.step_id,
                step.operation_id,
                "RESPONSE_SCHEMA_MISMATCH",
                str(errors[0]),
                status=response.status_code,
                body=response.text,
            )
            return {
                "status": "failed",
                "gateStatus": "FAIL",
                "reason": finding["message"],
                "defectClass": "SUT_DEFECT",
                "steps": reports + [report],
                "finding": finding,
            }
        for name, kind, value, _path in _leaves(openapi, schema, payload):
            # 빈 배열이나 빠진 선택 필드는 다음 호출에 전달할 실제 값이 아니다. None을
            # 하나뿐인 응답 값으로 취급하면 올바른 예시값보다 먼저 선택된다.
            if value is not None:
                previous.setdefault((name, kind), []).append(value)
        reports.append(report)
    return {
        "status": "passed",
        "gateStatus": "PASS",
        "steps": reports,
        "inputExampleMarker": _EXAMPLE,
    }


def operation_prompt_projection(
    document: dict[str, Any], operation_ids: list[str], *, use_case_id: str
) -> list[dict[str, Any]]:
    """LLM에는 operationId와 입출력 schema leaf만 보낸다."""
    result = []
    for operation_id in operation_ids:
        operation = operation_for_id(document, operation_id, use_case_id=use_case_id)
        response = _response_schema(document, operation)
        result.append(
            {
                "operationId": operation_id,
                "requiredRequestFields": _request_fields(document, operation),
                "successResponseFields": [
                    {"name": name, "type": kind}
                    for name, kind, _value, _path in _leaves(document, response)
                ],
            }
        )
    return result


__all__ = [
    "UpstreamAmbiguity",
    "execute_functional_plan",
    "operation_for_id",
    "operation_prompt_projection",
]
