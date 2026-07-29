"""cb-tumblebug 자원·의존 산출물의 **구조적 불변식**.

값이 맞는지는 소스를 다시 읽어야 알 수 있고, 그건 이 테스트가 할 일이 아니다.
여기서 지키는 것은 **판정 기준이 무너지지 않는가**다 —
`docs/tumblebug-resource-dependency-2026-07-29.md`가 세운 규칙 중 코드로 옮길 수
있는 것을 옮겨 둔다. 문서에만 있는 규칙은 다음 사람이 모른다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ARTIFACT = (
    Path(__file__).resolve().parents[1] / "graphkb" / "parsers" / "tumblebug_resources.json"
)


@pytest.fixture(scope="module")
def data() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_every_edge_endpoint_is_a_known_resource(data) -> None:
    """간선의 양 끝은 자원 목록에 있어야 한다 — 어휘가 새면 조인이 조용히 깨진다."""
    known = {r["id"] for r in data["resources"]}
    unknown = {
        end
        for edge in data["edges"]
        for end in (edge["from"], edge["to"])
        if end not in known
    }
    assert not unknown, f"자원 목록에 없는 이름이 간선에 있다: {sorted(unknown)}"


def test_every_edge_carries_a_citation(data) -> None:
    """근거 없는 간선은 주장이지 관측이 아니다.

    각 증거에 **어디서 봤는지**가 있어야 한다. 파일:줄이거나 라우트 문자열이다.
    """
    for edge in data["edges"]:
        assert edge["layers"], f"{edge['from']}→{edge['to']}: 증거층이 없다"
        for layer, observations in edge["evidence"].items():
            assert observations, f"{edge['from']}→{edge['to']} {layer}: 관측이 비었다"
            for obs in observations:
                assert obs.get("evidence"), (
                    f"{edge['from']}→{edge['to']} {layer}: 인용이 없다"
                )


def test_layers_are_declared(data) -> None:
    """쓰는 층은 전부 선언돼 있어야 한다 — 새 층을 몰래 도입하면 등급이 뜻을 잃는다."""
    declared = set(data["evidenceLayers"])
    used = {layer for edge in data["edges"] for layer in edge["layers"]}
    assert used <= declared, f"선언되지 않은 층: {sorted(used - declared)}"


def test_ordering_evidence_never_stands_alone(data) -> None:
    """**D8(운영 순서)은 단독으로 간선을 만들지 못한다.**

    기준의 핵심이라 검사로 박는다. 순서는 의존을 함의하지 않는다 — A 다음에 B가
    온다고 B가 A를 요구하는 것은 아니다. 초안이 선형 순서에서 쌍을 만들어
    `image→vNet` 같은 거짓 간선 10개를 냈고, 그래서 D8을 보강 전용으로 내렸다.
    이 검사가 없으면 같은 실수가 다음 라운드에 다시 들어온다.
    """
    alone = [
        (e["from"], e["to"]) for e in data["edges"] if e["layers"] == ["D8"]
    ]
    assert not alone, f"순서 증거만으로 선 간선: {alone}"


def test_catalog_resources_have_no_outgoing_dependencies(data) -> None:
    """카탈로그(spec·image)는 아무것도 요구하지 않는다.

    카탈로그는 등록된 사실이지 프로비저닝되는 실물이 아니다. 근거는 코드에 있다 —
    노드 삭제가 참조 카운트를 image에는 되돌리는데 spec에는 일부러 안 건다
    (`core/infra/control.go:1302` 주석). 여기서 나가는 간선이 생겼다면 자원의
    종류를 잘못 분류한 것이다.
    """
    catalog = {r["id"] for r in data["resources"] if r["kind"] == "catalog"}
    outgoing = [(e["from"], e["to"]) for e in data["edges"] if e["from"] in catalog]
    assert not outgoing, f"카탈로그가 무언가를 요구한다: {outgoing}"


def test_the_strongest_edges_are_the_vm_ones(data) -> None:
    """층이 다섯 겹인 간선은 정확히 node의 셋이다.

    이 셋(vNet·sshKey·securityGroup)이 스키마·런타임·삭제보호·자동생성·CSP 계약에서
    **동시에** 관측되는 것이 이 시스템에서 가장 단단한 사실이고, 연계 리소스 군
    (과제 문제 ②)의 답이 그 위에 선다. 줄어들면 근거가 약해진 것이고, 늘어나면
    층 판정이 헐거워진 것이다 — 어느 쪽이든 사람이 봐야 한다.
    """
    five = {
        (e["from"], e["to"]) for e in data["edges"] if len(e["layers"]) >= 5
    }
    assert five == {
        ("node", "vNet"), ("node", "sshKey"), ("node", "securityGroup"),
    }, f"다섯 겹 간선이 바뀌었다: {sorted(five)}"


def test_k8s_subnet_count_is_provider_scoped(data) -> None:
    """서브넷 최소 개수는 프로바이더의 성질이다 — aws만 2다.

    카디널리티가 (자원쌍 × CSP)의 함수라는 것의 가장 단단한 증거이고, 값이 우리
    추측이 아니라 `assets/k8sclusterinfo.yaml`에서 온 것이다. 프로바이더별 상세표는
    담지 않으므로(§CSP 최소화) 경계 안 셋만 본다.
    """
    counts = data["cspConditional"]["k8sCluster->subnet.minCount"]
    assert counts["aws"] == 2, "aws는 서로 다른 AZ의 서브넷 둘을 요구한다"
    assert counts["azure"] == 1 and counts["gcp"] == 1

    edge = next(e for e in data["edges"] if (e["from"], e["to"]) == ("k8sCluster", "subnet"))
    assert edge["minCardinalityByCsp"]["aws"] == 2, (
        "간선에서 CSP별 최소 개수가 빠지면 aws가 1로 읽힌다"
    )


def test_csp_specific_data_is_kept_to_the_minimum(data) -> None:
    """**CSP 특화 데이터는 기본적으로 담지 않는다** (2026-07-29 결정).

    기준은 cb-tumblebug의 벤더 중립 코어다. 프로바이더별 상세표(CIDR 범위·예약 IP·
    k8s 버전 목록 등)를 담기 시작하면 코어 모델이 CSP 카탈로그가 된다 — 이 저장소가
    범위를 좁힌 이유 그 자체다. 남긴 것은 **빼면 간선이 거짓이 되는 것들**뿐이다.
    """
    assert "networkByProvider" not in data and "k8sByProvider" not in data, (
        "프로바이더별 상세표가 다시 들어왔다 — 부족분이면 cspConditional에 근거와 함께"
    )
    keys = {k for k in data["cspConditional"] if not k.startswith("_")}
    assert keys == {
        "k8sCluster->subnet.minCount",
        "vpn->subnet.required",
        "sqlDb->vNet.required", "sqlDb->subnet.required", "sqlDb->subnet.minCount",
        "securityGroup->vNet.required",
    }, f"최소 집합이 바뀌었다: {sorted(keys)}"
    # 조건은 **간선 다섯**에만 붙는다. 늘어나면 CSP 카탈로그로 가는 길이다.
    assert len({k.rsplit(".", 1)[0] for k in keys}) == 5
    for key in keys:
        assert data["cspConditional"][key].get("_evidence"), f"{key}: 근거가 없다"


def test_conditions_stay_inside_the_provider_boundary(data) -> None:
    """조건은 경계인 aws·azure·gcp 안에서만 적는다.

    경계 밖 프로바이더(alibaba·ibm·ncp·nhn·tencent)의 조건은 관측했지만 담지 않는다.
    담으면 "우리가 답하는 모든 것이 배포 가능하다"가 깨진다.
    """
    boundary = {"aws", "azure", "gcp"}
    for key, table in data["cspConditional"].items():
        if key.startswith("_"):
            continue
        outside = {k for k in table if not k.startswith("_")} - boundary
        assert not outside, f"{key}: 경계 밖 프로바이더가 있다 {sorted(outside)}"
    for edge in data["edges"]:
        outside = set(edge["cspScoped"]) - boundary
        assert not outside, (
            f"{edge['from']}→{edge['to']}: 경계 밖 CSP 한정 {sorted(outside)}"
        )


def test_azure_vpn_needs_its_own_gateway_subnet(data) -> None:
    """azure만 VPN에 전용 게이트웨이 서브넷을 요구한다.

    스키마만 읽었으면 `vpn → vNet`에서 끝났을 간선이다. `assets/networkinfo.yaml`이
    프로바이더별로 적어 둔 데이터(D9)라서 잡혔고, 코드 쪽 증거
    (`AzureSpecificProperty.GatewaySubnetCidr`가 azure 분기에만 있다)와도 맞물린다.

    **이 간선이 CSP 조건을 최소한만 남기는 이유의 실례다** — 조건을 지우면 간선이
    통째로 사라지거나(azure에 없다고 말하거나) 모든 CSP에 없는 요구를 지우게 된다.
    """
    cond = data["cspConditional"]["vpn->subnet.required"]
    assert cond["azure"] is True and cond["aws"] is False and cond["gcp"] is False

    edge = next(
        (e for e in data["edges"] if (e["from"], e["to"]) == ("vpn", "subnet")), None
    )
    assert edge is not None, "azure 게이트웨이 서브넷 간선이 사라졌다"
    assert edge["cspScoped"] == ["azure"]


def test_coverage_records_what_the_rule_could_not_judge(data) -> None:
    """전수의 조건은 미판정이 0인 것이 아니라 **세어져 있는 것**이다.

    규칙이 못 읽은 것을 0으로 적으면 "다 봤다"로 읽힌다. 실제로 초안 규칙이
    `sourceNodeId`·`subnet1ID`·`VpcIID`를 놓쳤고, 미판정을 남겨 뒀기 때문에
    발견됐다.
    """
    cov = data["_coverage"]
    assert cov["structsScanned"] > 0
    assert cov["unjudgedIdFields"] > 0, (
        "미판정이 0이면 규칙이 전수를 봤다는 뜻이 아니라 세기를 그만뒀다는 뜻이기 쉽다"
    )
    assert cov["restRoutes"] > cov["swaggerPathsAtPinnedV0118"], (
        "핀 박은 swagger가 소스보다 뒤처져 있다는 사실이 산출물에 남아야 한다"
    )


def test_sources_are_pinned(data) -> None:
    """핀 없는 소스는 재현이 안 된다."""
    for src in data["_source"]:
        assert src.get("pin"), f"{src.get('source')}: 핀이 없다"
