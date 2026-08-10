from __future__ import annotations

import copy

import pytest

from app.core.cloudkb.depkb.derived import DerivedCatalog
from app.core.cloudkb.depkb.native.freeze import _digest


def _graph(provider: str) -> dict:
    graph = {
        "schemaVersion": "easydep-native-graph/v1",
        "provider": provider,
        "source": {"identity": provider, "version": "1"},
        "nodes": [
            {
                "nativeId": f"{provider}.native",
                "criterion": "provisioningOutcome",
                "reason": "reviewed native element",
                "sourceLocators": [f"{provider}#/native"],
            }
        ],
        "edges": [],
        "review": {
            "inventoryElementCount": 1,
            "inventoryCandidateCount": 0,
            "complete": True,
        },
    }
    graph["freeze"] = {
        "sha256": _digest(graph),
        "p1P2P3UsedDuringDiscovery": False,
    }
    return graph


def _alignment(graphs: dict) -> dict:
    document = {
        "schemaVersion": "easydep-neutral-alignment/v1",
        "nativeGraphs": {
            provider: {"sha256": graph["freeze"]["sha256"]}
            for provider, graph in graphs.items()
        },
        "concepts": [
            {
                "id": "derived-concept",
                "definition": "Derived only after native freeze.",
                "derivation": "Operational effect and lifecycle were compared.",
                "tier": "core",
                "providerMappings": [
                    {
                        "provider": provider,
                        "kind": "equivalent",
                        "nativeIds": [f"{provider}.native"],
                        "preservedMeaning": "reviewed effect",
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
        "freeze": {
            "sha256": "alignment-hash",
            "p1P2P3UsedDuringDerivation": False,
        },
    }
    return document


def test_catalog_realizes_derived_concept_through_authoritative_native_graph():
    graphs = {provider: _graph(provider) for provider in ("aws", "azure", "gcp")}
    catalog = DerivedCatalog(_alignment(graphs), graphs)

    result = catalog.realize(["derived-concept"], "azure", "koreacentral")

    assert result.nativeNodes[0]["nativeId"] == "azure.native"
    assert result.provenance["nativeGraphAuthoritative"] is True
    assert result.provenance["nativeGraph"] == graphs["azure"]["freeze"]["sha256"]


def test_catalog_rejects_alignment_against_different_native_graph_revision():
    graphs = {provider: _graph(provider) for provider in ("aws", "azure", "gcp")}
    alignment = _alignment(graphs)
    alignment["nativeGraphs"]["gcp"]["sha256"] = "stale"

    with pytest.raises(ValueError, match="different gcp graph"):
        DerivedCatalog(alignment, graphs)


def test_catalog_rejects_unknown_native_mapping():
    graphs = {provider: _graph(provider) for provider in ("aws", "azure", "gcp")}
    alignment = _alignment(graphs)
    broken = copy.deepcopy(alignment)
    broken["concepts"][0]["providerMappings"][0]["nativeIds"] = ["aws.unknown"]

    with pytest.raises(ValueError, match="unknown aws native ids"):
        DerivedCatalog(broken, graphs)


def test_catalog_preserves_explicit_unmatched_provider_mapping():
    graphs = {provider: _graph(provider) for provider in ("aws", "azure", "gcp")}
    alignment = _alignment(graphs)
    mapping = alignment["concepts"][0]["providerMappings"][0]
    mapping.update(
        kind="unmatched",
        nativeIds=[],
        lostOrDifferentMeaning="No AWS realization was found in the frozen graph.",
    )
    alignment["concepts"][0]["tier"] = "shared"
    coverage = copy.deepcopy(alignment["concepts"][0])
    coverage.update(id="aws-provider-extension", tier="providerExtension")
    coverage["providerMappings"] = [
        {
            "provider": "aws",
            "kind": "equivalent",
            "nativeIds": ["aws.native"],
            "preservedMeaning": "AWS-specific reviewed effect",
        }
    ]
    alignment["concepts"].append(coverage)
    catalog = DerivedCatalog(alignment, graphs)

    result = catalog.realize(["derived-concept"], "aws", "ap-northeast-2")

    assert result.mappings[0]["status"] == "unmatched"
    assert result.nativeNodes == ()


def test_catalog_rejects_native_nodes_hidden_by_the_neutral_alignment():
    graphs = {provider: _graph(provider) for provider in ("aws", "azure", "gcp")}
    graphs["aws"]["nodes"].append(
        {
            "nativeId": "aws.unmapped",
            "criterion": "provisioningOutcome",
            "reason": "provider-native element",
            "sourceLocators": ["test#/unmapped"],
        }
    )
    graph_without_freeze = {
        key: value for key, value in graphs["aws"].items() if key != "freeze"
    }
    graphs["aws"]["freeze"]["sha256"] = _digest(graph_without_freeze)
    alignment = _alignment(graphs)

    with pytest.raises(ValueError, match="leaves aws native nodes uncovered"):
        DerivedCatalog(alignment, graphs)


def test_structural_corpus_covers_every_frozen_node_without_p_scenarios():
    graphs = {provider: _graph(provider) for provider in ("aws", "azure", "gcp")}
    catalog = DerivedCatalog(_alignment(graphs), graphs)

    corpus = catalog.structural_corpus()

    expected = sum(len(graph["nodes"]) + len(graph["edges"]) for graph in graphs.values())
    assert len(corpus) == expected
    assert all(item["p1P2P3Derived"] is False for item in corpus)
    assert {item["kind"] for item in corpus} == {"nativeNodeCoverage"}
