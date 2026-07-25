"""core 레이어와 벤더 레이어가 '무엇이 필수인가'를 다르게 말할 때.

**사용자가 잡아낸 결함이다.** "aws vm을 물으면 혼자 만들 수 있다는데, 일반적으로
물으면 선행 리소스가 있어야 한다고 한다 — 불일치 아니냐."

    core::vm                 반드시 먼저 있어야 하는 것 7개
    aws::AWS::EC2::Instance  필수 0개

둘 다 사실이다. `core::vm`은 cb-tumblebug REST 스키마에서 왔고 거기서 `vNetId`·
`subnetId`·`securityGroupIds`가 required다. `AWS::EC2::Instance`는 CFN에서 왔고
CFN은 `SubnetId`를 선택으로 둔다(기본 VPC).

스키마만 보고 "필수 없음"이라 답하면 **VM 하나 만들려는 사람에게 쓸모없는 답**이
된다. 실측상 이 어긋남은 5개 코어 타입에 있었고 vm은 9/9 CSP 전부 0을 말했다.

**다만 코어 목록을 그대로 옮기면 안 된다.** `core::vm`의 필수 7개에는 만드는
리소스(vNet·subnet·securityGroup·sshKey)와 고르는 카탈로그(spec·image), 그리고
cb-tumblebug 고유 개념(mci)이 섞여 있다. "EC2를 만들려면 mci가 필요하다"는
**가이드라인으로서 거짓**이다 — 우리는 배포기가 아니라 가이드라인 지식베이스를
만든다. 벤더 대응물이 있는 코어 타입만 옮기면 셋이 자동으로 걸러진다.
"""

from __future__ import annotations

import pytest

from graphkb.agent_api import _practical_prerequisites, creation_order
from graphkb.model import Edge, Graph, Node


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


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
        ("core::mci", "common", "core"),      # 도구 고유 개념 — 벤더 대응물이 없다
        ("aws::AWS::EC2::Instance", "aws", "vendor"),
        ("aws::AWS::EC2::VPC", "aws", "vendor"),
        ("aws::AWS::EC2::Subnet", "aws", "vendor"),
        ("aws::AWS::EC2::Volume", "aws", "vendor"),
    ):
        g.add_node(_node(node_id, provider, layer))

    def edge(a, b, kind, required):
        return Edge(from_id=a, to_id=b, type=kind, via_property="",
                    required=required, cardinality="one", evidence="swagger-field")

    # core는 실무 구성 기준으로 셋을 필수라 말한다 — 그중 mci는 도구 개념이다
    g.add_edge(edge("core::vm", "core::vNet", "references", True))
    g.add_edge(edge("core::vm", "core::subnet", "references", True))
    g.add_edge(edge("core::vm", "core::mci", "references", True))
    g.add_edge(edge("core::vm", "core::dataDisk", "references", False))
    # 매핑 — mci에는 대응물이 없다(core_vendor_map이 명시적으로 제외한다)
    g.add_edge(edge("core::vm", "aws::AWS::EC2::Instance", "equivalent_to", False))
    g.add_edge(edge("core::vNet", "aws::AWS::EC2::VPC", "equivalent_to", False))
    g.add_edge(edge("core::subnet", "aws::AWS::EC2::Subnet", "equivalent_to", False))
    # 벤더는 아무것도 필수라 하지 않는다
    g.add_edge(edge("aws::AWS::EC2::Instance", "aws::AWS::EC2::Volume", "references", False))
    return g


def test_tool_specific_concept_is_filtered_out(graph) -> None:
    """**이 수정의 핵심.** `mci`는 cb-tumblebug 개념이라 AWS에 대응물이 없다.

    "EC2를 만들려면 mci가 필요하다"는 가이드라인으로서 거짓이다. 벤더 대응물이
    있는 코어 타입만 옮기면 자동으로 걸러진다.
    """
    text = _practical_prerequisites(graph, "aws::AWS::EC2::Instance")
    assert "mci" not in text


def test_vendor_answer_carries_practical_prerequisites(graph) -> None:
    """벤더 타입을 물어도 실무에서 필요한 것이 함께 나와야 한다."""
    text = _practical_prerequisites(graph, "aws::AWS::EC2::Instance")
    assert text is not None
    assert "vNet" in text and "subnet" in text
    assert (
        "the schema leaves these optional, but in practice they are usually needed"
        in flat(text)
    )


def test_optional_core_dependency_is_not_promoted(graph) -> None:
    """core에서 선택인 것을 필수로 올리지 않는다."""
    text = _practical_prerequisites(graph, "aws::AWS::EC2::Instance")
    assert "dataDisk" not in text


def test_schema_silence_is_not_permission(graph) -> None:
    """스키마가 안 막는다고 그것만으로 쓸 수 있다는 뜻이 아니다."""
    text = _practical_prerequisites(graph, "aws::AWS::EC2::Instance")
    assert "not that it alone gives you a usable setup" in flat(text)


def test_core_type_does_not_get_the_note(graph) -> None:
    """core를 물으면 이미 그 층의 답이라 덧붙일 게 없다."""
    assert _practical_prerequisites(graph, "core::vm") is None


def test_unmapped_vendor_type_gets_nothing(graph) -> None:
    """대응 코어 타입이 없으면 아무 말도 만들지 않는다."""
    assert _practical_prerequisites(graph, "aws::AWS::EC2::Volume") is None


def test_creation_order_includes_the_note(graph, tmp_path, monkeypatch) -> None:
    """조회 API 전체 경로에서도 붙는지 — 문구가 아니라 존재를 본다."""
    monkeypatch.setattr("graphkb.agent_api.load_merged", lambda output_dir=None: graph)
    text = creation_order("AWS::EC2::Instance", output_dir=tmp_path)
    assert (
        "the schema leaves these optional, but in practice they are usually needed"
        in flat(text)
    )
