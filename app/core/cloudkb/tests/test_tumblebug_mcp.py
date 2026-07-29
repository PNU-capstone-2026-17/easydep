"""cb-tumblebug MCP 헬퍼 테스트.

핵심 회귀 방어: **with 본문에서 난 예외를 "MCP 연결 실패"로 오보고하면 안 된다.**
과거에 try/except가 `yield`까지 감싸는 바람에 에이전트 실행 오류(Max turns exceeded)가
MCP 기동 실패로 둔갑하고, 두 번째 yield 때문에 진짜 예외가
`RuntimeError: generator didn't stop after athrow()`로 덮여 사라졌다.

(pytest-asyncio를 추가하지 않고 asyncio.run으로 시나리오를 돌린다.)
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.cloudkb.nim_agent import tumblebug_mcp


class FakeServer:
    """MCPServerStreamableHttp 흉내 (connect/cleanup만 필요)."""

    def __init__(self, *, fail_connect: bool = False, fail_cleanup: bool = False) -> None:
        self.fail_connect = fail_connect
        self.fail_cleanup = fail_cleanup
        self.connected = False
        self.cleaned = False

    async def connect(self) -> None:
        if self.fail_connect:
            raise RuntimeError("연결 거부됨")
        self.connected = True

    async def cleanup(self) -> None:
        self.cleaned = True
        if self.fail_cleanup:
            raise RuntimeError("종료 실패")


@pytest.fixture()
def fake_tumblebug(monkeypatch):
    """MCPServerStreamableHttp를 가짜로 바꾼다."""

    def install(server: FakeServer) -> FakeServer:
        monkeypatch.setattr(tumblebug_mcp, "MCPServerStreamableHttp", lambda **_: server)
        return server

    return install


def test_body_exception_is_not_reported_as_connect_failure(fake_tumblebug, capsys) -> None:
    """본문 예외는 그대로 전파되고, 연결 실패 메시지가 찍히면 안 된다."""
    server = fake_tumblebug(FakeServer())

    async def scenario() -> None:
        async with tumblebug_mcp.optional_tumblebug_mcp() as connected:
            assert connected is server
            raise ValueError("Max turns (10) exceeded")

    with pytest.raises(ValueError, match="Max turns"):
        asyncio.run(scenario())

    stdout = capsys.readouterr().out
    assert "connection failed" not in stdout
    assert "[MCP connected]" in stdout
    assert server.cleaned  # 예외가 나도 정리는 된다


def test_body_exception_type_is_preserved(fake_tumblebug) -> None:
    """진짜 예외가 RuntimeError('generator didn't stop...')로 덮이면 안 된다."""
    fake_tumblebug(FakeServer())

    async def scenario() -> None:
        async with tumblebug_mcp.optional_tumblebug_mcp():
            raise ValueError("원래 오류")

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(scenario())
    assert str(excinfo.value) == "원래 오류"
    assert "generator didn't stop" not in str(excinfo.value)


def test_connect_failure_falls_back_to_none(fake_tumblebug, capsys) -> None:
    """서버가 없으면 None을 주고 costkb 단독으로 폴백한다."""
    server = fake_tumblebug(FakeServer(fail_connect=True))

    async def scenario() -> object:
        async with tumblebug_mcp.optional_tumblebug_mcp() as connected:
            return connected

    assert asyncio.run(scenario()) is None
    stdout = capsys.readouterr().out
    assert "cb-tumblebug connection failed" in stdout
    assert "연결 거부됨" in stdout  # FakeServer가 던진 예외 원문이 그대로 실린다
    assert "costkb" in stdout
    assert server.cleaned  # 실패해도 정리 시도는 한다


def test_cleanup_failure_is_swallowed(fake_tumblebug) -> None:
    """종료 중 오류가 본래 흐름을 방해하면 안 된다."""
    fake_tumblebug(FakeServer(fail_cleanup=True))

    async def scenario() -> object:
        async with tumblebug_mcp.optional_tumblebug_mcp() as connected:
            return connected

    assert asyncio.run(scenario()) is not None  # 예외 없이 빠져나온다


def test_basic_auth_is_attached_only_when_both_given(monkeypatch) -> None:
    """자격증명이 반쪽만 있으면 auth를 붙이지 않는다."""
    captured: list[dict] = []

    def fake_ctor(**kwargs):
        captured.append(kwargs["params"])
        return FakeServer()

    monkeypatch.setattr(tumblebug_mcp, "MCPServerStreamableHttp", fake_ctor)
    monkeypatch.setenv("TUMBLEBUG_API_USERNAME", "default")
    monkeypatch.delenv("TUMBLEBUG_API_PASSWORD", raising=False)

    async def scenario() -> None:
        async with tumblebug_mcp.optional_tumblebug_mcp():
            pass

    asyncio.run(scenario())
    assert "auth" not in captured[0]

    monkeypatch.setenv("TUMBLEBUG_API_PASSWORD", "default")
    asyncio.run(scenario())
    assert "auth" in captured[1]
