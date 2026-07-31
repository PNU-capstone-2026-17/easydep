"""하류 신호 번역(depkb.translate)의 규율 — P2.

지키는 것: 앵커를 추측하지 않는가 · 우리 축 밖을 침묵하지 않는가 · 측정 안 한
영역을 메우지 않는가 · 모든 앵커가 근거를 갖는가.
"""

from __future__ import annotations

from app.core.cloudkb.depkb.translate import OUT_OF_SCOPE, translate


def _intent(*workloads: dict) -> dict:
    return {"schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "test", "workloads": list(workloads)}


def test_workload_kind_gives_the_cluster_anchor() -> None:
    """`kind: Deployment`가 곧 k8sCluster다 — 매핑을 발명할 필요가 없었다."""
    t = translate(_intent({"name": "api", "kind": "Deployment"}))
    assert t.anchors == ("k8sCluster",)
    assert not t.open_questions


def test_ingress_adds_the_k8singress_anchor_not_a_direct_lb() -> None:
    """2라운드(2026-07-31)로 갱신: gcp는 내장 컨트롤러가 성좌를 합성한다 —
    loadBalancer 직접 앵커면 이중 생성이다. azure·aws 기본 구성은 합성이
    없고, 그 사실은 폐포에서 attachable(비자동)로 내려간다."""
    t = translate(_intent({"name": "api", "kind": "Deployment",
                           "capabilities": {"ingress": True}}))
    assert set(t.anchors) == {"k8sCluster", "k8sIngress"}


def test_pvc_adds_the_k8spvc_anchor_not_a_direct_disk() -> None:
    """2026-07-31 합성 라운드로 갱신: PVC의 실체는 CSI가 합성하는 디스크다
    (azure·gcp 실측). disk를 직접 앵커로 삼으면 IaC가 디스크를 또 만든다."""
    t = translate(_intent({"name": "db", "kind": "StatefulSet",
                           "capabilities": {"pvc": True}}))
    assert "k8sPvc" in t.anchors
    assert "disk" not in t.anchors


def test_unknown_kind_becomes_a_question_never_a_guess() -> None:
    """모르는 kind에 앵커를 붙이면 계획 전체가 근거를 잃는다."""
    t = translate(_intent({"name": "x", "kind": "SomethingElse"}))
    assert t.anchors == ()
    assert t.open_questions and "x" in t.open_questions[0]


def test_empty_workloads_is_a_question_not_an_empty_plan() -> None:
    t = translate(_intent())
    assert t.anchors == () and t.open_questions


def test_service_without_ingress_becomes_the_k8sservice_anchor() -> None:
    """unmeasured였다가 2026-07-31 합성 라운드 실측으로 앵커가 됐다:
    type=LoadBalancer 서비스가 클라우드 LB를 합성한다(3사 apply). LB는
    직접 앵커가 아니다 — 폐포에서 autoFilled로 내려간다(이중 생성 방지)."""
    t = translate(_intent({"name": "api", "kind": "Deployment",
                           "capabilities": {"service": True}}))
    assert "k8sService" in t.anchors
    assert "loadBalancer" not in t.anchors, (
        "LB를 직접 앵커로 만들면 IaC가 LB를 또 만든다"
    )
    assert not t.unmeasured


def test_out_of_scope_signals_are_recorded_with_reasons() -> None:
    """우리 축이 아닌 것은 침묵하지 않는다 — 침묵하면 누락처럼 보인다.

    특히 networkPolicy는 k8s 층 오브젝트라 클라우드 firewall과 섞으면
    없는 의존을 만든다.
    """
    t = translate(_intent({"name": "api", "kind": "Deployment",
                           "replicas": {"min": 2, "max": 5},
                           "capabilities": {"networkPolicy": True, "hpa": True}}))
    recorded = dict(t.ignored)
    assert {"replicas", "networkPolicy", "hpa"} <= set(recorded)
    assert "firewall" not in t.anchors
    for signal, why in recorded.items():
        assert why == OUT_OF_SCOPE[signal]


def test_every_anchor_carries_its_rationale() -> None:
    """근거 없는 앵커 금지 — 계획서가 '왜 이게 여기 있나'에 답해야 한다."""
    t = translate(_intent(
        {"name": "api", "kind": "Deployment", "capabilities": {"ingress": True}},
        {"name": "db", "kind": "StatefulSet", "capabilities": {"pvc": True}}))
    assert {a for a, _ in t.rationale} == set(t.anchors)
    for _, why in t.rationale:
        assert why.strip()


def test_translation_feeds_the_intent_builder() -> None:
    """번역 결과가 인프라 의도 빌더의 입력으로 그대로 들어간다(P1↔P2 접합)."""
    from app.core.cloudkb.depkb.infra_intent import build

    t = translate(_intent({"name": "api", "kind": "Deployment",
                           "capabilities": {"ingress": True}}))
    intent = build(list(t.anchors), "aws", "ap-northeast-2")
    ids = {r.id for r in intent.resources}
    # loadBalancer는 k8sIngress의 attachable로 내려온다(기본 구성 무합성 실측)
    assert {"k8sCluster", "k8sIngress", "loadBalancer", "subnet", "network"} <= ids
