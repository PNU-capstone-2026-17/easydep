"""고정 OpenAPI를 읽어 작은 기능 계획을 HTTP로 직접 실행한다."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx
import jsonschema

from app.testing.schemas.functional_plan import FunctionalInputValue, FunctionalTestCase

_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_GENERATED = "generated-input"
_SUMMARY_STRING_LIMIT = 512
_SUMMARY_SIZE_LIMIT = 4000


class UpstreamAmbiguity(ValueError):
    """고정 산출물에 실행을 정할 정보가 없을 때 사용한다."""


class TestInputError(ValueError):
    """테스트가 제안한 입력이 OpenAPI 계약을 통과하지 못할 때 사용한다."""


@dataclass(frozen=True)
class Operation:
    operation_id: str
    path: str
    method: str
    value: dict[str, Any]
    path_item: dict[str, Any]


@dataclass(frozen=True)
class InputValueRequest:
    """LLM에 전체 요청이 아니라 값 하나만 물어보기 위한 최소 입력이다."""

    operation_id: str
    location: str
    schema: dict[str, Any]
    operation_context: str = ""


InputValueProposer = Callable[[InputValueRequest], Any]


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
        raise UpstreamAmbiguity("The OpenAPI schema must be an object.")
    pointer = schema.get("$ref")
    if not pointer:
        return schema
    if not isinstance(pointer, str) or not pointer.startswith("#/") or pointer in seen:
        raise UpstreamAmbiguity(f"The OpenAPI schema reference cannot be resolved: {pointer}")
    target: Any = document
    for part in pointer[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            raise UpstreamAmbiguity(f"The OpenAPI schema reference does not exist: {pointer}")
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
            raise UpstreamAmbiguity(f"The OpenAPI schema reference cannot be expanded: {pointer}")
        target: Any = document
        for part in pointer[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                raise UpstreamAmbiguity(
                    f"The OpenAPI schema reference does not exist: {pointer}"
                )
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
    alternatives = value.get("anyOf")
    if isinstance(alternatives, list):
        concrete = [
            item
            for item in alternatives
            if isinstance(item, dict) and item.get("type") != "null"
        ]
        has_null = any(
            isinstance(item, dict) and item.get("type") == "null"
            for item in alternatives
        )
        if has_null and len(concrete) == 1:
            return _type(document, concrete[0])
    if isinstance(value.get("properties"), dict):
        return "object"
    if "items" in value:
        return "array"
    if value.get("enum"):
        return "string"
    raise UpstreamAmbiguity("The OpenAPI schema type is missing.")


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
        raise UpstreamAmbiguity("An object OpenAPI schema has no properties.")
    required = set(value.get("required") or [])
    return [
        (str(name), _ref(document, child), name in required)
        for name, child in properties.items()
        if isinstance(child, dict)
    ]


def _schema_errors(document: dict[str, Any], schema: dict[str, Any], value: Any) -> list[str]:
    """제안값을 실제 OpenAPI leaf schema로 다시 검사한다."""

    expanded = _inline_refs(document, schema)
    try:
        validator = jsonschema.Draft202012Validator(
            expanded,
            format_checker=jsonschema.FormatChecker(),
        )
        return [error.message for error in sorted(validator.iter_errors(value), key=str)]
    except jsonschema.SchemaError as error:
        raise UpstreamAmbiguity(f"The OpenAPI input schema is invalid: {error}") from error


def _bounded_number(schema: dict[str, Any], kind: str) -> int | float | None:
    """숫자 범위가 명시됐을 때 그 범위 안의 가장 단순한 값을 고른다."""

    lower = schema.get("minimum")
    upper = schema.get("maximum")
    exclusive_lower = schema.get("exclusiveMinimum")
    exclusive_upper = schema.get("exclusiveMaximum")
    if isinstance(exclusive_lower, (int, float)) and not isinstance(exclusive_lower, bool):
        lower = exclusive_lower + (1 if kind == "integer" else 0.1)
    if isinstance(exclusive_upper, (int, float)) and not isinstance(exclusive_upper, bool):
        upper = exclusive_upper - (1 if kind == "integer" else 0.1)
    if lower is None and upper is None:
        return None
    candidate = lower if isinstance(lower, (int, float)) else upper
    if not isinstance(candidate, (int, float)) or isinstance(candidate, bool):
        return None
    if kind == "integer":
        candidate = math.ceil(candidate) if lower is not None else math.floor(candidate)
    multiple = schema.get("multipleOf")
    if isinstance(multiple, (int, float)) and multiple > 0:
        if lower is not None:
            candidate = math.ceil(float(candidate) / multiple) * multiple
        else:
            candidate = math.floor(float(candidate) / multiple) * multiple
    return int(candidate) if kind == "integer" else float(candidate)


def _suggest_value(
    document: dict[str, Any],
    schema: dict[str, Any],
    *,
    operation_id: str,
    location: str,
    proposer: InputValueProposer | None,
    preserved: dict[tuple[str, str], FunctionalInputValue],
    suggestions: list[FunctionalInputValue],
    operation_context: str,
) -> Any:
    """근거가 없는 leaf 하나만 제안받고 OpenAPI로 검증한다."""

    key = (operation_id, location)
    if key in preserved:
        proposed = preserved[key]
        errors = _schema_errors(document, schema, proposed.value)
        if errors:
            raise TestInputError(
                f"Preserved input {location} is invalid for {operation_id}: {errors[0]}"
            )
        suggestions.append(proposed)
        return proposed.value
    if proposer is None:
        raise UpstreamAmbiguity(
            f"OpenAPI has no success-path example for {operation_id} {location}."
        )
    try:
        value = proposer(
            InputValueRequest(
                operation_id=operation_id,
                location=location,
                schema=_inline_refs(document, schema),
                operation_context=operation_context,
            )
        )
    except (TypeError, ValueError) as error:
        raise TestInputError(
            f"Input suggestion failed for {operation_id} {location}: {error}"
        ) from error
    errors = _schema_errors(document, schema, value)
    if errors:
        raise TestInputError(
            f"Suggested input {location} is invalid for {operation_id}: {errors[0]}"
        )
    proposed = FunctionalInputValue(
        operation_id=operation_id,
        location=location,
        value=value,
    )
    suggestions.append(proposed)
    return value


def _sample(
    document: dict[str, Any],
    schema: Any,
    *,
    operation_id: str,
    location: str,
    sources: dict[str, str],
    proposer: InputValueProposer | None,
    preserved: dict[tuple[str, str], FunctionalInputValue],
    suggestions: list[FunctionalInputValue],
    previous: dict[tuple[str, str], list[Any]],
    operation_context: str,
) -> Any:
    """OpenAPI 근거를 먼저 쓰고, 근거 없는 leaf만 LLM에 제안받는다."""

    value = _ref(document, schema)
    if "const" in value:
        sources[location] = "openapi-const"
        return value["const"]
    if isinstance(value.get("enum"), list) and value["enum"]:
        sources[location] = "openapi-enum"
        return value["enum"][0]
    for key in ("example", "default"):
        if key in value:
            sources[location] = f"openapi-{key}"
            return value[key]
    examples = value.get("examples")
    if isinstance(examples, list) and examples:
        sources[location] = "openapi-example"
        return examples[0]

    kind, fmt = _type(document, value), str(value.get("format") or "")
    # 같은 이름과 타입의 응답값이 하나뿐이면 그것이 가장 강한 실행 근거다. body를
    # 임시값으로 먼저 채웠다가 나중에 덮어쓰면 불필요한 LLM 호출이 생기므로 leaf에서
    # 바로 이전 응답을 선택한다.
    leaf_name = location.rsplit(".", 1)[-1].split("[", 1)[0]
    transferred = previous.get((leaf_name, kind), [])
    if len(transferred) == 1:
        sources[location] = "previous-response"
        return transferred[0]
    if kind == "string":
        by_format = {
            "date": "2026-01-02",
            "date-time": "2026-01-02T03:04:05Z",
            "email": "easydep@example.test",
            "hostname": "example.test",
            "ipv4": "192.0.2.1",
            "ipv6": "2001:db8::1",
            "uri": "https://example.test/resource",
            "uuid": "00000000-0000-4000-8000-000000000001",
        }
        if fmt in by_format:
            sources[location] = "openapi-format"
            return by_format[fmt]
    elif kind in {"integer", "number"}:
        if (candidate := _bounded_number(value, kind)) is not None:
            if not _schema_errors(document, value, candidate):
                sources[location] = "openapi-bound"
                return candidate
    elif kind == "array":
        items = value.get("items")
        if not isinstance(items, dict):
            raise UpstreamAmbiguity("An array OpenAPI schema has no items.")
        minimum = value.get("minItems")
        if isinstance(minimum, int) and minimum > 0:
            sources[location] = "openapi-bound"
            return [
                _sample(
                    document,
                    items,
                    operation_id=operation_id,
                    location=f"{location}[{index}]",
                    sources=sources,
                    proposer=proposer,
                    preserved=preserved,
                    suggestions=suggestions,
                    previous=previous,
                    operation_context=operation_context,
                )
                for index in range(minimum)
            ]
        if value.get("maxItems") == 0:
            sources[location] = "openapi-bound"
            return []
        # ``type: array``만으로는 빈 배열이 정상 흐름인지 알 수 없다. 예시나
        # minItems가 없다면 임의로 []를 보내지 않고 이 배열 값 하나만 제안받는다.
        # 특정 도메인의 원소 개수나 의미를 코드에 넣지 않아도 성공 입력을 만들 수 있다.
        sources[location] = (
            "preserved-suggestion"
            if (operation_id, location) in preserved
            else "llm-suggestion"
        )
        return _suggest_value(
            document,
            value,
            operation_id=operation_id,
            location=location,
            proposer=proposer,
            preserved=preserved,
            suggestions=suggestions,
            operation_context=operation_context,
        )
    elif kind == "object":
        fields = _fields(document, value)
        if not fields:
            sources[location] = "openapi-type"
        return {
            name: _sample(
                document,
                child,
                operation_id=operation_id,
                location=f"{location}.{name}",
                sources=sources,
                proposer=proposer,
                preserved=preserved,
                suggestions=suggestions,
                previous=previous,
                operation_context=operation_context,
            )
            for name, child, required in fields
            if required
        }
    elif kind == "boolean":
        # true/false 중 어느 쪽이 정상 흐름인지 type만으로는 알 수 없다.
        pass
    elif kind == "null":
        sources[location] = "openapi-type"
        return None

    sources[location] = "preserved-suggestion" if (operation_id, location) in preserved else "llm-suggestion"
    return _suggest_value(
        document,
        value,
        operation_id=operation_id,
        location=location,
        proposer=proposer,
        preserved=preserved,
        suggestions=suggestions,
        operation_context=operation_context,
    )


def _index(document: dict[str, Any]) -> dict[str, list[Operation]]:
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise UpstreamAmbiguity("The frozen OpenAPI document has no paths.")
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
        detail = "was not found" if not matches else "is duplicated"
        raise UpstreamAmbiguity(f"OpenAPI operationId {operation_id} {detail}.")
    operation = matches[0]
    trace = operation.value.get("x-easydep-use-case-ids")
    if not isinstance(trace, list) or not all(
        isinstance(item, str) and item.strip() for item in trace
    ):
        raise UpstreamAmbiguity(f"OpenAPI operation trace is empty: {operation_id}")
    if use_case_id not in trace:
        raise UpstreamAmbiguity(
            f"OpenAPI operation trace does not match the use case: {operation_id} -> {use_case_id}"
        )
    return operation


def _response_schema(
    document: dict[str, Any], operation: Operation, status: int | None = None
) -> dict[str, Any] | None:
    responses = operation.value.get("responses")
    if not isinstance(responses, dict):
        raise UpstreamAmbiguity(f"OpenAPI responses are empty: {operation.operation_id}")
    response: Any
    if status is None:
        candidates = [
            value
            for key, value in responses.items()
            if str(key).startswith("2") and isinstance(value, dict)
        ]
        if len(candidates) != 1:
            raise UpstreamAmbiguity(
                f"OpenAPI success response schema is ambiguous: {operation.operation_id}"
            )
        response = candidates[0]
    else:
        response = (
            responses.get(str(status))
            or responses.get(f"{str(status)[0]}XX")
            or responses.get("default")
        )
    if not isinstance(response, dict):
        raise UpstreamAmbiguity(
            f"OpenAPI success response is missing: {operation.operation_id}"
        )
    content = response.get("content")
    # 204처럼 본문이 없는 성공 응답은 schema가 없는 것이 정상이다. ``content``를
    # 선언했는데 JSON schema만 빠진 경우와 구분하여, 후자는 계속 명세 오류로 다룬다.
    if content is None:
        return None
    json_content = content.get("application/json") if isinstance(content, dict) else None
    schema = json_content.get("schema") if isinstance(json_content, dict) else None
    if not isinstance(schema, dict):
        raise UpstreamAmbiguity(
            f"OpenAPI response schema is missing: {operation.operation_id}"
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
            raise UpstreamAmbiguity("An array OpenAPI schema has no items.")
        first = value[0] if isinstance(value, list) and value else None
        return _leaves(document, items, first, path + "[]")
    return [(path.rsplit(".", 1)[-1].replace("[]", ""), kind, value, path)]


def _inputs(
    document: dict[str, Any],
    operation: Operation,
    previous: dict[tuple[str, str], list[Any]],
    *,
    proposer: InputValueProposer | None,
    preserved: dict[tuple[str, str], FunctionalInputValue],
    suggestions: list[FunctionalInputValue],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], Any, dict[str, str]]:
    """필수 입력은 예시로 채우고 유일한 이전 response leaf만 덮어쓴다."""
    path_values: dict[str, Any] = {}
    query: dict[str, Any] = {}
    headers: dict[str, str] = {}
    sources: dict[str, str] = {}
    context_parts = [
        str(operation.value.get(key) or "").strip()
        for key in ("summary", "description")
    ]
    # 업무 의미를 추론할 최소 근거만 보낸다. 유스케이스나 request body 전체는 leaf마다
    # 반복하지 않으며, 지나치게 긴 설명이 테스트 생성의 중심이 되지 않게 제한한다.
    operation_context = "\n".join(part for part in context_parts if part)[:1000]

    def checked(schema: dict[str, Any], location: str, value: Any) -> Any:
        """조립한 값도 leaf가 아닌 실제 parameter/body schema로 한 번 더 검사한다."""

        errors = _schema_errors(document, schema, value)
        if not errors:
            return value
        suggested = any(
            key == location or key.startswith(location + ".") or key.startswith(location + "[")
            for key, source in sources.items()
            if source in {"llm-suggestion", "preserved-suggestion"}
        )
        error_type = TestInputError if suggested else UpstreamAmbiguity
        raise error_type(
            f"Input {location} is invalid for {operation.operation_id}: {errors[0]}"
        )

    def transferred(name: str, schema: dict[str, Any], location: str) -> Any:
        values = previous.get((name, _type(document, schema)), [])
        if len(values) == 1:
            sources[location] = "previous-response"
            return values[0]
        return _sample(
            document,
            schema,
            operation_id=operation.operation_id,
            location=location,
            sources=sources,
            proposer=proposer,
            preserved=preserved,
            suggestions=suggestions,
            previous=previous,
            operation_context=operation_context,
        )

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
                    f"OpenAPI parameter schema is incomplete: {operation.operation_id}"
                )
            location = f"{where}.{name}"
            value = checked(schema, location, transferred(name, schema, location))
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
    sample = _sample(
        document,
        body_schema,
        operation_id=operation.operation_id,
        location="body",
        sources=sources,
        proposer=proposer,
        preserved=preserved,
        suggestions=suggestions,
        previous=previous,
        operation_context=operation_context,
    )
    return path_values, query, headers, checked(body_schema, "body", sample), sources


def _basic_auth() -> tuple[str, str]:
    """로컬 Testing 앱의 공통 Basic 테스트 계정을 모든 호출에 보낸다."""
    return os.environ.get("EASYDEP_TEST_USERNAME", "easydep-test"), os.environ.get(
        "EASYDEP_TEST_PASSWORD", "easydep-test"
    )


def _url(
    target_url: str, operation: Operation, values: dict[str, Any], query: dict[str, Any]
) -> str:
    path = operation.path
    for name, value in values.items():
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    if "{" in path or "}" in path:
        raise UpstreamAmbiguity(
            f"A required path parameter cannot be populated: {operation.path}"
        )
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
    request: dict[str, Any] | None = None,
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
        result["responseBody"] = _response_summary(body)
    if request is not None:
        result["request"] = request
    return result


def _summary_value(value: Any, *, depth: int = 0) -> Any:
    """수리 evidence에 넣을 테스트 값을 작은 JSON 형태로 만든다."""

    if depth >= 6:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(name): _summary_value(item, depth=depth + 1)
            for name, item in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple)):
        values = [_summary_value(item, depth=depth + 1) for item in value[:20]]
        if len(value) > 20:
            values.append("[TRUNCATED]")
        return values
    if isinstance(value, str):
        return value[:_SUMMARY_STRING_LIMIT] + ("…" if len(value) > _SUMMARY_STRING_LIMIT else "")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_SUMMARY_STRING_LIMIT]


def _bounded_summary(value: Any) -> Any:
    summarized = _summary_value(value)
    rendered = json.dumps(summarized, ensure_ascii=False, default=str)
    if len(rendered) <= _SUMMARY_SIZE_LIMIT:
        return summarized
    return {
        "truncated": True,
        "preview": rendered[: _SUMMARY_SIZE_LIMIT - 64] + "…",
    }


def _response_summary(body: str) -> str:
    """오류 응답도 요청 evidence와 같은 크기 제한을 적용한다."""

    try:
        return json.dumps(_bounded_summary(json.loads(body)), ensure_ascii=False)
    except (TypeError, ValueError):
        return body[:2000] + ("…" if len(body) > 2000 else "")


def _request_summary(
    operation: Operation,
    paths: dict[str, Any],
    query: dict[str, Any],
    headers: dict[str, Any],
    body: Any,
    *,
    sent: bool = True,
) -> dict[str, Any]:
    path = operation.path
    for name, value in paths.items():
        path = path.replace("{" + name + "}", quote(str(_summary_value(value)), safe=""))
    result: dict[str, Any] = {
        "method": operation.method.upper(),
        "path": path,
        "query": _bounded_summary(query),
        "body": _bounded_summary(body),
    }
    if headers:
        result["headers"] = _bounded_summary(headers)
    if not sent:
        result["sent"] = False
    return result


def execute_functional_plan(
    plan: FunctionalTestCase,
    *,
    openapi: dict[str, Any],
    target_url: str,
    timeout_seconds: float = 30.0,
    propose_input: InputValueProposer | None = None,
    preserved_inputs: Sequence[FunctionalInputValue | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """계획 순서대로 HTTP를 호출하고 첫 정확한 실패를 기록한다."""
    try:
        preserved_values = [
            value
            if isinstance(value, FunctionalInputValue)
            else FunctionalInputValue.model_validate(value)
            for value in preserved_inputs or []
        ]
    except ValueError as error:
        return {
            **_ambiguity(f"Preserved functional input is invalid: {error}"),
            "defectClass": "TEST_DEFECT",
            "failureClass": "TEST_DEFECT",
        }
    preserved = {(item.operation_id, item.location): item for item in preserved_values}
    suggestions: list[FunctionalInputValue] = []
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
            paths, query, headers, body, sources = _inputs(
                openapi,
                operation,
                previous,
                proposer=propose_input,
                preserved=preserved,
                suggestions=suggestions,
            )
            response = httpx.request(
                operation.method,
                _url(target_url, operation, paths, query),
                headers={"Accept": "application/json", **headers},
                json=body,
                auth=_basic_auth(),
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        except TestInputError as error:
            request = _request_summary(operation, {}, {}, {}, None, sent=False)
            return {
                "status": "failed",
                "gateStatus": "FAIL",
                "reason": str(error),
                "defectClass": "TEST_DEFECT",
                "failureClass": "TEST_DEFECT",
                "steps": reports,
                "inputValues": [item.model_dump(mode="json") for item in suggestions],
                "finding": _finding(
                    step.step_id,
                    step.operation_id,
                    "TEST_INPUT_INVALID",
                    str(error),
                    request=request,
                ),
            }
        except UpstreamAmbiguity as error:
            request = _request_summary(operation, {}, {}, {}, None, sent=False)
            return _ambiguity(
                str(error),
                steps=reports,
                inputValues=[item.model_dump(mode="json") for item in suggestions],
                finding=_finding(
                    step.step_id,
                    step.operation_id,
                    "UPSTREAM_AMBIGUITY",
                    str(error),
                    request=request,
                ),
            )
        except httpx.RequestError as error:
            message = f"HTTP request could not reach {operation.method} {operation.path}: {error}"
            request = _request_summary(operation, paths, query, headers, body)
            return {
                "status": "unavailable",
                "gateStatus": "INCONCLUSIVE",
                "reason": message,
                "defectClass": "ENVIRONMENT_DEFECT",
                "steps": reports,
                "inputValues": [item.model_dump(mode="json") for item in suggestions],
                "finding": _finding(
                    step.step_id,
                    step.operation_id,
                    "HTTP_TRANSPORT_ERROR",
                    message,
                    request=request,
                ),
            }
        request = _request_summary(operation, paths, query, headers, body)
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
                location for location, source in sources.items() if source != "previous-response"
            )
            # 생성값이 있다는 사실만으로 4xx를 테스트 결함으로 보면 controller가 잘못된
            # POST 400도 제품 수리를 건너뛴다. 생성한 path 식별자로 기존 resource를
            # 조회했는데 404가 난 경우만 선행 fixture 부족으로 확정할 수 있다.
            generated_path = [
                location for location in generated_inputs if location.startswith("path.")
            ]
            needs_fixture = (
                response.status_code == 404
                and operation.method == "GET"
                and bool(generated_path)
            )
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
                request=request,
            )
            if needs_fixture:
                finding["generatedInputs"] = generated_path
            return {
                "status": "failed",
                "gateStatus": "FAIL",
                "reason": finding["message"],
                # OpenAPI shape는 맞아도 실제 저장 데이터나 업무상 정상값이라는 증거는
                # 없다. 이 경우 구현을 고치게 하지 않고 테스트 입력을 다시 보게 한다.
                "defectClass": "TEST_DEFECT" if needs_fixture else "SUT_DEFECT",
                "steps": reports + [report],
                "inputValues": [item.model_dump(mode="json") for item in suggestions],
                "finding": finding,
            }
        try:
            schema = _response_schema(openapi, operation, response.status_code)
            if schema is None:
                if response.content:
                    raise ValueError(
                        "The response contains a success body that is absent from OpenAPI."
                    )
                reports.append(report)
                continue
            payload = response.json()
            errors = sorted(
                jsonschema.Draft202012Validator(_inline_refs(openapi, schema)).iter_errors(payload),
                key=str,
            )
        except UpstreamAmbiguity as error:
            return _ambiguity(
                str(error),
                steps=reports + [report],
                inputValues=[item.model_dump(mode="json") for item in suggestions],
                finding=_finding(
                    step.step_id,
                    step.operation_id,
                    "UPSTREAM_AMBIGUITY",
                    str(error),
                    request=request,
                ),
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
                request=request,
            )
            return {
                "status": "failed",
                "gateStatus": "FAIL",
                "reason": finding["message"],
                "defectClass": "SUT_DEFECT",
                "steps": reports + [report],
                "inputValues": [item.model_dump(mode="json") for item in suggestions],
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
        "inputValues": [item.model_dump(mode="json") for item in suggestions],
        "inputExampleMarker": _GENERATED,
    }


__all__ = [
    "InputValueProposer",
    "InputValueRequest",
    "TestInputError",
    "UpstreamAmbiguity",
    "execute_functional_plan",
    "operation_for_id",
]
