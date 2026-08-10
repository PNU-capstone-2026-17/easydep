"""Validate neutral-model hypotheses before any provider projection is added."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.cloudkb.depkb.neutral_sources import source_registry

MODELS = frozenset({"cloud-barista", "tosca", "occi"})
FORBIDDEN_FIELDS = frozenset(
    {
        "aws",
        "azure",
        "gcp",
        "providerMappings",
        "mappingKind",
        "nativeIds",
        "p1",
        "p2",
        "p3",
    }
)


def validate_candidate_packet(packet: dict[str, Any]) -> None:
    if packet.get("schemaVersion") != "easydep-neutral-hypotheses/v1":
        raise ValueError("unsupported neutral hypothesis schemaVersion")
    model = packet.get("model")
    if model not in MODELS:
        raise ValueError("invalid neutral hypothesis model")
    source_id = packet.get("sourceId")
    sources = source_registry()
    if source_id not in sources or sources[source_id]["model"] != model:
        raise ValueError("neutral hypothesis source is not registered for its model")
    candidates = packet.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("neutral hypothesis packet must contain candidates")
    ids = [str(item.get("id") or "") for item in candidates]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if not all(ids) or duplicates:
        raise ValueError("neutral hypothesis candidate ids must be unique and non-empty")
    for candidate in candidates:
        candidate_id = candidate["id"]
        if FORBIDDEN_FIELDS & set(candidate):
            raise ValueError(f"premature provider projection on hypothesis: {candidate_id}")
        for field in ("sourceTerm", "definition", "sourceLocator", "scopeRationale"):
            if not str(candidate.get(field) or "").strip():
                raise ValueError(f"neutral hypothesis lacks {field}: {candidate_id}")
        relations = candidate.get("relations")
        if not isinstance(relations, list):
            raise TypeError(f"neutral hypothesis relations must be an array: {candidate_id}")
        for relation in relations:
            if not str(relation.get("predicate") or "").strip() or not str(
                relation.get("targetSourceTerm") or ""
            ).strip():
                raise ValueError(f"neutral hypothesis relation is incomplete: {candidate_id}")
            if not str(relation.get("sourceLocator") or "").strip():
                raise ValueError(f"neutral relation lacks source locator: {candidate_id}")

