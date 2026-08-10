from __future__ import annotations

import json
from pathlib import Path

from app.core.cloudkb.depkb.knowledge_access import query_knowledge


def test_query_is_deterministic_and_carries_frozen_snapshot():
    first = query_knowledge(
        provider="gcp",
        anchors=["loadBalancer", "vm"],
        capability_ids=["load-balanced-ingress"],
    )
    second = query_knowledge(
        provider="gcp",
        anchors=["vm", "loadBalancer"],
        capability_ids=["load-balanced-ingress"],
    )

    assert first == second
    assert len(first["capabilityRealizations"][0]["components"]) == 6
    assert all(len(value) == 64 for value in first["snapshot"].values())


def test_access_arms_share_one_query_and_measure_transport_costs():
    protocol = json.loads(Path(
        "evaluation/research_protocol/definitions/knowledge-access-protocol.json"
    ).read_text(encoding="utf-8"))

    assert {arm["access"] for arm in protocol["arms"]} == {
        "none", "prompt", "functionTool", "mcp"
    }
    assert protocol["invariants"]["sameKnowledgeSnapshotAcrossArms"] is True
    assert protocol["invariants"]["toolResultsCountTowardTokenBudget"] is True
    assert "unsupportedNecessityPromotionCount" in protocol["measurements"]["secondary"]
    assert protocol["apiEligibility"] == {
        "requiredApi": "responses",
        "sameEndpointAcrossArms": True,
        "sameFrozenModelAcrossArms": True,
        "requiredModelFeatures": ["functionCalling", "remoteMcp"],
        "chatCompletionsTextWrapperIsNotMcp": True,
        "missingFeatureAction": (
            "keep-development-status-and-exclude-four-arm-confirmatory-claim"
        ),
    }
