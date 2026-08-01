"""되먹임 고리 — **검증 결과가 계획을 만든 쪽에 돌아가는가.**

목표 ①("요구사항 기반 산출물 검증 및 사용자 피드백을 위한 에이전트 갱신")의
고리다. 그전까지 검증 결과가 **사람에게만** 갔다 — 대조기가 위반을 찾아도 계획을
만든 쪽은 몰랐고 다음 실행도 똑같이 만들었다.

## 이 저장소는 여기서 한 번 물렸다

되돌아가기(C2)를 만들고 **값을 못 냈다**. 그래서 순서를 뒤집었다: 고리를 붙이기
전에 **되먹임이 결과를 바꿀 수 있는지부터 쟀다**(2026-08-01). 첫 측정에서
`multiZone`을 줘도 계획이 한 글자도 안 바뀌었고, 원인이 계획 생성기가 실측
폐포를 안 본다는 것이었다.

즉 그때 되묻기를 만들었다면 **답해도 소용없는 질문**을 냈을 것이다.

## 고리가 실제로 값을 냈다 — 이 파일이 그 기록

고리를 붙이자마자 첫 위반이 나왔다:

    [violated-rule] k8sCluster→subnet: 계획에 subnet이 0개다
                    실측이 2개 이상을 요구한다

원인은 `core::k8sCluster`의 기본 번들이 클러스터·노드그룹뿐이고 **네트워크·
서브넷이 없다**는 것이었다(VM 번들은 다 갖는다). 번들은 편의 정보이고 실측이
권위이므로 생성기가 폐포의 필수를 함께 보게 고쳤고, 위반이 닫혔다.

**고리 → 결함 발견 → 수리 → 0.** 이것이 값을 냈다는 증거다.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.core import feedback_loop, plan_crosscheck
from app.core.cloudkb.appkb.plan import DeploymentPlan, PlanNode
from app.core.cloudkb.nim_agent.design_tools import compose
from app.core.cloudkb.tools.intake_report import _design_from, _read

_SAMPLE = (Path(__file__).resolve().parents[1] / "appkb" / "samples"
           / "lecture-platform")


def _design(csp: str, *, kubernetes: bool) -> dict:
    spec, _ = _read(_SAMPLE, "requirements/resource_spec.json")
    design, problems = _design_from(_SAMPLE, spec if isinstance(spec, dict) else None)
    assert design is not None, problems
    doc = copy.deepcopy(design)
    doc.setdefault("requirements", {})["provider"] = csp
    if kubernetes:
        for component in doc.get("components", []):
            component["deployHint"] = {"compute": "kubernetes", "reason": "k8s"}
    return doc


def _plan(*nodes: PlanNode) -> DeploymentPlan:
    plan = DeploymentPlan(name="t")
    plan.nodes = list(nodes)
    return plan


def test_a_violation_reaches_the_plan_itself() -> None:
    """위반이 **계획의 미결**로 올라간다 — 따로 대조를 돌려야 보이면 안 본다.

    `unresolved`에 두는 이유는 소비자가 이미 그것을 읽기 때문이다. 새 칸을
    만들면 안 고친 쪽에서 위반이 조용히 사라진다.
    """
    from app.core.plan_enrich import enrich

    # 서브넷 하나짜리 k8s 계획 — 실측은 둘을 요구한다.
    plan = _plan(
        PlanNode("k8s", "k8s", "compute", "inferred", host="Kubernetes node"),
        PlanNode("sub", "sub", "shared", "kb", type_id="aws::AWS::EC2::Subnet"))
    enrich(plan, "aws", "-")
    raised = feedback_loop.apply_to_plan(plan, "aws", "-")
    assert raised >= 1, plan.unresolved
    assert any("violated-rule" in u for u in plan.unresolved), plan.unresolved
    # 이 계획은 손으로 만든 것이라 컴퓨트 노드에 벤더 타입이 없다 —
    # `weak-reading`도 함께 뜨는 것이 맞다(실제 `compose` 경로에선 안 뜬다).
    assert any("weak-reading" in u for u in plan.unresolved), plan.unresolved


def test_a_declared_boundary_does_not_fill_the_unresolved_list() -> None:
    """경계는 결함이 아니다 — 올리면 미결이 늘 차 있어 진짜 미결이 묻힌다."""
    from app.core.plan_enrich import enrich

    plan = _plan(
        PlanNode("vm", "vm", "compute", "inferred", host="VM"),
        PlanNode("db", "db", "managed", "inferred",
                 type_id="aws::AWS::RDS::DBInstance"))
    enrich(plan, "aws", "-")
    feedback_loop.apply_to_plan(plan, "aws", "-")
    assert not [u for u in plan.unresolved if "out-of-scope" in u], plan.unresolved


def test_it_does_not_push_our_bugs_onto_the_user() -> None:
    """**되묻기는 답하면 닫히는 것만.** 지금은 비어 있는 것이 정상이다.

    대조 결과 전부가 우리 코드나 하류 스키마의 몫이라, 사용자에게 물어도 안
    닫힌다. 그 사실 자체가 측정 결과고, `_CLOSED_BY`가 그렇게 선언한다.
    """
    from app.core.plan_enrich import enrich

    plan = _plan(
        PlanNode("k8s", "k8s", "compute", "inferred", host="Kubernetes node"),
        PlanNode("sub", "sub", "shared", "kb", type_id="aws::AWS::EC2::Subnet"))
    enrich(plan, "aws", "-")
    assert feedback_loop.questions(plan, "aws", "-") == ()
    # 늘리려면 "그 값을 받으면 정말 닫히는가"를 재고 나서다.
    assert set(feedback_loop._CLOSED_BY) == {
        plan_crosscheck.VIOLATED_RULE, plan_crosscheck.MISSING_REQUIRED,
        plan_crosscheck.DOUBLE_CREATE, plan_crosscheck.REDUNDANT_NODE,
        plan_crosscheck.UNCHECKED_RULE, plan_crosscheck.ABSENT_ORDER,
        plan_crosscheck.ABSENT_WARNING, plan_crosscheck.ABSENT_WAIT,
        plan_crosscheck.WEAK_READING, plan_crosscheck.OUT_OF_VOCABULARY,
        plan_crosscheck.OUT_OF_SCOPE}


# --- 고리가 잡은 결함과 그 수리 (2026-08-01) ------------------------------------

def test_the_generator_now_follows_the_measurement_over_the_bundle() -> None:
    """**고리가 잡은 결함의 수리.** 번들에 없어도 실측이 필수면 세운다.

    `core::k8sCluster`의 기본 번들은 멀티클라우드 데모라 클러스터·노드그룹뿐이고
    네트워크·서브넷이 없다. 번들은 "도구가 함께 만들어 준다"는 편의 정보이고
    **실측이 권위**다.
    """
    plan = compose(_design("aws", kubernetes=True))
    shared = {n.id for n in plan.nodes if n.role == "shared"}
    assert {"vnet", "subnet1", "subnet2"} <= shared, shared


def test_the_measured_count_becomes_that_many_nodes() -> None:
    """**개수도 실측이다.** 컴퓨트의 `×?`(개수 미정)와 다른 종류다 —
    서로 다른 AZ의 서브넷은 복제가 아니라 별개 자원이다."""
    plan = compose(_design("aws", kubernetes=True))
    subnets = [n for n in plan.nodes if (n.type_id or "").endswith("EC2::Subnet")]
    assert len(subnets) == 2, [n.id for n in subnets]
    assert any("availability zone" in note.text
               for node in subnets for note in node.notes)


def test_the_repair_closed_the_violation() -> None:
    """**고리가 값을 냈다는 증거** — 잡았고, 고쳤고, 0이 됐다."""
    plan = compose(_design("aws", kubernetes=True))
    violations = [u for u in plan.unresolved if "violated-rule" in u]
    assert not violations, violations


def test_what_cannot_be_drawn_is_said_not_silently_skipped() -> None:
    """`iamRole`은 벤더 타입이 없어 못 그린다 — **그 사실이 미결로 남는다.**

    이름을 찍어 상자를 세우면 하류가 만들 수 없는 것을 만들라고 하는 셈이고,
    그냥 빼면 실측이 필수라고 한 것이 사라진다.
    """
    plan = compose(_design("aws", kubernetes=True))
    assert any("iamRole is required" in u for u in plan.unresolved), plan.unresolved


def test_the_core_id_map_comes_from_the_graph_not_from_a_guess() -> None:
    """`core::<우리 이름>`으로 찍으면 조용히 안 맞는다 — 실제로 그렇게 물렸다.

    우리 `network`가 저쪽 `core::vNet`이고 `firewall`이 `core::securityGroup`이다.
    """
    mapping = plan_crosscheck.core_ids()
    assert mapping["network"] == "core::vNet"
    assert mapping["firewall"] == "core::securityGroup"
    assert mapping["loadBalancer"] == "core::nlb"
    assert "iamRole" not in mapping   # 그래프에 없다 — 그래서 못 그린다


@pytest.mark.parametrize("csp", ["azure", "gcp"])
def test_other_csps_add_nothing_because_their_measurement_differs(csp: str) -> None:
    """azure·gcp의 k8s 폐포는 서브넷을 요구하지 않는다 — **양상 반전 실측**이다.

    aws에만 노드가 느는 것이 맞다. 셋을 같게 만들면 실측을 지우는 것이다.
    """
    plan = compose(_design(csp, kubernetes=True))
    subnets = [n for n in plan.nodes if "ubnet" in (n.type_id or "")]
    assert not subnets, [n.id for n in subnets]
    assert not [u for u in plan.unresolved if "violated-rule" in u]
