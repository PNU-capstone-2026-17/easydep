"""Shared type-reference rules for class proposals and persisted models."""
from __future__ import annotations

import re
from typing import Any

PRIMITIVES = frozenset({
    "any", "bigdecimal", "biginteger", "bool", "boolean", "byte", "char",
    "character", "date", "datetime", "decimal", "double", "float", "guid",
    "instant", "int", "integer", "localdate", "localdatetime", "localtime", "long",
    "number", "object", "offsetdatetime", "short", "str", "string", "time",
    "timestamp", "uuid", "void",
})
GENERIC_CONTAINERS = frozenset({
    "array", "collection", "iterable", "list", "map", "optional", "page", "set",
})
TYPE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def structure_type_inventory() -> dict[str, tuple[str, ...] | str]:
    """The closed vocabulary supplied to structure proposals and their validator."""
    return {
        "primitives": tuple(sorted(PRIMITIVES)),
        "genericContainers": tuple(sorted(GENERIC_CONTAINERS)),
        "arraySyntax": "byte[]",
    }


def structure_type_contract() -> str:
    """Render the shared closed vocabulary for the BCE structure-generation prompt."""
    inventory = structure_type_inventory()
    primitives = ", ".join(inventory["primitives"])
    containers = ", ".join(inventory["genericContainers"])
    return (
        "Type references are closed. Allowed primitive tokens: "
        f"{primitives}. Allowed generic container tokens: {containers}. "
        "For binary or array values use the recognized `byte[]` syntax or one of those "
        "containers; do not invent an array alias. Every other type token must exactly name "
        "a declared Class or DataType in this same proposal."
    )


def referenced_type_names(type_name: str) -> set[str]:
    return {
        token for token in TYPE_TOKEN.findall(type_name)
        if token.casefold() not in PRIMITIVES | GENERIC_CONTAINERS
    }


def reachable_data_type_names(
    classes: list[dict[str, Any]], data_types: list[dict[str, Any]],
) -> set[str]:
    """Return DataTypes transitively reachable from a class contract.

    Class fields and operation signatures are the roots.  A reachable value
    object may in turn reference another DataType through one of its fields.
    Declarations that are only self-referential, or form an otherwise isolated
    cycle, are not part of the executable class contract.
    """

    declared = {
        str(item.get("name") or "").strip(): item
        for item in data_types
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    pending: set[str] = set()
    for class_item in classes:
        if not isinstance(class_item, dict):
            continue
        for field in class_item.get("fields") or []:
            pending.update(referenced_type_names(field_type(field)) & declared.keys())
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters") or []:
                if isinstance(parameter, dict):
                    pending.update(
                        referenced_type_names(str(parameter.get("type") or ""))
                        & declared.keys()
                    )
            pending.update(
                referenced_type_names(str(
                    operation.get("returnType")
                    or operation.get("return_type")
                    or ""
                ))
                & declared.keys()
            )

    reachable: set[str] = set()
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        for field in declared[name].get("fields") or []:
            pending.update(
                (referenced_type_names(field_type(field)) & declared.keys()) - reachable
            )
    return reachable


def type_is_resolved(type_name: str, names: set[str], *, allow_void: bool) -> bool:
    normalized = " ".join(str(type_name or "").split())
    if not normalized:
        return False
    if not allow_void and normalized.casefold() == "void":
        return False
    if "unknownclass" in normalized.casefold().replace(" ", ""):
        return False
    return referenced_type_names(normalized) <= names


def field_type(field: object) -> str:
    text = " ".join(str(field or "").split())
    return text.rpartition(":")[2].strip() if ":" in text else ""


def field_name(field: object) -> str:
    """Return the declared name from a persisted ``name : Type`` field."""

    text = " ".join(str(field or "").split())
    return text.partition(":")[0].strip() if ":" in text else ""


def structured_field_types(model: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Index accessible fields of declared Classes and value objects by type.

    A collaboration can pass a field of an already available structured value
    without inventing a new producer.  Keeping the index derived from the
    accepted class model makes that provenance finite and independently
    checkable.
    """

    result: dict[str, dict[str, str]] = {}
    for item in [*(model.get("Classes") or []), *(model.get("DataTypes") or [])]:
        if not isinstance(item, dict):
            continue
        type_name = str(item.get("className") or item.get("name") or "").strip()
        if not type_name:
            continue
        fields = {
            field_name(field): field_type(field)
            for field in item.get("fields") or []
            if field_name(field) and field_type(field)
        }
        if fields:
            result[type_name] = fields
    return result


def types_compatible(left: str, right: str) -> bool:
    """Compare design types without treating spelling case as new semantics."""

    def normalize(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).casefold()

    return bool(normalize(left)) and normalize(left) == normalize(right)


def projected_field_type(
    root_type: str, path: str, fields_by_type: dict[str, dict[str, str]],
) -> str:
    """Resolve a dotted field projection such as ``details.offeringId``."""

    current_type = str(root_type or "").strip()
    for component in (part.strip() for part in str(path or "").split(".")):
        if not component:
            return ""
        type_name = next(
            (name for name in fields_by_type if types_compatible(name, current_type)),
            "",
        )
        if not type_name:
            return ""
        current_type = fields_by_type[type_name].get(component, "")
        if not current_type:
            return ""
    return current_type
