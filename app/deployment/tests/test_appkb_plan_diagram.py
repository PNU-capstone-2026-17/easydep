"""배포 계획 모델 · PlantUML 생성 · 다이어그램 주장 대조 (KB 없이 도는 부분).

다이어그램은 이 저장소가 처음 만드는 **생성물**이다. 지금까지는 원본이 말한 것을
옮겼지만 여기서는 새 그림을 만든다 — 어느 선이 설계도에서 왔고 어느 선이 우리
추론인지가 사라지면, 막아 온 실패가 그림의 형태로 돌아온다.
"""

from __future__ import annotations

import pytest

from app.deployment.appkb.diagram import parse_back, render
from app.deployment.appkb.plan import (
    ORIGIN_DESIGN,
    ORIGIN_DESIGNER,
    ORIGIN_INFERRED,
    ORIGIN_KB,
    DeploymentPlan,
    Note,
    PlanEdge,
    PlanNode,
    needs_hedge,
)
from app.deployment.appkb.verify import (
    unhedged_claims,
    verify_against_requirements,
    verify_diagram,
    verify_plan,
)
from app.deployment.tests._helpers import flat


def _plan() -> DeploymentPlan:
    plan = DeploymentPlan(name="데모")
    plan.nodes = [
        PlanNode("order-api", "OrderService", "compute", ORIGIN_INFERRED,
                 notes=(Note("OpenAPI가 있어 서비스로 봄", ORIGIN_INFERRED, "openapi"),)),
        PlanNode("order-api-db", "저장소", "managed", ORIGIN_INFERRED,
                 archetype="app::relationalDatabase",
                 type_id="aws::AWS::RDS::DBInstance",
                 notes=(Note("엔티티 소유", ORIGIN_INFERRED, "er"),)),
        PlanNode("pg-gateway", "PG사", "external", ORIGIN_DESIGN),
    ]
    plan.edges = [PlanEdge("order-api", "order-api-db", "읽기/쓰기", ORIGIN_INFERRED)]
    return plan


# --- 근거 규율 -----------------------------------------------------------------

def test_designer_claims_are_hedged_too() -> None:
    """설계자가 "이건 VM"이라 적었어도 **주장이지 검증된 사실이 아니다** —
    상류로 배포 결정을 옮기면 환각이 검사 없는 곳으로 이사할 뿐이다."""
    assert needs_hedge(ORIGIN_DESIGNER)
    assert needs_hedge(ORIGIN_INFERRED)
    assert not needs_hedge(ORIGIN_DESIGN)
    assert not needs_hedge(ORIGIN_KB)


def test_unknown_origin_is_rejected_not_defaulted() -> None:
    """모르는 근거를 조용히 받으면 hedge 계산에서 빠진다."""
    with pytest.raises(ValueError):
        PlanNode("x", "X", "compute", "somewhere")
    with pytest.raises(ValueError):
        Note("t", "somewhere")


def test_type_and_candidates_are_exclusive() -> None:
    """후보가 여럿인데 하나를 정해 둔 상태는 **임의 선택을 감춘 모양**이다."""
    with pytest.raises(ValueError):
        PlanNode("x", "X", "managed", ORIGIN_KB, type_id="a::T", candidates=("a::T", "a::U"))


def test_hedged_node_must_say_why() -> None:
    plan = _plan()
    plan.nodes.append(PlanNode("naked", "근거 없는 추론", "compute", ORIGIN_INFERRED))
    assert "naked" in unhedged_claims(plan)


# --- 다이어그램 왕복 -----------------------------------------------------------

def test_diagram_round_trips() -> None:
    plan = _plan()
    uml = render(plan)
    assert verify_diagram(plan, uml) == []


def test_hyphen_ids_survive_plantuml() -> None:
    """**실측에서 났던 결함.** 계약이 id에 하이픈을 허용하는데 PlantUML에서 `-`는
    화살표 문자다 — 맨 별칭으로 쓰면 `order-api`가 `order`로 조용히 쪼개졌다."""
    aliases, edges = parse_back(render(_plan()))
    assert "order-api" in aliases and "order-api-db" in aliases
    assert ("order-api", "order-api-db") in edges


def test_sync_arrows_are_not_dropped() -> None:
    """**두 번째 실측 결함.** 정규식이 `-->?`라 `--`가 필수였고, 동기 화살표 `->`가
    통째로 빠졌다 — 5개 선 중 4개가 조용히 사라졌다."""
    plan = _plan()
    plan.edges.append(PlanEdge("order-api", "pg-gateway", "결제", ORIGIN_DESIGN))
    plan.edges.append(PlanEdge("order-api", "order-api-db", "이벤트",
                               ORIGIN_DESIGN, async_=True))
    _, edges = parse_back(render(plan))
    assert ("order-api", "pg-gateway") in edges  # 동기
    assert ("order-api", "order-api-db") in edges  # 비동기


def test_containment_replaces_the_edge_explosion() -> None:
    """컴퓨트마다 공유 자원으로 선을 그으면 컴포넌트 5개짜리 앱에 선이 20개 는다
    (실측: 2개에 이미 15개). 배포 다이어그램은 그걸 **중첩**으로 표현한다."""
    plan = _plan()
    plan.nodes.append(PlanNode("vnet", "VPC", "shared", ORIGIN_KB,
                               type_id="aws::AWS::EC2::VPC"))
    plan.nodes.append(PlanNode("subnet", "서브넷", "shared", ORIGIN_KB,
                               type_id="aws::AWS::EC2::Subnet"))
    uml = render(plan)
    assert "{" in uml and "}" in uml
    # 중첩이 들어와도 되파싱은 여전히 전부 읽어야 한다
    assert verify_diagram(plan, uml) == []
    aliases, _ = parse_back(uml)
    assert {"vnet", "subnet", "order-api"} <= aliases


def test_diagram_carries_the_hedge_marks() -> None:
    """그림은 잘려 돌아다닌다 — 범례에만 적어 두면 부족하다."""
    assert "<<inferred>>" in render(_plan())


def test_missing_node_in_diagram_is_caught() -> None:
    """조립하다 노드를 흘리면 그림이 조용히 작아진다 — 눈으로는 안 걸린다."""
    plan = _plan()
    uml = render(plan).replace(
        'database "저장소\\naws::AWS::RDS::DBInstance" as "order-api-db" <<inferred>>\n',
        "",
    )
    assert any("nodes in the plan but not in the diagram" in flat(p)
               for p in verify_diagram(plan, uml))


def test_invented_node_in_diagram_is_caught() -> None:
    """계획에 없는 상자가 그림에 생기는 것은 **답변에서 없는 값을 만드는 것의 그림판**이다."""
    uml = render(_plan()).replace("@enduml", 'node "유령" as "ghost"\n@enduml')
    assert any("nodes in the diagram but not in the plan" in flat(p)
               for p in verify_diagram(_plan(), uml))


# --- 계획 정합성 ---------------------------------------------------------------

def test_edge_to_nowhere_is_caught() -> None:
    plan = _plan()
    plan.edges.append(PlanEdge("order-api", "no-such-node", "", ORIGIN_DESIGN))
    assert any("a connection points at a missing node" in flat(p)
               for p in verify_plan(plan))


def test_node_id_must_match_resource_name_rules() -> None:
    """계약이 막은 문자가 조립 중에 들어오면 우리가 망가뜨린 것이다."""
    plan = _plan()
    plan.nodes.append(PlanNode("Bad_Id", "X", "compute", ORIGIN_KB))
    assert any("node id breaks the resource name rule" in flat(p)
               for p in verify_plan(plan))


def test_empty_managed_node_must_be_reported_as_unresolved() -> None:
    """"관리형 서비스가 필요하다"까지만 알고 무엇인지 모르는 상태를 조용히 두면
    그림에 빈 상자가 남는다."""
    plan = _plan()
    plan.nodes.append(PlanNode("mystery", "무언가", "managed", ORIGIN_INFERRED,
                               notes=(Note("필요해 보임", ORIGIN_INFERRED),)))
    assert any("it has no type, no candidates" in flat(p) for p in verify_plan(plan))
    plan.unresolved.append("mystery: 대응 타입을 찾지 못했습니다")
    assert verify_plan(plan) == []


def test_plan_serialises_with_origins_intact() -> None:
    data = _plan().to_dict()
    assert data["nodes"][0]["origin"] == ORIGIN_INFERRED
    assert data["nodes"][0]["notes"][0]["origin"] == ORIGIN_INFERRED
    assert data["edges"][0]["origin"] == ORIGIN_INFERRED


# --- 요구사항 대조 (research.md 목표 1의 "부합 측정") ---------------------------
#
# 판정문 목록이지 문제 목록이 아니다 — "잴 수 없다"도 판정이고, 침묵이면 부분
# 답이 완전한 답처럼 읽힌다.

_HOURS = 730.0


def _priced_plan(hourly: float) -> DeploymentPlan:
    from dataclasses import replace

    plan = _plan()
    plan.nodes[0] = replace(plan.nodes[0], hourly_usd=hourly)
    return plan


def test_budget_overrun_is_confirmed_from_the_floor_alone() -> None:
    """**비대칭의 한쪽.** 값이 붙은 부분만의 월합은 실제 청구의 하한이다 —
    하한이 이미 예산을 넘으면 초과는 확정이다."""
    lines = verify_against_requirements(
        _priced_plan(1.0), {"monthlyBudgetUSD": 500}, _HOURS
    )
    assert any("**over, confirmed**" in flat(ln) for ln in lines)


def test_under_floor_is_never_called_compliant() -> None:
    """**비대칭의 반대쪽.** 하한이 예산 아래인 것은 부합의 근거가 못 된다 —
    미가격 구성원을 0으로 치면 서브넷 256 답과 같은 실패다."""
    lines = verify_against_requirements(
        _priced_plan(1.0), {"monthlyBudgetUSD": 1000}, _HOURS
    )
    budget_line = flat(next(ln for ln in lines if ln.startswith("Budget")))
    assert "**cannot be asserted to fit**" in budget_line
    assert "order-api-db" in budget_line  # 미가격 구성원의 이름이 보인다
    assert "fit" not in budget_line.replace("**cannot be asserted to fit**", "")


def test_no_priced_nodes_means_no_verdict_not_zero() -> None:
    lines = verify_against_requirements(_plan(), {"monthlyBudgetUSD": 500}, _HOURS)
    assert any("no verdict" in flat(ln) for ln in lines)


def test_no_budget_is_said_not_skipped() -> None:
    lines = verify_against_requirements(_plan(), {}, _HOURS)
    assert any("Budget: no yardstick" in flat(ln) for ln in lines)


def test_scale_gets_an_explicit_cannot_judge() -> None:
    """규모는 필수로 받지만 **스펙 충분성 판정은 KB 밖이다** — 그 사실을 말하는
    것까지가 측정이다. 말하지 않으면 계획이 규모를 보증한 것처럼 읽힌다."""
    lines = verify_against_requirements(
        _plan(), {"expectedConcurrentUsers": 200}, _HOURS
    )
    assert any("cannot judge whether the spec is sufficient" in flat(ln) and "200" in ln
               for ln in lines)
    rps = verify_against_requirements(
        _plan(), {"approxRequestsPerSecond": 30}, _HOURS
    )
    assert any("30" in ln and "RPS" in ln for ln in rps)


def test_multizone_unreflected_is_flagged() -> None:
    plan = _plan()
    plan.nodes.append(PlanNode("vnet", "VPC", "shared", ORIGIN_KB,
                               type_id="aws::AWS::EC2::VPC"))
    lines = verify_against_requirements(plan, {"multiZone": True}, _HOURS)
    assert any("multiZone: **not reflected**" in flat(ln) for ln in lines)


def test_multizone_reflected_when_subnet_carries_the_note() -> None:
    plan = _plan()
    plan.nodes.append(PlanNode(
        "subnet", "서브넷", "shared", ORIGIN_KB, type_id="aws::AWS::EC2::Subnet",
        notes=(Note("The requirement is multiZone, so the subnets have to be spread "
                    "across several availability zones", ORIGIN_DESIGN, "requirements"),),
    ))
    lines = verify_against_requirements(plan, {"multiZone": True}, _HOURS)
    assert any("multiZone: reflected" in flat(ln) for ln in lines)


def test_multizone_on_serverless_plan_is_not_a_false_violation() -> None:
    """전부 서버리스면 VM 네트워크가 없다 — 그걸 '미반영'이라 벌하면 없는 것을
    그리라는 요구가 된다."""
    lines = verify_against_requirements(_plan(), {"multiZone": True}, _HOURS)
    assert any("multiZone: nothing to check" in flat(ln) for ln in lines)
    assert not any("**not reflected**" in flat(ln) for ln in lines)


def _burst_plan() -> DeploymentPlan:
    plan = _plan()
    plan.nodes[0] = __import__("dataclasses").replace(
        plan.nodes[0],
        hourly_usd=0.05,
        notes=plan.nodes[0].notes + (
            # perfkb 데이터셋(`_NOTE_AWS_BURST`)의 실제 문구 그대로. 판정이
            # 이 문자열의 `burst`에 걸리므로 픽스처가 원문에서 멀어지면
            # **검사만 통과하고 실물은 못 잡는** 상태가 된다.
            Note("Burstable instance — performance drops to baseline once the "
                 "CPU credits run out.", ORIGIN_KB, "perfkb"),
        ),
    )
    return plan


def test_steady_traffic_on_burst_spec_is_a_conflict() -> None:
    """⑥-A의 핵심 소비자 — 버스트 **경고**가 이 앱에 **문제**인지를 처음으로
    판정한다. 지금까지는 경고만 하고 판단할 입력이 없었다."""
    lines = verify_against_requirements(
        _burst_plan(), {"trafficPattern": "steady"}, _HOURS
    )
    conflict = flat(next(ln for ln in lines if ln.startswith("trafficPattern")))
    assert "**conflict**" in conflict and "order-api" in conflict


def test_spiky_traffic_on_burst_spec_is_not_flagged() -> None:
    lines = verify_against_requirements(
        _burst_plan(), {"trafficPattern": "spiky"}, _HOURS
    )
    assert any("no known conflict with the burst instances" in flat(ln) for ln in lines)


def test_no_burst_note_is_only_trusted_when_values_joined() -> None:
    """경고의 **부재**를 '버스트 아님'으로 읽는 건 침묵 오독이다 — 성능 조인이
    실제로 돈 계획(값이 붙은 노드 존재)에서만 그렇게 말한다."""
    lines = verify_against_requirements(
        _priced_plan(0.05), {"trafficPattern": "steady"}, _HOURS
    )
    assert any("no burst warning on the plan's specs" in flat(ln) for ln in lines)
    unjoined = verify_against_requirements(
        _plan(), {"trafficPattern": "steady"}, _HOURS
    )
    assert any("no verdict" in flat(ln)
               for ln in unjoined if ln.startswith("trafficPattern"))


def test_stateful_app_with_serverless_is_a_flagged_inference() -> None:
    """상충 판정이지만 KB 사실이 아니라 **우리 추론**이다 — 그 표시가 판정문에
    붙어 있어야 한다."""
    plan = _plan()
    plan.nodes.append(PlanNode(
        "worker", "Worker", "managed", ORIGIN_DESIGNER,
        archetype="app::serverlessFunction",
        type_id="aws::AWS::Lambda::Function",
        notes=(Note("설계자가 지정", ORIGIN_DESIGNER, "deployHint"),),
    ))
    lines = verify_against_requirements(plan, {"stateless": False}, _HOURS)
    conflict = flat(next(ln for ln in lines if "serverless" in ln))
    assert "**possible conflict (we inferred)**" in conflict and "worker" in conflict


def test_serverless_without_statefulness_claim_is_said() -> None:
    """침묵을 적합으로 읽지 않는다 — stateless를 못 받았으면 미확인을 명시."""
    plan = _plan()
    plan.nodes.append(PlanNode(
        "worker", "Worker", "managed", ORIGIN_DESIGNER,
        archetype="app::serverlessFunction", type_id="aws::AWS::Lambda::Function",
        notes=(Note("설계자가 지정", ORIGIN_DESIGNER, "deployHint"),),
    ))
    lines = verify_against_requirements(plan, {}, _HOURS)
    assert any("Statefulness unconfirmed" in flat(ln) for ln in lines)


def test_foreign_provider_type_in_plan_is_caught() -> None:
    plan = _plan()
    plan.nodes.append(PlanNode("cache", "캐시", "managed", ORIGIN_KB,
                               type_id="gcp::redis.googleapis.com/Instance"))
    lines = verify_against_requirements(plan, {"provider": "aws"}, _HOURS)
    assert any("**mismatch**" in flat(ln) and "cache" in ln for ln in lines)


def test_matching_provider_is_stated_positively() -> None:
    lines = verify_against_requirements(_plan(), {"provider": "aws"}, _HOURS)
    assert any("every vendor type in the plan matches" in flat(ln) for ln in lines)
    assert not any("**mismatch**" in flat(ln) for ln in lines)
