"""verbose 모드: 에이전트의 도구 호출·인자·결과·토큰 사용량을 콘솔에 표시.

Runner.run은 최종 답변만 반환해 중간 과정이 보이지 않으므로,
verbose 모드에서는 Runner.run_streamed의 이벤트 스트림을 구독해
도구 호출(로컬 @function_tool + MCP 도구 모두)과 그 결과를 실시간 출력한다.
도구 자체의 print와 달리 LLM이 실제로 넘긴 인자(arguments)까지 보여
호출이 의도대로 이뤄졌는지 검증할 수 있다.

일반 질의/답변과 구별되도록 verbose 라인에는 ANSI 색을 입히고
블록 시작/끝에 빈 줄을 넣는다. 색은 stdout이 터미널일 때만 적용하며
NO_COLOR(끄기)/FORCE_COLOR(켜기) 환경변수를 존중한다.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from agents import Agent, Runner

# SDK 기본값(10)은 도구를 여러 번 호출하는 질의에 부족하다
# (계획 수립 + 도구 3~4회 + 요약이면 이미 한계에 닿는다).
DEFAULT_MAX_TURNS = 20

# 긴 도구 결과(예: 삭제 영향 477건)로 콘솔이 밀리지 않도록 자른다.
_ARGS_PREVIEW = 200
_OUTPUT_PREVIEW = 400

_RESET = "\x1b[0m"

#: **메시지 유형 → 색.** 색은 장식이 아니라 분류다 — 실패와 "신호"(실패는 아닌데
#: 봐야 하는 것)를 같은 색으로 칠하면 화면을 훑을 때 둘이 붙어 버린다.
#:
#: 프로브 하네스도 이 표를 쓴다(`tools/agent_probe.py`). 색 판단 로직을 두 곳에 두면
#: 한쪽만 NO_COLOR를 존중하는 식으로 갈린다.
#: **흐리게(`\x1b[2m`)는 쓰지 않는다.** 터미널·배색에 따라 회색이 배경에 묻혀 안
#: 보인다(사용자 지적 2026-07-29). 배경으로 물러나야 하는 것은 색을 빼서(기본색)
#: 물러나게 하고, 구별이 필요한 것은 **읽히는 색**으로 가른다.
_STYLES = {
    "agent": "\x1b[35m",       # 마젠타: 에이전트 시작
    "tool_call": "\x1b[36m",   # 시안: 도구 호출
    "tool_output": "\x1b[34m",  # 파랑: 도구 결과 (호출과 갈리되 읽힌다)
    "usage": "\x1b[33m",       # 노랑: 토큰 사용량
    # --- 프로브 하네스가 쓰는 유형 ---
    "pass": "\x1b[32m",        # 초록: 통과
    "fail": "\x1b[31m",        # 빨강: 실패 — 틀리면 진짜 결함인 것만
    "flaky": "\x1b[33m",       # 노랑: 불안정 — 통과했지만 회차마다 뒤집힌다
    "signal": "\x1b[35m",      # 마젠타: 실패는 아니지만 봐야 하는 신호(주장 대조·누출)
    "header": "\x1b[1m",       # 굵게: 구획
    # 답변 본문은 **색을 빼서** 물러나게 한다 — 화면에서 가장 긴 덩어리라 색을 입히면
    # 오히려 다른 신호를 덮는다.
    "answer": "",
}

_vt_enabled = False


def _enable_vt_on_windows() -> None:
    """구형 Windows 콘솔(conhost)의 ANSI 처리를 켠다 (no-op 트릭)."""
    global _vt_enabled
    if not _vt_enabled and os.name == "nt":
        os.system("")
        _vt_enabled = True


def use_color() -> bool:
    """verbose 라인에 색을 입힐지 결정한다."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def paint(text: str, kind: str, color: bool = True) -> str:
    """메시지 유형에 맞는 색을 입힌다. `color=False`면 원문 그대로.

    모르는 유형은 **색 없이 통과시킨다** — 색을 못 칠하는 것보다 KeyError로 출력이
    통째로 죽는 것이 나쁘다.
    """
    if not color or not _STYLES.get(kind):
        return text
    _enable_vt_on_windows()
    return f"{_STYLES[kind]}{text}{_RESET}"


#: 내부 호출부 호환용 별칭.
_paint = paint


def _preview(text: str, limit: int) -> str:
    flattened = " ".join(str(text).split())
    if len(flattened) <= limit:
        return flattened
    return f"{flattened[:limit]} …(+{len(flattened) - limit} chars)"


def describe_event(event: Any, *, color: bool = False) -> str | None:
    """스트림 이벤트를 사람이 읽을 한 줄로 요약한다. 표시할 게 없으면 None."""
    if getattr(event, "type", None) == "agent_updated_stream_event":
        return _paint(f"[verbose] agent start: {event.new_agent.name}", "agent", color)
    if getattr(event, "type", None) != "run_item_stream_event":
        return None
    item = event.item
    item_type = getattr(item, "type", None)
    if item_type == "tool_call_item":
        raw = item.raw_item
        name = getattr(raw, "name", None) or type(raw).__name__
        args = getattr(raw, "arguments", "") or ""
        return _paint(
            f"[verbose] tool call → {name}({_preview(args, _ARGS_PREVIEW)})",
            "tool_call",
            color,
        )
    if item_type == "tool_call_output_item":
        return _paint(
            f"[verbose] tool result ← {_preview(str(item.output), _OUTPUT_PREVIEW)}",
            "tool_output",
            color,
        )
    if item_type == "handoff_call_item":
        return _paint("[verbose] handoff call", "tool_call", color)
    return None


def describe_usage(usage: Any, *, color: bool = False) -> str:
    line = (
        f"[verbose] tokens: input {usage.input_tokens:,} + output {usage.output_tokens:,} "
        f"= total {usage.total_tokens:,} ({usage.requests} LLM requests)"
    )
    return _paint(line, "usage", color)


async def run_agent(
    agent: Agent,
    input_items: list,
    *,
    verbose: bool = False,
    max_turns: int = DEFAULT_MAX_TURNS,
    context: Any = None,
):
    """에이전트를 실행한다. verbose=True면 중간 이벤트를 출력하는 스트리밍 실행.

    반환값은 Runner.run과 동일하게 final_output / to_input_list()를 제공한다.
    턴 한도를 넘으면 agents.exceptions.MaxTurnsExceeded가 그대로 올라온다.

    Args:
        context: 요청 단위 상태(SessionState). 도구 게이팅에 쓰인다.
    """
    if not verbose:
        return await Runner.run(agent, input_items, max_turns=max_turns, context=context)

    color = use_color()
    result = Runner.run_streamed(agent, input_items, max_turns=max_turns, context=context)
    print()  # verbose 블록 시작 구분
    async for event in result.stream_events():
        line = describe_event(event, color=color)
        if line:
            print(line)
    print(describe_usage(result.context_wrapper.usage, color=color))
    print()  # verbose 블록 끝 구분
    return result
