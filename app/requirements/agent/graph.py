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

from app.requirements import session_store
from app.requirements.agent.state import AgentState
from app.requirements.agent.steps.feedback_gates import (
    gate_relationships,
    gate_requirements,
    gate_specs,
    gate_use_cases,
    route_gate,
)
from app.requirements.agent.subgraphs import build_stage_subgraphs
from app.requirements.common import telemetry
from app.requirements.config import settings
from app.requirements.schemas import FeedbackEdit
from app.requirements.session_store import SqlCheckpointSaver


def _build_plain_graph(saver):
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
    return builder.compile(checkpointer=saver)


def _build_gated_graph(saver):
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

    return builder.compile(checkpointer=saver)


def build_graph(feedback_gates: bool | None = None, *, persistent: bool = False):
    """settings(또는 인자)에 따라 두 플랫 빌더 중 하나를 골라 컴파일한 그래프를 반환한다.

    feedback_gates=None이면 빌드 타임에 settings.enable_feedback_gates 를 1회 읽는다(기존
    호출부/테스트 호환). 게이트 on/off는 런타임 분기가 아니라 서로 다른 정적 그래프다.

    persistent=True면 체크포인트를 MySQL에 쓴다(서버 재시작을 넘어 세션이 살아남는다).
    False면 프로세스 메모리 — CLI·배치처럼 프로세스가 끝나면 세션도 의미가 없는 경로용이고,
    DB 없이 돌 수 있어야 하는 경로이기도 하다.
    """
    gated = settings.enable_feedback_gates if feedback_gates is None else feedback_gates
    saver = SqlCheckpointSaver() if persistent else MemorySaver()
    return _build_gated_graph(saver) if gated else _build_plain_graph(saver)


# 앱 전역에서 재사용할 컴파일된 그래프 (모듈 로드 시 1회 생성)
graph = build_graph()

# (게이트 on/off) × (영속 여부) 네 토폴로지를 미리 컴파일해 두고 세션에 맞춰 고른다.
# 게이트 on/off는 서로 다른 그래프라 **세션을 시작한 쪽으로만 재개해야** 체크포인트가 맞는다.
# 그래서 thread_id → 모드를 기억한다. 영속 세션은 그 기억도 DB에 둔다(재시작을 넘겨야 하므로).
_GRAPHS: dict[tuple[bool, bool], object] = {}


def _compiled(gated: bool, persistent: bool):
    key = (gated, persistent)
    if key not in _GRAPHS:
        _GRAPHS[key] = build_graph(feedback_gates=gated, persistent=persistent)
    return _GRAPHS[key]


# 비영속 세션의 모드 기억. 영속 세션은 session_store가 대신한다.
_thread_gates: dict[str, bool] = {}


def rebuild_graph():
    """settings 변경(예: enable_feedback_gates) 후 컴파일된 그래프들을 버린다."""
    global graph
    graph = build_graph()
    _GRAPHS.clear()
    return graph


def _remember_mode(thread_id: str, gated: bool, persistent: bool) -> None:
    if persistent:
        session_store.remember_session_mode(thread_id, gated)
    else:
        _thread_gates[thread_id] = gated


def _recall_mode(thread_id: str, persistent: bool) -> bool:
    """이 세션이 시작된 토폴로지. 모르면 서버 기본값으로 떨어진다."""
    remembered = (
        session_store.session_mode(thread_id)
        if persistent
        else _thread_gates.get(thread_id)
    )
    return settings.enable_feedback_gates if remembered is None else remembered


def _invoke(gates: bool, thread_id: str, graph_input, persistent: bool):
    """모드에 맞는 정적 그래프를 골라 실행한다(런타임 라우팅 없음 → 직렬화 불필요)."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    return _compiled(gates, persistent).invoke(graph_input, config)  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------
# 서빙 헬퍼 (main.py에서 사용)
# ----------------------------------------------------------------------------
def _result_payload(
    result: dict, thread_id: str, stats: telemetry.RunStats | None = None
) -> dict:
    """그래프 실행 결과를 API 응답 형태(dict)로 변환한다.

    stats를 주면 이번 호출의 계측(호출 수·토큰·저하 목록)을 함께 싣는다. 저하가 있으면
    산출물 일부가 검증을 못 거친 것이므로, 화면이 그 사실을 알 수 있어야 한다.
    """
    def _finish(payload: dict) -> dict:
        if stats is not None:
            payload["telemetry"] = stats.as_dict()
        return payload

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
                # 화면이 구조화 편집을 만들 재료. 없으면(step1) 자연어만 받는다.
                "edit_stage": value.get("edit_stage"),
                "edit_targets": value.get("edit_targets"),
                "requirements": result.get("classified", []),
            }
            # 게이트에서 멈춘 시점까지 누적된 step2~4 산출물도 함께 실어 UI가 진행 상황을 보여준다.
            for key in ("actors", "use_cases", "coverage", "use_case_specs",
                        "spec_report", "relationships", "relationship_report", "diagram"):
                val = result.get(key)
                if val:
                    payload[key] = val
            return _finish(payload)
        # 요구사항 구체화(clarify)
        questions = value.get("questions", []) if isinstance(value, dict) else value
        return _finish({
            "thread_id": thread_id,
            "phase": "clarify",
            "status": "need_clarification",
            "questions": questions,
        })
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
    return _finish(payload)


def start_analysis(
    requirements: list[str],
    thread_id: str,
    feedback_gates: bool | None = None,
    *,
    persist: bool = False,
) -> dict:
    """새 요구사항 분석 세션을 시작한다.

    feedback_gates=None이면 서버 기본값(settings)을 따른다. 세션 모드는 thread_id에 기록해
    이후 resume_analysis가 같은 토폴로지의 그래프로 재개하도록 한다.

    persist=True면 체크포인트와 세션 모드를 MySQL에 남긴다 — 서버가 재시작해도 이어진다.
    서빙 경로(api.py)가 켜고, CLI·배치는 끈 채로 둔다(프로세스와 수명이 같고 DB 없이도 돌아야 한다).
    """
    gates = settings.enable_feedback_gates if feedback_gates is None else feedback_gates
    _remember_mode(thread_id, gates, persist)
    # 초기 입력은 부분 상태(나머지 키는 노드가 채움)라 AgentState로 캐스팅.
    with telemetry.run_scope(f"analyze:{thread_id}") as stats:
        result = _invoke(
            gates, thread_id, cast(AgentState, {"raw_requirements": requirements}), persist
        )
        return _result_payload(result, thread_id, stats)


def resume_analysis(
    answer: str | FeedbackEdit, thread_id: str, *, persist: bool = False
) -> dict:
    """clarifying question 또는 피드백 게이트에 대한 사용자 입력으로 세션을 재개한다.

    answer는 자연어 문자열이거나, 화면이 대상을 이미 아는 경우 `FeedbackEdit`이다.
    후자는 의도 분류 LLM 호출을 건너뛴다(app/requirements/feedback.py: resolve_intent).

    persist는 start_analysis 때와 같아야 한다 — 체크포인트가 있는 곳에서 찾아야 한다.
    """
    gates = _recall_mode(thread_id, persist)
    with telemetry.run_scope(f"resume:{thread_id}") as stats:
        result = _invoke(gates, thread_id, Command(resume=answer), persist)
        return _result_payload(result, thread_id, stats)
