from __future__ import annotations

from typing import Any

import pytest

from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.service import WorkspaceService, WorkspaceStopRequested


def test_monitor_observes_stop_before_another_worker_poll(monkeypatch) -> None:
    service = WorkspaceService()
    command_id = "implementation-command"
    try:
        monkeypatch.setattr(service, "_stop_requested", lambda value: value == command_id)
        monkeypatch.setattr(
            workspace_module.implementation_worker,
            "get",
            lambda _job_id: pytest.fail("a stopped command must not poll the worker again"),
        )

        with pytest.raises(WorkspaceStopRequested):
            service._monitor_implementation(
                {"job_id": "job-1", "app_id": "app-1"}, command_id=command_id
            )
    finally:
        service.shutdown()


def test_cancelled_implementation_command_best_effort_cancels_its_job(monkeypatch) -> None:
    command = {
        "command_id": "implementation-command",
        "app_id": "app-1",
        "stage": "implementation",
        "payload": {"job_id": "job-1", "_stop_requested": True},
    }
    cancelled: list[str] = []
    monkeypatch.setattr(repository, "get_command", lambda _id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "cancel",
        lambda job_id: cancelled.append(job_id),
    )
    monkeypatch.setattr(
        repository,
        "finish_command_honoring_stop",
        lambda command_id, **kwargs: {
            **command,
            "status": "CANCELLED",
            "result": kwargs["cancelled_result"],
        },
    )
    monkeypatch.setattr(repository, "notify_command_changed", lambda *_args, **_kw: None)

    WorkspaceService()._cancel_command(
        "implementation-command", command, "implementation"
    )

    assert cancelled == ["job-1"]


def test_reconcile_completion_uses_atomic_stop_aware_terminal_writer(monkeypatch) -> None:
    command = {
        "command_id": "implementation-command",
        "app_id": "app-1",
        "action": "start_implementation",
        "stage": "implementation",
        "status": "RUNNING",
        "payload": {"job_id": "job-1", "_stop_requested": True},
    }
    job = {"job_id": "job-1", "status": "COMPLETED"}
    terminal_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(workspace_module.implementation_worker, "get", lambda _id: job)

    def finish(_command_id: str, **kwargs: Any) -> dict[str, Any]:
        terminal_calls.append(kwargs)
        return {**command, "status": "CANCELLED", "result": kwargs["cancelled_result"]}

    monkeypatch.setattr(repository, "finish_command_honoring_stop", finish)
    monkeypatch.setattr(repository, "notify_command_changed", lambda *_args, **_kw: None)
    monkeypatch.setattr(repository, "append_progress_event", lambda *_args, **_kw: None)
    monkeypatch.setattr(repository, "list_progress_events", lambda _app_id: [])
    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result["status"] == "CANCELLED"
    assert len(terminal_calls) == 1


def test_testing_progress_checkpoint_acknowledges_stop_before_persisting(monkeypatch) -> None:
    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {},
    }
    service = WorkspaceService()
    persisted: list[dict[str, Any]] = []
    try:
        stop_states = iter((False, True))
        monkeypatch.setattr(service, "_stop_requested", lambda _id: next(stop_states))
        monkeypatch.setattr(
            repository,
            "update_command",
            lambda _id, **changes: persisted.append(changes),
        )

        def run(_app_id: str, _job_id: str, **kwargs: Any) -> dict[str, Any]:
            kwargs["progress"]({"current_node": "queued"})
            return {"job_id": "testing-command", "result": {"passed": True}}

        monkeypatch.setattr(workspace_module, "run_testing", run)
        with pytest.raises(WorkspaceStopRequested):
            service._run_testing_command(command, "implementation-1")
    finally:
        service.shutdown()

    assert persisted == []


def test_testing_owner_needs_input_does_not_retest(monkeypatch) -> None:
    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {},
    }
    previous_job = {"status": "COMPLETED", "result": {"passed": False}}
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "repair_state": {"status": "ACTIVE"},
        "blocking_findings": [
            {
                "repairable": True,
                "defect_class": "SUT_DEFECT",
                "repair_owner": "implementation",
            }
        ],
        "job": previous_job,
    }
    service = WorkspaceService()
    try:
        owner_outcome = {
            "awaiting_input": True,
            "message": "Implementation needs a decision.",
            "job": {"status": "NEEDS_INPUT"},
        }
        monkeypatch.setattr(
            service,
            "_repair_testing_with_owner",
            lambda *_args: (owner_outcome, "implementation-1", "testing-static"),
        )
        monkeypatch.setattr(
            service,
            "_run_testing_command",
            lambda *_args, **_kwargs: pytest.fail("must not retest an unfinished owner repair"),
        )
        monkeypatch.setattr(service, "_stop_requested", lambda _id: False)

        assert service._auto_repair_semantic_result(command, result) == owner_outcome
    finally:
        service.shutdown()
