"""Deterministic assembly of operation fragments into one closed BCE catalog draft."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.design.schemas.class_model import BCEModel, canonical_operation_id
from app.design.services.executable_behavior.contracts import (
    ValidatedCatalogDraft,
    ValidatedOperationFragment,
    canonical_digest,
    make_provenance,
    provenance_matches_subject,
)
from app.design.services.executable_behavior.operations import canonical_type


class CatalogValidationError(ValueError):
    """The fragments cannot share one closed operation and type namespace."""

    def __init__(self, findings: list[str]):
        self.findings = tuple(findings)
        super().__init__("catalog validation failed: " + "; ".join(findings))


@dataclass(frozen=True)
class CatalogResult:
    """Normalized catalog plus the validated, but not accepted, boundary."""

    payload: dict[str, Any]
    digest: str
    validated: ValidatedCatalogDraft


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _field_declaration(value: Mapping[str, Any] | str) -> str:
    if isinstance(value, Mapping):
        name = _text(value.get("name"))
        raw_type = value.get("type")
    else:
        name, separator, raw_type = str(value).partition(":")
        if not separator:
            return str(value)
    return f"{name.strip()} : {canonical_type(raw_type)}"


def _inventory_class(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "className": _text(value.get("className") or value.get("name")),
        "stereotype": _text(value.get("stereotype") or value.get("kind")),
        "description": _text(value.get("description")),
        "fields": [
            _field_declaration(field)
            for field in value.get("fields", []) or []
            if isinstance(field, (str, Mapping))
        ],
        "use_case_ids": list(
            value.get("use_case_ids") or value.get("useCaseIds") or []
        ),
        "identifier": list(value.get("identifier") or []),
        "operations": [],
    }


def _data_type(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": _text(value.get("name")),
        "kind": _text(value.get("kind")),
        "fields": [
            _field_declaration(field)
            for field in value.get("fields", []) or []
            if isinstance(field, (str, Mapping))
        ],
        "values": [str(item) for item in value.get("values", []) or []],
    }


def _operation(owner: str, value: Mapping[str, Any]) -> dict[str, Any]:
    parameters = [
        {
            "name": _text(parameter.get("name")),
            "type": canonical_type(parameter.get("type")),
        }
        for parameter in value.get("parameters", []) or []
        if isinstance(parameter, Mapping)
    ]
    operation = {
        "operationId": canonical_operation_id(
            owner, _text(value.get("name")), parameters
        ),
        "name": _text(value.get("name")),
        "parameters": parameters,
        "returnType": canonical_type(value.get("returnType", "void")),
        "stepRefs": sorted({str(item) for item in value.get("stepRefs", []) or []}),
    }
    if stable_id := value.get("stableId"):
        operation["stableId"] = str(stable_id)
    return operation


def _method_contract(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(
            (str(parameter.get("name")), canonical_type(parameter.get("type")))
            for parameter in value.get("parameters", []) or []
            if isinstance(parameter, Mapping)
        ),
        canonical_type(value.get("returnType", "void")),
    )


def _type_contract(value: Mapping[str, Any]) -> tuple[Any, ...]:
    normalized = _data_type(value)
    return (
        normalized["kind"],
        tuple(normalized["fields"]),
        tuple(normalized["values"]),
    )


def assemble_catalog(
    fragments: Iterable[ValidatedOperationFragment],
    *,
    inventory: Mapping[str, Any],
    scenario: Mapping[str, Any],
    validator_version: str = "executable-behavior.catalog.v1",
    revision: int = 1,
) -> CatalogResult:
    """Build one catalog transaction without claiming executable acceptance."""
    findings: list[str] = []
    classes: dict[str, dict[str, Any]] = {}
    for item in inventory.get("Classes", []) or []:
        if not isinstance(item, Mapping):
            findings.append("inventory class must be an object")
            continue
        candidate = _inventory_class(item)
        name = candidate["className"]
        if not name or name in classes:
            findings.append(f"invalid or duplicate inventory class: {name or '<empty>'}")
            continue
        classes[name] = candidate

    data_types: dict[str, dict[str, Any]] = {}
    for item in inventory.get("DataTypes", []) or []:
        if not isinstance(item, Mapping):
            findings.append("inventory DataType must be an object")
            continue
        candidate = _data_type(item)
        name = candidate["name"]
        if not name or name in data_types:
            findings.append(f"invalid or duplicate inventory DataType: {name or '<empty>'}")
            continue
        data_types[name] = candidate

    fragment_inputs: dict[str, Any] = {}
    expected_scenario = canonical_digest(scenario)
    expected_inventory = canonical_digest(inventory)
    seen_use_cases: set[str] = set()
    for position, item in enumerate(fragments, start=1):
        if not isinstance(item, ValidatedOperationFragment):
            findings.append("catalog accepts only validated operation fragments")
            continue
        if not provenance_matches_subject(item.provenance, item.payload):
            findings.append(
                f"operation fragment changed after validation: {item.use_case_id}"
            )
            continue
        if item.provenance.input_digests.get("scenario") != expected_scenario:
            findings.append(
                f"operation fragment uses a different scenario: {item.use_case_id}"
            )
        if item.provenance.input_digests.get("inventory") != expected_inventory:
            findings.append(
                f"operation fragment uses a different inventory: {item.use_case_id}"
            )
        if item.use_case_id in seen_use_cases:
            findings.append(f"duplicate operation fragment: {item.use_case_id}")
        seen_use_cases.add(item.use_case_id)
        fragment = item.payload
        fragment_inputs[f"fragment:{item.use_case_id}:{position}"] = item.model_dump(
            by_alias=True
        )
        for class_set in fragment.get("Classes", []) or []:
            if not isinstance(class_set, Mapping):
                findings.append("class operation set must be an object")
                continue
            owner = _text(class_set.get("className") or class_set.get("name"))
            target = classes.get(owner)
            if target is None:
                findings.append(
                    f"operation owner is absent from inventory: {owner or '<empty>'}"
                )
                continue
            by_name: dict[str, dict[str, Any]] = {
                str(operation.get("name")): operation
                for operation in target["operations"]
                if isinstance(operation, dict)
            }
            for raw_operation in class_set.get("operations", []) or []:
                if not isinstance(raw_operation, Mapping):
                    findings.append(f"{owner}: operation must be an object")
                    continue
                candidate = _operation(owner, raw_operation)
                name = candidate["name"]
                previous = by_name.get(name)
                if previous is None:
                    target["operations"].append(candidate)
                    by_name[name] = candidate
                elif _method_contract(previous) != _method_contract(candidate):
                    findings.append(f"operation contract collision: {owner}.{name}")
                else:
                    previous["stepRefs"] = sorted({
                        *previous.get("stepRefs", []),
                        *candidate.get("stepRefs", []),
                    })
        for raw_type in fragment.get("DataTypes", []) or []:
            if not isinstance(raw_type, Mapping):
                findings.append("DataType declaration must be an object")
                continue
            candidate = _data_type(raw_type)
            name = candidate["name"]
            previous = data_types.get(name)
            if previous is None:
                data_types[name] = candidate
            elif _type_contract(previous) != _type_contract(candidate):
                findings.append(f"DataType contract collision: {name}")

    if findings:
        raise CatalogValidationError(findings)

    try:
        model = BCEModel.model_validate({
            "Classes": sorted(classes.values(), key=lambda item: item["className"]),
            "DataTypes": sorted(data_types.values(), key=lambda item: item["name"]),
            "Relationships": deepcopy(list(inventory.get("Relationships", []) or [])),
            "Collaborations": [],
        })
    except (TypeError, ValueError) as error:
        raise CatalogValidationError([f"final catalog is invalid: {error}"]) from error
    payload = model.model_dump(by_alias=True)
    provenance = make_provenance(
        revision=revision,
        inputs={
            "scenario": scenario,
            "inventory": inventory,
            **fragment_inputs,
        },
        subject=payload,
        validator_version=validator_version,
    )
    validated = ValidatedCatalogDraft(payload=payload, provenance=provenance)
    return CatalogResult(payload, canonical_digest(payload), validated)


def catalog_digest(payload: Mapping[str, Any]) -> str:
    """Return the content identity independently of validation provenance."""
    return canonical_digest(payload)


__all__ = [
    "CatalogResult",
    "CatalogValidationError",
    "assemble_catalog",
    "catalog_digest",
]
