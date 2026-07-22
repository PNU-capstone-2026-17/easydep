"""도구를 건너뛸 때 **답은 맞는가** — 지명→리전코드.

    python tools/region_memory_experiment.py --runs 10

## 왜 이걸 재나

앞 실험에서 `R2`(도쿄 리전 코드)가 **도구 수와 무관하게 30% 안팎**으로 나왔다.
즉 모델은 열 번 중 일곱 번 도구를 건너뛰고 기억으로 답한다. 프로브가 재시도로
가려 왔을 뿐이다.

**"도구를 불렀나"는 대리 지표다.** 진짜 질문은 이것이다 —

    도구를 건너뛰었을 때 **답이 맞았나?**

맞았다면 이건 스타일 문제이지 결함이 아니다. 틀렸다면 근거 없는 단정이고,
이 저장소가 줄곧 막아 온 바로 그 실패다.

## 그래서 흔한 것과 드문 것을 섞는다

모델의 기억은 고르지 않다. `도쿄 → ap-northeast-1`은 어디에나 있는 사실이고,
`NHN 한국 → kr1`이나 `NCP 한국 → kr`은 훨씬 드물다. **드문 쪽에서 갈린다면**
도구를 건너뛰는 습관의 대가가 드러난다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from agents import Runner
from agents.exceptions import MaxTurnsExceeded

from kbcommon.console import use_utf8
from nim_agent.agent import build_agent
from nim_agent.session import SessionState


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    correct: tuple[str, ...]
    """정답 리전 코드. **우리 리전 카탈로그에서 확인한 것만 넣는다.**"""
    rarity: str


CASES = (
    # 프로바이더를 밝힌 물음 — 실측 결과 도구 호출 50/50, 정답 50/50
    Case("명시-흔함", "AWS 도쿄 리전 코드가 뭐야?", ("ap-northeast-1",), "프로바이더 명시"),
    Case("명시-드묾", "NCP(네이버 클라우드) 한국 리전 코드가 뭐야?", ("kr",), "프로바이더 명시"),
    # R2 원문 — 프로바이더가 없고, 물음이 배포 이야기에 얹혀 있다
    Case("R2원문", "우리 서비스는 도쿄에 배포할 건데 리전 코드가 뭐야?",
         ("ap-northeast-1",), "프로바이더 없음"),
    # 프로바이더만 넣고 나머지는 R2와 같게 — **문장 하나 차이를 격리한다**
    Case("R2+프로바이더", "우리 서비스는 AWS 도쿄에 배포할 건데 리전 코드가 뭐야?",
         ("ap-northeast-1",), "프로바이더 명시"),
)

_TOOL = "cap_resolve_region"


def _tools_called(result) -> list[str]:
    out = []
    for item in result.new_items:
        if getattr(item, "type", "") != "tool_call_item":
            continue
        name = getattr(getattr(item, "raw_item", None), "name", None)
        if name:
            out.append(name)
    return out


def _hit(answer: str, correct: tuple[str, ...]) -> bool:
    """정답 코드가 답변에 있나. 대소문자·구분자 흔들림을 흡수한다."""
    low = re.sub(r"[\s‑–—-]+", "-", answer.lower())
    return any(re.sub(r"[\s‑–—-]+", "-", c.lower()) in low for c in correct)


async def run_once(agent, case: Case) -> tuple[bool, bool]:
    """(도구 불렀나, 답 맞았나)."""
    try:
        result = await Runner.run(
            agent, [{"role": "user", "content": case.query}],
            max_turns=8, context=SessionState(),
        )
    except MaxTurnsExceeded:
        return False, False
    except Exception:  # noqa: BLE001
        return False, False
    called = _TOOL in _tools_called(result)
    return called, _hit(str(result.final_output or ""), case.correct)


async def main_async(runs: int) -> int:
    agent = build_agent()
    print(f"각 질의 {runs}회\n")
    print(f"  {'질의':10}{'':10}{'도구호출':>9}{'정답':>7}"
          f"{'도구O·정답':>11}{'도구X·정답':>11}")
    totals = {"tool_right": [0, 0], "notool_right": [0, 0]}
    for case in CASES:
        called_n = right_n = 0
        with_tool = [0, 0]
        without = [0, 0]
        for _ in range(runs):
            called, right = await run_once(agent, case)
            called_n += called
            right_n += right
            bucket = with_tool if called else without
            bucket[1] += 1
            bucket[0] += right
        totals["tool_right"][0] += with_tool[0]
        totals["tool_right"][1] += with_tool[1]
        totals["notool_right"][0] += without[0]
        totals["notool_right"][1] += without[1]

        def pct(hit, n):
            return f"{hit}/{n}" if n else "-"

        print(
            f"  {case.id:10}{case.rarity:10}{called_n:6}/{runs:<3}"
            f"{right_n:5}/{runs:<3}{pct(*with_tool):>11}{pct(*without):>11}"
        )

    print("\n" + "=" * 68)
    tr, tn = totals["tool_right"]
    nr, nn = totals["notool_right"]
    print(f"  도구를 부른 경우   정답 {tr}/{tn}" + (f" ({tr/tn:.0%})" if tn else ""))
    print(f"  도구를 건너뛴 경우 정답 {nr}/{nn}" + (f" ({nr/nn:.0%})" if nn else ""))
    print(
        "\n※ 두 비율이 같으면 도구 호출은 스타일 문제다.\n"
        "  건너뛴 쪽이 낮으면 **근거 없는 단정의 대가**가 숫자로 드러난 것이다."
    )
    return 0


def main(argv=None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(prog="region_memory_experiment")
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args.runs))


if __name__ == "__main__":
    sys.exit(main())
