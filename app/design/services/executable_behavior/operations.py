"""Pure operation-fragment boundary for the executable behavior prototype.

This module deliberately knows nothing about calls or argument bindings.  A
proposer is injected as a callable and its JSON-like result is accepted only
after deterministic validation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.design.contracts.type_system import (
    DesignTypeError,
    canonical_design_type,
    parse_type_expression,
    referenced_names,
)

ROLES = frozenset({"Boundary", "Control", "Entity"})
FRAGMENT_KEYS = frozenset({"Classes", "DataTypes"})
CLASS_SET_KEYS = frozenset({"className", "name", "stereotype", "operations"})
OPERATION_KEYS = frozenset(
    {
        "stableId",
        "name",
        "parameters",
        "returnType",
        "stepRefs",
    }
)
PARAMETER_KEYS = frozenset({"name", "type"})
DATA_TYPE_KEYS = frozenset({"name", "kind", "fields", "values"})
FIELD_KEYS = frozenset({"name", "type"})


@dataclass(frozen=True)
class OperationContext:
    use_case_id: str
    allowed_step_ids: tuple[str, ...]
    inventory: Mapping[str, Any]
    scenario: Mapping[str, Any]
    durable_entity_names: tuple[str, ...] = ()
    allowed_owner_names: tuple[str, ...] = ()
    validator_version: str = "executable-behavior.operations.v4"
    revision: int = 1

    @classmethod
    def from_payload(
        cls,
        use_case_id: str,
        inventory: Mapping[str, Any],
        *,
        scenario: Mapping[str, Any] | None = None,
        allowed_step_ids: tuple[str, ...] = (),
        durable_entity_names: tuple[str, ...] = (),
        allowed_owner_names: tuple[str, ...] = (),
        revision: int = 1,
        validator_version: str = "executable-behavior.operations.v4",
    ) -> OperationContext:
        scenario_payload = dict(
            scenario
            or {
                "useCaseId": use_case_id,
                "allowedStepIds": list(allowed_step_ids),
            }
        )
        return cls(
            use_case_id,
            tuple(allowed_step_ids),
            inventory,
            scenario_payload,
            tuple(durable_entity_names),
            tuple(allowed_owner_names),
            validator_version,
            revision,
        )


class OperationProposer(Protocol):
    def __call__(
        self,
        context: OperationContext,
        previous: Mapping[str, Any] | None = None,
        findings: tuple[str, ...] = (),
    ) -> Mapping[str, Any]: ...


class OperationValidationError(ValueError):
    def __init__(self, findings: list[str]):
        self.findings = tuple(findings)
        super().__init__("operation fragment validation failed: " + "; ".join(findings))


def propose_operations(
    proposer: OperationProposer | Callable[..., Mapping[str, Any]],
    context: OperationContext,
    *,
    previous: Mapping[str, Any] | None = None,
    findings: tuple[str, ...] = (),
) -> Mapping[str, Any]:
    """Invoke an injected proposer; no LLM or side effect is performed here."""
    value = proposer(context, previous, findings)
    if not isinstance(value, Mapping):
        raise OperationValidationError(["proposal must be a mapping"])
    return value


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def canonical_type(value: Any) -> str:
    try:
        return canonical_design_type(_text(value))
    except DesignTypeError:
        return ""


def _declared_names(context: OperationContext, fragment: Mapping[str, Any]) -> set[str]:
    names = {
        _text(x.get("className") or x.get("name"))
        for x in context.inventory.get("Classes", [])
        if isinstance(x, Mapping)
    }
    names |= {
        _text(x.get("name"))
        for x in context.inventory.get("DataTypes", [])
        if isinstance(x, Mapping)
    }
    names |= {
        _text(x.get("name"))
        for x in fragment.get("DataTypes", [])
        if isinstance(x, Mapping)
    }
    return {x for x in names if x}


def _type_closed(value: Any, names: set[str], *, allow_void: bool = False) -> bool:
    try:
        expression = parse_type_expression(_text(value))
    except DesignTypeError:
        return False
    if not allow_void and expression.kind == "scalar" and expression.name == "void":
        return False
    return not (referenced_names(expression) - names)


def _classes(fragment: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [x for x in fragment.get("Classes", []) if isinstance(x, Mapping)]


def _data_type_fields(
    context: OperationContext,
    fragment: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for item in [
        *(context.inventory.get("DataTypes", []) or []),
        *(fragment.get("DataTypes", []) or []),
    ]:
        if not isinstance(item, Mapping):
            continue
        field_types: list[str] = []
        for field in item.get("fields", []) or []:
            if isinstance(field, Mapping):
                raw_type = field.get("type")
            else:
                _, _, raw_type = _text(field).partition(":")
            normalized = canonical_type(raw_type)
            if normalized:
                field_types.append(normalized)
        result[_text(item.get("name"))] = tuple(field_types)
    return result


def _sourceability_findings(
    fragment: Mapping[str, Any],
    context: OperationContext,
    classes: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Reject parameter types the later finite source graph can never produce."""
    type_fields = _data_type_fields(context, fragment)
    actor_types: set[str] = set()
    result_types: set[str] = set()
    operation_values: list[tuple[str, str, str, list[Mapping[str, Any]]]] = []
    for class_set in _classes(fragment):
        owner = _text(class_set.get("className") or class_set.get("name"))
        role = _text(
            class_set.get("stereotype") or classes.get(owner, {}).get("stereotype")
        )
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            operation_name = _text(operation.get("name"))
            parameters = [
                value
                for value in operation.get("parameters", []) or []
                if isinstance(value, Mapping)
            ]
            operation_values.append((owner, role, operation_name, parameters))
            if role.casefold() == "boundary":
                for parameter in parameters:
                    parameter_type = canonical_type(parameter.get("type"))
                    if parameter_type:
                        actor_types.add(parameter_type)
                        actor_types.update(type_fields.get(parameter_type, ()))
            return_type = canonical_type(operation.get("returnType", "void"))
            # Boundary returns leave the system toward the actor. They are not an
            # internal value source for a synchronous child call.
            if (
                role.casefold() != "boundary"
                and return_type
                and return_type != "void"
            ):
                result_types.add(return_type)
    available = actor_types | result_types
    findings: list[str] = []
    for owner, role, operation_name, parameters in operation_values:
        if role.casefold() == "boundary":
            continue
        for parameter in parameters:
            parameter_name = _text(parameter.get("name"))
            parameter_type = canonical_type(parameter.get("type"))
            if parameter_type and parameter_type not in available:
                findings.append(
                    f"{owner}.{operation_name}#{parameter_name}: "
                    f"no finite actor-input or operation-result source for {parameter_type}"
                )
    return findings


def _inventory_classes(context: OperationContext) -> dict[str, Mapping[str, Any]]:
    return {
        _text(x.get("className") or x.get("name")): x
        for x in context.inventory.get("Classes", [])
        if isinstance(x, Mapping)
    }


def validate_operation_payload(
    fragment: Mapping[str, Any], context: OperationContext
) -> list[str]:
    findings: list[str] = []
    if not isinstance(fragment, Mapping):
        return ["fragment must be a mapping"]
    scenario_use_cases = [
        item
        for item in (
            context.scenario.get("useCases") or context.scenario.get("use_cases") or []
        )
        if isinstance(item, Mapping)
    ]
    matching_use_cases = [
        item
        for item in scenario_use_cases
        if _text(item.get("useCaseId") or item.get("id")) == context.use_case_id
    ]
    if scenario_use_cases and len(matching_use_cases) != 1:
        findings.append(
            "operation fragment use case must exist exactly once in scenario"
        )
    if matching_use_cases:
        scenario_steps = {
            _text(item.get("stepId") or item.get("id"))
            for item in matching_use_cases[0].get("steps", []) or []
            if isinstance(item, Mapping) and _text(item.get("stepId") or item.get("id"))
        }
        if scenario_steps and set(context.allowed_step_ids) != scenario_steps:
            findings.append("allowed operation steps must match the scenario slice")
    unknown_fragment_keys = set(fragment) - FRAGMENT_KEYS
    if unknown_fragment_keys:
        findings.append(
            "operation-only fragment has unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown_fragment_keys))
        )
    classes = _inventory_classes(context)
    allowed = set(context.allowed_step_ids)
    if not allowed:
        allowed = {
            _text(x) for x in context.inventory.get("allowedStepRefs", []) if _text(x)
        }
    declared = _declared_names(context, fragment)
    covered: set[str] = set()
    seen_operation_names: set[tuple[str, str]] = set()
    for class_set in _classes(fragment):
        unknown_class_keys = set(class_set) - CLASS_SET_KEYS
        if unknown_class_keys:
            findings.append(
                "class operation set has unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown_class_keys))
            )
        name = _text(class_set.get("className") or class_set.get("name"))
        if not name or name not in classes:
            findings.append(f"unknown operation owner: {name or '<empty>'}")
            continue
        if context.allowed_owner_names and name not in context.allowed_owner_names:
            findings.append(f"operation owner is outside this use-case slice: {name}")
        role = _text(class_set.get("stereotype") or classes[name].get("stereotype"))
        if role and role not in ROLES:
            findings.append(f"unsupported owner stereotype: {name}:{role}")
        for operation in class_set.get("operations", []):
            if not isinstance(operation, Mapping):
                findings.append(f"{name}: operation must be an object")
                continue
            unknown_operation_keys = set(operation) - OPERATION_KEYS
            if unknown_operation_keys:
                findings.append(
                    f"{name}: operation has unsupported fields: "
                    + ", ".join(sorted(str(item) for item in unknown_operation_keys))
                )
            op_name = _text(operation.get("name"))
            if not op_name:
                findings.append(f"{name}: operation name is required")
            operation_identity = (name, op_name)
            if operation_identity in seen_operation_names:
                findings.append(f"duplicate operation name: {name}.{op_name}")
            seen_operation_names.add(operation_identity)
            parameters = operation.get("parameters") or []
            parameter_names: set[str] = set()
            for parameter in parameters:
                if not isinstance(parameter, Mapping):
                    findings.append(f"{name}.{op_name}: parameter must be an object")
                    continue
                unknown_parameter_keys = set(parameter) - PARAMETER_KEYS
                if unknown_parameter_keys:
                    findings.append(
                        f"{name}.{op_name}: parameter has unsupported fields: "
                        + ", ".join(
                            sorted(str(item) for item in unknown_parameter_keys)
                        )
                    )
                parameter_name = _text(parameter.get("name"))
                if not parameter_name or parameter_name in parameter_names:
                    findings.append(f"{name}.{op_name}: parameter names must be unique")
                parameter_names.add(parameter_name)
                if not _type_closed(parameter.get("type"), declared):
                    findings.append(
                        f"{name}.{op_name}#{parameter_name}: unresolved parameter type"
                    )
            return_type = canonical_type(operation.get("returnType", "void"))
            if not _type_closed(return_type, declared, allow_void=True):
                findings.append(f"{name}.{op_name}: unresolved return type")
            refs = {_text(x) for x in operation.get("stepRefs", []) if _text(x)}
            invalid_refs = refs - allowed if allowed else set()
            if not refs:
                findings.append(
                    f"{name}.{op_name}: stepRefs must not be empty"
                )
            elif invalid_refs:
                findings.append(
                    f"{name}.{op_name}: invalid stepRefs are "
                    f"[{', '.join(sorted(invalid_refs))}]; exact allowedStepIds are "
                    f"[{', '.join(sorted(allowed))}]"
                )
            covered |= refs
    if allowed - covered:
        findings.append(
            "operations do not cover steps: " + ", ".join(sorted(allowed - covered))
        )
    durable = set(context.durable_entity_names)
    owners = {
        _text(class_set.get("className") or class_set.get("name"))
        for class_set in _classes(fragment)
        if any(isinstance(op, Mapping) for op in class_set.get("operations", []) or [])
    }
    missing = durable - owners
    if missing:
        findings.append(
            "durable Entity responsibility missing: " + ", ".join(sorted(missing))
        )
    local_type_names: set[str] = set()
    inventory_names = set(classes) | {
        _text(item.get("name"))
        for item in context.inventory.get("DataTypes", [])
        if isinstance(item, Mapping)
    }
    for item in fragment.get("DataTypes", []) or []:
        if not isinstance(item, Mapping):
            findings.append("DataType must be an object")
            continue
        unknown_type_keys = set(item) - DATA_TYPE_KEYS
        if unknown_type_keys:
            findings.append(
                "DataType has unsupported fields: "
                + ", ".join(sorted(str(value) for value in unknown_type_keys))
            )
        name, kind = _text(item.get("name")), _text(item.get("kind"))
        fields, values = item.get("fields") or [], item.get("values") or []
        if not name or kind not in {"valueObject", "enumeration"}:
            findings.append(f"invalid local DataType declaration: {name or '<empty>'}")
        if name in local_type_names or name in inventory_names:
            findings.append(f"duplicate or reserved DataType name: {name or '<empty>'}")
        local_type_names.add(name)
        if kind == "valueObject" and (not fields or values):
            findings.append(f"valueObject {name} requires fields and no values")
        if kind == "enumeration" and (not values or fields):
            findings.append(f"enumeration {name} requires values and no fields")
        for field in fields:
            if isinstance(field, Mapping):
                unknown_field_keys = set(field) - FIELD_KEYS
                if unknown_field_keys:
                    findings.append(
                        f"{name}: field has unsupported fields: "
                        + ", ".join(sorted(str(value) for value in unknown_field_keys))
                    )
                typ = field.get("type")
            else:
                _, _, typ = _text(field).partition(":")
            if not _type_closed(typ, declared):
                findings.append(f"{name}: unresolved field type")
    findings.extend(_sourceability_findings(fragment, context, classes))
    return findings


def validated_operation_fragment(
    fragment: Mapping[str, Any], context: OperationContext
):
    findings = validate_operation_payload(fragment, context)
    if findings:
        raise OperationValidationError(findings)
    from .contracts import ValidatedOperationFragment, make_provenance

    provenance = make_provenance(
        revision=context.revision,
        inputs={
            "scenario": context.scenario,
            "inventory": context.inventory,
            "allowed_steps": context.allowed_step_ids,
            "allowed_owners": context.allowed_owner_names,
            "durable_entities": context.durable_entity_names,
        },
        subject=dict(fragment),
        validator_version=context.validator_version,
    )
    return ValidatedOperationFragment(
        useCaseId=context.use_case_id, payload=dict(fragment), provenance=provenance
    )


__all__ = [
    "OperationContext",
    "OperationProposer",
    "OperationValidationError",
    "canonical_type",
    "propose_operations",
    "validate_operation_payload",
    "validated_operation_fragment",
]
