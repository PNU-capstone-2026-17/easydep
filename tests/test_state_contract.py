"""app/requirements/common/state_contract.py + 단계별 적용 테스트.

핵심 구분 하나를 고정한다: **키 부재 ≠ 빈 값.**
  - 부재  = 상류가 돌지 않았다 (배선 오류 → 크게 실패)
  - 빈 값 = 상류가 돌았고 결과가 없었다 (정상일 수 있다 → 통과)

이 구분이 없으면 cascade에서 한 단계가 통째로 빠져도 "결과가 0건인 분석"으로 보인다.
"""
import pytest

from app.requirements.common.state_contract import (
    MissingUpstreamState,
    require,
    require_any,
)
from app.requirements.modeling import diagram, relationships, specifications, use_cases


def test_require_passes_when_keys_exist_even_if_empty():
    require({"a": [], "b": None}, "a", "b", stage="s")   # 예외 없이 통과


def test_require_names_every_missing_key():
    with pytest.raises(MissingUpstreamState) as excinfo:
        require({"a": 1}, "a", "b", "c", stage="draw")
    error = excinfo.value
    assert error.stage == "draw"
    assert error.missing == ["b", "c"]
    assert "draw" in str(error)


def test_require_any_needs_only_one():
    require_any({"raw_requirements": []}, "refined_requirements", "raw_requirements", stage="c")
    with pytest.raises(MissingUpstreamState):
        require_any({}, "refined_requirements", "raw_requirements", stage="c")


# ---------------------------------------------------------------------------
# 단계에 실제로 걸려 있는가 — 조용한 빈 산출물 대신 큰 실패가 나는가.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("call", "missing"),
    [
        (lambda: use_cases.identify_actors({}), "classified"),
        (lambda: use_cases.identify_use_cases({"classified": []}), "actors"),
        (lambda: use_cases.check_coverage({"classified": []}), "use_cases"),
        (lambda: specifications.generate_specs({"use_cases": []}), "classified"),
        (lambda: specifications.check_specs({}), "use_case_specs"),
        (lambda: relationships.identify_relationships({"use_cases": []}), "actors"),
        (lambda: relationships.check_relationships({}), "relationships"),
        (lambda: diagram.render_diagram({"use_cases": [], "actors": []}), "relationships"),
    ],
)
def test_missing_upstream_state_fails_loudly(call, missing):
    with pytest.raises(MissingUpstreamState) as excinfo:
        call()
    assert missing in excinfo.value.missing


@pytest.mark.parametrize(
    "call",
    [
        lambda: use_cases.identify_actors({"classified": []}),
        lambda: use_cases.identify_use_cases({"classified": [], "actors": []}),
        lambda: use_cases.check_coverage({"classified": [], "use_cases": []}),
        lambda: specifications.generate_specs({"use_cases": [], "classified": []}),
        lambda: specifications.check_specs({"use_case_specs": []}),
        lambda: relationships.identify_relationships({"use_cases": [], "actors": []}),
        lambda: relationships.check_relationships({"relationships": {}}),
        lambda: diagram.render_diagram({"use_cases": [], "actors": [], "relationships": {}}),
    ],
)
def test_empty_upstream_output_is_allowed(call):
    """상류가 돌았는데 결과가 없는 것은 판단할 문제가 아니다 — 그대로 통과시킨다."""
    assert call() is not None


def test_a_stage_that_does_not_produce_what_it_declared_fails_at_the_stage():
    """**범인을 그 자리에서 잡는다.**

    선언한 산출물을 안 내면, 그 사실은 하류가 `MissingUpstreamState`로 죽을 때에야
    드러난다 — 그런데 그 메시지는 **하류 단계의 이름**을 가리킨다. 진짜 범인은 아무것도
    안 낸 상류이고, 그 어긋남이 배선 오류 디버깅을 어렵게 만든다.
    """
    from app.requirements.common.state_contract import BrokenStageOutput, contract

    @contract("liar", requires=("x",), produces=("y",))
    def liar(_state):
        return {"phase": "liar"}      # y 를 안 낸다

    with pytest.raises(BrokenStageOutput) as excinfo:
        liar({"x": 1})
    assert excinfo.value.stage == "liar"
    assert excinfo.value.missing == ["y"]


def test_declaring_output_is_a_floor_not_an_exact_set():
    """선언에 없는 키를 더 내는 것은 괜찮다.

    단계는 기록용 키(`phase` 등)를 함께 낸다. 정확히 일치를 요구하면 선언이 산출물의
    사본이 되고, 사본은 갈린다 — 지켜야 하는 것은 "선언한 것은 반드시 낸다"뿐이다.
    """
    from app.requirements.common.state_contract import contract

    @contract("generous", requires=("x",), produces=("y",))
    def generous(_state):
        return {"y": 1, "phase": "generous", "extra": 2}

    assert generous({"x": 1})["extra"] == 2
