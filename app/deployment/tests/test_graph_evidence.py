"""그래프 축도 **근거를 출력한다**.

용량 축은 5개 도구 전부 근거를 내는데 그래프 축은 6개 중 3개가 버리고 있었다
(`equivalent_types`·`creation_order`·`deletion_impact`). 그래서 짐작이 답변에서
단언이 됐다 — 실측에서 "AWS ALB는 GCP에서 뭐야?"에 모델이 `ComputeForwardingRule`을
단언했고, 데이터의 basis는 `짐작(검수됨)`인데 출력에 안 실려 모델이 알 방법이 없었다.

**동치는 코어를 거쳐 이어진다**(`aws VPC → core vNet → gcp ComputeNetwork`). 그래서
두 벤더 타입 사이에는 직접 엣지가 없고 경로의 근거가 곧 그 쌍의 근거다 — **여러
고리 중 하나라도 짐작이면 결과도 짐작**이다.
"""

from __future__ import annotations

import pytest

from graphkb.agent_api import (
    _evidence_footer,
    _weakest_link,
    creation_order,
    deletion_impact,
    equivalent_types,
)
from graphkb.model import Edge, Graph, Node


def _node(node_id: str, provider: str, layer: str = "vendor") -> Node:
    return Node(
        id=node_id, layer=layer, provider=provider,
        display_name=node_id.split("::", 1)[-1], source="t",
    )


@pytest.fixture
def graph() -> Graph:
    g = Graph()
    for node_id, provider, layer in (
        ("core::vNet", "common", "core"),
        ("aws::AWS::EC2::VPC", "aws", "vendor"),
        ("gcp::ComputeNetwork", "gcp", "vendor"),
        ("aws::AWS::EC2::Subnet", "aws", "vendor"),
        ("aws::AWS::Guess::Thing", "aws", "vendor"),
    ):
        g.add_node(_node(node_id, provider, layer))

    def edge(a, b, kind, evidence, required=True, reviewed=False):
        return Edge(from_id=a, to_id=b, type=kind, via_property="x",
                    required=required, cardinality="one", evidence=evidence,
                    reviewed=reviewed)

    # 동치: 한쪽은 원본 선언, 한쪽은 짐작 — 경로를 거치면 약한 쪽이 결과가 된다
    g.add_edge(edge("core::vNet", "aws::AWS::EC2::VPC", "equivalent_to", "human-review"))
    g.add_edge(edge("core::vNet", "gcp::ComputeNetwork", "equivalent_to", "cb-spider-driver"))
    # 의존: 명시 하나 + 짐작 하나
    g.add_edge(edge("aws::AWS::EC2::Subnet", "aws::AWS::EC2::VPC", "references", "relationshipRef"))
    g.add_edge(edge("aws::AWS::Guess::Thing", "aws::AWS::EC2::VPC", "references", "heuristic"))
    return g


def test_equivalent_types_shows_basis(graph, tmp_path, monkeypatch) -> None:
    """짐작이 단언으로 나가지 않도록 근거가 줄마다 붙는다."""
    monkeypatch.setattr("graphkb.agent_api.load_merged", lambda output_dir=None: graph)
    text = equivalent_types("AWS::EC2::VPC", output_dir=tmp_path)
    assert "gcp::ComputeNetwork" in text
    assert "근거" in text
    assert "짐작" in text


def test_transitive_equivalence_takes_the_weakest_link(graph) -> None:
    """`aws VPC ↔ gcp Network`는 직접 엣지가 없다 — 경로의 약한 고리가 근거다."""
    edge = _weakest_link(graph, "aws::AWS::EC2::VPC", "gcp::ComputeNetwork")
    assert edge is not None
    assert edge.evidence == "cb-spider-driver"  # 짐작 쪽
    assert edge.basis == "inferred"


def test_guessed_equivalence_gets_a_warning(graph, tmp_path, monkeypatch) -> None:
    """클라우드마다 리소스를 나누는 결이 달라 딱 맞는 짝이 없을 수 있다."""
    monkeypatch.setattr("graphkb.agent_api.load_merged", lambda output_dir=None: graph)
    text = equivalent_types("AWS::EC2::VPC", output_dir=tmp_path)
    assert "가장 가까운 것" in text


def test_deletion_impact_summarises_evidence(graph, tmp_path, monkeypatch) -> None:
    """466건짜리 목록에 줄마다 붙이면 노이즈라 **블록당 한 번** 밝힌다."""
    monkeypatch.setattr("graphkb.agent_api.load_merged", lambda output_dir=None: graph)
    text = deletion_impact("AWS::EC2::VPC", output_dir=tmp_path)
    assert "이 관계들의 근거" in text
    assert "이름 규칙 추정" in text
    assert "이름 추론에서 나온 것" in text  # 짐작 비율 경고


def test_creation_order_shows_evidence(graph, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("graphkb.agent_api.load_merged", lambda output_dir=None: graph)
    text = creation_order("AWS::EC2::Subnet", output_dir=tmp_path)
    assert "이 관계들의 근거" in text


def test_reviewed_guess_still_reads_as_a_guess(graph) -> None:
    """**검수는 확인이지 원본이 선언했다는 뜻이 아니다.**

    실제 데이터에서 이름 추론 엣지 1,113건이 전부 `reviewed=True`인데, 검수 파일이
    "(3)은 표본 판단이므로 전수 개별 확인은 아니다"라고 밝혀 뒀다. 그러니 출력도
    "짐작(검수됨)"이어야 하고 그냥 "명시"로 승격되면 안 된다.
    """
    reviewed_guess = Edge(
        from_id="a", to_id="b", type="references", via_property="x",
        required=False, cardinality="one", evidence="heuristic", reviewed=True,
    )
    g = Graph()
    for n in ("a", "b"):
        g.add_node(_node(f"aws::{n}", "aws"))
    from kbcommon.basis import describe

    assert "짐작" in describe(reviewed_guess.basis, reviewed_guess.reviewed)
    assert "검수" in describe(reviewed_guess.basis, reviewed_guess.reviewed)


def test_footer_is_none_without_edges(graph) -> None:
    """관계가 없으면 아무 말도 만들지 않는다."""
    assert _evidence_footer(graph, set(), "aws::AWS::Nope::Nope") is None
