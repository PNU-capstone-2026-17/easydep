"""실행 조각의 연산 계약과 값 흐름을 결정론적으로 검사한다."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.validation import CheckSpec, Finding, ValidationReport, run_checks
from app.design.services.class_diagram.scenario import (
    ScenarioIndex,
    UseCase,
    text,
)
from app.design.services.class_diagram.type_system import (
    field_name,
    field_type,
    referenced_type_names,
    structured_field_types,
    type_is_resolved,
    types_compatible,
)
from app.design.services.class_diagram.validation.model import (
    _structured_value_is_derivable,
    class_name,
    optional_inner_type,
    runtime_value_source,
)


@dataclass(frozen=True)
class OperationContext:
    """한 fragment 검사가 읽을 수 있는 시나리오·inventory·소유 범위다."""
    index: ScenarioIndex
    inventory: dict[str, Any]
    use_case: UseCase
    allowed_step_ids: tuple[str, ...] = ()
    execution_group_ids: tuple[str, ...] = ()


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
    inventory_names = {
        class_name(item) for item in context.inventory.get("Classes") or []
        if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in context.inventory.get("DataTypes") or []
        if isinstance(item, dict)
    }
    local_types = {
        text(item.get("name")): item for item in _fragment_data_types(fragment)
    }
    declared = inventory_names | set(local_types)
    findings: list[Finding] = []
    if inventory_names & set(local_types):
        findings.append(Finding(
            "class.operation.data-types",
            "a local DataType must not redeclare an inventory name",
            context.use_case.id,
        ))
    referenced: set[str] = set()
    for operation in _fragment_operations(fragment, context.inventory):
        referenced.update(
            name
            for parameter in operation.get("parameters") or []
            if isinstance(parameter, dict)
            for name in referenced_type_names(text(parameter.get("type")))
            if name in local_types
        )
        referenced.update(
            name for name in referenced_type_names(text(operation.get("returnType")))
            if name in local_types
        )
    pending = list(referenced)
    while pending:
        owner = pending.pop()
        item = local_types.get(owner, {})
        for raw_field in item.get("fields") or []:
            target_names = referenced_type_names(field_type(raw_field))
            for target in target_names:
                if target in local_types and target not in referenced:
                    referenced.add(target)
                    pending.append(target)
    for name, item in local_types.items():
        kind = text(item.get("kind"))
        raw_fields = list(item.get("fields") or [])
        values = list(item.get("values") or [])
        if kind == "valueObject" and (not raw_fields or values):
            findings.append(Finding(
                "class.operation.data-types",
                "a local valueObject requires typed fields and no literals",
                name,
            ))
        if kind == "enumeration" and (raw_fields or not values):
            findings.append(Finding(
                "class.operation.data-types",
                "a local enumeration requires literals and no fields",
                name,
            ))
        for raw_field in raw_fields:
            if not field_name(raw_field) or not type_is_resolved(
                field_type(raw_field), declared, allow_void=False,
            ):
                findings.append(Finding(
                    "class.operation.data-types",
                    f"unresolved local DataType field: {raw_field}",
                    name,
                ))
        if name not in referenced:
            findings.append(Finding(
                "class.operation.data-types",
                "a local DataType must be reachable from an operation signature",
                name,
            ))
    return findings


def _operation_references(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    class_names = {
        class_name(item) for item in context.inventory.get("Classes") or []
        if isinstance(item, dict)
    }
    type_names = class_names | {
        text(item.get("name")) for item in context.inventory.get("DataTypes") or []
        if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in _fragment_data_types(fragment)
    }
    allowed_steps = _allowed_operation_steps(context)
    findings: list[Finding] = []
    for operation in _fragment_operations(fragment, context.inventory):
        owner = operation["className"]
        location = f"{context.use_case.id}:{owner}.{operation.get('name')}"
        normalized_name = re.sub(
            r"[^a-z0-9]", "", text(operation.get("name")).casefold(),
        )
        if normalized_name in {"none", "noop", "notapplicable"}:
            findings.append(Finding(
                "class.operation.references",
                "operation name must describe concrete behavior",
                location,
            ))
        if owner not in class_names:
            findings.append(Finding(
                "class.operation.references", "operation owner is outside the inventory", location,
            ))
        refs = {text(value) for value in operation.get("stepRefs") or []}
        if not refs or not refs <= allowed_steps:
            findings.append(Finding(
                "class.operation.references", "stepRefs must belong to this use case", location,
            ))
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                continue
            parameter_type = text(parameter.get("type"))
            if not type_is_resolved(
                parameter_type, type_names, allow_void=False,
            ):
                findings.append(Finding(
                    "class.operation.references", "parameter type does not resolve", location,
                ))
            if (
                operation["stereotype"] == "Entity"
                and parameter_type.casefold() == owner.casefold()
            ):
                findings.append(Finding(
                    "class.operation.references",
                    "an Entity receiver must not accept its own complete Entity type; accept upstream request fields or generate owned state internally",
                    f"{location}#{parameter.get('name')}",
                ))
        if not type_is_resolved(
            text(operation.get("returnType")), type_names, allow_void=True,
        ):
            findings.append(Finding(
                "class.operation.references", "return type does not resolve", location,
            ))
    return findings


def _operation_coverage(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    operations = _fragment_operations(fragment, context.inventory)
    required = _allowed_operation_steps(context)
    covered = {
        text(ref) for operation in operations for ref in operation.get("stepRefs") or []
    }
    stereotypes = {operation["stereotype"] for operation in operations}
    findings: list[Finding] = []
    missing = sorted(required - covered)
    if missing:
        findings.append(Finding(
            "class.operation.coverage", f"operations do not cover steps: {missing}", context.use_case.id,
        ))
    if "Control" not in stereotypes:
        findings.append(Finding(
            "class.operation.coverage", "use case requires a Control operation", context.use_case.id,
        ))
    if context.use_case.primary_actor and "Boundary" not in stereotypes:
        findings.append(Finding(
            "class.operation.coverage", "actor-driven use case requires a Boundary operation", context.use_case.id,
        ))
    return findings


def _operation_groups(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    operations = _fragment_operations(fragment, context.inventory)
    selected_group_ids = set(context.execution_group_ids)
    groups = [
        group for group in context.index.groups
        if group.use_case_id == context.use_case.id
        and (not selected_group_ids or group.id in selected_group_ids)
    ]
    findings: list[Finding] = []
    actor_entries = {group.actor_step for group in groups if group.actor_step}
    for operation in operations:
        if operation["stereotype"] == "Boundary":
            continue
        delegated_actor_steps = (
            set(operation.get("stepRefs") or []) & actor_entries
        )
        if delegated_actor_steps:
            findings.append(Finding(
                "class.operation.execution-groups",
                "only Boundary may own an actor entry step; delegated operations must trace the system behavior they perform",
                f"{context.use_case.id}:{operation['className']}.{operation.get('name')}",
            ))
    for group in groups:
        refs = set(group.step_ids)
        if group.actor_step:
            owners = [
                operation for operation in operations
                if operation["stereotype"] == "Boundary"
                and group.actor_step in set(operation.get("stepRefs") or [])
            ]
            if len(owners) != 1:
                findings.append(Finding(
                    "class.operation.execution-groups",
                    "each actor entry must be owned by exactly one Boundary operation",
                    group.id,
                ))
        if not any(
            operation["stereotype"] == "Control"
            and refs & set(operation.get("stepRefs") or [])
            for operation in operations
        ):
            findings.append(Finding(
                "class.operation.execution-groups",
                "each execution group requires an in-group Control operation",
                group.id,
            ))
    for operation in operations:
        if len(set(operation.get("stepRefs") or []) & actor_entries) > 1:
            findings.append(Finding(
                "class.operation.execution-groups",
                "one Boundary operation cannot merge separate actor entries",
                f"{context.use_case.id}:{operation['className']}.{operation.get('name')}",
            ))
    for group in groups:
        refs = set(group.step_ids)
        by_control: dict[str, list[str]] = {}
        for operation in operations:
            if operation["stereotype"] != "Control" or not (
                refs & set(operation.get("stepRefs") or [])
            ):
                continue
            by_control.setdefault(operation["className"], []).append(
                text(operation.get("name"))
            )
        for owner, names in by_control.items():
            if len(names) > 1:
                findings.append(Finding(
                    "class.operation.execution-groups",
                    "one Control class exposes one cohesive orchestration operation per execution group",
                    f"{group.id}:{owner}",
                ))
    scoped_entities = {
        class_name(item)
        for item in context.inventory.get("Classes") or []
        if isinstance(item, dict)
        and text(item.get("stereotype")) == "Entity"
        and context.use_case.id in set(item.get("useCaseIds") or [])
    }
    used_entities = {
        operation["className"]
        for operation in operations if operation["stereotype"] == "Entity"
    }
    if scoped_entities and not used_entities:
        findings.append(Finding(
            "class.operation.execution-groups",
            "a use case with persistent-state candidates requires an Entity operation",
            context.use_case.id,
        ))
    return findings


def _operation_results(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    actor = context.use_case.primary_actor.casefold()
    by_id = {step.id: step for step in context.use_case.steps}
    findings: list[Finding] = []
    for operation in _fragment_operations(fragment, context.inventory):
        if operation["stereotype"] != "Boundary":
            continue
        steps = [by_id[ref] for ref in operation.get("stepRefs") or [] if ref in by_id]
        covers_entry = any(step.subject.casefold() == actor for step in steps if actor)
        covers_output = any(step.subject.casefold() not in {"", actor} for step in steps)
        if covers_entry and covers_output and text(operation.get("returnType")).casefold() == "void":
            findings.append(Finding(
                "class.operation.results",
                "an actor-facing operation that covers an output step requires a result type",
                f"{context.use_case.id}:{operation['className']}.{operation.get('name')}",
            ))
    return findings


def _operation_value_flow(
    fragment: dict[str, Any], context: OperationContext,
) -> list[Finding]:
    operations = _fragment_operations(fragment, context.inventory)
    type_model = {
        "Classes": context.inventory.get("Classes") or [],
        "DataTypes": [
            *(context.inventory.get("DataTypes") or []),
            *(_fragment_data_types(fragment)),
        ],
    }
    fields_by_type = structured_field_types(type_model)
    step_order = {step.id: step.order for step in context.use_case.steps}

    def parameter_source_types(stereotypes: set[str]) -> set[str]:
        result: set[str] = set()
        for operation in operations:
            if operation["stereotype"] not in stereotypes:
                continue
            for parameter in operation.get("parameters") or []:
                if not isinstance(parameter, dict):
                    continue
                source_type = text(parameter.get("type"))
                result.add(source_type)
                result.update(fields_by_type.get(source_type, {}).values())
        return result

    def named_parameter_sources(stereotypes: set[str]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for producer in operations:
            if producer["stereotype"] not in stereotypes:
                continue
            for parameter in producer.get("parameters") or []:
                if not isinstance(parameter, dict):
                    continue
                source_type = text(parameter.get("type"))
                result.setdefault(text(parameter.get("name")).casefold(), set()).add(
                    source_type
                )
                for name, field_source_type in fields_by_type.get(source_type, {}).items():
                    result.setdefault(name.casefold(), set()).add(field_source_type)
        return result

    boundary_sources = parameter_source_types({"Boundary"})
    control_sources = parameter_source_types({"Boundary", "Control"})
    boundary_named = named_parameter_sources({"Boundary"})
    control_named = named_parameter_sources({"Boundary", "Control"})
    findings: list[Finding] = []
    for operation in operations:
        stereotype = operation["stereotype"]
        if stereotype == "Boundary":
            continue
        available = set(boundary_sources if stereotype == "Control" else control_sources)
        available_named = {
            name: set(values)
            for name, values in (
                boundary_named if stereotype == "Control" else control_named
            ).items()
        }
        current_positions = [
            step_order[ref] for ref in operation.get("stepRefs") or [] if ref in step_order
        ]
        if current_positions:
            current_start = min(current_positions)
            for producer in operations:
                producer_positions = [
                    step_order[ref]
                    for ref in producer.get("stepRefs") or [] if ref in step_order
                ]
                return_type = text(producer.get("returnType"))
                if (
                    producer is not operation
                    and producer_positions
                    and max(producer_positions) <= current_start
                    and return_type.casefold() != "void"
                ):
                    available.add(return_type)
                    if optional_inner_type(return_type):
                        available.add(optional_inner_type(return_type))
                    available.update(fields_by_type.get(return_type, {}).values())
                    for name, source_type in fields_by_type.get(return_type, {}).items():
                        available_named.setdefault(name.casefold(), set()).add(source_type)
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                continue
            expected = text(parameter.get("type"))
            if runtime_value_source(expected):
                continue
            if not any(types_compatible(source, expected) for source in available):
                if _structured_value_is_derivable(
                    expected, available_named, fields_by_type,
                ):
                    continue
                candidates = ", ".join(sorted(available)) or "none"
                findings.append(Finding(
                    "class.operation.value-flow",
                    (
                        "downstream parameter has no compatible upstream value source; "
                        f"expected {expected}; available upstream types: {candidates}. "
                        "Reuse an available source type, consume an earlier result, or "
                        "remove a parameter that the receiving operation can generate "
                        "internally; do not invent a new caller input."
                    ),
                    (
                        f"{context.use_case.id}:{operation['className']}."
                        f"{operation.get('name')}#{parameter.get('name')}"
                    ),
                ))
    return findings


# 선언 해소와 coverage를 먼저 확인한 뒤 결과/값 흐름처럼 앞선 구조가 필요한 규칙을
# 실행한다. finding 순서는 repair prompt와 테스트에서 안정적인 계약이다.
OPERATION_CHECKS = (
    CheckSpec("class.operation.data-types", _operation_data_types),
    CheckSpec("class.operation.references", _operation_references),
    CheckSpec("class.operation.coverage", _operation_coverage),
    CheckSpec("class.operation.execution-groups", _operation_groups),
    CheckSpec("class.operation.results", _operation_results),
    CheckSpec("class.operation.value-flow", _operation_value_flow),
)


def validate_operations(
    fragment: dict[str, Any], context: OperationContext
) -> ValidationReport:
    """실행 조각의 연산과 값 흐름을 변경 없이 검사한다.

    Args:
        fragment: 한 유스케이스 또는 실행 그룹이 소유한 연산 후보다.
        context: 허용 단계, 예약 연산과 타입을 고정한 검사 문맥이다.

    Returns:
        참조, 커버리지, 결과와 provenance finding을 담은 보고서다.

    Notes:
        service는 이 보고서를 영어 finding 문장으로 직렬화해 같은 유스케이스의 전체
        fragment replacement에만 전달한다. validator는 후보를 고치지 않는다.
    """
    return run_checks(OPERATION_CHECKS, fragment or {}, context)


__all__ = ["OPERATION_CHECKS", "OperationContext", "validate_operations"]
