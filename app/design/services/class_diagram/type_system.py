"""Shared type-reference rules for class proposals and persisted models."""
from __future__ import annotations

import re

PRIMITIVES = frozenset({
    "any", "bigdecimal", "biginteger", "bool", "boolean", "byte", "char",
    "character", "date", "datetime", "decimal", "double", "float", "guid",
    "instant", "int", "integer", "localdate", "localdatetime", "long",
    "number", "object", "offsetdatetime", "short", "str", "string", "time",
    "timestamp", "uuid", "void",
})
GENERIC_CONTAINERS = frozenset({
    "array", "collection", "iterable", "list", "map", "optional", "page", "set",
})
TYPE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def referenced_type_names(type_name: str) -> set[str]:
    return {
        token for token in TYPE_TOKEN.findall(type_name)
        if token.casefold() not in PRIMITIVES | GENERIC_CONTAINERS
    }


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
