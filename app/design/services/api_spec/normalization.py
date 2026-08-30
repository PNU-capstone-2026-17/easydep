"""작은 HTTP 제안에 승인된 클래스 실행 계약을 결합한다."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.models import (
    ApiEndpoint,
    ApiSpecModel,
    ApiSpecProposal,
)
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
                    (parameter.name, parameter.type)
                    for parameter in control_operation.parameters
                ),
                return_type=control_operation.return_type,
                use_case_ids=tuple(dict.fromkeys((
                    *(previous.use_case_ids if previous else ()),
                    *collaboration.use_case_ids,
                ))),
            )
    return tuple(contracts.values())


def interaction_context(bce_model: BCEModel) -> list[dict[str, Any]]:
    """LLM에 HTTP 판단에 필요한 유한한 후보만 제공한다."""

    return [
        {
            "interactionId": item.interaction_id,
            "boundary": {
                "class": item.boundary_class,
                "method": item.boundary_method,
            },
            "control": {
                "class": item.control_class,
                "method": item.control_method,
                "parameters": [
                    {"name": name, "type": type_name}
                    for name, type_name in item.parameters
                ],
                "returnType": item.return_type,
            },
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
    if is_array:
        return "array"
    lowered = item.casefold()
    if lowered.startswith("java.time."):
        return "string"
    return _JSON_TYPES.get(lowered, item or "string")


def response_contract_for_control(return_type: str) -> tuple[str, bool]:
    """Control 반환 타입으로 성공 응답 schema와 배열 여부를 정한다."""

    item, is_array = _type_parts(return_type)
    if not item or item.casefold() == "void":
        return "", False
    return api_input_type_for_control(item), is_array


def normalize_api_spec_model(
    proposal: ApiSpecProposal,
    bce_model: BCEModel,
) -> ApiSpecModel:
    """LLM의 HTTP 제안과 승인된 Boundary→Control 계약을 결합한다."""

    contracts = {item.interaction_id: item for item in interaction_contracts(bce_model)}
    schemas = {
        schema.name: schema.model_dump()
        for schema in proposal.Schemas
        if schema.name.strip()
    }
    domain_schemas = _domain_schemas(bce_model)
    for name, schema in domain_schemas.items():
        current = schemas.setdefault(name, schema)
        current["fields"] = schema["fields"]
        current["source_class"] = name
    source_types = set(domain_schemas)
    for name, schema in schemas.items():
        schema["source_class"] = name if name in source_types else ""

    endpoints = [
        _materialize_endpoint(endpoint.model_dump(), contracts, schemas)
        for endpoint in proposal.Endpoints
    ]
    request_schemas = {
        endpoint.request_schema for endpoint in endpoints if endpoint.request_schema
    }
    used_schemas = set(domain_schemas) | _schema_dependencies(request_schemas, schemas)
    return ApiSpecModel.model_validate({
        "title": proposal.title,
        "version": proposal.version,
        "Endpoints": endpoints,
        "Schemas": [
            schema for name, schema in schemas.items() if name in used_schemas
        ],
    })


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
    if request_name and request_schema is None:
        request_schema = {
            "name": request_name,
            "description": "",
            "fields": [
                {
                    "name": name,
                    "type": api_input_type_for_control(type_name),
                    "required": True,
                    "description": "",
                }
                for name, type_name in expected.items()
            ],
            "source_class": "",
        }
        schemas[request_name] = request_schema
    _align_request_types(endpoint, request_schema, expected)
    response_type, response_is_array = response_contract_for_control(
        contract.return_type
    )
    responses = []
    for response in endpoint.get("responses") or []:
        status = int(response.get("status", 0) or 0)
        responses.append({
            **response,
            "schema_name": response_type if 200 <= status < 300 and status != 204 else "",
            "is_array": response_is_array if 200 <= status < 300 and status != 204 else False,
        })
    return ApiEndpoint.model_validate({
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
    })


def _domain_schemas(bce_model: BCEModel) -> dict[str, dict[str, Any]]:
    """Entity와 구조 타입의 필드 선언을 API schema로 변환한다."""

    declarations = {
        item.class_name: item.fields
        for item in bce_model.Classes
        if item.stereotype == "Entity"
    }
    declarations.update({item.name: item.fields for item in bce_model.DataTypes})
    schemas: dict[str, dict[str, Any]] = {}
    for owner, fields in declarations.items():
        projected = []
        for declaration in fields:
            name, separator, type_name = str(declaration).partition(":")
            if not separator or not name.strip() or not type_name.strip():
                continue
            optional = _OPTIONAL.fullmatch(type_name.strip())
            projected.append({
                "name": name.strip(),
                "type": api_input_type_for_control(
                    optional.group(1) if optional else type_name
                ),
                "required": optional is None,
                "description": "",
            })
        schemas[owner] = {
            "name": owner,
            "description": "",
            "fields": projected,
            "source_class": owner,
        }
    return schemas


def _align_request_types(
    endpoint: dict[str, Any],
    request_schema: dict[str, Any] | None,
    expected: dict[str, str],
) -> None:
    """같은 이름의 HTTP 입력을 Control parameter 타입에 맞춘다."""

    fields = [
        field
        for key in ("path_params", "query_params")
        for field in endpoint.get(key) or []
        if isinstance(field, dict)
    ]
    if request_schema is not None:
        fields.extend(
            field
            for field in request_schema.get("fields") or []
            if isinstance(field, dict)
        )
    for field in fields:
        name = str(field.get("name") or "").strip()
        if name in expected:
            field["type"] = api_input_type_for_control(expected[name])


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
        compatible = [
            item for item in available
            if _input_types_compatible(item[2], expected_type)
        ]
        exact = [item for item in compatible if item[1].casefold() == name.casefold()]
        whole_body = [item for item in compatible if item[0] == "$body"]
        selected = (
            exact[0] if len(exact) == 1
            else whole_body[0] if len(whole_body) == 1
            else compatible[0] if len(compatible) == 1
            else None
        )
        if selected is not None:
            arguments.append({"name": name, "source": selected[0]})
    return arguments


def _input_types_compatible(actual: str, expected: str) -> bool:
    return (
        api_input_type_for_control(actual).casefold()
        == api_input_type_for_control(expected).casefold()
        or types_compatible(actual, expected)
    )


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
    request_schema_names = {
        endpoint.request_schema for endpoint in model.Endpoints if endpoint.request_schema
    }
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
            endpoints.append({
                "interaction_id": interaction_id,
                "path": endpoint.path,
                "method": endpoint.method,
                "summary": endpoint.summary,
                "operation_id": endpoint.operation_id,
                "path_params": endpoint.path_params,
                "query_params": endpoint.query_params,
                "request_schema": endpoint.request_schema,
                "responses": [
                    {"status": response.status, "description": response.description}
                    for response in endpoint.responses
                ],
            })
    return ApiSpecProposal.model_validate({
        "title": model.title,
        "version": model.version,
        "Endpoints": endpoints,
        "Schemas": [
            {
                "name": schema.name,
                "description": schema.description,
                "fields": schema.fields,
            }
            for schema in model.Schemas
            if schema.name in request_schema_names and not schema.source_class
        ],
    })
