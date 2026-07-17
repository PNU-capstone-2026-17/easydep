"""공통 데이터 모델 단위 테스트."""

from __future__ import annotations

import jsonschema
import pytest

from graphkb.model import Edge, Graph, Node


def make_node(node_id: str = "core::vNet") -> Node:
    return Node(
        id=node_id,
        layer="core",
        provider="common",
        display_name=node_id.split("::", 1)[1],
        source="cb-tumblebug-swagger",
    )


def make_edge(**overrides) -> Edge:
    base = {
        "from_id": "core::subnet",
        "to_id": "core::vNet",
        "type": "references",
        "via_property": "vNetId",
        "required": True,
        "cardinality": "one",
        "evidence": "swagger-field",
        "confidence": 1.0,
    }
    base.update(overrides)
    return Edge(**base)


def make_graph() -> Graph:
    graph = Graph()
    graph.add_node(make_node("core::vNet"))
    graph.add_node(make_node("core::subnet"))
    graph.add_edge(make_edge())
    return graph


def test_roundtrip() -> None:
    graph = make_graph()
    restored = Graph.from_dict(graph.to_dict())
    assert restored.to_dict() == graph.to_dict()


def test_serialized_edge_uses_from_to_keys() -> None:
    edge_dict = make_edge().to_dict()
    assert edge_dict["from"] == "core::subnet"
    assert edge_dict["to"] == "core::vNet"
    assert "from_id" not in edge_dict


def test_validate_passes_on_good_graph() -> None:
    make_graph().validate()


def test_validate_rejects_bad_confidence() -> None:
    graph = make_graph()
    graph.edges[0] = make_edge(confidence=2.0)
    with pytest.raises(jsonschema.ValidationError):
        graph.validate()


def test_validate_rejects_bad_evidence() -> None:
    graph = make_graph()
    graph.edges[0] = make_edge(evidence="guesswork")
    with pytest.raises(jsonschema.ValidationError):
        graph.validate()


def test_add_node_idempotent() -> None:
    graph = Graph()
    graph.add_node(make_node())
    graph.add_node(make_node())
    assert len(graph.nodes) == 1


def test_add_edge_dedup_keeps_higher_confidence() -> None:
    graph = make_graph()
    graph.add_edge(make_edge(evidence="heuristic", confidence=0.6))
    assert len(graph.edges) == 1
    assert graph.edges[0].confidence == 1.0

    graph2 = Graph()
    graph2.add_node(make_node("core::vNet"))
    graph2.add_node(make_node("core::subnet"))
    graph2.add_edge(make_edge(evidence="heuristic", confidence=0.6))
    graph2.add_edge(make_edge(evidence="swagger-field", confidence=1.0))
    assert len(graph2.edges) == 1
    assert graph2.edges[0].evidence == "swagger-field"


def test_add_edge_rejects_self_loop() -> None:
    graph = Graph()
    graph.add_node(make_node("core::vNet"))
    graph.add_edge(make_edge(from_id="core::vNet", to_id="core::vNet"))
    assert graph.edges == []


def test_merge_combines_graphs() -> None:
    left = make_graph()
    right = Graph()
    right.add_node(make_node("core::vNet"))
    right.add_node(make_node("core::securityGroup"))
    right.add_edge(
        make_edge(from_id="core::securityGroup", via_property="vNetId")
    )
    left.merge(right)
    assert set(left.nodes) == {"core::vNet", "core::subnet", "core::securityGroup"}
    assert len(left.edges) == 2


def test_save_and_load(tmp_path) -> None:
    graph = make_graph()
    path = tmp_path / "out" / "graph.json"
    graph.save(path)
    restored = Graph.load(path)
    assert restored.to_dict() == graph.to_dict()
