"""의미 규칙 눈금을 실제 NIM으로 재는 테스트 (옵트인).

    RUN_LIVE_TESTS=1 python -m pytest tests/test_live_evaluation.py -s

**CI 기본 실행에서는 제외된다** — LLM 판정은 결정론이 아니라서 한 번 실패한 것이 코드
잘못이라고 말할 수 없다. 그래도 재는 것 자체는 할 수 있고, 재지 않으면 의미 규칙에 대한
모든 "결함 0건"이 근거 없는 0이 된다.

그래서 여기서 주장하는 것은 **정확도가 아니라 눈금이 살아 있는지**다:
  - 심어 둔 결함을 N회 중 한 번이라도 잡는가(0/N이면 눈금이 죽었다).
  - 결함 없는 산출물에 지적을 쏟아내지 않는가(오탐이 잦으면 실행 비교가 오염된다).

수치 자체(검출률 2/3 대 3/3)는 표본이 작아 잡음이다. 프롬프트 전후를 비교하려면 같은 N으로
`python -m app.requirements.evaluation semantic --repeats <더 큰 N>`을 두 번 돌려 본다.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="라이브 의미 눈금 측정은 RUN_LIVE_TESTS=1 일 때만 실행",
)

#: 한 케이스가 잡히는지 보려면 몇 번 돌릴지. 늘리면 판정이 안정되지만 그만큼 호출이 늘어난다.
REPEATS = int(os.getenv("SEMANTIC_REPEATS", "3"))


@pytest.fixture(scope="module")
def report():
    from app.requirements.config import settings
    from app.requirements.evaluation import semantic

    if not settings.enable_semantic_validator:
        pytest.skip("enable_semantic_validator=False — 이대로 재면 전부 0/N으로 나온다")
    result = semantic.measure(repeats=REPEATS)
    print("\n[의미 규칙 눈금]")
    for case in result["cases"]:
        print(f"  {case['rate']:>5.0%}  {case['rule_id']}")
    for control in result["controls"]:
        print(f"  대조군 {control['stage']}: 오탐률 {control['false_positive_rate']:.0%}")
    return result


def test_no_semantic_gauge_is_completely_dead(report):
    """0/N인 규칙이 있으면 그 규칙의 판정은 신뢰할 수 없다."""
    dead = report["dead_gauges"]
    assert dead == [], (
        f"심어 둔 결함을 {REPEATS}회 중 한 번도 못 잡은 규칙: {dead}. "
        "이 규칙에 대한 '결함 0건'은 근거가 없다."
    )


def test_the_clean_controls_are_mostly_left_alone(report):
    """대조군에 매번 지적이 나오면 실행끼리의 비교가 오염된다."""
    noisy = [
        c for c in report["controls"] if c["false_positive_rate"] > 0.5
    ]
    assert noisy == [], f"결함 없는 산출물에 절반 넘게 지적이 나온다: {noisy}"


def test_every_validator_rule_is_actually_measured(report):
    """측정에서 빠진 의미 규칙이 있으면 그 규칙은 눈금이 없는 것과 같다."""
    from app.requirements.evaluation import seeded

    assert seeded.unseeded_validator_rules() == []
    assert {c["rule_id"] for c in report["cases"]} == {
        c.rule_id for c in seeded.SEEDED_SEMANTIC
    }
