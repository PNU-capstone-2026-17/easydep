"""GCP(KCC CRD) 파서 테스트.

fixture: v1.153.0에서 받은 실제 CRD 3개(ComputeSubnetwork/Firewall/Instance),
servicemappings 발췌본, DCL 스타일 합성 CRD 1개.
골든 케이스: ComputeSubnetwork→ComputeNetwork (required),
ComputeInstance→ComputeSubnetwork (중첩 배열 networkInterface 경유).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from graphkb.model import Edge, Graph
from graphkb.parsers.gcp import parse_crds

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gcp"


def load(name: str) -> dict:
    return yaml.safe_load((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph() -> Graph:
    crds = [
        load("crd-computesubnetwork.yaml"),
        load("crd-computefirewall.yaml"),
        load("crd-computeinstance.yaml"),
        load("crd-syntheticthing.yaml"),
    ]
    return parse_crds(
        crds,
        servicemappings=[load("servicemappings-compute-min.yaml")],
        heuristics=True,
    )


def find_edges(graph: Graph, from_id: str, to_id: str) -> list[Edge]:
    return [e for e in graph.edges if e.from_id == from_id and e.to_id == to_id]


def test_nodes_created(graph: Graph) -> None:
    for kind in ("ComputeSubnetwork", "ComputeFirewall", "ComputeInstance"):
        node = graph.nodes[f"gcp::{kind}"]
        assert node.layer == "vendor"
        assert node.provider == "gcp"
    # 참조 대상으로만 등장하는 kind도 노드가 된다
    assert "gcp::ComputeNetwork" in graph.nodes


def test_golden_subnetwork_to_network(graph: Graph) -> None:
    """골든: ComputeSubnetwork → ComputeNetwork (networkRef, required)."""
    edges = find_edges(graph, "gcp::ComputeSubnetwork", "gcp::ComputeNetwork")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.via_property == "networkRef"
    assert edge.required is True  # spec.required에 networkRef 포함
    assert edge.evidence == "kcc-ref"
    assert edge.confidence == 0.9  # description 패턴으로 해석


def test_firewall_to_network_required(graph: Graph) -> None:
    edges = find_edges(graph, "gcp::ComputeFirewall", "gcp::ComputeNetwork")
    assert len(edges) == 1
    assert edges[0].required is True


def test_golden_instance_to_subnetwork_nested_array(graph: Graph) -> None:
    """골든: ComputeInstance → ComputeSubnetwork (networkInterface[] 내부)."""
    edges = find_edges(graph, "gcp::ComputeInstance", "gcp::ComputeSubnetwork")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.via_property == "networkInterface.subnetworkRef"
    assert edge.cardinality == "many"
    assert edge.evidence == "kcc-ref"
    assert edge.confidence == 1.0  # servicemappings gvk.kind가 최우선


def test_instance_template_ref_description_tier(graph: Graph) -> None:
    """servicemappings에 없는 ref는 description 패턴(0.9)으로 해석."""
    edges = find_edges(graph, "gcp::ComputeInstance", "gcp::ComputeInstanceTemplate")
    assert len(edges) == 1
    assert edges[0].confidence == 0.9


def test_dcl_style_resolved_only_by_servicemappings(graph: Graph) -> None:
    """generic description은 servicemappings로만 해석된다 (tier 1)."""
    edges = find_edges(graph, "gcp::ComputeSyntheticThing", "gcp::ComputeDisk")
    assert len(edges) == 1
    assert edges[0].via_property == "attachedRef"
    assert edges[0].evidence == "kcc-ref"
    assert edges[0].confidence == 1.0
    assert edges[0].required is True


def test_heuristic_tier(graph: Graph) -> None:
    """servicemappings/description 모두 실패 → 필드명 휴리스틱 (tier 3)."""
    edges = find_edges(graph, "gcp::ComputeSyntheticThing", "gcp::ComputeFirewall")
    assert len(edges) == 1
    assert edges[0].evidence == "heuristic"
    assert edges[0].confidence == 0.6  # 동일 서비스(compute)


def test_unresolvable_ref_skipped(graph: Graph) -> None:
    edges = [
        e
        for e in graph.edges
        if e.from_id == "gcp::ComputeSyntheticThing" and "widget" in e.via_property
    ]
    assert edges == []


def test_no_heuristics_flag() -> None:
    crds = [load("crd-syntheticthing.yaml")]
    graph = parse_crds(crds, servicemappings=[], heuristics=False)
    assert all(e.evidence != "heuristic" for e in graph.edges)


def test_graph_validates(graph: Graph) -> None:
    graph.validate()
