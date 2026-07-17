"""agent_api(사전 정의 질의) + query.equivalents 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphkb import agent_api
from graphkb.model import Edge, Graph, Node
from graphkb.query import equivalents


def node(node_id: str, layer: str = "core", provider: str = "common") -> Node:
    return Node(
        id=node_id,
        layer=layer,
        provider=provider,
        display_name=node_id.split("::", 1)[1],
        source="test",
    )


def edge(from_id: str, to_id: str, *, type_: str = "references", required: bool = True) -> Edge:
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


@pytest.fixture()
def output_dir(tmp_path) -> Path:
    """core + mapping + aws 그래프 산출물이 있는 output 디렉터리를 흉내낸다."""
    core = Graph()
    for nid in ("core::vNet", "core::subnet", "core::vm"):
        core.add_node(node(nid))
    core.add_edge(edge("core::subnet", "core::vNet", type_="contained_in"))
    core.add_edge(edge("core::vm", "core::subnet"))
    core.save(tmp_path / "core-graph.json")

    aws = Graph()
    aws.add_node(node("aws::AWS::EC2::VPC", layer="vendor", provider="aws"))
    aws.save(tmp_path / "aws-graph.json")

    mapping = Graph()
    mapping.add_node(node("core::vNet"))
    mapping.add_node(node("aws::AWS::EC2::VPC", layer="vendor", provider="aws"))
    mapping.add_node(node("gcp::ComputeNetwork", layer="vendor", provider="gcp"))
    mapping.add_edge(
        Edge(
            from_id="core::vNet",
            to_id="aws::AWS::EC2::VPC",
            type="equivalent_to",
            via_property="",
            required=False,
            cardinality="one",
            evidence="cb-spider-driver",
            confidence=0.95,
        )
    )
    mapping.add_edge(
        Edge(
            from_id="core::vNet",
            to_id="gcp::ComputeNetwork",
            type="equivalent_to",
            via_property="",
            required=False,
            cardinality="one",
            evidence="cb-spider-driver",
            confidence=0.95,
        )
    )
    mapping.save(tmp_path / "mapping-graph.json")

    agent_api._load_merged_cached.cache_clear()
    return tmp_path


def test_equivalents_transitive_undirected(output_dir: Path) -> None:
    graph = agent_api.load_merged(output_dir)
    peers = {n.id for n in equivalents(graph, "aws::AWS::EC2::VPC")}
    # aws → core(역방향) → gcp(추이적)
    assert peers == {"core::vNet", "gcp::ComputeNetwork"}


def test_creation_order_text(output_dir: Path) -> None:
    text = agent_api.creation_order("vm", output_dir=output_dir)
    items = [line for line in text.splitlines() if line and line[0].isdigit()]
    assert any("core::vNet" in line for line in items)
    assert items[-1].endswith("← 대상")
    assert "core::vm" in items[-1]
    vnet_pos = next(i for i, line in enumerate(items) if "core::vNet" in line)
    vm_pos = next(i for i, line in enumerate(items) if "core::vm" in line)
    assert vnet_pos < vm_pos


def test_creation_order_leaf(output_dir: Path) -> None:
    text = agent_api.creation_order("vNet", output_dir=output_dir)
    assert "바로 생성" in text


def test_deletion_impact_text(output_dir: Path) -> None:
    text = agent_api.deletion_impact("vNet", output_dir=output_dir)
    assert "core::subnet" in text
    assert "core::vm" in text


def test_equivalent_types_text(output_dir: Path) -> None:
    text = agent_api.equivalent_types("AWS::EC2::VPC", output_dir=output_dir)
    assert "core::vNet" in text
    assert "gcp::ComputeNetwork" in text


def test_describe_type_text(output_dir: Path) -> None:
    text = agent_api.describe_type("vm", output_dir=output_dir)
    assert "references → core::subnet" in text
    assert "필수" in text


def test_search_types(output_dir: Path) -> None:
    text = agent_api.search_types("vpc", output_dir=output_dir)
    assert "aws::AWS::EC2::VPC" in text
    text2 = agent_api.search_types("vpc", provider="gcp", output_dir=output_dir)
    assert "없습니다" in text2


def test_agent_api_rank_types(output_dir: Path) -> None:
    """subnet→vNet, vm→subnet 이라 둘 다 의존 1개 (동점은 id 사전순으로 결정적)."""
    text = agent_api.rank_types("dependencies", output_dir=output_dir)
    assert "core::subnet — 1개" in text
    assert "core::vm — 1개" in text
    assert "core::vNet" not in text.split("상위")[1]  # vNet은 의존하는 게 없다


def test_agent_api_rank_types_by_dependents(output_dir: Path) -> None:
    text = agent_api.rank_types("dependents", output_dir=output_dir)
    assert "core::vNet" in text
    assert "삭제 시 영향" in text


def test_agent_api_rank_types_provider_filter(output_dir: Path) -> None:
    text = agent_api.rank_types("dependencies", provider="gcp", output_dir=output_dir)
    assert "없습니다" in text


def test_agent_api_rank_types_bad_axis(output_dir: Path) -> None:
    assert "dependencies" in agent_api.rank_types("vibes", output_dir=output_dir)


def test_unknown_name_returns_message_not_exception(output_dir: Path) -> None:
    text = agent_api.creation_order("nope", output_dir=output_dir)
    assert "찾을 수 없습니다" in text


def test_missing_output_dir_message(tmp_path) -> None:
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.creation_order("vm", output_dir=tmp_path / "empty")
    assert "graphkb build" in text
