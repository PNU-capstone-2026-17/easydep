"""진입점 — NIM으로 구동되는 자율 계획형 에이전트와의 대화형 루프.

실행:
    uv run python main.py
    uv run python main.py --verbose          # 도구 호출·인자·결과·토큰 사용량 표시
    uv run python main.py --max-turns 40     # 도구를 많이 부르는 질의용
    uv run python main.py --tumblebug        # cb-tumblebug MCP(라이브 축) 연결

기본 실행 경로는 **로컬 지식베이스 단독**이다(graphkb·capacitykb·costkb). cb-tumblebug
MCP는 `--tumblebug`을 줄 때만 연결한다 — 이유는 `nim_agent/tumblebug_mcp.py` 참고.

멀티턴 대화 히스토리는 result.to_input_list()로 이어붙인다.
'exit' / 'quit' / 빈 줄 또는 Ctrl+D/Ctrl+C로 종료.
플래그는 환경변수로도 켤 수 있다: NIM_AGENT_VERBOSE=1 / NIM_AGENT_TUMBLEBUG=1.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import AsyncExitStack

from agents.exceptions import MaxTurnsExceeded

from nim_agent.agent import build_agent
from nim_agent.session import SessionState
from nim_agent.tumblebug_mcp import optional_tumblebug_mcp
from nim_agent.verbose import DEFAULT_MAX_TURNS, run_agent


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NIM Planner Agent 대화형 루프")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=_env_flag("NIM_AGENT_VERBOSE"),
        help="도구 호출·인자·결과·토큰 사용량을 실시간 표시 (NIM_AGENT_VERBOSE=1과 동일)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help=f"한 요청에서 허용할 최대 에이전트 턴 수 (기본 {DEFAULT_MAX_TURNS})",
    )
    parser.add_argument(
        "--tumblebug",
        action="store_true",
        default=_env_flag("NIM_AGENT_TUMBLEBUG"),
        help=(
            "cb-tumblebug MCP를 연결해 라이브 스펙 추천·인프라 실행 도구를 추가한다 "
            "(기본 꺼짐, NIM_AGENT_TUMBLEBUG=1과 동일). 서버가 없으면 자동 폴백한다."
        ),
    )
    return parser.parse_args()


async def main(
    verbose: bool = False,
    max_turns: int = DEFAULT_MAX_TURNS,
    tumblebug: bool = False,
) -> None:
    async with AsyncExitStack() as stack:
        mcp_servers = []
        if tumblebug:
            server = await stack.enter_async_context(optional_tumblebug_mcp())
            if server is not None:
                mcp_servers.append(server)
        agent = build_agent(mcp_servers=mcp_servers)

        flags = " [verbose]" if verbose else ""
        print(f"NIM Planner Agent 준비 완료{flags}. 요청을 입력하세요. (종료: exit)\n")
        history: list = []
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user.lower() in {"exit", "quit", ""}:
                break

            try:
                result = await run_agent(
                    agent,
                    history + [{"role": "user", "content": user}],
                    verbose=verbose,
                    max_turns=max_turns,
                    # 요청마다 새 상태 — 앞 요청의 계획이 이번 게이트를 열면 안 된다.
                    context=SessionState(),
                )
            except MaxTurnsExceeded:
                # 히스토리는 그대로 두고 다음 입력을 받는다 (대화가 끊기지 않게).
                print(
                    f"agent> 도구 호출이 {max_turns}턴을 넘어 중단했습니다. "
                    "질문을 더 좁게 나누거나 --max-turns 값을 올려 다시 시도해 보세요.\n"
                )
                continue
            print("agent>", result.final_output, "\n")
            history = result.to_input_list()


if __name__ == "__main__":
    _args = _parse_args()
    asyncio.run(
        main(
            verbose=_args.verbose,
            max_turns=_args.max_turns,
            tumblebug=_args.tumblebug,
        )
    )
