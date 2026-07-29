"""Neo4j 적재/Cypher 내보내기 테스트 (서버 없이 스크립트 생성만 검증)."""

from __future__ import annotations

from app.core.cloudkb.graphkb.model import Edge, Graph, Node
from app.core.cloudkb.graphkb.neo4j_load import CONSTRAINT, REL_TYPES, cypher_script


def make_graph() -> Graph:
    graph = Graph()
    graph.add_node(
        Node(id="core::vNet", layer="core", provider="common", display_name="vNet", source="t")
    )
    graph.add_node(
        Node(
            id="aws::AWS::EC2::VPC",
            layer="vendor",
            provider="aws",
            display_name="AWS's \"VPC\"",  # 이스케이프 검증용
            source="t",
        )
    )
    graph.add_edge(
        Edge(
            from_id="core::vNet",
            to_id="aws::AWS::EC2::VPC",
            type="equivalent_to",
            via_property="",
            required=False,
            cardinality="one",
            evidence="cb-spider-driver",
        )
    )
    return graph


def test_script_starts_with_constraint() -> None:
    script = cypher_script(make_graph())
    assert script.startswith(CONSTRAINT + ";")


def test_script_merges_nodes_and_edges() -> None:
    script = cypher_script(make_graph())
    assert "MERGE (n:ResourceType {id: 'core::vNet'})" in script
    assert "MERGE (a)-[r:EQUIVALENT_TO {via_property: ''}]->(b)" in script
    assert 'r.basis = "inferred"' in script
    assert "r.required = false" in script


def test_single_quote_escaped() -> None:
    script = cypher_script(make_graph())
    assert "AWS\\'s" in script


def test_all_edge_types_have_rel_mapping() -> None:
    assert set(REL_TYPES) == {"references", "contained_in", "equivalent_to"}


def test_statement_per_line_ends_with_semicolon() -> None:
    script = cypher_script(make_graph())
    lines = [line for line in script.strip().splitlines() if line]
    assert all(line.endswith(";") for line in lines)
    assert len(lines) == 1 + 2 + 1  # 제약 + 노드 2 + 엣지 1
