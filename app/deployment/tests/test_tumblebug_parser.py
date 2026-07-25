"""Tumblebug 파서 테스트 — 브리프 완료 기준 1의 골든 케이스 검증.

fixture는 실제 v0.11.8 swagger.json에서 발췌한 축소본으로, 오프라인 실행.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphkb.model import Edge, Graph
from graphkb.parsers.tumblebug import parse_swagger
from graphkb.query import dependency_chain

FIXTURE = Path(__file__).parent / "fixtures" / "tumblebug-swagger-min.json"


@pytest.fixture(scope="module")
def graph() -> Graph:
    spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return parse_swagger(spec)


def find_edges(graph: Graph, from_id: str, to_id: str) -> list[Edge]:
    return [e for e in graph.edges if e.from_id == from_id and e.to_id == to_id]


def test_at_least_eight_core_nodes(graph: Graph) -> None:
    assert len(graph.nodes) >= 8
    assert all(node.layer == "core" for node in graph.nodes.values())
    assert all(node.provider == "common" for node in graph.nodes.values())


def test_security_group_references_vnet(graph: Graph) -> None:
    edges = find_edges(graph, "core::securityGroup", "core::vNet")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.type == "references"
    assert edge.via_property == "vNetId"
    assert edge.required is True
    assert edge.evidence == "swagger-field"
    assert edge.basis == "stated"


def test_subnet_contained_in_vnet(graph: Graph) -> None:
    edges = find_edges(graph, "core::subnet", "core::vNet")
    contained = [e for e in edges if e.type == "contained_in"]
    assert len(contained) == 1
    assert contained[0].via_property == "subnetInfoList"


def test_vm_references_all_prerequisites(graph: Graph) -> None:
    for target, via in [
        ("core::vNet", "vNetId"),
        ("core::subnet", "subnetId"),
        ("core::securityGroup", "securityGroupIds"),
        ("core::sshKey", "sshKeyId"),
        ("core::spec", "specId"),
        ("core::image", "imageId"),
    ]:
        edges = [
            e
            for e in find_edges(graph, "core::vm", target)
            if e.via_property == via
        ]
        assert len(edges) == 1, f"vm → {target} ({via}) 엣지 누락"
        assert edges[0].required is True


def test_security_group_ids_cardinality_many(graph: Graph) -> None:
    edges = find_edges(graph, "core::vm", "core::securityGroup")
    assert edges[0].cardinality == "many"


def test_vm_contained_in_mci(graph: Graph) -> None:
    edges = find_edges(graph, "core::vm", "core::mci")
    assert any(e.type == "contained_in" for e in edges)


def test_nlb_references_vm_via_nested_target_group(graph: Graph) -> None:
    edges = find_edges(graph, "core::nlb", "core::vm")
    assert len(edges) == 1
    assert edges[0].via_property == "targetGroup.subGroupId"


def test_k8s_cluster_references(graph: Graph) -> None:
    assert find_edges(graph, "core::k8sCluster", "core::vNet")
    assert find_edges(graph, "core::k8sNodeGroup", "core::k8sCluster")


def test_dependency_chain_reproduces_brief_chain(graph: Graph) -> None:
    """완료 기준 1: vNet → subnet → securityGroup → VM 체인 재현."""
    chain = [n.id for n in dependency_chain(graph, "core::vm")]
    assert chain[-1] == "core::vm"
    for nid in ("core::vNet", "core::subnet", "core::securityGroup"):
        assert nid in chain
    assert chain.index("core::vNet") < chain.index("core::subnet")
    assert chain.index("core::subnet") < chain.index("core::vm")
    assert chain.index("core::securityGroup") < chain.index("core::vm")


def test_graph_validates(graph: Graph) -> None:
    graph.validate()


def test_missing_definition_warns_not_crashes(capsys) -> None:
    spec = {"swagger": "2.0", "definitions": {}}
    graph = parse_swagger(spec)
    assert graph.nodes == {}
    assert "건너뜀" in capsys.readouterr().err
