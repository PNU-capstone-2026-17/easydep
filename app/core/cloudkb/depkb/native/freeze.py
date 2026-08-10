"""Freeze completely reviewed native packets into immutable provider graphs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .review import validate_review


def _digest(document: dict[str, Any]) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def freeze_native_graph(
    inventory: dict[str, Any], packet: dict[str, Any]
) -> dict[str, Any]:
    validate_review(inventory, packet, require_complete=True)
    consensus = packet.get("consensus")
    if not isinstance(consensus, dict):
        raise TypeError("native graph freeze requires two-reviewer consensus")
    if consensus.get("schemaVersion") != "easydep-native-consensus/v1":
        raise ValueError("unsupported native consensus schemaVersion")
    reviewers = consensus.get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 2 or reviewers[0] == reviewers[1]:
        raise ValueError("native graph freeze requires two distinct reviewers")
    if consensus.get("humanReviewRequired") or consensus.get("conflicts"):
        raise ValueError("native graph freeze has unresolved review conflicts")
    included_nodes = {
        item["nativeId"]: item
        for item in packet["decisions"]
        if item["status"] == "included"
    }
    if not included_nodes:
        raise ValueError("cannot freeze a native graph without included elements")

    nodes = [
        {
            "nativeId": native_id,
            "criterion": item["criterion"],
            "reason": item["reason"],
            "sourceLocators": item["sourceLocators"],
        }
        for native_id, item in sorted(included_nodes.items())
    ]
    edges: list[dict[str, Any]] = []
    for item in packet["candidateDecisions"]:
        if item["status"] != "included":
            continue
        subject = item["subjectNativeId"]
        if subject not in included_nodes:
            raise ValueError(f"included relation subject is not an included node: {subject}")
        for target in item["resolvedObjectNativeIds"]:
            if target not in included_nodes:
                raise ValueError(f"included relation target is not an included node: {target}")
            edges.append(
                {
                    "candidateId": item["candidateId"],
                    "subjectNativeId": subject,
                    "objectNativeId": target,
                    "relationKind": item["relationKind"],
                    "reason": item["reason"],
                    "sourceLocators": item["sourceLocators"],
                }
            )
    graph = {
        "schemaVersion": "easydep-native-graph/v1",
        "provider": inventory["provider"],
        "source": inventory["source"],
        "nodes": nodes,
        "edges": sorted(
            edges,
            key=lambda item: (
                item["subjectNativeId"], item["objectNativeId"], item["relationKind"]
            ),
        ),
        "review": {
            "inventoryElementCount": len(inventory["elements"]),
            "inventoryCandidateCount": len(inventory["candidates"]),
            "complete": True,
            "reviewers": reviewers,
            "inputDigests": consensus.get("inputDigests"),
            "independentAgreement": True,
        },
    }
    graph["freeze"] = {"sha256": _digest(graph), "p1P2P3UsedDuringDiscovery": False}
    return graph


def validate_frozen_graph(graph: dict[str, Any]) -> None:
    freeze = graph.get("freeze")
    if not isinstance(freeze, dict) or not freeze.get("sha256"):
        raise ValueError("native graph has no freeze coordinate")
    if freeze.get("p1P2P3UsedDuringDiscovery") is not False:
        raise ValueError("P1-P3 contamination recorded in native graph")
    unhashed = {key: value for key, value in graph.items() if key != "freeze"}
    if freeze["sha256"] != _digest(unhashed):
        raise ValueError("native graph freeze digest does not match content")
