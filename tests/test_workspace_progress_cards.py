from datetime import UTC, datetime
from contextlib import contextmanager
from types import SimpleNamespace

from app.workspace import repository


def _card(*, status: str, detail: str = "") -> dict:
    return {
        "id": "testing:command-1:progress",
        "stage": "testing",
        "order": 3,
        "label": "Testing progress",
        "status": status,
        "tasks": [
            {
                "id": "run-verification",
                "label": "Run application verification",
                "order": 1,
                "status": status,
                "detail": detail,
            }
        ],
    }


def test_progress_card_merge_keeps_stable_ids_and_does_not_rewind_waiting() -> None:
    observed = repository._merge_progress_card(None, _card(status="running", detail="Checking"))
    event_id = 123
    observed["event_id"] = event_id
    observed["created_at"] = "2026-09-19T00:00:00+09:00"

    merged = repository._merge_progress_card(observed, _card(status="waiting"))

    assert merged["id"] == "testing:command-1:progress"
    assert merged["event_id"] == event_id
    assert merged["tasks"] == [
        {
            "id": "run-verification",
            "label": "Run application verification",
            "order": 1,
            "status": "running",
            "detail": "Checking",
        }
    ]


def test_waiting_patch_does_not_rewind_a_completed_task() -> None:
    merged = repository._merge_progress_card(
        _card(status="completed", detail="Done"), _card(status="waiting")
    )

    assert merged["status"] == "completed"
    assert merged["tasks"][0]["status"] == "completed"
    assert merged["tasks"][0]["detail"] == "Done"


def test_stage_adapter_builds_a_stable_card_for_each_workspace_stage() -> None:
    for stage in ("requirements", "design", "implementation", "testing"):
        cards = repository._stage_progress_card(
            stage,
            "command-1",
            {
                "progress_event": f"{stage}StepUpdated",
                "step": "one-step",
                "progress_step_label": "One step",
                "progress_status": "running",
            },
        )
        assert cards[0]["id"] == f"{stage}:command-1:progress"
        assert cards[0]["tasks"][0]["id"] == "one-step"


def test_refresh_projection_uses_one_durable_snapshot_and_preserves_testing_terminal() -> None:
    created_at = datetime(2026, 9, 19, 0, 0, tzinfo=UTC).replace(tzinfo=None)
    row = SimpleNamespace(
        app_id="app-1",
        command_id="command-1",
        action="start_testing",
        stage="testing",
        status="COMPLETED",
        payload={
            repository._TIMELINE_PROGRESS_KEY: {
                "version": 1,
                "revision": 2,
                "cards": [_card(status="completed", detail="Verification gates finished.")],
            },
            "testing_checkpoint": {"current_node": "verification_complete"},
        },
        result={"kind": "status", "message": "Testing complete.", "passed": True},
        error=None,
        created_at=created_at,
        started_at=created_at,
        completed_at=created_at,
    )

    events = repository._command_timeline_events(row)
    durable = [
        event for event in events if event["metadata"].get("progress_event") == "durableProgressCards"
    ]
    legacy_testing = [
        event for event in events if event["metadata"].get("progress_event") == "testingStepUpdated"
    ]

    assert len(durable) == 1
    assert durable[0]["metadata"]["progress_cards"]["cards"][0]["status"] == "completed"
    assert legacy_testing == []
    assert events[-1]["text"] == "Testing complete."
    assert durable[0]["event_id"] < events[-1]["event_id"]


def test_legacy_payload_write_preserves_the_progress_namespace(monkeypatch) -> None:
    created_at = datetime(2026, 9, 19, 0, 0)
    row = SimpleNamespace(
        app_id="app-1",
        command_id="command-1",
        action="start_testing",
        stage="testing",
        status="RUNNING",
        payload={"ordinary": "old", repository._TIMELINE_PROGRESS_KEY: {"revision": 3}},
        result=None,
        error=None,
        created_at=created_at,
        started_at=None,
        completed_at=None,
    )

    class Session:
        def scalar(self, _query):
            return row

        def flush(self):
            pass

    @contextmanager
    def fake_scope():
        yield Session()

    monkeypatch.setattr(repository, "session_scope", fake_scope)

    repository.update_command("command-1", payload={"ordinary": "new", "checkpoint": 1})

    assert row.payload == {
        "ordinary": "new",
        "checkpoint": 1,
        repository._TIMELINE_PROGRESS_KEY: {"revision": 3},
    }


def test_terminal_command_rejects_late_durable_patch(monkeypatch) -> None:
    row = SimpleNamespace(app_id="app-1", command_id="command-1", status="COMPLETED")

    class Session:
        def scalar(self, _query):
            return row

        def flush(self):
            raise AssertionError("terminal command must not be mutated")

    @contextmanager
    def fake_scope():
        yield Session()

    monkeypatch.setattr(repository, "session_scope", fake_scope)

    assert repository.publish_progress_cards(
        "app-1", command_id="command-1", stage="testing", cards=[_card(status="running")]
    ) == {}
