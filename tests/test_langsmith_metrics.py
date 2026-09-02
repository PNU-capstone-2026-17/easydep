"""Tests for the optional, built-in-only LangSmith integration."""

from __future__ import annotations

import sys
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from app.metrics import langsmith


def test_trace_scope_is_a_noop_without_explicit_configuration(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    with langsmith.trace_scope("test") as span:
        span.set_usage(input_tokens=3, output_tokens=5)

    assert span.run is None


def test_trace_scope_sends_only_standard_usage_and_metadata(monkeypatch):
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

    class FakeRun:
        def set(self, **kwargs):
            captured["usage"] = kwargs

    @contextmanager
    def fake_context(**kwargs):
        captured["context"] = kwargs
        yield

    @contextmanager
    def fake_trace(**kwargs):
        captured["trace"] = kwargs
        yield FakeRun()

    fake_module = types.SimpleNamespace(
        Client=FakeClient,
        trace=fake_trace,
        tracing_context=fake_context,
    )
    monkeypatch.setitem(sys.modules, "langsmith", fake_module)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls__test")
    monkeypatch.setenv("LANGSMITH_PROJECT", "easydep-test")

    with langsmith.trace_metadata({"app_id": "app-1"}), langsmith.trace_scope(
        "llm-span", run_type="llm", metadata={"agent": "requirements", "run_id": "r1"}
    ) as span:
        span.set_usage(input_tokens=3, output_tokens=5)

    assert captured["trace"] == {
        "name": "llm-span",
        "run_type": "llm",
        "metadata": {
            "service": "easydep",
            "app_id": "app-1",
            "thread_id": "app-1",
            "agent": "requirements",
            "run_id": "r1",
        },
        "project_name": "easydep-test",
        "client": captured["context"]["client"],
    }
    assert captured["usage"] == {
        "usage_metadata": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
    }
    assert captured["context"]["metadata"] == {
        "service": "easydep",
        "app_id": "app-1",
        "thread_id": "app-1",
        "agent": "requirements",
        "run_id": "r1",
    }


def test_app_id_becomes_thread_id_and_crosses_thread_boundary():
    with langsmith.trace_metadata({"app_id": "app-1", "command_id": "command-1"}):
        bound = langsmith.bind_context(lambda: dict(langsmith._TRACE_METADATA.get()))

    with ThreadPoolExecutor(max_workers=1) as executor:
        metadata = executor.submit(bound).result()

    assert metadata == {
        "app_id": "app-1",
        "thread_id": "app-1",
        "command_id": "command-1",
    }


def test_explicit_thread_id_is_not_replaced_by_app_id():
    with langsmith.trace_metadata({"app_id": "app-1", "thread_id": "process-2"}):
        metadata = dict(langsmith._TRACE_METADATA.get())

    assert metadata["thread_id"] == "process-2"
