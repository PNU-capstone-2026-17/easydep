"""GCP(KCC CRD) 파서 테스트.

fixture: v1.153.0에서 받은 실제 CRD 3개(ComputeSubnetwork/Firewall/Instance),
servicemappings 발췌본, DCL 스타일 합성 CRD 1개.
골든 케이스: ComputeSubnetwork→ComputeNetwork (required),
ComputeInstance→ComputeSubnetwork (중첩 배열 networkInterface 경유).
"""

from __future__ import annotations

from pathlib import Path

from app.core.cloudkb.tests._helpers import find_edges

#: 저장소 기준 경로. CWD 기준으로 열면 easydep 루트에서 돌 때 파일을 못 찾고,
#: exists() 가드가 있는 곳은 실패 대신 **조용히 스킵**된다(병합 때 실제로 그랬다).
_ROOT = Path(__file__).resolve().parent.parent

import pytest
import yaml

from app.core.cloudkb.graphkb.model import Graph
from app.core.cloudkb.graphkb.parsers.gcp import parse_crds

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
    from app.core.cloudkb.graphkb.parsers.gcp import _KIND_NAME

    for word in ("externally", "parent", "private", "service", "certificatemanager"):
        assert not _KIND_NAME.fullmatch(word), word
    for kind in ("ComputeNetwork", "IAMServiceAccount", "ComputeInstanceTemplate"):
        assert _KIND_NAME.fullmatch(kind), kind


def test_kind_without_crd_still_yields_a_relationship() -> None:
    """CRD를 안 받은 종류라도 관계는 남긴다.

    스키마가 없어도 "이게 있어야 한다"는 사실 자체가 답이 된다 —
    ComputeInstanceTemplate은 fixture에 CRD가 없지만 의존은 실재한다.
    """
    from app.core.cloudkb.graphkb.parsers.gcp import _KIND_NAME

    assert _KIND_NAME.fullmatch("ComputeInstanceTemplate")


def test_description_name_is_corrected_to_the_real_kind(monkeypatch) -> None:
    """설명문 표기를 실재 종류명으로 바로잡는다 (사람이 채운 별칭 표).

    CRD 설명문은 사람이 읽으라고 쓴 산문이라 종류명을 정확히 적지 않는다 —
    "a Secret resource"라고 쓰지만 KCC 종류는 `SecretManagerSecret`이다.

    **검수 파일의 rejected+added로 처리하면 안 된다.** 엣지만 지워지고 파서가 만든
    허구 종류 노드(`gcp::Secret`)는 그대로 남는다. 그래서 id를 만들기 전에 고친다.
    """
    from app.core.cloudkb.graphkb.parsers import gcp

    monkeypatch.setattr(gcp, "_KIND_ALIASES", {"Secret": "SecretManagerSecret"})
    resolved = gcp._resolve_target(
        "DatastreamConnectionProfile",
        "secretManagerSecretRef",
        {"description": "The name of a Secret resource", "properties": {"external": {}}},
        sm_index={},
        known_kinds={"SecretManagerSecret"},
        heuristics=False,
    )
    assert resolved is not None
    target, evidence, _field = resolved
    assert target == "SecretManagerSecret"
    assert evidence == "kcc-description"


def test_alias_table_targets_are_real_kinds() -> None:
    """별칭의 도착지는 실재하는 종류여야 한다 — 오타 나면 조용히 허구가 하나 더 생긴다."""
    import json

    path = _ROOT / "graphkb/reviewed/gcp-kind-aliases.json"
    if not path.exists():
        return
    aliases = json.loads(path.read_text(encoding="utf-8"))["aliases"]
    graph_path = _ROOT / "output/gcp-graph.json"
    if not graph_path.exists():
        return  # 빌드 전이면 건너뛴다
    nodes = json.loads(graph_path.read_text(encoding="utf-8"))["nodes"]
    ids = set(nodes) if isinstance(nodes, dict) else {n["id"] for n in nodes}
    for wrong, right in aliases.items():
        assert f"gcp::{right}" in ids, f"{wrong} → {right} (없는 종류)"
        assert f"gcp::{wrong}" not in ids, f"{wrong} 노드가 아직 남아 있다"


# --- 자원 계층 = 담김 (D5) ---


def _crd_with_ref(kind: str, ref_name: str, description: str) -> dict:
    return {
        "kind": "CustomResourceDefinition",
        "spec": {
            "names": {"kind": kind},
            "versions": [{
                "name": "v1beta1", "storage": True,
                "schema": {"openAPIV3Schema": {"properties": {"spec": {
                    "properties": {ref_name: {
                        "type": "object",
                        "description": description,
                        "properties": {"external": {"type": "string"},
                                       "name": {"type": "string"},
                                       "namespace": {"type": "string"}},
                    }},
                }}}},
            }],
        },
    }


def _project_crd() -> dict:
    return {"kind": "CustomResourceDefinition",
            "spec": {"names": {"kind": "Project"},
                     "versions": [{"name": "v1beta1", "storage": True,
                                   "schema": {"openAPIV3Schema": {"properties": {"spec": {}}}}}]}}


def test_project_ref_becomes_containment() -> None:
    """`projectRef`는 참조가 아니라 담김이다 — 설명문이 "belongs to"라고 말한다.

    Azure는 타입 이름이 계층적이라 공짜로 나오지만 GCP는 이름이 평평해서
    이 참조가 **유일한 계층 신호**다. 이게 없으면 담김 축이 통째로 빈다.
    """
    from app.core.cloudkb.graphkb.parsers.gcp import parse_crds

    graph = parse_crds([
        _project_crd(),
        _crd_with_ref("AIPlatformModel", "projectRef",
                      "The project that this resource belongs to."),
    ])
    edges = [e for e in graph.edges if e.from_id == "gcp::AIPlatformModel"]
    assert len(edges) == 1
    assert edges[0].type == "contained_in"
    assert edges[0].to_id == "gcp::Project"
    assert edges[0].is_fact, "이름이 KCC의 규약이므로 짐작이 아니다"


def test_hierarchy_naming_does_not_override_a_resolved_target() -> None:
    """설명문이 대상을 밝히면 그쪽을 쓴다.

    Apigee의 `organizationRef`는 GCP 조직이 아니라 ApigeeOrganization을 가리킨다.
    이름 규약을 먼저 적용하면 이걸 틀리게 된다.
    """
    from app.core.cloudkb.graphkb.parsers.gcp import parse_crds

    apigee_org = {"kind": "CustomResourceDefinition",
                  "spec": {"names": {"kind": "ApigeeOrganization"},
                           "versions": [{"name": "v1beta1", "storage": True,
                                         "schema": {"openAPIV3Schema": {"properties": {"spec": {}}}}}]}}
    graph = parse_crds([
        apigee_org,
        _crd_with_ref("ApigeeEnvironment", "organizationRef",
                      "The name of an ApigeeOrganization resource."),
    ])
    edges = [e for e in graph.edges if e.from_id == "gcp::ApigeeEnvironment"]
    assert [e.to_id for e in edges] == ["gcp::ApigeeOrganization"]
    assert edges[0].type == "contained_in", "대상이 달라도 관계는 여전히 담김이다"


def test_plain_reference_is_still_a_reference() -> None:
    from app.core.cloudkb.graphkb.parsers.gcp import parse_crds

    net = {"kind": "CustomResourceDefinition",
           "spec": {"names": {"kind": "ComputeNetwork"},
                    "versions": [{"name": "v1beta1", "storage": True,
                                  "schema": {"openAPIV3Schema": {"properties": {"spec": {}}}}}]}}
    graph = parse_crds([
        net,
        _crd_with_ref("ComputeSubnetwork", "networkRef",
                      "The name of a ComputeNetwork resource."),
    ])
    edges = [e for e in graph.edges if e.from_id == "gcp::ComputeSubnetwork"]
    assert [e.type for e in edges] == ["references"]
