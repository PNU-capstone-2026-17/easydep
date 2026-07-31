"""인프라 계획 진입점(app.core.infra_planning)의 계약 — P5.

이 문은 **다른 영역이 부르는 자리**다. 그래서 여기서 지키는 것은 계약이다:
한 번 호출로 두 뷰가 나오는가 · 경계를 넘지 않는가 · 모르면 죽는가 ·
물어야 할 것과 말할 수 없는 것이 함께 나오는가.
"""

from __future__ import annotations

import pytest

from app.core.infra_planning import (
    plan_for_anchors,
    plan_from_deployment_intent,
)

CSPS = ("aws", "azure", "gcp")


def _di(**caps) -> dict:
    return {"schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "t",
            "workloads": [{"name": "api", "kind": "Deployment",
                           "capabilities": caps}]}


def test_one_call_gives_both_views() -> None:
    """설계(배포 다이어그램)와 구현(manifest+IaC)이 한 번에 나온다 — 부르는
    쪽이 depkb 내부 모듈 순서를 알 필요가 없다."""
    for csp in CSPS:
        p = plan_from_deployment_intent(_di(ingress=True), csp, "r")
        assert p.design["view"] == "design"
        assert p.provision["view"] == "provision"
        assert p.intent.anchors


def test_provision_view_declares_the_layer_boundary() -> None:
    """구현 에이전트는 manifest도 낸다 — 우리 침묵을 '제약 없음'으로 읽으면
    안 된다는 것을 진입점을 통해서도 확인한다."""
    p = plan_from_deployment_intent(_di(), "gcp", "r")
    assert p.provision["layer"] == "cloud"
    assert "kubernetes" in p.provision["notForLayer"]


def test_questions_merge_translation_and_intent() -> None:
    """물어야 할 것은 한 곳에 모인다 — 하류 신호에서 못 읽은 것과 우리가
    대신 정하지 않는 것 둘 다."""
    # ingress가 있어야 loadBalancer 앵커가 서고, 그 선언 술어가 질문이 된다.
    p = plan_from_deployment_intent(_di(ingress=True), "azure", "r")
    assert any("고르세요" in q for q in p.questions), p.questions


def test_unmeasured_anchor_is_demoted_not_planned() -> None:
    """aws의 k8sPvc→disk는 미측정(간선 없음 — 범위 표시)이다. 번역이 그 앵커를
    읽어도 계획을 내지 않고 unmeasured로 강등한다 — 나머지 앵커 계획은 낸다."""
    p = plan_from_deployment_intent(_di(pvc=True), "aws", "r")
    assert any("k8sPvc" in u and "미측정" in u for u in p.unmeasured), p.unmeasured
    assert "k8sPvc" not in p.intent.anchors
    assert "k8sCluster" in p.intent.anchors, "나머지 계획은 나와야 한다"


def test_service_synthesis_flows_into_donotcreate_and_cascades() -> None:
    """합성 실측이 소비층까지 닿는다: k8sService 앵커면 provision 뷰가 LB를
    doNotCreate로 내리고, 동반 정리(cleanupCascades)로 삭제 단계 금지를 알린다.
    이것이 이중 생성 경계(I2)의 소비측 완결이다."""
    p = plan_from_deployment_intent(_di(service=True), "azure", "r")
    dnc = {d["id"] for d in p.provision["doNotCreate"]}
    assert "loadBalancer" in dnc
    cascades = {(c["owner"], c["synthesized"])
                for c in p.provision["cleanupCascades"]}
    assert ("k8sService", "loadBalancer") in cascades
    assert not p.unmeasured


def test_out_of_scope_signals_become_notes() -> None:
    """우리 축이 아닌 신호는 사유와 함께 notes로 — 침묵하면 누락처럼 보인다."""
    di = _di(hpa=True, networkPolicy=True)
    di["workloads"][0]["replicas"] = {"min": 1, "max": 3}
    p = plan_from_deployment_intent(di, "aws", "r")
    joined = " ".join(p.notes)
    assert "replicas" in joined and "networkPolicy" in joined


def test_unreadable_intent_fails_loudly() -> None:
    """앵커를 못 읽으면 추측하지 않고 죽는다 — 이유를 담아서."""
    with pytest.raises(ValueError) as e:
        plan_from_deployment_intent(
            {"workloads": [{"name": "x", "kind": "Unknown"}]}, "aws", "r")
    assert "x" in str(e.value)


def test_concrete_plan_is_checked_when_given() -> None:
    """계획을 함께 주면 실측 규칙 위반을 같은 호출에서 잡는다."""
    bad = {"resources": [
        {"id": "network", "instances": [{"name": "vpc"}]},
        {"id": "subnet", "instances": [{"name": "s1", "zone": "a"},
                                       {"name": "s2", "zone": "a"}]},
        {"id": "k8sCluster", "instances": [{"name": "c"}]}]}
    p = plan_for_anchors(["k8sCluster"], "aws", "r", concrete_plan=bad)
    assert p.report is not None and not p.report.ok
    assert any("같은 영역" in v.detail for v in p.report.violations)


def test_gate_does_not_import_other_agent_areas() -> None:
    """경계: 이 문은 다른 영역(app.design·app.requirements)을 부르지 않는다.

    dict를 받고 dict를 주는 관계라 저쪽이 자기 파이프라인에서 호출하면 된다.
    """
    from pathlib import Path

    import app.core.infra_planning as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("app.design", "app.requirements", "app.implementation"):
        assert f"import {forbidden}" not in src and f"from {forbidden}" not in src
