"""Validate neutral concepts derived after provider-native graph freeze."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .neutral_sources import source_registry

PROVIDERS = frozenset({"aws", "azure", "gcp"})
MAPPING_KINDS = frozenset({"equivalent", "partial", "composite", "unmatched"})
MODEL_NAMES = frozenset({"cloud-barista", "tosca", "occi"})
CROSSCHECK_RESULTS = frozenset(
    {"agreement", "partialAgreement", "conflict", "notRepresented"}
)


def _tier(providers: set[str]) -> str:
    if providers == PROVIDERS:
        return "core"
    if len(providers) == 2:
        return "shared"
    return "providerExtension"


def validate_alignment(document: dict[str, Any], *, require_frozen: bool = True) -> None:
    if document.get("schemaVersion") != "easydep-neutral-alignment/v1":
        raise ValueError("unsupported neutral alignment schemaVersion")
    native_graphs = document.get("nativeGraphs")
    if not isinstance(native_graphs, dict) or set(native_graphs) != PROVIDERS:
        raise ValueError("alignment requires all three provider-native graph coordinates")
    for provider, coordinate in native_graphs.items():
        if not isinstance(coordinate, dict) or not coordinate.get("sha256"):
            raise ValueError(f"native graph is not frozen for {provider}")

    concepts = document.get("concepts")
    if not isinstance(concepts, list):
        raise TypeError("alignment concepts must be an array")
    if require_frozen and not concepts:
        raise ValueError("a frozen alignment cannot be empty")
    concept_ids = [str(item.get("id") or "") for item in concepts]
    duplicates = sorted(key for key, count in Counter(concept_ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate neutral concept ids: {duplicates}")

    for concept in concepts:
        concept_id = str(concept.get("id") or "")
        if not concept_id or not concept.get("definition") or not concept.get("derivation"):
            raise ValueError("neutral concept requires id, definition, and derivation")
        mappings = concept.get("providerMappings")
        if not isinstance(mappings, list) or not mappings:
            raise ValueError(f"neutral concept has no provider mappings: {concept_id}")
        providers: set[str] = set()
        for mapping in mappings:
            provider = mapping.get("provider")
            if provider not in PROVIDERS:
                raise ValueError(f"invalid provider mapping on {concept_id}")
            kind = mapping.get("kind")
            if kind not in MAPPING_KINDS:
                raise ValueError(f"invalid mapping kind on {concept_id}")
            native_ids = mapping.get("nativeIds")
            if not isinstance(native_ids, list):
                raise TypeError(f"mapping native ids must be an array on {concept_id}")
            if kind == "unmatched":
                if native_ids:
                    raise ValueError(f"unmatched mapping cannot have native ids: {concept_id}")
            elif not native_ids:
                raise ValueError(f"mapping has no native ids on {concept_id}")
            else:
                providers.add(provider)
            if not mapping.get("preservedMeaning"):
                raise ValueError(f"mapping lacks preserved meaning on {concept_id}")
            if kind in {"partial", "composite", "unmatched"} and not mapping.get(
                "lostOrDifferentMeaning"
            ):
                raise ValueError(
                    f"lossy mapping must state lost or different meaning: {concept_id}"
                )
        if not providers:
            raise ValueError(f"neutral concept has no provider realization: {concept_id}")
        if concept.get("tier") != _tier(providers):
            raise ValueError(f"incorrect derivation tier on {concept_id}")

        crosschecks = concept.get("externalCrossChecks")
        if not isinstance(crosschecks, list):
            raise TypeError(f"external cross-checks must be an array: {concept_id}")
        seen_models = {item.get("model") for item in crosschecks}
        if seen_models != MODEL_NAMES:
            raise ValueError(f"all neutral models must be cross-checked: {concept_id}")
        for item in crosschecks:
            source_id = item.get("sourceId")
            sources = source_registry()
            if source_id not in sources:
                raise ValueError(f"unregistered external source on {concept_id}")
            if sources[source_id]["model"] != item.get("model"):
                raise ValueError(f"external source model mismatch on {concept_id}")
            if item.get("result") not in CROSSCHECK_RESULTS:
                raise ValueError(f"invalid external cross-check on {concept_id}")

    if require_frozen:
        freeze = document.get("freeze")
        if not isinstance(freeze, dict) or not freeze.get("sha256"):
            raise ValueError("alignment freeze coordinate is required")
        if freeze.get("p1P2P3UsedDuringDerivation") is not False:
            raise ValueError("P1-P3 must not be used during neutral derivation")
