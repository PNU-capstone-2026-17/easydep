"""Call-plan selection, binding provenance, and collaboration materialization."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, create_model

from app.core.config import settings
from app.core.validation import Finding, run_checks
from app.design.schemas.class_model import canonical_call_id
from app.design.services.class_diagram.models import GroupResult
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    ProposedCall,
)
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    text,
)
from app.design.services.class_diagram.type_system import (
    projected_field_type,
    structured_field_types,
    types_compatible,
)
from app.design.services.class_diagram.validation.collaboration import (
    COLLABORATION_CHECKS,
    CollaborationContext,
)
from app.design.services.class_diagram.validation.model import (
    derived_value_source,
    operation_catalog,
    optional_inner_type,
    runtime_value_source,
    type_can_default,
)
from app.design.services.common.structured import parse_structured

CALL_PLAN_PROMPT = """
Build the ordered call tree for exactly one execution group. Select only the
supplied receiverOperationId values. Step references are projected from the
selected operation contracts; do not return them. Return no classes, methods,
call ids, values, or argument bindings.

The first call has no parent. Every later call names the one-based index of an
earlier caller. An actor group enters through Boundary and delegates to
Control. Control delegates persistent-state work to Entity. Boundary never
calls Entity directly. Cover every requiredStepRef, including included-flow
steps, without repeating calls merely to repeat a step label.
""".strip()


BINDING_PROMPT = """
Select one sourceRef for each supplied choice. The response schema restricts
every field to that parameter's finite candidates. Resolve all fields and
return no explanation.
Prefer the source whose name and role best match the receiver parameter; do
not invent values or identifiers.
""".strip()


def _finding_text(findings: tuple[Finding, ...]) -> list[str]:
    return [
        f"{finding.location}: {finding.message}" if finding.location else finding.message
        for finding in findings
    ]


def _group_operations(
    model: dict[str, Any], group: ExecutionGroup,
) -> dict[str, dict[str, Any]]:
    allowed = set(group.trace_use_case_ids)
    return {
        operation_id: operation
        for operation_id, operation in operation_catalog(model).items()
        if allowed & {
            text(use_case_id)
            for class_item in model.get("Classes") or [] if isinstance(class_item, dict)
            if text(class_item.get("className")) == operation["className"]
            for use_case_id in class_item.get("use_case_ids") or []
        }
    }


def _group_payload(
    index: ScenarioIndex, model: dict[str, Any], group: ExecutionGroup,
) -> dict[str, Any]:
    operations = _group_operations(model, group)
    steps = {
        step.id: step for use_case_id in group.trace_use_case_ids
        for step in index.use_case(use_case_id).steps
    }
    return {
        "collaborationId": group.id,
        "entryActor": group.entry_actor,
        "actorStepRef": group.actor_step,
        "requiredStepRefs": list(group.required_step_ids),
        "steps": [
            {"id": step_id, "sentence": steps[step_id].sentence}
            for step_id in group.required_step_ids if step_id in steps
        ],
        "receiverOperations": [
            {
                "operationId": operation_id,
                "className": operation["className"],
                "stereotype": operation["stereotype"],
                "parameters": operation.get("parameters") or [],
                "returnType": operation.get("returnType"),
                "stepRefs": operation.get("stepRefs") or [],
            }
            for operation_id, operation in sorted(operations.items())
            if set(operation.get("stepRefs") or []) & set(group.required_step_ids)
        ],
    }


def propose_call_plan(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    *,
    previous: CallPlanProposal | None = None,
    finding: str = "",
) -> CallPlanProposal:
    payload = _group_payload(index, model, group)
    if previous is not None:
        payload["previousPlan"] = previous.model_dump(by_alias=True)
    if finding:
        payload["task"] = "Return one full repaired call plan and resolve the finding."
        payload["finding"] = finding
    operation_ids = tuple(item["operationId"] for item in payload["receiverOperations"])
    if not operation_ids:
        raise ValueError(f"execution group has no receiver operations: {group.id}")
    finite_call = create_model(
        "FiniteProposedCall",
        __base__=ProposedCall,
        receiver_operation_id=(
            Literal.__getitem__(operation_ids),
            Field(alias="receiverOperationId"),
        ),
    )
    finite_plan = create_model(
        "FiniteCallPlan",
        __base__=CallPlanProposal,
        calls=(list[finite_call], Field(min_length=1, max_length=len(operation_ids))),
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": CALL_PLAN_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        finite_plan,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
        operation="InteractionCallPlanRepair" if finding else "InteractionCallPlan",
        metadata={"collaborationGroup": group.id},
    )
    return CallPlanProposal.model_validate(
        finite_plan.model_validate(parsed).model_dump(by_alias=True),
    )


def _ancestors(calls: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    previous = {text(call.get("callId")): call for call in calls[:index]}
    parent_id = text(calls[index].get("parentCallId"))
    result: list[dict[str, Any]] = []
    while parent_id and parent_id in previous:
        call = previous[parent_id]
        result.append(call)
        parent_id = text(call.get("parentCallId"))
    return result


def _binding_candidates(
    model: dict[str, Any],
    index: ScenarioIndex,
    group: ExecutionGroup,
    calls: list[dict[str, Any]],
    call_index: int,
    parameter: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[str]:
    name = text(parameter.get("name"))
    target_type = text(parameter.get("type"))
    if call_index == 0 and group.actor_step:
        return [f"{group.actor_step}#{name}"]
    if call_index == 0:
        return [
            f"{ref}#{name}" for use_case_id in group.trace_use_case_ids
            for ref in index.use_case(use_case_id).precondition_refs
        ]
    fields_by_type = structured_field_types(model)
    candidates: list[str] = []
    named_sources: dict[str, list[tuple[str, str]]] = {}

    def add_named(source_name: str, source_type: str, source_ref: str) -> None:
        named_sources.setdefault(source_name.casefold(), []).append((source_type, source_ref))

    ancestors = _ancestors(calls, call_index)
    ancestor_ids = {text(item.get("callId")) for item in ancestors}
    for ancestor in ancestors:
        operation = operations[text(ancestor.get("receiverOperationId"))]
        for source in operation.get("parameters") or []:
            if not isinstance(source, dict):
                continue
            source_name = text(source.get("name"))
            source_type = text(source.get("type"))
            source_ref = f"{ancestor['callId']}#{source_name}"
            add_named(source_name, source_type, source_ref)
            if types_compatible(source_type, target_type):
                candidates.append(source_ref)
            for field_path in fields_by_type.get(source_type, {}):
                projected = projected_field_type(source_type, field_path, fields_by_type)
                field_ref = f"{source_ref}.{field_path}"
                add_named(field_path, projected, field_ref)
                if field_path.casefold() == name.casefold() and types_compatible(projected, target_type):
                    candidates.append(field_ref)
        return_type = text(operation.get("returnType"))
        if return_type.casefold() != "void" and types_compatible(return_type, target_type):
            candidates.append(f"{ancestor['callId']}#result")
        elif types_compatible(optional_inner_type(return_type), target_type):
            candidates.append(f"{ancestor['callId']}#result.unwrap")
        for field_path in fields_by_type.get(return_type, {}):
            projected = projected_field_type(return_type, field_path, fields_by_type)
            field_ref = f"{ancestor['callId']}#result.{field_path}"
            add_named(field_path, projected, field_ref)
            if field_path.casefold() == name.casefold() and types_compatible(projected, target_type):
                candidates.append(field_ref)
    for earlier in reversed(calls[:call_index]):
        if text(earlier.get("callId")) in ancestor_ids:
            continue
        operation = operations[text(earlier.get("receiverOperationId"))]
        return_type = text(operation.get("returnType"))
        if return_type.casefold() != "void" and types_compatible(return_type, target_type):
            candidates.append(f"{earlier['callId']}#result")
        elif types_compatible(optional_inner_type(return_type), target_type):
            candidates.append(f"{earlier['callId']}#result.unwrap")
        for field_path in fields_by_type.get(return_type, {}):
            projected = projected_field_type(return_type, field_path, fields_by_type)
            field_ref = f"{earlier['callId']}#result.{field_path}"
            add_named(field_path, projected, field_ref)
            if field_path.casefold() == name.casefold() and types_compatible(projected, target_type):
                candidates.append(field_ref)
    target_fields = fields_by_type.get(target_type, {})
    if not candidates and target_fields:
        mappings: dict[str, str] = {}
        for field, expected in target_fields.items():
            matching = [
                source_ref for source_type, source_ref in named_sources.get(field.casefold(), [])
                if types_compatible(source_type, expected)
            ]
            if matching:
                mappings[field] = matching[0]
            elif runtime_value_source(expected):
                mappings[field] = runtime_value_source(expected)
            elif not type_can_default(expected):
                break
        else:
            candidates.append(derived_value_source(target_type, mappings))
    if not candidates and runtime_value_source(target_type):
        candidates.append(runtime_value_source(target_type))
    return list(dict.fromkeys(candidates))


def select_ambiguous_bindings(
    group: ExecutionGroup,
    ambiguous: dict[str, list[str]],
) -> dict[str, str]:
    fields: dict[str, tuple[Any, Any]] = {}
    choices: list[dict[str, Any]] = []
    locations: dict[str, str] = {}
    for position, (parameter, candidates) in enumerate(sorted(ambiguous.items()), start=1):
        field_name = f"choice{position}"
        finite_values = tuple(dict.fromkeys(candidates))
        if not finite_values:
            raise ValueError(f"binding candidates are empty for {parameter}")
        fields[field_name] = (
            Literal.__getitem__(finite_values), Field(description=f"Source for {parameter}"),
        )
        choices.append({"choice": field_name, "parameter": parameter, "candidates": list(finite_values)})
        locations[field_name] = parameter
    selection_schema = create_model(
        "FiniteBindingChoices", __config__={"extra": "forbid"}, **fields,
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": BINDING_PROMPT},
            {"role": "user", "content": json.dumps({
                "collaborationId": group.id, "choices": choices,
            }, ensure_ascii=False)},
        ],
        selection_schema,
        reasoning_effort="low",
        max_completion_tokens=min(settings.design_class_collaboration_max_completion_tokens, 2048),
        operation="InteractionBindingSelection",
        metadata={"collaborationGroup": group.id},
    )
    selected = selection_schema.model_validate(parsed).model_dump()
    return {locations[field_name]: source_ref for field_name, source_ref in selected.items()}


def materialize(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    plan: CallPlanProposal,
) -> dict[str, Any]:
    operations = _group_operations(model, group)
    calls: list[dict[str, Any]] = []
    allowed = set(group.required_step_ids)
    for position, proposed in enumerate(plan.calls, start=1):
        operation_id = text(proposed.receiver_operation_id)
        if operation_id not in operations:
            raise ValueError(f"unknown receiverOperationId: {operation_id}")
        parent_index = proposed.parent_call_index
        if position == 1 and parent_index is not None:
            raise ValueError("the first call cannot have a parent")
        if position > 1 and parent_index is None:
            raise ValueError("every delegated call requires an earlier parent")
        if parent_index is not None and parent_index >= position:
            raise ValueError("parentCallIndex must reference an earlier call")
        operation = operations[operation_id]
        declared = {text(ref) for ref in operation.get("stepRefs") or []}
        refs = [ref for ref in group.required_step_ids if ref in declared and ref in allowed]
        if not refs:
            raise ValueError("selected operation has no declared step in this group")
        calls.append({
            "callId": canonical_call_id(group.id, position),
            "parentCallId": canonical_call_id(group.id, parent_index) if parent_index else None,
            "receiverOperationId": operation_id,
            "stepRefs": refs,
            "argumentBindings": [],
        })
    for call in calls[1:]:
        parent = next(item for item in calls if item["callId"] == call["parentCallId"])
        source = operations[parent["receiverOperationId"]]["stereotype"]
        target = operations[call["receiverOperationId"]]["stereotype"]
        if (source, target) in {
            ("boundary", "boundary"), ("boundary", "entity"),
            ("entity", "boundary"), ("entity", "control"),
        }:
            raise ValueError(f"BCE communication is invalid: {source} -> {target}")
    ambiguous: dict[str, list[str]] = {}
    for call_index, call in enumerate(calls):
        operation = operations[call["receiverOperationId"]]
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                raise TypeError("operation parameter must be an object")
            candidates = _binding_candidates(model, index, group, calls, call_index, parameter, operations)
            location = f"{call['callId']}#{text(parameter.get('name'))}"
            if not candidates:
                raise ValueError(f"no finite source for {location}")
            if len(candidates) > 1:
                ambiguous[location] = candidates
            else:
                call["argumentBindings"].append({
                    "parameter": text(parameter.get("name")), "sourceRef": candidates[0],
                })
    selected = select_ambiguous_bindings(group, ambiguous) if ambiguous else {}
    for call in calls:
        operation = operations[call["receiverOperationId"]]
        existing = {text(binding.get("parameter")) for binding in call["argumentBindings"]}
        for parameter in operation.get("parameters") or []:
            name = text(parameter.get("name"))
            if name not in existing:
                call["argumentBindings"].append({
                    "parameter": name, "sourceRef": selected[f"{call['callId']}#{name}"],
                })
    collaboration = {
        "collaborationId": group.id,
        "useCaseIds": list(group.trace_use_case_ids),
        "entryActor": group.entry_actor,
        "calls": calls,
    }
    report = run_checks(
        COLLABORATION_CHECKS, collaboration, CollaborationContext(index, model, group), parallel=True,
    )
    if report.errors or report.findings:
        raise ValueError("; ".join([*report.errors, *_finding_text(report.findings)]))
    return collaboration


def process_group(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    directive: str = "",
) -> GroupResult:
    plan: CallPlanProposal | None = None
    try:
        plan = propose_call_plan(index, model, group, finding=directive)
        return GroupResult(group.id, materialize(index, model, group, plan))
    except Exception as first_error:  # one local replacement, never a global loop
        try:
            repaired = propose_call_plan(index, model, group, previous=plan, finding=str(first_error))
            return GroupResult(group.id, materialize(index, model, group, repaired))
        except Exception as second_error:
            return GroupResult(group.id, None, f"{type(second_error).__name__}: {second_error}")


call_plan = propose_call_plan

__all__ = [
    "call_plan",
    "materialize",
    "process_group",
    "propose_call_plan",
    "select_ambiguous_bindings",
]



