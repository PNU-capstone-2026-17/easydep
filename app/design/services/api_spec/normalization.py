"""작은 HTTP 제안에 승인된 클래스 실행 계약을 결합한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.design.contracts.api_spec import (
    ApiEndpoint,
    ApiSpecModel,
    ApiSpecProposal,
)
from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.type_system import types_compatible

_JSON_TYPES = {
    "byte": "integer",
    "short": "integer",
    "int": "integer",
    "integer": "integer",
    "long": "integer",
    "float": "number",
    "double": "number",
    "bigdecimal": "number",
    "number": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "string": "string",
    "str": "string",
    "char": "string",
    "character": "string",
    "uuid": "string",
    "localdate": "string",
    "localdatetime": "string",
    "instant": "string",
}
_WIRE_FORMATS = {
    "uuid": "uuid",
    "localdate": "date",
    "localdatetime": "date-time",
    "instant": "date-time",
    "offsetdatetime": "date-time",
}
_COLLECTION = re.compile(
    r"(?:java\.util\.)?(?:List|Set|Collection|Iterable|Array)<(.+)>",
    re.IGNORECASE,
)
_OPTIONAL = re.compile(r"Optional[<\[](.+)[>\]]", re.IGNORECASE)


@dataclass(frozen=True)
class InteractionContract:
    """외부 요청을 받는 Boundary와 첫 Control 호출의 승인된 계약이다."""

    interaction_id: str
    boundary_class: str
    boundary_method: str
    control_class: str
    control_method: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    use_case_ids: tuple[str, ...]


def interaction_contracts(bce_model: BCEModel) -> tuple[InteractionContract, ...]:
    """클래스 collaboration에서 API 후보인 Boundary→Control 호출을 찾는다."""

    operations = {
        operation.operation_id: (accepted_class, operation)
        for accepted_class in bce_model.Classes
        for operation in accepted_class.operations
    }
    contracts: dict[str, InteractionContract] = {}
    for collaboration in bce_model.Collaborations:
        calls = {call.call_id: call for call in collaboration.calls}
        for root in (call for call in calls.values() if call.parent_call_id is None):
            boundary = operations.get(root.receiver_operation_id)
            if boundary is None or boundary[0].stereotype != "Boundary":
                continue
            handoff_call = next(
                (
                    call
                    for call in calls.values()
                    if call.parent_call_id == root.call_id
                    and operations.get(call.receiver_operation_id) is not None
                    and operations[call.receiver_operation_id][0].stereotype == "Control"
                ),
                None,
            )
            if handoff_call is None:
                continue
            control = operations[handoff_call.receiver_operation_id]
            boundary_class, boundary_operation = boundary
            control_class, control_operation = control
            interaction_id = (
                f"{boundary_operation.operation_id} -> {control_operation.operation_id}"
            )
            previous = contracts.get(interaction_id)
            contracts[interaction_id] = InteractionContract(
                interaction_id=interaction_id,
                boundary_class=boundary_class.class_name,
                boundary_method=boundary_operation.name,
                control_class=control_class.class_name,
                control_method=control_operation.name,
                parameters=tuple(
                    (parameter.name, parameter.type) for parameter in control_operation.parameters
                ),
                return_type=control_operation.return_type,
                use_case_ids=tuple(
                    dict.fromkeys(
                        (
                            *(previous.use_case_ids if previous else ()),
                            *collaboration.use_case_ids,
                        )
                    )
                ),
            )
    return tuple(contracts.values())


def interaction_context(bce_model: BCEModel) -> list[dict[str, Any]]:
    """LLM에 선택할 상호작용 ID와 관련 유스케이스만 제공한다.

    ``interaction_id`` 자체에 Boundary·Control 연산과 서명이 들어 있다. 같은 정보를
    별도 객체로 다시 풀어 보내면 입력만 길어지고 서로 다른 값을 답할 여지도 생긴다.
    """

    return [
        {
            "interactionId": item.interaction_id,
            "useCaseIds": list(item.use_case_ids),
        }
        for item in interaction_contracts(bce_model)
    ]


def _type_parts(type_name: str) -> tuple[str, bool]:
    """Optional과 collection 껍질을 제거한 내부 타입과 배열 여부를 반환한다."""

    value = re.sub(r"\s+", "", type_name or "")
    optional = _OPTIONAL.fullmatch(value)
    if optional:
        value = optional.group(1)
    collection = _COLLECTION.fullmatch(value)
    if collection:
        return collection.group(1), True
    if value.endswith("[]"):
        return value[:-2], True
    return value, False


def api_input_type_for_control(type_name: str) -> str:
    """Control 타입을 JSON primitive 또는 도메인 schema 이름으로 바꾼다."""

    item, is_array = _type_parts(type_name)
    lowered = item.casefold()
    if lowered.startswith("java.time."):
        normalized = "string"
    else:
        normalized = _JSON_TYPES.get(lowered, item or "string")
    return f"{normalized}[]" if is_array else normalized


def _api_contract_type_for_control(type_name: str) -> str:
    """JSON primitive로 줄이기 전에 BCE의 문자열 형식을 보존한다.

    UUID와 날짜는 JSON에서 모두 문자열이지만 아무 문자열이나 받을 수 있는 값은 아니다.
    내부 API 모델에 이 차이를 남겨야 OpenAPI가 ``format``을 기록하고 Testing도 올바른
    예시값을 만들 수 있다. 그 밖의 타입 변환은 기존 규칙을 그대로 사용한다.
    """

    item, is_array = _type_parts(type_name)
    lowered = item.casefold().removeprefix("java.time.")
    normalized = _WIRE_FORMATS.get(lowered, api_input_type_for_control(item))
    return f"{normalized}[]" if is_array else normalized


def response_contract_for_control(return_type: str) -> tuple[str, bool]:
    """Control 반환 타입으로 성공 응답 schema와 배열 여부를 정한다."""

    item, is_array = _type_parts(return_type)
    if not item or item.casefold() == "void":
        return "", False
    return _api_contract_type_for_control(item), is_array


def normalize_api_spec_model(
    proposal: ApiSpecProposal,
    bce_model: BCEModel,
) -> ApiSpecModel:
    """최소 HTTP 선택과 승인된 BCE 계약을 실행 가능한 API로 결합한다.

    LLM 응답에는 클래스 모델에 이미 있는 타입과 매개변수가 없다. 이 함수가 path의
    placeholder, query/body 입력, operation ID, 성공 응답과 schema를 한 번만 계산한다.
    """

    ordered_contracts = interaction_contracts(bce_model)
    contracts = {item.interaction_id: item for item in ordered_contracts}
    schemas = _domain_schemas(bce_model)
    endpoints: list[ApiEndpoint] = []
    used_operation_ids: set[str] = set()
    for endpoint in proposal.Endpoints:
        contract = contracts.get(endpoint.interaction_id)
        if contract is None:
            continue
        payload = endpoint.model_dump()
        payload.update(_http_inputs(payload, contract, schemas))
        payload["operation_id"] = _unique_operation_id(
            contract.boundary_method,
            contract.boundary_class,
            used_operation_ids,
        )
        payload["responses"] = _complete_responses(
            payload.get("responses") or [],
            contract.return_type,
        )
        endpoints.append(_materialize_endpoint(payload, contracts, schemas))
    request_schemas = {endpoint.request_schema for endpoint in endpoints if endpoint.request_schema}
    response_schemas = {
        response.schema_name
        for endpoint in endpoints
        for response in endpoint.responses
        if response.schema_name
    }
    parameter_schemas = {
        type_name
        for endpoint in endpoints
        for parameter in (*endpoint.path_params, *endpoint.query_params)
        for type_name, _is_array in [_type_parts(parameter.type)]
        if type_name in schemas
    }
    used_schemas = _schema_dependencies(
        request_schemas | response_schemas | parameter_schemas,
        schemas,
    )
    return ApiSpecModel.model_validate(
        {
            "title": "API",
            "version": "1.0.0",
            "Endpoints": endpoints,
            "Schemas": [schema for name, schema in schemas.items() if name in used_schemas],
        }
    )


def _schema_dependencies(
    roots: set[str],
    schemas: dict[str, dict[str, Any]],
) -> set[str]:
    """요청 body가 직접 또는 필드를 통해 참조하는 schema 이름을 찾는다."""

    used = set(roots)
    pending = list(roots)
    while pending:
        schema = schemas.get(pending.pop())
        for field in schema.get("fields", []) if schema else []:
            type_name, _is_array = _type_parts(str(field.get("type") or ""))
            if type_name in schemas and type_name not in used:
                used.add(type_name)
                pending.append(type_name)
    return used


def _materialize_endpoint(
    endpoint: dict[str, Any],
    contracts: dict[str, InteractionContract],
    schemas: dict[str, dict[str, Any]],
) -> ApiEndpoint:
    """endpoint 하나에 코드가 소유한 실행 정보만 추가한다."""

    contract = contracts.get(str(endpoint.get("interaction_id") or ""))
    if contract is None:
        return ApiEndpoint.model_validate(endpoint)

    expected = dict(contract.parameters)
    request_name = str(endpoint.get("request_schema") or "").strip()
    request_schema = schemas.get(request_name)
    response_type, response_is_array = response_contract_for_control(contract.return_type)
    control_returns_void = _type_parts(contract.return_type)[0].casefold() == "void"
    responses = []
    void_success_added = False
    for response in endpoint.get("responses") or []:
        status = int(response.get("status", 0) or 0)
        # A void Control cannot satisfy a successful HTTP response-body
        # contract.  Keep the accepted BCE operation authoritative and
        # canonicalize an LLM-proposed 2xx response to No Content before the
        # response schema and named outcomes are derived.  Without this step
        # every API-only revision is normalized back to the same invalid
        # ``200 + empty schema`` candidate and the repair loop stalls.
        void_success = control_returns_void and 200 <= status < 300
        if void_success and void_success_added:
            continue
        normalized_void_success = void_success and status != 204
        if void_success:
            status = 204
            void_success_added = True
        responses.append(
            {
                **response,
                "status": status,
                **(
                    {"description": "Completed successfully with no response body."}
                    if normalized_void_success
                    else {}
                ),
                "schema_name": response_type if 200 <= status < 300 and status != 204 else "",
                "is_array": response_is_array if 200 <= status < 300 and status != 204 else False,
            }
        )
    return ApiEndpoint.model_validate(
        {
            **endpoint,
            "responses": responses,
            "source_classes": [contract.boundary_class, contract.control_class],
            "use_case_ids": list(contract.use_case_ids),
            "control_binding": {
                "control": contract.control_class,
                "method": contract.control_method,
                "arguments": _control_arguments(endpoint, request_schema, expected),
                "outcomes": [
                    {
                        "status": int(response["status"]),
                        "outcome": _outcome_name(int(response["status"])),
                    }
                    for response in responses
                    if int(response.get("status", 0) or 0) > 0
                ],
            },
        }
    )


def _domain_schemas(bce_model: BCEModel) -> dict[str, dict[str, Any]]:
    """Entity와 구조 타입의 필드 선언을 API schema로 변환한다."""

    declarations = {
        item.class_name: {"fields": item.fields, "values": []}
        for item in bce_model.Classes
        if item.stereotype == "Entity"
    }
    declarations.update(
        {item.name: {"fields": item.fields, "values": item.values} for item in bce_model.DataTypes}
    )
    schemas: dict[str, dict[str, Any]] = {}
    for owner, declaration in declarations.items():
        projected = []
        for raw_field in declaration["fields"]:
            name, separator, type_name = str(raw_field).partition(":")
            if not separator or not name.strip() or not type_name.strip():
                continue
            optional = _OPTIONAL.fullmatch(type_name.strip())
            projected.append(
                {
                    "name": name.strip(),
                    "type": _api_contract_type_for_control(
                        optional.group(1) if optional else type_name
                    ),
                    "required": optional is None,
                    "description": "",
                }
            )
        schemas[owner] = {
            "name": owner,
            "description": "",
            "fields": projected,
            "values": list(declaration["values"]),
            "source_class": owner,
        }
    return schemas


def _unique_operation_id(base: str, owner: str, used: set[str]) -> str:
    """Boundary method를 안정적인 operation ID로 쓰고 충돌할 때만 소유자를 붙인다."""

    candidate = base or "operation"
    if candidate in used:
        candidate = owner[:1].lower() + owner[1:] + base[:1].upper() + base[1:]
    suffix = 2
    unique = candidate
    while unique in used:
        unique = f"{candidate}{suffix}"
        suffix += 1
    used.add(unique)
    return unique


def _field_type_for_placeholder(
    name: str,
    expected: dict[str, str],
    schemas: dict[str, dict[str, Any]],
) -> str:
    """경로 이름과 같은 직접 parameter 또는 DTO field의 wire 타입을 찾는다."""

    direct = next(
        (type_name for key, type_name in expected.items() if key.casefold() == name.casefold()),
        None,
    )
    if direct:
        return _api_contract_type_for_control(direct)
    nested = {
        str(field.get("type") or "string")
        for type_name in expected.values()
        for schema_name, _is_array in [_type_parts(type_name)]
        for field in schemas.get(schema_name, {}).get("fields", [])
        if str(field.get("name") or "").casefold() == name.casefold()
    }
    return next(iter(nested)) if len(nested) == 1 else "string"


def _http_inputs(
    endpoint: dict[str, Any],
    contract: InteractionContract,
    schemas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Control 서명과 HTTP 방식에서 path/query/body 입력을 결정한다."""

    expected = dict(contract.parameters)
    placeholders = list(dict.fromkeys(re.findall(r"\{([^{}]+)\}", str(endpoint.get("path") or ""))))
    path_params = [
        {
            "name": name,
            "type": _field_type_for_placeholder(name, expected, schemas),
            "required": True,
            "description": "",
        }
        for name in placeholders
    ]
    placeholder_names = {name.casefold() for name in placeholders}
    consumed = {
        parameter_name
        for parameter_name in expected
        if parameter_name.casefold() in placeholder_names
    }
    remaining = [(name, type_name) for name, type_name in expected.items() if name not in consumed]
    method = str(endpoint.get("method") or "get").lower()
    if method not in {"post", "put", "patch"}:
        return {
            "path_params": path_params,
            "query_params": [
                {
                    "name": name,
                    "type": _api_contract_type_for_control(type_name),
                    "required": True,
                    "description": "",
                }
                for name, type_name in remaining
            ],
            "request_schema": "",
        }
    if not remaining:
        return {"path_params": path_params, "query_params": [], "request_schema": ""}

    if len(remaining) == 1:
        _name, type_name = remaining[0]
        schema_name, is_array = _type_parts(type_name)
        if not is_array and schema_name in schemas:
            return {
                "path_params": path_params,
                "query_params": [],
                "request_schema": schema_name,
            }

    schema_name = contract.boundary_method[:1].upper() + contract.boundary_method[1:]
    if not schema_name.endswith("Request"):
        schema_name += "Request"
    schemas[schema_name] = {
        "name": schema_name,
        "description": "",
        "fields": [
            {
                "name": name,
                "type": _api_contract_type_for_control(type_name),
                "required": True,
                "description": "",
            }
            for name, type_name in remaining
        ],
        "values": [],
        "source_class": "",
    }
    return {
        "path_params": path_params,
        "query_params": [],
        "request_schema": schema_name,
    }


def _complete_responses(
    proposed: list[dict[str, Any]],
    return_type: str,
) -> list[dict[str, Any]]:
    """LLM의 HTTP 상태 선택을 보존하되 성공 상태는 항상 하나 보장한다."""

    normalized: dict[int, dict[str, Any]] = {}
    for item in proposed:
        status = int(item.get("status", 0) or 0)
        if 100 <= status <= 599:
            normalized.setdefault(
                status,
                {
                    "status": status,
                    "description": str(item.get("description") or ""),
                },
            )
    is_void = _type_parts(return_type)[0].casefold() in {"", "void"}
    if not any(200 <= status < 300 for status in normalized):
        status = 204 if is_void else 200
        normalized[status] = {
            "status": status,
            "description": (
                "Completed successfully with no response body."
                if is_void
                else "Successful response."
            ),
        }
    return list(normalized.values())


def _control_arguments(
    endpoint: dict[str, Any],
    request_schema: dict[str, Any] | None,
    expected: dict[str, str],
) -> list[dict[str, str]]:
    """각 Control parameter에 대응하는 유일한 HTTP 입력을 고른다."""

    available: list[tuple[str, str, str]] = []
    for prefix, key in (("$path.", "path_params"), ("$query.", "query_params")):
        available.extend(
            (
                prefix + str(field.get("name") or "").strip(),
                str(field.get("name") or "").strip(),
                str(field.get("type") or "string").strip(),
            )
            for field in endpoint.get(key) or []
            if isinstance(field, dict) and str(field.get("name") or "").strip()
        )
    request_name = str(endpoint.get("request_schema") or "").strip()
    if request_schema is not None and request_name:
        available.append(("$body", "", request_name))
        available.extend(
            (
                "$body." + str(field.get("name") or "").strip(),
                str(field.get("name") or "").strip(),
                str(field.get("type") or "string").strip(),
            )
            for field in request_schema.get("fields") or []
            if isinstance(field, dict) and str(field.get("name") or "").strip()
        )

    arguments = []
    for name, expected_type in expected.items():
        compatible = [item for item in available if _input_types_compatible(item[2], expected_type)]
        exact = [item for item in compatible if item[1].casefold() == name.casefold()]
        whole_body = [item for item in compatible if item[0] == "$body"]
        selected = (
            exact[0]
            if len(exact) == 1
            else whole_body[0]
            if len(whole_body) == 1
            else compatible[0]
            if len(compatible) == 1
            else None
        )
        if selected is not None:
            arguments.append({"name": name, "source": selected[0]})
    return arguments


def _input_types_compatible(actual: str, expected: str) -> bool:
    return api_input_type_for_control(actual).casefold() == api_input_type_for_control(
        expected
    ).casefold() or types_compatible(actual, expected)


def _outcome_name(status: int) -> str:
    return {
        200: "ok",
        201: "created",
        202: "accepted",
        204: "completed",
        400: "validation_error",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
    }.get(status, "error" if status >= 400 else "ok")


def api_spec_proposal_from_model(
    model: ApiSpecModel,
    bce_model: BCEModel,
) -> ApiSpecProposal:
    """저장 모델에서 코드 생성 필드를 제외한 수정용 proposal을 만든다."""

    contracts = interaction_contracts(bce_model)
    endpoints = []
    for endpoint in model.Endpoints:
        interaction_id = endpoint.interaction_id
        if not interaction_id and endpoint.control_binding is not None:
            interaction_id = next(
                (
                    item.interaction_id
                    for item in contracts
                    if item.control_class == endpoint.control_binding.control
                    and item.control_method == endpoint.control_binding.method
                    and (
                        not endpoint.use_case_ids
                        or set(item.use_case_ids) & set(endpoint.use_case_ids)
                    )
                ),
                "",
            )
        if interaction_id:
            endpoints.append(
                {
                    "interaction_id": interaction_id,
                    "path": endpoint.path,
                    "method": endpoint.method,
                    "summary": endpoint.summary,
                    "responses": [
                        {"status": response.status, "description": response.description}
                        for response in endpoint.responses
                    ],
                }
            )
    return ApiSpecProposal.model_validate(
        {
            "Endpoints": endpoints,
        }
    )


def normalize_stored_api_spec_model(
    value: dict[str, Any] | ApiSpecModel,
    bce_model: BCEModel,
) -> ApiSpecModel:
    """과거 checkpoint의 HTTP 제안을 현재 BCE 계약으로 다시 결합한다.

    저장 모델의 Control binding은 코드가 만든 파생 정보다. schema 이름 표기나 wire 타입
    규칙이 바뀌었을 때 과거 binding을 그대로 검사하면 이미 수정된 코드에서도 앱이 계속
    막힌다. 사용자가 정한 HTTP surface만 proposal로 되돌린 뒤 현재 결정론적 규칙으로
    binding과 trace를 다시 만든다.
    """

    current = value if isinstance(value, ApiSpecModel) else ApiSpecModel.model_validate(value)
    return normalize_api_spec_model(
        api_spec_proposal_from_model(current, bce_model),
        bce_model,
    )
