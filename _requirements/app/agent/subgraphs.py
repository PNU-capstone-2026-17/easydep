"""4단계 서브그래프 빌더(순수 스테이지).

각 단계를 독립 서브그래프로 컴파일한다. 상위 그래프의 노드명은 단계별 '동작'을 나타낸다:
  refine_requirements  — intake → clarify → classify        (구체화 + FR/NFR 분류)
  model_use_cases      — identify_actors → identify_use_cases → check_coverage
  write_specifications — generate_specs → check_specs
  draw_diagram         — identify_relationships → check_relationships → render_diagram

단계별 세부 작업은 서브그래프 내부 노드로 캡슐화된다. 대화형 피드백 게이트는 상위 그래프의
스테이지 사이에 배치한다(서브그래프 안이 아님) — LangGraph는 서브그래프가 interrupt로 멈추면
그 내부 누적 상태를 부모로 올리지 않으므로, 게이트를 부모 레벨에 두어야 멈춘 시점의 산출물이
응답에 실린다.

라우팅은 전부 정적(컴파일 타임 엣지)이다. 서브그래프는 체크포인터 없이 컴파일한다 — 상위
그래프의 MemorySaver가 트리 전체를 관장한다.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.state import AgentState
from app.agent.steps.step1_requirements import intake, clarify, classify
from app.agent.steps.step2_usecases import (
    identify_actors, identify_use_cases, check_coverage,
)
from app.agent.steps.step3_specifications import generate_specs, check_specs
from app.agent.steps.step4_diagram import (
    identify_relationships, check_relationships, render_diagram,
)


def build_refine_requirements():
    """STEP 1 — 요구사항 구체화 및 분류: intake → clarify → classify."""
    b = StateGraph(AgentState)
    b.add_node("intake", intake)
    b.add_node("clarify", clarify)
    b.add_node("classify", classify)
    b.add_edge(START, "intake")
    b.add_edge("intake", "clarify")
    b.add_edge("clarify", "classify")
    b.add_edge("classify", END)
    return b.compile()


def build_model_use_cases():
    """STEP 2 — 액터/유스케이스 도출 + FR 커버리지 점검."""
    b = StateGraph(AgentState)
    b.add_node("identify_actors", identify_actors)
    b.add_node("identify_use_cases", identify_use_cases)
    b.add_node("check_coverage", check_coverage)
    b.add_edge(START, "identify_actors")
    b.add_edge("identify_actors", "identify_use_cases")
    b.add_edge("identify_use_cases", "check_coverage")
    b.add_edge("check_coverage", END)
    return b.compile()


def build_write_specifications():
    """STEP 3 — 유스케이스별 명세 생성 + 검증 집계."""
    b = StateGraph(AgentState)
    b.add_node("generate_specs", generate_specs)
    b.add_node("check_specs", check_specs)
    b.add_edge(START, "generate_specs")
    b.add_edge("generate_specs", "check_specs")
    b.add_edge("check_specs", END)
    return b.compile()


def build_draw_diagram():
    """STEP 4 — 관계 식별 + 검증 집계 + 다이어그램 렌더."""
    b = StateGraph(AgentState)
    b.add_node("identify_relationships", identify_relationships)
    b.add_node("check_relationships", check_relationships)
    b.add_node("render_diagram", render_diagram)
    b.add_edge(START, "identify_relationships")
    b.add_edge("identify_relationships", "check_relationships")
    b.add_edge("check_relationships", "render_diagram")
    b.add_edge("render_diagram", END)
    return b.compile()


def build_stage_subgraphs() -> dict:
    """4단계 순수 스테이지 서브그래프를 컴파일해 {동작이름: 컴파일된 서브그래프}로 반환한다.

    키(=상위 그래프 노드명)는 스테이지 순서를 따른다.
    """
    return {
        "refine_requirements": build_refine_requirements(),
        "model_use_cases": build_model_use_cases(),
        "write_specifications": build_write_specifications(),
        "draw_diagram": build_draw_diagram(),
    }
