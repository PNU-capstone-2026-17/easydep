"""agent_api(사전 정의 질의) + query.equivalents 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.deployment.graphkb import agent_api
from app.deployment.graphkb.model import Edge, Graph, Node
from app.deployment.graphkb.query import equivalents


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


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
    assert items[-1].endswith("← target")
    assert "core::vm" in items[-1]
    vnet_pos = next(i for i, line in enumerate(items) if "core::vNet" in line)
    vm_pos = next(i for i, line in enumerate(items) if "core::vm" in line)
    assert vnet_pos < vm_pos


def test_creation_order_leaf(output_dir: Path) -> None:
    """필수 표시가 없어도 **"바로 만들어도 된다"고 말하지 않는다.**

    스키마의 필수 표시는 실제 배포 요건과 다르다 — Azure VM은 networkProfile이
    스키마상 선택이라 필수 목록이 비지만 NIC 없이는 만들 수 없다. 침묵을 허가로
    바꿔 말하면 fail-open이 아니라 그냥 틀린 말이 된다.
    """
    text = flat(agent_api.creation_order("vNet", output_dir=output_dir))
    assert "can be created" not in text, "침묵을 허가로 바꿔 말하고 있다"
    assert "**marks no prerequisite as required**" in text
    assert "does not mean nothing is actually needed" in text


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
    # 내부 id의 `core::` 접두사는 사용자에게 보이지 않는다 (kbcommon.display)
    assert "references → subnet" in text
    assert "core::" not in text
    assert "via x (required, one," in flat(text)


def test_search_types(output_dir: Path) -> None:
    text = agent_api.search_types("vpc", output_dir=output_dir)
    assert "aws::AWS::EC2::VPC" in text
    text2 = agent_api.search_types("vpc", provider="gcp", output_dir=output_dir)
    assert "No type matches 'vpc'" in flat(text2)


def test_agent_api_rank_types(output_dir: Path) -> None:
    """subnet→vNet, vm→subnet 이라 둘 다 의존 1개 (동점은 id 사전순으로 결정적)."""
    text = flat(agent_api.rank_types("dependencies", output_dir=output_dir))
    assert "core::subnet — 1" in text
    assert "core::vm — 1" in text
    assert "core::vNet" not in text.split("Top")[1]  # vNet은 의존하는 게 없다


def test_agent_api_rank_types_by_dependents(output_dir: Path) -> None:
    text = flat(agent_api.rank_types("dependents", output_dir=output_dir))
    assert "core::vNet" in text
    assert "what is affected when you delete it" in text


def test_agent_api_rank_types_provider_filter(output_dir: Path) -> None:
    text = agent_api.rank_types("dependencies", provider="gcp", output_dir=output_dir)
    assert "No types available to rank" in flat(text)


def test_agent_api_rank_types_bad_axis(output_dir: Path) -> None:
    assert "dependencies" in agent_api.rank_types("vibes", output_dir=output_dir)


def test_unknown_name_returns_message_not_exception(output_dir: Path) -> None:
    text = agent_api.creation_order("nope", output_dir=output_dir)
    assert "Node not found: 'nope'" in flat(text)


def test_missing_output_dir_message(tmp_path) -> None:
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.creation_order("vm", output_dir=tmp_path / "empty")
    assert "graphkb build" in text
