"""영어 질의 회귀 — **옵트인**. 실제 모델을 호출한다.

    RUN_AGENT_TESTS=1 uv run pytest app/deployment/tests/test_agent_regression_en.py -v

`test_agent_regression.py`와 같은 이유로 기본 실행에서는 건너뛴다(돈·네트워크·
비결정성). 파일을 나눈 이유는 **성격이 다르기 때문**이다.

    test_agent_regression.py     한국어 질의 — 한 번 깨진 적 있는 것을 지킨다 (회귀)
    이 파일                      영어 질의  — 아직 재본 적이 **없는** 것을 연다 (공백)

시스템 타겟은 영어이고 도구 출력·판정문·고지도 영어로 넘어갔는데, 지금까지의
프로브 64건은 전부 한국어 질의다. 즉 **영어로 물었을 때의 증거가 0건**이다.
질의 언어가 라우팅을 바꾼 사례를 이 저장소가 직접 겪었으므로("로컬 SSD 용량"이
용량 축으로 새어 웹검색까지 간 건), 같은 결과를 가정으로 넘길 수 없다.

**첫 실행의 실패를 회귀로 읽지 말 것.** 여기 프로브는 통과 이력이 없다 — 처음
돌리면 어떤 것은 실패할 수 있고, 그건 **발견이지 퇴행이 아니다.** 실패하면
`tools/probe_en.py`의 `PAIRED_WITH`가 가리키는 한국어 짝을 같은 횟수로 돌려
비교한다. 짝이 통과하고 영어만 실패해야 **언어 효과**다.
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
    from app.deployment.tools.agent_probe import run_probes
    from app.deployment.tools.probe_en import PROBES_EN

    if not hasattr(_results, "cache"):
        _results.cache = {
            r.probe.id: r
            for r in asyncio.run(run_probes(PROBES_EN, max_turns=25, retries=1))
        }
    return _results.cache


@pytest.mark.parametrize(
    "probe_id",
    ["EN1", "EN2", "EN3", "EN4", "EN5", "EN6", "EN7", "EN8", "EN9", "EN10"],
)
def test_english_probe(probe_id: str) -> None:
    from app.deployment.tools.probe_en import PAIRED_WITH

    result = _results()[probe_id]
    pair = PAIRED_WITH[probe_id]
    assert not result.error, f"{result.probe.why} — {result.error}"
    assert not result.failures, (
        f"{result.probe.why}\n"
        f"  실패: {'; '.join(result.failures)}\n"
        f"  도구: {' → '.join(result.tools) or '(없음)'}\n"
        f"  답변: {result.answer[:400]}\n"
        f"  → 언어 효과인지 보려면 한국어 짝 {pair}를 같은 --repeat로 돌려 비교할 것"
    )
    if result.flaky:
        pytest.xfail(f"재시도로 통과 — 가끔 틀린다: {result.probe.id}")
