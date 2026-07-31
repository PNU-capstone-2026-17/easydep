"""인프라 의도(depkb.infra_intent)의 구조적 불변식 — P1.

지키는 것: 판정이 claims에서 오고 문장이 우리 구성으로 표시되는가 · 서버가
채우는 것에 고지가 붙는가 · 대신 정하지 않는가 · 모르면 죽는가 · 순서가
의존과 모순되지 않는가.
"""

from __future__ import annotations

import json

import pytest

from app.core.cloudkb.depkb.infra_intent import SCHEMA_VERSION, build

CSPS = ("aws", "azure", "gcp")


def test_every_csp_yields_a_valid_intent() -> None:
    """3사 × 대표 앵커에서 스키마가 서고 JSON으로 직렬화된다."""
    for csp in CSPS:
        for anchor in ("k8sCluster", "vm"):
            intent = build([anchor], csp, "test-region")
            assert intent.schemaVersion == SCHEMA_VERSION
            assert intent.anchors == (anchor,)
            doc = json.loads(intent.to_json())
            assert doc["csp"] == csp and doc["region"] == "test-region"
            assert anchor in doc["createOrder"]


def test_create_order_respects_required_edges() -> None:
    """생성 순서는 필수 의존과 모순될 수 없다 — 앵커가 마지막이고, 필수로
    딸려온 것은 그 앞에 있어야 한다."""
    for csp in CSPS:
        intent = build(["k8sCluster"], csp, "r")
        order = list(intent.createOrder)
        assert order[-1] == "k8sCluster"
        for r in intent.resources:
            if r.role == "required":
                assert order.index(r.id) < order.index("k8sCluster"), (
                    f"{csp}: {r.id}가 앵커 뒤에 온다"
                )


def test_multi_anchor_order_stays_topological() -> None:
    """앵커를 합쳐도 순서가 필수 간선과 모순되지 않는다.

    폐포별 인덱스를 섞는 병합은 위상 순서를 뒤집는다(A=(X,Y)·B=(Z,X)에서
    min-rank가 X를 Z 앞에 놓는다) — 그래서 합집합 위에서 다시 정렬한다.
    단일 앵커만 검사하면 이 결함이 안 잡힌다.
    """
    from app.core.cloudkb.depkb.closure import _claims

    for csp in CSPS:
        intent = build(["k8sCluster", "vm", "loadBalancer"], csp, "r")
        pos = {name: i for i, name in enumerate(intent.createOrder)}
        for c in _claims():
            if (c["csp"] == csp and c["question"] == "existence"
                    and c["verdict"] == "required"
                    and c["subject"] in pos and c["object"] in pos):
                assert pos[c["object"]] < pos[c["subject"]], (
                    f"{csp}: {c['object']}가 {c['subject']} 뒤에 온다"
                )


def test_autofilled_never_stays_silent() -> None:
    """서버가 채우는 것은 반드시 고지 문장을 갖는다 — 침묵하면 사용자가
    통제를 잃는다(실측된 대체만 여기 온다)."""
    for csp in CSPS:
        intent = build(["k8sCluster", "vm"], csp, "r")
        for a in intent.autoFilled:
            assert a.notice and csp in a.notice, f"{csp}/{a.id}: 고지가 없다"
        auto_ids = {a.id for a in intent.autoFilled}
        roles = {r.id: r.role for r in intent.resources}
        for rid in auto_ids:
            assert roles[rid] == "attachable", (
                f"{csp}: 필수인 것이 autoFilled에 있다 — {rid}"
            )


def test_decisions_carry_readable_questions_not_predicates() -> None:
    """사람이 정할 것은 술어 원문이 아니라 질문 문장으로 나간다."""
    intent = build(["loadBalancer"], "azure", "r")
    assert intent.decisions, "azure LB의 선언 술어가 결정으로 안 나온다"
    for d in intent.decisions:
        assert d.question.endswith("?"), f"질문이 아니다: {d.question}"
        assert "disjunctive" not in d.question and "server-" not in d.question


def test_constraints_carry_measured_rules() -> None:
    """제약은 실측된 술어에서 온다 — aws k8s의 다른 AZ ≥2가 대표."""
    intent = build(["k8sCluster"], "aws", "r")
    kinds = {(c.kind, c.subject, c.object) for c in intent.constraints}
    assert ("배치 조건", "k8sCluster", "subnet") in kinds
    azure = build(["k8sCluster"], "azure", "r")
    assert any(c.kind == "수명 조건" for c in azure.constraints)


def test_csp_answers_differ_and_that_is_the_point() -> None:
    """같은 앵커가 CSP마다 다른 계획을 낸다 — 양상 반전의 소비측.

    aws는 사용자에게 network·subnet을 요구하고, gcp는 전부 서버가 채우며,
    azure는 노드풀을 필수로 든다. 이 차이가 사라지면 배선이 CSP를 잃은 것이다.
    """
    roles = {}
    for csp in CSPS:
        intent = build(["k8sCluster"], csp, "r")
        roles[csp] = {r.id: r.role for r in intent.resources}
    assert roles["aws"]["subnet"] == "required"
    assert roles["gcp"]["subnet"] == "attachable"
    assert roles["azure"]["k8sNodeGroup"] == "required"


def test_unknown_and_empty_anchors_fail_loudly() -> None:
    with pytest.raises(ValueError):
        build([], "aws", "r")
    with pytest.raises(KeyError):
        build(["quantumComputer"], "aws", "r")


def test_provenance_declares_what_is_ours() -> None:
    """근거와 우리 구성이 산출물 안에서 갈려 있어야 한다."""
    intent = build(["vm"], "gcp", "r")
    assert intent.provenance["oracleLayer"] == "apply"
    assert "우리 구성" in intent.provenance["note"]
