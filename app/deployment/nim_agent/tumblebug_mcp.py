"""cb-tumblebug MCP 서버 연결 (옵트인 — `main.py --tumblebug`).

cb-tumblebug은 내장 MCP 서버(Streamable HTTP, 기본 http://127.0.0.1:8000/mcp)로
recommend_vm_spec / search_images / create_infra_dynamic 등을 노출한다.

**왜 기본이 꺼져 있나**: MCP의 `recommend_vm_spec`은 costkb의 `cost_recommend_specs`와
**같은 질문에 답하는 다른 소스**다(축이 다른 게 아니다). 둘 다 손에 쥐여주면 에이전트가
무엇을 부를지는 **프롬프트 권고로만** 정해지는데, 이 프로젝트는 프롬프트로 도구 순서를
지시하는 게 안 통한다는 걸 이미 확인했다(계획 게이트를 코드로 만든 이유 —
`nim_agent/session.py` 참고). 실측 없이 그 불확실성을 기본 경로에 끼워 넣지 않는다.
그래서 기본 실행 경로는 costkb 단일화이고, 라이브 축이 필요할 때만 명시적으로 켠다.

costkb가 이 서버의 **오프라인 미러**라 둘은 같은 `spec_infos` 표를 본다 — 어느 쪽을
골라도 스펙 집합은 같고 갈리는 건 가격뿐이다(덤프는 스냅샷이라 드리프트).
자세한 건 `costkb/parsers/tumblebug.py` 참고.

`create_infra_dynamic` 등은 **현재 상태·실행** 축이라 costkb에 대응물이 없다 —
이 서버를 켜야만 쓸 수 있다.

환경변수:
- TUMBLEBUG_MCP_URL       (기본 http://127.0.0.1:8000/mcp)
- TUMBLEBUG_API_USERNAME  (basic auth, 선택)
- TUMBLEBUG_API_PASSWORD  (basic auth, 선택)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import httpx
from agents.mcp import MCPServerStreamableHttp

DEFAULT_URL = "http://127.0.0.1:8000/mcp"


async def close_quietly(server) -> None:
    """MCP 서버를 닫는다. 종료 중 오류는 사용자에게 의미가 없으므로 삼킨다."""
    try:
        await server.cleanup()
    except Exception:  # noqa: BLE001 - 종료 실패로 본래 흐름을 방해하지 않는다.
        pass


@asynccontextmanager
async def optional_tumblebug_mcp():
    """cb-tumblebug MCP 서버를 async 컨텍스트로 연다. 연결 실패 시 None을 yield한다.

    **주의 — try/except가 `yield`를 감싸면 안 된다.** asynccontextmanager는 with 본문에서
    발생한 예외를 `yield` 지점으로 던지기 때문에, 셋업과 본문을 같은 try로 감싸면
    에이전트 실행 중 난 오류(예: Max turns exceeded)가 "MCP 연결 실패"로 둔갑하고,
    두 번째 yield 때문에 진짜 예외가 `RuntimeError: generator didn't stop after athrow()`로
    덮여 사라진다. 그래서 **셋업만** 보호하고 본문 예외는 그대로 전파시킨다.
    """
    url = os.getenv("TUMBLEBUG_MCP_URL", DEFAULT_URL)
    user = os.getenv("TUMBLEBUG_API_USERNAME")
    password = os.getenv("TUMBLEBUG_API_PASSWORD")

    params: dict = {"url": url}
    if user and password:
        params["auth"] = httpx.BasicAuth(user, password)

    server = MCPServerStreamableHttp(
        name="cb-tumblebug",
        params=params,
        cache_tools_list=True,
        client_session_timeout_seconds=15,
    )
    try:
        await server.connect()
    except Exception as exc:  # noqa: BLE001 - 서버가 없으면 costkb 단독으로 폴백한다.
        print(f"[MCP skipped] cb-tumblebug connection failed ({url}): {exc}")
        print("          → specs and prices run on costkb (the cost_* tools).")
        await close_quietly(server)
        yield None
        return

    print(f"[MCP connected] cb-tumblebug ({url})")
    try:
        yield server  # 본문 예외는 가로채지 않고 그대로 전파시킨다.
    finally:
        await close_quietly(server)
