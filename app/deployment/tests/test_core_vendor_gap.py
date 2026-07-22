"""core 레이어와 벤더 레이어가 '무엇이 필수인가'를 다르게 말할 때.

**사용자가 잡아낸 결함이다.** "aws vm을 물으면 혼자 만들 수 있다는데, 일반적으로
물으면 선행 리소스가 있어야 한다고 한다 — 불일치 아니냐."

    core::vm                 반드시 먼저 있어야 하는 것 7개
    aws::AWS::EC2::Instance  필수 0개

둘 다 사실이다. `core::vm`은 cb-tumblebug REST 스키마에서 왔고 거기서 `vNetId`·
`subnetId`·`securityGroupIds`가 required다. `AWS::EC2::Instance`는 CFN에서 왔고
CFN은 `SubnetId`를 선택으로 둔다(기본 VPC).

문제는 **우리 실행 경로가 cb-tumblebug**이라는 것이다. 벤더 스키마만 보고
"필수 없음"이라 답하면 실제 배포에서 틀린다. 실측상 이 어긋남은 5개 코어 타입에
있었고 vm은 9/9 CSP 전부 0을 말했다.

**해결은 감추는 것이 아니라 나란히 놓는 것이다** — 어느 한쪽을 고르면 그건 우리가
정한 게 되고, 실제로는 무엇으로 만드느냐에 달렸다.
"""

from __future__ import annotations

import pytest

from graphkb.agent_api import _runtime_requirements, creation_order
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
        ("core::vm", "common", "core"),
        ("core::vNet", "common", "core"),
        ("core::subnet", "common", "core"),
        ("core::dataDisk", "common", "core"),
        ("aws::AWS::EC2::Instance", "aws", "vendor"),
        ("aws::AWS::EC2::Volume", "aws", "vendor"),
    ):
        g.add_node(_node(node_id, provider, layer))

    def edge(a, b, kind, required):
        return Edge(from_id=a, to_id=b, type=kind, via_property="",
                    required=required, cardinality="one", evidence="swagger-field")

    # core는 실행 경로 기준으로 둘을 필수라 말한다
    g.add_edge(edge("core::vm", "core::vNet", "references", True))
    g.add_edge(edge("core::vm", "core::subnet", "references", True))
    g.add_edge(edge("core::vm", "core::dataDisk", "references", False))
    # 매핑
    g.add_edge(edge("core::vm", "aws::AWS::EC2::Instance", "equivalent_to", False))
    # 벤더는 아무것도 필수라 하지 않는다
    g.add_edge(edge("aws::AWS::EC2::Instance", "aws::AWS::EC2::Volume", "references", False))
    return g


def test_vendor_answer_carries_runtime_requirements(graph) -> None:
    """벤더 타입을 물어도 실행 경로 요구사항이 함께 나와야 한다."""
    text = _runtime_requirements(graph, "aws::AWS::EC2::Instance")
    assert text is not None
    assert "vNet" in text and "subnet" in text
    assert "2가지를 필수로 요구" in text


def test_optional_core_dependency_is_not_promoted(graph) -> None:
    """core에서 선택인 것을 필수로 올리지 않는다."""
    text = _runtime_requirements(graph, "aws::AWS::EC2::Instance")
    assert "dataDisk" not in text


def test_both_sides_are_stated_as_true(graph) -> None:
    """한쪽을 틀렸다고 하지 않는다 — 근거가 다를 뿐이다."""
    text = _runtime_requirements(graph, "aws::AWS::EC2::Instance")
    assert "둘 다 사실" in text


def test_core_type_does_not_get_the_note(graph) -> None:
    """core를 물으면 그 자체가 실행 경로라 덧붙일 게 없다."""
    assert _runtime_requirements(graph, "core::vm") is None


def test_unmapped_vendor_type_gets_nothing(graph) -> None:
    """대응 코어 타입이 없으면 아무 말도 만들지 않는다."""
    assert _runtime_requirements(graph, "aws::AWS::EC2::Volume") is None


def test_creation_order_includes_the_note(graph, tmp_path, monkeypatch) -> None:
    """조회 API 전체 경로에서도 붙는지 — 문구가 아니라 존재를 본다."""
    monkeypatch.setattr("graphkb.agent_api.load_merged", lambda output_dir=None: graph)
    text = creation_order("AWS::EC2::Instance", output_dir=tmp_path)
    assert "실행 경로에서는 더 필요합니다" in text
