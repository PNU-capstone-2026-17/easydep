"""operation fragment가 후속 단계에서 실행 가능한 최소 계약인지 검사한다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.design.services.class_diagram.scenario import ScenarioIndex, UseCase, text
from app.design.services.class_diagram.type_system import (
    field_name,
    field_type,
    type_is_resolved,
)
from app.design.services.class_diagram.validation.model import class_name
from app.validation import CheckSpec, Finding, ValidationReport, run_checks


@dataclass(frozen=True)
class OperationContext:
    """한 fragment가 참조할 수 있는 명세 단계와 고정 inventory다."""

    index: ScenarioIndex
    inventory: dict[str, Any]
    use_case: UseCase
    allowed_step_ids: tuple[str, ...] = ()


def _allowed_operation_steps(context: OperationContext) -> set[str]:
    return set(context.allowed_step_ids) or {step.id for step in context.use_case.steps}


def _fragment_operations(
    fragment: dict[str, Any], inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    stereotypes = {
        class_name(item): text(item.get("stereotype"))
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    return [
        {
            **operation,
            "className": text(class_set.get("className")),
            "stereotype": stereotypes.get(text(class_set.get("className")), ""),
        }
        for class_set in fragment.get("Classes") or [] if isinstance(class_set, dict)
        for operation in class_set.get("operations") or [] if isinstance(operation, dict)
    ]


def _fragment_data_types(fragment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in fragment.get("DataTypes") or [] if isinstance(item, dict)
    ]


def _operation_data_types(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    """지역 DataType field의 이름과 타입 참조만 확인한다."""

    declared = {
        class_name(item)
        for item in context.inventory.get("Classes") or [] if isinstance(item, dict)
    } | {
        text(item.get("name"))
        for item in context.inventory.get("DataTypes") or [] if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in _fragment_data_types(fragment)
    }
    findings: list[Finding] = []
    for item in _fragment_data_types(fragment):
        name = text(item.get("name"))
        for raw_field in item.get("fields") or []:
            if not field_name(raw_field) or not type_is_resolved(
                field_type(raw_field), declared, allow_void=False,
            ):
                findings.append(Finding(
                    "class.operation.data-types",
                    f"unresolved local DataType field: {raw_field}",
                    name,
                ))
    return findings


def _operation_references(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    """operation의 단계 범위와 signature 타입 참조를 확인한다."""

    type_names = {
        class_name(item)
        for item in context.inventory.get("Classes") or [] if isinstance(item, dict)
    } | {
        text(item.get("name"))
        for item in context.inventory.get("DataTypes") or [] if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in _fragment_data_types(fragment)
    }
    allowed_steps = _allowed_operation_steps(context)
    findings: list[Finding] = []
    for operation in _fragment_operations(fragment, context.inventory):
        location = (
            f"{context.use_case.id}:{operation['className']}."
            f"{operation.get('name')}"
        )
        refs = {text(value) for value in operation.get("stepRefs") or []}
        if not refs or not refs <= allowed_steps:
            findings.append(Finding(
                "class.operation.references",
                "stepRefs must belong to this use case",
                location,
            ))
        for parameter in operation.get("parameters") or []:
            if isinstance(parameter, dict) and not type_is_resolved(
                text(parameter.get("type")), type_names, allow_void=False,
            ):
                findings.append(Finding(
                    "class.operation.references",
                    "parameter type does not resolve",
                    f"{location}#{parameter.get('name')}",
                ))
        if not type_is_resolved(
            text(operation.get("returnType")), type_names, allow_void=True,
        ):
            findings.append(Finding(
                "class.operation.references",
                "return type does not resolve",
                location,
            ))
    return findings


def _operation_coverage(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    """현재 실행 범위의 모든 명세 단계가 operation에 연결됐는지 확인한다."""

    required = _allowed_operation_steps(context)
    covered = {
        text(ref)
        for operation in _fragment_operations(fragment, context.inventory)
        for ref in operation.get("stepRefs") or []
    }
    missing = sorted(required - covered)
    return [
        Finding(
            "class.operation.coverage",
            f"operations do not cover steps: {missing}",
            context.use_case.id,
        )
    ] if missing else []


def _operation_state_ownership(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    """inventory가 직접 상태 사용을 표시한 UC만 Entity operation을 요구한다."""

    candidates = {
        class_name(item)
        for item in context.inventory.get("Classes") or []
        if isinstance(item, dict)
        and text(item.get("stereotype")) == "Entity"
        and context.use_case.id in set(item.get("useCaseIds") or [])
    }
    if not candidates:
        return []
    selected = {
        operation["className"]
        for operation in _fragment_operations(fragment, context.inventory)
        if operation["stereotype"] == "Entity"
    }
    if candidates & selected:
        return []
    return [Finding(
        "class.operation.state-ownership",
        "inventory identifies durable domain information for this use case; "
        "at least one scoped Entity operation must own its read or change",
        context.use_case.id,
    )]


OPERATION_CHECKS = (
    CheckSpec("class.operation.data-types", _operation_data_types),
    CheckSpec("class.operation.references", _operation_references),
    CheckSpec("class.operation.state-ownership", _operation_state_ownership),
    CheckSpec("class.operation.coverage", _operation_coverage),
)


def validate_operations(
    fragment: dict[str, Any], context: OperationContext,
) -> ValidationReport:
    """타입 참조와 명세 단계 coverage를 변경 없이 검사한다."""

    return run_checks(OPERATION_CHECKS, fragment or {}, context)


__all__ = ["OPERATION_CHECKS", "OperationContext", "validate_operations"]
