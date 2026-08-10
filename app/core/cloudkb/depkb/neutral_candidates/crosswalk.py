"""Validate source-only concept synthesis before CSP mappings are introduced."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .model import MODELS, validate_candidate_packet

MEMBER_KINDS = frozenset({"equivalent", "partial", "structuralSupport", "conflict"})
RELATION_DISPOSITIONS = frozenset({"preserved", "structuralOnly", "excluded"})


def validate_crosswalk(
    document: dict[str, Any], packets: dict[str, dict[str, Any]]
) -> None:
    if document.get("schemaVersion") != "easydep-neutral-crosswalk/v1":
        raise ValueError("unsupported neutral crosswalk schemaVersion")
    if set(packets) != MODELS:
        raise ValueError("crosswalk validation requires all three neutral source packets")
    source_ids: dict[str, set[str]] = {}
    for model, packet in packets.items():
        validate_candidate_packet(packet)
        if packet["model"] != model:
            raise ValueError("neutral packet model key mismatch")
        source_ids[model] = {item["id"] for item in packet["candidates"]}

    concepts = document.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        raise ValueError("neutral crosswalk must contain concepts")
    ids = [str(item.get("id") or "") for item in concepts]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if not all(ids) or duplicates:
        raise ValueError("crosswalk concept ids must be unique and non-empty")
    used_members: set[tuple[str, str]] = set()
    for concept in concepts:
        concept_id = concept["id"]
        for field in ("definition", "derivation", "unresolvedDifferences"):
            if not str(concept.get(field) or "").strip():
                raise ValueError(f"crosswalk concept lacks {field}: {concept_id}")
        members = concept.get("sourceMembers")
        if not isinstance(members, list) or not members:
            raise ValueError(f"crosswalk concept has no source members: {concept_id}")
        for member in members:
            model = member.get("model")
            candidate_id = member.get("candidateId")
            if model not in MODELS or candidate_id not in source_ids[model]:
                raise ValueError(f"unknown source member on crosswalk concept: {concept_id}")
            if member.get("kind") not in MEMBER_KINDS:
                raise ValueError(f"invalid source member kind: {concept_id}")
            if not str(member.get("rationale") or "").strip():
                raise ValueError(f"source member lacks rationale: {concept_id}")
            key = (model, candidate_id)
            if key in used_members:
                raise ValueError(f"source candidate mapped more than once: {key}")
            used_members.add(key)
    if any(key in concept for concept in concepts for key in ("aws", "azure", "gcp")):
        raise ValueError("provider projections are forbidden in the neutral crosswalk")

    exclusions = document.get("excludedSourceCandidates")
    if not isinstance(exclusions, list):
        raise TypeError("excludedSourceCandidates must be an array")
    excluded: set[tuple[str, str]] = set()
    for item in exclusions:
        key = (item.get("model"), item.get("candidateId"))
        if key[0] not in MODELS or key[1] not in source_ids[key[0]]:
            raise ValueError(f"unknown excluded source candidate: {key}")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"excluded source candidate lacks reason: {key}")
        if key in excluded or key in used_members:
            raise ValueError(f"source candidate classified more than once: {key}")
        excluded.add(key)
    expected = {(model, item) for model, items in source_ids.items() for item in items}
    if used_members | excluded != expected:
        raise ValueError("every neutral source candidate must be mapped or explicitly excluded")

    relation_coverage = document.get("sourceRelationCoverage")
    if not isinstance(relation_coverage, list):
        raise TypeError("sourceRelationCoverage must be an array")
    expected_relations = {
        (model, candidate["id"], index)
        for model, packet in packets.items()
        for candidate in packet["candidates"]
        for index, _relation in enumerate(candidate["relations"])
    }
    actual_relations: set[tuple[str, str, int]] = set()
    concept_set = set(ids)
    for item in relation_coverage:
        key = (item.get("model"), item.get("candidateId"), item.get("relationIndex"))
        if key in actual_relations:
            raise ValueError(f"source relation classified more than once: {key}")
        actual_relations.add(key)
        if item.get("disposition") not in RELATION_DISPOSITIONS:
            raise ValueError(f"invalid source relation disposition: {key}")
        if not str(item.get("rationale") or "").strip():
            raise ValueError(f"source relation lacks rationale: {key}")
        concept_ids = item.get("conceptIds")
        if not isinstance(concept_ids, list) or not set(concept_ids) <= concept_set:
            raise ValueError(f"source relation references unknown concepts: {key}")
        if item["disposition"] != "excluded" and not concept_ids:
            raise ValueError(f"retained source relation has no crosswalk concepts: {key}")
    if actual_relations != expected_relations:
        raise ValueError("every source relation must be classified exactly once")
