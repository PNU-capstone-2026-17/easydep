"""query 모듈 단위 테스트 (수작업 그래프 사용, 파서 불필요)."""

from __future__ import annotations

import pytest

from graphkb.model import Edge, Graph, Node
from graphkb.query import dependency_chain, dependents, rank_types, resolve_node


def node(node_id: str, layer: str = "core", provider: str = "common") -> Node:
    return Node(
        id=node_id,
        layer=layer,
        provider=provider,
        display_name=node_id.split("::", 1)[1],
        source="test",
    )


def edge(from_id: str, to_id: str, *, required: bool = True, type_: str = "references") -> Edge:
    return Edge(
        from_id=from_id,
        to_id=to_id,
        type=type_,
        via_property="x",
        required=required,
        cardinality="one",
        evidence="swagger-field",
        confidence=1.0,
    )


@pytest.fixture
def core_graph() -> Graph:
    """vNet ← subnet ← vm, vNet ← securityGroup ← vm, spec ← vm 형태의 그래프."""
    graph = Graph()
    for nid in ("core::vNet", "core::subnet", "core::securityGroup", "core::vm", "core::spec"):
        graph.add_node(node(nid))
    graph.add_edge(edge("core::subnet", "core::vNet", type_="contained_in"))
    graph.add_edge(edge("core::securityGroup", "core::vNet"))
    graph.add_edge(edge("core::vm", "core::subnet"))
    graph.add_edge(edge("core::vm", "core::securityGroup"))
    graph.add_edge(edge("core::vm", "core::spec", required=False))
    return graph


def test_resolve_exact_id(core_graph: Graph) -> None:
    assert resolve_node(core_graph, "core::vNet").id == "core::vNet"


def test_resolve_display_name_case_insensitive(core_graph: Graph) -> None:
    assert resolve_node(core_graph, "VNET").id == "core::vNet"


def test_resolve_unknown_raises(core_graph: Graph) -> None:
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        resolve_node(core_graph, "nope")


def test_resolve_ambiguous_lists_candidates() -> None:
    graph = Graph()
    graph.add_node(node("core::subnet"))
    graph.add_node(node("aws::Subnet", layer="vendor", provider="aws"))
    with pytest.raises(ValueError, match="모호"):
        resolve_node(graph, "subnet")


def test_dependency_chain_topological_order(core_graph: Graph) -> None:
    chain = [n.id for n in dependency_chain(core_graph, "core::vm")]
    assert chain[-1] == "core::vm"
    assert set(chain) == {
        "core::vNet", "core::subnet", "core::securityGroup", "core::vm", "core::spec",
    }
    assert chain.index("core::vNet") < chain.index("core::subnet")
    assert chain.index("core::vNet") < chain.index("core::securityGroup")
    assert chain.index("core::subnet") < chain.index("core::vm")


def test_dependency_chain_required_only(core_graph: Graph) -> None:
    chain = [n.id for n in dependency_chain(core_graph, "core::vm", required_only=True)]
    assert "core::spec" not in chain
    assert "core::subnet" in chain


def test_dependency_chain_leaf_is_self_only(core_graph: Graph) -> None:
    assert [n.id for n in dependency_chain(core_graph, "core::vNet")] == ["core::vNet"]


def test_dependency_chain_cycle_fallback(capsys) -> None:
    graph = Graph()
    graph.add_node(node("core::a"))
    graph.add_node(node("core::b"))
    graph.add_edge(edge("core::a", "core::b"))
    graph.add_edge(edge("core::b", "core::a"))
    chain = [n.id for n in dependency_chain(graph, "core::a")]
    assert set(chain) == {"core::a", "core::b"}
    assert "사이클" in capsys.readouterr().err


def test_dependents_reverse_closure(core_graph: Graph) -> None:
    affected = {n.id for n in dependents(core_graph, "core::vNet")}
    assert affected == {"core::subnet", "core::securityGroup", "core::vm"}
    assert {n.id for n in dependents(core_graph, "core::vm")} == set()


def test_rank_types_by_dependencies(core_graph: Graph) -> None:
    """vm은 subnet/securityGroup/spec 3개에 의존 → 1위."""
    ranked = rank_types(core_graph, by="dependencies")
    assert (ranked[0][0].id, ranked[0][1]) == ("core::vm", 3)


def test_rank_types_by_dependents(core_graph: Graph) -> None:
    """vNet에는 subnet/securityGroup 2개가 의존 → 1위."""
    ranked = rank_types(core_graph, by="dependents")
    assert (ranked[0][0].id, ranked[0][1]) == ("core::vNet", 2)


def test_rank_counts_distinct_types_not_edges() -> None:
    """한 타입을 여러 프로퍼티로 참조해도 의존 대상은 하나다."""
    graph = Graph()
    graph.add_node(node("core::vm"))
    graph.add_node(node("core::vNet"))
    graph.add_edge(
        Edge(
            from_id="core::vm", to_id="core::vNet", type="references",
            via_property="vNetId", required=True, cardinality="one",
            evidence="swagger-field", confidence=1.0,
        )
    )
    graph.add_edge(
        Edge(
            from_id="core::vm", to_id="core::vNet", type="references",
            via_property="defaultVNetId", required=False, cardinality="one",
            evidence="swagger-field", confidence=1.0,
        )
    )
    assert rank_types(graph, by="dependencies")[0][1] == 1


def test_rank_types_provider_filter() -> None:
    graph = Graph()
    graph.add_node(node("core::vm"))
    graph.add_node(node("core::vNet"))
    graph.add_node(node("aws::AWS::EC2::Subnet", layer="vendor", provider="aws"))
    graph.add_node(node("aws::AWS::EC2::VPC", layer="vendor", provider="aws"))
    graph.add_edge(edge("core::vm", "core::vNet"))
    graph.add_edge(edge("aws::AWS::EC2::Subnet", "aws::AWS::EC2::VPC"))
    ranked = rank_types(graph, by="dependencies", provider="aws")
    assert [n.id for n, _ in ranked] == ["aws::AWS::EC2::Subnet"]


def test_rank_types_required_only(core_graph: Graph) -> None:
    """spec은 required=False라 required_only면 vm의 의존 수가 줄어든다."""
    assert rank_types(core_graph, by="dependencies")[0][1] == 3
    assert rank_types(core_graph, by="dependencies", required_only=True)[0][1] == 2


def test_rank_types_limit_and_ordering(core_graph: Graph) -> None:
    ranked = rank_types(core_graph, by="dependencies", limit=2)
    assert len(ranked) == 2
    assert ranked[0][1] >= ranked[1][1]  # 내림차순


def test_rank_types_rejects_bad_axis(core_graph: Graph) -> None:
    with pytest.raises(ValueError, match="dependencies"):
        rank_types(core_graph, by="vibes")


def test_equivalent_to_ignored_for_ordering() -> None:
    graph = Graph()
    graph.add_node(node("core::vNet"))
    graph.add_node(node("aws::AWS::EC2::VPC", layer="vendor", provider="aws"))
    graph.add_edge(edge("core::vNet", "aws::AWS::EC2::VPC", type_="equivalent_to"))
    assert [n.id for n in dependency_chain(graph, "core::vNet")] == ["core::vNet"]
    assert dependents(graph, "aws::AWS::EC2::VPC") == []
