"""Azure(bicep-types-az) 파서 테스트.

fixture는 실측한 types.json 포맷을 그대로 따른 축소 합성본이다.
골든 케이스: subnets contained_in virtualNetworks (arm-hierarchy),
subnets → networkSecurityGroups (bicep-ref), NIC → subnets (중첩 배열 경유).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphkb.model import Edge, Graph
from graphkb.parsers.azure import extract_references, parse_index

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "azure"

VNET = "azure::Microsoft.Network/virtualNetworks"
SUBNET = "azure::Microsoft.Network/virtualNetworks/subnets"
NSG = "azure::Microsoft.Network/networkSecurityGroups"
NIC = "azure::Microsoft.Network/networkInterfaces"


def load_index() -> dict:
    return json.loads((FIXTURE_DIR / "index.json").read_text(encoding="utf-8"))


def load_types() -> list[dict]:
    path = FIXTURE_DIR / "network" / "microsoft.network" / "2025-01-01" / "types.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph() -> Graph:
    g, _latest = parse_index(load_index())
    extract_references(g, load_types(), heuristics=True)
    return g


def find_edges(graph: Graph, from_id: str, to_id: str) -> list[Edge]:
    return [e for e in graph.edges if e.from_id == from_id and e.to_id == to_id]


def test_nodes_from_index(graph: Graph) -> None:
    for node_id in (VNET, SUBNET, NSG, NIC):
        node = graph.nodes[node_id]
        assert node.layer == "vendor"
        assert node.provider == "azure"


def test_latest_stable_version_preferred() -> None:
    _g, latest = parse_index(load_index())
    version, _path = latest["Microsoft.Network/virtualNetworks"]
    assert version == "2025-01-01"  # 2026-01-01-preview보다 비-preview 우선


def test_hierarchy_containment(graph: Graph) -> None:
    """골든: subnets는 virtualNetworks에 contained_in (이름 계층에서 유도)."""
    edges = find_edges(graph, SUBNET, VNET)
    contained = [e for e in edges if e.type == "contained_in"]
    assert len(contained) == 1
    assert contained[0].evidence == "arm-hierarchy"
    assert contained[0].confidence == 1.0


def test_hierarchy_walks_all_levels(graph: Graph) -> None:
    """손자 타입도 각 단계 부모에 연결된다 (storage 3단계 체인)."""
    acct = "azure::Microsoft.Storage/storageAccounts"
    blob = "azure::Microsoft.Storage/storageAccounts/blobServices"
    cont = "azure::Microsoft.Storage/storageAccounts/blobServices/containers"
    assert find_edges(graph, blob, acct)
    assert find_edges(graph, cont, blob)
    assert not find_edges(graph, cont, acct)  # 직계 부모만


def test_subnet_references_nsg(graph: Graph) -> None:
    """골든: subnets → networkSecurityGroups (인라인 객체 이름 매칭)."""
    edges = find_edges(graph, SUBNET, NSG)
    ref = [e for e in edges if e.evidence == "bicep-ref"]
    assert len(ref) == 1
    assert ref[0].via_property == "properties.networkSecurityGroup"
    assert ref[0].confidence == 0.8


def test_nic_references_subnet_through_nested_array(graph: Graph) -> None:
    edges = find_edges(graph, NIC, SUBNET)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.evidence == "bicep-ref"
    assert edge.via_property == "properties.ipConfigurations.properties.subnet"
    assert edge.cardinality == "many"  # 배열을 거쳤으므로


def test_inline_child_listing_not_a_reference(graph: Graph) -> None:
    """vNet body의 subnets 배열은 자식 인라인 포함 — 참조 엣지를 만들지 않는다."""
    assert not [
        e for e in find_edges(graph, VNET, SUBNET) if e.type == "references"
    ]


def test_heuristic_string_id_property(graph: Graph) -> None:
    edges = [e for e in find_edges(graph, NIC, NSG) if e.evidence == "heuristic"]
    assert len(edges) == 1
    assert edges[0].via_property == "properties.networkSecurityGroupId"
    assert edges[0].confidence == 0.6


def test_readonly_property_skipped(graph: Graph) -> None:
    assert not [e for e in graph.edges if "internalDomainNameId" in e.via_property]


def test_no_heuristics_flag() -> None:
    g, _ = parse_index(load_index())
    extract_references(g, load_types(), heuristics=False)
    assert all(e.evidence != "heuristic" for e in g.edges)
    # bicep-ref 엣지는 유지
    assert [e for e in g.edges if e.evidence == "bicep-ref"]


def test_graph_validates(graph: Graph) -> None:
    graph.validate()
