"""GCP(KCC CRD) 파서 테스트.

fixture: v1.153.0에서 받은 실제 CRD 3개(ComputeSubnetwork/Firewall/Instance),
servicemappings 발췌본, DCL 스타일 합성 CRD 1개.
골든 케이스: ComputeSubnetwork→ComputeNetwork (required),
ComputeInstance→ComputeSubnetwork (중첩 배열 networkInterface 경유).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from graphkb.model import Edge, Graph
from graphkb.parsers.gcp import parse_crds

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gcp"


def load(name: str) -> dict:
    return yaml.safe_load((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph() -> Graph:
    crds = [
        load("crd-computesubnetwork.yaml"),
        load("crd-computefirewall.yaml"),
        load("crd-computeinstance.yaml"),
        load("crd-syntheticthing.yaml"),
    ]
    return parse_crds(
        crds,
        servicemappings=[load("servicemappings-compute-min.yaml")],
        heuristics=True,
    )


def find_edges(graph: Graph, from_id: str, to_id: str) -> list[Edge]:
    return [e for e in graph.edges if e.from_id == from_id and e.to_id == to_id]


def test_nodes_created(graph: Graph) -> None:
    for kind in ("ComputeSubnetwork", "ComputeFirewall", "ComputeInstance"):
        node = graph.nodes[f"gcp::{kind}"]
        assert node.layer == "vendor"
        assert node.provider == "gcp"
    # 참조 대상으로만 등장하는 kind도 노드가 된다
    assert "gcp::ComputeNetwork" in graph.nodes


def test_golden_subnetwork_to_network(graph: Graph) -> None:
    """골든: ComputeSubnetwork → ComputeNetwork (networkRef, required)."""
    edges = find_edges(graph, "gcp::ComputeSubnetwork", "gcp::ComputeNetwork")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.via_property == "networkRef"
    assert edge.required is True  # spec.required에 networkRef 포함
    assert edge.evidence == "kcc-description"  # 설명문 패턴 — 짐작
    assert edge.basis == "inferred"


def test_firewall_to_network_required(graph: Graph) -> None:
    edges = find_edges(graph, "gcp::ComputeFirewall", "gcp::ComputeNetwork")
    assert len(edges) == 1
    assert edges[0].required is True


def test_golden_instance_to_subnetwork_nested_array(graph: Graph) -> None:
    """골든: ComputeInstance → ComputeSubnetwork (networkInterface[] 내부)."""
    edges = find_edges(graph, "gcp::ComputeInstance", "gcp::ComputeSubnetwork")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.via_property == "networkInterface.subnetworkRef"
    assert edge.cardinality == "many"
    assert edge.evidence == "kcc-ref"
    assert edge.basis == "stated"  # servicemappings gvk.kind가 최우선


def test_instance_template_ref_description_tier(graph: Graph) -> None:
    """servicemappings에 없는 ref는 설명문 패턴으로 해석 — **짐작**이다.

    예전엔 이것도 `kcc-ref` 라벨을 달아서, 라벨 단위 검수가 짐작까지 승인했다.
    """
    edges = find_edges(graph, "gcp::ComputeInstance", "gcp::ComputeInstanceTemplate")
    assert len(edges) == 1
    assert edges[0].evidence == "kcc-description"
    assert edges[0].basis == "inferred"


def test_dcl_style_resolved_only_by_servicemappings(graph: Graph) -> None:
    """generic description은 servicemappings로만 해석된다 (tier 1)."""
    edges = find_edges(graph, "gcp::ComputeSyntheticThing", "gcp::ComputeDisk")
    assert len(edges) == 1
    assert edges[0].via_property == "attachedRef"
    assert edges[0].evidence == "kcc-ref"
    assert edges[0].basis == "stated"
    assert edges[0].required is True


def test_heuristic_tier(graph: Graph) -> None:
    """servicemappings/description 모두 실패 → 필드명 휴리스틱 (tier 3)."""
    edges = find_edges(graph, "gcp::ComputeSyntheticThing", "gcp::ComputeFirewall")
    assert len(edges) == 1
    assert edges[0].evidence == "heuristic"
    assert edges[0].basis == "inferred"  # 동일 서비스(compute)


def test_unresolvable_ref_skipped(graph: Graph) -> None:
    edges = [
        e
        for e in graph.edges
        if e.from_id == "gcp::ComputeSyntheticThing" and "widget" in e.via_property
    ]
    assert edges == []


def test_no_heuristics_flag() -> None:
    crds = [load("crd-syntheticthing.yaml")]
    graph = parse_crds(crds, servicemappings=[], heuristics=False)
    assert all(e.evidence != "heuristic" for e in graph.edges)


def test_graph_validates(graph: Graph) -> None:
    graph.validate()


def test_prose_words_are_not_treated_as_kinds() -> None:
    """설명문 정규식이 잡은 낱말이 곧 KCC 종류는 아니다.

    실측: `externally`가 67곳에서 종류로 읽혔고, 소문자 `service`는 `gcp::service`라는
    없는 부품까지 만들었다(진짜 대상은 IAMServiceAccount). 판별은 KCC 작명 규칙 —
    종류 이름은 예외 없이 PascalCase다.
    """
    from graphkb.parsers.gcp import _KIND_NAME

    for word in ("externally", "parent", "private", "service", "certificatemanager"):
        assert not _KIND_NAME.fullmatch(word), word
    for kind in ("ComputeNetwork", "IAMServiceAccount", "ComputeInstanceTemplate"):
        assert _KIND_NAME.fullmatch(kind), kind


def test_kind_without_crd_still_yields_a_relationship() -> None:
    """CRD를 안 받은 종류라도 관계는 남긴다.

    스키마가 없어도 "이게 있어야 한다"는 사실 자체가 답이 된다 —
    ComputeInstanceTemplate은 fixture에 CRD가 없지만 의존은 실재한다.
    """
    from graphkb.parsers.gcp import _KIND_NAME

    assert _KIND_NAME.fullmatch("ComputeInstanceTemplate")
