"""Timing and provider-count contracts for class-design cache invocations."""

from __future__ import annotations

from app.design.services.class_diagram.cache import CacheResult, record_cache_outcome
from app.design.services.common.structured import (
    capture_llm_timings,
    record_llm_timing,
)
from app.orchestration.adapters.design import DesignAdapter
from app.orchestration.contracts import RunMode, StepContext
from app.orchestration.providers import MemberDesignProvider


def test_design_invocation_replaces_session_events_between_start_and_resume():
    """A resumed invocation exposes only its own events, not session history."""

    adapter = object.__new__(DesignAdapter)
    adapter._timings = {}

    adapter._invoke_with_timings(
        "session-1",
        lambda: record_llm_timing("start", status="completed"),
    )
    assert [event["operation"] for event in adapter.timing_events("session-1")] == [
        "start",
    ]

    adapter._invoke_with_timings(
        "session-1",
        lambda: record_llm_timing("resume", status="completed"),
    )
    assert [event["operation"] for event in adapter.timing_events("session-1")] == [
        "resume",
    ]


def test_logical_cache_timing_does_not_count_as_a_provider_request():
    with capture_llm_timings() as events:
        record_cache_outcome(
            CacheResult({"accepted": True}, "hit", "cache-key"),
            operation="ClassInventory",
            unit="inventory",
        )
        record_llm_timing(
            "ClassOperation",
            status="completed",
            metadata={"physicalRequest": True},
        )

    assert events[0]["physicalRequest"] is False
    assert events[0]["cacheStatus"] == "hit"

    class FakeAdapter:
        def has_pending(self, *, session_id):
            return False

        def start(self, *, session_id, requirements_result):
            return {
                "status": "completed",
                "llm_timing_events": events,
            }

        def timing_events(self, session_id):
            return events

    result = MemberDesignProvider(FakeAdapter()).run(
        {"requirements_result": {}},
        StepContext(run_id="run-1", app_id="app-1", mode=RunMode.INTERACTIVE),
    )
    assert result.metrics["llm_calls"] == 1
