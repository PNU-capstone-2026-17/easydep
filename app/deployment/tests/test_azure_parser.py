"""Azure(bicep-types-az) 파서 테스트.

fixture는 실측한 types.json 포맷을 그대로 따른 축소 합성본이다.
골든 케이스: subnets contained_in virtualNetworks (arm-hierarchy),
subnets → networkSecurityGroups (bicep-ref), NIC → subnets (중첩 배열 경유).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.deployment.graphkb.model import Graph
from app.deployment.graphkb.parsers.azure import extract_references, parse_index
from app.deployment.tests._helpers import find_edges

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "azure"

VNET = "azure::Microsoft.Network/virtualNetworks"
SUBNET = "azure::Microsoft.Network/virtualNetworks/subnets"
NSG = "azure::Microsoft.Network/networkSecurityGroups"
NIC = "azure::Microsoft.Network/networkInterfaces"


def load_index() -> dict:
    return json.loads((FIXTURE_DIR / "index.json").read_text(encoding="utf-8"))


def load_types() -> list[dict]:
    path = FIXTURE_DIR / "network" / "microsoft.network" / "2025-01-01" / "types.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph() -> Graph:
    g, _latest = parse_index(load_index())
    extract_references(g, load_types(), heuristics=True)
    return g


def test_nodes_from_index(graph: Graph) -> None:
    for node_id in (VNET, SUBNET, NSG, NIC):
        node = graph.nodes[node_id]
        assert node.layer == "vendor"
        assert node.provider == "azure"


def test_latest_stable_version_preferred() -> None:
    _g, latest = parse_index(load_index())
    version, _path = latest["Microsoft.Network/virtualNetworks"]
    assert version == "2025-01-01"  # 2026-01-01-preview보다 비-preview 우선


def test_hierarchy_containment(graph: Graph) -> None:
    """골든: subnets는 virtualNetworks에 contained_in (이름 계층에서 유도)."""
    edges = find_edges(graph, SUBNET, VNET)
    contained = [e for e in edges if e.type == "contained_in"]
    assert len(contained) == 1
    assert contained[0].evidence == "arm-hierarchy"
    assert contained[0].basis == "stated"


def test_hierarchy_walks_all_levels(graph: Graph) -> None:
    """손자 타입도 각 단계 부모에 연결된다 (storage 3단계 체인)."""
    acct = "azure::Microsoft.Storage/storageAccounts"
    blob = "azure::Microsoft.Storage/storageAccounts/blobServices"
    cont = "azure::Microsoft.Storage/storageAccounts/blobServices/containers"
    assert find_edges(graph, blob, acct)
    assert find_edges(graph, cont, blob)
    assert not find_edges(graph, cont, acct)  # 직계 부모만


def test_subnet_references_nsg(graph: Graph) -> None:
    """골든: subnets → networkSecurityGroups (인라인 객체 이름 매칭)."""
    edges = find_edges(graph, SUBNET, NSG)
    ref = [e for e in edges if e.evidence == "bicep-ref"]
    assert len(ref) == 1
    assert ref[0].via_property == "properties.networkSecurityGroup"
    assert ref[0].basis == "inferred"


def test_nic_references_subnet_through_nested_array(graph: Graph) -> None:
    edges = find_edges(graph, NIC, SUBNET)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.evidence == "bicep-ref"
    assert edge.via_property == "properties.ipConfigurations.properties.subnet"
    assert edge.cardinality == "many"  # 배열을 거쳤으므로


def test_inline_child_listing_not_a_reference(graph: Graph) -> None:
    """vNet body의 subnets 배열은 자식 인라인 포함 — 참조 엣지를 만들지 않는다."""
    assert not [
        e for e in find_edges(graph, VNET, SUBNET) if e.type == "references"
    ]


def test_heuristic_string_id_property(graph: Graph) -> None:
    edges = [e for e in find_edges(graph, NIC, NSG) if e.evidence == "heuristic"]
    assert len(edges) == 1
    assert edges[0].via_property == "properties.networkSecurityGroupId"
    assert edges[0].basis == "inferred"


def test_readonly_property_skipped(graph: Graph) -> None:
    assert not [e for e in graph.edges if "internalDomainNameId" in e.via_property]


def test_no_heuristics_flag() -> None:
    g, _ = parse_index(load_index())
    extract_references(g, load_types(), heuristics=False)
    assert all(e.evidence != "heuristic" for e in g.edges)
    # bicep-ref 엣지는 유지
    assert [e for e in g.edges if e.evidence == "bicep-ref"]


def test_graph_validates(graph: Graph) -> None:
    graph.validate()


# --- 참조 껍데기 해석: 사람이 채운 표 (2026-07-21) ---
#
# Azure 참조는 `NetworkInterfaceReference`처럼 대상의 id만 담는 껍데기로 표현되는데,
# 껍데기 이름에서 대상을 되찾는 일은 자동으로 안 된다. 이 표가 없던 동안
# Microsoft.Compute/virtualMachines는 나가는 관계가 **0개**였다.


def _types_with_wrapper(wrapper_name: str) -> list[dict]:
    """`properties.sourceVault`가 주어진 이름의 껍데기를 가리키는 최소 types.json.

    실제 SubResource가 이 모양이다 — id만 담고 있어 참조인 건 분명한데, 이름에
    대상 정보가 없다(303곳에서 쓰이며 sourceVault는 KeyVault를 뜻한다).
    """
    return [
        {"$type": "StringType"},                                        # 0
        {"$type": "ObjectType", "name": wrapper_name,
         "properties": {"id": {"type": {"$ref": "#/0"}, "flags": 0}}},  # 1
        {"$type": "ObjectType", "name": "VaultProperties",
         "properties": {"sourceVault": {"type": {"$ref": "#/1"}, "flags": 0}}},  # 2
        {"$type": "ObjectType", "name": "Microsoft.Network/virtualNetworks",
         "properties": {"properties": {"type": {"$ref": "#/2"}, "flags": 0}}},   # 3
        {"$type": "ResourceType", "name": "Microsoft.Network/virtualNetworks@2025-01-01",
         "body": {"$ref": "#/3"}},                                      # 4
    ]


def build_with_table(table: dict, monkeypatch) -> Graph:
    monkeypatch.setattr(
        "app.deployment.graphkb.parsers.azure.load_reference_map", lambda _provider: table
    )
    g, _ = parse_index(load_index())
    extract_references(g, load_types(), heuristics=False)
    return g


def test_table_target_is_used_and_marked_reviewed(monkeypatch) -> None:
    """표에서 나온 대상은 사람이 정한 것이므로 reviewed로 표시된다.

    검수 이력은 azure-references.json이 갖는다 — 같은 판단을 azure-edges.json에
    한 번 더 적으면 두 파일이 어긋난다.
    """
    g = build_with_table({"NetworkSecurityGroup": NSG.removeprefix("azure::")}, monkeypatch)
    edges = [e for e in find_edges(g, SUBNET, NSG) if e.evidence == "bicep-ref"]
    assert edges and all(e.reviewed for e in edges)


def test_null_in_table_keeps_walking_into_the_object(monkeypatch) -> None:
    """**핵심 회귀**: 참조가 아니라고 적힌 껍데기에서 멈추면 안 된다.

    가상머신의 networkProfile은 참조가 아닌 인라인 설정이지만, 그 **안에**
    networkInterfaces라는 진짜 참조가 들어 있다. 여기서 멈추면 못 닿는다.
    """
    g = build_with_table(
        {
            "NetworkInterfaceIPConfiguration": None,  # 인라인 자식
            "Subnet": SUBNET.removeprefix("azure::"),
        },
        monkeypatch,
    )
    # ipConfigurations(인라인) 안으로 내려가 그 아래 subnet 참조에 닿는다
    assert [e for e in find_edges(g, NIC, SUBNET) if e.evidence == "bicep-ref"]


def test_common_prefix_is_stripped_for_lookup(monkeypatch) -> None:
    """bicep-types는 공용 정의에 CommonSubnet처럼 사본을 한 벌 더 낸다.

    안 벗기면 표가 두 배가 된다(실측 251행 중 61행이 이 중복이었다).
    fixture의 대상 객체 이름도 실제와 같이 CommonSubnet이다.
    """
    g = build_with_table({"Subnet": SUBNET.removeprefix("azure::")}, monkeypatch)
    assert [e for e in find_edges(g, NIC, SUBNET) if e.evidence == "bicep-ref"]


def test_unknown_wrapper_makes_no_edge_but_is_reported(monkeypatch) -> None:
    """표에 없으면 짐작하지 않는다. 대신 미결로 세어 빌드가 알린다.

    조용히 넘어가면 "관계가 없는 것"과 "아직 안 본 것"이 겉보기에 같아진다.
    """
    from app.deployment.graphkb.parsers import azure

    azure.UNRESOLVED_REFS.clear()
    monkeypatch.setattr(azure, "load_reference_map", lambda _provider: {})
    g, _ = parse_index(load_index())
    # id는 있는데 이름이 어떤 타입과도 안 맞는 껍데기 — 실제로는 SubResource가 이렇다
    azure.extract_references(g, _types_with_wrapper("SubResource"), heuristics=False)

    assert "SubResource@sourceVault" in azure.UNRESOLVED_REFS
    assert not [e for e in g.edges if e.via_property == "properties.sourceVault"]
    azure.UNRESOLVED_REFS.clear()


def test_table_resolves_what_the_name_cannot(monkeypatch) -> None:
    """이름에 대상 정보가 없는 껍데기는 **오직 표로만** 풀린다.

    SubResource는 303곳에서 쓰이는데 그 이름은 "어떤 리소스의 id"라는 뜻뿐이다.
    단서는 프로퍼티 이름인데 sourceVault→KeyVault처럼 그것도 어긋나므로,
    자동 규칙으로는 원리적으로 못 푼다.
    """
    from app.deployment.graphkb.parsers import azure

    azure.UNRESOLVED_REFS.clear()
    monkeypatch.setattr(
        azure, "load_reference_map",
        lambda _provider: {"SubResource@sourceVault": "Microsoft.Storage/storageAccounts"},
    )
    g, _ = parse_index(load_index())
    azure.extract_references(g, _types_with_wrapper("SubResource"), heuristics=False)

    edges = find_edges(g, VNET, "azure::Microsoft.Storage/storageAccounts")
    assert len(edges) == 1
    assert edges[0].reviewed and edges[0].target_property == "id"
    assert not azure.UNRESOLVED_REFS
    azure.UNRESOLVED_REFS.clear()


def test_unique_name_still_resolves_without_the_table(monkeypatch) -> None:
    """표는 못 푸는 것만 담는다 — 후보가 하나뿐이면 표 없이도 이어야 한다.

    그러지 않으면 껍데기 158종을 전부 손으로 적어야 한다(실제로는 125종이었다).
    """
    from app.deployment.graphkb.parsers import azure

    azure.UNRESOLVED_REFS.clear()
    g = build_with_table({}, monkeypatch)
    edges = [e for e in find_edges(g, SUBNET, NSG) if e.evidence == "bicep-ref"]
    assert edges and not edges[0].reviewed  # 사람이 정한 게 아니므로 미확인
    azure.UNRESOLVED_REFS.clear()
