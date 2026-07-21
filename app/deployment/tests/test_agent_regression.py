"""에이전트 회귀 — **옵트인**. 실제 모델을 호출한다.

    RUN_AGENT_TESTS=1 uv run pytest tests/test_agent_regression.py -v

기본 실행에서는 건너뛴다. 이유가 셋이다:
- 실제 API를 호출한다(돈과 시간이 든다 — 9건에 1분 남짓).
- 네트워크가 필요하다. 나머지 592개 테스트는 네트워크 없이 돈다.
- **비결정성이 있다.** 같은 질의도 실행마다 도구 순서가 달라져서, 기본 스위트에
  넣으면 "실패가 일상"이 되고 그러면 진짜 실패가 안 보인다.

그래도 테스트로 두는 이유: 여기서 지키는 것들은 **한 번 깨진 적이 있는 것들**이다.
`cap_check_value`가 `context`를 못 받아 조건부 판정이 통째로 안 쓰이던 것,
낡음 고지가 답변에서 사라지던 것 — 둘 다 실측으로만 발견됐고 단위 테스트는
전부 통과하고 있었다.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AGENT_TESTS") != "1",
    reason="실제 모델을 호출한다. RUN_AGENT_TESTS=1 로 켠다.",
)


def _results():
    from tools.agent_probe import PROBES, run_probes

    if not hasattr(_results, "cache"):
        # 질의 하나당 한 번만 태운다 — 테스트마다 다시 부르면 9배가 된다.
        _results.cache = {
            r.probe.id: r
            for r in asyncio.run(run_probes(PROBES, max_turns=25, retries=1))
        }
    return _results.cache


@pytest.mark.parametrize("probe_id", [
    "1-1b", "1-2a", "3-1", "3-3", "3-6", "3-9", "3-11", "D4", "D5", "N1", "N2", "H1", "H2",
])
def test_probe(probe_id: str) -> None:
    result = _results()[probe_id]
    assert not result.error, f"{result.probe.why} — {result.error}"
    assert not result.failures, (
        f"{result.probe.why}\n"
        f"  실패: {'; '.join(result.failures)}\n"
        f"  도구: {' → '.join(result.tools) or '(없음)'}\n"
        f"  답변: {result.answer[:400]}"
    )
    if result.flaky:
        pytest.xfail(f"재시도로 통과 — 가끔 틀린다: {result.probe.id}")
