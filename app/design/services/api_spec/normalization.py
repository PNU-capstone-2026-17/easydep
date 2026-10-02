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
from app.design.contracts.type_system import (
    DesignTypeError,
    api_type_for_design,
    parse_type_expression,
    render_design_type,
)
from app.design.schemas.class_model import BCEModel


@dataclass(frozen=True)
class InteractionContract:
    """외부 요청을 받는 Boundary와 첫 Control 호출의 승인된 계약이다."""

    interaction_id: str
    boundary_class: str
    boundary_method: str
    boundary_return_type: str
    boundary_parameters: tuple[tuple[str, str], ...]
    boundary_parameter_stable_refs: tuple[tuple[str, str], ...]
    boundary_call_id: str
    boundary_call_stable_id: str | None
    control_class: str
    control_method: str
    control_parameters: tuple[tuple[str, str], ...]
    control_parameter_provenance: tuple[tuple[str, str, str], ...]
    control_argument_sources: tuple[tuple[str, str], ...]
    return_type: str
    use_case_ids: tuple[str, ...]
    scenario_step_refs: tuple[str, ...]


def response_projection_context(
    model: ApiSpecModel,
    bce_model: BCEModel,
) -> list[dict[str, Any]]:
    """Enumerate required response paths with existing BCE stable field refs.

    This is intentionally an internal, read-only planning context.  It neither
    changes the accepted API model nor asserts that an implementation can
    populate a field.  A later response-projection contract may use these
    finite rows, but unknown source provenance must not be inferred here.
    """

    # API schemas can be projected from Entity and DataType declarations.  Do
    # not use Boundary/Control fields as nested response schemas.
    declaration_items = [
        *(
            item
            for item in bce_model.Classes
            if item.stereotype == "Entity"
        ),
        *bce_model.DataTypes,
    ]
    declaration_sources = {
        item.class_name if hasattr(item, "class_name") else item.name: item
        for item in declaration_items
    }
    declarations: dict[str, list[tuple[str, str, str, bool]]] = {}

    def declaration_fields(owner: str) -> list[tuple[str, str, str, bool]]:
        """Validate only a declaration reached from a successful response."""

        if owner in declarations:
            return declarations[owner]
        item = declaration_sources[owner]
        fields = list(item.fields)
        refs = list(item.field_refs)
        if len(refs) != len(fields) or not all(str(ref).strip() for ref in refs):
            raise ValueError(
                f"Response projection requires aligned stable field refs for {owner}."
            )
        entries: list[tuple[str, str, str, bool]] = []
        for declaration, ref in zip(fields, refs, strict=True):
            name, separator, type_name = str(declaration).partition(":")
            if not separator or not name.strip() or not type_name.strip():
                raise ValueError(
                    f"Response projection requires a typed field declaration in {owner}."
                )
            expression = parse_type_expression(type_name)
            optional = expression.kind == "container" and expression.name == "optional"
            entries.append((name.strip(), type_name.strip(), str(ref), not optional))
        declarations[owner] = entries
        return entries

    contracts = {item.interaction_id: item for item in interaction_contracts(bce_model)}
    control_stable_ids = {
        (component.class_name, operation.name): operation.stable_id
        for component in bce_model.Classes
        for operation in component.operations
        if component.stereotype == "Control" and operation.stable_id
    }
    rows: list[dict[str, Any]] = []

    def walk(
        type_name: str,
        path_refs: tuple[str, ...],
        path_names: tuple[str, ...],
        seen: frozenset[str],
        base: dict[str, Any],
    ) -> None:
        item, _is_array = _type_parts(type_name)
        if item not in declaration_sources or item in seen:
            return
        for field_name, field_type, field_ref, required in declaration_fields(item):
            if not required:
                continue
            row = {
                **base,
                "pathFieldRefs": [*path_refs, field_ref],
                "pathNames": [*path_names, field_name],
                "declaredType": field_type,
            }
            rows.append(row)
            walk(
                field_type,
                tuple(row["pathFieldRefs"]),
                tuple(row["pathNames"]),
                seen | {item},
                base,
            )

    for endpoint in model.Endpoints:
        contract = contracts.get(endpoint.interaction_id)
        if contract is None:
            continue
        control_stable_id = control_stable_ids.get(
            (contract.control_class, contract.control_method)
        )
        for response in endpoint.responses:
            if not (200 <= response.status < 300) or not response.schema_name:
                continue
            base = {
                "interactionId": endpoint.interaction_id,
                "operationId": endpoint.operation_id,
                "status": response.status,
                "responseSchema": response.schema_name,
                "rootControlOperationStableId": control_stable_id,
            }
            if response.schema_name not in declaration_sources:
                rows.append({**base, "pathFieldRefs": [], "pathNames": [], "declaredType": response.schema_name})
                continue
            walk(response.schema_name, (), (), frozenset(), base)
    return rows


def allowed_path_parameter_names(
    contract: InteractionContract,
    bce_model: BCEModel,
) -> tuple[str, ...]:
    """Expose top-level Boundary values that fit one HTTP path segment."""

    enum_names = {
        item.name for item in bce_model.DataTypes if item.kind == "enumeration"
    }

    def path_scalar(type_name: str) -> bool:
        try:
            expression = parse_type_expression(type_name)
        except DesignTypeError:
            return False
        if expression.kind == "named":
            return expression.name in enum_names
        return expression.kind == "scalar" and expression.name not in {
            "binary",
            "object",
            "void",
        }

    return tuple(
        name
        for name, type_name in contract.boundary_parameters
        if path_scalar(type_name)
    )


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
                boundary_return_type=boundary_operation.return_type,
                boundary_parameters=tuple(
                    (parameter.name, parameter.type)
                    for parameter in boundary_operation.parameters
                ),
                boundary_parameter_stable_refs=tuple(
                    (parameter.stable_ref, parameter.name)
                    for parameter in boundary_operation.parameters
                    if parameter.stable_ref
                ),
                boundary_call_id=root.call_id,
                boundary_call_stable_id=root.stable_id,
                control_class=control_class.class_name,
                control_method=control_operation.name,
                control_parameters=tuple(
                    (parameter.name, parameter.type)
                    for parameter in control_operation.parameters
                ),
                control_parameter_provenance=tuple(
                    (parameter.name, parameter.stable_ref or "", parameter.required_value_ref or "")
                    for parameter in control_operation.parameters
                ),
                control_argument_sources=tuple(
                    (binding.parameter, binding.source_ref)
                    for binding in handoff_call.argument_bindings
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
                scenario_step_refs=tuple(
                    dict.fromkeys(
                        (
                            *(previous.scenario_step_refs if previous else ()),
                            *boundary_operation.step_refs,
                            *root.step_refs,
                        )
                    )
                ),
            )
    return tuple(contracts.values())


def interaction_context(bce_model: BCEModel) -> list[dict[str, Any]]:
    """LLM에 상호작용 ID와 그 후보에서 유효한 HTTP path 입력을 제공한다.

    ``interaction_id`` 자체에 Boundary·Control 연산과 서명이 들어 있다. path
    placeholder와 공개 반환 계약만 기계적으로 덧붙인다. LLM은 HTTP 표현만 고르고,
    입력·타입·실행 연결은 승인된 interaction에서 계속 유도한다.
    """

    return [
        {
            "interactionId": item.interaction_id,
            "useCaseIds": list(item.use_case_ids),
            "publicReturnType": item.boundary_return_type,
            "allowedPathParameters": list(
                allowed_path_parameter_names(item, bce_model)
            ),
        }
        for item in api_executable_interaction_contracts(bce_model)
    ]


def _type_parts(type_name: str) -> tuple[str, bool]:
    """Optional과 collection 껍질을 제거한 내부 타입과 배열 여부를 반환한다."""

    if re.sub(r"\s+", "", str(type_name or "")).casefold() == "date-time":
        return "date-time", False
    expression = parse_type_expression(type_name)
    if expression.kind == "container" and expression.name == "optional":
        expression = expression.arguments[0]
    if expression.kind == "container":
        return render_design_type(expression.arguments[0]), True
    return render_design_type(expression), False


def api_input_type_for_control(type_name: str) -> str:
    """Control 타입을 JSON primitive 또는 도메인 schema 이름으로 바꾼다."""

    return api_type_for_design(type_name)


def _api_contract_type_for_control(type_name: str) -> str:
    """JSON primitive로 줄이기 전에 BCE의 문자열 형식을 보존한다.

    UUID와 날짜는 JSON에서 모두 문자열이지만 아무 문자열이나 받을 수 있는 값은 아니다.
    내부 API 모델에 이 차이를 남겨야 OpenAPI가 ``format``을 기록하고 Testing도 올바른
    예시값을 만들 수 있다. 그 밖의 타입 변환은 기존 규칙을 그대로 사용한다.
    """

    return api_type_for_design(type_name)


def response_contract_for_control(return_type: str) -> tuple[str, bool]:
    """Control 반환 타입으로 성공 응답 schema와 배열 여부를 정한다."""

    item, is_array = _type_parts(return_type)
    if not item or item.casefold() == "void":
        return "", False
    return _api_contract_type_for_control(item), is_array


def _same_public_and_control_result(contract: InteractionContract) -> bool:
    """Return whether the generated controller can return the Control result directly."""

    try:
        boundary = render_design_type(parse_type_expression(contract.boundary_return_type))
        control = render_design_type(parse_type_expression(contract.return_type))
    except DesignTypeError:
        return False
    return boundary == control


def _require_executable_interaction(contract: InteractionContract) -> None:
    """Reject a BCE interaction that has no direct API/controller projection."""

    if not _same_public_and_control_result(contract):
        raise ValueError(
            "API interaction requires matching Boundary and Control return types "
            f"because no result adapter is declared: {contract.interaction_id} "
            f"(Boundary={contract.boundary_return_type}, Control={contract.return_type})"
        )


def api_executable_interaction_contracts(
    bce_model: BCEModel,
) -> tuple[InteractionContract, ...]:
    """Return only interactions whose result contract can be scaffolded directly."""

    return tuple(
        contract
        for contract in interaction_contracts(bce_model)
        if _same_public_and_control_result(contract)
    )


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
        _require_executable_interaction(contract)
        payload = endpoint.model_dump()
        payload.update(_http_inputs(payload, contract, schemas, bce_model))
        payload["operation_id"] = _unique_operation_id(
            contract.boundary_method,
            contract.boundary_class,
            used_operation_ids,
        )
        payload["responses"] = _complete_responses(
            payload.get("responses") or [],
            contract.boundary_return_type,
        )
        endpoints.append(_materialize_endpoint(payload, contracts, schemas, bce_model))
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
    bce_model: BCEModel,
) -> ApiEndpoint:
    """endpoint 하나에 코드가 소유한 실행 정보만 추가한다."""

    contract = contracts.get(str(endpoint.get("interaction_id") or ""))
    if contract is None:
        return ApiEndpoint.model_validate(endpoint)

    request_name = str(endpoint.get("request_schema") or "").strip()
    request_schema = schemas.get(request_name)
    _require_executable_interaction(contract)
    response_type, response_is_array = response_contract_for_control(
        contract.boundary_return_type
    )
    public_returns_void = _type_parts(contract.boundary_return_type)[0].casefold() == "void"
    responses = []
    void_success_added = False
    for response in endpoint.get("responses") or []:
        status = int(response.get("status", 0) or 0)
        # A void public result cannot satisfy a successful HTTP response-body
        # contract.  Keep the accepted BCE operation authoritative and
        # canonicalize an LLM-proposed 2xx response to No Content before the
        # response schema and named outcomes are derived.  Without this step
        # every API-only revision is normalized back to the same invalid
        # ``200 + empty schema`` candidate and the repair loop stalls.
        void_success = public_returns_void and 200 <= status < 300
        if void_success and void_success_added:
            continue
        normalized_void_success = void_success and status != 204
        if void_success:
            status = 204
            void_success_added = True
        nonvoid_no_content = not public_returns_void and status == 204
        if nonvoid_no_content:
            status = 200
        responses.append(
            {
                **response,
                "status": status,
                **(
                    {"description": "Completed successfully with no response body."}
                    if normalized_void_success
                    else {"description": "Successful response."}
                    if nonvoid_no_content
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
            "scenario_step_refs": list(contract.scenario_step_refs),
            "control_binding": {
                "control": contract.control_class,
                "method": contract.control_method,
                "arguments": _control_arguments(
                    endpoint, request_schema, contract, bce_model
                ),
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
            expression = parse_type_expression(type_name)
            optional = expression.kind == "container" and expression.name == "optional"
            projected.append(
                {
                    "name": name.strip(),
                    "type": _api_contract_type_for_control(type_name),
                    "required": not optional,
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
    allowed: set[str],
) -> str:
    """Resolve a path placeholder only as an exact Boundary parameter reference."""

    if name not in allowed:
        raise ValueError(
            f"path placeholder {{{name}}} must identify an allowed scalar Boundary "
            "parameter; structured and nested values require another HTTP binding"
        )
    return _api_contract_type_for_control(expected[name])


def path_placeholders(path: str) -> tuple[str, ...]:
    """Return unique placeholder names using the accepted HTTP path grammar."""

    return tuple(dict.fromkeys(re.findall(r"\{([^{}]+)\}", path)))


def _http_inputs(
    endpoint: dict[str, Any],
    contract: InteractionContract,
    schemas: dict[str, dict[str, Any]],
    bce_model: BCEModel,
) -> dict[str, Any]:
    """Control 서명과 HTTP 방식에서 path/query/body 입력을 결정한다."""

    expected = dict(contract.boundary_parameters)
    allowed = set(allowed_path_parameter_names(contract, bce_model))
    placeholders = path_placeholders(str(endpoint.get("path") or ""))
    path_params = [
        {
            "name": name,
            "type": _field_type_for_placeholder(name, expected, allowed),
            "required": True,
            "description": "",
        }
        for name in placeholders
    ]
    placeholder_names = set(placeholders)
    consumed = {
        parameter_name
        for parameter_name in expected
        if parameter_name in placeholder_names
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
    contract: InteractionContract,
    bce_model: BCEModel,
) -> list[dict[str, str]]:
    """Project accepted Boundary→Control provenance into HTTP sources.

    Parameter names and types are not semantic routing hints.  A Control input is
    connected only when the accepted collaboration explicitly points at the entry
    Boundary call and that Boundary input has an HTTP representation, or when it
    carries an already-validated required-value catalog source.
    """

    boundary_sources = _boundary_http_sources(endpoint, request_schema, contract)
    arguments: list[dict[str, str]] = []
    expected_parameters = {name for name, _type in contract.control_parameters}
    control_provenance = {
        name: (stable_ref, required_value_ref)
        for name, stable_ref, required_value_ref in contract.control_parameter_provenance
    }
    accepted_boundary_call_ids = {contract.boundary_call_id}
    if contract.boundary_call_stable_id:
        accepted_boundary_call_ids.add(contract.boundary_call_stable_id)
    supplied_parameters: set[str] = set()
    unsupported: list[str] = []
    for parameter, source_ref in contract.control_argument_sources:
        if parameter not in expected_parameters:
            continue
        supplied_parameters.add(parameter)
        if source_ref.partition("#")[0] == "value" and source_ref.partition("#")[2]:
            # Exact accepted value catalog refs remain server-owned; never turn
            # them into HTTP request fields.
            arguments.append({"name": parameter, "source": f"$context.{parameter}"})
            continue
        if source_ref.startswith("runtime#"):
            unsupported.append(f"{parameter} uses runtime source '{source_ref}'")
            continue
        source_call, separator, source_path = source_ref.partition("#")
        if not separator:
            unsupported.append(f"{parameter} has invalid source '{source_ref}'")
            continue
        if source_path.startswith("result"):
            unsupported.append(
                f"{parameter} uses earlier-result source '{source_ref}'"
            )
            continue
        if source_call not in accepted_boundary_call_ids:
            unsupported.append(
                f"{parameter} is not sourced by this Boundary call: '{source_ref}'"
            )
            continue
        boundary_parameter_ref, dot, nested_path = source_path.partition(".")
        boundary_parameter = dict(contract.boundary_parameter_stable_refs).get(
            boundary_parameter_ref,
            boundary_parameter_ref,
        )
        source = boundary_sources.get(boundary_parameter)
        if source is None:
            unsupported.append(
                f"{parameter} references Boundary value '{boundary_parameter}' "
                "without an HTTP representation"
            )
            continue
        if dot:
            field_path = _resolve_stable_field_path(
                dict(contract.boundary_parameters).get(boundary_parameter, ""),
                nested_path,
                bce_model,
            )
            if field_path is None:
                unsupported.append(
                    f"{parameter} has unresolved Boundary field source '{source_ref}'"
                )
                continue
            if source == "$body":
                source = f"$body.{field_path}"
            elif source.startswith("$body."):
                source = f"{source}.{field_path}"
            else:
                unsupported.append(
                    f"{parameter} requires nested non-body source '{source_ref}'"
                )
                continue
        arguments.append({"name": parameter, "source": source})
    missing = expected_parameters - supplied_parameters
    if missing:
        unsupported.append("missing bindings for " + ", ".join(sorted(missing)))
    if unsupported:
        raise ValueError(
            "API interaction cannot project accepted Control inputs without an "
            "explicit runtime or cross-request contract: "
            + contract.interaction_id
            + "; "
            + "; ".join(unsupported)
        )
    return [
        {
            **argument,
            "stable_ref": control_provenance.get(argument["name"], ("", ""))[0],
            "required_value_ref": control_provenance.get(argument["name"], ("", ""))[1],
        }
        for argument in arguments
    ]


def _resolve_stable_field_path(
    root_type: str,
    stable_path: str,
    bce_model: BCEModel,
) -> str | None:
    """Resolve opaque field identities through each declared structured type.

    Stable references are meaningful only within the declared parameter type.
    Every segment must match an aligned ``fieldRefs`` entry; an unresolved or
    scalar intermediate value is left unmapped for the API validator to report.
    """

    declarations = {
        item.class_name: (item.fields, item.field_refs)
        for item in bce_model.Classes
    }
    declarations.update(
        {item.name: (item.fields, item.field_refs) for item in bce_model.DataTypes}
    )
    try:
        expression = parse_type_expression(root_type)
    except DesignTypeError:
        return None
    if expression.kind == "container" and expression.name == "optional":
        expression = expression.arguments[0]
    if expression.kind != "named":
        return None

    names: list[str] = []
    owner = expression.name
    stable_refs = stable_path.split(".")
    if not stable_path or any(not ref for ref in stable_refs):
        return None
    for position, stable_ref in enumerate(stable_refs):
        declaration = declarations.get(owner)
        if declaration is None:
            return None
        fields, field_refs = declaration
        if len(fields) != len(field_refs) or stable_ref not in field_refs:
            return None
        index = field_refs.index(stable_ref)
        field_name, separator, field_type = str(fields[index]).partition(":")
        if not separator or not field_name.strip() or not field_type.strip():
            return None
        names.append(field_name.strip())
        try:
            expression = parse_type_expression(field_type.strip())
        except DesignTypeError:
            return None
        if expression.kind == "container" and expression.name == "optional":
            expression = expression.arguments[0]
        if position < len(stable_refs) - 1:
            if expression.kind != "named":
                return None
            owner = expression.name
    return ".".join(names)


def _boundary_http_sources(
    endpoint: dict[str, Any],
    request_schema: dict[str, Any] | None,
    contract: InteractionContract,
) -> dict[str, str]:
    """Return exact Boundary parameter identifiers exposed by the HTTP contract."""

    boundary_types = dict(contract.boundary_parameters)
    result: dict[str, str] = {}
    for prefix, key in (("$path.", "path_params"), ("$query.", "query_params")):
        for field in endpoint.get(key) or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            if name in boundary_types:
                result[name] = prefix + name

    request_name = str(endpoint.get("request_schema") or "").strip()
    if request_schema is None or not request_name:
        return result
    remaining = [
        (name, type_name)
        for name, type_name in contract.boundary_parameters
        if name not in result
    ]
    if len(remaining) == 1 and _type_parts(remaining[0][1])[0] == request_name:
        result[remaining[0][0]] = "$body"
        return result
    request_fields = {
        str(field.get("name") or "").strip()
        for field in request_schema.get("fields") or []
        if isinstance(field, dict) and str(field.get("name") or "").strip()
    }
    for name, _type_name in remaining:
        if name in request_fields:
            result[name] = f"$body.{name}"
    return result


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
    valid_interaction_ids = {item.interaction_id for item in contracts}
    endpoints = []
    for endpoint in model.Endpoints:
        interaction_id = endpoint.interaction_id
        if interaction_id not in valid_interaction_ids and endpoint.control_binding is not None:
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
    return ApiSpecProposal.model_validate({"Endpoints": endpoints})


__all__ = [
    "api_input_type_for_control",
    "api_executable_interaction_contracts",
    "api_spec_proposal_from_model",
    "interaction_context",
    "interaction_contracts",
    "normalize_api_spec_model",
    "response_projection_context",
    "response_contract_for_control",
]
