"""배선의 불변식 — **붙이되 고치지 않는다.**

`plan_enrich`는 대조기가 센 틈을 메우는 쪽이다. 표본 3종 × 3사에서 17·15·10이던
것이 배선 뒤 4·5·4로 줄었고(2026-08-01), 남은 것은 배선으로 닫을 수 없는 종류다
(계획이 컴퓨트 노드에 타입을 안 다는 것 · 관리형 서비스 실측 0건).

여기서 지키는 것:

  - **노드·선을 건드리지 않는다.** 계획의 모양은 설계 산출물이 정한다.
  - `None`(안 붙였다)과 빈 묶음(붙였는데 아는 게 없다)을 가른다.
  - 필수는 **안 그렸어도 순서에 싣는다** — 없으면 apply가 거부한다.
  - 어휘 밖은 침묵하지 않는다.
"""

from __future__ import annotations

from dataclasses import replace

from app.core.cloudkb.appkb.plan import DeploymentPlan, PlanNode
from app.core.plan_enrich import enrich, render


def _plan(*nodes: PlanNode) -> DeploymentPlan:
    plan = DeploymentPlan(name="t")
    plan.nodes = list(nodes)
    return plan


def _vm(node_id: str = "app") -> PlanNode:
    return PlanNode(node_id, "app", "compute", "inferred", host="VM")


def _sample_plan() -> DeploymentPlan:
    return _plan(
        _vm(),
        PlanNode("lb", "lb", "ingress", "inferred",
                 type_id="aws::AWS::ElasticLoadBalancingV2::LoadBalancer"),
        PlanNode("net", "net", "shared", "kb", type_id="aws::AWS::EC2::VPC"),
        PlanNode("sub", "sub", "shared", "kb", type_id="aws::AWS::EC2::Subnet"),
        PlanNode("db", "db", "managed", "inferred",
                 type_id="aws::AWS::RDS::DBInstance"),
    )


def test_enrich_does_not_touch_the_nodes_or_edges() -> None:
    """**붙이되 고치지 않는다** — 계획의 모양은 설계 산출물이 정한다."""
    plan = _sample_plan()
    before = [replace(n) for n in plan.nodes]
    enrich(plan, "aws", "ap-northeast-2")
    assert plan.nodes == before
    assert plan.edges == []


def test_the_order_carries_a_required_resource_even_if_it_is_not_drawn() -> None:
    """aws `image`는 계획에 노드가 없다 — 그래도 순서에 실려야 한다.

    계획은 이미지를 자원이 아니라 **값**으로 다뤄 컴퓨트 노트로 붙인다(그 판단은
    맞다). 그래서 노드 집합만 보면 "필수가 빠졌다"로 읽히는데, 없으면 apply가
    거부하므로 실행하는 사람에게는 요건으로 가야 한다.
    """
    plan = enrich(_sample_plan(), "aws", "-")
    assert "image" in plan.measured.create_order
    assert plan.measured.create_order.index("image") == 0
    assert "vm" in plan.measured.create_order


def test_optional_resources_do_not_leak_into_the_order() -> None:
    """선택 자원까지 실으면 계획이 아니라 폐포를 옮겨 적는 것이 된다."""
    plan = enrich(_sample_plan(), "aws", "-")
    # `sshKey`는 aws vm에 선택이고 이 계획에 없다 — 순서에 들어오면 안 된다.
    assert "sshKey" not in plan.measured.create_order


def test_the_same_pair_measured_twice_is_one_warning() -> None:
    """aws `subnet→internetGateway`는 인바운드·아웃바운드로 **따로** 실측됐다.

    주장은 둘이지만 사람에게 할 말은 하나다 — 같은 줄을 두 번 내면 목록이
    길어질수록 신뢰가 떨어진다.
    """
    plan = enrich(_sample_plan(), "aws", "-")
    pairs = [(s, o) for s, o, _ in plan.measured.operational_warnings]
    assert len(pairs) == len(set(pairs)), pairs
    assert ("subnet", "internetGateway") in pairs


def test_a_plan_with_no_measurable_workload_says_unknown_not_empty() -> None:
    """앵커가 없으면 **빈 묶음**을 붙인다 — `None`(안 붙였다)과 다른 사실이다."""
    plan = enrich(_plan(PlanNode("db", "db", "managed", "inferred",
                                 type_id="aws::AWS::RDS::DBInstance")), "aws")
    assert plan.measured is not None
    assert plan.measured.anchors == ()
    assert plan.measured.unmeasured == ("db",)
    assert "unknown" in render(plan.measured)


def test_silence_is_never_rendered_as_a_pass() -> None:
    """어휘 밖·미부착을 침묵으로 두지 않는다."""
    assert "not attached" in render(None)
    plan = enrich(_sample_plan(), "aws", "-")
    text = render(plan.measured)
    assert "silence here is not a pass" in text
    assert "db" in text


def test_the_two_kinds_of_server_filling_read_differently() -> None:
    """`server-implicit`(이중 생성)과 `server-default`(정해도 된다)를 가른다."""
    text = render(enrich(_sample_plan(), "aws", "-").measured)
    assert "creating it as well is a duplicate" in text   # nic · disk
    assert "you may still set it yourself" in text        # firewall


def test_a_waited_operation_does_not_claim_it_must_be_waited_for() -> None:
    """미표시는 '동기'가 아니고, `waited`는 '기다려야 한다'가 아니다."""
    plan = enrich(_sample_plan(), "aws", "-")
    for _rid, _op, _signal, confidence in plan.measured.wait_for:
        assert confidence in ("async-confirmed", "waited")
    text = render(plan.measured)
    if any(c == "waited" for *_x, c in plan.measured.wait_for):
        assert "not proof that waiting is required" in text


def test_compose_attaches_it_so_the_whole_chain_carries_the_measurement() -> None:
    """**배선의 요점** — 설계 산출물에서 나온 계획이 실측을 들고 나온다."""
    import json
    from pathlib import Path

    from app.core.cloudkb.nim_agent.design_tools import compose

    root = (Path(__file__).resolve().parents[1] / "appkb" / "samples"
            / "lecture-platform")
    spec = json.loads((root / "requirements" / "resource_spec.json")
                      .read_text(encoding="utf-8"))
    from app.core.cloudkb.tools.intake_report import _design_from

    design, _ = _design_from(root, spec)
    plan = compose(design)
    assert plan.measured is not None and plan.measured.anchors
    assert plan.measured.create_order and plan.measured.delete_before
    assert plan.measured.operational_warnings
    assert plan.to_dict()["measured"]["csp"] == "aws"
