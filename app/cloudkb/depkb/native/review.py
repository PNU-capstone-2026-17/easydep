"""Review boundary between broad native extraction and the study graph.

Extraction is intentionally high-recall.  Nothing enters the empirical study
until every native element has an explicit, evidenced inclusion decision.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from .model import validate_inventory

DECISION_STATUSES = frozenset({"unreviewed", "included", "excluded"})
INCLUSION_CRITERIA = frozenset(
    {
        "provisioningOutcome",
        "lifecycleOutcome",
        "networkReachability",
        "dataPersistence",
        "failureRouting",
        "vmAttachedIdentity",
    }
)
EXCLUSION_REASONS = frozenset(
    {
        "outsideStudyBoundary",
        "noDirectOrTransitiveVmConnection",
        "observationalMetadataOnly",
        "operationDuplicateOfNativeElement",
        "supersededProviderApi",
    }
)
RELATION_KINDS = frozenset(
    {
        "reference",
        "attachment",
        "containment",
        "selection",
        "providerCreation",
        "providerDefault",
    }
)


def _candidate_id(candidate: dict[str, Any]) -> str:
    identity = json.dumps(
        {
            "subjectNativeId": candidate.get("subjectNativeId"),
            "objectNativeId": candidate.get("objectNativeId"),
            "referenceToken": candidate.get("referenceToken"),
            "form": candidate.get("form"),
            "sourceLocator": candidate.get("sourceLocator"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:20]


def make_review_packet(inventory: dict[str, Any]) -> dict[str, Any]:
    """Create a neutral-free packet; reviewers fill decisions from native sources."""
    validate_inventory(inventory)
    return {
        "schemaVersion": "easydep-native-review/v1",
        "provider": inventory["provider"],
        "source": inventory["source"],
        "inventoryElementCount": len(inventory["elements"]),
        "decisions": [
            {
                "nativeId": element["nativeId"],
                "status": "unreviewed",
                "criterion": None,
                "reason": "",
                "sourceLocators": [element["sourceLocator"]],
            }
            for element in inventory["elements"]
        ],
        "candidateDecisions": [
            {
                "candidateId": _candidate_id(candidate),
                "subjectNativeId": candidate["subjectNativeId"],
                "observedObjectNativeId": candidate.get("objectNativeId"),
                "status": "unreviewed",
                "relationKind": None,
                "resolvedObjectNativeIds": [],
                "reason": "",
                "sourceLocators": [candidate["sourceLocator"]],
            }
            for candidate in inventory["candidates"]
        ],
    }


def validate_review(
    inventory: dict[str, Any], packet: dict[str, Any], *, require_complete: bool
) -> None:
    validate_inventory(inventory)
    if packet.get("schemaVersion") != "easydep-native-review/v1":
        raise ValueError("unsupported native review schemaVersion")
    if packet.get("provider") != inventory.get("provider"):
        raise ValueError("native review provider does not match inventory")

    expected = {item["nativeId"] for item in inventory["elements"]}
    decisions = packet.get("decisions")
    if not isinstance(decisions, list):
        raise TypeError("native review decisions must be an array")
    ids = [str(item.get("nativeId") or "") for item in decisions]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate native review decisions: {duplicates[:5]}")
    actual = set(ids)
    if actual != expected:
        raise ValueError(
            "native review must decide every inventory element exactly once; "
            f"missing={sorted(expected - actual)[:5]}, extra={sorted(actual - expected)[:5]}"
        )

    for decision in decisions:
        native_id = decision["nativeId"]
        status = decision.get("status")
        if status not in DECISION_STATUSES:
            raise ValueError(f"invalid review status for {native_id}: {status!r}")
        if require_complete and status == "unreviewed":
            raise ValueError(f"native review is not complete: {native_id}")
        reason = str(decision.get("reason") or "").strip()
        locators = decision.get("sourceLocators")
        if not isinstance(locators, list) or not locators:
            raise ValueError(f"native review lacks source locators: {native_id}")
        if status == "included":
            if decision.get("criterion") not in INCLUSION_CRITERIA:
                raise ValueError(f"included element lacks a valid criterion: {native_id}")
            if not reason:
                raise ValueError(f"included element lacks a reason: {native_id}")
        if status == "excluded":
            if decision.get("criterion") not in EXCLUSION_REASONS:
                raise ValueError(f"excluded element lacks a valid reason: {native_id}")
            if not reason:
                raise ValueError(f"excluded element lacks a reason: {native_id}")
        forbidden = {"neutralId", "capability", "crossProviderConcept"} & set(decision)
        if forbidden:
            raise ValueError(
                f"native review contains premature neutral fields on {native_id}: "
                f"{sorted(forbidden)}"
            )

    expected_candidates = {_candidate_id(item) for item in inventory["candidates"]}
    candidate_decisions = packet.get("candidateDecisions")
    if not isinstance(candidate_decisions, list):
        raise TypeError("native candidate decisions must be an array")
    candidate_ids = [str(item.get("candidateId") or "") for item in candidate_decisions]
    duplicate_candidates = sorted(
        key for key, count in Counter(candidate_ids).items() if count > 1
    )
    if duplicate_candidates:
        raise ValueError(f"duplicate candidate review decisions: {duplicate_candidates[:5]}")
    if set(candidate_ids) != expected_candidates:
        raise ValueError("native review must decide every relation candidate exactly once")
    for decision in candidate_decisions:
        candidate_id = decision["candidateId"]
        status = decision.get("status")
        if status not in DECISION_STATUSES:
            raise ValueError(f"invalid candidate review status: {candidate_id}")
        if require_complete and status == "unreviewed":
            raise ValueError(f"native candidate review is not complete: {candidate_id}")
        reason = str(decision.get("reason") or "").strip()
        locators = decision.get("sourceLocators")
        if not isinstance(locators, list) or not locators:
            raise ValueError(f"candidate review lacks source locators: {candidate_id}")
        if status == "included":
            if decision.get("relationKind") not in RELATION_KINDS:
                raise ValueError(f"included candidate lacks relation kind: {candidate_id}")
            targets = decision.get("resolvedObjectNativeIds")
            if not isinstance(targets, list) or not targets:
                raise ValueError(f"included candidate lacks resolved native target: {candidate_id}")
            if not reason:
                raise ValueError(f"included candidate lacks a reason: {candidate_id}")
        if status == "excluded" and not reason:
            raise ValueError(f"excluded candidate lacks a reason: {candidate_id}")


def review_counts(packet: dict[str, Any]) -> dict[str, int]:
    node_counts = Counter(str(item.get("status")) for item in packet.get("decisions", []))
    edge_counts = Counter(
        str(item.get("status")) for item in packet.get("candidateDecisions", [])
    )
    return {
        f"nodes.{key}": node_counts.get(key, 0) for key in sorted(DECISION_STATUSES)
    } | {
        f"candidates.{key}": edge_counts.get(key, 0)
        for key in sorted(DECISION_STATUSES)
    }
