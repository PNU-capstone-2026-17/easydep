"""배포 계획 모델 · PlantUML 생성 · 다이어그램 주장 대조 (KB 없이 도는 부분).

다이어그램은 이 저장소가 처음 만드는 **생성물**이다. 지금까지는 원본이 말한 것을
옮겼지만 여기서는 새 그림을 만든다 — 어느 선이 설계도에서 왔고 어느 선이 우리
추론인지가 사라지면, 막아 온 실패가 그림의 형태로 돌아온다.
"""

from __future__ import annotations

import pytest

from appkb.diagram import parse_back, render
from appkb.plan import (
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
from appkb.verify import unhedged_claims, verify_diagram, verify_plan


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
    assert "<<추론>>" in render(_plan())


def test_missing_node_in_diagram_is_caught() -> None:
    """조립하다 노드를 흘리면 그림이 조용히 작아진다 — 눈으로는 안 걸린다."""
    plan = _plan()
    uml = render(plan).replace('database "저장소\\naws::AWS::RDS::DBInstance" as "order-api-db" <<추론>>\n', "")
    assert any("그림에 없는 노드" in p for p in verify_diagram(plan, uml))


def test_invented_node_in_diagram_is_caught() -> None:
    """계획에 없는 상자가 그림에 생기는 것은 **답변에서 없는 값을 만드는 것의 그림판**이다."""
    uml = render(_plan()).replace("@enduml", 'node "유령" as "ghost"\n@enduml')
    assert any("계획에 없는데 그림에 있는" in p for p in verify_diagram(_plan(), uml))


# --- 계획 정합성 ---------------------------------------------------------------

def test_edge_to_nowhere_is_caught() -> None:
    plan = _plan()
    plan.edges.append(PlanEdge("order-api", "no-such-node", "", ORIGIN_DESIGN))
    assert any("없는 노드를 가리킨다" in p for p in verify_plan(plan))


def test_node_id_must_match_resource_name_rules() -> None:
    """계약이 막은 문자가 조립 중에 들어오면 우리가 망가뜨린 것이다."""
    plan = _plan()
    plan.nodes.append(PlanNode("Bad_Id", "X", "compute", ORIGIN_KB))
    assert any("리소스 이름 규칙" in p for p in verify_plan(plan))


def test_empty_managed_node_must_be_reported_as_unresolved() -> None:
    """"관리형 서비스가 필요하다"까지만 알고 무엇인지 모르는 상태를 조용히 두면
    그림에 빈 상자가 남는다."""
    plan = _plan()
    plan.nodes.append(PlanNode("mystery", "무언가", "managed", ORIGIN_INFERRED,
                               notes=(Note("필요해 보임", ORIGIN_INFERRED),)))
    assert any("타입도 후보도 없고" in p for p in verify_plan(plan))
    plan.unresolved.append("mystery: 대응 타입을 찾지 못했습니다")
    assert verify_plan(plan) == []


def test_plan_serialises_with_origins_intact() -> None:
    data = _plan().to_dict()
    assert data["nodes"][0]["origin"] == ORIGIN_INFERRED
    assert data["nodes"][0]["notes"][0]["origin"] == ORIGIN_INFERRED
    assert data["edges"][0]["origin"] == ORIGIN_INFERRED
