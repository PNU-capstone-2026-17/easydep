"""app/requirements/common/state_contract.py + 단계별 적용 테스트.

핵심 구분 하나를 고정한다: **키 부재 ≠ 빈 값.**
  - 부재  = 상류가 돌지 않았다 (배선 오류 → 크게 실패)
  - 빈 값 = 상류가 돌았고 결과가 없었다 (정상일 수 있다 → 통과)

이 구분이 없으면 cascade에서 한 단계가 통째로 빠져도 "결과가 0건인 분석"으로 보인다.
"""
import pytest

from app.requirements.agent.steps import step2_usecases as s2
from app.requirements.agent.steps import step3_specifications as s3
from app.requirements.agent.steps import step4_diagram as s4
from app.requirements.common.state_contract import (
    MissingUpstreamState,
    require,
    require_any,
)


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
        (lambda: s2.identify_actors({}), "classified"),
        (lambda: s2.identify_use_cases({"classified": []}), "actors"),
        (lambda: s2.check_coverage({"classified": []}), "use_cases"),
        (lambda: s3.generate_specs({"use_cases": []}), "classified"),
        (lambda: s3.check_specs({}), "use_case_specs"),
        (lambda: s4.identify_relationships({"use_cases": []}), "actors"),
        (lambda: s4.check_relationships({}), "relationships"),
        (lambda: s4.render_diagram({"use_cases": [], "actors": []}), "relationships"),
    ],
)
def test_missing_upstream_state_fails_loudly(call, missing):
    with pytest.raises(MissingUpstreamState) as excinfo:
        call()
    assert missing in excinfo.value.missing


@pytest.mark.parametrize(
    "call",
    [
        lambda: s2.identify_actors({"classified": []}),
        lambda: s2.identify_use_cases({"classified": [], "actors": []}),
        lambda: s2.check_coverage({"classified": [], "use_cases": []}),
        lambda: s3.generate_specs({"use_cases": [], "classified": []}),
        lambda: s3.check_specs({"use_case_specs": []}),
        lambda: s4.identify_relationships({"use_cases": [], "actors": []}),
        lambda: s4.check_relationships({"relationships": {}}),
        lambda: s4.render_diagram({"use_cases": [], "actors": [], "relationships": {}}),
    ],
)
def test_empty_upstream_output_is_allowed(call):
    """상류가 돌았는데 결과가 없는 것은 판단할 문제가 아니다 — 그대로 통과시킨다."""
    assert call() is not None
