"""예제와 데모의 불변식 — 전체 사슬이 돌고, 규율이 시연되는가.

예제는 시연물이지만 **주장을 담고 있다**(이 자리에서 답이 갈린다는 주장).
그래서 그 주장이 실제로 산출물에서 확인되는지 검사한다 — 문서에만 있는 시연은
다음 턴에 근거로 둔갑한다.
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.depkb.examples import EXAMPLES, by_id
from app.core.cloudkb.depkb.render_deployment import render
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


def test_deployment_diagram_renders() -> None:
    """배포 다이어그램이 자립형 HTML로 나온다(3예제 × 3열)."""
    html = render()
    assert html.startswith("<!doctype html>")
    assert html.count("<article") == len(EXAMPLES)
    assert "계획 없음" in html, "재지 않은 CSP를 빈 패널로 보여야 한다"
    assert "http://" not in html and "https://" not in html.replace(
        "http-equiv", ""), "외부 자원을 참조하면 자립형이 아니다"
