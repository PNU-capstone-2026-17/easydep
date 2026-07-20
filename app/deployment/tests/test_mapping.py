"""매핑 레이어 테스트."""

from __future__ import annotations

import json

import pytest

from graphkb.model import Graph, Node
from graphkb.parsers.mapping import (
    build_graph,
    load_mappings,
    suggest,
    write_candidates,
)


@pytest.fixture(scope="module")
def graph() -> Graph:
    return build_graph(load_mappings())


def test_golden_vnet_equivalences(graph: Graph) -> None:
    """골든: core vNet ≡ AWS VPC ≡ Azure virtualNetworks ≡ GCP ComputeNetwork."""
    targets = {
        e.to_id
        for e in graph.edges
        if e.from_id == "core::vNet" and e.type == "equivalent_to"
    }
    assert targets == {
        "aws::AWS::EC2::VPC",
        "azure::Microsoft.Network/virtualNetworks",
        "gcp::ComputeNetwork",
    }


def test_all_edges_are_equivalent_to(graph: Graph) -> None:
    assert all(e.type == "equivalent_to" for e in graph.edges)
    assert all(e.evidence == "cb-spider-driver" for e in graph.edges)


def test_lossy_mappings_are_noted(graph: Graph) -> None:
    sg_gcp = [
        e
        for e in graph.edges
        if e.from_id == "core::securityGroup" and e.to_id == "gcp::ComputeFirewall"
    ]
    assert len(sg_gcp) == 1
    assert sg_gcp[0].basis == "inferred"  # 1:N 매핑은 신뢰도 하향


def test_no_equivalents_are_absent(graph: Graph) -> None:
    """등가물이 없는 조합(sshKey/gcp, spec, image, mci)은 엣지가 없어야 한다."""
    assert not [
        e for e in graph.edges if e.from_id == "core::sshKey" and e.to_id.startswith("gcp::")
    ]
    for core in ("core::spec", "core::image", "core::mci"):
        assert not [e for e in graph.edges if e.from_id == core]


def test_user_mapping_file_merges_and_overrides(tmp_path) -> None:
    user_file = tmp_path / "reviewed.json"
    user_file.write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "core": "mci",
                        "provider": "aws",
                        "target": "AWS::Test::Group",
                        "status": "confirmed",
                    },
                    {
                        "core": "vNet",
                        "provider": "aws",
                        "target": "AWS::EC2::VPC",
                        "status": "rejected",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    graph = build_graph(load_mappings(user_file))
    assert [e for e in graph.edges if e.from_id == "core::mci"]  # 추가 반영
    assert not [
        e
        for e in graph.edges
        if e.from_id == "core::vNet" and e.to_id == "aws::AWS::EC2::VPC"
    ]  # 사용자 검수(rejected)가 번들 confirmed를 덮어씀


def test_candidate_status_not_built(graph: Graph) -> None:
    extra = load_mappings()
    extra.append(
        {"core": "vNet", "provider": "aws", "target": "AWS::Fake::Thing", "status": "candidate"}
    )
    g = build_graph(extra)
    assert "aws::AWS::Fake::Thing" not in g.nodes


def test_suggest_finds_name_similar_candidates() -> None:
    core = Graph()
    core.add_node(Node(id="core::fileShare", layer="core", provider="common", display_name="fileShare", source="t"))
    vendor = Graph()
    vendor.add_node(Node(id="aws::AWS::EFS::FileShare", layer="vendor", provider="aws", display_name="AWS::EFS::FileShare", source="t"))
    vendor.add_node(Node(id="aws::AWS::EC2::VPC", layer="vendor", provider="aws", display_name="AWS::EC2::VPC", source="t"))
    candidates = suggest(core, {"aws": vendor})
    assert len(candidates) == 1
    assert candidates[0]["target"] == "AWS::EFS::FileShare"
    assert candidates[0]["status"] == "candidate"


def test_suggest_skips_confirmed_pairs() -> None:
    core = Graph()
    core.add_node(Node(id="core::subnet", layer="core", provider="common", display_name="subnet", source="t"))
    vendor = Graph()
    vendor.add_node(Node(id="aws::AWS::EC2::Subnet", layer="vendor", provider="aws", display_name="AWS::EC2::Subnet", source="t"))
    # (subnet, aws)는 번들에서 이미 confirmed → 후보 생성 안 함
    assert suggest(core, {"aws": vendor}) == []


def test_write_candidates_file(tmp_path) -> None:
    core = Graph()
    core.add_node(Node(id="core::fileShare", layer="core", provider="common", display_name="fileShare", source="t"))
    vendor = Graph()
    vendor.add_node(Node(id="gcp::FileShare", layer="vendor", provider="gcp", display_name="FileShare", source="t"))
    out = tmp_path / "candidates.json"
    write_candidates(out, core, {"gcp": vendor})
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["mappings"][0]["core"] == "fileShare"


def test_graph_validates(graph: Graph) -> None:
    graph.validate()
