"""승인된 BCE 계약을 기준으로 API 제안을 결정론적으로 정규화한다."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.models import ApiSpecModel

ControlParameters = dict[tuple[str, str], dict[str, str]]
ControlReturns = dict[tuple[str, str], str]


def control_contracts(
    bce_model: BCEModel,
) -> tuple[ControlParameters, ControlReturns]:
    """승인 BCE 모델의 정확한 parameter·응답 계약을 색인한다."""

    parameters: ControlParameters = {}
    returns: ControlReturns = {}
    for accepted_class in bce_model.Classes:
        if accepted_class.stereotype != "Control":
            continue
        for operation in accepted_class.operations:
            key = (accepted_class.class_name, operation.name)
            parameters[key] = {
                parameter.name: parameter.type for parameter in operation.parameters
            }
            returns[key] = operation.return_type
    return parameters, returns


def control_contracts_from_payload(
    class_model: Any,
) -> tuple[ControlParameters, ControlReturns] | None:
    """이전 호출자가 넘긴 느슨한 typed payload에서 기존 방식으로 계약을 읽는다."""

    payload = (
        class_model.model_dump(by_alias=True)
        if isinstance(class_model, BaseModel)
        else class_model
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("Classes"), list):
        return None
    parameters: ControlParameters = {}
    returns: ControlReturns = {}
    for class_item in payload["Classes"]:
        if not isinstance(class_item, dict):
            continue
        if str(class_item.get("stereotype") or "").strip().casefold() != "control":
            continue
        class_name = str(class_item.get("className") or "").strip()
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            method_name = str(operation.get("name") or "").strip()
            if not class_name or not method_name:
                continue
            operation_parameters: dict[str, str] = {}
            for parameter in operation.get("parameters") or []:
                if not isinstance(parameter, dict):
                    continue
                name = str(parameter.get("name") or "").strip()
                type_name = str(parameter.get("type") or "").strip()
                if name and type_name:
                    operation_parameters[name] = type_name
            parameters[(class_name, method_name)] = operation_parameters
            return_type = str(operation.get("returnType") or "").strip()
            if return_type:
                returns[(class_name, method_name)] = return_type
    return parameters, returns


def response_contract_for_control(return_type: str) -> tuple[str, bool]:
    """BCE 반환 타입 하나를 추론 없이 HTTP 응답 shape로 바꾼다."""

    normalized = re.sub(r"\s+", "", return_type or "")
    collection = re.fullmatch(
        r"(?:java\.util\.)?(?:List|Set|Collection|Iterable|Array)<(.+)>",
        normalized,
        re.IGNORECASE,
    )
    item = collection.group(1) if collection else normalized
    primitive = {
        "string": "string",
        "str": "string",
        "boolean": "boolean",
        "bool": "boolean",
        "int": "integer",
        "integer": "integer",
        "long": "integer",
        "short": "integer",
        "float": "number",
        "double": "number",
        "bigdecimal": "number",
        "number": "number",
    }.get(item.lower())
    if not item or item.lower() == "void":
        return "", False
    return primitive or item, collection is not None


def api_field_type_for_control(type_name: str) -> str:
    """BCE scalar 또는 collection을 JSON wire 타입으로 투영한다."""

    token = re.sub(r"\s+", "", type_name).lower()
    if re.match(
        r"(?:java\.util\.)?(?:list|set|collection|iterable|array)<.+>", token,
    ):
        return "array"
    if token.endswith("[]"):
        return "array"
    if token in {"byte", "short", "int", "integer", "long"}:
        return "integer"
    if token in {"float", "double", "bigdecimal", "number"}:
        return "number"
    if token in {"boolean", "bool"}:
        return "boolean"
    return "string"


def api_query_type_for_control(type_name: str) -> str:
    """이름 있는 aggregate query 계약을 string으로 뭉개지 않고 유지한다."""

    normalized = re.sub(r"\s+", "", type_name).lower()
    scalar = api_field_type_for_control(type_name)
    if scalar != "string" or normalized in {
        "",
        "string",
        "str",
        "char",
        "character",
        "uuid",
        "localdate",
        "localdatetime",
        "instant",
    } or normalized.startswith("java.time."):
        return scalar
    return str(type_name).strip() or scalar


def normalize_api_spec_payload(
    model: dict[str, Any],
    control_parameters: ControlParameters,
    control_returns: ControlReturns,
) -> dict[str, Any]:
    """API 동작을 추가하지 않고 기계적 누락만 채운다.

    입력 dict는 의도적으로 제자리에서 갱신해 체크포인트와 피드백 호출자가 사용하던
    이전 정규화 계약을 유지한다.
    """

    for endpoint in model.get("Endpoints", []) or []:
        if not isinstance(endpoint, dict):
            continue
        binding = endpoint.get("control_binding")
        if not isinstance(binding, dict):
            continue

        control = str(binding.get("control") or "").strip()
        method = str(binding.get("method") or "").strip()
        if "." in control:
            candidate_control, candidate_method = control.rsplit(".", 1)
            if (candidate_control, candidate_method) in control_parameters:
                control = candidate_control
                method = candidate_method
                binding["control"] = control
                binding["method"] = method

        expected_parameters = control_parameters.get((control, method), {})
        response_schema, response_is_array = response_contract_for_control(
            control_returns.get((control, method), "")
        )
        source_classes = endpoint.setdefault("source_classes", [])
        if control and isinstance(source_classes, list) and control not in source_classes:
            source_classes.append(control)

        query_params = endpoint.get("query_params")
        if not isinstance(query_params, list):
            query_params = []
            endpoint["query_params"] = query_params
        known_query_params = {
            str(item.get("name") or "").strip()
            for item in query_params
            if isinstance(item, dict)
        }
        for argument in binding.get("arguments", []) or []:
            if not isinstance(argument, dict):
                continue
            source = str(argument.get("source") or "").strip()
            if not source.startswith("$query."):
                continue
            query_name = source.removeprefix("$query.").strip()
            if not query_name or query_name in known_query_params:
                continue
            parameter_name = str(argument.get("name") or "").strip()
            query_params.append(
                {
                    "name": query_name,
                    "type": api_query_type_for_control(
                        expected_parameters.get(parameter_name, "String")
                    ),
                    "required": True,
                    "description": "",
                }
            )
            known_query_params.add(query_name)

        request_schema = str(endpoint.get("request_schema") or "").strip()
        if request_schema:
            schema = next(
                (
                    item
                    for item in model.get("Schemas", []) or []
                    if isinstance(item, dict) and item.get("name") == request_schema
                ),
                None,
            )
            if isinstance(schema, dict):
                fields = schema.setdefault("fields", [])
                known = {
                    str(item.get("name") or "").strip()
                    for item in fields
                    if isinstance(item, dict)
                }
                for argument in binding.get("arguments", []) or []:
                    if not isinstance(argument, dict):
                        continue
                    source = str(argument.get("source") or "")
                    name = source.removeprefix("$body.").strip()
                    if not name or name == source or name in known:
                        if name and name in known and source.startswith("$body."):
                            expected = expected_parameters.get(
                                str(argument.get("name") or "").strip()
                            )
                            if expected:
                                for field in fields:
                                    if (
                                        isinstance(field, dict)
                                        and field.get("name") == name
                                    ):
                                        field["type"] = api_field_type_for_control(expected)
                        continue
                    fields.append(
                        {
                            "name": name,
                            "type": "string",
                            "required": True,
                            "description": "",
                        }
                    )
                    known.add(name)

        for response in endpoint.get("responses", []) or []:
            if not isinstance(response, dict):
                continue
            status = int(response.get("status", 0) or 0)
            if not (200 <= status < 300) or status == 204 or not response_schema:
                continue
            current_schema = str(response.get("schema_name") or "").strip()
            primitive_schema = response_schema in {
                "string",
                "integer",
                "number",
                "boolean",
            }
            if not current_schema or primitive_schema:
                response["schema_name"] = response_schema
                response["is_array"] = response_is_array
    return model


def normalize_api_spec_model(
    proposal: ApiSpecModel,
    bce_model: BCEModel,
) -> ApiSpecModel:
    """typed API 제안을 BCE 계약으로 정규화한다.

    Args:
        proposal: structured LLM 경계가 schema 검증한 API 제안이다.
        bce_model: class·operation 계약이 승인된 BCE 모델이다.

    Returns:
        기계적 누락과 wire 타입이 정규화된 typed API 모델이다.

    Notes:
        새 endpoint나 binding을 만들지 않으며 입력 제안을 직접 변경하지 않는다.
    """

    parameters, returns = control_contracts(bce_model)
    payload = proposal.model_dump()
    return ApiSpecModel.model_validate(
        normalize_api_spec_payload(payload, parameters, returns)
    )
