"""예제와 데모의 불변식 — 전체 사슬이 돌고, 규율이 시연되는가.

예제는 시연물이지만 **주장을 담고 있다**(이 자리에서 답이 갈린다는 주장).
그래서 그 주장이 실제로 산출물에서 확인되는지 검사한다 — 문서에만 있는 시연은
다음 턴에 근거로 둔갑한다.
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.depkb.examples import EXAMPLES, by_id
from app.core.cloudkb.depkb.plantuml import deployment_puml, deployment_puml_set
from app.core.infra_planning import plan_for_anchors, plan_from_deployment_intent

CSPS = ("aws", "azure", "gcp")


def test_every_example_flows_through_the_chain() -> None:
    """요구사항 → 배포 의도 → 계획이 예제마다 돈다(재지 않은 자원은 제외)."""
    for ex in EXAMPLES:
        plan = plan_from_deployment_intent(ex.deployment_intent, "azure", "-")
        assert plan.intent.anchors, ex.id
        assert plan.design["nodes"] and plan.provision["createOrder"]


def test_example1_shows_three_different_shapes() -> None:
    """① 같은 요구가 3사에서 구조가 다른 계획이 된다 — 이 예제의 주장이다."""
    ex = by_id("portable-api")
    shapes = {}
    for csp in CSPS:
        p = plan_from_deployment_intent(ex.deployment_intent, csp, "-")
        shapes[csp] = (tuple(p.intent.createOrder),
                       tuple(x["id"] for x in p.provision["doNotCreate"]))
    assert len(set(shapes.values())) == 3, f"세 답이 같다: {shapes}"
    # 서버가 채우는 것도 CSP마다 다르다
    assert shapes["gcp"][1] != shapes["aws"][1]


def test_example2_catches_the_zone_mismatch() -> None:
    """② 존이 어긋난 계획을 검사기가 잡는다 — 실측(gcp invalid)의 재현."""
    ex = by_id("stateful-store")
    checked = plan_for_anchors(list(ex.check_anchors), "gcp", "-",
                               concrete_plan=ex.concrete_plans["gcp"])
    assert checked.report is not None and not checked.report.ok
    assert any("존" in v.rule for v in checked.report.violations)


def test_example2_declares_our_own_gap() -> None:
    """②는 우리 공백도 말한다 — k8s PVC → 클라우드 디스크 경로는 미측정이라
    검사를 다른 층(vm→disk)에서 했고, 그 사실을 밝힌다."""
    ex = by_id("stateful-store")
    assert ex.check_anchors and ex.check_anchors_why
    assert any("재지 않았다" in h for h in ex.hard_for)


def test_example3_catches_the_name_condition() -> None:
    """③ GatewaySubnet 이름 조건 위반을 잡는다 — 실측 재현."""
    ex = by_id("private-link")
    checked = plan_for_anchors(list(ex.check_anchors), "azure", "-",
                               concrete_plan=ex.concrete_plans["azure"])
    assert not checked.report.ok
    assert any("GatewaySubnet" in v.detail for v in checked.report.violations)


def test_example3_refuses_to_plan_where_we_did_not_measure() -> None:
    """③에서 aws·gcp는 계획이 **없어야** 한다 — vpn을 그 두 곳에서 재지
    않았다. 추측으로 채우면 이 프로젝트의 전제가 무너진다."""
    ex = by_id("private-link")
    for csp in ("aws", "gcp"):
        with pytest.raises(KeyError):
            plan_for_anchors(["k8sCluster", *ex.given_anchors], csp, "-")
    azure = plan_for_anchors(["k8sCluster", *ex.given_anchors], "azure", "-")
    assert "vpn" in azure.intent.createOrder


def test_examples_do_not_claim_about_uncompared_systems() -> None:
    """비교군을 측정하지 않았다 — 예제가 '비교군이 틀린다'고 말하면 안 된다."""
    for ex in EXAMPLES:
        for line in ex.hard_for + ex.highlights:
            assert "MetaGPT" not in line and "GPT" not in line, ex.id


def test_deployment_diagram_is_valid_plantuml() -> None:
    """배포 다이어그램이 PlantUML로 나온다 — 설계 에이전트의 산출 형식."""
    plan = plan_for_anchors(["k8sCluster"], "aws", "-")
    puml = deployment_puml(plan.intent, title="t", slug="t")
    assert puml.startswith("@startuml") and puml.rstrip().endswith("@enduml")
    assert puml.count("@startuml") == puml.count("@enduml") == 1
    assert "화살표 A --> B" in puml, "방향의 뜻을 그림에 적어야 한다"


def test_plantuml_encodes_role_by_stereotype_not_color_alone() -> None:
    """색만으로 구분하지 않는다 — 스테레오타입 문자열이 같은 정보를 나른다."""
    plan = plan_for_anchors(["k8sCluster"], "gcp", "-")
    puml = deployment_puml(plan.intent)
    assert "<<선택한 것>>" in puml
    assert "<<자동>>" in puml, "gcp는 서버가 채우는 자원이 여럿이다"


def test_plantuml_carries_reasons_and_rules() -> None:
    """그림이 '왜'와 '지켜야 할 것'을 함께 나른다 — 그림만 보고도 판단 가능."""
    plan = plan_for_anchors(["k8sCluster"], "aws", "-")
    puml = deployment_puml(plan.intent)
    assert "note right of" in puml and "왜:" in puml
    assert "legend bottom" in puml and "지켜야 할 규칙" in puml


def test_puml_set_puts_csps_side_by_side() -> None:
    """한 파일에 CSP별 다이어그램이 나란히 들어간다 — 차이가 요점이다."""
    intents = {csp: plan_for_anchors(["k8sCluster"], csp, "-").intent
               for csp in CSPS}
    puml = deployment_puml_set(intents, title="비교")
    assert puml.count("@startuml") == len(CSPS)
    for csp in CSPS:
        assert f"— {csp}" in puml
