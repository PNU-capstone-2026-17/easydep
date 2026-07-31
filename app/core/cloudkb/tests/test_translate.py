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


def test_ingress_adds_the_load_balancer_anchor() -> None:
    t = translate(_intent({"name": "api", "kind": "Deployment",
                           "capabilities": {"ingress": True}}))
    assert set(t.anchors) == {"k8sCluster", "loadBalancer"}


def test_pvc_adds_the_disk_anchor() -> None:
    t = translate(_intent({"name": "db", "kind": "StatefulSet",
                           "capabilities": {"pvc": True}}))
    assert "disk" in t.anchors


def test_unknown_kind_becomes_a_question_never_a_guess() -> None:
    """모르는 kind에 앵커를 붙이면 계획 전체가 근거를 잃는다."""
    t = translate(_intent({"name": "x", "kind": "SomethingElse"}))
    assert t.anchors == ()
    assert t.open_questions and "x" in t.open_questions[0]


def test_empty_workloads_is_a_question_not_an_empty_plan() -> None:
    t = translate(_intent())
    assert t.anchors == () and t.open_questions


def test_service_without_ingress_is_flagged_as_unmeasured() -> None:
    """k8s Service의 클라우드 LB 자동 생성은 한 번도 재지 않았다 — 우리가 LB를
    또 만들면 이중 생성이 된다. 추측으로 메우지 않고 경고로 내보낸다."""
    t = translate(_intent({"name": "api", "kind": "Deployment",
                           "capabilities": {"service": True}}))
    assert t.unmeasured and "이중 생성" in t.unmeasured[0]
    assert "loadBalancer" not in t.anchors, (
        "측정 안 한 경로로 앵커를 만들면 안 된다"
    )


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
    assert {"k8sCluster", "loadBalancer", "subnet", "network"} <= ids
