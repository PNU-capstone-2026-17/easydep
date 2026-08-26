"""Deterministic validation for reusable BCE signatures and collaborations."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.design.schemas.class_model import canonical_call_id, canonical_operation_id
from app.design.services.class_diagram.behavior import (
    _available_trace_steps,
    _class_name,
    _precondition_refs,
    _primary_actor,
    _required_trace_steps,
    _specification_map,
    _stereotype,
    _text,
    _trace_scope_ids,
    execution_groups,
    group_outcomes,
    project_call_dependencies,
)
from app.design.services.class_diagram.type_system import (
    field_type,
    referenced_type_names,
    type_is_resolved,
)


def is_stale_class_model(model: dict[str, Any]) -> bool:
    """Whether an artifact predates collaboration persistence.

    This intentionally has no version label in the artifact.  Consumers can
    retain rendering support for old ``methods`` records while blocking them
    from execution-aware downstream generation.
    """

    if not isinstance(model, dict):
        return False
    if not model.get("Classes") and not model.get("Relationships"):
        return False
    if "Collaborations" not in model:
        return True
    for class_item in model.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        if "methods" in class_item:
            return True
        for operation in class_item.get("operations") or []:
            if isinstance(operation, dict) and ({"actorEntry", "inputBindings"} & set(operation)):
                return True
    return False


def operation_contract_issues(
    model: dict[str, Any], state: dict[str, Any],
) -> list[tuple[str, str, str | None]]:
    """Return deterministic operation/collaboration defects.

    Tuple kinds remain compatible with the existing detector bridge: operation
    covers durable signature/call topology defects; input covers provenance.
    No diagnostic data is added to the persisted model.
    """

    if not isinstance(model, dict):
        return []
    issues: list[tuple[str, str, str | None]] = []
    if is_stale_class_model(model):
        has_legacy_execution = any(
            isinstance(item, dict) and (
                "methods" in item or any(
                    isinstance(operation, dict) and ({"actorEntry", "inputBindings"} & set(operation))
                    for operation in item.get("operations") or []
                )
            )
            for item in model.get("Classes") or []
        )
        if not has_legacy_execution:
            return []
        return [("operation", "class model is stale: Collaborations are required for execution-aware consumers", None)]

    classes = [item for item in model.get("Classes") or [] if isinstance(item, dict)]
    class_by_name = {_class_name(item): item for item in classes}
    data_types = {
        _text(item.get("name")): item
        for item in model.get("DataTypes") or [] if isinstance(item, dict)
    }
    operations = _operation_catalog(classes, issues)
    _type_issues(classes, data_types, issues)
    scenario = state.get("usecase_spec") if isinstance(state, dict) else None
    if isinstance(scenario, dict):
        relationships = state.get("relationships") if isinstance(state, dict) else None
        if isinstance(relationships, dict):
            scenario = {**scenario, "relationships": relationships}
    _relationship_issues(model, class_by_name, issues)
    _collaboration_issues(
        model, operations, class_by_name, issues,
        scenario if isinstance(scenario, dict) else None,
    )
    for outcome in group_outcomes(model):
        if outcome.status == "accepted":
            continue
        message = outcome.issues[0] if outcome.issues else "collaboration enrichment did not accept the execution group"
        issues.append((
            "needs_input" if outcome.needs_input else "operation",
            f"collaboration enrichment {outcome.status} for {outcome.group_id}: {message}",
            outcome.group_id,
        ))
    return issues


def _operation_catalog(
    classes: list[dict[str, Any]], issues: list[tuple[str, str, str | None]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    operations: dict[str, tuple[str, dict[str, Any]]] = {}
    for class_item in classes:
        class_name = _class_name(class_item)
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                issues.append(("operation", "operation must be an object", class_name))
                continue
            operation_id = _text(operation.get("operationId"))
            parameters = [item for item in operation.get("parameters") or [] if isinstance(item, dict)]
            expected = canonical_operation_id(class_name, _text(operation.get("name")), parameters)
            if class_name[:1].isupper() and class_name.isalnum() and operation_id != expected:
                issues.append(("operation", "operationId is not canonical", operation_id or class_name))
            names = [_text(item.get("name")) for item in parameters]
            if len(parameters) != len(operation.get("parameters") or []) or not all(names) or len(names) != len(set(names)):
                issues.append(("operation", "operation parameters are invalid or duplicated", operation_id or class_name))
            if operation_id in operations:
                issues.append(("operation", "operationId is duplicated", operation_id))
            elif operation_id:
                operations[operation_id] = (class_name, operation)
    return operations


def _type_issues(
    classes: list[dict[str, Any]], data_types: dict[str, dict[str, Any]],
    issues: list[tuple[str, str, str | None]],
) -> None:
    names = {_class_name(item) for item in classes} | set(data_types)
    referenced_data_types: set[str] = set()
    for class_item in classes:
        class_name = _class_name(class_item)
        for field in class_item.get("fields") or []:
            type_name = field_type(field)
            referenced_data_types.update(referenced_type_names(type_name))
            if type_name and not type_is_resolved(type_name, names, allow_void=False):
                issues.append(("operation", "field type does not resolve to a primitive, Class, or DataType", class_name))
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_id = _text(operation.get("operationId")) or class_name
            for parameter in operation.get("parameters") or []:
                if isinstance(parameter, dict) and not type_is_resolved(_text(parameter.get("type")), names, allow_void=False):
                    issues.append(("operation", "parameter type does not resolve to a primitive, Class, or DataType", operation_id))
                if isinstance(parameter, dict):
                    referenced_data_types.update(referenced_type_names(_text(parameter.get("type"))))
            if not type_is_resolved(_text(operation.get("returnType")), names, allow_void=True):
                issues.append(("operation", "return type does not resolve to a primitive, Class, or DataType", operation_id))
            referenced_data_types.update(referenced_type_names(_text(operation.get("returnType"))))
    for name, data_type in data_types.items():
        for field in data_type.get("fields") or []:
            type_name = field_type(field)
            referenced_data_types.update(referenced_type_names(type_name))
            if type_name and not type_is_resolved(type_name, names, allow_void=False):
                issues.append(("operation", "DataType field type does not resolve to a primitive, Class, or DataType", name))
    for name in sorted(set(data_types) - referenced_data_types):
        issues.append(("operation", "DataType is not referenced by a Class or operation", name))


def _relationship_issues(
    model: dict[str, Any], class_by_name: dict[str, dict[str, Any]],
    issues: list[tuple[str, str, str | None]],
) -> None:
    expected_dependencies = {
        (_text(item.get("source")), _text(item.get("target")))
        for item in project_call_dependencies(model)
        if _text(item.get("type")) == "Dependency"
    }
    actual_dependencies: set[tuple[str, str]] = set()
    comparable = True
    for relationship in model.get("Relationships") or []:
        if not isinstance(relationship, dict):
            comparable = False
            continue
        source, target = _text(relationship.get("source")), _text(relationship.get("target"))
        relationship_type = _text(relationship.get("type"))
        if source not in class_by_name or target not in class_by_name:
            comparable = False
            continue
        if relationship_type == "Dependency":
            if (
                _stereotype(class_by_name[source]) in {"boundary", "control"}
                and _stereotype(class_by_name[target]) in {"control", "entity"}
            ):
                actual_dependencies.add((source, target))
            else:
                comparable = False
            continue
        if relationship_type not in {"Association", "Aggregation", "Composition", "Inheritance"}:
            comparable = False
        if not (
            _stereotype(class_by_name[source]) == _stereotype(class_by_name[target]) == "entity"
        ):
            # Dedicated relationship/BCE checks own malformed semantic links.
            comparable = False
    if comparable and actual_dependencies != expected_dependencies:
        issues.append(("operation", "Dependency relationships must be projected exactly from collaboration calls", None))


def _collaboration_issues(
    model: dict[str, Any], operations: dict[str, tuple[str, dict[str, Any]]],
    class_by_name: dict[str, dict[str, Any]], issues: list[tuple[str, str, str | None]],
    scenario: dict[str, Any] | None,
) -> None:
    collaborations = [item for item in model.get("Collaborations") or [] if isinstance(item, dict)]
    expected_groups = {group.id: group for group in execution_groups(scenario)} if scenario else {}
    seen_collaborations: set[str] = set()
    for collaboration in collaborations:
        collaboration_id = _text(collaboration.get("collaborationId"))
        if not collaboration_id or collaboration_id in seen_collaborations:
            issues.append(("operation", "collaborationId is missing or duplicated", collaboration_id or None))
            continue
        seen_collaborations.add(collaboration_id)
        use_case_ids = [_text(value) for value in collaboration.get("useCaseIds") or []]
        if not use_case_ids or not all(use_case_ids) or len(use_case_ids) != len(set(use_case_ids)):
            issues.append(("operation", "Collaboration useCaseIds must be a non-duplicated ordered list", collaboration_id))
            continue
        group = expected_groups.get(collaboration_id)
        if scenario:
            if not group:
                issues.append(("operation", "collaborationId does not identify an accepted execution group", collaboration_id))
            else:
                expected_scope = _trace_scope_ids(group, scenario)
                if use_case_ids != expected_scope:
                    issues.append(("operation", "useCaseIds must put the execution/root use case first, followed by deterministic include/extend trace scope", collaboration_id))
                _actor_issue(collaboration, group, scenario, issues)
        _call_issues(collaboration, operations, class_by_name, group, scenario, issues)
    if scenario:
        for group_id in sorted(set(expected_groups) - seen_collaborations):
            issues.append(("operation", "accepted execution group has no Collaboration", group_id))


def _actor_issue(
    collaboration: dict[str, Any], group, scenario: dict[str, Any],
    issues: list[tuple[str, str, str | None]],
) -> None:
    actor = _text(collaboration.get("entryActor"))
    expected_actor = _primary_actor(scenario, _specification_map(scenario).get(group.use_case_id, {}))
    if group.actor_step and actor != expected_actor:
        issues.append(("operation", "actor execution group must declare its deterministic entryActor", group.id))
    if not group.actor_step and actor:
        issues.append(("operation", "non-actor execution group cannot declare entryActor", group.id))


def _call_issues(
    collaboration: dict[str, Any], operations: dict[str, tuple[str, dict[str, Any]]],
    class_by_name: dict[str, dict[str, Any]], group, scenario: dict[str, Any] | None,
    issues: list[tuple[str, str, str | None]],
) -> None:
    collaboration_id = _text(collaboration.get("collaborationId"))
    calls = [item for item in collaboration.get("calls") or [] if isinstance(item, dict)]
    if len(calls) != len(collaboration.get("calls") or []) or not calls:
        issues.append(("operation", "Collaboration must contain ordered call objects", collaboration_id))
        return
    known_steps: set[str] = set()
    required_steps: set[str] = set()
    if scenario and group:
        known_steps = _available_trace_steps(group, scenario)
        required_steps = _required_trace_steps(group, scenario)
    call_by_id: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    for position, call in enumerate(calls, start=1):
        call_id = _text(call.get("callId"))
        expected_call_id = canonical_call_id(collaboration_id, position)
        if call_id != expected_call_id:
            issues.append(("operation", "callId is not deterministic for collaboration order", call_id or collaboration_id))
        parent_id = _text(call.get("parentCallId"))
        if position == 1:
            if parent_id:
                issues.append(("operation", "the first call cannot have parentCallId", call_id))
        elif not parent_id or parent_id not in call_by_id:
            issues.append(("operation", "parentCallId must reference an earlier call in the same collaboration", call_id))
        operation_id = _text(call.get("receiverOperationId"))
        target = operations.get(operation_id)
        if not target:
            issues.append(("operation", "call receiverOperationId does not exist", call_id or collaboration_id))
        else:
            class_name = target[0]
            trace_scope = {_text(item) for item in collaboration.get("useCaseIds") or []}
            if not set(class_by_name.get(class_name, {}).get("use_case_ids") or []) & trace_scope:
                issues.append(("operation", "call receiver class is outside collaboration useCaseIds trace scope", call_id))
        step_refs = [_text(ref) for ref in call.get("stepRefs") or []]
        if not step_refs:
            issues.append(("operation", "every call needs stepRefs", call_id))
        elif known_steps and any(ref not in known_steps for ref in step_refs):
            issues.append(("operation", "call stepRefs are outside collaboration trace scope", call_id))
        covered.update(step_refs)
        call_by_id[call_id] = call
    if required_steps - covered:
        issues.append(("operation", "collaboration calls do not cover every execution-group step", collaboration_id))
    if group and calls:
        first_target = operations.get(_text(calls[0].get("receiverOperationId")))
        if first_target:
            first_stereotype = _stereotype(class_by_name.get(first_target[0], {}))
            if group.actor_step and first_stereotype != "boundary":
                issues.append(("operation", "actor group's root call must target a Boundary operation", collaboration_id))
            if group.internal and first_stereotype != "control":
                issues.append(("operation", "internal group's root call must target a Control operation", collaboration_id))
        called_stereotypes = {
            _stereotype(class_by_name.get(target[0], {}))
            for call in calls
            if (target := operations.get(_text(call.get("receiverOperationId"))))
        }
        if group.actor_step and "control" not in called_stereotypes:
            issues.append((
                "operation",
                "actor group's Boundary root must delegate to a Control call",
                collaboration_id,
            ))
    for position, call in enumerate(calls):
        target = operations.get(_text(call.get("receiverOperationId")))
        if not target:
            continue
        _binding_issues_for_call(
            collaboration, calls, position, target[1], operations, group, scenario, issues,
        )


def _binding_issues_for_call(
    collaboration: dict[str, Any], calls: list[dict[str, Any]], index: int,
    operation: dict[str, Any], operations: dict[str, tuple[str, dict[str, Any]]], group,
    scenario: dict[str, Any] | None, issues: list[tuple[str, str, str | None]],
) -> None:
    call = calls[index]
    call_id = _text(call.get("callId"))
    declared = {
        _text(parameter.get("name")): _text(parameter.get("type"))
        for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
    }
    bindings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in call.get("argumentBindings") or []:
        if isinstance(binding, dict):
            bindings[_text(binding.get("parameter"))].append(binding)
        else:
            issues.append(("input", "argumentBinding must be an object", call_id))
    for parameter, type_name in declared.items():
        entries = bindings.get(parameter, [])
        location = f"{call_id}#{parameter}"
        if len(entries) != 1:
            issues.append(("input", "every call parameter needs exactly one argumentBinding", location))
            continue
        _source_issue(
            _text(entries[0].get("sourceRef")), parameter, type_name,
            collaboration, calls, index, operations, group, scenario, issues,
        )
    for parameter in sorted(set(bindings) - set(declared)):
        issues.append(("input", "argumentBinding names no declared receiver parameter", f"{call_id}#{parameter}"))


def _source_issue(
    source_ref: str, parameter: str, type_name: str, collaboration: dict[str, Any],
    calls: list[dict[str, Any]], index: int,
    operations: dict[str, tuple[str, dict[str, Any]]], group, scenario: dict[str, Any] | None,
    issues: list[tuple[str, str, str | None]],
) -> None:
    call_id = _text(calls[index].get("callId"))
    location = f"{call_id}#{parameter}"
    if not source_ref:
        issues.append(("input", "argumentBinding sourceRef is required", location))
        return
    if group and scenario and source_ref == f"{group.actor_step}#{parameter}":
        if index != 0 or not _text(collaboration.get("entryActor")):
            issues.append(("producer", "actor/main-step input is valid only for the entry call", location))
        return
    if group and scenario and source_ref in _precondition_refs(
        _specification_map(scenario).get(group.use_case_id, {})
    ):
        specification = _specification_map(scenario).get(group.use_case_id, {})
        if source_ref not in _precondition_refs(specification):
            issues.append(("producer", "precondition context sourceRef is not declared", location))
        return
    earlier = {_text(call.get("callId")): call for call in calls[:index]}
    if "#" in source_ref and not source_ref.endswith("#result"):
        source_call_id, source_parameter = source_ref.rsplit("#", 1)
        source_call = earlier.get(source_call_id)
        if not source_call:
            issues.append(("producer", "ancestor-call parameter source must reference an earlier call", location))
            return
        if source_call_id not in _ancestor_ids(calls, index):
            issues.append(("producer", "parameter source must be an ancestor call, not an unrelated earlier call", location))
            return
        source_operation = operations.get(_text(source_call.get("receiverOperationId")), ("", {}))[1]
        source_type = next((
            _text(item.get("type")) for item in source_operation.get("parameters") or []
            if isinstance(item, dict) and _text(item.get("name")) == source_parameter
        ), "")
        if source_parameter != parameter or source_type != type_name:
            issues.append(("producer", "ancestor-call parameter source must preserve parameter name and type", location))
        return
    source_call_id, separator, source_kind = source_ref.partition("#")
    if not separator or source_kind != "result":
        issues.append(("producer", "earlier call result sourceRef must end with #result", location))
        return
    source_call = earlier.get(source_call_id)
    if not source_call:
        issues.append(("producer", "sourceRef must be actor input, precondition context, ancestor parameter, or earlier call result", location))
        return
    source_operation = operations.get(_text(source_call.get("receiverOperationId")), ("", {}))[1]
    return_type = _text(source_operation.get("returnType"))
    if return_type.casefold() == "void" or return_type != type_name:
        issues.append(("producer", "earlier call result must be non-void and type-compatible", location))


def _ancestor_ids(calls: list[dict[str, Any]], index: int) -> set[str]:
    prior = {_text(call.get("callId")): call for call in calls[:index]}
    result: set[str] = set()
    parent = _text(calls[index].get("parentCallId"))
    while parent and parent in prior:
        result.add(parent)
        parent = _text(prior[parent].get("parentCallId"))
    return result
