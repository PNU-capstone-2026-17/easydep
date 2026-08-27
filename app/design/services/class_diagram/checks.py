"""Pure deterministic checks for the interaction-design vertical slice."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.validation import CheckSpec, Finding
from app.design.schemas.class_model import BCEModel, canonical_operation_id
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    text,
)
from app.design.services.class_diagram.type_system import (
    field_name,
    field_type,
    projected_field_type,
    referenced_type_names,
    structured_field_types,
    type_is_resolved,
    types_compatible,
)


def class_name(item: dict[str, Any]) -> str:
    return text(item.get("className") or item.get("class_name"))


def runtime_value_source(type_expression: str) -> str:
    """Return the explicit runtime source supported by a temporal type."""

    normalized = re.sub(r"\s+", "", text(type_expression)).casefold()
    normalized = normalized.removeprefix("java.time.")
    return {
        "date": "runtime#currentDate",
        "localdate": "runtime#currentDate",
        "datetime": "runtime#currentDateTime",
        "localdatetime": "runtime#currentDateTime",
        "offsetdatetime": "runtime#currentDateTime",
        "zoneddatetime": "runtime#currentDateTime",
        "instant": "runtime#currentInstant",
        "timestamp": "runtime#currentInstant",
    }.get(normalized, "")


def type_can_default(type_expression: str) -> bool:
    normalized = re.sub(r"\s+", "", text(type_expression)).casefold()
    return normalized.startswith("optional<") or normalized.startswith("optional[")


def optional_inner_type(type_expression: str) -> str:
    normalized = re.sub(r"\s+", "", text(type_expression))
    match = re.fullmatch(r"(?i:optional)[<\[](.+)[>\]]", normalized)
    return match.group(1) if match else ""


def derived_value_source(target_type: str, field_sources: dict[str, str]) -> str:
    assignments = ",".join(
        f"{name}={field_sources[name]}" for name in sorted(field_sources)
    )
    return f"derived#{target_type}({assignments})"


def derived_value_parts(source_ref: str) -> tuple[str, dict[str, str]]:
    match = re.fullmatch(r"derived#([A-Za-z_][A-Za-z0-9_]*)\((.*)\)", source_ref)
    if not match:
        return "", {}
    assignments: dict[str, str] = {}
    if match.group(2):
        for raw in match.group(2).split(","):
            name, separator, value = raw.partition("=")
            if not separator or not name or not value or name in assignments:
                return "", {}
            assignments[name] = value
    return match.group(1), assignments


def _structured_value_is_derivable(
    target_type: str,
    named_source_types: dict[str, set[str]],
    fields_by_type: dict[str, dict[str, str]],
) -> bool:
    target_fields = fields_by_type.get(target_type, {})
    if not target_fields:
        return False
    for name, expected in target_fields.items():
        available = named_source_types.get(name.casefold(), set())
        if any(types_compatible(source, expected) for source in available):
            continue
        if runtime_value_source(expected) or type_can_default(expected):
            continue
        return False
    return True


def operation_catalog(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for class_item in model.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        owner = class_name(class_item)
        stereotype = text(class_item.get("stereotype")).casefold()
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_id = text(operation.get("operationId"))
            if operation_id:
                result[operation_id] = {
                    **operation,
                    "className": owner,
                    "stereotype": stereotype,
                }
    return result


def _inventory_names(
    inventory: dict[str, Any], _index: ScenarioIndex,
) -> list[Finding]:
    findings: list[Finding] = []
    names: list[str] = []
    for item in inventory.get("Classes") or []:
        if not isinstance(item, dict):
            continue
        name = class_name(item)
        names.append(name)
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", name) or "unknownclass" in name.casefold():
            findings.append(Finding(
                "class.inventory.names", "className must be concrete PascalCase", name,
            ))
    for item in inventory.get("DataTypes") or []:
        if isinstance(item, dict):
            names.append(text(item.get("name")))
    if len(names) != len(set(names)):
        findings.append(Finding(
            "class.inventory.names", "class and DataType names must be unique",
        ))
    return findings


def _inventory_types(
    inventory: dict[str, Any], _index: ScenarioIndex,
) -> list[Finding]:
    classes = {
        class_name(item): item for item in inventory.get("Classes") or []
        if isinstance(item, dict)
    }
    data_types = {
        text(item.get("name")): item for item in inventory.get("DataTypes") or []
        if isinstance(item, dict)
    }
    declared = set(classes) | set(data_types)
    findings: list[Finding] = []
    for name, item in classes.items():
        stereotype = text(item.get("stereotype"))
        raw_fields = list(item.get("fields") or [])
        identifiers = list(item.get("identifier") or [])
        values = list(item.get("values") or [])
        if stereotype == "Entity" and (not raw_fields or values):
            findings.append(Finding(
                "class.inventory.types", "Entity requires typed persistent fields and no literals", name,
            ))
        if stereotype in {"Boundary", "Control"} and (
            raw_fields or identifiers or values
        ):
            findings.append(Finding(
                "class.inventory.types", "Boundary and Control cannot retain fields, identifiers, or literals", name,
            ))
        field_names = {field_name(value) for value in raw_fields}
        for value in raw_fields:
            if not field_name(value) or not type_is_resolved(
                field_type(value), declared, allow_void=False,
            ):
                findings.append(Finding(
                    "class.inventory.types", f"unresolved field declaration: {value}", name,
                ))
        if not set(identifiers) <= field_names:
            findings.append(Finding(
                "class.inventory.types", "Entity identifiers must name declared fields", name,
            ))
    for name, item in data_types.items():
        kind = text(item.get("kind"))
        raw_fields = list(item.get("fields") or [])
        values = list(item.get("values") or [])
        identifiers = list(item.get("identifier") or [])
        if kind == "valueObject" and (
            not raw_fields or values or identifiers
        ):
            findings.append(Finding(
                "class.inventory.types", "valueObject requires typed fields only", name,
            ))
        if kind == "enumeration" and (raw_fields or identifiers or not values):
            findings.append(Finding(
                "class.inventory.types", "enumeration requires values and no fields", name,
            ))
        for value in raw_fields:
            if not field_name(value) or not type_is_resolved(
                field_type(value), declared, allow_void=False,
            ):
                findings.append(Finding(
                    "class.inventory.types", f"unresolved DataType field: {value}", name,
                ))
    return findings


def _inventory_relationships(
    inventory: dict[str, Any], _index: ScenarioIndex,
) -> list[Finding]:
    entities = {
        class_name(item) for item in inventory.get("Classes") or []
        if isinstance(item, dict) and text(item.get("stereotype")) == "Entity"
    }
    findings: list[Finding] = []
    seen: set[frozenset[str]] = set()
    for relationship in inventory.get("Relationships") or []:
        if not isinstance(relationship, dict):
            continue
        source = text(relationship.get("source"))
        target = text(relationship.get("target"))
        location = f"{source}->{target}"
        if source not in entities or target not in entities:
            findings.append(Finding(
                "class.inventory.relationships",
                "structural relationships may connect only Entity classes",
                location,
            ))
        if not text(relationship.get("sourceMultiplicity")) or not text(
            relationship.get("targetMultiplicity")
        ):
            findings.append(Finding(
                "class.inventory.relationships",
                "both endpoint multiplicities are required",
                location,
            ))
        pair = frozenset((source, target))
        if pair in seen:
            findings.append(Finding(
                "class.inventory.relationships",
                "one semantic relationship must not be emitted in both directions",
                location,
            ))
        seen.add(pair)
    return findings


def _inventory_scope(
    inventory: dict[str, Any], index: ScenarioIndex,
) -> list[Finding]:
    """Keep each operation prompt inside an inventory-declared UC slice."""

    known = {use_case.id for use_case in index.use_cases}
    findings: list[Finding] = []
    items = [
        item for item in inventory.get("Classes") or [] if isinstance(item, dict)
    ]
    scopes = {
        class_name(item) or text(item.get("name")): set(item.get("useCaseIds") or [])
        for item in items
    }
    for name, scope in scopes.items():
        if not scope or not scope <= known:
            findings.append(Finding(
                "class.inventory.scope",
                "useCaseIds must be a non-empty subset of supplied use cases",
                name,
            ))
    for use_case in index.use_cases:
        selected = [
            item for item in inventory.get("Classes") or []
            if isinstance(item, dict) and use_case.id in set(item.get("useCaseIds") or [])
        ]
        stereotypes = {text(item.get("stereotype")) for item in selected}
        if use_case.primary_actor and "Boundary" not in stereotypes:
            findings.append(Finding(
                "class.inventory.scope",
                "actor-driven use case scope requires a Boundary candidate",
                use_case.id,
            ))
        if "Control" not in stereotypes:
            findings.append(Finding(
                "class.inventory.scope",
                "use case scope requires a Control candidate",
                use_case.id,
            ))
    return findings


INVENTORY_CHECKS = (
    CheckSpec("class.inventory.names", _inventory_names),
    CheckSpec("class.inventory.types", _inventory_types),
    CheckSpec("class.inventory.relationships", _inventory_relationships),
    CheckSpec("class.inventory.scope", _inventory_scope),
)


@dataclass(frozen=True)
class OperationContext:
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


OPERATION_CHECKS = (
    CheckSpec("class.operation.data-types", _operation_data_types),
    CheckSpec("class.operation.references", _operation_references),
    CheckSpec("class.operation.coverage", _operation_coverage),
    CheckSpec("class.operation.execution-groups", _operation_groups),
    CheckSpec("class.operation.results", _operation_results),
    CheckSpec("class.operation.value-flow", _operation_value_flow),
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
    call_by_id = {text(item.get("callId")): item for item in calls}
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


def final_model_findings(model: dict[str, Any], index: ScenarioIndex) -> list[Finding]:
    findings: list[Finding] = []
    try:
        BCEModel.model_validate(model)
    except Exception as error:  # Pydantic supplies the exact schema location.
        return [Finding("class.model.schema", str(error), "BCEModel", origin="schema")]
    operations = operation_catalog(model)
    for operation_id, operation in operations.items():
        if operation_id != canonical_operation_id(
            operation["className"], text(operation.get("name")), list(operation.get("parameters") or []),
        ):
            findings.append(Finding(
                "class.model.operation-ids", "operationId is not canonical", operation_id,
            ))
        normalized_name = re.sub(
            r"[^a-z0-9]", "", text(operation.get("name")).casefold(),
        )
        if normalized_name in {"none", "noop", "notapplicable"}:
            findings.append(Finding(
                "class.model.operation-names",
                "operation name must describe concrete behavior",
                operation_id,
            ))
    collaborations = {
        text(item.get("collaborationId")): item for item in model.get("Collaborations") or []
        if isinstance(item, dict)
    }
    expected = {group.id for group in index.groups}
    if set(collaborations) != expected:
        findings.append(Finding(
            "class.model.collaboration-coverage",
            f"collaborations must exactly cover execution groups; missing={sorted(expected - set(collaborations))}, extra={sorted(set(collaborations) - expected)}",
            "Collaborations",
        ))
    for group in index.groups:
        collaboration = collaborations.get(group.id)
        if collaboration:
            context = CollaborationContext(index, model, group)
            findings.extend(_collaboration_contract(collaboration, context))
            findings.extend(_collaboration_order(collaboration, context))
            findings.extend(_collaboration_bindings(collaboration, context))
    return findings



