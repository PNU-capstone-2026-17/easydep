"""easydep 최종본 → 입력 계약 어댑터.

고정하는 것 셋:
1. **컴포넌트를 합성하지 않는다** — easydep 설계에는 배포 단위 분해가 없으므로
   컴포넌트는 정확히 하나다. 스테레오타입에서 경계를 지어내면 설계가 그리지
   않은 경계를 우리가 만드는 것이다.
2. **못 읽으면 못 읽었다고 한다** — 시퀀스·ERD는 문법 제약 없는 LLM 출력이라
   파서는 아는 모양만 읽고, 못 읽은 것과 추정한 것을 목록으로 낸다.
3. **어댑터의 출력이 계약을 통과한다** — 어댑터가 계약을 어기는 설계를 만들면
   composer가 계약 위반으로 거절하는데, 그건 어댑터의 결함이다.

클래스 픽스처는 easydep `plantuml_class_diagram.py` 렌더러의 출력 모양 그대로다
(그 렌더러가 곧 문법 명세다). 시퀀스·ERD 픽스처는 흔한 LLM 출력 모양이다.
"""

from __future__ import annotations

from appkb.contract import validate_design
from appkb.easydep import (
    COMPONENT_ID,
    design_from_easydep,
    parse_classes,
    parse_er_entities,
    parse_sequence,
)


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())

#: easydep 결정론 렌더러의 출력 모양 그대로.
_CLASS_PUML = """@startuml
allowmixing
!theme plain
skinparam classAttributeIconSize 0

class OrderController <<Controller>> {
  - orderService
  + createOrder()
}
note top of OrderController : 주문 API 진입점

class OrderService <<Service>> {
  + placeOrder()
}

class OrderRepository <<Repository>> {
  + save()
}

OrderController --> OrderService
OrderService ..> OrderRepository
@enduml"""

_SEQUENCE_PUML = """@startuml
actor "구매자" as buyer
participant OrderController
participant OrderService
database OrderDB
participant "PG Gateway" as pg

buyer -> OrderController : POST /orders
OrderController -> OrderService : placeOrder()
OrderService -> pg : 결제 승인 요청
OrderService -> OrderDB : INSERT
OrderService ->> OrderService : 주문 완료 이벤트
@enduml"""

_ERD_PUML = """@startuml
entity Order {
  * id : uuid
  --
  status : varchar
}
entity "Order Item" as order_item {
  * id : uuid
}
Order ||--o{ order_item
@enduml"""

_API_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Order API", "version": "1.0.0"},
    "paths": {"/orders": {"post": {"responses": {"201": {"description": "ok"}}}}},
}

_RESOURCE_SPEC = {
    "schemaVersion": "1",
    "provider": "aws",
    "region": "ap-northeast-2",
    "regionAsWritten": "서울",
    "monthlyBudgetUSD": 300,
    "expectedConcurrentUsers": 500,
    "multiZone": True,
}


def _adapt(**overrides):
    kwargs = dict(
        api_spec=_API_SPEC, class_puml=_CLASS_PUML,
        sequence_puml=_SEQUENCE_PUML, erd_puml=_ERD_PUML,
        resource_spec=_RESOURCE_SPEC,
    )
    kwargs.update(overrides)
    return design_from_easydep("주문 서비스", **kwargs)


# --- 파서 (아는 모양만 읽는다) --------------------------------------------------

def test_renderer_dialect_classes_parse_with_stereotypes() -> None:
    parsed = parse_classes(_CLASS_PUML)
    assert ("OrderController", "Controller") in parsed
    assert ("OrderRepository", "Repository") in parsed


def test_er_entities_parse_both_quoted_and_bare() -> None:
    assert parse_er_entities(_ERD_PUML) == ["Order", "Order Item"]


def test_sequence_async_comes_from_the_arrowhead() -> None:
    _, messages = parse_sequence(_SEQUENCE_PUML)
    async_flags = {(m[0], m[1]): m[3] for m in messages}
    assert async_flags[("buyer", "OrderController")] is False
    assert async_flags[("OrderService", "OrderService")] is True


def test_dashed_only_sequence_falls_back_instead_of_vanishing() -> None:
    """점선은 응답이라 버리지만, 전부 점선이면 그 판단이 메시지를 0으로 만든다 —
    그때는 점선을 메시지로 받는다."""
    _, messages = parse_sequence("@startuml\nA --> B : x\n@enduml")
    assert messages == [("A", "B", "x", False)]


# --- 단일 컴포넌트 --------------------------------------------------------------

def test_exactly_one_component_never_synthesized() -> None:
    design, _ = _adapt()
    assert [c["id"] for c in design["components"]] == [COMPONENT_ID]
    assert ("the source carries no signal for splitting it into deployment units"
            in flat(design["components"][0]["summary"]))


def test_app_classes_collapse_to_the_single_component() -> None:
    design, _ = _adapt()
    seq = next(a for a in design["artifacts"] if a["kind"] == "sequence")
    component_participants = [p for p in seq["participants"] if p.get("componentId")]
    assert len(component_participants) == 1
    assert component_participants[0]["componentId"] == COMPONENT_ID


def test_intra_app_sync_calls_are_not_deployment_messages() -> None:
    """Controller→Service 같은 한 배포 단위 안의 동기 호출은 배포 선이 아니다."""
    design, _ = _adapt()
    seq = next(a for a in design["artifacts"] if a["kind"] == "sequence")
    assert not any(
        m["from"] == COMPONENT_ID and m["to"] == COMPONENT_ID and not m["async"]
        for m in seq["messages"]
    )


def test_intra_app_async_survives_as_the_queue_signal() -> None:
    """내부 비동기(이벤트)는 큐 인프라의 신호라 버리면 안 된다."""
    design, _ = _adapt()
    seq = next(a for a in design["artifacts"] if a["kind"] == "sequence")
    assert any(m["async"] for m in seq["messages"])


def test_store_participants_are_skipped_and_said() -> None:
    """database 참가자를 그대로 옮기면 ER이 세운 저장소와 같은 것이 다른 두
    상자가 된다 — 거르되, 걸렀다는 사실을 말한다."""
    design, skipped = _adapt()
    seq = next(a for a in design["artifacts"] if a["kind"] == "sequence")
    ids = {p["id"] for p in seq["participants"]}
    assert not any("db" in i.lower() for i in ids)
    assert any("OrderDB" in flat(s) and "(heuristic)" in flat(s) for s in skipped)


def test_unknown_participant_becomes_an_external() -> None:
    design, _ = _adapt()
    assert any(e["name"] == "PG Gateway" for e in design.get("externals", []))
    assert all(e["id"] == "pg-gateway" for e in design["externals"])


def test_er_entities_are_owned_by_the_single_component() -> None:
    design, _ = _adapt()
    er = next(a for a in design["artifacts"] if a["kind"] == "er")
    assert all(e["ownerComponentId"] == COMPONENT_ID for e in er["entities"])


# --- 못 읽으면 못 읽었다고 한다 -------------------------------------------------

def test_unreadable_erd_is_reported_not_guessed() -> None:
    design, skipped = _adapt(erd_puml="@startuml\ntable(orders){}\n@enduml")
    assert not any(a["kind"] == "er" for a in design["artifacts"])
    assert any("ERD" in flat(s) and "Could not read a single entity" in flat(s)
               for s in skipped)


def test_non_3x_openapi_is_reported_not_forced() -> None:
    design, skipped = _adapt(api_spec={"swagger": "2.0", "paths": {}})
    assert not any(a["kind"] == "openapi" for a in design["artifacts"])
    assert any("3.x" in s for s in skipped)


def test_arrow_inference_is_disclosed() -> None:
    """계약의 async 칸은 '표기가 아니라 의미'를 받으라고 적혀 있다 — 최종본이
    PlantUML뿐이라 표기에서 추정했다는 사실을 밝힌다."""
    _, skipped = _adapt()
    assert any("arrow notation" in flat(s) and "was guessed" in flat(s)
               for s in skipped)


def test_nothing_readable_says_the_contract_will_fail() -> None:
    design, skipped = design_from_easydep("빈 설계")
    assert design["artifacts"] == []
    assert any("does not pass the input contract" in flat(s) for s in skipped)


# --- RESOURCE_SPEC 투영 ---------------------------------------------------------

def test_resource_spec_projects_only_design_side_fields() -> None:
    design, _ = _adapt()
    req = design["requirements"]
    assert req["provider"] == "aws" and req["monthlyBudgetUSD"] == 300
    assert "schemaVersion" not in req and "regionAsWritten" not in req


# --- 계약 왕복 ------------------------------------------------------------------

def test_adapter_output_passes_the_design_contract() -> None:
    design, _ = _adapt()
    assert validate_design(design) == []


def test_easydep_entrypoint_carries_verdicts_and_adapter_notes() -> None:
    """easydep 배포 노드가 부를 진입점 — 부합 판정과 어댑터 고지가 답에 실린다."""
    from nim_agent.design_tools import deployment_answer_from_easydep

    text = deployment_answer_from_easydep(
        "주문 서비스", api_spec=_API_SPEC, class_puml=_CLASS_PUML,
        sequence_puml=_SEQUENCE_PUML, erd_puml=_ERD_PUML,
        resource_spec=_RESOURCE_SPEC, diagram=True,
    )
    assert "[Requirements comparison]" in text and "Budget" in text
    assert "[What the adapter could not read · had to guess]" in text
    assert "@startuml" in text


def test_easydep_entrypoint_reports_invalid_resource_spec() -> None:
    from nim_agent.design_tools import deployment_answer_from_easydep

    text = deployment_answer_from_easydep(
        "주문 서비스", api_spec=_API_SPEC, class_puml=_CLASS_PUML,
        resource_spec={"provider": "aws"}, diagram=False,
    )
    assert "[Constraint (RESOURCE_SPEC) check]" in text
    assert "monthlyBudgetUSD" in text and "no scale signal" in text


def test_puml_document_carries_evidence_as_comments() -> None:
    """easydep 저장은 스테이지당 단일 문서(PUML)다 — 근거·판정을 버리면 이 교체의
    요점이 사라지므로 PlantUML 주석으로 같은 문서에 싣는다(팀 결정 2026-07-24)."""
    from appkb.diagram import parse_back
    from nim_agent.design_tools import deployment_puml_from_easydep

    doc = deployment_puml_from_easydep(
        "주문 서비스", api_spec=_API_SPEC, class_puml=_CLASS_PUML,
        sequence_puml=_SEQUENCE_PUML, erd_puml=_ERD_PUML,
        resource_spec=_RESOURCE_SPEC,
    )
    assert doc.startswith("@startuml") and doc.rstrip().endswith("@enduml")
    comments = [ln for ln in doc.splitlines() if ln.startswith("'")]
    joined = "\n".join(comments)
    assert "[Requirements comparison]" in joined and "Budget" in joined
    assert "are not verified facts" in joined
    # 주석이 되파싱을 오염시키면 안 된다 — 주석 속 문장이 노드·선으로 읽히면
    # 그림 대조가 유령을 잡는다.
    with_comments = parse_back(doc)
    bare = parse_back(doc[: doc.index("' ────")] + "@enduml\n")
    assert with_comments == bare


def test_contract_failure_becomes_a_drawn_failure_not_an_empty_diagram() -> None:
    from nim_agent.design_tools import deployment_puml_from_easydep

    doc = deployment_puml_from_easydep("빈 설계")
    assert doc.startswith("@startuml")
    assert "did not pass the contract" in doc and "note" in doc


def test_adapter_output_composes_into_a_plan() -> None:
    """끝에서 끝까지: easydep 산출물 모양 → 계약 → 배포 계획. 저장소·큐·공개
    노출·값이 전부 나오고, 자체 검증을 통과한다."""
    from appkb.diagram import render
    from appkb.verify import verify_diagram, verify_plan
    from nim_agent.design_tools import compose

    design, _ = _adapt()
    plan = compose(design)
    assert plan.node("app") is not None                      # 단일 컴퓨트
    assert plan.node("app-db") is not None                   # ER → 저장소
    assert plan.node("message-queue") is not None            # 내부 비동기 → 큐
    assert plan.node("app").hourly_usd                       # 값 조인(aws)
    assert not any(e.from_id == e.to_id for e in plan.edges)  # 자기 선 없음
    assert verify_plan(plan) == []
    assert verify_diagram(plan, render(plan)) == []
