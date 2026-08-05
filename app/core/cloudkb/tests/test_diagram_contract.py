"""그림의 정보 계약과 **하류 전수 대조** — 두 표가 거짓이 되지 않게.

`docs/kb-and-contract-plan-2026-07-29.md` §W4. 두 가지를 지킨다.

1. `diagram_contract.REQUIRED_FACTS`의 사실이 **실제로 그림에 나타나는가**. 규칙이
   코드에 흩어져 있으면 하나가 빠져도 아무 검사가 실패하지 않는다 — 큐가 원통으로
   그려지던 것도 사람이 보고 나서야 발견됐다.
2. `downstream.INTENT_FIELDS`가 하류 요구에 대해 **전수·사유 있는 판정**인가.
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.appkb.diagram import parse_back, render
from app.core.cloudkb.appkb.diagram_contract import BY_KEY, REQUIRED_FACTS
from app.core.cloudkb.appkb.downstream import (
    HAVE,
    INTENT_FIELDS,
    PARTIAL,
    STATUSES,
    blocking_decisions,
    coverage,
)
from app.core.cloudkb.appkb.plan import (
    ORIGIN_DESIGN,
    ORIGIN_INFERRED,
    ORIGIN_KB,
    DeploymentPlan,
    PlanEdge,
    PlanNode,
)


def _rich_plan() -> DeploymentPlan:
    """계약이 요구하는 사실을 **전부** 담은 계획. 하나라도 빠지면 검사가 무의미해진다."""
    plan = DeploymentPlan(name="계약 시연")
    plan.nodes = [
        PlanNode("end-user", "구매자", "actor", ORIGIN_DESIGN),
        PlanNode("lb", "로드밸런서", "ingress", ORIGIN_KB,
                 type_id="aws::AWS::ElasticLoadBalancingV2::LoadBalancer"),
        PlanNode("order-api", "OrderService", "compute", ORIGIN_INFERRED,
                 host="VM · t3a.medium", replicas=None, hourly_usd=0.02),
        PlanNode("worker", "OrderWorker", "compute", ORIGIN_INFERRED,
                 host="VM", replicas=2, hourly_usd=0.02),
        PlanNode("queue", "주문 큐", "managed", ORIGIN_INFERRED,
                 archetype="app::messageQueue", type_id="aws::AWS::SQS::Queue"),
        PlanNode("secrets", "비밀 저장소", "managed", ORIGIN_KB,
                 archetype="app::secretStore",
                 candidates=("aws::AWS::SecretsManager::Secret",)),
        PlanNode("pg", "PG사", "external", ORIGIN_DESIGN),
    ]
    plan.edges = [
        PlanEdge("end-user", "lb", "request", ORIGIN_DESIGN),
        PlanEdge("lb", "order-api", "forward", ORIGIN_KB),
        PlanEdge("order-api", "queue", "publish", ORIGIN_DESIGN, async_=True),
        PlanEdge("order-api", "pg", "결제 승인", ORIGIN_DESIGN),
    ]
    return plan


# --- 1. 그림의 정보 계약 -------------------------------------------------------

def test_identity_round_trips() -> None:
    plan = _rich_plan()
    aliases, _ = parse_back(render(plan))
    assert {n.id for n in plan.nodes} <= aliases, BY_KEY["identity"].why


def test_edges_round_trip_including_async() -> None:
    plan = _rich_plan()
    _, edges = parse_back(render(plan))
    for edge in plan.edges:
        assert (edge.from_id, edge.to_id) in edges, BY_KEY["edges"].why


def test_archetype_decides_the_shape() -> None:
    """큐는 큐로, 시크릿은 폴더로. **관리형을 전부 원통으로 그리던 결함의 회귀 검사.**"""
    uml = render(_rich_plan())
    assert 'queue "주문 큐' in uml, BY_KEY["archetype-shape"].why
    assert 'folder "비밀 저장소' in uml
    assert 'database "주문 큐' not in uml


def test_host_nesting_is_drawn() -> None:
    uml = render(_rich_plan())
    assert '"order-api@host"' in uml and "{" in uml, BY_KEY["host-nesting"].why


def test_undecided_replicas_are_visible() -> None:
    uml = render(_rich_plan())
    assert "×?" in uml, BY_KEY["undecided-replicas"].why
    assert "×2" in uml


def test_hedge_marks_survive_in_the_picture() -> None:
    assert "<<" in render(_rich_plan()), BY_KEY["hedge-origin"].why


def test_boundary_shapes_separate_what_we_do_not_build() -> None:
    uml = render(_rich_plan())
    assert 'actor "구매자' in uml, BY_KEY["boundary"].why
    assert 'cloud "PG사' in uml


def test_type_or_candidates_is_on_the_label() -> None:
    uml = render(_rich_plan())
    assert "aws::AWS::SQS::Queue" in uml, BY_KEY["type-or-candidates"].why
    assert "1 candidates" in uml  # 후보만 있는 노드


def test_every_required_fact_has_a_probe() -> None:
    """확인할 수 없는 요구는 요구가 아니라 바람이다."""
    assert REQUIRED_FACTS
    for fact in REQUIRED_FACTS:
        assert fact.why.strip() and fact.grammar.strip() and fact.probe.strip()


# --- 2. 하류 전수 대조 ---------------------------------------------------------

def test_intent_fields_are_unique_and_reasoned() -> None:
    names = [m.field for m in INTENT_FIELDS]
    assert len(names) == len(set(names)), "같은 필드를 두 번 판정하고 있다"
    for mapping in INTENT_FIELDS:
        assert mapping.status in STATUSES
        assert len(mapping.why) > 20, f"{mapping.field}: 사유가 너무 짧다"


def test_what_we_can_give_names_where_it_comes_from() -> None:
    for mapping in INTENT_FIELDS:
        if mapping.status in (HAVE, PARTIAL):
            assert mapping.source, mapping.field


def test_coverage_adds_up() -> None:
    assert sum(coverage().values()) == len(INTENT_FIELDS)
    # 하나도 못 주면 대조표를 만든 의미가 없고, 전부 준다면 대조를 안 한 것이다.
    assert coverage()[HAVE] >= 1
    assert coverage()["missing"] >= 1


def test_network_policy_is_something_we_actually_have() -> None:
    """엣지 목록이 곧 허용 흐름이다 — 하류가 요구하는 것 중 우리가 가장 잘 답하는 칸."""
    row = next(m for m in INTENT_FIELDS if m.field == "capabilities.networkPolicy")
    assert row.status == HAVE and "edges" in row.source


# --- 3. 미정의 전파 ------------------------------------------------------------

def test_undecided_things_are_reported_as_blockers() -> None:
    """빈칸으로 채우지 않는다 — **무엇을 정해야 매니페스트가 성립하는지**를 말한다."""
    blockers = blocking_decisions(_rich_plan())
    joined = " ".join(blockers)
    assert "order-api" in joined and "대수가 미정" in joined
    assert "secrets" in joined and "후보" in joined


def test_settled_plan_has_no_blockers() -> None:
    plan = _rich_plan()
    plan.nodes = [
        n if n.role in ("actor", "external") else
        type(n)(**{**n.__dict__,
                   "replicas": n.replicas or 1,
                   "type_id": n.type_id or "aws::AWS::SecretsManager::Secret",
                   "candidates": (),
                   "hourly_usd": n.hourly_usd or 0.01})
        for n in plan.nodes
    ]
    assert blocking_decisions(plan) == []


@pytest.mark.parametrize("status", STATUSES)
def test_every_status_is_used_or_deliberately_not(status: str) -> None:
    """쓰이지 않는 판정 값이 있으면 표가 실제보다 정교해 보인다."""
    assert any(m.status == status for m in INTENT_FIELDS), (
        f"{status} 판정이 하나도 없다 — 값을 지우거나 실제로 쓰세요"
    )
