"""query 모듈 단위 테스트 (수작업 그래프 사용, 파서 불필요)."""

from __future__ import annotations

import pytest

from graphkb.model import Edge, Graph, Node
from graphkb.query import (
    dependency_chain,
    dependency_chain_detail,
    dependents,
    rank_types,
    resolve_node,
)


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


def _two_cycle() -> Graph:
    """a ↔ b 상호 참조 — 실제 데이터의 Lambda::Function ↔ S3::Bucket과 같은 모양."""
    graph = Graph()
    graph.add_node(node("core::a"))
    graph.add_node(node("core::b"))
    graph.add_edge(edge("core::a", "core::b"))
    graph.add_edge(edge("core::b", "core::a"))
    return graph


def test_dependency_chain_cycle_keeps_target_last() -> None:
    """**C1 회귀**: 대상이 순환에 걸려도 마지막 자리를 지킨다.

    예전엔 남은 노드를 BFS 순서로 뒤에 덧붙여서, 대상이 그 첫 원소가 되고
    진짜 선행 리소스가 대상 뒤로 밀렸다(3,225개 중 503개).
    """
    result = dependency_chain_detail(_two_cycle(), "core::a")
    assert result.has_cycle
    assert result.ordered[-1].id == "core::a"
    # a↔b는 한 단계로 묶인다 — 둘 중 누가 먼저인지는 스키마가 답할 수 없다.
    assert len(result.steps) == 1
    assert [n.id for n in result.steps[0].nodes] == ["core::b", "core::a"]


def test_cycle_does_not_swallow_downstream_order() -> None:
    """순환에 *딸린* 노드까지 순서를 잃지 않는다.

    Kahn이 못 놓은 걸 전부 한 바구니에 넣으면 c와 d의 확정 순서까지 버려진다
    (실측: EC2::Instance에서 22개가 그렇게 쓸려 들어갔다).
    """
    graph = Graph()
    for nid in ("core::a", "core::b", "core::c", "core::d"):
        graph.add_node(node(nid))
    graph.add_edge(edge("core::a", "core::b"))  # a ↔ b 순환
    graph.add_edge(edge("core::b", "core::a"))
    graph.add_edge(edge("core::b", "core::c"))  # 순환에 딸린 선행들
    graph.add_edge(edge("core::c", "core::d"))

    result = dependency_chain_detail(graph, "core::a")
    assert [[n.id for n in s.nodes] for s in result.steps] == [
        ["core::d"],
        ["core::c"],
        ["core::b", "core::a"],
    ]
    assert len(result.cyclic_steps) == 1


def test_dependency_chain_flattens_with_target_last() -> None:
    """평탄화해도 대상은 마지막 — dependency_chain의 오래된 계약."""
    assert [n.id for n in dependency_chain(_two_cycle(), "core::a")] == [
        "core::b",
        "core::a",
    ]


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


# --- creation_order: 필수와 선택을 나눠 보여준다 (2026-07-21) ---


def _order_graph() -> Graph:
    """정책은 저장소가 필수, 저장소는 접근점이 선택 — 실제 S3 모양."""
    graph = Graph()
    for nid in ("aws::Bucket", "aws::Policy", "aws::AccessPoint", "aws::LogGroup"):
        graph.add_node(node(nid))
    graph.add_edge(edge("aws::Policy", "aws::Bucket", required=True))
    graph.add_edge(edge("aws::Bucket", "aws::AccessPoint", required=False))
    graph.add_edge(edge("aws::Bucket", "aws::LogGroup", required=False))
    return graph


def test_creation_order_orders_only_the_required_chain(monkeypatch, tmp_path) -> None:
    """필수만 위상정렬한다 — 선택까지 섞으면 무관한 타입이 딸려와 뒤엉킨다.

    실측: S3::BucketPolicy가 15단계에 순환 3그룹으로 나왔는데 실제 답은 2단계였다.
    """
    from graphkb import agent_api

    monkeypatch.setattr(agent_api, "load_merged", lambda *a, **k: _order_graph())
    text = agent_api.creation_order("aws::Policy")

    assert "1. aws::Bucket" in text
    assert "2. aws::Policy ← 대상" in text
    # 저장소의 선택 의존(접근점·로그그룹)은 정책의 순서에 끼어들지 않는다
    assert "aws::AccessPoint" not in text


def test_creation_order_still_lists_optional_dependencies(monkeypatch) -> None:
    """필수가 없다고 "바로 만들 수 있다"고 하면 거짓말이 된다.

    스키마상 required가 아닌 실질 의존이 많아 469개 타입이 그렇게 된다 —
    APS::Scraper는 실제로 구역·작업공간이 필요한데도 필수 체인이 자기뿐이다.
    """
    from graphkb import agent_api

    monkeypatch.setattr(agent_api, "load_merged", lambda *a, **k: _order_graph())
    text = agent_api.creation_order("aws::Bucket")

    assert "반드시 먼저 있어야 하는 것은 없습니다" in text
    assert "함께 쓸 수 있는 것 (2개" in text
    assert "aws::AccessPoint" in text and "aws::LogGroup" in text
    # 대상 자신만 있는 목록은 정보가 아니므로 찍지 않는다
    assert "1. aws::Bucket" not in text


def test_creation_order_required_only_suppresses_the_optional_list(monkeypatch) -> None:
    from graphkb import agent_api

    monkeypatch.setattr(agent_api, "load_merged", lambda *a, **k: _order_graph())
    text = agent_api.creation_order("aws::Bucket", required_only=True)

    assert "바로 생성할 수 있습니다" in text
    assert "aws::AccessPoint" not in text
