"""Validate focused provider projections of audited neutral hypotheses."""

from __future__ import annotations

from collections import Counter
from typing import Any

MAPPING_KINDS = frozenset({"equivalent", "partial", "composite", "unmatched"})
EVIDENCE_STRENGTHS = frozenset({"schemaCandidate", "nativeDocumentation", "measured"})


def validate_projection_protocol(protocol: dict[str, Any], crosswalk: dict[str, Any]) -> None:
    if protocol.get("schemaVersion") != "easydep-neutral-projection-protocol/v1":
        raise ValueError("unsupported neutral projection protocol")
    resource = protocol.get("resourceConceptIds")
    structural = protocol.get("structuralConceptIds")
    if not isinstance(resource, list) or not isinstance(structural, list):
        raise TypeError("projection concept partitions must be arrays")
    crosswalk_ids = {item["id"] for item in crosswalk["concepts"]}
    if set(resource) & set(structural) or set(resource) | set(structural) != crosswalk_ids:
        raise ValueError("projection protocol must partition every crosswalk concept")


def validate_provider_projection(
    packet: dict[str, Any],
    *,
    protocol: dict[str, Any],
    crosswalk: dict[str, Any],
    inventory: dict[str, Any],
) -> None:
    validate_projection_protocol(protocol, crosswalk)
    if packet.get("schemaVersion") != "easydep-provider-hypotheses/v1":
        raise ValueError("unsupported provider hypothesis schemaVersion")
    provider = packet.get("provider")
    if provider != inventory.get("provider") or provider not in {"aws", "azure", "gcp"}:
        raise ValueError("provider projection does not match native inventory")
    if packet.get("inventorySource") != inventory.get("source"):
        raise ValueError("provider projection references a different native inventory source")
    mappings = packet.get("mappings")
    if not isinstance(mappings, list):
        raise TypeError("provider projection mappings must be an array")
    ids = [str(item.get("conceptId") or "") for item in mappings]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicates or set(ids) != set(protocol["resourceConceptIds"]):
        raise ValueError("provider projection must map every resource concept exactly once")
    native_ids = {item["nativeId"] for item in inventory["elements"]}
    for mapping in mappings:
        concept_id = mapping["conceptId"]
        kind = mapping.get("kind")
        if kind not in MAPPING_KINDS:
            raise ValueError(f"invalid provider mapping kind: {concept_id}")
        targets = mapping.get("nativeIds")
        if not isinstance(targets, list):
            raise TypeError(f"provider nativeIds must be an array: {concept_id}")
        if kind == "unmatched" and targets:
            raise ValueError(f"unmatched provider concept cannot have native ids: {concept_id}")
        if kind != "unmatched" and not targets:
            raise ValueError(f"provider mapping has no native ids: {concept_id}")
        unknown = set(targets) - native_ids
        if unknown:
            raise ValueError(f"provider mapping references unknown native ids: {concept_id}")
        if not str(mapping.get("preservedMeaning") or "").strip():
            raise ValueError(f"provider mapping lacks preserved meaning: {concept_id}")
        if kind in {"partial", "composite", "unmatched"} and not str(
            mapping.get("lostOrDifferentMeaning") or ""
        ).strip():
            raise ValueError(f"lossy provider mapping lacks difference: {concept_id}")
        evidence = mapping.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"provider mapping lacks evidence: {concept_id}")
        for item in evidence:
            if item.get("strength") not in EVIDENCE_STRENGTHS:
                raise ValueError(f"invalid provider evidence strength: {concept_id}")
            if not str(item.get("sourceLocator") or "").strip() or not str(
                item.get("supports") or ""
            ).strip():
                raise ValueError(f"incomplete provider evidence: {concept_id}")
        if mapping.get("runtimeNecessityConfirmed") is True and not any(
            item["strength"] == "measured" for item in evidence
        ):
            raise ValueError(f"runtime necessity requires measured evidence: {concept_id}")

