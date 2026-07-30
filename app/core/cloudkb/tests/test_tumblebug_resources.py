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


def test_every_observation_names_its_source_form_and_citation(data) -> None:
    """관측마다 **어느 저장소에서 · 어떤 형태로 · 어디서** 봤는지가 있어야 한다.

    셋 중 하나라도 없으면 그건 관측이 아니라 주장이다. 특히 `form`(요청 스키마 필드인가,
    삭제 코드인가, YAML 데이터인가)이 중요하다 — 같은 사실이라도 어떤 형태로 적혀
    있느냐에 따라 믿을 수 있는 정도가 다르다.
    """
    for edge in data["edges"]:
        obs = edge["observations"]
        assert obs, f"{edge['from']}→{edge['to']}: 관측이 없다"
        for o in obs:
            for key in ("source", "form", "cite"):
                assert o.get(key), f"{edge['from']}→{edge['to']}: {key}가 없다 — {o}"


def test_our_own_grading_scheme_is_not_back(data) -> None:
    """**우리가 얹은 분류를 1급 필드로 되돌리지 않는다.**

    이전 판은 관측을 `D1~D9` 증거층으로 묶고 `layers`를 간선의 주 필드로 뒀다. 그건
    우리 분류이지 근거가 아니었고, 층 이름이 인용을 가리는 구조였다(라벨이 주, 인용이
    종). 등급 체계는 편해서 다시 기어들어오기 쉬우므로 검사로 막는다.
    """
    banned = {"layers", "layerCount", "kind"}
    for edge in data["edges"]:
        present = banned & set(edge)
        assert not present, f"{edge['from']}→{edge['to']}: 분류가 되돌아왔다 {present}"
    assert "evidenceLayers" not in data, "층 사전이 되돌아왔다"
    assert data.get("_retracted"), "무엇을 왜 걷어냈는지가 남아 있어야 한다"


def test_ordering_alone_never_makes_an_edge(data) -> None:
    """**운영 순서만으로는 간선이 서지 않는다.**

    순서는 의존을 함의하지 않는다 — A 다음에 B가 온다고 B가 A를 요구하는 것은 아니다.
    초안이 스크립트의 선형 순서에서 쌍을 만들어 `image→vNet` 같은 거짓 간선 10개를
    냈다. 순서 관측은 다른 형태의 관측이 이미 제안한 간선을 **보강**할 뿐이다.
    """
    alone = [
        (e["from"], e["to"]) for e in data["edges"]
        if {o["form"] for o in e["observations"]} == {"운영 스크립트 순서"}
    ]
    assert not alone, f"순서 관측만으로 선 간선: {alone}"


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


def test_the_most_independently_observed_edges_are_the_vm_ones(data) -> None:
    """서로 다른 **다섯 형태**로 관측되는 간선은 정확히 node의 셋이다.

    이 셋(vNet·sshKey·securityGroup)은 요청 스키마 필드 · 생성 전 존재 확인 코드 ·
    삭제 보호 코드 · 자동 생성 코드 · CSP 중립 인터페이스에서 **각각 독립적으로**
    관측된다. 등급을 매겨서가 아니라 **서로 다른 자리에서 같은 말이 나와서** 단단한
    것이고, 연계 리소스 군(과제 문제 ②)의 답이 그 위에 선다.

    줄면 근거가 약해진 것이고 늘면 형태 구분이 헐거워진 것이다 — 어느 쪽이든 사람이
    봐야 한다.
    """
    many = {
        (e["from"], e["to"]) for e in data["edges"]
        if len({o["form"] for o in e["observations"]}) >= 5
    }
    assert many == {
        ("node", "vNet"), ("node", "sshKey"), ("node", "securityGroup"),
    }, f"다섯 형태로 관측되는 간선이 바뀌었다: {sorted(many)}"


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


def test_questions_and_authorities_are_projections_of_observations(data) -> None:
    """`questions`·`authorities`는 관측에서 재계산 가능해야 한다.

    `D1~D9` 층을 걷어낸 이유는 라벨이 인용을 가리는 구조여서다
    (`test_our_own_grading_scheme_is_not_back`). 이 두 필드가 같은 길을 가지 않는
    조건이 이 검사다 — 저장된 값이 관측의 사영과 어긋나면, 필드가 근거에서 떨어져
    나와 등급이 된 것이다. 대응표는 `graphkb/edge_semantics.py`(우리 구성으로 표시).

    질문 없는 간선도 금지다 — 어떤 질문에도 답하지 못하는 관측 묶음은 간선이 아니라
    주장이다(순서 관측만으로 서는 간선 금지와 같은 규율의 일반화).
    """
    from app.core.cloudkb.graphkb import edge_semantics

    assert data.get("_semantics"), "도출 규칙의 소재를 산출물이 밝혀야 한다"
    for edge in data["edges"]:
        name = f"{edge['from']}→{edge['to']}"
        assert edge["questions"] == edge_semantics.questions_of(edge), (
            f"{name}: questions가 관측과 어긋난다 — 필드를 고치지 말고 관측을 보라"
        )
        assert edge["authorities"] == edge_semantics.authorities_of(edge), (
            f"{name}: authorities가 관측과 어긋난다"
        )
        assert edge["questions"], f"{name}: 어떤 질문에도 답하지 못하는 간선이다"


def test_cloud_authority_needs_vendor_evidence_not_cb_evidence(data) -> None:
    """`cloud` 권위는 CB 소스 관측만으로는 서지 않는다.

    "cb-tumblebug이 요구한다 ≠ 클라우드가 요구한다"(진실 문서 §6) — sshKey가 그
    반례였다(29개 외부 소스에서 키 자원 0건, 커밋 a490071). 지금 관측은 전부
    CB 두 저장소에서 왔으므로 권위는 도구 층까지만 선다. `cloud`를 열려면 벤더
    스키마 원문·드라이버 코드 관측(계획 P2)을 **관측으로** 달아야 하고, 그때 이
    검사를 같은 커밋에서 바꾼다.
    """
    for edge in data["edges"]:
        outside = set(edge["authorities"]) - {"tumblebug", "spider"}
        assert not outside, (
            f"{edge['from']}→{edge['to']}: 관측 없이 선 권위 {sorted(outside)}"
        )


def test_the_only_lifecycle_only_edge_is_node_to_customimage(data) -> None:
    """존재 증거 없이 생명주기만 관측된 간선은 정확히 하나 — `node→customImage`.

    노드는 customImage 없이도 만들어지므로(required: false — 이미지는 선택 원천)
    존재 의존 증거가 없는 것이 맞고, 남는 것은 참조 카운트(삭제 보호,
    `core/infra/control.go:927`)뿐이다. 이 집합이 늘면 존재 증거 없는 간선이 새로
    생긴 것이고, 줄면 근거가 사라진 것이다 — 어느 쪽이든 사람이 봐야 한다.
    """
    lifecycle_only = {
        (e["from"], e["to"]) for e in data["edges"] if e["questions"] == ["lifecycle"]
    }
    assert lifecycle_only == {("node", "customImage")}, (
        f"생명주기 단독 간선이 바뀌었다: {sorted(lifecycle_only)}"
    )
