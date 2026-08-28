"""정규화된 API 모델과 승인된 typed 설계 입력의 정합성을 순수 검증한다."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.models import ApiSpecModel
from app.design.services.sequence_diagram.projection import SequenceCollection


class ApiSpecValidationReport(BaseModel):
    """LLM repair를 시작하거나 넓히지 않는 기계 판독 검증 결과다."""

    valid: bool
    errors: list[str] = Field(default_factory=list)


def _sequence_control_calls(sequence_model: SequenceCollection) -> set[tuple[str, str]]:
    calls: set[tuple[str, str]] = set()
    for diagram in sequence_model.Diagrams:
        participant_classes = {
            participant.alias: participant.source_class
            for participant in diagram.Participants
            if participant.source_class
        }
        for message in diagram.Messages:
            if message.type not in {"sync", "self"}:
                continue
            owner = participant_classes.get(message.target, "")
            method = message.label.partition("(")[0].strip()
            if owner and method:
                calls.add((owner, method))
    return calls


def validate_api_spec_model(
    model: ApiSpecModel,
    bce_model: BCEModel,
    sequence_model: SequenceCollection,
) -> ApiSpecValidationReport:
    """repair 호출 없이 API·BCE·sequence의 결정론적 정합성을 검사한다.

    Args:
        model: 정규화된 API endpoint 모델이다.
        bce_model: 승인된 typed BCE 계약이다.
        sequence_model: 결정론적 typed 상호작용 투영이다.

    Returns:
        endpoint 순서의 안정적인 오류 문자열을 담은 보고서다.

    Notes:
        이 validator는 관찰만 한다. graph의 기존 semantic gate가 LLM repair 횟수와
        범위를 단독으로 소유한다.
    """

    errors: list[str] = []
    schema_names = {schema.name for schema in model.Schemas}
    operation_ids: set[str] = set()
    controls = {
        (accepted_class.class_name, operation.name): operation
        for accepted_class in bce_model.Classes
        if accepted_class.stereotype == "Control"
        for operation in accepted_class.operations
    }
    sequence_calls = _sequence_control_calls(sequence_model)

    for position, endpoint in enumerate(model.Endpoints):
        owner = endpoint.operation_id or f"endpoint[{position}]"
        braces = set(re.findall(r"\{([^{}]+)\}", endpoint.path))
        parameters = {parameter.name for parameter in endpoint.path_params}
        if braces != parameters:
            errors.append(f"{owner}: path parameters do not match path variables")
        if endpoint.operation_id:
            if endpoint.operation_id in operation_ids:
                errors.append(f"{owner}: duplicate operation_id")
            operation_ids.add(endpoint.operation_id)
        if endpoint.request_schema and endpoint.request_schema not in schema_names:
            errors.append(f"{owner}: unknown request schema {endpoint.request_schema}")
        for response in endpoint.responses:
            if (
                response.schema_name
                and response.schema_name
                not in schema_names | {"string", "integer", "number", "boolean"}
            ):
                errors.append(
                    f"{owner}: unknown response schema {response.schema_name}"
                )

        binding = endpoint.control_binding
        if binding is None:
            errors.append(f"{owner}: missing control binding")
            continue
        operation = controls.get((binding.control, binding.method))
        if operation is None:
            errors.append(
                f"{owner}: unknown Control operation {binding.control}.{binding.method}"
            )
            continue
        expected_arguments = {parameter.name for parameter in operation.parameters}
        actual_arguments = {argument.name for argument in binding.arguments}
        if expected_arguments != actual_arguments:
            errors.append(f"{owner}: control arguments do not match BCE signature")
        response_statuses = {response.status for response in endpoint.responses}
        outcome_statuses = {outcome.status for outcome in binding.outcomes}
        if response_statuses != outcome_statuses:
            errors.append(f"{owner}: control outcomes do not cover responses")
        if (binding.control, binding.method) not in sequence_calls:
            errors.append(f"{owner}: control call is absent from sequence model")

    return ApiSpecValidationReport(valid=not errors, errors=errors)
