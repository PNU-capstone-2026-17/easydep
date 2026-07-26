"""각 스텝 서브그래프 말미의 대화형 피드백 게이트(LangGraph interrupt, 정적 라우팅).

사용자가 피드백을 주면 그 스텝을 재생성하고 게이트로 루프백(다시 물음), 빈 값이면 다음 단계로
진행한다. 상위 스텝을 재생성하면 forward 흐름이 하위 스텝을 fresh 재실행하므로 cascade
정책(상위 재생성→하위 재생성)이 자연스럽게 성립한다.

라우팅 방식(정적): 게이트 노드는 상태 업데이트 + 라우팅 마커(gate_route: "advance"|"loop")만
반환하고, 분기 대상은 서브그래프의 add_conditional_edges(route_gate, {...})가 컴파일 타임에
선언한다. (예전 Command(goto=...) 런타임 라우팅을 대체 — 토폴로지가 컴파일 타임에 고정된다.)
"advance"는 서브그래프 END(→ 상위 그래프가 다음 스텝으로), "loop"는 같은 게이트로 재진입.

피드백은 배치 경로(app/feedback.py)와 동일한 의도 분류 엔진(apply_feedback_upto)을 거친다:
자연어 피드백을 {stage, scope, target_ids}로 분류해 '어느 게이트에서 말했든' 대상 stage로 라우팅하고
(예: use_cases 게이트에서 '외부 액터 추가'라고 하면 actors를 재생성), local scope면 대상 항목만
고쳐 형제(및 그 id)를 보존한다. cascade는 게이트가 이미 진행한 단계(up_to)까지만 수행한다.
"""
from __future__ import annotations

from typing import cast

from langgraph.types import interrupt

from app.requirements.agent.state import AgentState
from app.requirements.agent.steps.step1_requirements import classify
from app.requirements.agent.steps.step3_specifications import check_specs
from app.requirements.agent.steps.step4_diagram import check_relationships
from app.requirements.schemas import FeedbackEdit


def apply_feedback_upto(state: dict, feedback: str, up_to: str):
    """app.requirements.feedback.apply_feedback_upto 로의 지연 위임.

    app.feedback가 app.requirements.agent.* 를 임포트하므로 모듈 상단에서 직접 임포트하면 순환 참조가 된다
    (graph→feedback_gates→app.requirements.feedback→app.requirements.agent.__init__→graph). 호출 시점에 임포트해 끊는다.
    함수 내부의 `from ... import`는 호출 시 모듈 속성을 조회하므로 테스트의 monkeypatch도 반영된다.
    """
    from app.requirements.feedback import apply_feedback_upto as _impl
    return _impl(state, feedback, up_to)


def _ask(stage: str, summary, *, edit_stage: str | None = None, edit_targets=()) -> object:
    """피드백을 요청하는 interrupt. 재개 값을 그대로 반환한다.

    재개 값은 자연어 문자열이거나 `FeedbackEdit`이다. `edit_stage`/`edit_targets`는
    화면이 후자를 만들 때 쓰는 재료다 — 어느 단계를 재생성할 수 있고 어떤 항목을
    고를 수 있는지. 화면이 그걸 보내면 의도 분류 LLM 호출이 생략된다.
    """
    return interrupt({
        "stage": stage,
        "status": "need_feedback",
        "prompt": f"[{stage}] 결과에 대한 피드백을 입력하세요. 비워두면 다음 단계로 진행합니다.",
        "summary": summary,
        "edit_stage": edit_stage,
        "edit_targets": list(edit_targets),
    })


def _empty(answer) -> bool:
    """다음 단계로 진행하라는 신호인지. 구조화 편집은 지시가 비었을 때만 비어 있다."""
    if isinstance(answer, FeedbackEdit):
        return not answer.instruction.strip()
    return not str(answer or "").strip()


def _as_text(answer) -> str:
    """자연어만 받는 자리(step1 재분류)에 넘길 문자열."""
    return answer.instruction if isinstance(answer, FeedbackEdit) else str(answer)


def _pick(state: dict, keys: tuple[str, ...]) -> dict:
    """state에서 존재하는 키만 골라 상태 업데이트 delta로 만든다."""
    return {k: state[k] for k in keys if k in state}


def route_gate(state: AgentState) -> str:
    """게이트가 남긴 마커를 읽어 분기 키("advance"|"loop")를 돌려준다(조건부 엣지용)."""
    return state.get("gate_route", "advance")


# ---------------------------------------------------------------------------
# 게이트 노드 — (상태 업데이트 + gate_route 마커) dict를 반환. Command 라우팅 없음.
# ---------------------------------------------------------------------------
def gate_requirements(state: AgentState) -> dict:
    """step1(요구사항 구체화·FR/NFR 분류) 말미 게이트.

    피드백이 있으면 재분류(classify: BERT 단독) 후 루프백, 없으면 다음 단계(step2)로 진행한다.
    (step1은 아직 액터/UC가 없어 의도 분류 대상이 아니므로 여기서는 분류 노드를 그대로 재실행한다.)
    """
    # step1에는 재생성할 stage 선택지가 없다(분류는 BERT 단독 결정론). 그래서
    # edit_stage를 주지 않는다 — 화면이 구조화 편집을 만들 재료가 없다는 뜻이다.
    answer = _ask(
        "requirements",
        [f"{r['id']}:{r['type']} {r['text']}" for r in state.get("classified", [])],
    )
    if _empty(answer):
        return {"gate_route": "advance"}
    upd = classify(state, feedback=_as_text(answer))  # BERT 단독 재분류
    return {**upd, "gate_route": "loop"}


def gate_use_cases(state: AgentState) -> dict:
    """step2(액터/유스케이스) 말미 게이트. 의도에 따라 actors/use_cases를 범위대로 재생성."""
    use_cases = state.get("use_cases", [])
    answer = _ask(
        "use_cases",
        [u["name"] for u in use_cases],
        edit_stage="use_cases",
        # id가 없는 항목은 고를 수 없으니 뺀다. 게이트가 사용자에게 물어보는 자리라
        # 여기서 KeyError로 죽으면 세션이 통째로 끝난다.
        edit_targets=[u["id"] for u in use_cases if u.get("id")],
    )
    if _empty(answer):
        return {"gate_route": "advance"}
    st = dict(state)
    apply_feedback_upto(st, answer, up_to="coverage")
    return {**_pick(st, ("actors", "use_cases", "coverage")), "gate_route": "loop"}


def gate_specs(state: AgentState) -> dict:
    """step3(명세) 말미 게이트. specs local 피드백은 대상 UC 명세만 재생성한다."""
    specs = state.get("use_case_specs", [])
    answer = _ask(
        "specs",
        [s["use_case_id"] for s in specs],
        edit_stage="specs",
        edit_targets=[s["use_case_id"] for s in specs if s.get("use_case_id")],
    )
    if _empty(answer):
        return {"gate_route": "advance"}
    st = dict(state)
    apply_feedback_upto(st, answer, up_to="specs")
    st.update(check_specs(cast(AgentState, st)))  # spec_report 갱신
    upd = _pick(st, ("actors", "use_cases", "coverage", "use_case_specs", "spec_report"))
    return {**upd, "gate_route": "loop"}


def gate_relationships(state: AgentState) -> dict:
    """step4(관계/다이어그램) 말미 게이트."""
    # 관계는 항목 단위로 고르기 어렵다(연결의 집합이지 목록이 아니다) → broad만 제공.
    answer = _ask(
        "relationships", state.get("relationships", {}), edit_stage="relationships"
    )
    if _empty(answer):
        return {"gate_route": "advance"}
    st = dict(state)
    apply_feedback_upto(st, answer, up_to="diagram")
    st.update(check_specs(cast(AgentState, st)))          # 상위 stage 편집 시 명세도 바뀔 수 있어 갱신
    st.update(check_relationships(cast(AgentState, st)))  # relationship_report 갱신
    upd = _pick(st, (
        "actors", "use_cases", "coverage", "use_case_specs", "spec_report",
        "relationships", "relationship_report", "diagram",
    ))
    return {**upd, "gate_route": "loop"}
