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
_STYLES = {
    "agent": "\x1b[35m",  # 마젠타: 에이전트 시작
    "tool_call": "\x1b[36m",  # 시안: 도구 호출
    "tool_output": "\x1b[2m",  # 흐리게: 도구 결과
    "usage": "\x1b[33m",  # 노랑: 토큰 사용량
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


def _paint(text: str, kind: str, color: bool) -> str:
    if not color:
        return text
    _enable_vt_on_windows()
    return f"{_STYLES[kind]}{text}{_RESET}"


def _preview(text: str, limit: int) -> str:
    flattened = " ".join(str(text).split())
    if len(flattened) <= limit:
        return flattened
    return f"{flattened[:limit]} …(+{len(flattened) - limit}자)"


def describe_event(event: Any, *, color: bool = False) -> str | None:
    """스트림 이벤트를 사람이 읽을 한 줄로 요약한다. 표시할 게 없으면 None."""
    if getattr(event, "type", None) == "agent_updated_stream_event":
        return _paint(f"[verbose] 에이전트 시작: {event.new_agent.name}", "agent", color)
    if getattr(event, "type", None) != "run_item_stream_event":
        return None
    item = event.item
    item_type = getattr(item, "type", None)
    if item_type == "tool_call_item":
        raw = item.raw_item
        name = getattr(raw, "name", None) or type(raw).__name__
        args = getattr(raw, "arguments", "") or ""
        return _paint(
            f"[verbose] 도구 호출 → {name}({_preview(args, _ARGS_PREVIEW)})",
            "tool_call",
            color,
        )
    if item_type == "tool_call_output_item":
        return _paint(
            f"[verbose] 도구 결과 ← {_preview(str(item.output), _OUTPUT_PREVIEW)}",
            "tool_output",
            color,
        )
    if item_type == "handoff_call_item":
        return _paint("[verbose] 핸드오프 호출", "tool_call", color)
    return None


def describe_usage(usage: Any, *, color: bool = False) -> str:
    line = (
        f"[verbose] 토큰 사용: 입력 {usage.input_tokens:,} + 출력 {usage.output_tokens:,} "
        f"= 총 {usage.total_tokens:,} (LLM 요청 {usage.requests}회)"
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
