"""Deterministic validation for accepted BCE operation contracts."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.design.schemas.class_model import canonical_operation_id, operation_method_signature
from app.design.services.class_diagram.behavior import (
    _binding_source_operation_id,
    _class_in_scope,
    _class_name,
    _dependencies,
    _execution_order_edges,
    _formal_callsite_source_ref,
    _group_contract_issues,
    _Group,
    _is_scalar_value_type,
    _operation_sort_key,
    _reachable,
    _specification_map,
    _steps,
    _stereotype,
    _text,
    _topological_operation_order,
    execution_groups,
    group_outcomes,
    relationship_pairs,
)


def operation_contract_issues(
    model: dict[str, Any], state: dict[str, Any]
) -> list[tuple[str, str, str | None]]:
    """Validate operation ids, bindings, and finite producer order.

    The return tuples let the class detector map operation-shape versus
    input/producer failures onto the existing class rule catalog without adding
    diagnostic fields to the persisted BCE artifact.
    """
    scenario = state.get("usecase_spec") if isinstance(state, dict) else None
    if not isinstance(scenario, dict):
        return []
    relationships = state.get("relationships") if isinstance(state, dict) else None
    if isinstance(relationships, dict):
        scenario = {**scenario, "relationships": relationships}
    classes = [item for item in model.get("Classes") or [] if isinstance(item, dict)]
    if not any("operations" in item for item in classes):
        return []
    class_by_name = {_class_name(item): item for item in classes}
    operations: dict[str, tuple[str, dict[str, Any]]] = {}
    issues: list[tuple[str, str, str | None]] = []
    for class_item in classes:
        class_name = _class_name(class_item)
        expected_methods: list[str] = []
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                issues.append(("operation", "operation must be an object", class_name))
                continue
            operation_id = _text(operation.get("operationId"))
            if operation_id != canonical_operation_id(
                class_name,
                _text(operation.get("name")),
                [item for item in operation.get("parameters") or [] if isinstance(item, dict)],
            ):
                issues.append(("operation", "operationId is not canonical", operation_id or class_name))
            if operation_id in operations:
                issues.append(("operation", "operationId is duplicated", operation_id))
            else:
                operations[operation_id] = (class_name, operation)
            parameters = operation.get("parameters") or []
            names = [_text(item.get("name")) for item in parameters if isinstance(item, dict)]
            if len(names) != len(parameters) or not all(names) or len(set(names)) != len(names):
                issues.append(("operation", "operation parameters are invalid or duplicated", operation_id or class_name))
            expected_methods.append(operation_method_signature(
                _text(operation.get("name")),
                [item for item in operation.get("parameters") or [] if isinstance(item, dict)],
                _text(operation.get("returnType")) or "void",
            ))
        if list(class_item.get("methods") or []) != expected_methods:
            issues.append(("operation", "methods must mirror accepted operations", class_name))

    groups = execution_groups(scenario)
    group_by_step = {step: group for group in groups for step in group.step_ids}
    edges = _dependencies(model)
    ranks = _operation_ranks(
        operations, groups, group_by_step, scenario, class_by_name, edges, issues
    )
    _topology_issues(operations, groups, ranks, class_by_name, edges, issues, scenario)
    _binding_issues(
        operations, ranks, group_by_step, edges, scenario, class_by_name, issues
    )
    for outcome in group_outcomes(model):
        if outcome.status == "accepted":
            continue
        message = outcome.issues[0] if outcome.issues else "behavior enrichment did not accept the group"
        issues.append((
            "operation",
            f"behavior enrichment {outcome.status} for {outcome.group_id}: {message}",
            outcome.group_id,
        ))
    return issues


def _operation_ranks(
    operations: dict[str, tuple[str, dict[str, Any]]],
    groups: list[_Group],
    group_by_step: dict[str, _Group],
    scenario: dict[str, Any],
    class_by_name: dict[str, dict[str, Any]],
    edges: dict[str, set[str]],
    issues: list[tuple[str, str, str | None]],
) -> dict[str, tuple[str, int]]:
    grouped: dict[str, _Group] = {}
    for operation_id, (_, operation) in operations.items():
        refs = [_text(ref) for ref in operation.get("stepRefs") or []]
        group = next((group_by_step[ref] for ref in refs if ref in group_by_step), None)
        if not group or any(group_by_step.get(ref) != group for ref in refs):
            issues.append(("operation", "operation stepRefs do not belong to one execution group", operation_id))
        else:
            grouped[operation_id] = group
    ranks: dict[str, tuple[str, int]] = {}
    for group in groups:
        step_order = {
            step.id: step.order
            for step in _steps(_specification_map(scenario).get(group.use_case_id, {}))
        }
        members = {
            operation_id: (class_name, operation)
            for operation_id, (class_name, operation) in operations.items()
            if grouped.get(operation_id) == group
        }
        for operation_id, (class_name, _operation) in members.items():
            if not _class_in_scope(class_by_name.get(class_name, {}), group.use_case_id):
                issues.append((
                    "operation",
                    "operation class is outside its execution group's use_case_ids scope",
                    operation_id,
                ))
        binding_edges = {
            (source_id, operation_id)
            for operation_id, (_class_name, operation) in members.items()
            for binding in operation.get("inputBindings") or []
            if isinstance(binding, dict)
            if (source_id := _binding_source_operation_id(
                _text(binding.get("sourceRef")), set(members)
            ))
        }
        _binding_order, binding_cyclic = _topological_operation_order(
            members, step_order, binding_edges
        )
        if binding_cyclic:
            issues.append((
                "producer",
                f"operation input bindings form a cycle in {group.id}: {sorted(binding_cyclic)}",
                group.id,
            ))
        stereotypes = {
            class_name: _stereotype(class_by_name.get(class_name, {}))
            for class_name, _operation in members.values()
        }
        execution_edges = _execution_order_edges(
            group, members, step_order, edges, stereotypes
        )
        ordered_ids, cyclic = _topological_operation_order(
            members, step_order, binding_edges | execution_edges
        )
        if cyclic:
            issues.append((
                "producer" if cyclic & binding_cyclic else "operation",
                f"operation execution order forms a cycle in {group.id}: {sorted(cyclic)}",
                group.id,
            ))
        for index, operation_id in enumerate(ordered_ids):
            ranks[operation_id] = (group.id, index)
    return ranks


def _topology_issues(
    operations: dict[str, tuple[str, dict[str, Any]]],
    groups: list[_Group],
    ranks: dict[str, tuple[str, int]],
    class_by_name: dict[str, dict[str, Any]],
    edges: dict[str, set[str]],
    issues: list[tuple[str, str, str | None]],
    scenario: dict[str, Any],
) -> None:
    for group in groups:
        group_operations = [
            (operation_id, class_name, operation)
            for operation_id, (class_name, operation) in operations.items()
            if ranks.get(operation_id, (None,))[0] == group.id
        ]
        ordered_ids = [
            operation_id
            for operation_id, _class_name, _operation in sorted(
                group_operations,
                key=lambda item: ranks[item[0]][1],
            )
        ]
        group_members = [
            (class_name, operation)
            for _operation_id, class_name, operation in group_operations
        ]
        for issue in _group_contract_issues(
            group, group_members, ordered_ids, class_by_name, edges, scenario
        ):
            issues.append(("operation", issue, group.id))
    for _kind, base_id, child_id in relationship_pairs(scenario):
        caller_controls = [
            class_name for operation_id, (class_name, _operation) in operations.items()
            if ranks.get(operation_id, ("",))[0].partition(":")[0] == base_id
            and _stereotype(class_by_name.get(class_name, {})) == "control"
        ]
        child_controls = [
            class_name for operation_id, (class_name, _operation) in operations.items()
            if ranks.get(operation_id, ("",))[0] == f"{child_id}:internal"
            and _stereotype(class_by_name.get(class_name, {})) == "control"
        ]
        if caller_controls and child_controls and not any(
            _reachable(edges, caller, child)
            for caller in caller_controls for child in child_controls
        ):
            issues.append(("operation", "caller Control has no reachable dependency path to internal Control", f"{base_id}->{child_id}"))


def _binding_issues(
    operations: dict[str, tuple[str, dict[str, Any]]],
    ranks: dict[str, tuple[str, int]],
    group_by_step: dict[str, _Group],
    edges: dict[str, set[str]],
    scenario: dict[str, Any],
    class_by_name: dict[str, dict[str, Any]],
    issues: list[tuple[str, str, str | None]],
) -> None:
    for operation_id, (class_name, operation) in operations.items():
        parameters = {
            _text(parameter.get("name")): _text(parameter.get("type"))
            for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
        }
        bindings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for binding in operation.get("inputBindings") or []:
            if isinstance(binding, dict):
                bindings[_text(binding.get("parameter"))].append(binding)
            else:
                issues.append(("input", "inputBinding must be an object", operation_id))
        rank = ranks.get(operation_id)
        is_internal_entry = operation_id == _internal_entry_operation_id(
            operations, ranks, class_by_name, scenario, rank
        )
        if is_internal_entry:
            for parameter, _type_name in parameters.items():
                values = bindings.get(parameter, [])
                location = f"{operation_id}#{parameter}"
                if len(values) != 1:
                    issues.append(("input", "every operation parameter needs exactly one binding", location))
                    continue
                binding = values[0]
                expected = _formal_callsite_source_ref(rank[0], parameter) if rank else ""
                if (
                    _text(binding.get("useCaseId")) != rank[0].partition(":")[0]
                    or _text(binding.get("sourceRef")) != expected
                ):
                    issues.append((
                        "input",
                        "internal reusable entry must bind each formal to its callsite source",
                        location,
                    ))
            for parameter in sorted(set(bindings) - set(parameters)):
                issues.append(("input", "inputBinding names no declared parameter", f"{operation_id}#{parameter}"))
            continue
        for parameter, type_name in parameters.items():
            values = bindings.get(parameter, [])
            location = f"{operation_id}#{parameter}"
            if len(values) != 1:
                issues.append(("input", "every operation parameter needs exactly one binding", location))
                continue
            binding = values[0]
            use_case_id, source_ref = _text(binding.get("useCaseId")), _text(binding.get("sourceRef"))
            target_rank = ranks.get(operation_id)
            if not target_rank or use_case_id != target_rank[0].partition(":")[0]:
                issues.append(("input", "binding useCaseId does not own the target operation", location))
                continue
            _source_issue(
                source_ref, parameter, type_name, operation_id, class_name, operation,
                use_case_id, target_rank, operations, ranks, group_by_step, edges,
                scenario, issues,
            )
        for parameter in sorted(set(bindings) - set(parameters)):
            issues.append(("input", "inputBinding names no declared parameter", f"{operation_id}#{parameter}"))


def _internal_entry_operation_id(
    operations: dict[str, tuple[str, dict[str, Any]]],
    ranks: dict[str, tuple[str, int]],
    class_by_name: dict[str, dict[str, Any]],
    scenario: dict[str, Any],
    rank: tuple[str, int] | None,
) -> str | None:
    """Identify an internal callable's first Control without rank-side effects."""
    if not rank or not rank[0].endswith(":internal"):
        return None
    use_case_id = rank[0].partition(":")[0]
    step_order = {
        step.id: step.order
        for step in _steps(_specification_map(scenario).get(use_case_id, {}))
    }
    candidates = [
        (operation_id, class_name, operation)
        for operation_id, (class_name, operation) in operations.items()
        if ranks.get(operation_id, (None,))[0] == rank[0]
        and _stereotype(class_by_name.get(class_name, {})) == "control"
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: _operation_sort_key((item[1], item[2]), step_order),
    )[0]


def _source_issue(
    source_ref: str, parameter: str, type_name: str, target_id: str,
    target_class: str, target: dict[str, Any], use_case_id: str,
    target_rank: tuple[str, int], operations: dict[str, tuple[str, dict[str, Any]]],
    ranks: dict[str, tuple[str, int]], group_by_step: dict[str, _Group],
    edges: dict[str, set[str]], scenario: dict[str, Any],
    issues: list[tuple[str, str, str | None]],
) -> None:
    location = f"{target_id}#{parameter}"
    if source_ref.startswith("callsite:"):
        issues.append((
            "producer",
            "callsite source is allowed only on an internal reusable Control formal",
            location,
        ))
        return
    if "#" in source_ref:
        source_id, source_parameter = source_ref.rsplit("#", 1)
        known_steps = {step.id for step in _steps(_specification_map(scenario).get(use_case_id, {}))}
        if source_id in known_steps:
            group = group_by_step.get(source_id)
            if not (
                bool(target.get("actorEntry")) and group and group.id == target_rank[0]
                and source_id == group.actor_step and source_parameter == parameter
            ):
                issues.append(("producer", "actor-step input is allowed only on its Boundary actorEntry", location))
            return
        source = operations.get(source_id)
        if not source:
            issues.append(("producer", "input sourceRef does not name a known actor step or operation", location))
            return
        source_class, source_operation = source
        source_type = next((
            _text(item.get("type")) for item in source_operation.get("parameters") or []
            if isinstance(item, dict) and _text(item.get("name")) == source_parameter
        ), "")
        if source_parameter != parameter:
            issues.append((
                "producer",
                "operation-parameter source must use the exact same parameter name",
                location,
            ))
        if source_type != type_name:
            issues.append(("producer", "operation-parameter source type is incompatible", location))
        if (
            not _reachable(edges, source_class, target_class)
            or not _earlier_in_group(source_id, target_id, ranks)
            or not _source_step_is_not_later(source_operation, target, use_case_id, scenario)
        ):
            issues.append(("producer", "operation-parameter source is future, reverse, cyclic, or unreachable", location))
        return
    source = operations.get(source_ref)
    if not source:
        issues.append(("producer", "result sourceRef does not name a known operation", location))
        return
    source_class, source_operation = source
    return_type = _text(source_operation.get("returnType"))
    if return_type.casefold() == "void" or return_type != type_name:
        issues.append(("producer", "operation-result source must be compatible and non-void", location))
    elif _is_scalar_value_type(type_name):
        issues.append((
            "producer",
            "scalar operation result cannot bind a differently named parameter without semantic identity",
            location,
        ))
    if not (
        _reachable(edges, source_class, target_class)
        or _reachable(edges, target_class, source_class)
    ):
        issues.append((
            "producer",
            "operation-result source has no caller-callee dependency path",
            location,
        ))
    if (
        not _earlier_in_group(source_ref, target_id, ranks)
        or not _source_step_is_not_later(source_operation, target, use_case_id, scenario)
    ):
        issues.append(("producer", "operation-result source is future, reverse, cyclic, or unreachable", location))


def _earlier_in_group(
    source_id: str, target_id: str, ranks: dict[str, tuple[str, int]],
) -> bool:
    source_rank, target_rank = ranks.get(source_id), ranks.get(target_id)
    return bool(
        source_rank and target_rank and source_rank[0] == target_rank[0]
        and source_rank[1] < target_rank[1]
    )


def _source_step_is_not_later(
    source: dict[str, Any], target: dict[str, Any], use_case_id: str, scenario: dict[str, Any]
) -> bool:
    order = {
        step.id: step.order
        for step in _steps(_specification_map(scenario).get(use_case_id, {}))
    }
    source_first = min(
        (order.get(_text(ref), 10**9) for ref in source.get("stepRefs") or []),
        default=10**9,
    )
    target_first = min(
        (order.get(_text(ref), 10**9) for ref in target.get("stepRefs") or []),
        default=10**9,
    )
    return source_first <= target_first
