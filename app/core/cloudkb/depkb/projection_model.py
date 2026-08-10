"""중립 개념과 벤더 리소스 사이의 다대다·합성 대응을 검증한다."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REPRESENTATIONS = {"independent", "embedded", "composite-member"}
COVERAGE = {"full", "partial"}
PATH = Path(__file__).with_name("provider-projections.json")


def validate_projection(document: dict[str, Any]) -> None:
    if document.get("schemaVersion") != "easydep-provider-projection/v1":
        raise ValueError("unsupported provider projection")
    for provider, projection in document.get("providers", {}).items():
        if provider not in {"aws", "azure", "gcp"}:
            raise ValueError("unsupported provider")
        mappings = projection.get("mappings") or []
        realizations = projection.get("realizations") or []
        if not mappings or not realizations:
            raise ValueError("projection requires mappings and realizations")
        mapping_ids = set()
        for mapping in mappings:
            if mapping["id"] in mapping_ids:
                raise ValueError("duplicate mapping id")
            mapping_ids.add(mapping["id"])
            if mapping.get("representation") not in REPRESENTATIONS:
                raise ValueError("invalid representation")
            if mapping.get("coverage") not in COVERAGE:
                raise ValueError("invalid mapping coverage")
            if not mapping.get("nativePath") or not mapping.get("neutralConceptId"):
                raise ValueError("mapping requires both sides")
            if mapping["representation"] == "embedded" and not mapping.get("ownerResourceId"):
                raise ValueError("embedded mapping requires its owner")
            evidence = mapping.get("terraformEvidence") or {}
            if not evidence.get("resourceTypes") and not (
                evidence.get("ownerType") and evidence.get("ownerBlock")
            ):
                raise ValueError("mapping requires machine-checkable Terraform evidence")
        for realization in realizations:
            members = realization.get("mappingIds") or []
            if not members or not set(members) <= mapping_ids:
                raise ValueError("realization references unknown mappings")
            if realization.get("composition") not in {"single-with-embedded", "multi-resource"}:
                raise ValueError("invalid realization composition")
            if not realization.get("variant") or not realization.get("capabilityIds"):
                raise ValueError("realization requires a scoped variant and capabilities")


def projection_gaps(document: dict[str, Any]) -> list[dict[str, str]]:
    validate_projection(document)
    gaps: list[dict[str, str]] = []
    for provider, projection in document["providers"].items():
        for mapping in projection["mappings"]:
            if mapping.get("boundaryStatus") != "confirmed":
                gaps.append({
                    "provider": provider,
                    "mappingId": mapping["id"],
                    "boundaryStatus": mapping.get("boundaryStatus", "missing"),
                })
    return gaps


@lru_cache(maxsize=1)
def load_projection() -> dict[str, Any]:
    document = json.loads(PATH.read_text(encoding="utf-8"))
    validate_projection(document)
    return document


def capability_realizations(provider: str, capability_id: str) -> tuple[dict[str, Any], ...]:
    projection = load_projection()["providers"].get(provider)
    if projection is None:
        raise ValueError(f"unsupported provider projection: {provider}")
    mappings = {item["id"]: item for item in projection["mappings"]}
    selected = []
    for realization in projection["realizations"]:
        if capability_id not in realization["capabilityIds"]:
            continue
        selected.append({
            **realization,
            "components": [mappings[mapping_id] for mapping_id in realization["mappingIds"]],
        })
    return tuple(selected)
