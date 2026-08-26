"""검사 노드의 재생성 루프 계약 (네트워크 불필요).

`check_node`는 **모델이 규칙을 지켰는지** 판정하고, 어겼으면 유계로 재생성한다. 여기서
고정하는 것은 그 루프가 지켜야 할 약속들이다:

  - 위반이 줄면 채택하고, 안 줄면 **직전본을 지킨다**(종료 보장의 근거)
  - 예산을 넘겨 고치지 않는다
  - 재생성이 실패해도 스테이지를 죽이지 않고 직전본을 지킨다
  - 왜 멈췄는지를 기록한다 — "위반 0건"과 "예산이 끝났다"를 같은 값으로 두지 않는다
  - 남은 위반이 게이트까지 실려 간다 (숨기지 않는다)

LLM은 전부 스크립트 페이크로 대체한다. 진짜 모델을 부르면 재현이 안 되고, 여기서
검사하려는 것(루프의 산술)이 흐려진다.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.design.evaluation.seeded import CLEAN, CLEAN_STATE, SEEDED
from app.design.graphs.subgraphs import CLASS_DIAGRAM_SPEC
from app.design.knowledge.detectors import Finding
from app.design.nodes.artifact import (
    BUDGET,
    CHECKED_ONLY,
    CLEAN as STOPPED_CLEAN,
    ERROR,
    NO_IMPROVEMENT,
    check_node,
    repair_directive,
)

#: 매달린 끝 하나를 심은 모델과, 그것이 없는 깨끗한 모델.
DIRTY = SEEDED[0].model
CHECK_KEY = CLASS_DIAGRAM_SPEC.check_key


def _spec_with(revise):
    """진짜 리바이저 대신 스크립트를 끼운 클래스 다이어그램 스펙."""
    return dataclasses.replace(CLASS_DIAGRAM_SPEC, revise=revise)


def _run(spec, model, state=None):
    merged = {**(state or CLEAN_STATE), spec.model_key: model}
    return check_node(spec)(merged)


@pytest.fixture(autouse=True)
def _two_repairs(monkeypatch):
    """예산을 테스트 안에서 못박는다 — 환경변수에 따라 결과가 달라지면 안 된다."""
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "2")


# ---------------------------------------------------------------------------
# 깨끗한 모델
# ---------------------------------------------------------------------------
def test_a_clean_model_is_not_sent_to_the_llm_at_all():
    """위반이 없으면 재생성을 아예 부르지 않는다 — 부르면 그건 낭비이고 회귀 위험이다."""
    calls = []

    def never(*args, **kwargs):
        calls.append(args)
        raise AssertionError("깨끗한 모델에 재생성을 불렀다")

    out = _run(_spec_with(never), CLEAN)
    assert calls == []
    assert out[CHECK_KEY] == {"findings": [], "repair_iters": 0, "stopped": STOPPED_CLEAN}


def test_operation_contract_findings_are_not_sent_to_the_whole_model_reviser():
    """Execution-group repair owns operation defects; the global reviser must not compete."""

    calls = []

    def never(*args, **kwargs):
        calls.append(args)
        raise AssertionError("operation defects must stay in the group-local repair path")

    spec = dataclasses.replace(
        CLASS_DIAGRAM_SPEC,
        revise=never,
        check=lambda model, state: [
            Finding(
                "class.operation-input-producers",
                "parameter source is incomplete",
                "ExampleControl::handle(value:String)#value",
            )
        ],
    )

    out = _run(spec, CLEAN)

    assert calls == []
    assert out[CHECK_KEY]["repair_iters"] == 0
    assert out[CHECK_KEY]["stopped"] == CHECKED_ONLY
    assert out[CHECK_KEY]["findings"]


# ---------------------------------------------------------------------------
# 고쳐지는 경우
# ---------------------------------------------------------------------------
def test_a_repair_that_removes_the_violation_is_adopted():
    def fixes_it(current, feedback, state, targets):
        return CLEAN

    out = _run(_spec_with(fixes_it), DIRTY)
    assert out[CHECK_KEY]["stopped"] == STOPPED_CLEAN
    assert out[CHECK_KEY]["findings"] == []
    assert out[CHECK_KEY]["repair_iters"] == 1
    assert out[CLASS_DIAGRAM_SPEC.model_key] == CLEAN


def test_the_repair_prompt_carries_the_rule_tag_not_just_the_complaint():
    """모델에게 **왜** 결함인지를 준다.

    근거를 숨기고 고치라고 하면, 규칙을 지키는 대신 지적 문구를 회피하는 쪽으로 고친다.
    """
    seen: list[str] = []

    def record(current, feedback, state, targets):
        seen.append(feedback)
        return CLEAN

    _run(_spec_with(record), DIRTY)
    assert "class.relationship-endpoints-exist" in seen[0]
    assert "GhostEntity" in seen[0]


def test_the_repair_is_asked_to_revise_the_whole_artifact():
    """지목 수정이 아니다 — 위반이 여러 클래스에 걸칠 수 있다.

    `targets`가 비어 있어야 `merge_model`이 재생성본을 그대로 쓴다. 비어 있지 않으면
    비대상 항목의 수정이 조용히 버려져 위반이 안 줄고 `no_improvement`로 멈춘다.
    """
    seen: list[set] = []

    def record(current, feedback, state, targets):
        seen.append(targets)
        return CLEAN

    _run(_spec_with(record), DIRTY)
    assert seen[0] == set()


# ---------------------------------------------------------------------------
# 안 고쳐지는 경우 — 여기가 종료 보장의 자리다
# ---------------------------------------------------------------------------
def test_a_repair_that_does_not_reduce_violations_is_discarded():
    """위반 수가 안 줄면 재생성본을 **버리고 직전본을 지킨다.**

    이 조건이 종료를 보장한다: 위반 수는 자연수이고 매 회 반드시 줄어야 하므로, 루프가
    무한히 돌 수 없다. 옛 문법 수리 루프에 없던 성질이다.
    """
    def makes_it_no_better(current, feedback, state, targets):
        return SEEDED[3].model  # 다른 위반 1건 — 수가 그대로다

    out = _run(_spec_with(makes_it_no_better), DIRTY)
    assert out[CHECK_KEY]["stopped"] == NO_IMPROVEMENT
    assert out[CHECK_KEY]["repair_iters"] == 1
    # 버렸으므로 원래 위반이 그대로 남아 있어야 한다.
    assert out[CLASS_DIAGRAM_SPEC.model_key] == DIRTY
    assert "GhostEntity" in out[CHECK_KEY]["findings"][0]


def test_a_repair_that_makes_it_worse_is_discarded_too():
    def makes_it_worse(current, feedback, state, targets):
        return {"Classes": [], "Relationships": [{"source": "A", "target": "B"}]}

    out = _run(_spec_with(makes_it_worse), DIRTY)
    assert out[CHECK_KEY]["stopped"] == NO_IMPROVEMENT
    assert out[CLASS_DIAGRAM_SPEC.model_key] == DIRTY


def test_the_budget_is_not_exceeded_and_says_so(monkeypatch):
    """예산을 다 쓰면 멈추고, **그 사실을 기록한다.**

    남은 위반을 안고 멈춘 것과 깨끗해서 멈춘 것이 같은 값이면, 화면은 통과했다고 믿는다.
    """
    from app.core.config import settings
    monkeypatch.setattr(settings, "design_max_repair_iters", 3)

    def with_ghosts(count):
        """매달린 끝을 `count`개 단 모델 — 위반이 정확히 `count`건이다."""
        return {
            "Classes": CLEAN["Classes"],
            "Relationships": CLEAN["Relationships"]
            + [{"source": "OrderController", "target": f"Ghost{i}"} for i in range(count)],
        }

    calls = []

    def shaves_one_off(current, feedback, state, targets):
        """매번 위반을 하나씩만 줄인다 — 채택되지만 예산 안에 깨끗해지지 않는다."""
        calls.append(1)
        return with_ghosts(5 - len(calls))

    out = _run(_spec_with(shaves_one_off), with_ghosts(5))
    assert len(calls) == 3
    assert out[CHECK_KEY]["repair_iters"] == 3
    assert out[CHECK_KEY]["stopped"] == BUDGET
    assert out[CHECK_KEY]["findings"], "남은 위반이 있는데 비어 있다"


def test_a_zero_budget_checks_but_never_repairs(monkeypatch):
    """예산 0은 "검사만 하고 보고한다"이다 — 검사를 끄는 것이 아니다."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "design_max_repair_iters", 0)

    def never(*args, **kwargs):
        raise AssertionError("예산이 0인데 재생성을 불렀다")

    out = _run(_spec_with(never), DIRTY)
    assert out[CHECK_KEY]["repair_iters"] == 0
    assert out[CHECK_KEY]["stopped"] == BUDGET
    assert len(out[CHECK_KEY]["findings"]) == 1


# ---------------------------------------------------------------------------
# 재생성이 실패하는 경우
# ---------------------------------------------------------------------------
def test_a_failing_repair_does_not_kill_the_stage():
    """LLM 호출이 실패해도 스테이지는 계속 간다.

    검증을 붙였다고 산출물을 못 만들게 되면 안 된다 — 검증은 없던 것을 더한 것이지,
    있던 것을 걸어 잠그는 장치가 아니다.
    """
    def explodes(current, feedback, state, targets):
        raise TimeoutError("LLM request timed out")

    out = _run(_spec_with(explodes), DIRTY)
    assert out[CHECK_KEY]["stopped"] == ERROR
    assert "TimeoutError" in out[CHECK_KEY]["error"]
    # 직전본을 지켰다.
    assert out[CLASS_DIAGRAM_SPEC.model_key] == DIRTY
    assert out[CHECK_KEY]["findings"], "실패했는데 위반이 사라졌다"


@pytest.mark.parametrize(
    "wiped",
    [{}, {"Classes": [], "Relationships": []}],
    ids=["빈 dict", "빈 목록"],
)
@pytest.mark.parametrize(
    "state",
    [CLEAN_STATE, {"usecase_spec": {}}],
    ids=["상류 있음", "상류 없음"],
)
def test_a_repair_that_empties_the_model_is_never_adopted(wiped, state):
    """산출물을 비워서 위반을 없앤 것을 성공으로 받지 않는다.

    **빈 모델은 거의 모든 검사를 통과한다** — 검사할 것이 없으니 위반도 없다. 위반 수만
    보는 수용 조건은 여기서 무너진다: 클래스를 전부 날린 재생성본이 "위반 0건"으로
    채택되고 `stopped="clean"`이 적힌다. 산출물을 통째로 잃고도 깨끗하다고 보고하는 것이다.

    `상류 없음`이 이 테스트의 핵심이다. 상류에 유스케이스 id가 있으면 커버리지 검사가
    우연히 막아 주지만(아무도 안 가리키게 되므로), 입력에 id가 없으면 그 검사가 아예
    안 돌아서 함정이 그대로 열린다. 두 상태를 다 돌리는 이유다.
    """
    def wipes_it(current, feedback, state, targets):
        return wiped

    out = _run(_spec_with(wipes_it), DIRTY, state)
    assert out[CLASS_DIAGRAM_SPEC.model_key] == DIRTY
    assert out[CHECK_KEY]["stopped"] == NO_IMPROVEMENT


# ---------------------------------------------------------------------------
# 남은 위반이 사람에게 도달하는가
# ---------------------------------------------------------------------------
def test_remaining_violations_reach_the_gate():
    """게이트 페이로드가 남은 위반과 멈춘 이유를 함께 싣는가.

    사용자가 판단할 재료가 곧 이 페이로드다. 여기 안 실리면 "문법 통과"만 보이고
    내용이 틀린 다이어그램이 통과한 것처럼 보인다.
    """
    from app.design.nodes.gates import make_gate

    captured: dict = {}

    def fake_interrupt(payload):
        captured.update(payload)
        return ""

    import app.design.nodes.gates as gates_module

    original = gates_module.interrupt
    gates_module.interrupt = fake_interrupt
    try:
        make_gate("class_diagram")(
            {
                "class_diagram_puml": "@startuml\n@enduml",
                CHECK_KEY: {
                    "findings": ["OrderController -> GhostEntity: ... [rule]"],
                    "repair_iters": 2,
                    "stopped": BUDGET,
                },
            }
        )
    finally:
        gates_module.interrupt = original

    assert captured["findings"] == ["OrderController -> GhostEntity: ... [rule]"]
    assert captured["check_status"] == BUDGET
    assert captured["repair_iters"] == 2


def test_a_stage_without_rules_reports_not_checked_rather_than_clean():
    """규칙이 없는 스테이지는 `check_status`가 None이다.

    빈 목록 + None은 "검사하지 않았다"이고, 빈 목록 + "clean"은 "검사했고 깨끗하다"이다.
    둘을 같은 모양으로 내보내면 화면이 검사하지 않은 것을 통과로 그린다.
    """
    from app.design.nodes.gates import make_gate
    import app.design.nodes.gates as gates_module

    captured: dict = {}
    original = gates_module.interrupt
    gates_module.interrupt = lambda payload: captured.update(payload) or ""
    try:
        make_gate("erd")({"erd_puml": "@startuml\n@enduml"})
    finally:
        gates_module.interrupt = original

    assert captured["findings"] == []
    assert captured["check_status"] is None


# ---------------------------------------------------------------------------
# 지시문
# ---------------------------------------------------------------------------
def test_the_directive_tells_the_model_to_keep_what_was_right():
    """"고쳐라"만 주면 모델이 나머지를 다시 쓴다 — 그러면 위반이 안 줄어 버려진다."""
    directive = repair_directive([Finding("class.names-unique", "중복", "Order")])
    assert "Order" in directive
    assert "do not introduce new violations" in directive
    assert "Keep everything that was already correct" in directive
