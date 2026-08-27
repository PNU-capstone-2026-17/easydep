"""호출 순서와 parameter provenance를 검사하는 결정론적 협업 규칙이다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.validation import CheckSpec, Finding, ValidationReport, run_checks
from app.design.services.class_diagram.scenario import ExecutionGroup, ScenarioIndex, text
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


@dataclass(frozen=True)
class CollaborationContext:
    index: ScenarioIndex
    model: dict[str, Any]
    group: ExecutionGroup


def _collaboration_contract(
    collaboration: dict[str, Any], context: CollaborationContext,
) -> list[Finding]:
    operations = operation_catalog(context.model)
    calls = [item for item in collaboration.get("calls") or [] if isinstance(item, dict)]
    findings: list[Finding] = []
    if text(collaboration.get("collaborationId")) != context.group.id:
        findings.append(Finding(
            "class.collaboration.contract", "collaborationId does not match its execution group", context.group.id,
        ))
    if not calls:
        return [*findings, Finding(
            "class.collaboration.contract", "execution group requires calls", context.group.id,
        )]
    call_by_id = {text(call.get("callId")): call for call in calls}
    if len(call_by_id) != len(calls):
        findings.append(Finding(
            "class.collaboration.contract", "call IDs must be nonblank and unique", context.group.id,
        ))
    covered: set[str] = set()
    for position, call in enumerate(calls, start=1):
        call_id = text(call.get("callId"))
        operation_id = text(call.get("receiverOperationId"))
        operation = operations.get(operation_id)
        if operation is None:
            findings.append(Finding(
                "class.collaboration.contract", "call receiver operation does not exist", call_id,
            ))
            continue
        expected_id = f"{context.group.id}::call:{position}"
        if call_id != expected_id:
            findings.append(Finding(
                "class.collaboration.contract", "call ID is not canonical", call_id,
            ))
        parent_id = text(call.get("parentCallId"))
        if position == 1 and parent_id:
            findings.append(Finding(
                "class.collaboration.contract", "the root call cannot have a parent", call_id,
            ))
        if position > 1 and parent_id not in {
            text(previous.get("callId")) for previous in calls[: position - 1]
        }:
            findings.append(Finding(
                "class.collaboration.contract", "parentCallId must reference an earlier call", call_id,
            ))
        parent = call_by_id.get(parent_id)
        if parent:
            parent_operation = operations.get(
                text(parent.get("receiverOperationId")), {}
            )
            parent_stereotype = text(parent_operation.get("stereotype"))
            receiver_stereotype = text(operation.get("stereotype"))
            invalid_edge = (
                (parent_stereotype == "boundary" and receiver_stereotype != "control")
                or (receiver_stereotype == "entity" and parent_stereotype != "control")
                or parent_stereotype == "entity"
            )
            if invalid_edge:
                findings.append(Finding(
                    "class.collaboration.contract",
                    "call tree must follow Boundary to Control to Entity responsibilities",
                    call_id,
                ))
        declared = {text(ref) for ref in operation.get("stepRefs") or []}
        refs = {text(ref) for ref in call.get("stepRefs") or []}
        if not refs or not refs <= declared or not refs <= set(context.group.required_step_ids):
            findings.append(Finding(
                "class.collaboration.contract", "call stepRefs are outside receiver trace scope", call_id,
            ))
        covered.update(refs)
        expected_parameters = {
            text(parameter.get("name")) for parameter in operation.get("parameters") or []
            if isinstance(parameter, dict)
        }
        bound = {
            text(binding.get("parameter")) for binding in call.get("argumentBindings") or []
            if isinstance(binding, dict)
        }
        if bound != expected_parameters:
            findings.append(Finding(
                "class.collaboration.contract", "argumentBindings must match receiver parameters", call_id,
            ))
    if set(context.group.required_step_ids) - covered:
        findings.append(Finding(
            "class.collaboration.contract", "collaboration does not cover every required step", context.group.id,
        ))
    root = operations.get(text(calls[0].get("receiverOperationId")), {})
    if context.group.actor_step and root.get("stereotype") != "boundary":
        findings.append(Finding(
            "class.collaboration.contract", "an actor group must enter through Boundary", context.group.id,
        ))
    if context.group.actor_step and not any(
        operations.get(text(call.get("receiverOperationId")), {}).get("stereotype") == "control"
        for call in calls
    ):
        findings.append(Finding(
            "class.collaboration.contract", "Boundary must delegate to Control", context.group.id,
        ))
    return findings


def _collaboration_order(
    collaboration: dict[str, Any], context: CollaborationContext,
) -> list[Finding]:
    """Require one depth-first call tree whose main-flow calls stay ordered."""

    calls = [item for item in collaboration.get("calls") or [] if isinstance(item, dict)]
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for call in calls:
        call_id = text(call.get("callId"))
        parent_id = text(call.get("parentCallId"))
        if parent_id:
            children.setdefault(parent_id, []).append(call_id)
        else:
            roots.append(call_id)
    preorder: list[str] = []

    def visit(call_id: str) -> None:
        preorder.append(call_id)
        for child_id in children.get(call_id, []):
            visit(child_id)

    if len(roots) == 1:
        visit(roots[0])
    findings: list[Finding] = []
    if preorder != [text(item.get("callId")) for item in calls]:
        findings.append(Finding(
            "class.collaboration.order",
            "calls must be stored in depth-first execution order",
            context.group.id,
        ))
        return findings
    step_order = {
        step.id: step.order
        for use_case_id in context.group.trace_use_case_ids
        for step in context.index.use_case(use_case_id).steps
        if step.branch == "main"
    }
    positions = [
        min(step_order[ref] for ref in call.get("stepRefs") or [] if ref in step_order)
        for call in calls
        if any(ref in step_order for ref in call.get("stepRefs") or [])
    ]
    if positions != sorted(positions):
        findings.append(Finding(
            "class.collaboration.order",
            "main-flow calls must follow specification step order",
            context.group.id,
        ))
    return findings


def _source_type(
    source_ref: str,
    call: dict[str, Any],
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
            nested_ref = field_sources.get(name, "")
            if not nested_ref:
                if type_can_default(expected):
                    continue
                return ""
            if nested_ref == runtime_value_source(expected):
                continue
            nested_type = _source_type(
                nested_ref, call, previous_calls, operations, fields_by_type,
            )
            if not nested_type or not types_compatible(nested_type, expected):
                return ""
        return derived_type
    source_id, separator, path = source_ref.partition("#")
    if not separator:
        return ""
    if source_id.endswith(":precondition:1") or ":precondition:" in source_id:
        return "__precondition__"
    if source_id not in {text(item.get("callId")) for item in previous_calls}:
        return "__entry__" if not previous_calls else ""
    source_call = next(item for item in previous_calls if text(item.get("callId")) == source_id)
    operation = operations.get(text(source_call.get("receiverOperationId")), {})
    if path == "result" or path.startswith("result."):
        previous_by_id = {text(item.get("callId")): item for item in previous_calls}
        ancestor_id = text(call.get("parentCallId"))
        while ancestor_id and ancestor_id in previous_by_id:
            if ancestor_id == source_id:
                return ""
            ancestor_id = text(previous_by_id[ancestor_id].get("parentCallId"))
        source_type = text(operation.get("returnType"))
        field_path = path.removeprefix("result.") if path.startswith("result.") else ""
        if field_path == "unwrap":
            return optional_inner_type(source_type)
    else:
        previous_by_id = {text(item.get("callId")): item for item in previous_calls}
        ancestor_id = text(call.get("parentCallId"))
        ancestors: set[str] = set()
        while ancestor_id and ancestor_id in previous_by_id:
            ancestors.add(ancestor_id)
            ancestor_id = text(previous_by_id[ancestor_id].get("parentCallId"))
        if source_id not in ancestors:
            return ""
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
    findings: list[Finding] = []
    for index, call in enumerate(calls):
        operation = operations.get(text(call.get("receiverOperationId")), {})
        parameter_types = {
            text(parameter.get("name")): text(parameter.get("type"))
            for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
        }
        for binding in call.get("argumentBindings") or []:
            if not isinstance(binding, dict):
                continue
            parameter = text(binding.get("parameter"))
            source_ref = text(binding.get("sourceRef"))
            source_type = _source_type(
                source_ref, call, calls[:index], operations, fields_by_type,
            )
            expected = parameter_types.get(parameter, "")
            if source_ref == runtime_value_source(expected):
                valid = True
            elif source_type == "__entry__":
                valid = index == 0 and source_ref == f"{context.group.actor_step}#{parameter}"
            elif source_type == "__precondition__":
                valid = index == 0 and source_ref.partition("#")[0] in {
                    ref for use_case_id in context.group.trace_use_case_ids
                    for ref in context.index.use_case(use_case_id).precondition_refs
                }
            else:
                valid = bool(source_type and types_compatible(source_type, expected))
            if not valid:
                findings.append(Finding(
                    "class.collaboration.bindings",
                    "parameter source must be an entry input, explicit precondition, ancestor parameter, earlier result, or supported runtime clock with a compatible type",
                    f"{text(call.get('callId'))}#{parameter}",
                ))
    return findings


COLLABORATION_CHECKS = (
    CheckSpec("class.collaboration.contract", _collaboration_contract),
    CheckSpec("class.collaboration.order", _collaboration_order),
    CheckSpec("class.collaboration.bindings", _collaboration_bindings),
)


def validate_collaboration(
    collaboration: dict[str, Any], context: CollaborationContext
) -> ValidationReport:
    """한 실행 그룹의 호출 트리와 parameter provenance를 검사한다.

    Args:
        collaboration: 수락 전 협업 JSON 후보다.
        context: BCE 연산 카탈로그와 소유 실행 그룹을 고정한 문맥이다.

    Returns:
        호출 계약, 순서와 binding 규칙의 finding을 담은 보고서다.
    """
    return run_checks(COLLABORATION_CHECKS, collaboration or {}, context)


__all__ = ["COLLABORATION_CHECKS", "CollaborationContext", "validate_collaboration"]
