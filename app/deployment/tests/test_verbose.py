"""verbose 모드 이벤트 요약 테스트 (에이전트 실행 없이 가짜 이벤트로 검증)."""

from __future__ import annotations

from types import SimpleNamespace

from app.deployment.nim_agent.verbose import describe_event, describe_usage


def tool_call_event(name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="tool_call_item",
            raw_item=SimpleNamespace(name=name, arguments=arguments),
        ),
    )


def test_tool_call_shows_name_and_arguments() -> None:
    line = describe_event(tool_call_event("kb_creation_order", '{"resource_type": "vm"}'))
    assert "kb_creation_order" in line
    assert '"resource_type": "vm"' in line
    assert line.startswith("[verbose] tool call →")


def test_long_arguments_truncated() -> None:
    line = describe_event(tool_call_event("t", "x" * 500))
    assert "…(+300 chars)" in line
    assert len(line) < 300


def test_tool_output_truncated_and_flattened() -> None:
    event = SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(type="tool_call_output_item", output="줄1\n줄2\n" + "y" * 500),
    )
    line = describe_event(event)
    assert line.startswith("[verbose] tool result ←")
    assert "\n" not in line
    assert "줄1 줄2" in line


def test_agent_updated_event() -> None:
    event = SimpleNamespace(
        type="agent_updated_stream_event",
        new_agent=SimpleNamespace(name="NIM Planner"),
    )
    assert "NIM Planner" in describe_event(event)


def test_uninteresting_events_return_none() -> None:
    assert describe_event(SimpleNamespace(type="raw_response_event")) is None
    message = SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(type="message_output_item"),
    )
    assert describe_event(message) is None


def test_describe_usage_format() -> None:
    usage = SimpleNamespace(
        input_tokens=1234, output_tokens=567, total_tokens=1801, requests=3
    )
    line = describe_usage(usage)
    assert "1,234" in line
    assert "567" in line
    assert "(3 LLM requests)" in line


def test_color_off_by_default() -> None:
    line = describe_event(tool_call_event("t", "{}"))
    assert "\x1b[" not in line


def test_color_applied_when_requested() -> None:
    call = describe_event(tool_call_event("t", "{}"), color=True)
    assert call.startswith("\x1b[36m")  # 시안
    assert call.endswith("\x1b[0m")

    output = SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(type="tool_call_output_item", output="ok"),
    )
    assert describe_event(output, color=True).startswith("\x1b[2m")  # 흐리게

    usage = SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3, requests=1)
    assert describe_usage(usage, color=True).startswith("\x1b[33m")  # 노랑


def test_use_color_respects_env(monkeypatch) -> None:
    from app.deployment.nim_agent.verbose import use_color

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert use_color() is False

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert use_color() is True
