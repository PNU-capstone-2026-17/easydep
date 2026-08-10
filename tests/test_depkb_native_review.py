from __future__ import annotations

import copy

import pytest

from app.core.cloudkb.depkb.alignment import validate_alignment
from app.core.cloudkb.depkb.native.consensus import reconcile_reviews
from app.core.cloudkb.depkb.native.discovery import discover_aws
from app.core.cloudkb.depkb.native.freeze import freeze_native_graph, validate_frozen_graph
from app.core.cloudkb.depkb.native.review import make_review_packet, validate_review


def test_review_packet_covers_every_native_element_without_neutral_fields():
    inventory = discover_aws()
    packet = make_review_packet(inventory)

    validate_review(inventory, packet, require_complete=False)
    assert len(packet["decisions"]) == len(inventory["elements"])
    assert len(packet["candidateDecisions"]) == len(inventory["candidates"])
    assert all(item["status"] == "unreviewed" for item in packet["decisions"])
    assert all("neutralId" not in item for item in packet["decisions"])


def test_incomplete_native_review_cannot_be_frozen():
    inventory = discover_aws()
    packet = make_review_packet(inventory)

    with pytest.raises(ValueError, match="not complete"):
        validate_review(inventory, packet, require_complete=True)


def test_review_requires_evidenced_reason_for_inclusion():
    inventory = discover_aws()
    packet = make_review_packet(inventory)
    packet["decisions"][0].update(
        status="included", criterion="provisioningOutcome", reason=""
    )

    with pytest.raises(ValueError, match="lacks a reason"):
        validate_review(inventory, packet, require_complete=False)


def _alignment() -> dict:
    return {
        "schemaVersion": "easydep-neutral-alignment/v1",
        "nativeGraphs": {
            provider: {"sha256": f"{provider}-frozen"}
            for provider in ("aws", "azure", "gcp")
        },
        "concepts": [
            {
                "id": "derived-after-native-freeze",
                "definition": "Meaning derived from frozen native graphs.",
                "derivation": "Compared operational effect and lifecycle boundaries.",
                "tier": "core",
                "providerMappings": [
                    {
                        "provider": provider,
                        "kind": "equivalent",
                        "nativeIds": [f"{provider}.native"],
                        "preservedMeaning": "same observed operational effect",
                    }
                    for provider in ("aws", "azure", "gcp")
                ],
                "externalCrossChecks": [
                    {
                        "model": model,
                        "result": "agreement",
                        "sourceId": {
                            "cloud-barista": "cloud-barista.cb-tumblebug.c2c4e76",
                            "tosca": "oasis.tosca.2.0.csd07",
                            "occi": "ogf.occi.infrastructure.gfd224",
                        }[model],
                    }
                    for model in ("cloud-barista", "tosca", "occi")
                ],
            }
        ],
        "freeze": {"sha256": "alignment-frozen", "p1P2P3UsedDuringDerivation": False},
    }


def test_alignment_requires_frozen_native_graphs_and_all_external_crosschecks():
    document = _alignment()
    validate_alignment(document)

    broken = copy.deepcopy(document)
    broken["concepts"][0]["externalCrossChecks"].pop()
    with pytest.raises(ValueError, match="all neutral models"):
        validate_alignment(broken)


def test_partial_mapping_must_preserve_information_loss():
    document = _alignment()
    mapping = document["concepts"][0]["providerMappings"][0]
    mapping["kind"] = "partial"

    with pytest.raises(ValueError, match="lossy mapping"):
        validate_alignment(document)


def test_unmatched_provider_is_not_counted_as_a_neutral_realization():
    document = _alignment()
    mapping = document["concepts"][0]["providerMappings"][0]
    mapping.update(
        kind="unmatched",
        nativeIds=[],
        lostOrDifferentMeaning="No corresponding native element was found.",
    )
    document["concepts"][0]["tier"] = "shared"

    validate_alignment(document)


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
