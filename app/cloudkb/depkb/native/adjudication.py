"""Apply explicit human decisions to conflicts from independent native reviews."""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from .review import validate_review


def make_adjudication_template(consensus: dict[str, Any]) -> dict[str, Any]:
    metadata = consensus.get("consensus")
    if not isinstance(metadata, dict):
        raise TypeError("review packet has no consensus metadata")
    conflicts = metadata.get("conflicts")
    if not isinstance(conflicts, list):
        raise TypeError("consensus conflicts must be an array")
    return {
        "schemaVersion": "easydep-native-adjudication/v1",
        "provider": consensus["provider"],
        "reviewers": metadata["reviewers"],
        "humanIdentity": "",
        "decisions": [
            {
                "kind": conflict["kind"],
                "id": conflict["id"],
                "firstDecision": conflict["firstDecision"],
                "secondDecision": conflict["secondDecision"],
                "resolution": "unreviewed",
                "rationale": "",
            }
            for conflict in conflicts
        ],
    }


def apply_adjudication(
    inventory: dict[str, Any],
    consensus: dict[str, Any],
    adjudication: dict[str, Any],
) -> dict[str, Any]:
    if adjudication.get("schemaVersion") != "easydep-native-adjudication/v1":
        raise ValueError("unsupported native adjudication schemaVersion")
    if adjudication.get("provider") != consensus.get("provider"):
        raise ValueError("adjudication provider does not match consensus")
    identity = str(adjudication.get("humanIdentity") or "").strip()
    if not identity:
        raise ValueError("human adjudicator identity is required")
    metadata = consensus.get("consensus")
    if not isinstance(metadata, dict):
        raise TypeError("review packet has no consensus metadata")
    conflicts = metadata.get("conflicts")
    if not isinstance(conflicts, list):
        raise TypeError("consensus conflicts must be an array")
    expected = {(item["kind"], item["id"]): item for item in conflicts}
    submitted = adjudication.get("decisions")
    if not isinstance(submitted, list):
        raise TypeError("adjudication decisions must be an array")
    keys = [(str(item.get("kind") or ""), str(item.get("id") or "")) for item in submitted]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    if duplicates or set(keys) != set(expected):
        raise ValueError("adjudication must resolve every conflict exactly once")

    resolved: dict[tuple[str, str], dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for item in submitted:
        key = (item["kind"], item["id"])
        resolution = item.get("resolution")
        if resolution not in {"first", "second", "override"}:
            raise ValueError(f"unresolved or invalid human decision: {key}")
        rationale = str(item.get("rationale") or "").strip()
        if not rationale:
            raise ValueError(f"human adjudication rationale is required: {key}")
        conflict = expected[key]
        if resolution == "first":
            decision = copy.deepcopy(conflict["firstDecision"])
        elif resolution == "second":
            decision = copy.deepcopy(conflict["secondDecision"])
        else:
            override = item.get("overrideDecision")
            if not isinstance(override, dict):
                raise TypeError(f"overrideDecision must be an object: {key}")
            decision = copy.deepcopy(override)
        decision["humanAdjudicated"] = True
        resolved[key] = decision
        audit.append(
            {
                "kind": key[0],
                "id": key[1],
                "resolution": resolution,
                "rationale": rationale,
            }
        )

    packet = copy.deepcopy(consensus)
    for field, kind, identity_field in (
        ("decisions", "element", "nativeId"),
        ("candidateDecisions", "candidate", "candidateId"),
    ):
        packet[field] = [
            resolved.get((kind, item[identity_field]), item) for item in packet[field]
        ]
    packet["consensus"].update(
        conflicts=[],
        humanReviewRequired=False,
        adjudicator=identity,
        resolvedConflicts=audit,
    )
    validate_review(inventory, packet, require_complete=True)
    return packet
