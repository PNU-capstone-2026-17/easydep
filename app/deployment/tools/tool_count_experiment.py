"""도구 수가 라우팅 정확도를 떨어뜨리는가 — 통제 실험.

    python tools/tool_count_experiment.py --runs 12

## 왜 이 실험인가

전체 프로브에서 `R2`(지명→리전코드)가 실패했다. 도구가 29 → 38개로 늘었으니
**"선택지가 늘어 선택이 어려워졌다"**는 가설이 선다. 하지만 경쟁 가설이 있다.

    가설 A  도구 수가 늘어 라우팅이 나빠졌다
    가설 B  R2는 **모델이 기억으로 답할 수 있는 질문**이라 원래부터 도구를 건너뛴다
            (`도쿄 → ap-northeast-1`은 모델이 그냥 안다)

**둘을 가르려면 두 축이 필요하다.**

    도구 수     29개(이번 세션 이전) vs 38개(현재)
    질문 성격   기억으로 답할 수 있는 것 vs 없는 것

가설 A가 맞다면 **두 질문 다** 38개에서 나빠진다.
가설 B가 맞다면 기억으로 답할 수 있는 질문만 나쁘고, 도구 수와는 무관하다.

## 왜 반복하나

한 번 돌려 나온 성공/실패는 신호가 아니다 — 같은 질의도 실행마다 도구 선택이 달라진다
(프로브 하네스가 재시도를 두는 이유가 그것이다). 비율을 봐야 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from agents import Runner
from agents.exceptions import MaxTurnsExceeded

from kbcommon.console import use_utf8
from nim_agent.agent import build_agent
from nim_agent.bundle_tools import BUNDLE_TOOLS
from nim_agent.session import SessionState
from nim_agent.sizing_tools import SIZING_TOOLS
from nim_agent.tools import LOCAL_TOOLS

#: 이번 세션에 더한 것들. 빼면 이전 상태가 된다.
_ADDED_NAMES = (
    {getattr(t, "name", "") for t in BUNDLE_TOOLS}
    | {getattr(t, "name", "") for t in SIZING_TOOLS}
    | {"cap_region_latency", "cap_basic_image"}
)

BEFORE = [t for t in LOCAL_TOOLS if getattr(t, "name", "") not in _ADDED_NAMES]
AFTER = list(LOCAL_TOOLS)


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    want_tool: str
    memorizable: bool
    """모델이 도구 없이도 답할 수 있는 질문인가 — 이게 가설 B의 축이다."""


CASES = (
    Case("R2", "우리 서비스는 도쿄에 배포할 건데 리전 코드가 뭐야?",
         "cap_resolve_region", memorizable=True),
    Case("N3", "n2-highmem-8 메모리 몇 GiB야?",
         "cost_describe_spec", memorizable=False),
    Case("1-2a", "서브넷에서 나중에 못 바꾸는 속성이 뭐야?",
         "cap_immutable_properties", memorizable=False),
)


def _tool_names(result) -> list[str]:
    out = []
    for item in result.new_items:
        if getattr(item, "type", "") != "tool_call_item":
            continue
        raw = getattr(item, "raw_item", None)
        name = getattr(raw, "name", None)
        if name:
            out.append(name)
    return out


async def run_once(agent, query: str) -> list[str]:
    try:
        result = await Runner.run(
            agent, [{"role": "user", "content": query}],
            max_turns=8, context=SessionState(),
        )
        return _tool_names(result)
    except MaxTurnsExceeded:
        return ["(턴 초과)"]
    except Exception as exc:  # noqa: BLE001 — 한 번 실패가 실험을 막지 않게
        return [f"(오류 {type(exc).__name__})"]


async def main_async(runs: int, out: Path | None) -> int:
    arms = {"29개(이전)": BEFORE, "38개(현재)": AFTER}
    print(f"도구 수: " + ", ".join(f"{k}={len(v)}" for k, v in arms.items()))
    print(f"각 조합 {runs}회\n")

    rows: list[dict] = []
    for arm, tools in arms.items():
        agent = build_agent(tools=tools)
        for case in CASES:
            hits = 0
            no_tool = 0
            picked: collections.Counter = collections.Counter()
            for _ in range(runs):
                called = await run_once(agent, case.query)
                picked.update(called or ["(없음)"])
                if case.want_tool in called:
                    hits += 1
                if not called:
                    no_tool += 1
            rows.append({
                "arm": arm, "case": case.id, "memorizable": case.memorizable,
                "runs": runs, "hits": hits, "noTool": no_tool,
                "picked": dict(picked.most_common(4)),
            })
            mark = "기억가능" if case.memorizable else "기억불가"
            print(
                f"  [{arm:11}] {case.id:6} ({mark}) "
                f"기대도구 {hits:2}/{runs}  도구없음 {no_tool:2}  {dict(picked.most_common(3))}"
            )

    print("\n" + "=" * 70)
    print("정리 — 기대 도구 호출률")
    print(f"  {'':8}{'29개':>10}{'38개':>10}   차이")
    for case in CASES:
        a = next(r for r in rows if r["case"] == case.id and r["arm"].startswith("29"))
        b = next(r for r in rows if r["case"] == case.id and r["arm"].startswith("38"))
        ra, rb = a["hits"] / runs, b["hits"] / runs
        mark = "기억가능" if case.memorizable else "기억불가"
        print(f"  {case.id:8}{ra:9.0%}{rb:10.0%}   {rb - ra:+.0%}  ({mark})")

    print(
        "\n※ 가설 A(도구 수)가 맞다면 **두 질문 다** 38개에서 떨어진다.\n"
        "  가설 B(기억으로 답함)가 맞다면 기억 가능한 것만 낮고 도구 수와 무관하다."
    )
    if out:
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n결과 저장: {out}")
    return 0


def main(argv=None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(prog="tool_count_experiment")
    parser.add_argument("--runs", type=int, default=12, help="조합당 반복 횟수")
    parser.add_argument("--out", type=Path, help="결과 JSON 경로")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args.runs, args.out))


if __name__ == "__main__":
    sys.exit(main())
