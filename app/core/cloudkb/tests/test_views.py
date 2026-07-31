"""두 뷰(depkb.views)의 규율 — P3.

설계 뷰는 배포 다이어그램이, 프로비저닝 뷰는 manifest+IaC 생성기가 소비한다.
지키는 것: 뷰가 지식을 복사하지 않고 사영인가 · 다이어그램이 방향을 밝히는가 ·
IaC 뷰가 층 경계와 '만들지 말 것'을 밝히는가 · 결정이 미해결로 남는가.
"""

from __future__ import annotations

from app.core.cloudkb.depkb.infra_intent import build
from app.core.cloudkb.depkb.views import design_view, provision_view

CSPS = ("aws", "azure", "gcp")


def test_design_view_carries_structure_and_reasons_not_order() -> None:
    """다이어그램에는 시간축이 없다 — 순서 대신 근거를 싣는다."""
    for csp in CSPS:
        d = design_view(build(["k8sCluster"], csp, "r"))
        assert "createOrder" not in d and "deleteBefore" not in d
        assert d["nodes"] and d["view"] == "design"
        for node in d["nodes"]:
            if node["role"] == "required":
                assert node["because"], f"{csp}/{node['id']}: 근거 없는 노드"


def test_design_view_declares_edge_direction() -> None:
    """방향을 말하지 않은 다이어그램은 읽는 사람이 각자 해석한다."""
    d = design_view(build(["vm"], "azure", "r"))
    assert "요구한다" in d["edgeSemantics"]
    froms = {e["from"] for e in d["edges"]}
    assert "vm" in froms, "앵커에서 나가는 요구 간선이 없다"


def test_design_view_shows_autofilled_notice_on_the_node() -> None:
    """서버가 채우는 자원은 그림에서도 그렇다고 보여야 한다."""
    d = design_view(build(["vm"], "aws", "r"))
    auto = [n for n in d["nodes"] if n["autoFilledNotice"]]
    assert auto, "aws vm에는 서버 대체가 여럿인데 그림에 표시가 없다"
    for n in auto:
        assert n["role"] == "attachable"


def test_provision_view_declares_its_layer_and_silence() -> None:
    """구현 에이전트는 manifest(k8s)와 IaC(클라우드)를 둘 다 낸다. 우리 주장은
    후자뿐이므로, 침묵을 '제약 없음'으로 읽지 않도록 밝힌다."""
    p = provision_view(build(["k8sCluster"], "gcp", "r"))
    assert p["layer"] == "cloud"
    assert "kubernetes" in p["notForLayer"]
    assert "제약 없음" in p["notForLayerNote"]


def test_provision_view_says_what_not_to_create() -> None:
    """서버가 채우는 것을 우리가 또 만들면 계획이 실제와 어긋난다."""
    p = provision_view(build(["k8sCluster"], "gcp", "r"))
    ids = {x["id"] for x in p["doNotCreate"]}
    assert ids, "gcp k8s는 서버 대체가 여럿인데 doNotCreate가 비었다"
    for entry in p["doNotCreate"]:
        assert entry["why"], "왜 만들지 말아야 하는지가 없다"
    for step in p["createOrder"]:
        if step["id"] in ids:
            assert step["skipIfOmitted"] and step["comment"]


def test_provision_view_order_matches_the_intent() -> None:
    """뷰는 사영이다 — 순서를 자기가 다시 만들지 않는다."""
    intent = build(["k8sCluster", "vm"], "aws", "r")
    p = provision_view(intent)
    assert [s["id"] for s in p["createOrder"]] == list(intent.createOrder)
    assert [tuple(x) for x in p["deleteBefore"]] == list(intent.deleteBefore)


def test_constraint_kinds_are_classified_not_swallowed(  ) -> None:
    """제약의 부류가 규칙 문장을 통째로 삼키면 안 된다(콜론 없는 술어가 있다)."""
    p = provision_view(build(["loadBalancer"], "aws", "r"))
    kinds = {c["kind"] for c in p["checks"]}
    assert kinds and all(len(k) <= 8 for k in kinds), f"부류가 문장이다: {kinds}"


def test_open_decisions_block_provisioning() -> None:
    """사람이 정할 것이 남아 있으면 IaC 뷰가 그것을 막힌 것으로 표시한다 —
    대신 고르지 않는다는 규율의 소비측."""
    intent = build(["loadBalancer"], "azure", "r")
    assert provision_view(intent)["blockedBy"], "azure LB의 선택이 안 막는다"
    assert design_view(intent)["openDecisions"][0]["question"].endswith("?")
