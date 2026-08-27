"""전역 BCE 인벤토리의 이름·타입·관계·범위를 결정론적으로 검사한다."""
from __future__ import annotations

import re
from typing import Any

from app.core.validation import CheckSpec, Finding, ValidationReport, run_checks
from app.design.services.class_diagram.scenario import ScenarioIndex, text
from app.design.services.class_diagram.type_system import (
    field_name,
    field_type,
    type_is_resolved,
)
from app.design.services.class_diagram.validation.model import class_name


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


def validate_inventory(
    inventory: dict[str, Any], index: ScenarioIndex
) -> ValidationReport:
    """수락 전 인벤토리를 변경하지 않고 검사한다.

    Args:
        inventory: 별칭 JSON 형태의 BCE 인벤토리 후보다.
        index: 허용된 유스케이스 식별자를 제공하는 시나리오 인덱스다.

    Returns:
        이름, 타입, 관계, 범위 규칙을 등록 순서로 담은 보고서다.
    """
    return run_checks(INVENTORY_CHECKS, inventory or {}, index)


__all__ = ["INVENTORY_CHECKS", "validate_inventory"]
