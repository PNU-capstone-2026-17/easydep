"""요구사항 분석 그래프 조립기 + 서빙 헬퍼.

이 파일은 4단계 서브그래프(app/agent/subgraphs.py)를 상위 그래프로 배선·컴파일하는 단일
진입점이다. 세부 작업은 각 단계 서브그래프 안에 캡슐화돼 있다.

전체 워크플로우(상위 그래프, 노드명 = 단계별 동작):
  START → refine_requirements → model_use_cases → write_specifications → draw_diagram → END
  (각 노드 = 컴파일된 스테이지 서브그래프. 세부 노드는 app/agent/subgraphs.py 참조)

대화형 게이트는 gated 그래프에서만 스테이지 사이에 부모-레벨 노드로 삽입한다:
  refine_requirements → gate_requirements → model_use_cases → gate_use_cases
      → write_specifications → gate_specs → draw_diagram → gate_relationships → END
(게이트를 서브그래프 안이 아니라 부모 레벨에 두는 이유: LangGraph는 서브그래프가 interrupt로
멈추면 그 내부 누적 상태를 부모로 올리지 않으므로, 스테이지 완료 후 부모 게이트에서 멈춰야
멈춘 시점의 산출물이 응답에 실린다.)

라우팅은 전부 정적(컴파일 타임 엣지)이다. 게이트는 gate_route 마커 + add_conditional_edges로
advance(다음 스테이지)/loop(재생성 후 재질문)를 정적 선언한다. 게이트 on/off는 서로 다른 두
그래프로 컴파일한다(런타임 settings 분기 없음). build_graph(None)만 빌드 타임에 settings를 1회
읽어 토폴로지를 고른다(런타임 라우팅이 아니라 빌드 선택).
"""
from __future__ import annotations

from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.requirements.agent.state import AgentState
from app.requirements.agent.subgraphs import build_stage_subgraphs
from app.requirements.agent.steps.feedback_gates import (
    route_gate, gate_requirements, gate_use_cases, gate_specs, gate_relationships,
)
from app.requirements.config import settings

def _build_plain_graph():
    """게이트 없는 플랫 파이프라인: 4단계가 순서대로 이어지고 끝난다.

    START → refine_requirements → model_use_cases → write_specifications → draw_diagram → END
    """
    subs = build_stage_subgraphs()
    builder = StateGraph(AgentState)
    builder.add_node("refine_requirements", subs["refine_requirements"])
    builder.add_node("model_use_cases", subs["model_use_cases"])
    builder.add_node("write_specifications", subs["write_specifications"])
    builder.add_node("draw_diagram", subs["draw_diagram"])

    builder.add_edge(START, "refine_requirements")
    builder.add_edge("refine_requirements", "model_use_cases")
    builder.add_edge("model_use_cases", "write_specifications")
    builder.add_edge("write_specifications", "draw_diagram")
    builder.add_edge("draw_diagram", END)

    # 체크포인터는 상위 그래프에만 둔다 — 서브그래프의 interrupt도 상위로 전파돼 상위
    # invoke(Command(resume=...))로 재개된다(서브그래프는 무-체크포인터로 컴파일됨).
    return builder.compile(checkpointer=MemorySaver())


def _build_gated_graph():
    """대화형 피드백 게이트가 낀 파이프라인: 각 스테이지 뒤에 부모-레벨 게이트를 둔다.

    각 게이트는 advance→다음 스테이지(마지막은 END), loop→게이트 자신(재생성 후 재질문).
    게이트를 서브그래프 안이 아니라 부모 레벨에 두는 이유는 모듈 상단 docstring 참조.
    """
    subs = build_stage_subgraphs()
    builder = StateGraph(AgentState)
    builder.add_node("refine_requirements", subs["refine_requirements"])
    builder.add_node("model_use_cases", subs["model_use_cases"])
    builder.add_node("write_specifications", subs["write_specifications"])
    builder.add_node("draw_diagram", subs["draw_diagram"])
    builder.add_node("gate_requirements", gate_requirements)
    builder.add_node("gate_use_cases", gate_use_cases)
    builder.add_node("gate_specs", gate_specs)
    builder.add_node("gate_relationships", gate_relationships)

    builder.add_edge(START, "refine_requirements")
    builder.add_edge("refine_requirements", "gate_requirements")
    builder.add_conditional_edges(
        "gate_requirements", route_gate,
        {"advance": "model_use_cases", "loop": "gate_requirements"},
    )
    builder.add_edge("model_use_cases", "gate_use_cases")
    builder.add_conditional_edges(
        "gate_use_cases", route_gate,
        {"advance": "write_specifications", "loop": "gate_use_cases"},
    )
    builder.add_edge("write_specifications", "gate_specs")
    builder.add_conditional_edges(
        "gate_specs", route_gate,
        {"advance": "draw_diagram", "loop": "gate_specs"},
    )
    builder.add_edge("draw_diagram", "gate_relationships")
    builder.add_conditional_edges(
        "gate_relationships", route_gate,
        {"advance": END, "loop": "gate_relationships"},
    )

    return builder.compile(checkpointer=MemorySaver())


def build_graph(feedback_gates: bool | None = None):
    """settings(또는 인자)에 따라 두 플랫 빌더 중 하나를 골라 컴파일한 그래프를 반환한다.

    feedback_gates=None이면 빌드 타임에 settings.enable_feedback_gates 를 1회 읽는다(기존
    호출부/테스트 호환). 게이트 on/off는 런타임 분기가 아니라 서로 다른 정적 그래프다.
    """
    gated = settings.enable_feedback_gates if feedback_gates is None else feedback_gates
    return _build_gated_graph() if gated else _build_plain_graph()


# 앱 전역에서 재사용할 컴파일된 그래프 (모듈 로드 시 1회 생성)
graph = build_graph()

# 대화형 게이트 on/off 두 토폴로지를 모두 미리 컴파일해 두고 세션 모드에 맞춰 고른다.
# 각 그래프는 완전히 정적이며 독립 MemorySaver를 가지므로, 세션을 시작한 그래프로만 재개해야
# 체크포인트가 맞는다(그래서 _thread_gates로 thread_id → 모드를 기억해 같은 그래프로 재개).
_GRAPHS: dict[bool, object] = {
    False: build_graph(feedback_gates=False),
    True: build_graph(feedback_gates=True),
}
_thread_gates: dict[str, bool] = {}


def rebuild_graph():
    """settings 변경(예: enable_feedback_gates) 후 컴파일된 그래프들을 재컴파일한다."""
    global graph, _GRAPHS
    graph = build_graph()
    _GRAPHS = {
        False: build_graph(feedback_gates=False),
        True: build_graph(feedback_gates=True),
    }
    return graph


def _invoke(gates: bool, thread_id: str, graph_input):
    """모드에 맞는 정적 그래프를 골라 실행한다(런타임 라우팅 없음 → 직렬화 불필요)."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    return _GRAPHS[gates].invoke(graph_input, config)  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------
# 서빙 헬퍼 (main.py에서 사용)
# ----------------------------------------------------------------------------
def _result_payload(result: dict, thread_id: str) -> dict:
    """그래프 실행 결과를 API 응답 형태(dict)로 변환한다."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        value = interrupts[0].value
        # 대화형 피드백 게이트(step1~4 말미)
        if isinstance(value, dict) and value.get("status") == "need_feedback":
            payload = {
                "thread_id": thread_id,
                "phase": value.get("stage", "feedback"),
                "status": "need_feedback",
                "feedback_prompt": value.get("prompt"),
                "feedback_summary": value.get("summary"),
                "requirements": result.get("classified", []),
            }
            # 게이트에서 멈춘 시점까지 누적된 step2~4 산출물도 함께 실어 UI가 진행 상황을 보여준다.
            for key in ("actors", "use_cases", "coverage", "use_case_specs",
                        "spec_report", "relationships", "relationship_report", "diagram"):
                val = result.get(key)
                if val:
                    payload[key] = val
            return payload
        # 요구사항 구체화(clarify)
        questions = value.get("questions", []) if isinstance(value, dict) else value
        return {
            "thread_id": thread_id,
            "phase": "clarify",
            "status": "need_clarification",
            "questions": questions,
        }
    payload = {
        "thread_id": thread_id,
        "phase": result.get("phase", "completed"),
        "status": "completed",
        "requirements": result.get("classified", []),
    }
    # step2~4 산출물은 파이프라인이 돌았을 때만 존재하므로, 있을 때만 응답에 싣는다.
    for key in ("actors", "use_cases", "coverage", "use_case_specs", "spec_report",
                "relationships", "relationship_report", "diagram"):
        value = result.get(key)
        if value:
            payload[key] = value
    return payload


def start_analysis(
    requirements: list[str], thread_id: str, feedback_gates: bool | None = None
) -> dict:
    """새 요구사항 분석 세션을 시작한다.

    feedback_gates=None이면 서버 기본값(settings)을 따른다. 세션 모드는 thread_id에 기록해
    이후 resume_analysis가 같은 토폴로지의 그래프로 재개하도록 한다.
    """
    gates = settings.enable_feedback_gates if feedback_gates is None else feedback_gates
    _thread_gates[thread_id] = gates
    # 초기 입력은 부분 상태(나머지 키는 노드가 채움)라 AgentState로 캐스팅.
    result = _invoke(gates, thread_id, cast(AgentState, {"raw_requirements": requirements}))
    return _result_payload(result, thread_id)


def resume_analysis(answer: str, thread_id: str) -> dict:
    """clarifying question 또는 피드백 게이트에 대한 사용자 입력으로 세션을 재개한다."""
    gates = _thread_gates.get(thread_id, settings.enable_feedback_gates)
    result = _invoke(gates, thread_id, Command(resume=answer))
    return _result_payload(result, thread_id)
