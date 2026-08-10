"""Reconcile two independent native reviews without hiding disagreements."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..reliability import cohen_kappa, krippendorff_alpha_nominal, percent_agreement
from .review import validate_review


def _digest(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decision_key(decision: dict[str, Any], *, candidate: bool) -> tuple[Any, ...]:
    common = (decision.get("status"),)
    if candidate:
        return common + (
            decision.get("relationKind"),
            tuple(sorted(decision.get("resolvedObjectNativeIds") or [])),
        )
    return common + (decision.get("criterion"),)


def _merge_agreement(
    first: dict[str, Any], second: dict[str, Any], *, candidate: bool
) -> dict[str, Any]:
    merged = copy.deepcopy(first)
    merged["sourceLocators"] = sorted(
        set(first.get("sourceLocators") or []) | set(second.get("sourceLocators") or [])
    )
    reasons = [
        reason
        for reason in (str(first.get("reason") or ""), str(second.get("reason") or ""))
        if reason
    ]
    merged["reason"] = " | ".join(dict.fromkeys(reasons))
    merged["independentAgreement"] = True
    return merged


def reconcile_reviews(
    inventory: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    first_reviewer: str,
    second_reviewer: str,
) -> dict[str, Any]:
    """Return a freeze-compatible packet; disagreements remain unreviewed."""
    reviewers = [first_reviewer.strip(), second_reviewer.strip()]
    if not all(reviewers) or reviewers[0] == reviewers[1]:
        raise ValueError("two distinct reviewer identities are required")
    validate_review(inventory, first, require_complete=True)
    validate_review(inventory, second, require_complete=True)
    first_nodes = {item["nativeId"]: item for item in first["decisions"]}
    second_nodes = {item["nativeId"]: item for item in second["decisions"]}
    status_pairs = [
        (first_nodes[key]["status"], second_nodes[key]["status"])
        for key in sorted(first_nodes)
    ]
    type_rows = [
        (
            f"{first_nodes[key]['status']}:{first_nodes[key].get('criterion')}",
            f"{second_nodes[key]['status']}:{second_nodes[key].get('criterion')}",
        )
        for key in sorted(first_nodes)
    ]

    conflicts: list[dict[str, Any]] = []

    def reconcile(kind: str, key: str, candidate: bool) -> list[dict[str, Any]]:
        left = {item[key]: item for item in first[kind]}
        right = {item[key]: item for item in second[kind]}
        output = []
        for identity in sorted(left):
            if _decision_key(left[identity], candidate=candidate) == _decision_key(
                right[identity], candidate=candidate
            ):
                output.append(
                    _merge_agreement(left[identity], right[identity], candidate=candidate)
                )
                continue
            unresolved = copy.deepcopy(left[identity])
            unresolved.update(status="unreviewed", reason="")
            if candidate:
                unresolved.update(relationKind=None, resolvedObjectNativeIds=[])
            else:
                unresolved["criterion"] = None
            output.append(unresolved)
            conflicts.append(
                {
                    "kind": "candidate" if candidate else "element",
                    "id": identity,
                    "firstDecision": left[identity],
                    "secondDecision": right[identity],
                    "humanReviewRequired": True,
                }
            )
        return output

    packet = {
        "schemaVersion": "easydep-native-review/v1",
        "provider": inventory["provider"],
        "source": inventory["source"],
        "inventoryElementCount": len(inventory["elements"]),
        "decisions": reconcile("decisions", "nativeId", False),
        "candidateDecisions": reconcile(
            "candidateDecisions", "candidateId", True
        ),
        "consensus": {
            "schemaVersion": "easydep-native-consensus/v1",
            "reviewers": reviewers,
            "inputDigests": [_digest(first), _digest(second)],
            "conflicts": conflicts,
            "humanReviewRequired": bool(conflicts),
            "reliability": {
                "percentAgreement": percent_agreement(status_pairs),
                "cohenKappaInclusion": cohen_kappa(status_pairs),
                "krippendorffAlphaType": krippendorff_alpha_nominal(type_rows),
            },
        },
    }
    validate_review(inventory, packet, require_complete=not conflicts)
    return packet
