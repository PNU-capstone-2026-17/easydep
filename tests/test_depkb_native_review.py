from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.cloudkb.depkb.native.consensus import reconcile_reviews
from app.cloudkb.depkb.native.freeze import freeze_native_graph, validate_frozen_graph
from app.cloudkb.depkb.native.review import make_review_packet, validate_review


def _aws_inventory() -> dict:
    return json.loads(
        Path("app/cloudkb/depkb/native/aws-inventory.json").read_text(encoding="utf-8")
    )


def test_review_packet_covers_every_native_element_without_cross_provider_fields():
    inventory = _aws_inventory()
    packet = make_review_packet(inventory)

    validate_review(inventory, packet, require_complete=False)
    assert len(packet["decisions"]) == len(inventory["elements"])
    assert len(packet["candidateDecisions"]) == len(inventory["candidates"])
    assert all(item["status"] == "unreviewed" for item in packet["decisions"])
    assert all("crossProviderId" not in item for item in packet["decisions"])


def test_incomplete_native_review_cannot_be_frozen():
    inventory = _aws_inventory()
    packet = make_review_packet(inventory)

    with pytest.raises(ValueError, match="not complete"):
        validate_review(inventory, packet, require_complete=True)


def test_review_requires_evidenced_reason_for_inclusion():
    inventory = _aws_inventory()
    packet = make_review_packet(inventory)
    packet["decisions"][0].update(
        status="included", criterion="provisioningOutcome", reason=""
    )

    with pytest.raises(ValueError, match="lacks a reason"):
        validate_review(inventory, packet, require_complete=False)


def test_frozen_native_graph_requires_complete_node_and_candidate_review():
    inventory = {
        "schemaVersion": "easydep-native-discovery/v1",
        "provider": "aws",
        "source": {"identity": "test", "version": "1"},
        "elements": [
            {
                "nativeId": "aws.native.a",
                "nativeForm": "standaloneResource",
                "sourceLocator": "source#/a",
            },
            {
                "nativeId": "aws.native.b",
                "nativeForm": "nestedConfiguration",
                "sourceLocator": "source#/b",
            },
        ],
        "candidates": [
            {
                "subjectNativeId": "aws.native.a",
                "objectNativeId": "aws.native.b",
                "form": "typedSchemaReference",
                "sourceLocator": "source#/a/b",
            }
        ],
    }
    packet = make_review_packet(inventory)
    for decision in packet["decisions"]:
        decision.update(
            status="included",
            criterion="provisioningOutcome",
            reason="The pinned native schema exposes this provisioning element.",
        )
    packet["candidateDecisions"][0].update(
        status="included",
        relationKind="reference",
        resolvedObjectNativeIds=["aws.native.b"],
        reason="The pinned native schema contains a typed reference.",
    )

    graph = freeze_native_graph(
        inventory,
        reconcile_reviews(
            inventory,
            packet,
            copy.deepcopy(packet),
            first_reviewer="reviewer-a",
            second_reviewer="reviewer-b",
        ),
    )
    validate_frozen_graph(graph)
    assert graph["edges"][0]["subjectNativeId"] == "aws.native.a"

    graph["nodes"][0]["reason"] = "tampered"
    with pytest.raises(ValueError, match="digest"):
        validate_frozen_graph(graph)
