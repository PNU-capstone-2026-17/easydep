"""유스케이스 전체의 여러 actor root와 parameter provenance를 검사한다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    text,
)
from app.design.services.class_diagram.type_system import (
    projected_field_type,
    structured_field_types,
    types_compatible,
)
from app.design.services.class_diagram.validation.model import (
    derived_value_parts,
    operation_catalog,
    optional_inner_type,
    runtime_value_source,
    type_can_default,
)
from app.validation import CheckSpec, Finding, ValidationReport, run_checks


@dataclass(frozen=True)
class CollaborationContext:
    """협업 검사를 한 유스케이스와 수락 model에 고정한다."""

    index: ScenarioIndex
    model: dict[str, Any]
    use_case: UseCase

    @property
    def groups(self) -> tuple[ExecutionGroup, ...]:
        """actor entry별 단계 범위는 기존 ScenarioIndex 계산을 재사용한다."""

        return tuple(
            group for group in self.index.groups
            if group.use_case_id == self.use_case.id
        )


def _root_positions(calls: list[dict[str, Any]]) -> list[int]:
    return [
        position for position, call in enumerate(calls)
        if not text(call.get("parentCallId"))
    ]


def _root_index(calls: list[dict[str, Any]], position: int) -> int | None:
    by_id = {text(call.get("callId")): index for index, call in enumerate(calls)}
    current = position
    visited: set[int] = set()
    while current not in visited:
        visited.add(current)
        parent_id = text(calls[current].get("parentCallId"))
        if not parent_id:
            return current
        parent = by_id.get(parent_id)
        if parent is None or parent >= current:
            return None
        current = parent
    return None


def _collaboration_contract(
    collaboration: dict[str, Any], context: CollaborationContext,
) -> list[Finding]:
    """참조, root 순서, 단계 범위와 최소 BCE 호출 방향만 검사한다."""

    operations = operation_catalog(context.model)
    calls = [item for item in collaboration.get("calls") or [] if isinstance(item, dict)]
    findings: list[Finding] = []
    location = context.use_case.id
    if text(collaboration.get("collaborationId")) != context.use_case.id:
        findings.append(Finding(
            "class.collaboration.contract",
            "collaborationId does not match its use case",
            location,
        ))
    groups = context.groups
    roots = _root_positions(calls)
    if len(roots) != len(groups):
        findings.append(Finding(
            "class.collaboration.contract",
            "root calls must match actor entry groups in scenario order",
            location,
        ))
    call_by_id = {text(call.get("callId")): call for call in calls}
    covered_by_group: list[set[str]] = [set() for _group in groups]
    root_ordinal = {position: ordinal for ordinal, position in enumerate(roots)}
    control_by_group = [False for _group in groups]
    for position, call in enumerate(calls):
        call_id = text(call.get("callId"))
        operation = operations.get(text(call.get("receiverOperationId")))
        if operation is None:
            findings.append(Finding(
                "class.collaboration.contract", "call receiver operation does not exist", call_id,
            ))
            continue
        parent_id = text(call.get("parentCallId"))
        if parent_id and parent_id not in {
            text(previous.get("callId")) for previous in calls[:position]
        }:
            findings.append(Finding(
                "class.collaboration.contract", "parentCallId must reference an earlier call", call_id,
            ))
        root_position = _root_index(calls, position)
        ordinal = root_ordinal.get(root_position, -1) if root_position is not None else -1
        if ordinal < 0 or ordinal >= len(groups):
            continue
        group = groups[ordinal]
        latest_root = max((root for root in roots if root <= position), default=-1)
        if root_position != latest_root:
            findings.append(Finding(
                "class.collaboration.contract",
                "a call cannot return to an earlier actor root",
                call_id,
            ))
        declared = {text(ref) for ref in operation.get("stepRefs") or []}
        refs = {text(ref) for ref in call.get("stepRefs") or []}
        if not refs or not refs <= declared or not refs <= set(group.required_step_ids):
            findings.append(Finding(
                "class.collaboration.contract", "call stepRefs are outside its actor entry scope", call_id,
            ))
        covered_by_group[ordinal].update(refs)
        expected = {
            text(parameter.get("name")) for parameter in operation.get("parameters") or []
            if isinstance(parameter, dict)
        }
        bound = {
            text(binding.get("parameter")) for binding in call.get("argumentBindings") or []
            if isinstance(binding, dict)
        }
        if bound != expected:
            findings.append(Finding(
                "class.collaboration.contract", "argumentBindings must match receiver parameters", call_id,
            ))
        if position == root_position:
            if text(operation.get("stereotype")) != "boundary":
                findings.append(Finding(
                    "class.collaboration.contract", "actor entry must start at Boundary", call_id,
                ))
            if group.actor_step and group.actor_step not in refs:
                findings.append(Finding(
                    "class.collaboration.contract", "root call must cover its actor entry step", call_id,
                ))
        if text(operation.get("stereotype")) == "control":
            control_by_group[ordinal] = True
        parent = call_by_id.get(parent_id)
        if parent:
            parent_operation = operations.get(text(parent.get("receiverOperationId")), {})
            source = text(parent_operation.get("stereotype"))
            target = text(operation.get("stereotype"))
            if (
                (source == "boundary" and target != "control")
                or (target == "entity" and source != "control")
                or source == "entity"
            ):
                findings.append(Finding(
                    "class.collaboration.contract",
                    "call tree must follow Boundary to Control to Entity responsibilities",
                    call_id,
                ))
    for ordinal, group in enumerate(groups):
        if set(group.required_step_ids) - covered_by_group[ordinal]:
            findings.append(Finding(
                "class.collaboration.contract",
                "actor entry root does not cover every required step",
                group.id,
            ))
        if not control_by_group[ordinal]:
            findings.append(Finding(
                "class.collaboration.contract",
                "Boundary must delegate each actor entry flow to Control",
                group.id,
            ))
    return findings


def _source_type(
    source_ref: str,
    previous_calls: list[dict[str, Any]],
    operations: dict[str, dict[str, Any]],
    fields_by_type: dict[str, dict[str, str]],
) -> str:
    derived_type, field_sources = derived_value_parts(source_ref)
    if derived_type:
        target_fields = fields_by_type.get(derived_type, {})
        if not target_fields or not set(field_sources) <= set(target_fields):
            return ""
        for name, expected in target_fields.items():
            nested = field_sources.get(name, "")
            if not nested:
                if type_can_default(expected):
                    continue
                return ""
            if nested == runtime_value_source(expected):
                continue
            actual = _source_type(nested, previous_calls, operations, fields_by_type)
            if not actual or not types_compatible(actual, expected):
                return ""
        return derived_type
    source_id, separator, path = source_ref.partition("#")
    if not separator:
        return ""
    if ":precondition:" in source_id:
        return "__precondition__"
    source_call = next(
        (call for call in previous_calls if text(call.get("callId")) == source_id), None,
    )
    if source_call is None:
        return "__entry__"
    operation = operations.get(text(source_call.get("receiverOperationId")), {})
    if path == "result" or path.startswith("result."):
        source_type = text(operation.get("returnType"))
        field_path = path.removeprefix("result.") if path.startswith("result.") else ""
        if field_path == "unwrap":
            return optional_inner_type(source_type)
    else:
        parameter_name, dot, field_path = path.partition(".")
        source_type = next((
            text(parameter.get("type")) for parameter in operation.get("parameters") or []
            if isinstance(parameter, dict) and text(parameter.get("name")) == parameter_name
        ), "")
        if not dot:
            field_path = ""
    return projected_field_type(source_type, field_path, fields_by_type) if field_path else source_type


def _collaboration_bindings(
    collaboration: dict[str, Any], context: CollaborationContext,
) -> list[Finding]:
    operations = operation_catalog(context.model)
    fields_by_type = structured_field_types(context.model)
    calls = [item for item in collaboration.get("calls") or [] if isinstance(item, dict)]
    roots = _root_positions(calls)
    root_ordinal = {position: ordinal for ordinal, position in enumerate(roots)}
    preconditions = set(context.use_case.precondition_refs)
    findings: list[Finding] = []
    for position, call in enumerate(calls):
        operation = operations.get(text(call.get("receiverOperationId")), {})
        parameter_types = {
            text(parameter.get("name")): text(parameter.get("type"))
            for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
        }
        root_position = _root_index(calls, position)
        ordinal = root_ordinal.get(root_position, -1) if root_position is not None else -1
        actor_step = (
            context.groups[ordinal].actor_step
            if 0 <= ordinal < len(context.groups) else None
        )
        for binding in call.get("argumentBindings") or []:
            if not isinstance(binding, dict):
                continue
            parameter = text(binding.get("parameter"))
            source_ref = text(binding.get("sourceRef"))
            expected = parameter_types.get(parameter, "")
            source_type = _source_type(source_ref, calls[:position], operations, fields_by_type)
            source_id = source_ref.partition("#")[0]
            if source_ref == runtime_value_source(expected):
                valid = True
            elif source_type == "__entry__":
                valid = bool(actor_step and source_ref == f"{actor_step}#{parameter}")
            elif source_type == "__precondition__":
                valid = source_id in preconditions
            else:
                valid = bool(source_type and types_compatible(source_type, expected))
            if not valid:
                findings.append(Finding(
                    "class.collaboration.bindings",
                    "parameter source must be a compatible actor input, precondition, or earlier call value",
                    f"{text(call.get('callId'))}#{parameter}",
                ))
    return findings


COLLABORATION_CHECKS = (
    CheckSpec("class.collaboration.contract", _collaboration_contract),
    CheckSpec("class.collaboration.bindings", _collaboration_bindings),
)


def validate_collaboration(
    collaboration: dict[str, Any], context: CollaborationContext,
) -> ValidationReport:
    """한 유스케이스의 여러 root 호출과 binding을 검사한다."""

    return run_checks(COLLABORATION_CHECKS, collaboration or {}, context)


__all__ = ["COLLABORATION_CHECKS", "CollaborationContext", "validate_collaboration"]
