"""GraphML/DOT 내보내기 테스트."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.core.cloudkb.graphkb.export import write_dot, write_graphml
from app.core.cloudkb.graphkb.model import Edge, Graph, Node


@pytest.fixture
def graph() -> Graph:
    g = Graph()
    g.add_node(Node(id="core::vNet", layer="core", provider="common", display_name="vNet", source="t"))
    g.add_node(Node(id="core::subnet", layer="core", provider="common", display_name="subnet", source="t"))
    g.add_edge(
        Edge(
            from_id="core::subnet",
            to_id="core::vNet",
            type="contained_in",
            via_property="subnetInfoList",
            required=True,
            cardinality="one",
            evidence="swagger-field",
        )
    )
    g.add_edge(
        Edge(
            from_id="core::subnet",
            to_id="core::vNet",
            type="references",
            via_property="vNetId",
            required=True,
            cardinality="one",
            evidence="heuristic",
        )
    )
    return g


def test_graphml_roundtrips_through_elementtree(graph: Graph, tmp_path) -> None:
    path = tmp_path / "g.graphml"
    write_graphml(graph, path)
    tree = ET.parse(path)
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    nodes = tree.findall(".//g:node", ns)
    edges = tree.findall(".//g:edge", ns)
    assert len(nodes) == 2
    assert len(edges) == 2
    assert {n.get("id") for n in nodes} == {"core::vNet", "core::subnet"}
    assert edges[0].get("source") == "core::subnet"
    assert edges[0].get("target") == "core::vNet"


def test_dot_contains_expected_lines(graph: Graph, tmp_path) -> None:
    path = tmp_path / "g.dot"
    write_dot(graph, path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("digraph graphkb {")
    assert '"core::subnet" -> "core::vNet"' in text
    assert "shape=box" in text  # 코어 노드
    assert "style=dashed" in text  # 짐작(heuristic)은 점선
    assert "color=gray" in text  # contained_in
