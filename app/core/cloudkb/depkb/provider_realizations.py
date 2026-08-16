"""선택된 capability를 CSP native 구성요소로 구체화한다."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REPRESENTATIONS = {"independent", "embedded", "composite-member"}
PATH = Path(__file__).with_name("provider-realizations.json")


def validate_realizations(document: dict[str, Any]) -> None:
    if document.get("schemaVersion") != "easydep-provider-realizations/v1":
        raise ValueError("unsupported provider realization catalog")
    for provider, catalog in document.get("providers", {}).items():
        if provider not in {"aws", "azure", "gcp"}:
            raise ValueError("unsupported provider")
        components = catalog.get("components") or []
        realizations = catalog.get("realizations") or []
        if not components or not realizations:
            raise ValueError("provider catalog requires components and realizations")
        component_ids: set[str] = set()
        for component in components:
            component_id = component.get("id")
            if not component_id or component_id in component_ids:
                raise ValueError("component ids must be present and unique")
            component_ids.add(component_id)
            if component.get("representation") not in REPRESENTATIONS:
                raise ValueError("invalid component representation")
            if not component.get("nativePath"):
                raise ValueError("component requires a provider-native path")
            if component["representation"] == "embedded" and not component.get(
                "ownerResourceId"
            ):
                raise ValueError("embedded component requires its owner")
            evidence = component.get("terraformEvidence") or {}
            if not evidence.get("resourceTypes") and not (
                evidence.get("ownerType") and evidence.get("ownerBlock")
            ):
                raise ValueError("component requires machine-checkable Terraform evidence")
        for realization in realizations:
            members = realization.get("componentIds") or []
            if not members or not set(members) <= component_ids:
                raise ValueError("realization references unknown components")
            if realization.get("composition") not in {
                "single-with-embedded",
                "multi-resource",
            }:
                raise ValueError("invalid realization composition")
            if not realization.get("variant") or not realization.get("capabilityIds"):
                raise ValueError("realization requires a variant and capabilities")


def realization_gaps(document: dict[str, Any]) -> list[dict[str, str]]:
    validate_realizations(document)
    gaps: list[dict[str, str]] = []
    for provider, catalog in document["providers"].items():
        for component in catalog["components"]:
            if component.get("boundaryStatus") != "confirmed":
                gaps.append({
                    "provider": provider,
                    "componentId": component["id"],
                    "boundaryStatus": component.get("boundaryStatus", "missing"),
                })
    return gaps


@lru_cache(maxsize=1)
def load_realizations() -> dict[str, Any]:
    document = json.loads(PATH.read_text(encoding="utf-8"))
    validate_realizations(document)
    return document


def capability_realizations(provider: str, capability_id: str) -> tuple[dict[str, Any], ...]:
    catalog = load_realizations()["providers"].get(provider)
    if catalog is None:
        raise ValueError(f"unsupported provider: {provider}")
    components = {item["id"]: item for item in catalog["components"]}
    selected = []
    for realization in catalog["realizations"]:
        if capability_id not in realization["capabilityIds"]:
            continue
        selected.append({
            **realization,
            "components": [components[item] for item in realization["componentIds"]],
        })
    return tuple(selected)
