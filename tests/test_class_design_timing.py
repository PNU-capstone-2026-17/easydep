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


def test_design_invocation_replaces_session_events_between_start_and_resume(tmp_path):
    """A resumed invocation exposes only its own events, not session history."""

    class GraphState:
        next = ()

    class RecordingGraph:
        def __init__(self):
            self.calls = 0

        def invoke(self, _value, _config):
            self.calls += 1
            operation = "start" if self.calls == 1 else "resume"
            record_llm_timing(operation, status="completed")
            return {}

        def get_state(self, _config):
            return GraphState()

    adapter = DesignAdapter(tmp_path / "design-checkpoint.sqlite")
    adapter.graph = RecordingGraph()
    requirements_result = {
        "actors": [{"name": "Member"}],
        "requirements": [{"id": "FR1"}],
        "use_cases": [{
            "id": "UC1",
            "primary_actor": "Member",
            "requirement_ids": ["FR1"],
        }],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "trigger": "The member submits a request.",
            "main_scenario": [{"covered_req_ids": ["FR1"]}],
            "success_guarantee": "The request is accepted.",
            "generated": True,
        }],
        "relationships": {},
    }

    adapter.start(session_id="session-1", requirements_result=requirements_result)
    assert [event["operation"] for event in adapter.timing_events("session-1")] == [
        "start",
    ]

    adapter.resume(session_id="session-1", feedback="continue")
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
