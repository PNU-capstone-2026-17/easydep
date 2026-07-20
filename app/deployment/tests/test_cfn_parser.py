"""CFN 파서 테스트 — 브리프 완료 기준 2의 골든 케이스 검증.

실측상 AWS::EC2::Subnet의 VpcId에는 relationshipRef가 없으므로,
Subnet→VPC 골든 케이스는 CDK out-of-band 근거(cdk-oob)로 검증하고
relationshipRef 추출은 실존하는 AWS::EC2::VPCEndpoint 스키마로 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphkb.model import Edge, Graph
from graphkb.parsers.cfn import parse_schemas

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CFN_DIR = FIXTURE_DIR / "cfn"


def load_schemas() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CFN_DIR.glob("*.json"))
    ]


def load_oob() -> dict:
    path = FIXTURE_DIR / "cdk-relationships-min.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph() -> Graph:
    return parse_schemas(load_schemas(), oob=load_oob(), heuristics=True)


def find_edges(graph: Graph, from_id: str, to_id: str) -> list[Edge]:
    return [e for e in graph.edges if e.from_id == from_id and e.to_id == to_id]


def test_nodes_created_for_all_schemas(graph: Graph) -> None:
    for type_name in (
        "AWS::EC2::VPC",
        "AWS::EC2::Subnet",
        "AWS::EC2::VPCEndpoint",
        "AWS::Legacy::Widget",
    ):
        node = graph.nodes[f"aws::{type_name}"]
        assert node.layer == "vendor"
        assert node.provider == "aws"


def test_golden_subnet_to_vpc_via_cdk_oob(graph: Graph) -> None:
    """골든 케이스: AWS::EC2::Subnet → AWS::EC2::VPC (VpcId)."""
    edges = [
        e
        for e in find_edges(graph, "aws::AWS::EC2::Subnet", "aws::AWS::EC2::VPC")
        if e.via_property == "VpcId"
    ]
    assert len(edges) == 1
    edge = edges[0]
    assert edge.evidence == "cdk-oob"
    assert edge.confidence == 0.9
    assert edge.required is True  # Subnet 스키마에서 VpcId는 required
    assert edge.cardinality == "one"


def test_relationship_ref_in_array_items(graph: Graph) -> None:
    """실존 relationshipRef: VPCEndpoint.SubnetIds → Subnet (items 내부)."""
    edges = [
        e
        for e in find_edges(
            graph, "aws::AWS::EC2::VPCEndpoint", "aws::AWS::EC2::Subnet"
        )
        if e.via_property == "SubnetIds"
    ]
    assert len(edges) == 1
    assert edges[0].evidence == "relationshipRef"
    assert edges[0].confidence == 1.0
    assert edges[0].cardinality == "many"


def test_relationship_ref_in_anyof_branches(graph: Graph) -> None:
    """anyOf 분기 내부 relationshipRef: SecurityGroupIds → SecurityGroup/VPC."""
    to_sg = [
        e
        for e in find_edges(
            graph, "aws::AWS::EC2::VPCEndpoint", "aws::AWS::EC2::SecurityGroup"
        )
        if e.via_property == "SecurityGroupIds"
    ]
    assert len(to_sg) == 1  # GroupId/Id 두 분기는 같은 키로 dedup
    assert to_sg[0].evidence == "relationshipRef"
    to_vpc = [
        e
        for e in find_edges(graph, "aws::AWS::EC2::VPCEndpoint", "aws::AWS::EC2::VPC")
        if e.via_property == "SecurityGroupIds"
    ]
    assert len(to_vpc) == 1
    # zip에 없는 참조 타겟도 노드로 생성된다
    assert "aws::AWS::EC2::SecurityGroup" in graph.nodes


def test_heuristic_edge_from_plain_vpcid(graph: Graph) -> None:
    """relationshipRef 없는 VPCEndpoint.VpcId → VPC (동일 서비스 0.6)."""
    edges = [
        e
        for e in find_edges(graph, "aws::AWS::EC2::VPCEndpoint", "aws::AWS::EC2::VPC")
        if e.via_property == "VpcId"
    ]
    assert len(edges) == 1
    assert edges[0].evidence == "heuristic"
    assert edges[0].confidence == 0.6
    assert edges[0].required is True


def test_legacy_schema_heuristics(graph: Graph) -> None:
    """구형 스키마: 휴리스틱 엣지 생성, 교차 서비스 confidence 0.5."""
    to_vpc = [
        e
        for e in find_edges(graph, "aws::AWS::Legacy::Widget", "aws::AWS::EC2::VPC")
        if e.via_property == "VpcId"
    ]
    assert len(to_vpc) == 1
    assert to_vpc[0].evidence == "heuristic"
    assert to_vpc[0].confidence == 0.5
    to_subnet = [
        e
        for e in find_edges(graph, "aws::AWS::Legacy::Widget", "aws::AWS::EC2::Subnet")
        if e.via_property == "SubnetIds"
    ]
    assert len(to_subnet) == 1
    assert to_subnet[0].cardinality == "many"


def test_readonly_properties_skipped(graph: Graph) -> None:
    """readOnly 속성은 relationshipRef/휴리스틱/OoB 모두에서 제외."""
    # 합성 legacy 스키마의 OutputVpcId (readOnly)
    legacy = [
        e
        for e in graph.edges
        if e.from_id == "aws::AWS::Legacy::Widget" and "OutputVpcId" in e.via_property
    ]
    assert legacy == []
    # OoB fixture의 VPCEndpoint.Id (readOnly) 역방향 항목
    oob = [
        e
        for e in graph.edges
        if e.from_id == "aws::AWS::EC2::VPCEndpoint" and e.via_property == "Id"
    ]
    assert oob == []


def test_ambiguous_heuristic_skipped(graph: Graph) -> None:
    """매칭되는 타입이 없는 ClusterId는 엣지를 만들지 않는다."""
    edges = [
        e
        for e in graph.edges
        if e.from_id == "aws::AWS::Legacy::Widget" and e.via_property == "ClusterId"
    ]
    assert edges == []


def test_no_heuristics_flag(graph: Graph) -> None:
    quiet = parse_schemas(load_schemas(), oob=None, heuristics=False)
    assert all(e.evidence != "heuristic" for e in quiet.edges)
    assert not [
        e for e in quiet.edges if e.from_id == "aws::AWS::Legacy::Widget"
    ]
    # relationshipRef 엣지는 여전히 추출된다
    assert [
        e
        for e in quiet.edges
        if e.from_id == "aws::AWS::EC2::VPCEndpoint" and e.evidence == "relationshipRef"
    ]


def test_dedup_prefers_higher_confidence(graph: Graph) -> None:
    """Subnet.VpcId는 heuristic(0.6)과 cdk-oob(0.9)가 겹치며 cdk-oob가 남는다."""
    edges = [
        e
        for e in find_edges(graph, "aws::AWS::EC2::Subnet", "aws::AWS::EC2::VPC")
        if e.via_property == "VpcId"
    ]
    assert len(edges) == 1
    assert edges[0].evidence == "cdk-oob"


def test_graph_validates(graph: Graph) -> None:
    graph.validate()


# --- 경로 보존·순회 범위 회귀 (2026-07-20) ---
#
# 예전 파서는 루트를 훑은 뒤 `definitions`를 **빈 경로로 다시** 순회했다. 그래서
# 중첩 속성이 마치 루트 속성인 것처럼 기록됐고(실측: heuristic 486/1,102 ·
# relationshipRef 18/59가 실재하지 않는 경로), readOnly 필터도 우회됐다.


def _one(schemas: list[dict], *, heuristics: bool = True) -> Graph:
    return parse_schemas(schemas, oob=None, heuristics=heuristics)


def test_ref_keeps_the_real_property_path() -> None:
    """`$ref`를 따라가되 **경로를 유지**한다.

    via_property는 "이 의존을 만들려면 어느 속성을 채우나"를 답하는 필드다.
    틀린 경로가 실리면 그 값으로 템플릿을 만드는 쪽이 전부 깨진다.
    """
    schema = {
        "typeName": "AWS::Test::Thing",
        "properties": {"Config": {"$ref": "#/definitions/Config"}},
        "definitions": {
            "Config": {"properties": {"VpcId": {"type": "string"}}}
        },
    }
    graph = _one([schema, {"typeName": "AWS::EC2::VPC", "properties": {}}])
    vias = [e.via_property for e in graph.edges if e.to_id == "aws::AWS::EC2::VPC"]
    assert vias == ["Config/VpcId"], f"경로가 보존되지 않았다: {vias}"


def test_unreferenced_definition_produces_no_edge() -> None:
    """어디서도 참조되지 않는 definition은 실제 속성이 아니므로 엣지를 만들지 않는다."""
    schema = {
        "typeName": "AWS::Test::Thing",
        "properties": {"Name": {"type": "string"}},
        "definitions": {"Unused": {"properties": {"VpcId": {"type": "string"}}}},
    }
    graph = _one([schema, {"typeName": "AWS::EC2::VPC", "properties": {}}])
    assert not [e for e in graph.edges if e.to_id == "aws::AWS::EC2::VPC"]


def test_pattern_properties_are_traversed() -> None:
    """맵 타입(`patternProperties`)도 따라간다 — 실측 253개 스키마가 이 모양이다."""
    schema = {
        "typeName": "AWS::Test::Thing",
        "properties": {
            "Actions": {
                "type": "object",
                "patternProperties": {"^.+$": {"$ref": "#/definitions/Action"}},
            }
        },
        "definitions": {"Action": {"properties": {"VpcId": {"type": "string"}}}},
    }
    graph = _one([schema, {"typeName": "AWS::EC2::VPC", "properties": {}}])
    assert [e.to_id for e in graph.edges] == ["aws::AWS::EC2::VPC"]


def test_nested_readonly_pointer_does_not_skip_whole_property() -> None:
    """`/properties/A/B/C`가 읽기 전용이어도 **A 전체**를 건너뛰면 안 된다.

    실측 사고: AWS::Batch::ComputeEnvironment의
    `/properties/ComputeResources/Ec2Configuration/*/BatchImageStatus` 때문에
    `ComputeResources` 서브트리가 통째로 배제돼 LaunchTemplate 참조가 사라졌다.
    """
    schema = {
        "typeName": "AWS::Test::Thing",
        "properties": {"Compute": {"$ref": "#/definitions/Compute"}},
        "definitions": {"Compute": {"properties": {"VpcId": {"type": "string"}}}},
        "readOnlyProperties": ["/properties/Compute/Nested/Status"],
    }
    graph = _one([schema, {"typeName": "AWS::EC2::VPC", "properties": {}}])
    assert [e.via_property for e in graph.edges] == ["Compute/VpcId"]

    # 반대로 정확히 `/properties/Compute`면 여전히 통째로 건너뛴다
    schema["readOnlyProperties"] = ["/properties/Compute"]
    assert not _one([schema, {"typeName": "AWS::EC2::VPC", "properties": {}}]).edges


    