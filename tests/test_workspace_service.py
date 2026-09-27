from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.db.models import Base
from app.design import progress as design_progress
from app.design.services.common.structured import record_llm_timing
from app.repositories import artifact_repository
from app.workspace import api as workspace_api
from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.conversation.contracts import RevisionTarget
from app.workspace.live_preview import LivePreviewStore, live_previews
from app.workspace.service import WorkspaceService


def test_transient_requirements_failure_retries_same_command(monkeypatch) -> None:
    command = {
        "command_id": "requirements-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {},
    }
    service = WorkspaceService()
    attempts = iter([TimeoutError("provider timed out"), {"message": "resumed"}])
    updates: list[dict[str, Any]] = []
    try:
        monkeypatch.setattr(repository, "get_command", lambda _id: dict(command))
        monkeypatch.setattr(
            repository, "update_command", lambda _id, **changes: updates.append(changes) or command
        )
        monkeypatch.setattr(repository, "notify_command_changed", lambda *_args, **_kw: None)
        def dispatch(_command):
            value = next(attempts)
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(service, "_dispatch", dispatch)
        monkeypatch.setattr(
            service, "_transient_retry_operation", lambda _command: lambda: next(attempts)
        )
        monkeypatch.setattr(service, "_sleep_for_retry", lambda *_args: None)

        assert service._dispatch_with_transient_retry(command) == {"message": "resumed"}
    finally:
        service.shutdown()

    assert updates == [
        {
            "payload": {
                "_transient_retry": {"attempt": 1, "error_type": "TimeoutError"}
            },
            "error": None,
        }
    ]


def test_startup_resumes_only_a_noninteractive_technical_pause(monkeypatch) -> None:
    technical = {
        "command_id": "design-technical",
        "app_id": "app-1",
        "action": "retry_design",
        "stage": "design",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {
            "awaiting_input": True,
            "requires_revision": True,
            "repair_state": {"status": "ACTIVE", "attempt_count": 0},
            "blocking_findings": [{"repairable": True}],
        },
    }
    question = {
        **technical,
        "command_id": "design-question",
        "result": {
            **technical["result"],
            "feedback_question": {"question_id": "q1"},
        },
    }
    service = WorkspaceService()
    submitted: list[str] = []
    try:
        monkeypatch.setattr(repository, "interrupt_unfinished", lambda: 1)
        monkeypatch.setattr(repository, "interrupted_testing_commands", lambda: [])
        technical["payload"] = {"_technical_repair_retry": {"attempt": 1}}
        monkeypatch.setattr(
            repository, "interrupted_technical_retry_commands", lambda: [technical, question]
        )
        monkeypatch.setattr(service._executor, "submit", lambda _fn, command_id: submitted.append(command_id))
        assert service.startup() == 1
    finally:
        service.shutdown()

    assert submitted == ["design-technical"]


def test_error_without_a_checkpoint_is_not_retried(monkeypatch) -> None:
    command = {
        "command_id": "requirements-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {},
    }
    service = WorkspaceService()
    try:
        monkeypatch.setattr(repository, "get_command", lambda _id: dict(command))
        monkeypatch.setattr(
            service,
            "_dispatch",
            lambda _command: (_ for _ in ()).throw(ValueError("invalid input")),
        )
        monkeypatch.setattr(
            service,
            "_transient_retry_operation",
            lambda _command: None,
        )
        with pytest.raises(ValueError, match="invalid input"):
            service._dispatch_with_transient_retry(command)
    finally:
        service.shutdown()


def test_checkpointed_nontransient_error_retries_the_same_command(monkeypatch) -> None:
    command = {
        "command_id": "design-command",
        "app_id": "app-1",
        "action": "retry_design",
        "stage": "design",
        "payload": {},
    }
    service = WorkspaceService()
    attempts = iter([ValueError("technical validation failed"), {"message": "resumed"}])
    updates: list[dict[str, Any]] = []
    try:
        monkeypatch.setattr(repository, "get_command", lambda _id: dict(command))
        monkeypatch.setattr(
            repository, "update_command", lambda _id, **changes: updates.append(changes) or changes
        )
        monkeypatch.setattr(repository, "notify_command_changed", lambda *_args, **_kw: None)

        def dispatch(_command):
            value = next(attempts)
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(service, "_dispatch", dispatch)
        monkeypatch.setattr(service, "_transient_retry_operation", lambda _command: lambda: next(attempts))
        monkeypatch.setattr(service, "_sleep_for_retry", lambda *_args: None)

        assert service._dispatch_with_transient_retry(command) == {"message": "resumed"}
    finally:
        service.shutdown()

    assert updates == [
        {
            "payload": {
                "_transient_retry": {"attempt": 1, "error_type": "ValueError"}
            },
            "error": None,
        }
    ]


def test_testing_retry_uses_linked_job_before_checkpoint_is_persisted(monkeypatch) -> None:
    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {"implementation_job_id": "implementation-1"},
    }
    service = WorkspaceService()
    observed: dict[str, str] = {}
    try:
        monkeypatch.setattr(repository, "get_command", lambda _id: dict(command))
        monkeypatch.setattr(
            service,
            "_run_testing_command",
            lambda current, job_id: observed.update(
                command_id=str(current["command_id"]), job_id=job_id
            )
            or {"message": "resumed"},
        )
        operation = service._transient_retry_operation(command)
        assert operation is not None
        assert operation() == {"message": "resumed"}
    finally:
        service.shutdown()

    assert observed == {"command_id": "testing-command", "job_id": "implementation-1"}


def test_technical_retry_persists_its_current_awaiting_result(monkeypatch) -> None:
    command = {
        "command_id": "design-command",
        "app_id": "app-1",
        "payload": {},
    }
    current = {"awaiting_input": True, "requires_revision": True}
    updates: list[dict[str, Any]] = []
    service = WorkspaceService()
    try:
        monkeypatch.setattr(repository, "get_command", lambda _id: dict(command))
        monkeypatch.setattr(
            repository, "update_command", lambda _id, **changes: updates.append(changes) or changes
        )
        monkeypatch.setattr(repository, "notify_command_changed", lambda *_args, **_kw: None)
        service._record_technical_retry(
            command, 1, "design", "finding-digest", current
        )
    finally:
        service.shutdown()

    assert updates == [
        {
            "payload": {
                "_technical_repair_retry": {
                    "attempt": 1,
                    "stage": "design",
                    "finding_digest": "finding-digest",
                }
            },
            "result": current,
        }
    ]


def test_stop_during_retry_backoff_cancels_without_another_attempt(monkeypatch) -> None:
    command = {
        "command_id": "requirements-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {},
    }
    service = WorkspaceService()
    state = {"stop": False}
    updates: list[dict[str, Any]] = []
    try:
        def current(_id: str) -> dict[str, Any]:
            return {**command, "payload": {"_stop_requested": state["stop"]}}

        monkeypatch.setattr(repository, "get_command", current)
        monkeypatch.setattr(repository, "update_command", lambda _id, **changes: updates.append(changes) or changes)
        monkeypatch.setattr(
            repository,
            "finish_command_honoring_stop",
            lambda _id, *, status, result, error, cancelled_result: updates.append(
                {"status": status, "result": result, "error": error}
            )
            or {"status": status, "result": result},
        )
        monkeypatch.setattr(repository, "notify_command_changed", lambda *_args, **_kw: None)
        monkeypatch.setattr(repository, "now", lambda: datetime.now(UTC).replace(tzinfo=None))
        monkeypatch.setattr(
            service,
            "_dispatch",
            lambda _command: (_ for _ in ()).throw(TimeoutError("timeout")),
        )
        # The retry adapter is selected before backoff; cancellation must prevent its execution.
        monkeypatch.setattr(service, "_transient_retry_operation", lambda _command: lambda: pytest.fail("no second attempt"))
        monkeypatch.setattr(service, "_record_transient_retry", lambda *_args: None)
        monkeypatch.setattr(workspace_module.random, "uniform", lambda *_args: 1.0)
        monkeypatch.setattr(workspace_module.time, "sleep", lambda _seconds: state.__setitem__("stop", True))

        service._execute_command(command["command_id"], command)
    finally:
        service.shutdown()

    assert updates[-1]["status"] == "CANCELLED"


def test_technical_testing_failure_reuses_its_checkpoint_without_user_action(
    monkeypatch,
) -> None:
    """A technical test pause reuses the existing implementation checkpoint."""

    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {"implementation_job_id": "implementation-1"},
    }
    result = {
        "awaiting_input": True,
        "kind": "action_required",
        "message": "Testing found a blocking failure.",
        "requires_revision": True,
        "job_id": "testing-command",
        "job": {"implementation_job_id": "implementation-1"},
        "blocking_findings": [
            {
                "message": "runtime check failed",
                "repairable": False,
                "defect_class": "ENVIRONMENT_DEFECT",
            }
        ],
    }
    service = WorkspaceService()
    observed: dict[str, Any] = {}
    monkeypatch.setattr(service, "_record_technical_retry", lambda *_args: None)
    monkeypatch.setattr(service, "_sleep_for_retry", lambda *_args: None)
    monkeypatch.setattr(
        service,
        "_run_testing_command",
        lambda command_arg, implementation_job_id: observed.update(
            command_id=command_arg["command_id"], implementation_job_id=implementation_job_id
        )
        or {"message": "Testing recovered."},
    )
    try:
        repaired = service._auto_repair_semantic_result(command, result)
    finally:
        service.shutdown()

    assert repaired == {"message": "Testing recovered."}
    assert observed == {
        "command_id": "testing-command",
        "implementation_job_id": "implementation-1",
    }


def test_active_requirements_finding_uses_in_memory_repair_once(monkeypatch) -> None:
    command = {
        "command_id": "requirements-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "repair_state": {"status": "ACTIVE", "attempt_count": 2},
        "blocking_findings": [
            {
                "stage": "specs",
                "message": "The precondition is incomplete.",
                "repairable": True,
            }
        ],
    }
    observed: dict[str, object] = {}
    service = WorkspaceService()

    def repair(_command, previous, *, instruction):
        observed.setdefault("instructions", []).append(instruction)
        if previous["repair_state"]["attempt_count"] == 2:
            return {
                **previous,
                "repair_state": {
                    **previous["repair_state"],
                    "attempt_count": 3,
                    "finding_digest": "progressed",
                },
            }
        return {"message": "Requirements repaired."}

    monkeypatch.setattr(service, "_repair_requirements_result", repair)
    try:
        repaired = service._auto_repair_semantic_result(command, result)
    finally:
        service.shutdown()

    assert repaired == {"message": "Requirements repaired."}
    assert len(observed["instructions"]) == 2
    assert "automatic:requirements:episode-3" in str(observed["instructions"][0])
    assert "automatic:requirements:episode-4" in str(observed["instructions"][1])


def test_stalled_technical_design_finding_retries_same_command(monkeypatch) -> None:
    command = {
        "command_id": "design-command",
        "app_id": "app-1",
        "action": "advance",
        "stage": "design",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "repair_state": {"status": "STALLED", "attempt_count": 2},
        "blocking_findings": [{"message": "Unresolved.", "repairable": True}],
    }
    retries: list[dict[str, Any]] = []
    service = WorkspaceService()
    monkeypatch.setattr(service, "_record_technical_retry", lambda *_args: retries.append({}))
    monkeypatch.setattr(service, "_sleep_for_retry", lambda *_args: None)
    monkeypatch.setattr(
        service,
        "_run_design_operation",
        lambda *_args, **_kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(service, "_design_result", lambda response: {"message": response["status"]})
    try:
        assert service._auto_repair_semantic_result(command, result) == {"message": "completed"}
    finally:
        service.shutdown()
    assert len(retries) == 1


def test_stalled_class_technical_finding_uses_one_guarded_reconcile_retry(
    monkeypatch,
) -> None:
    command = {
        "command_id": "class-command",
        "app_id": "app-1",
        "action": "advance",
        "stage": "design",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "current_stage": "class_diagram",
        "repair_state": {"status": "STALLED", "attempt_count": 1},
        "finding_details": [{
            "rule_id": "class.public-contract-semantic",
            "requires_user_input": False,
        }],
        "blocking_findings": [{"message": "Missing value reference.", "repairable": True}],
    }
    observed: dict[str, object] = {}
    service = WorkspaceService()
    monkeypatch.setattr(service, "_stop_requested", lambda _command_id: False)
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {"active": True, "stage": "class_diagram"},
    )
    original_model = {"Classes": [{"className": "SubmitControl"}]}
    normalized_model = {"Classes": [{"className": "SubmitControl", "operations": []}]}
    monkeypatch.setitem(
        workspace_module.DESIGN_SPECS,
        "class_diagram",
        SimpleNamespace(
            model_key="extracted_bce_classes",
            reconcile=lambda _state: {"extracted_bce_classes": normalized_model},
        ),
    )
    monkeypatch.setattr(
        workspace_module.artifact_repository,
        "load_state",
        lambda _app_id: {"extracted_bce_classes": original_model},
    )
    rechecks: list[dict[str, object]] = []
    monkeypatch.setattr(
        service,
        "_active_semantic_repair_input",
        lambda current: rechecks.append(current) or None,
    )

    def run_operation(_command, *, stage, label, operation):
        observed.update(stage=stage, label=label)
        return operation()

    def retry(app_id):
        observed["retry_app_id"] = app_id
        return {"status": "need_feedback", "stage": "class_diagram"}

    monkeypatch.setattr(service, "_run_design_operation", run_operation)
    monkeypatch.setattr(workspace_module, "retry_design_session", retry)
    monkeypatch.setattr(service, "_design_result", lambda _response: {"message": "Rechecked."})
    try:
        repaired = service._auto_repair_semantic_result(command, result)
    finally:
        service.shutdown()

    assert repaired == {"message": "Rechecked."}
    assert observed == {
        "stage": "class_diagram",
        "label": "Repairing the class diagram",
        "retry_app_id": "app-1",
    }
    assert len(rechecks) == 2


@pytest.mark.parametrize(
    "result_patch",
    [
        {"finding_details": [{"requires_user_input": True}]},
        {"current_stage": "sequence_diagram"},
        {"feedback_question": {"question_id": "q1"}},
        {"resource_questions": [{"question_id": "resource-q1"}]},
    ],
)
def test_class_reconcile_retry_does_not_run_for_user_input_or_other_state(
    monkeypatch, result_patch: dict[str, Any]
) -> None:
    command = {
        "command_id": "class-command",
        "app_id": "app-1",
        "action": "advance",
        "stage": "design",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "current_stage": "class_diagram",
        "repair_state": {"status": "STALLED"},
        "finding_details": [{"requires_user_input": False}],
        "blocking_findings": [{"message": "Technical finding."}],
        **result_patch,
    }
    service = WorkspaceService()
    monkeypatch.setattr(service, "_stop_requested", lambda _command_id: False)
    monkeypatch.setattr(
        workspace_module, "session_status",
        lambda _app_id: pytest.fail("Ineligible class result must not retry."),
    )
    try:
        assert service._auto_repair_semantic_result(command, result) == result
    finally:
        service.shutdown()


def test_stalled_class_result_retries_without_model_reconcile_delta(monkeypatch) -> None:
    command = {
        "command_id": "class-command",
        "app_id": "app-1",
        "action": "advance",
        "stage": "design",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "current_stage": "class_diagram",
        "repair_state": {"status": "STALLED"},
        "finding_details": [{"requires_user_input": False}],
        "blocking_findings": [{"message": "Technical finding."}],
    }
    service = WorkspaceService()
    monkeypatch.setattr(service, "_record_technical_retry", lambda *_args: None)
    monkeypatch.setattr(
        service,
        "_run_design_operation",
        lambda *_args, **_kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(service, "_design_result", lambda response: {"message": response["status"]})
    try:
        assert service._auto_repair_semantic_result(command, result) == {"message": "completed"}
    finally:
        service.shutdown()


def test_no_progress_semantic_repair_waits_with_cancellable_backoff(monkeypatch) -> None:
    command = {
        "command_id": "requirements-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "repair_state": {"status": "ACTIVE", "attempt_count": 2},
        "blocking_findings": [
            {"stage": "specs", "message": "Unresolved.", "repairable": True}
        ],
    }
    service = WorkspaceService()
    calls = 0
    sleeps = 0
    monkeypatch.setattr(service, "_record_technical_retry", lambda *_args: None)
    def stop(_command_id):
        return calls >= 2
    monkeypatch.setattr(service, "_stop_requested", stop)
    def sleep(*_args):
        nonlocal sleeps
        sleeps += 1
    monkeypatch.setattr(service, "_sleep_for_retry", sleep)
    def unchanged(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return dict(result)
    monkeypatch.setattr(
        service, "_repair_requirements_result", unchanged
    )
    try:
        with pytest.raises(workspace_module.WorkspaceStopRequested):
            service._auto_repair_semantic_result(command, result)
    finally:
        service.shutdown()
    assert calls == 2
    assert sleeps == 2


def test_semantic_repair_retries_until_stop_not_a_hard_iteration_bound(monkeypatch) -> None:
    command = {
        "command_id": "requirements-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "repair_state": {"status": "ACTIVE", "attempt_count": 0},
        "blocking_findings": [
            {"stage": "specs", "message": "Still unresolved.", "repairable": True}
        ],
    }
    attempts = 0
    service = WorkspaceService()
    monkeypatch.setattr(service, "_record_technical_retry", lambda *_args: None)
    monkeypatch.setattr(service, "_sleep_for_retry", lambda *_args: None)
    monkeypatch.setattr(service, "_stop_requested", lambda _id: attempts >= 3)

    def make_progress(_command, previous, *, instruction):
        nonlocal attempts
        attempts += 1
        return {
            **previous,
            "repair_state": {
                **previous["repair_state"],
                "attempt_count": attempts,
                "finding_digest": f"changed-{attempts}",
            },
        }

    monkeypatch.setattr(service, "_repair_requirements_result", make_progress)
    try:
        with pytest.raises(workspace_module.WorkspaceStopRequested):
            service._auto_repair_semantic_result(command, result)
    finally:
        service.shutdown()

    assert attempts == 3


def test_testing_sut_repair_rechecks_with_the_same_command_and_implementation_job(
    monkeypatch,
) -> None:
    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {"testing_checkpoint": {"implementation_job_id": "implementation-1"}},
    }
    previous_job = {
        "job_id": "testing-command",
        "app_id": "app-1",
        "implementation_job_id": "implementation-1",
        "status": "COMPLETED",
        "result": {"passed": False},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "repair_state": {"status": "ACTIVE", "attempt_count": 0},
        "blocking_findings": [
            {
                "message": "The implementation returned the wrong response.",
                "repairable": True,
                "defect_class": "SUT_DEFECT",
                "repair_owner": "implementation",
                "implementation_owner": "backend",
            }
        ],
        "job": previous_job,
    }
    observed: dict[str, Any] = {}
    service = WorkspaceService()

    def repair(testing_command, testing_result):
        observed["repair_command_id"] = testing_command["command_id"]
        observed["repair_result"] = testing_result
        return (
            {"job_id": "implementation-1", "job": {"status": "COMPLETED"}},
            "implementation-1",
            "testing-dynamic-functional",
        )

    def recheck(testing_command, implementation_job_id, **kwargs):
        observed["testing_command_id"] = testing_command["command_id"]
        observed["implementation_job_id"] = implementation_job_id
        observed.update(kwargs)
        return {"message": "Testing completed.", "job_id": "testing-command"}

    monkeypatch.setattr(service, "_repair_testing_with_owner", repair)
    monkeypatch.setattr(service, "_run_testing_command", recheck)
    try:
        completed = service._auto_repair_semantic_result(command, result)
    finally:
        service.shutdown()

    assert completed["message"] == "Testing completed."
    assert observed["repair_command_id"] == "testing-command"
    assert observed["testing_command_id"] == "testing-command"
    assert observed["implementation_job_id"] == "implementation-1"
    assert observed["previous_job"] is previous_job
    assert observed["preserve_test"] is True
    assert observed["repair_task_type"] == "testing-dynamic-functional"
    assert observed["reset_checkpoint"] is True


def test_testing_repair_groups_findings_by_declared_source_task(monkeypatch) -> None:
    service = WorkspaceService()
    blockers = [
        {"id": "registration-create"},
        {"id": "registration-swap"},
        {"id": "course-search"},
    ]
    targets = {
        "registration-create": ["task:registration-owner"],
        "registration-swap": ["task:registration-owner"],
        "course-search": ["task:search-owner"],
    }
    monkeypatch.setattr(
        service,
        "_testing_repair_request",
        lambda _app_id, _result, selected: (
            selected,
            "implementation",
            "testing-dynamic-functional",
            [f"{selected[0]['id']}.java"],
            {},
        ),
    )
    monkeypatch.setattr(
        service,
        "_testing_implementation_repair_targets",
        lambda _app_id, selected, hints: (targets[selected[0]["id"]], hints),
    )
    try:
        groups = service._testing_repair_owner_groups("app-1", {}, blockers)
    finally:
        service.shutdown()

    assert [[item["id"] for item in group] for group in groups] == [
        ["registration-create", "registration-swap"],
        ["course-search"],
    ]


@pytest.mark.parametrize(
    ("owner_status", "checkpoint_retryable", "expects_retry"),
    [
        ("AWAITING_INPUT", False, False),
        ("INTERRUPTED", True, True),
    ],
    ids=["preserves-owner-question", "retries-interrupted-checkpoint"],
)
def test_testing_repair_batches_declared_owners_before_one_monitor(
    monkeypatch, owner_status, checkpoint_retryable, expects_retry
) -> None:
    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "payload": {},
    }
    first = {
        "id": "first-owner",
        "repairable": True,
        "repair_owner": "implementation",
    }
    second = {
        "id": "second-owner",
        "repairable": True,
        "repair_owner": "implementation",
    }
    result = {
        "blocking_findings": [first, second],
        "job": {"implementation_job_id": "implementation-1"},
    }
    submitted: list[list[str]] = []
    monitored: list[str] = []
    retried: list[str] = []
    retry_records: list[dict[str, Any]] = []
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "_testing_repair_request",
        lambda _app_id, _result, selected: (
            selected,
            "implementation",
            "testing-dynamic-functional",
            [f"{selected[0]['id']}.java"],
            {},
        ),
    )
    monkeypatch.setattr(
        service,
        "_testing_repair_owner_groups",
        lambda _app_id, _result, _blockers: [[first], [second]],
    )
    monkeypatch.setattr(
        service,
        "_testing_implementation_feedback",
        lambda *_args, **_kwargs: "repair evidence",
    )
    monkeypatch.setattr(
        service,
        "_testing_implementation_repair_targets",
        lambda _app_id, selected, hints: ([f"task:{selected[0]['id']}"], hints),
    )
    monkeypatch.setattr(
        service,
        "_implementation_repair_outcomes",
        lambda _job: ([], []),
    )
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {
            "job_id": "implementation-1",
            "status": owner_status,
            "checkpoint_retryable": checkpoint_retryable,
        },
    )
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "retry_failed",
        lambda job_id: retried.append(job_id) or {"job_id": job_id},
    )
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "request_owner_repair_batch",
        lambda _job_id, **kwargs: submitted.append(
            [
                json.loads(repair["evidence"]["testResults"])["confirmedTargetRefs"][0]
                for repair in kwargs["repairs"]
            ]
        )
        or {"job_id": "implementation-1"},
    )
    def monitor(_job, **_kwargs):
        monitored.append("implementation-1")
        if expects_retry and len(monitored) == 1:
            raise RuntimeError("owner repair interrupted")
        return {
            "job_id": "implementation-1",
            "awaiting_input": not expects_retry,
            "job": {"status": "COMPLETED" if expects_retry else "AWAITING_INPUT"},
        }

    monkeypatch.setattr(service, "_monitor_implementation", monitor)
    monkeypatch.setattr(repository, "update_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_record_transient_retry",
        lambda _command_id, _attempt, error: retry_records.append(
            {"attempt": _attempt, "error_type": type(error).__name__}
        ),
    )
    monkeypatch.setattr(service, "_sleep_for_retry", lambda *_args: None)
    try:
        repaired, job_id, task_type = service._repair_testing_with_owner(command, result)
    finally:
        service.shutdown()

    assert submitted == [["task:first-owner", "task:second-owner"]]
    assert monitored == ["implementation-1"] * (2 if expects_retry else 1)
    assert retried == (["implementation-1"] if expects_retry else [])
    assert retry_records == (
        [{"attempt": 1, "error_type": "RuntimeError"}] if expects_retry else []
    )
    assert repaired["job"]["status"] == (
        "COMPLETED" if expects_retry else "AWAITING_INPUT"
    )
    assert job_id == "implementation-1"
    assert task_type == "testing-dynamic-functional"


def test_testing_owner_repair_discards_the_old_checkpoint_before_recapture(monkeypatch) -> None:
    stale_checkpoint = {"implementation_job_id": "implementation-old"}
    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {"testing_checkpoint": stale_checkpoint},
    }
    updates: list[dict[str, Any]] = []
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: updates.append(changes) or changes,
    )
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda _command_id: {"payload": dict(command["payload"])},
    )

    def run_testing(_app_id, implementation_job_id, **kwargs):
        observed["run_id"] = kwargs["run_id"]
        observed["implementation_job_id"] = implementation_job_id
        observed["checkpoint"] = kwargs["checkpoint"]
        kwargs["progress"](
            {"implementation_job_id": implementation_job_id, "current_node": "queued"}
        )
        return {
            "job_id": kwargs["run_id"],
            "implementation_job_id": implementation_job_id,
            "status": "COMPLETED",
            "result": {"passed": True},
        }

    monkeypatch.setattr(workspace_module, "run_testing", run_testing)
    service = WorkspaceService()
    try:
        result = service._run_testing_command(
            command,
            "implementation-1",
            previous_job={"job_id": "testing-command", "status": "COMPLETED"},
            preserve_test=True,
            repair_task_type="testing-dynamic-functional",
            reset_checkpoint=True,
        )
    finally:
        service.shutdown()

    assert observed["run_id"] == "testing-command"
    assert observed["implementation_job_id"] == "implementation-1"
    assert observed["checkpoint"] is None
    assert "testing_checkpoint" not in updates[0]["payload"]
    assert updates[-1]["payload"]["testing_checkpoint"]["implementation_job_id"] == (
        "implementation-1"
    )
    assert result["job"]["job_id"] == "testing-command"


def test_active_design_finding_keeps_its_saved_gate(monkeypatch) -> None:
    command = {
        "command_id": "design-command",
        "app_id": "app-1",
        "action": "advance",
        "stage": "design",
        "payload": {},
    }
    result = {
        "awaiting_input": True,
        "requires_revision": True,
        "current_stage": "class_diagram",
        "repair_state": {"status": "ACTIVE", "attempt_count": 0},
        "blocking_findings": [{"message": "Class mismatch.", "repairable": True}],
    }
    service = WorkspaceService()
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: pytest.fail("Active design findings must not inspect session state."),
    )
    monkeypatch.setattr(
        workspace_module,
        "resume_design_session",
        lambda *_args: pytest.fail("Active design findings must not resume a session."),
    )
    try:
        repaired = service._auto_repair_semantic_result(command, result)
    finally:
        service.shutdown()

    assert repaired is result


def test_testing_upstream_ambiguity_waits_for_user_without_rewinding_design() -> None:
    job = {
        "job_id": "testing-command",
        "implementation_job_id": "implementation-1",
        "result": {
            "passed": False,
            "blocking_findings": [
                {
                    "message": "No exact operation can realize the use case.",
                    "repairable": True,
                    "defect_class": "UPSTREAM_AMBIGUITY",
                    "repair_owner": "requirements-or-design",
                }
            ],
        },
    }
    service = WorkspaceService()
    try:
        result = service._testing_result(job)
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["blocking_route"] == "design"
    assert "can_delegate_repair" not in result
    assert "Review the affected design" in result["message"]


def test_testing_plan_defect_does_not_start_an_unbounded_repair_episode() -> None:
    job = {
        "job_id": "testing-command",
        "implementation_job_id": "implementation-1",
        "result": {
            "passed": False,
            "blocking_findings": [
                {
                    "message": "The generated Arazzo workflow is invalid.",
                    "repairable": True,
                    "defect_class": "TEST_DEFECT",
                    "repair_owner": "testing",
                }
            ],
        },
    }
    service = WorkspaceService()
    try:
        result = service._testing_result(job)
    finally:
        service.shutdown()

    assert result["blocking_route"] == "platform"
    assert "can_delegate_repair" not in result
    assert "EasyDep platform" in result["message"]


def test_testing_static_repair_request_keeps_exact_gate_scope() -> None:
    selected, owner, task_type, files, profile = WorkspaceService._testing_repair_request(
        "app-1",
        {
            "job": {
                "testing_input": {
                    "app_id": "app-1",
                    "implementation_job_id": "job-1",
                }
            }
        },
        [
            {
                "code": "testing.static",
                "repairable": True,
                "implementation_owner": "backend",
                "file_hints": ["application/deployment/tofu/main.tf"],
                "evidence": {"gate": "static", "issues": ["AWS-0131"]},
            }
        ],
    )

    assert selected[0]["code"] == "testing.static"
    assert owner == "backend"
    assert task_type == "testing-static"
    assert files == ["application/deployment/tofu/main.tf"]
    assert profile["testing_input"]["implementation_job_id"] == "job-1"


def test_testing_static_feedback_describes_the_assigned_deployment_check() -> None:
    """IaC 오류를 기능 테스트 오류라고 잘못 소개하지 않는다."""

    feedback = WorkspaceService._testing_implementation_feedback(
        {"repair_state": {}},
        [
            {
                "code": "testing.static",
                "message": "AWS-0131: root block device is not encrypted.",
                "file_hints": ["application/deployment/tofu/main.tf"],
            }
        ],
    )

    assert feedback.startswith("The generated deployment infrastructure failed")
    assert "functional test" not in feedback


def test_workspace_tables_are_part_of_the_shared_database_schema() -> None:
    assert "workspace_commands" in Base.metadata.tables
    assert "workspace_events" not in Base.metadata.tables
    assert "deployment_preferences" not in Base.metadata.tables
    assert "deployment_preferences" in Base.metadata.tables["apps"].columns


def test_workspace_event_summary_omits_large_llm_contents() -> None:
    row = SimpleNamespace(
        event_id=7,
        app_id="app-1",
        command_id="command-1",
        stage="design",
        kind="progress",
        actor="system",
        text="Design LLM metrics recorded.",
        event_data={
            "progress_event": "designLlmMetrics",
            "analysis_step": "class_diagram",
            "llm_timing_events": [
                {"operation": "ClassInventory", "responseContent": "x" * 1_000_000},
                {"operation": "ClassRepair", "reasoningContent": "y" * 1_000_000},
            ],
        },
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )

    summary = repository.event_dict(row, include_llm_timings=False)

    assert summary["metadata"] == {
        "progress_event": "designLlmMetrics",
        "analysis_step": "class_diagram",
        "llm_timing_count": 2,
    }
    assert len(json.dumps(summary)) < 1000
    assert len(row.event_data["llm_timing_events"]) == 2


def test_persisted_command_projects_user_message_and_terminal_card() -> None:
    created_at = datetime(2026, 9, 13, 1, 2, 3, tzinfo=UTC)
    row = SimpleNamespace(
        command_id="command-1",
        app_id="app-1",
        action="message",
        stage="design",
        status="AWAITING_INPUT",
        payload={
            "text": "Revise CourseOffering.",
            "context": {"artifact_stage": "class_diagram"},
        },
        result={
            "kind": "action_required",
            "message": "Design revision completed. Review the result.",
            "current_stage": "class_diagram",
        },
        error=None,
        created_at=created_at,
        started_at=created_at,
        completed_at=None,
    )

    events = repository._command_timeline_events(row)

    assert [(event["actor"], event["kind"]) for event in events] == [
        ("user", "message"),
        ("assistant", "action_required"),
    ]
    assert events[0]["text"] == "Revise CourseOffering."
    assert events[1]["metadata"]["current_stage"] == "class_diagram"


def _completed_testing_row(*, status: str = "COMPLETED", passed: bool = True):
    completed_at = datetime(2026, 9, 13, 1, 2, 4, tzinfo=UTC)
    return SimpleNamespace(
        command_id="testing-command",
        app_id="app-1",
        action="start_testing",
        stage="testing",
        status=status,
        payload={
            "testing_checkpoint": {
                "current_node": "verification_complete",
                "result": {"passed": passed, "gateStatus": "PASS" if passed else "FAIL"},
            }
        },
        result={"message": "Testing completed.", "job": {"result": {"passed": passed}}},
        error="Testing stopped." if status == "INTERRUPTED" else None,
        created_at=completed_at,
        started_at=completed_at,
        completed_at=completed_at,
    )


def test_completed_testing_checkpoint_projects_terminal_steps_after_stale_running() -> None:
    row = _completed_testing_row()
    stale_running = repository._timeline_event_id(row.completed_at, 0) - 1

    events = repository._command_timeline_events(row)

    steps = [
        event for event in events
        if event["metadata"].get("progress_event") == "testingStepUpdated"
    ]
    assert [(event["metadata"]["step"], event["metadata"]["progress_status"]) for event in steps] == [
        ("prepare-testing", "completed"),
        ("run-verification", "completed"),
        ("finalize-testing", "completed"),
    ]
    assert all(event["event_id"] > stale_running for event in steps)
    assert events[-1]["kind"] == "status"


@pytest.mark.parametrize(
    ("status", "passed"),
    [
        ("FAILED", True),
        ("INTERRUPTED", True),
        ("AWAITING_INPUT", True),
        ("COMPLETED", False),
    ],
)
def test_nonterminal_or_nonpassing_testing_command_never_projects_completion_steps(
    status: str,
    passed: bool,
) -> None:
    events = repository._command_timeline_events(
        _completed_testing_row(status=status, passed=passed)
    )

    assert not [
        event for event in events
        if event["metadata"].get("progress_event") == "testingStepUpdated"
    ]


@pytest.mark.parametrize("action", ["start_implementation"])
def test_reconcile_implementation_command_closes_stale_running_command(
    monkeypatch,
    action: str,
) -> None:
    command = {
        "command_id": "command-1",
        "action": action,
        "stage": "implementation",
        "status": "RUNNING",
        "payload": {"job_id": "job-1"},
        "started_at": "2026-09-11T10:00:00+09:00",
    }
    completed_job = {
        "job_id": "job-1",
        "status": "COMPLETED",
        "owner_repair": {"requested_at": "2026-09-11T10:00:01+09:00"},
    }
    updated = {**command, "status": "COMPLETED"}
    events: list[dict] = []
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker, "get", lambda _job_id: completed_job
    )
    monkeypatch.setattr(
        repository,
        "finish_command_honoring_stop",
        lambda _command_id, **_kwargs: updated,
    )
    monkeypatch.setattr(repository, "append_progress_event", lambda *args, **kwargs: events.append(kwargs))
    monkeypatch.setattr(repository, "list_progress_events", lambda _app_id: [])
    monkeypatch.setattr(
        repository,
        "now",
        lambda: datetime.now(UTC).replace(tzinfo=None),  # noqa: PLW0108
    )

    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result["status"] == "COMPLETED"
    assert events[0]["metadata"]["progress_event"] == "commandStateChanged"
    assert [event["metadata"]["step"] for event in events[1:]] == [
        "phase-backend",
        "phase-frontend",
        "phase-integration",
    ]
    assert {event["metadata"]["progress_status"] for event in events[1:]} == {"completed"}


@pytest.mark.parametrize(
    "started_at",
    ["2026-09-11T10:00:00+09:00", "2026-09-11T10:00:00"],
)
def test_reconcile_does_not_mistake_an_unrequested_repair_for_completion(
    monkeypatch,
    started_at: str,
) -> None:
    command = {
        "command_id": "repair-command",
        "app_id": "app-1",
        "action": "start_implementation",
        "stage": "implementation",
        "status": "INTERRUPTED",
        "payload": {"action_id": "testing-command", "job_id": "job-1"},
        "started_at": started_at,
    }
    completed_before_request = {
        "job_id": "job-1",
        "status": "COMPLETED",
        "owner_repair": {"requested_at": "2026-09-11T09:59:00+09:00"},
    }
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: completed_before_request,
    )
    monkeypatch.setattr(repository, "now", lambda: datetime.now(UTC).replace(tzinfo=None))
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: {**command, **changes},
    )
    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result["status"] == "FAILED"
    assert "job_id" not in result["payload"]
    assert not any(
        action["action"] == "delegate_repair"
        for action in result["result"]["actions"]
    )


def test_reconcile_leaves_an_active_repair_alone_before_worker_scheduling(
    monkeypatch,
) -> None:
    command = {
        "command_id": "repair-command",
        "app_id": "app-1",
        "action": "start_implementation",
        "stage": "implementation",
        "status": "RUNNING",
        "payload": {"action_id": "testing-command", "job_id": "job-1"},
        "started_at": "2026-09-11T10:00:00+09:00",
    }
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {
            "job_id": "job-1",
            "status": "COMPLETED",
            "owner_repair": {"requested_at": "2026-09-11T09:59:00+09:00"},
        },
    )
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda *_args, **_kwargs: pytest.fail("An active command must not be reconciled early."),
    )

    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result == command


def test_reconcile_implementation_command_restores_progress_after_restart(
    monkeypatch,
) -> None:
    command = {
        "command_id": "command-1",
        "action": "start_implementation",
        "stage": "implementation",
        "status": "INTERRUPTED",
        "payload": {"job_id": "job-1"},
    }
    events: list[dict] = []
    updates: list[dict] = []
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {"job_id": "job-1", "status": "RUNNING"},
    )
    monkeypatch.setattr(repository, "list_progress_events", lambda _app_id: [])
    monkeypatch.setattr(repository, "append_progress_event", lambda *args, **kwargs: events.append(kwargs))
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: updates.append(changes) or {**command, **changes},
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_implementation_progress_snapshot",
        staticmethod(
            lambda _job: {
                "progress_card_label": "Implementation progress",
                "updates": [
                    {
                        "step": "phase-backend",
                        "label": "Backend implementation",
                        "status": "running",
                        "detail": "Backend implementation is in progress.",
                    }
                ],
            }
        ),
    )

    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result["status"] == "RUNNING"
    assert updates == [{"status": "RUNNING", "error": None, "completed_at": None}]
    assert events[0]["metadata"]["step"] == "phase-backend"
    assert events[0]["metadata"]["progress_status"] == "running"


def test_sync_implementation_progress_emits_changed_owner_file_only(monkeypatch) -> None:
    labels = {
        "phase-backend": "Backend implementation",
        "phase-frontend": "Frontend implementation",
        "phase-integration": "Integration verification",
    }
    previous_events = []
    for step, label in labels.items():
        status = "running" if step == "phase-backend" else "pending"
        metadata = {
            "step": step,
            "progress_status": status,
            "progress_step_label": label,
            "progress_detail": (
                "Backend implementation is in progress." if status == "running" else ""
            ),
            "repairing": False,
        }
        if step == "phase-backend":
            metadata.update(
                current_file="application/src/Old.java",
                current_class="Old",
            )
        previous_events.append(
            {
                "command_id": "command-1",
                "stage": "implementation",
                "kind": "progress",
                "metadata": metadata,
            }
        )
    appended: list[dict] = []
    monkeypatch.setattr(repository, "list_progress_events", lambda _app_id: previous_events)
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: appended.append(kwargs),
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_implementation_progress_snapshot",
        staticmethod(
            lambda _job: {
                "progress_card_label": "Implementation progress",
                "updates": [
                    {
                        "step": "phase-backend",
                        "label": "Backend implementation",
                        "status": "running",
                        "detail": "Backend implementation is in progress.",
                        "implementation_owner": "backend",
                        "current_file": "application/src/New.java",
                        "current_class": "New",
                        "repairing": False,
                    },
                    {
                        "step": "phase-frontend",
                        "label": "Frontend implementation",
                        "status": "pending",
                        "detail": "",
                        "implementation_owner": "frontend",
                        "repairing": False,
                    },
                    {
                        "step": "phase-integration",
                        "label": "Integration verification",
                        "status": "pending",
                        "detail": "",
                        "implementation_owner": "integration",
                        "repairing": False,
                    },
                ],
            }
        ),
    )

    service = WorkspaceService()
    try:
        service._sync_implementation_progress("app-1", "command-1", {})
    finally:
        service.shutdown()

    assert len(appended) == 1
    assert appended[0]["metadata"]["step"] == "phase-backend"
    assert appended[0]["metadata"]["current_file"] == "application/src/New.java"


def test_reconcile_does_not_finish_testing_command_with_completed_repair_job(
    monkeypatch,
) -> None:
    """구현 수리가 끝나도 Testing 명령은 남은 기능 검사를 직접 끝내야 한다."""
    command = {
        "command_id": "command-1",
        "action": "retry_implementation",
        "stage": "testing",
        "status": "RUNNING",
        "payload": {"job_id": "repair-job-1"},
    }
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: pytest.fail("Testing 명령을 구현 작업으로 조회하면 안 된다."),
    )

    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result == command


@pytest.mark.parametrize(
    ("command_status", "job_status", "checkpoint_retryable", "expected_status"),
    [
        ("FAILED", "FAILED", True, "FAILED"),
        ("RUNNING", "INTERRUPTED", True, "INTERRUPTED"),
        ("INTERRUPTED", "NEEDS_PLANNER", True, "FAILED"),
        ("RUNNING", "NEEDS_INPUT", False, "AWAITING_INPUT"),
        ("RUNNING", "NEEDS_INPUT", True, "FAILED"),
        ("AWAITING_INPUT", "NEEDS_INPUT", True, "FAILED"),
    ],
)
def test_reconcile_stopped_implementation_exposes_checkpoint_retry(
    monkeypatch,
    command_status: str,
    job_status: str,
    checkpoint_retryable: bool,
    expected_status: str,
) -> None:
    command = {
        "command_id": "command-1",
        "app_id": "app-1",
        "action": "start_implementation",
        "stage": "implementation",
        "status": command_status,
        "payload": {"job_id": "job-1"},
        "result": None,
    }
    failed_job = {
        "job_id": "job-1",
        "app_id": "app-1",
        "status": job_status,
        "checkpoint_retryable": checkpoint_retryable,
    }
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: failed_job,
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_sync_implementation_progress",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: {**command, **changes},
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_finish_terminal_command",
        lambda _self, _command_id, _command, *, status, result, error: {
            **command,
            "status": status,
            "result": result,
            "error": error,
        },
    )

    service = WorkspaceService()
    try:
        reconciled = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert reconciled is not None
    assert reconciled["status"] == expected_status
    assert reconciled["result"]["job_id"] == "job-1"
    if expected_status in {"FAILED", "INTERRUPTED"}:
        assert reconciled["result"]["checkpoint_retryable"] is True
    else:
        assert reconciled["result"]["kind"] == "question"


def test_chat_event_timestamp_is_returned_as_explicit_korean_time() -> None:
    event = repository.event_dict(
        SimpleNamespace(
            event_id=1,
            app_id="app-1",
            command_id=None,
            stage="requirements",
            kind="message",
            actor="user",
            text="hello",
            event_data={},
            # MySQL DATETIME values are stored as naive UTC values.
            created_at=datetime(2026, 8, 19, 5, 30, 21),  # noqa: DTZ001
        )
    )

    assert event["created_at"] == "2026-08-19T14:30:21+09:00"


def test_artifact_stage_is_normalized_to_the_user_visible_workflow_stage() -> None:
    assert repository.workflow_stage("resource_spec") == "requirements"
    assert repository.workflow_stage("capability_contract") == "requirements"
    assert repository.workflow_stage("resource_intake") == "requirements"
    assert repository.workflow_stage("deployment_diagram") == "design"
    assert repository.workflow_stage(None) == "requirements"


def _pinned_class_question() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    target = {
        "ref": "class_diagram:Registration::swap()",
        "kind": "operation",
        "element_id": "Registration.swap",
        "owner": "design",
        "artifact_type": "CLASS",
        "artifact_version_id": 7,
        "display_label": "Registration.swap",
    }
    context = {"element_ref": target["ref"], "validated_target": target}
    pending = {
        "command_id": "class-question",
        "app_id": "app-1",
        "action": "message",
        "stage": "design",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {
            "resource_question": {
                "choices": [{"value": "Add swap operation"}],
                "allowFreeText": True,
                "context": context,
            }
        },
    }
    return target, context, pending


def _install_pinned_class_question(monkeypatch, pending, target, *, latest=None) -> None:
    monkeypatch.setattr(
        repository, "latest_command", lambda _app_id, **_kwargs: latest or pending
    )
    monkeypatch.setattr(repository, "get_command", lambda _command_id: pending)
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {"active": True, "stage": "class_diagram", "retryable": False},
    )

    class FakeProjectTools:
        def __init__(self, app_id: str) -> None:
            assert app_id == "app-1"

        def current_revision_target(self, selected):
            return selected

        def trace_impact(self, refs, *, view):
            assert refs == [target["ref"]]
            assert view == "editing"
            return {"impacts": []}

    monkeypatch.setattr(workspace_module, "ProjectTools", FakeProjectTools)


def test_fixed_class_resource_choice_bypasses_conversation_and_revises_batch(
    monkeypatch,
) -> None:
    target, context, pending = _pinned_class_question()
    observed: dict[str, object] = {}
    _install_pinned_class_question(monkeypatch, pending, target)
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "interpret_revision",
        lambda *_args, **_kwargs: pytest.fail("fixed class choice must not use ConversationAgent"),
    )
    monkeypatch.setattr(
        workspace_module,
        "revise_design_elements",
        lambda app_id, request, **kwargs: observed.update(
            app_id=app_id,
            revisions=request.revisions,
            authority=kwargs["approved_authority_targets"],
        )
        or {"changed": ["class_diagram"], "touched": {}, "related": []},
    )
    service = WorkspaceService()
    try:
        assert (
            service._fixed_class_resource_choice(
                "app-1",
                {
                    "action_id": "class-question",
                    "text": "Use a different operation name.",
                    "context": context,
                },
                pending,
            )
            is None
        )
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={
                "action_id": "class-question",
                "text": "Add swap operation",
                "context": context,
            },
            stage=None,
        )
        result = service._stage_message(
            {
                "command_id": "answer",
                "app_id": "app-1",
                "action": action,
                "stage": stage,
                "payload": payload,
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert stage == "design"
    assert observed["app_id"] == "app-1"
    assert observed["revisions"][0].target == target["ref"]
    assert observed["revisions"][0].feedback == "Add swap operation"
    assert observed["authority"] == {target["ref"]}
    assert result["design"]["changed"] == ["class_diagram"]


def test_fixed_class_choice_rejects_an_old_question_before_conversation(monkeypatch) -> None:
    target, context, old = _pinned_class_question()
    old["command_id"] = "old-question"
    latest = {**old, "command_id": "new-question"}
    _install_pinned_class_question(monkeypatch, old, target, latest=latest)
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "interpret_revision",
        lambda *_args, **_kwargs: pytest.fail("stale choice must not be interpreted"),
    )
    service = WorkspaceService()
    try:
        with pytest.raises(ValueError, match="no longer the current design question"):
            service._prepare_conversational_message(
                "app-1",
                action="message",
                payload={
                    "action_id": "old-question",
                    "text": "Add swap operation",
                    "context": context,
                },
                stage=None,
            )
    finally:
        service.shutdown()

def test_cross_stage_feedback_waits_before_mutating_artifacts(monkeypatch) -> None:
    service = WorkspaceService()
    service._stage_message = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("a pending revision plan must not mutate a stage")
    )
    try:
        result = service._dispatch(
            {
                "command_id": "revision-plan",
                "app_id": "app-1",
                "action": "message",
                "stage": "design",
                "payload": {
                    "_conversation_outcome": {"kind": "revision_plan"},
                    "revision_plan": {
                        "plan_digest": "a" * 64,
                        "status": "needs_confirmation",
                        "requested_targets": [
                            {
                                "ref": "erd:Order",
                                "kind": "erd_entity",
                                "element_id": "Order",
                                "owner": "design",
                                "artifact_type": "ERD",
                                "artifact_version_id": 4,
                                "display_label": "Order",
                            }
                        ],
                        "authority_targets": [
                            {
                                "ref": "class_diagram:Order",
                                "kind": "class",
                                "element_id": "Order",
                                "owner": "design",
                                "artifact_type": "CLASS_DIAGRAM",
                                "artifact_version_id": 6,
                                "display_label": "Order",
                            }
                        ],
                        "upstream_candidates": [],
                        "downstream_targets": [],
                        "execution_mode": "targeted_revision",
                        "reason_codes": ["upstream_authority"],
                        "explanation": "Changing this projection requires approval.",
                        "artifact_versions": {"CLASS_DIAGRAM": 6, "ERD": 4},
                        "trace_digest": "b" * 64,
                    },
                },
            }
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["action"] == "confirm_change"


def test_requirement_reply_uses_the_waiting_command_as_continuation(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda *_args, **_kwargs: {
            "command_id": "prior",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
        },
    )

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        result = service._stage_message(
            {
                "command_id": "reply",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {"text": "Use the Seoul region.", "action_id": "prior"},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert captured["request"].answer == "Use the Seoul region."
    assert captured["request"].requirements is None
    assert result["message"] == "Requirements analysis completed."


def test_requirement_reply_answers_the_resource_question_without_reclassification(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda *_args, **_kwargs: {
            "command_id": "prior",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {
                "resource_question": {
                    "field": "monthlyBudgetUSD",
                    "kind": "missing",
                    "question": "What is the monthly budget?",
                }
            },
        },
    )

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "reply",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {"text": "100 USD", "action_id": "prior"},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert captured["request"].resource_answers == {"monthlyBudgetUSD": "100 USD"}
    assert captured["request"].answer is None


def test_design_endpoint_answer_uses_the_pinned_resource_question(monkeypatch) -> None:
    question = {
        "field": "connectionEndpoint:orders-db",
        "kind": "text",
        "question": "What endpoint should Orders use for the database?",
        "sourceRefs": ["connection:orders-db"],
        "context": {
            "connectionId": "orders-db",
            "sourceRef": "connection:orders-db",
            "workloadGraphStructureDigest": "a" * 64,
        },
    }
    pending = {
        "command_id": "endpoint-question",
        "app_id": "app-1",
        "stage": "design",
        "status": "AWAITING_INPUT",
        "result": {"resource_question": question},
    }
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: {
        "command_id": "previous-complete", "status": "COMPLETED"
    })
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: pending)
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "interpret_revision",
        lambda *_args, **_kwargs: pytest.fail("endpoint intake must not be a revision"),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        workspace_module,
        "apply_deployment_endpoint_answer_session",
        lambda app_id, pinned_question, text: captured.update(
            app_id=app_id, question=pinned_question, text=text
        ) or {"status": "completed"},
    )
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {"active": True, "stage": "deployment_diagram"},
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"action_id": "endpoint-question", "text": "postgresql://db:5432/orders"},
            stage=None,
        )
        monkeypatch.setattr(
            service,
            "_run_design_operation",
            lambda _command, *, operation, **_kwargs: operation(),
        )
        monkeypatch.setattr(service, "_design_result", lambda result: result)
        result = service._stage_message(
            {
                "command_id": "endpoint-answer",
                "app_id": "app-1",
                "action": action,
                "stage": stage,
                "payload": payload,
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert (action, stage) == ("message", "design")
    assert result == {"status": "completed"}
    assert captured == {
        "app_id": "app-1",
        "question": question,
        "text": "postgresql://db:5432/orders",
    }


def test_conversation_answer_to_resource_question_uses_free_form_contract(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda *_args, **_kwargs: {
            "command_id": "prior",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {"resource_question": {"field": "provider", "kind": "missing"}},
        },
    )

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "app_id": "app-1",
                "command_id": "reply",
                "action": "message",
                "stage": "requirements",
                "payload": {
                    "text": "Use Azure East US and a 100 USD budget.",
                    "action_id": "prior",
                    "conversation_intent": {"intent": "answer"},
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert captured["request"].resource_answers is None
    assert captured["request"].resource_answer.free_text == "Use Azure East US and a 100 USD budget."
    assert captured["request"].resource_answer.expected_field == "provider"


def test_planned_requirement_revision_is_not_consumed_as_a_resource_answer(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda *_args, **_kwargs: {
            "command_id": "prior",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {
                "resource_question": {
                    "field": "monthlyBudgetUSD",
                    "kind": "missing",
                    "question": "What is the monthly budget?",
                }
            },
        },
    )

    def revise(edit, thread_id, *, app_id):
        captured.update(edit=edit, thread_id=thread_id, app_id=app_id)
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "revise_requirements_analysis", revise)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "revision",
                "app_id": "app-1",
                "action": "message",
                "stage": "requirements",
                "payload": {
                    "text": "Rename the use case.",
                    "action_id": "prior",
                    "conversation_intent": {"intent": "revise"},
                    "validated_targets": [
                        {
                            "ref": "use_case:UC-1",
                            "kind": "use_case",
                            "element_id": "UC-1",
                            "owner": "requirements",
                            "artifact_type": "USECASE_SPEC",
                            "artifact_version_id": 5,
                            "display_label": "Place order",
                        }
                    ],
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert captured["edit"].instruction == "Rename the use case."
    assert captured["edit"].target_ids == ["UC-1"]
    assert captured["thread_id"] == "app-1"
    assert captured["app_id"] == "app-1"


def test_legacy_handoff_checkpoint_backfills_and_routes_a_capability_choice(
    monkeypatch,
) -> None:
    captured = {}
    previous = {
        "command_id": "prior",
        "stage": "requirements",
        "status": "AWAITING_INPUT",
        "result": {
            "phase": "requirements_handoff",
            "blocking_findings": [
                {
                    "code": "requirements.capability-contract",
                    "repairable": False,
                }
            ],
        },
    }
    question = {
        "field": "capability:persistent_storage",
        "kind": "choice",
        "question": "Should data survive service restarts?",
        "choices": [{"value": "accepted", "label": "Yes"}],
    }
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: previous)
    monkeypatch.setattr(
        artifact_repository,
        "load_state",
        lambda _app_id: {"capability_contract": {"capabilities": []}},
    )
    monkeypatch.setattr(
        workspace_module,
        "capability_resource_questions",
        lambda _contract: [question],
    )

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        presented = service.present_command("app-1", previous)
        service._stage_message(
            {
                "command_id": "reply",
                "app_id": "app-1",
                "stage": "requirements",
                "action": "message",
                "payload": {"text": "accepted", "action_id": "prior"},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert presented["result"]["resource_question"] == question
    assert captured["request"].resource_answers == {"capability:persistent_storage": "accepted"}
    assert captured["request"].answer is None


def test_failed_implementation_command_is_presented_as_implementation() -> None:
    command = {
        "command_id": "repair-1",
        "app_id": "app-1",
        "action": "start_implementation",
        "stage": "implementation",
        "status": "FAILED",
        "payload": {
            "action_id": "testing-1",
            "job_id": "implementation-repair-1",
        },
        "result": {"job_id": "implementation-repair-1"},
    }
    service = WorkspaceService()
    try:
        presented = service.present_command("app-1", command)
    finally:
        service.shutdown()

    assert presented["stage"] == "implementation"
    retry = next(
        action
        for action in presented["result"]["actions"]
        if action["action"] == "retry_implementation"
    )
    assert retry["payload"]["job_id"] == "implementation-repair-1"


def test_presented_legacy_clarification_restores_testing_retry(monkeypatch) -> None:
    failure = {
        "command_id": "testing-failure",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {
            "requires_revision": True,
            "job": {"implementation_job_id": "implementation-1"},
            "blocking_findings": [
                {
                    "repairable": True,
                    "defect_class": "TEST_DEFECT",
                    "repair_owner": "testing",
                }
            ],
        },
    }
    clarification = {
        "command_id": "clarification",
        "app_id": "app-1",
        "action": "message",
        "stage": "testing",
        "status": "AWAITING_INPUT",
        "payload": {
            "action_id": "testing-failure",
            "text": "test",
            "_conversation_actions": [
                {
                    "action": "message",
                    "label": "Send design revision feedback",
                    "payload": {"action_id": "testing-failure"},
                }
            ],
        },
        "result": {
            "kind": "question",
            "conversation": {"clarification": {"question": "What should be retried?"}},
        },
    }
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: failure if command_id == "testing-failure" else None,
    )
    service = WorkspaceService()
    try:
        presented = service.present_command("app-1", clarification)
    finally:
        service.shutdown()

    assert [item["action"] for item in presented["result"]["actions"]] == [
        "message",
        "start_testing",
    ]
    assert presented["result"]["actions"][1]["payload"] == {
        "action_id": "testing-failure",
        "implementation_job_id": "implementation-1",
    }


def test_presented_completed_reply_restores_current_design_gate_actions(monkeypatch) -> None:
    gate = {
        "command_id": "design-gate",
        "app_id": "app-1",
        "action": "advance",
        "stage": "design",
        "status": "COMPLETED",
        "payload": {},
        "result": {"message": "Design completed."},
    }
    informational = {
        "command_id": "informational-reply",
        "app_id": "app-1",
        "action": "message",
        "stage": "design",
        "status": "COMPLETED",
        "payload": {
            "action_id": "design-gate",
            "_conversation_actions": [
                {
                    "action": "message",
                    "label": "Ask about deployment options",
                    "payload": {"action_id": "design-gate"},
                }
            ],
        },
        "result": {
            "kind": "reply",
            "conversation": {"reply": {"text": "Design is ready."}},
        },
    }
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: gate if command_id == "design-gate" else None,
    )
    service = WorkspaceService()
    try:
        presented = service.present_command("app-1", informational)
    finally:
        service.shutdown()

    assert [item["action"] for item in presented["result"]["actions"]] == [
        "message",
        "start_implementation",
    ]
    assert all(
        item["payload"]["action_id"] == "design-gate"
        for item in presented["result"]["actions"]
    )


def test_presented_reply_does_not_borrow_actions_without_same_app_lineage(monkeypatch) -> None:
    informational = {
        "command_id": "informational-reply",
        "app_id": "app-1",
        "action": "message",
        "stage": "design",
        "status": "COMPLETED",
        "payload": {
            "action_id": "other-app-gate",
            "_conversation_actions": [
                {
                    "action": "message",
                    "label": "Ask about deployment options",
                    "payload": {"action_id": "other-app-gate"},
                }
            ],
        },
        "result": {
            "kind": "reply",
            "conversation": {"reply": {"text": "No current gate."}},
        },
    }
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda _command_id: {
            "command_id": "other-app-gate",
            "app_id": "other-app",
            "stage": "design",
            "status": "COMPLETED",
            "payload": {},
            "result": {"message": "Other app."},
        },
    )
    service = WorkspaceService()
    try:
        presented = service.present_command("app-1", informational)
    finally:
        service.shutdown()

    assert [item["action"] for item in presented["result"]["actions"]] == ["message"]
    assert presented["result"]["actions"][0]["payload"]["action_id"] == "other-app-gate"


def test_initial_workspace_request_accepts_provider_and_region_without_budget(
    monkeypatch,
) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "initial",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {
                    "text": "Students can register for a course.",
                    "provider": "aws",
                    "region": "ap-northeast-2",
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    constraints = captured["request"].cloud_constraints
    assert constraints.provider == "aws"
    assert constraints.region == "ap-northeast-2"
    assert constraints.monthly_budget_amount is None


def test_initial_workspace_request_can_start_before_cloud_selection(
    monkeypatch,
) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "initial",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {"text": "Students can register for a course."},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    request = captured["request"]
    assert request.cloud_constraints is None
    assert request.requirements == ["Students can register for a course."]


def test_initial_workspace_request_extracts_numbered_items_from_structured_brief(
    monkeypatch,
) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "initial",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {
                    "text": (
                        "Develop a course-registration application.\n\n"
                        "Requirements:\n"
                        "- [REQ-01] Students shall register for an offering.\n"
                        "- [REQ-02] Students shall view their schedule.\n\n"
                        "Cloud and deployment constraints:\n"
                        "- [CLOUD-SOURCE] Use a fixed regional pilot environment."
                    ),
                    "resource_constraints_text": "Use a fixed regional pilot environment.",
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    request = captured["request"]
    assert request.requirements == [
        "Students shall register for an offering.",
        "Students shall view their schedule.",
    ]
    assert request.resource_constraints_text == "Use a fixed regional pilot environment."


def test_structured_deployment_preferences_resume_the_waiting_requirements_gate(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: {
            "command_id": command_id,
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {"resource_questions": [{"field": "provider"}]},
        },
    )

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "resume",
                "app_id": "app-1",
                "action": "apply_deployment_preferences",
                "stage": "requirements",
                "payload": {
                    "action_id": "prior",
                    "deployment_preferences": {
                        "targets": [
                            {
                                "provider": "aws",
                                "region": "ap-northeast-2",
                                "zones": ["ap-northeast-2a"],
                            },
                            {
                                "provider": "gcp",
                                "region": "asia-northeast3",
                                "zones": ["asia-northeast3-a"],
                            },
                        ]
                    },
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    preferences = captured["request"].deployment_preferences
    assert preferences is not None
    assert [target.provider for target in preferences.targets] == ["aws", "gcp"]
    assert captured["request"].answer is None


def test_saved_coordinates_do_not_answer_a_later_budget_question(monkeypatch) -> None:
    monkeypatch.setattr(
        repository,
        "get_deployment_preferences",
        lambda _app_id: {
            "targets": [
                {
                    "provider": "aws",
                    "region": "ap-northeast-2",
                    "zones": ["ap-northeast-2a"],
                }
            ]
        },
    )
    monkeypatch.setattr(
        repository,
        "latest_command",
        lambda _app_id: {
            "command_id": "budget-question",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {"resource_questions": [{"field": "monthlyBudgetUSD"}]},
        },
    )
    service = WorkspaceService()
    try:
        assert service.apply_saved_deployment_preferences("app-1") is None
    finally:
        service.shutdown()


def test_saved_coordinates_add_durable_timeline_text_to_the_resume_command(
    monkeypatch,
) -> None:
    preferences = {
        "targets": [
            {
                "provider": "aws",
                "region": "ap-northeast-2",
                "zones": ["ap-northeast-2a"],
            }
        ]
    }
    monkeypatch.setattr(
        repository, "get_deployment_preferences", lambda _app_id: preferences
    )
    monkeypatch.setattr(
        repository,
        "latest_command",
        lambda _app_id: {
            "command_id": "provider-question",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {"resource_questions": [{"field": "provider"}]},
        },
    )
    submitted: dict[str, Any] = {}
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "submit",
        lambda app_id, **values: submitted.update({"app_id": app_id, **values})
        or values,
    )
    try:
        service.apply_saved_deployment_preferences("app-1")
    finally:
        service.shutdown()

    assert submitted["payload"]["_timeline_text"] == (
        "Deployment alternatives selected: AWS ap-northeast-2"
    )


def test_initial_workspace_request_forwards_structured_monthly_budget(
    monkeypatch,
) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "initial",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {
                    "text": "Students can register for a course.",
                    "provider": "aws",
                    "region": "ap-northeast-2",
                    "monthly_budget_amount": 300000,
                    "monthly_budget_currency": "KRW",
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    constraints = captured["request"].cloud_constraints
    assert constraints.monthly_budget_amount == 300000
    assert constraints.monthly_budget_currency == "KRW"


def test_workspace_cloud_options_include_supported_default_regions() -> None:
    from app.workspace.api import cloud_options

    options = cloud_options()
    codes = {
        provider: {item["code"] for item in rows} for provider, rows in options["regions"].items()
    }
    assert "ap-northeast-2" in codes["aws"]
    assert "koreacentral" in codes["azure"]
    assert "asia-northeast3" in codes["gcp"]
    seoul = next(item for item in options["regions"]["aws"] if item["code"] == "ap-northeast-2")
    assert isinstance(seoul["latitude"], float)
    assert isinstance(seoul["longitude"], float)
    assert seoul["zones"]


def test_requirements_progress_is_emitted_as_transient_workspace_events(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    report = WorkspaceService._requirements_progress_reporter("app-1", "command-1")
    report("analysisStepStarted", {"step": "clarify"})
    report(
        "llmOperationFinished",
        {
            "operation": "structured:ClarifyOnlyResult",
            "status": "completed",
            "elapsedSeconds": 1.25,
        },
    )

    assert len(events) == 2
    assert events[0]["stage"] == "requirements"
    assert events[0]["command_id"] == "command-1"
    assert events[1]["metadata"]["elapsedSeconds"] == 1.25
    assert events[1]["metadata"]["analysis_step"] == "clarify"
    assert events[1]["metadata"]["progress_step_label"] == (
        "Refining ambiguous or compound requirements"
    )
    assert events[1]["metadata"]["progress_detail"] == (
        "AI requirement refinement completed in 1.2s"
    )


def test_workspace_command_trace_uses_app_as_langsmith_thread(monkeypatch) -> None:
    command = {
        "command_id": "command-1",
        "app_id": "app-1",
        "action": "message",
        "stage": "requirements",
        "payload": {"text": "private requirement text"},
    }
    captured: dict[str, Any] = {}

    @contextmanager
    def trace_scope(name, *, metadata):
        captured["name"] = name
        captured["metadata"] = metadata
        yield

    monkeypatch.setattr(repository, "get_command", lambda _command_id: command)
    monkeypatch.setattr(workspace_module.langsmith_metrics, "trace_scope", trace_scope)
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "_execute_command",
        lambda command_id, value: captured.update({"command_id": command_id, "command": value}),
    )
    try:
        service._execute("command-1")
    finally:
        service.shutdown()

    assert captured["name"] == "easydep.workspace.requirements"
    assert captured["metadata"] == {
        "thread_id": "app-1",
        "app_id": "app-1",
        "command_id": "command-1",
        "stage": "requirements",
        "action": "message",
        "agent": "requirements",
        "operation": "workspace_command",
    }
    assert "private requirement text" not in str(captured["metadata"])


def test_design_operation_emits_a_named_progress_card(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    response = WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="sequence_diagram",
        label="Retrying the sequence diagram",
        operation=lambda: {"status": "need_feedback", "validation": {}},
    )

    assert response == {"status": "need_feedback", "validation": {}}
    assert [event["metadata"]["progress_status"] for event in events] == [
        "running",
        "completed",
    ]
    assert events[-1]["metadata"]["progress_card_label"] == "Design generation"

    calls: list[str] = []
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": True,
            "active": True,
            "retryable": True,
            "stage": "sequence_diagram",
        },
    )
    monkeypatch.setattr(
        workspace_module,
        "start_design_session",
        lambda _app_id: calls.append("start") or {"status": "need_feedback"},
    )
    monkeypatch.setattr(
        workspace_module,
        "resume_design_session",
        lambda *_args: calls.append("resume") or {"status": "need_feedback"},
    )
    service = WorkspaceService()
    monkeypatch.setattr(service, "_design_result", lambda response: response)
    try:
        service._stage_message(
            {
                "command_id": "restart-command",
                "app_id": "app-1",
                "action": "start_design",
                "stage": "design",
                "payload": {"text": ""},
            },
            advance=True,
        )
    finally:
        service.shutdown()
    assert calls == ["start"]


def test_inactive_targeted_design_review_advances_without_restarting(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": False,
            "active": False,
            "retryable": False,
            "stage": "class_diagram",
        },
    )
    monkeypatch.setattr(
        workspace_module,
        "start_design_session",
        lambda _app_id: calls.append("start") or {"status": "need_feedback"},
    )
    service = WorkspaceService()
    monkeypatch.setattr(service, "_run_design_operation", lambda _command, **kwargs: kwargs["operation"]())
    try:
        result = service._stage_message(
            {
                "command_id": "targeted-review-command",
                "app_id": "app-1",
                "action": "advance",
                "stage": "design",
                "payload": {"text": ""},
            },
            advance=True,
        )
    finally:
        service.shutdown()

    assert result["message"] == "Design artifact generation completed."
    assert result["design"] == {"status": "completed", "app_id": "app-1"}
    assert calls == []


def test_requirements_restart_restores_original_cloud_inputs_and_prefers_saved_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = {
        "source_app_id": "source-app",
        "target_app_id": "target-app",
        "initial_cloud_inputs": {
            "provider": "aws",
            "region": "ap-northeast-2",
            "monthly_budget_amount": 500,
            "monthly_budget_currency": "USD",
            "resource_constraints_text": "Use the existing pilot environment.",
        },
    }
    state = {
        "requirements_text": "Build the requested service.",
        "resource_constraints_text": "Use the existing pilot environment.",
    }
    submitted: list[dict[str, Any]] = []
    monkeypatch.setattr(workspace_module, "create_restart_branch", lambda *_args: branch)
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: state)
    monkeypatch.setattr(repository, "get_deployment_preferences", lambda _app_id: None)
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "submit",
        lambda app_id, **kwargs: submitted.append({"app_id": app_id, **kwargs})
        or {"command_id": "restart-requirements-command"},
    )
    try:
        result = service._rerun_from_stage(
            {
                "app_id": "source-app",
                "payload": {"restart_stage": "requirements"},
            }
        )
    finally:
        service.shutdown()

    assert result["started_command_id"] == "restart-requirements-command"
    assert submitted[0]["payload"] == {
        "text": "Build the requested service.",
        "resource_constraints_text": "Use the existing pilot environment.",
        "provider": "aws",
        "region": "ap-northeast-2",
        "monthly_budget_amount": 500,
        "monthly_budget_currency": "USD",
    }

    saved_preferences = {
        "mode": "alternatives",
        "targets": [{"provider": "gcp", "region": "asia-northeast3", "zones": []}],
        "monthly_budget_amount": 700,
        "monthly_budget_currency": "KRW",
        "resource_constraints_text": "Use the updated deployment choices.",
    }
    submitted.clear()
    monkeypatch.setattr(
        repository, "get_deployment_preferences", lambda _app_id: saved_preferences
    )
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "submit",
        lambda app_id, **kwargs: submitted.append({"app_id": app_id, **kwargs})
        or {"command_id": "restart-requirements-command"},
    )
    try:
        service._rerun_from_stage(
            {"app_id": "source-app", "payload": {"restart_stage": "requirements"}}
        )
    finally:
        service.shutdown()
    assert submitted[0]["payload"]["deployment_preferences"] == saved_preferences
    assert "provider" not in submitted[0]["payload"]


def test_design_operation_exposes_existing_llm_timing_events(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    def operation():
        record_llm_timing(
            "ClassInventory",
            status="cache_hit",
            metadata={
                "physicalRequest": False,
                "cacheStatus": "hit",
                "failureContentSha256": "safe-digest",
                "failureContentPrefix": "private response start",
                "failureContentSuffix": "private response end",
                "responseContent": '{"Classes": []}',
                "reasoningContent": "empty inventory is enough",
                "schemaValidationErrors": [{"loc": ["Classes"], "type": "too_short"}],
            },
        )
        return {"status": "need_feedback", "validation": {}}

    WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="class_diagram",
        label="Generating the class diagram",
        operation=operation,
    )

    metrics = next(
        event for event in events if event["metadata"].get("progress_event") == "designLlmMetrics"
    )
    assert metrics["metadata"]["llm_timing_events"][0]["operation"] == ("ClassInventory")
    assert metrics["metadata"]["llm_timing_events"][0]["cacheStatus"] == "hit"
    assert metrics["metadata"]["llm_timing_events"][0]["failureContentSha256"] == ("safe-digest")
    assert "failureContentPrefix" not in metrics["metadata"]["llm_timing_events"][0]
    assert "failureContentSuffix" not in metrics["metadata"]["llm_timing_events"][0]
    assert metrics["metadata"]["llm_timing_events"][0]["responseContent"] == ('{"Classes": []}')
    assert metrics["metadata"]["llm_timing_events"][0]["reasoningContent"] == (
        "empty inventory is enough"
    )
    assert metrics["metadata"]["llm_timing_events"][0]["schemaValidationErrors"] == [
        {"loc": ["Classes"], "type": "too_short"}
    ]


def test_design_operation_publishes_only_the_latest_class_preview(monkeypatch) -> None:
    events = []
    live_previews.clear()
    monkeypatch.setattr(
        "app.workspace.service.render_plantuml",
        lambda _puml, _image_format: b"<svg />",
    )
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    def operation():
        design_progress.emit_progress(
            "classDiagramSnapshotAccepted",
            puml="@startuml\nclass Course\n@enduml",
            phase="inventory",
            unit="inventory",
            completed=1,
            total=2,
        )
        design_progress.emit_progress(
            "classDiagramSnapshotAccepted",
            puml="@startuml\nclass Course {\n  + find()\n}\n@enduml",
            phase="operations",
            unit="UC1",
            completed=2,
            total=2,
        )
        return {"status": "need_feedback", "validation": {}}

    assert WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="class_diagram",
        label="Generating the class diagram",
        operation=operation,
    ) == {"status": "need_feedback", "validation": {}}

    preview = live_previews.get("app-1", "command-1", "class_diagram")
    assert preview is not None
    assert preview.revision == 2
    assert preview.unit == "UC1"
    assert preview.image_svg == b"<svg />"
    preview_events = [
        event
        for event in events
        if event["metadata"].get("progress_event") == "classDiagramPreviewUpdated"
    ]
    assert [event["metadata"]["preview_revision"] for event in preview_events] == [1, 2]


def test_live_preview_store_isolates_commands_and_invalidates_cached_svg() -> None:
    store = LivePreviewStore()
    first = store.publish(
        app_id="app-1",
        command_id="command-1",
        stage="class_diagram",
        puml="@startuml\nclass A\n@enduml",
        phase="inventory",
    )
    store.cache_svg("app-1", "command-1", "class_diagram", first.revision, b"svg")
    second = store.publish(
        app_id="app-1",
        command_id="command-1",
        stage="class_diagram",
        puml="@startuml\nclass B\n@enduml",
        phase="operations",
    )
    store.publish(
        app_id="app-1",
        command_id="command-2",
        stage="class_diagram",
        puml="@startuml\nclass C\n@enduml",
        phase="inventory",
    )

    assert second.revision == 2
    assert second.image_svg is None
    assert store.get("app-1", "command-2", "class_diagram").revision == 1


def test_class_preview_endpoints_return_and_cache_the_latest_snapshot(
    monkeypatch,
) -> None:
    app_id = "11111111-1111-4111-8111-111111111111"
    live_previews.clear()
    preview = live_previews.publish(
        app_id=app_id,
        command_id="command-1",
        stage="class_diagram",
        puml="@startuml\nclass Course\n@enduml",
        phase="inventory",
        unit="inventory",
        completed=1,
        total=3,
    )
    monkeypatch.setattr(
        workspace_api.repository,
        "get_command",
        lambda command_id: {"command_id": command_id, "app_id": app_id},
    )
    renders = []
    monkeypatch.setattr(
        workspace_api,
        "render_plantuml",
        lambda puml, image_format: renders.append((puml, image_format)) or b"<svg />",
    )

    payload = workspace_api.get_class_diagram_preview(app_id, "command-1")
    first_image = workspace_api.get_class_diagram_preview_image(
        app_id,
        "command-1",
    )
    second_image = workspace_api.get_class_diagram_preview_image(
        app_id,
        "command-1",
    )

    assert payload == {
        "command_id": "command-1",
        "stage": "class_diagram",
        "revision": preview.revision,
        "phase": "inventory",
        "unit": "inventory",
        "completed": 1,
        "total": 3,
        "puml": "@startuml\nclass Course\n@enduml",
    }
    assert first_image.body == second_image.body == b"<svg />"
    assert renders == [("@startuml\nclass Course\n@enduml", "svg")]


def test_design_operation_marks_a_generated_draft_as_needing_review(
    monkeypatch,
) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    response = WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="sequence_diagram",
        label="Retrying the sequence diagram",
        operation=lambda: {"validation": {"sequence_diagram": {"findings": ["missing flow step"]}}},
    )

    assert response["validation"]["sequence_diagram"]["findings"] == ["missing flow step"]
    assert events[-1]["metadata"]["progress_status"] == "needs_review"
    assert "1 findings require revision" in events[-1]["metadata"]["progress_detail"]


def test_design_api_completed_status_finishes_the_workspace_stage() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "completed",
                "stage": None,
                "class_diagram_puml": "@startuml\n@enduml",
            }
        )
    finally:
        service.shutdown()

    assert result["message"] == "Design artifact generation completed."
    assert "awaiting_input" not in result


def test_design_feedback_status_remains_a_workspace_review_gate() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result({"status": "need_feedback", "stage": "deployment_diagram"})
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["current_stage"] == "deployment_diagram"


def _complete_design_source_state() -> dict[str, object]:
    return {
        config["source_key"]: {"generated": stage}
        for stage, config in artifact_repository.STAGE_ARTIFACTS.items()
        if stage in workspace_module.DESIGN_STAGES
    }


def test_design_progress_hints_offer_advance_only_at_a_ready_active_gate(
    monkeypatch,
) -> None:
    state = _complete_design_source_state()
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": True,
            "active": True,
            "retryable": False,
            "stage": "class_diagram",
        },
    )
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: state)
    monkeypatch.setattr(
        workspace_module,
        "design_readiness_report",
        lambda _state, stages=None: {"status": "READY", "findings": []},
    )

    service = WorkspaceService()
    try:
        hints = service._design_progress_hints("app-1", {"stage": "class_diagram"})
    finally:
        service.shutdown()

    assert hints == {"design_can_advance": True, "design_complete": False}


def test_design_progress_hints_require_every_persisted_design_model(
    monkeypatch,
) -> None:
    state = _complete_design_source_state()
    state.pop(artifact_repository.STAGE_ARTIFACTS["api_spec"]["source_key"])
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": True,
            "active": False,
            "retryable": False,
            "stage": None,
        },
    )
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: state)
    monkeypatch.setattr(
        workspace_module,
        "design_readiness_report",
        lambda _state, stages=None: {"status": "READY", "findings": []},
    )

    service = WorkspaceService()
    try:
        hints = service._design_progress_hints("app-1", {"status": "completed"})
    finally:
        service.shutdown()

    assert hints == {"design_can_advance": False, "design_complete": False}


def test_completed_design_checkpoint_evidence_and_nonpersistent_erd_offer_implementation(
    monkeypatch,
) -> None:
    state = _complete_design_source_state()
    state["extracted_bce_classes"] = {"Classes": []}
    semantic_evidence = {
        "version": "test/v1",
        "modelDigest": "bound-to-current-model",
        "contractDigest": "bound-to-current-contract",
        "status": "pass",
        "verdicts": [{"status": "pass"}],
    }
    graph_values = {
        "app_id": "app-1",
        "class_diagram_check": {
            "findings": [{"issue": "stale hydrated class check"}],
            "semanticEvidence": semantic_evidence,
        },
    }
    readiness_states: list[dict[str, Any]] = []
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": True,
            "active": False,
            "retryable": False,
            "stage": None,
        },
    )
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: dict(state))
    monkeypatch.setattr(
        workspace_module.design_graph,
        "get_state",
        lambda _config: SimpleNamespace(
            config={"configurable": {"thread_id": "app-1", "checkpoint_id": "cp-1"}},
            values=graph_values,
        ),
    )

    def ready_with_evidence(check_state, stages=None):
        readiness_states.append(dict(check_state))
        assert stages is None
        assert check_state["class_diagram_check"]["semanticEvidence"] == semantic_evidence
        return {"status": "READY", "findings": []}

    monkeypatch.setattr(workspace_module, "design_readiness_report", ready_with_evidence)
    service = WorkspaceService()
    try:
        hints = service._design_progress_hints(
            "app-1", {"app_id": "app-1", "status": "completed"}
        )
    finally:
        service.shutdown()

    assert hints == {"design_can_advance": False, "design_complete": True}
    assert readiness_states
    assert "erd" not in WorkspaceService._missing_design_artifacts(state)
    offers = workspace_module.offered_actions(
        {
            "command_id": "design-command",
            "stage": "design",
            "status": "COMPLETED",
            "result": hints,
        }
    )
    assert any(offer.action == "start_implementation" for offer in offers)


@pytest.mark.parametrize(
    "checkpoint_values",
    [
        {"app_id": "other-app", "class_diagram_check": {"semanticEvidence": {"modelDigest": "current"}}},
        {"app_id": "app-1"},
        {"app_id": "app-1", "class_diagram_check": {"semanticEvidence": {"modelDigest": "stale"}}},
    ],
    ids=["wrong-app", "missing-check", "stale-evidence"],
)
def test_completed_checkpoint_with_missing_or_stale_evidence_stays_blocked(
    monkeypatch, checkpoint_values: dict[str, Any]
) -> None:
    state = _complete_design_source_state()
    state["extracted_bce_classes"] = {"Classes": []}
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": True,
            "active": False,
            "retryable": False,
            "stage": None,
        },
    )
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: dict(state))
    monkeypatch.setattr(
        workspace_module.design_graph,
        "get_state",
        lambda _config: SimpleNamespace(
            config={"configurable": {"thread_id": "app-1", "checkpoint_id": "cp-1"}},
            values=checkpoint_values,
        ),
    )
    monkeypatch.setattr(
        workspace_module,
        "design_readiness_report",
        lambda check_state, stages=None: (
            {"status": "READY", "findings": []}
            if check_state.get("class_diagram_check", {}).get("semanticEvidence", {}).get("modelDigest") == "current"
            else {"status": "BLOCKED", "findings": [{"issue": "missing evidence"}]}
        ),
    )

    service = WorkspaceService()
    try:
        hints = service._design_progress_hints(
            "app-1", {"app_id": "app-1", "status": "completed"}
        )
    finally:
        service.shutdown()

    assert hints == {"design_can_advance": False, "design_complete": False}


def test_missing_erd_remains_required_for_entity_model() -> None:
    state = _complete_design_source_state()
    state.pop(artifact_repository.STAGE_ARTIFACTS["erd"]["source_key"])
    state["extracted_bce_classes"] = {
        "Classes": [{"className": "CalculatorRecord", "stereotype": "Entity"}]
    }

    assert "erd" in WorkspaceService._missing_design_artifacts(state)


def test_design_progress_hint_wrapper_preserves_result_content(monkeypatch) -> None:
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "_design_progress_hints",
        lambda _app_id, _result: {
            "design_can_advance": True,
            "design_complete": False,
        },
    )
    try:
        result = service._with_design_progress_hints(
            "app-1", {"message": "Review complete.", "design": {"stage": "class_diagram"}}
        )
    finally:
        service.shutdown()

    assert result == {
        "message": "Review complete.",
        "design": {"stage": "class_diagram"},
        "design_can_advance": True,
        "design_complete": False,
    }


def test_historical_message_reply_uses_referenced_design_stage(monkeypatch) -> None:
    latest = {
        "command_id": "implementation-command",
        "app_id": "app-1",
        "stage": "implementation",
        "status": "FAILED",
        "payload": {},
        "result": {},
    }
    referenced = {
        "command_id": "design-command",
        "app_id": "app-1",
        "stage": "design",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {},
    }
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: latest)
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: referenced if command_id == "design-command" else None,
    )
    monkeypatch.setattr(WorkspaceService, "_fixed_class_resource_choice", lambda *_args: None)
    monkeypatch.setattr(
        workspace_module,
        "build_conversation_context",
        lambda _app_id: SimpleNamespace(workspace={}),
    )
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_args, **_kwargs: workspace_module.Reply(text="Acknowledged."),
    )
    monkeypatch.setattr(workspace_module, "offered_actions", lambda _command: [])

    service = WorkspaceService()
    try:
        _action, _payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"action_id": "design-command", "text": "Re-evaluate the review."},
            stage=None,
        )
    finally:
        service.shutdown()

    assert stage == "design"


def test_start_implementation_rejects_missing_persisted_design_models(
    monkeypatch,
) -> None:
    state = _complete_design_source_state()
    state.pop(artifact_repository.STAGE_ARTIFACTS["api_spec"]["source_key"])
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: state)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "create_job",
        lambda *_args, **_kwargs: pytest.fail("incomplete design must not start a job"),
    )
    command = {
        "command_id": "implementation-command",
        "app_id": "app-1",
        "action": "start_implementation",
        "stage": "implementation",
        "payload": {},
    }

    service = WorkspaceService()
    try:
        with pytest.raises(ValueError, match="Missing required design artifacts: api_spec"):
            service._dispatch(command)
    finally:
        service.shutdown()


def test_multiple_completed_deployment_targets_use_the_artifact_configuration_gate() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "deployment_diagram",
                "artifact_metadata": {
                    "deployment_diagram": {
                        "selection": {"status": "needsInput"},
                        "targets": [
                            {
                                "id": "aws-target",
                                "provider": "aws",
                                "region": "ap-northeast-2",
                                "zones": ["ap-northeast-2a"],
                                "status": "completed",
                            },
                            {
                                "id": "gcp-target",
                                "provider": "gcp",
                                "region": "asia-northeast3",
                                "zones": [],
                                "status": "completed",
                            },
                        ],
                    }
                },
            }
        )
    finally:
        service.shutdown()

    assert result["deployment_configuration_required"] is True
    assert "resource_question" not in result
    assert "target, VM size, and replicas together" in result["message"]


def test_single_deployment_target_requires_sizing_before_advance() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "deployment_diagram",
                "artifact_metadata": {
                    "deployment_diagram": {
                        "selection": {"status": "selected"},
                        "selectedTarget": {"id": "aws-target"},
                        "targets": [{"id": "aws-target", "status": "completed"}],
                    }
                },
            }
        )
    finally:
        service.shutdown()

    assert result["deployment_configuration_required"] is True


def test_completed_deployment_sizing_allows_normal_review_actions() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "deployment_diagram",
                "artifact_metadata": {
                    "deployment_diagram": {
                        "selection": {"status": "selected"},
                        "selectedTarget": {"id": "aws-target"},
                        "sizing": {
                            "status": "completed",
                            "target": {"id": "aws-target"},
                        },
                        "targets": [{"id": "aws-target", "status": "completed"}],
                    }
                },
            }
        )
    finally:
        service.shutdown()

    assert result.get("deployment_configuration_required") is None
    assert result["awaiting_input"] is True


def test_deployment_findings_are_repaired_before_sizing() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "deployment_diagram",
                "validation": {
                    "deployment_diagram": {"findings": ["topology needs repair"]}
                },
                "artifact_metadata": {
                    "deployment_diagram": {
                        "selection": {"status": "selected"},
                        "selectedTarget": {"id": "aws-target"},
                        "targets": [{"id": "aws-target", "status": "completed"}],
                    }
                },
            }
        )
    finally:
        service.shutdown()

    assert result["requires_revision"] is True
    assert result.get("deployment_configuration_required") is None


def test_completed_deployment_configuration_finishes_workspace_wait(
    monkeypatch,
) -> None:
    updates = []
    events = []
    monkeypatch.setattr(
        repository,
        "latest_command",
        lambda _app_id, **_filters: {
            "command_id": "design-command",
            "app_id": "app-1",
            "stage": "design",
            "status": "AWAITING_INPUT",
            "payload": {},
            "result": {"deployment_configuration_required": True},
        },
    )
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda command_id, **changes: updates.append((command_id, changes)),
    )
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda app_id, **values: events.append((app_id, values)),
    )
    monkeypatch.setattr(repository, "now", lambda: "now")
    service = WorkspaceService()
    try:
        service.sync_deployment_configuration("app-1", {"status": "completed"})
    finally:
        service.shutdown()

    assert updates[0][0] == "design-command"
    assert updates[0][1]["status"] == "COMPLETED"
    assert [item["action"] for item in updates[0][1]["result"]["actions"]] == [
        "message",
        "start_implementation",
    ]
    assert events[0][1]["metadata"]["progress_event"] == "commandStateChanged"


def test_deployment_sizing_finds_waiting_gate_after_informational_message(
    monkeypatch,
) -> None:
    updates = []
    calls = []

    def latest(_app_id, **filters):
        calls.append(filters)
        return {
            "command_id": "design-gate",
            "app_id": "app-1",
            "stage": "design",
            "status": "AWAITING_INPUT",
            "payload": {},
            "result": {"deployment_configuration_required": True},
        }

    monkeypatch.setattr(repository, "latest_command", latest)
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda command_id, **changes: updates.append((command_id, changes)),
    )
    monkeypatch.setattr(repository, "append_progress_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(repository, "now", lambda: "now")
    service = WorkspaceService()
    try:
        service.sync_deployment_configuration("app-1", {"status": "completed"})
    finally:
        service.shutdown()

    assert calls == [{"stage": "design", "status": "AWAITING_INPUT"}]
    assert updates[0][0] == "design-gate"
    assert updates[0][1]["status"] == "COMPLETED"


def test_deployment_sizing_ignores_non_deployment_waiting_design_gate(
    monkeypatch,
) -> None:
    updates = []
    monkeypatch.setattr(
        repository,
        "latest_command",
        lambda _app_id, **_filters: {
            "command_id": "class-gate",
            "app_id": "app-1",
            "stage": "design",
            "status": "AWAITING_INPUT",
            "payload": {},
            "result": {"current_stage": "class_diagram"},
        },
    )
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda command_id, **changes: updates.append((command_id, changes)),
    )
    service = WorkspaceService()
    try:
        service.sync_deployment_configuration("app-1", {"status": "completed"})
    finally:
        service.shutdown()

    assert updates == []


def test_design_findings_without_an_artifact_require_revision() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "sequence_diagram",
                "validation": {"sequence_diagram": {"findings": ["missing flow step"]}},
            }
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["requires_revision"] is True
    assert result["blocking_findings"][0]["message"] == "missing flow step"
    assert "can_delegate_repair" not in result
    assert "before continuing" in result["message"]


def test_design_findings_with_a_generated_artifact_still_require_revision() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "sequence_diagram",
                "artifacts": {"sequence_diagram": "@startuml\n@enduml"},
                "validation": {"sequence_diagram": {"findings": ["missing flow step"]}},
            }
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["requires_revision"] is True
    assert result["blocking_findings"][0]["message"] == "missing flow step"
    assert result["findings"] == ["missing flow step"]
    assert "before continuing" in result["message"]


def test_design_findings_cannot_be_waived_by_a_persisted_artifact(monkeypatch) -> None:
    monkeypatch.setattr(
        artifact_repository,
        "load_state",
        lambda _app_id: {"sequence_diagram_puml": "@startuml\n@enduml"},
    )
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "app_id": "app-1",
                "status": "need_feedback",
                "stage": "sequence_diagram",
                "validation": {"sequence_diagram": {"findings": ["missing flow step"]}},
            }
        )
    finally:
        service.shutdown()

    assert result["requires_revision"] is True
    assert result["blocking_findings"][0]["message"] == "missing flow step"
    assert "before continuing" in result["message"]


def test_retry_design_accepts_only_a_failed_design_command(monkeypatch) -> None:
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda _command_id: {
            "command_id": "failed-design",
            "app_id": "app-1",
            "stage": "design",
            "status": "FAILED",
            "result": None,
        },
    )

    service = WorkspaceService()
    try:
        service._validate_action_reference("app-1", "retry_design", {"action_id": "failed-design"})
    finally:
        service.shutdown()


def test_requirements_progress_tracks_only_active_use_case_spec_tasks(
    monkeypatch,
) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    report = WorkspaceService._requirements_progress_reporter("app-1", "command-1")
    report("analysisStepStarted", {"step": "generate_specs"})
    report("specTaskStarted", {"useCaseId": "UC1", "useCaseName": "Browse courses"})
    report("specTaskStarted", {"useCaseId": "UC2", "useCaseName": "Enroll"})
    assert events[-1]["metadata"]["active_spec_tasks"] == [
        {"id": "UC1", "name": "Browse courses"},
        {"id": "UC2", "name": "Enroll"},
    ]

    report(
        "specTaskFinished",
        {"useCaseId": "UC1", "useCaseName": "Browse courses", "status": "completed"},
    )
    assert events[-1]["metadata"]["active_spec_tasks"] == [{"id": "UC2", "name": "Enroll"}]

    report(
        "specTaskFinished",
        {"useCaseId": "UC2", "useCaseName": "Enroll", "status": "completed"},
    )
    assert events[-1]["metadata"]["active_spec_tasks"] == []


def test_requirements_progress_exposes_concurrent_analysis_steps(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    report = WorkspaceService._requirements_progress_reporter("app-1", "command-1")
    report("analysisStepStarted", {"step": "derive_deployment_needs"})
    report("analysisStepStarted", {"step": "extract_resource_constraints"})

    assert events[-1]["metadata"]["active_analysis_steps"] == [
        "derive_deployment_needs",
        "extract_resource_constraints",
    ]

    report(
        "analysisStepFinished",
        {
            "step": "derive_deployment_needs",
            "status": "completed",
            "elapsedSeconds": 1.0,
        },
    )
    assert events[-1]["metadata"]["active_analysis_steps"] == ["extract_resource_constraints"]


def test_workspace_replaces_internal_feedback_prompt_with_english_ui_copy() -> None:
    service = WorkspaceService()
    try:
        result = service._requirements_result(
            {
                "status": "need_feedback",
                "phase": "requirements",
                "feedback_prompt": "내부 에이전트용 문구",
                "requirements": [{"id": "FR1", "type": "FR"}],
                "capability_contract": {"capabilities": [{"id": "public_ingress"}]},
                "resource_intake": {
                    "valid": False,
                    "questions": [{"field": "budget"}],
                },
                "resource_questions": [
                    {
                        "field": "monthlyBudgetUSD",
                        "kind": "missing",
                        "why": "A budget is required for cost filtering.",
                        "question": "What is the monthly budget?",
                        "choices": [],
                    }
                ],
            }
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["kind"] == "question"
    assert result["resource_question"]["field"] == "monthlyBudgetUSD"
    assert result["review_artifacts"] == ["Refined requirements"]
    assert "Before I continue" not in result["message"]
    assert "Waiting for deployment details." not in result["message"]
    assert not any("가" <= character <= "힣" for character in result["message"])


def test_requirements_handoff_exposes_llm_repair_without_allowing_advance() -> None:
    service = WorkspaceService()
    try:
        result = service._requirements_result(
            {
                "status": "need_feedback",
                "phase": "requirements_handoff",
                "blocking_findings": [
                    {
                        "code": "requirements.specification",
                        "stage": "specs",
                        "target_ids": ["UC1"],
                        "message": "Specification has an unresolved finding.",
                        "severity": "error",
                        "repairable": True,
                    }
                ],
                "repair_state": {
                    "status": "ACTIVE",
                    "attempt_count": 3,
                    "accepted_count": 1,
                    "recent_attempts": [],
                },
            }
        )
    finally:
        service.shutdown()

    assert result["requires_revision"] is True
    assert "can_delegate_repair" not in result
    assert result["repair_state"]["attempt_count"] == 3


def test_requirements_handoff_exposes_capability_choices_instead_of_llm_repair() -> None:
    service = WorkspaceService()
    try:
        result = service._requirements_result(
            {
                "status": "need_feedback",
                "phase": "requirements_handoff",
                "blocking_findings": [
                    {
                        "code": "requirements.capability-contract",
                        "stage": "resources",
                        "target_ids": [],
                        "message": "capability contract needs answers for: persistent_storage",
                        "severity": "error",
                        "repairable": False,
                    }
                ],
                "repair_state": {
                    "status": "NEEDS_INPUT",
                    "attempt_count": 2,
                    "accepted_count": 2,
                    "recent_attempts": [],
                },
                "resource_questions": [
                    {
                        "field": "capability:persistent_storage",
                        "kind": "choice",
                        "question": "Should data survive service restarts?",
                        "choices": [
                            {"value": "accepted", "label": "Yes"},
                            {"value": "abstained", "label": "No"},
                        ],
                    }
                ],
            }
        )
    finally:
        service.shutdown()

    assert "can_delegate_repair" not in result
    assert result["resource_question"]["kind"] == "choice"
    assert result["resource_question"]["choices"][0]["value"] == "accepted"
    assert "Should data survive service restarts?" in result["message"]


def test_retry_requirements_accepts_only_a_failed_requirements_command(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda _command_id: {
            "command_id": "failed-requirements",
            "app_id": "app-1",
            "stage": "requirements",
            "status": "FAILED",
            "result": None,
        },
    )

    service = WorkspaceService()
    try:
        service._validate_action_reference(
            "app-1",
            "retry_requirements",
            {"action_id": "failed-requirements"},
        )
    finally:
        service.shutdown()


def test_failed_testing_is_an_actionable_repair_gate(monkeypatch) -> None:
    job = {
        "job_id": "command-1",
        "status": "COMPLETED",
        "implementation_job_id": "implementation-1",
        "result": {
            "passed": False,
            "blocking_findings": [
                {
                    "code": "testing.dynamic",
                    "stage": "testing",
                    "target_ids": [],
                    "message": "FR1 assertion failed",
                    "severity": "error",
                    "repairable": True,
                }
            ],
            "repair_state": {
                "status": "ACTIVE",
                "attempt_count": 1,
                "accepted_count": 0,
                "recent_attempts": [],
            },
        },
    }
    service = WorkspaceService()
    try:
        result = service._testing_result(job)
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert "can_delegate_repair" not in result
    assert result["job"]["implementation_job_id"] == "implementation-1"


@pytest.mark.parametrize(
    ("defect_class", "repair_owner", "blocking_route", "message_fragment"),
    [
        (
            "ENVIRONMENT_DEFECT",
            "environment",
            "environment",
            "runtime environment must be restored",
        ),
        (
            "PLATFORM_DEFECT",
            "platform",
            "platform",
            "failure is in the EasyDep platform",
        ),
        (
            "PLATFORM_OR_DESIGN_DEFECT",
            "platform-or-design",
            "platform-or-design",
            "deployment design and EasyDep platform evidence",
        ),
    ],
)
def test_unrepairable_testing_result_uses_explicit_failure_guidance(
    defect_class: str,
    repair_owner: str,
    blocking_route: str,
    message_fragment: str,
) -> None:
    service = WorkspaceService()
    try:
        result = service._testing_result(
            {
                "job_id": "testing-1",
                "result": {
                    "passed": False,
                    "blocking_findings": [
                        {
                            "message": "gate failed",
                            "repairable": False,
                            "defect_class": defect_class,
                            "repair_owner": repair_owner,
                        }
                    ],
                },
            }
        )
    finally:
        service.shutdown()

    assert "can_delegate_repair" not in result
    assert result["blocking_route"] == blocking_route
    assert message_fragment in result["message"]


def test_start_testing_persists_checkpoint_in_the_command(monkeypatch) -> None:
    """Testing 입력을 실행 전에 현재 Workspace command에 저장한다."""
    updates: list[dict] = []
    events: list[dict] = []
    command = {
        "command_id": "command-1",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {"implementation_job_id": "implementation-1"},
    }
    monkeypatch.setattr(repository, "get_command", lambda _command_id: command)
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: updates.append(changes) or changes,
    )
    monkeypatch.setattr(
        repository,
        "append_progress_event",
        lambda _app_id, **values: events.append(values) or values,
    )

    def run_testing(_app_id, implementation_job_id, **kwargs):
        checkpoint = {
            "implementation_job_id": implementation_job_id,
            "testing_input": {"app_id": "app-1"},
            "current_node": "queued",
            "testing_progress": {
                "last_event": {
                    "progress_event": "testingProgressUpdated",
                    "phase": "prepare",
                    "scope": "phase",
                    "status": "RUNNING",
                    "progress_status": "running",
                    "progress_step_label": "Preparing fixed application snapshot",
                    "updated_at": "2026-09-07T01:00:00Z",
                }
            },
        }
        kwargs["progress"](checkpoint)
        checkpoint["testing_progress"]["last_event"]["updated_at"] = (
            "2026-09-07T01:00:01Z"
        )
        kwargs["progress"](checkpoint)
        return {
            "job_id": kwargs["run_id"],
            "implementation_job_id": implementation_job_id,
            "status": "COMPLETED",
            "result": {"passed": True},
        }

    monkeypatch.setattr(workspace_module, "run_testing", run_testing)

    service = WorkspaceService()
    try:
        result = service._dispatch(command)
    finally:
        service.shutdown()

    assert updates[0]["payload"]["testing_checkpoint"]["current_node"] == "queued"
    assert len(events) == 2
    assert [event["metadata"]["progress_event"] for event in events] == [
        "testingProgressUpdated",
        "testingStepUpdated",
    ]
    assert result["job"]["job_id"] == "command-1"


def test_interrupted_testing_command_reuses_saved_checkpoint(monkeypatch) -> None:
    """재시작 복구는 command에 저장한 Testing 입력을 그대로 사용한다."""
    checkpoint = {
        "implementation_job_id": "implementation-1",
        "testing_input": {"app_id": "app-1"},
        "current_node": "verification",
    }
    observed: dict[str, object] = {}

    def run_testing(_app_id, implementation_job_id, **kwargs):
        observed["implementation_job_id"] = implementation_job_id
        observed["checkpoint"] = kwargs["checkpoint"]
        return {
            "job_id": kwargs["run_id"],
            "status": "COMPLETED",
            "result": {"passed": True},
        }

    monkeypatch.setattr(workspace_module, "run_testing", run_testing)

    service = WorkspaceService()
    try:
        result = service._dispatch(
            {
                "command_id": "command-1",
                "app_id": "app-1",
                "action": "start_testing",
                "stage": "testing",
                "payload": {
                    "implementation_job_id": "implementation-1",
                    "testing_checkpoint": checkpoint,
                },
            }
        )
    finally:
        service.shutdown()

    assert observed["implementation_job_id"] == "implementation-1"
    assert observed["checkpoint"] == checkpoint
    assert result["job"]["job_id"] == "command-1"


def test_testing_sut_failure_is_repaired_automatically_with_testing_evidence(
    monkeypatch,
) -> None:
    """Implementation owns the repair; Testing is not rerun in the same command."""
    prior = {
        "stage": "testing",
        "payload": {},
        "result": {
            "job_id": "testing-1",
            "job": {
                "job_id": "testing-1",
                "implementation_job_id": "implementation-1",
            },
            "blocking_findings": [
                {
                    "message": "FR1 failed",
                    "repairable": True,
                    "defect_class": "SUT_DEFECT",
                    "implementation_owner": "backend",
                    "target_ids": [
                        "api:submitRegistration",
                        "test:plan-digest:UC1",
                    ],
                    "file_hints": ["application/src/RegistrationService.java"],
                    "trace_refs": ["task:implement-registration"],
                    "candidate_digest": "plan-digest",
                    "evidence": {
                        "operationId": "submitRegistration",
                        "statusCode": 500,
                        "finding": {
                            "applicationLogRef": {
                                "ref": ".easydep/testing-evidence/"
                                + ("a" * 64)
                                + ".log"
                            },
                            "applicationLogExcerpt": (
                                "UNIQUE_LOG_PREVIEW_SHOULD_NOT_BE_IN_PROMPT"
                            ),
                        },
                    },
                    "candidate_plan": {
                        "arazzo": "1.1.0",
                        "info": {"title": "Registration", "version": "1.0.0"},
                        "sourceDescriptions": [
                            {
                                "name": "application",
                                "url": "openapi.json",
                                "type": "openapi",
                            }
                        ],
                        "workflows": [
                            {
                                "workflowId": "workflow-UC1",
                                "steps": [
                                    {
                                        "stepId": "submit",
                                        "operationId": "submitRegistration",
                                    }
                                ],
                            }
                        ],
                    },
                    "workflow_inputs": {"workflow-UC1": {}},
                    "input_values": {"workflow-UC1": []},
                    "failed_workflow_id": "workflow-UC1",
                    "failed_step_id": "submit",
                }
            ],
            "repair_state": {"attempt_count": 1},
        },
    }
    observed: dict[str, object] = {}
    monkeypatch.setattr(repository, "get_command", lambda _command_id: prior)
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: observed.update(changes) or changes,
    )
    monkeypatch.setattr(
        artifact_repository,
        "load_state",
        lambda _app_id: {"class_diagram_puml": "A", "api_spec": {"paths": {}}},
    )

    class FakeProjectTools:
        def __init__(self, app_id: str) -> None:
            assert app_id == "app-1"

        def validate_targets(self, refs):
            observed["repair_evidence_refs"] = list(refs)
            return {
                "targets": [
                    {
                        "canonical_ref": ref,
                        "valid": True,
                        "owner": "implementation",
                    }
                    for ref in refs
                ]
            }

        def normalize_revision_targets(self, refs):
            return [
                RevisionTarget(
                    ref=ref,
                    kind=ref.partition(":")[0],
                    element_id=ref.partition(":")[2],
                    owner="implementation",
                    artifact_type="SOURCE_CODE",
                    artifact_version_id=10,
                    display_label=ref,
                )
                for ref in sorted(refs)
            ]

    monkeypatch.setattr(workspace_module, "ProjectTools", FakeProjectTools)

    def request_owner_repair(job_id, *, owner, evidence):
        observed["linked_before_request"] = (observed.get("payload") or {}).get("job_id")
        observed["owner"] = owner
        observed["feedback"] = evidence["stderr"]
        repair_context = json.loads(evidence["testResults"])
        observed["confirmed_target_refs"] = repair_context["confirmedTargetRefs"]
        observed["verification_profile"] = repair_context["verificationProfile"]
        return {"job_id": job_id, "app_id": "app-1"}

    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "request_owner_repair",
        request_owner_repair,
    )
    jobs = {
        job_id: {
            "job_id": job_id,
            "parent_job_id": parent_job_id,
            "base_package": "com.example.app",
            "job_type": "FEEDBACK_REVISION",
            "status": "COMPLETED",
        }
        for job_id, parent_job_id in (
            ("implementation-1", "implementation-0"),
            ("implementation-0", "implementation-old-1"),
            ("implementation-old-1", "implementation-old-2"),
            ("implementation-old-2", "implementation-old-3"),
            ("implementation-old-3", ""),
        )
    }
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda job_id: jobs[job_id],
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_implementation_progress_snapshot",
        staticmethod(
            lambda job: {
                "agent_results": [
                    {
                        "task_id": "apply-source-feedback",
                        "status": "SUCCEEDED",
                        "changed_files": ["application/src/RegistrationService.java"],
                        "raw_response": (
                            "An older repeated edit did not fix the runtime."
                            if str(job.get("job_id") or "").startswith(
                                "implementation-old-"
                            )
                            else "Changed the service, but the runtime still failed."
                        ),
                    }
                ]
            }
        ),
    )

    def monitor_implementation(_self, job, *, command_id=None):
        return {"job_id": job["job_id"], "job": {**job, "status": "COMPLETED"}}

    monkeypatch.setattr(
        WorkspaceService,
        "_monitor_implementation",
        monitor_implementation,
    )

    monkeypatch.setattr(
        WorkspaceService,
        "_run_testing_command",
        lambda *_args, **_kwargs: {"message": "Testing completed.", "job_id": "testing-1"},
    )

    service = WorkspaceService()
    try:
        command = {
            **prior,
            "command_id": "testing-1",
            "app_id": "app-1",
            "action": "start_testing",
        }
        result = service._auto_repair_semantic_result(command, prior["result"])
    finally:
        service.shutdown()

    assert "Preserved functional test plan" in str(observed["feedback"])
    assert "api:submitRegistration" in str(observed["feedback"])
    assert "RegistrationService.java" in str(observed["feedback"])
    assert '"statusCode": 500' in str(observed["feedback"])
    assert "UNIQUE_LOG_PREVIEW_SHOULD_NOT_BE_IN_PROMPT" not in str(
        observed["feedback"]
    )
    assert "Most recent implementation repair outcomes" in str(observed["feedback"])
    assert "Do not repeat the same edit" in str(observed["feedback"])
    assert '"job_id": "implementation-1"' in str(observed["feedback"])
    assert "Changed the service, but the runtime still failed." not in str(
        observed["feedback"]
    )
    assert "Older implementation repair outcomes" in str(observed["feedback"])
    assert '"repetitions": 3' in str(observed["feedback"])
    assert observed["repair_evidence_refs"] == [
        "task:implement-registration",
        "file:application/src/RegistrationService.java",
    ]
    assert observed["verification_profile"]["application_log_ref"] == (
        ".easydep/testing-evidence/" + ("a" * 64) + ".log"
    )
    assert observed["verification_profile"]["failed_workflow_id"] == "workflow-UC1"
    assert observed["verification_profile"]["failed_step_id"] == "submit"
    assert observed["verification_profile"]["workflow_inputs"] == {"workflow-UC1": {}}
    assert observed["verification_profile"]["input_values"] == {"workflow-UC1": []}
    assert observed["confirmed_target_refs"] == [
        "file:application/src/RegistrationService.java",
        "task:implement-registration",
    ]
    assert observed["owner"] == "backend"
    assert observed["linked_before_request"] == "implementation-1"
    assert observed["payload"]["job_id"] == "implementation-1"
    assert result["job_id"] == "testing-1"


def test_implementation_progress_snapshot_uses_owner_and_verifier_phases() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "RUNNING",
                "workflow": {
                    "status": "RUNNING",
                    "currentPhase": "backend",
                    "phases": [
                        {"phaseId": "backend", "status": "RUNNING"},
                        {"phaseId": "frontend", "status": "PENDING"},
                        {"phaseId": "integration", "status": "PENDING"},
                    ],
                    "tasks": [
                        {
                            "taskId": "backend-owner",
                            "taskType": "backend-implementation",
                            "owner": "backend",
                            "phase": "backend",
                            "status": "RUNNING",
                        }
                    ],
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert list(updates) == ["phase-backend", "phase-frontend", "phase-integration"]
    assert updates["phase-backend"]["status"] == "running"
    assert updates["phase-frontend"]["status"] == "pending"
    assert updates["phase-integration"]["status"] == "pending"


def test_implementation_progress_snapshot_reads_live_workflow_and_current_file(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    reports = run_root / "reports"
    event_dir = reports / "agent-executions"
    event_dir.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "backend",
                "phases": [
                    {"phaseId": "backend", "status": "RUNNING"},
                    {"phaseId": "frontend", "status": "PENDING"},
                    {"phaseId": "integration", "status": "PENDING"},
                ],
                "tasks": [
                    {
                        "task_id": "backend-owner",
                        "taskType": "backend-implementation",
                        "owner": "backend",
                        "phase": "backend",
                        "status": "RUNNING",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    journal_path = event_dir / "backend-owner.events.jsonl"
    journal_path.write_text(
        json.dumps(
            {
                "tool": "file_editor",
                "event": {
                    "action": {
                        "path": str(
                            tmp_path
                            / "agent-workspace"
                            / "application"
                            / "src"
                            / "main"
                            / "java"
                            / "BoundaryAdapter.java"
                        )
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (event_dir / "backend-owner.result.json").write_text(
        json.dumps(
            {
                "taskId": "backend-owner",
                "taskType": "backend-implementation",
                "owner": "backend",
                "status": "SUCCEEDED",
                "changedFiles": ["application/src/main/java/BoundaryAdapter.java"],
                "verification": {"command": ["./gradlew", "test"], "exitCode": 0},
                "repairHistory": {"attempts": []},
                "eventJournal": "reports/agent-executions/backend-owner.events.jsonl",
                "rawResponse": "The boundary adapter implementation is complete.",
            }
        ),
        encoding="utf-8",
    )

    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "RUNNING",
                "run_root": str(run_root),
                "workflow": {"status": "READY", "tasks": []},
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert list(updates) == ["phase-backend", "phase-frontend", "phase-integration"]
    assert updates["phase-backend"]["status"] == "running"
    assert updates["phase-backend"]["current_file"] == (
        "application/src/main/java/BoundaryAdapter.java"
    )
    assert updates["phase-backend"]["recent_command"] == "./gradlew test"
    assert updates["phase-backend"]["verification_status"] == "passed"
    assert updates["phase-backend"]["detail"] == (
        "Backend implementation is in progress. Last check passed: ./gradlew test."
    )
    assert progress["current_file"] == ("application/src/main/java/BoundaryAdapter.java")
    assert progress["current_class"] == "BoundaryAdapter"
    assert progress["agent_results"] == [
        {
            "task_id": "backend-owner",
            "task_type": "backend-implementation",
            "owner": "backend",
            "status": "SUCCEEDED",
            "raw_response": "The boundary adapter implementation is complete.",
            "changed_files": ["application/src/main/java/BoundaryAdapter.java"],
            "verification": {"command": ["./gradlew", "test"], "exitCode": 0},
            "repair_history": {"attempts": []},
            "event_journal": "reports/agent-executions/backend-owner.events.jsonl",
        }
    ]


def test_implementation_progress_snapshot_shows_integration_activity() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "FINALIZING",
                "workflow": {
                    "status": "FINALIZING",
                    "currentPhase": "integration",
                    "phases": [
                        {"phaseId": "backend", "status": "SUCCEEDED"},
                        {"phaseId": "frontend", "status": "SUCCEEDED"},
                        {"phaseId": "integration", "status": "RUNNING"},
                    ],
                    "tasks": [],
                    "currentActivity": {
                        "id": "completion-audit",
                        "owner": "integration",
                        "status": "RUNNING",
                        "detail": "Checking the combined application.",
                    },
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert list(updates) == ["phase-backend", "phase-frontend", "phase-integration"]
    assert updates["phase-integration"] == {
        "step": "phase-integration",
        "label": "Integration verification",
        "status": "running",
        "detail": "Checking the combined application.",
        "implementation_owner": "integration",
        "repairing": False,
    }
    assert "current_file" not in progress


def test_implementation_progress_snapshot_shows_owner_repair_in_existing_phase() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "QUEUED",
                "owner_repair": {"owner": "frontend"},
                "workflow": {
                    "status": "COMPLETE",
                    "currentPhase": "integration",
                    "phases": [
                        {"phaseId": "backend", "status": "SUCCEEDED"},
                        {"phaseId": "frontend", "status": "SUCCEEDED"},
                        {"phaseId": "integration", "status": "SUCCEEDED"},
                    ],
                    "tasks": [],
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert list(updates) == ["phase-backend", "phase-frontend", "phase-integration"]
    assert updates["phase-backend"]["status"] == "completed"
    assert updates["phase-frontend"]["status"] == "running"
    assert updates["phase-frontend"]["repairing"] is True
    assert updates["phase-frontend"]["detail"] == (
        "Repairing with the existing frontend owner conversation."
    )
    assert updates["phase-integration"]["status"] == "pending"


@pytest.mark.parametrize(
    ("job_status", "expected_status", "expected_detail"),
    [
        ("FAILED", "failed", "npm ci timed out"),
        ("INTERRUPTED", "failed", "npm ci timed out"),
        ("NEEDS_INPUT", "running", "Integration verification is in progress."),
        ("NEEDS_PLANNER", "failed", "npm ci timed out"),
    ],
)
def test_implementation_progress_snapshot_marks_terminal_failure(
    job_status: str,
    expected_status: str,
    expected_detail: str,
) -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": job_status,
                "error": "npm ci timed out",
                "workflow": {
                    "status": "RUNNING",
                    "currentPhase": "integration",
                    "phases": [
                        {"phaseId": "backend", "status": "SUCCEEDED"},
                        {"phaseId": "frontend", "status": "SUCCEEDED"},
                        {"phaseId": "integration", "status": "RUNNING"},
                    ],
                    "tasks": [],
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert list(updates) == ["phase-backend", "phase-frontend", "phase-integration"]
    assert updates["phase-backend"]["status"] == "completed"
    assert updates["phase-frontend"]["status"] == "completed"
    assert updates["phase-integration"]["status"] == expected_status
    assert progress["progress_status"] == expected_status
    assert progress["progress_detail"] == expected_detail


def test_implementation_progress_snapshot_marks_completed_workflow() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "COMPLETED",
                "workflow": {
                    "status": "COMPLETE",
                    "currentPhase": "integration",
                    "phases": [
                        {"phaseId": "backend", "status": "SUCCEEDED"},
                        {"phaseId": "frontend", "status": "SUCCEEDED"},
                        {"phaseId": "integration", "status": "SUCCEEDED"},
                    ],
                    "tasks": [
                        {
                            "taskId": "backend-owner",
                            "taskType": "backend-implementation",
                            "owner": "backend",
                            "phase": "backend",
                            "status": "SUCCEEDED",
                        },
                        {
                            "taskId": "frontend-owner",
                            "taskType": "frontend-implementation",
                            "owner": "frontend",
                            "phase": "frontend",
                            "status": "SUCCEEDED",
                        },
                    ],
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert list(updates) == ["phase-backend", "phase-frontend", "phase-integration"]
    assert {item["status"] for item in updates.values()} == {"completed"}
    assert progress["progress_status"] == "completed"


def test_rerun_implementation_creates_a_new_job(monkeypatch) -> None:
    calls: list[dict] = []
    events: list[dict] = []
    command_updates: list[dict] = []

    def fake_create_job(app_id, _design, base_package, allow_assumptions):
        calls.append(
            {
                "app_id": app_id,
                "base_package": base_package,
                "allow_assumptions": allow_assumptions,
            }
        )
        return {"job_id": "new-job", "app_id": app_id, "status": "QUEUED"}

    monkeypatch.setattr(workspace_module.implementation_worker, "create_job", fake_create_job)
    monkeypatch.setattr(
        workspace_module.repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append(kwargs),
    )
    monkeypatch.setattr(
        workspace_module.repository,
        "update_command",
        lambda _command_id, **changes: command_updates.append(changes) or changes,
    )
    monkeypatch.setattr(
        workspace_module,
        "artifact_repository",
        SimpleNamespace(
            load_state=lambda _app_id: {
                "class_diagram_puml": "A",
                "api_spec": {"paths": {}},
            }
        ),
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_monitor_implementation",
        lambda _self, job, command_id=None: {"job": job},
    )

    service = WorkspaceService()
    try:
        result = service._dispatch(
            {
                "action": "rerun_implementation",
                "app_id": "app-1",
                "command_id": "cmd-1",
                "stage": "implementation",
                "payload": {
                    "base_package": "com.example.app",
                    "allow_assumptions": True,
                },
            }
        )
    finally:
        service.shutdown()

    assert calls and calls[0]["app_id"] == "app-1"
    assert result["job"]["job_id"] == "new-job"
    assert events and events[0]["metadata"]["reset_implementation_timeline"] is True
    assert command_updates[0]["payload"]["job_id"] == "new-job"


def test_retry_implementation_resumes_the_failed_job_checkpoint(monkeypatch) -> None:
    calls: list[str] = []
    events: list[dict] = []
    job = {
        "job_id": "failed-job",
        "app_id": "app-1",
        "status": "QUEUED",
        "checkpoint_retryable": False,
    }
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {**job, "status": "FAILED", "checkpoint_retryable": True},
    )
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "retry_failed",
        lambda job_id: calls.append(job_id) or job,
    )
    monkeypatch.setattr(
        workspace_module.repository,
        "append_progress_event",
        lambda *args, **kwargs: events.append(kwargs),
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_monitor_implementation",
        lambda _self, current, command_id=None: {
            "job": current,
            "command_id": command_id,
        },
    )

    service = WorkspaceService()
    try:
        result = service._dispatch(
            {
                "action": "retry_implementation",
                "app_id": "app-1",
                "command_id": "retry-command",
                "stage": "implementation",
                "payload": {
                    "action_id": "failed-command",
                    "job_id": "failed-job",
                },
            }
        )
    finally:
        service.shutdown()

    assert calls == ["failed-job"]
    assert result["job"]["job_id"] == "failed-job"
    assert result["command_id"] == "retry-command"
    assert events[0]["metadata"]["status"] == "CHECKPOINT_RETRY_STARTED"


@pytest.mark.parametrize(
    ("checkpoint_retryable", "expected_action"),
    [(True, "retry_implementation"), (False, "start_testing")],
)
def test_failed_testing_offer_uses_only_a_linked_retryable_implementation_checkpoint(
    monkeypatch, checkpoint_retryable: bool, expected_action: str
) -> None:
    command = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "status": "FAILED",
        "payload": {"implementation_job_id": "implementation-1"},
        "result": {},
    }
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {
            "app_id": "app-1",
            "status": "INTERRUPTED",
            "checkpoint_retryable": checkpoint_retryable,
        },
    )
    service = WorkspaceService()
    try:
        offers = service._resolved_offered_actions("app-1", command)
    finally:
        service.shutdown()

    assert [str(offer.action) for offer in offers] == ["message", expected_action]
    assert offers[1].payload == {
        "action_id": "testing-command",
        ("job_id" if checkpoint_retryable else "implementation_job_id"): "implementation-1",
    }


def test_start_testing_after_repair_retry_reuses_the_failed_plan(monkeypatch) -> None:
    previous_job = {
        "job_id": "testing-run",
        "app_id": "app-1",
        "implementation_job_id": "implementation-1",
        "status": "COMPLETED",
        "result": {"passed": False},
    }
    original_testing = {
        "command_id": "testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "status": "COMPLETED",
        "payload": {},
        "result": {
            "job": previous_job,
            "blocking_findings": [
                {
                    "candidate_plan": {
                        "arazzo": "1.1.0",
                        "info": {"title": "Workflow", "version": "1.0.0"},
                    }
                }
            ],
        },
    }
    repair = {
        "command_id": "repair-command",
        "app_id": "app-1",
        "action": "start_implementation",
        "stage": "implementation",
        "status": "FAILED",
        "payload": {"action_id": "testing-command", "job_id": "implementation-2"},
    }
    retry = {
        "command_id": "retry-command",
        "app_id": "app-1",
        "action": "retry_implementation",
        "stage": "implementation",
        "status": "COMPLETED",
        "payload": {"action_id": "repair-command", "job_id": "implementation-2"},
        "result": {"job_id": "implementation-2"},
    }

    commands = {
        "testing-command": original_testing,
        "repair-command": repair,
        "retry-command": retry,
    }
    monkeypatch.setattr(repository, "get_command", commands.get)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id_id: {
            "job_id": "implementation-2",
            "app_id": "app-1",
            "repair_task_type": "testing-dynamic-functional",
        },
    )
    observed: dict[str, object] = {}

    def run_testing_command(
        _self,
        _command,
        implementation_job_id,
        *,
        previous_job=None,
        preserve_test=False,
        repair_task_type=None,
        reset_checkpoint=False,
    ):
        observed.update(
            implementation_job_id=implementation_job_id,
            previous_job=previous_job,
            preserve_test=preserve_test,
            repair_task_type=repair_task_type,
            reset_checkpoint=reset_checkpoint,
        )
        return {"job": {"job_id": "retested"}}

    monkeypatch.setattr(WorkspaceService, "_run_testing_command", run_testing_command)

    service = WorkspaceService()
    try:
        assert service.infer_stage(
            "app-1",
            "retry_implementation",
            {"action_id": "repair-command", "job_id": "implementation-2"},
        ) == "implementation"
        result = service._dispatch(
            {
                "action": "start_testing",
                "app_id": "app-1",
                "command_id": "new-testing-command",
                "stage": "testing",
                "payload": {
                    "action_id": "retry-command",
                    "implementation_job_id": "implementation-2",
                },
            }
        )
    finally:
        service.shutdown()

    assert observed == {
        "implementation_job_id": "implementation-2",
        "previous_job": previous_job,
        "preserve_test": True,
        "repair_task_type": "testing-dynamic-functional",
        "reset_checkpoint": False,
    }
    assert result["job"]["job_id"] == "retested"


@pytest.mark.parametrize(
    ("candidate_status", "candidate_job_id", "stopped", "expects_checkpoint"),
    [
        ("FAILED", "implementation-1", False, True),
        ("FAILED", "other-implementation", False, False),
        ("FAILED", "implementation-1", True, False),
        ("COMPLETED", "implementation-1", False, False),
    ],
    ids=["matching", "other-job", "stopped", "fresh"],
)
def test_start_testing_reuses_only_matching_unstopped_failed_checkpoint(
    monkeypatch,
    candidate_status,
    candidate_job_id,
    stopped,
    expects_checkpoint,
) -> None:
    command = {
        "command_id": "new-testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {
            "action_id": "implementation-command",
            "implementation_job_id": "implementation-1",
        },
    }
    implementation = {
        "command_id": "implementation-command",
        "app_id": "app-1",
        "stage": "implementation",
        "payload": {},
    }
    checkpoint = {
        "implementation_job_id": candidate_job_id,
        "testing_input": {
            "app_id": "app-1",
            "implementation_job_id": candidate_job_id,
        },
    }
    prior = {
        "command_id": "prior-testing-command",
        "app_id": "app-1",
        "stage": "testing",
        "status": candidate_status,
        "payload": {
            "implementation_job_id": candidate_job_id,
            "testing_checkpoint": checkpoint,
            "_stop_requested": stopped,
        },
        "result": {"job": {"status": "COMPLETED", "result": {"passed": False}}},
    }
    updates: list[dict[str, Any]] = []
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: implementation if command_id == "implementation-command" else None,
    )
    monkeypatch.setattr(
        repository,
        "latest_command",
        lambda *_args, **_kwargs: prior,
    )
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: updates.append(changes),
    )
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {"status": "COMPLETED", "repair_task_type": ""},
    )

    def run_testing_command(_self, current, _job_id, **_kwargs):
        observed["checkpoint"] = (current.get("payload") or {}).get("testing_checkpoint")
        return {"job": {"status": "COMPLETED"}}

    monkeypatch.setattr(WorkspaceService, "_run_testing_command", run_testing_command)
    service = WorkspaceService()
    try:
        service._dispatch(command)
    finally:
        service.shutdown()

    assert observed["checkpoint"] == (checkpoint if expects_checkpoint else None)
    assert bool(updates) is expects_checkpoint


def test_start_testing_matching_checkpoint_resumes_the_same_interrupted_job(monkeypatch) -> None:
    command = {
        "command_id": "new-testing-command",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "payload": {
            "action_id": "implementation-command",
            "implementation_job_id": "implementation-1",
        },
    }
    checkpoint = {
        "implementation_job_id": "implementation-1",
        "testing_input": {"app_id": "app-1", "implementation_job_id": "implementation-1"},
    }
    implementation = {
        "command_id": "implementation-command",
        "app_id": "app-1",
        "stage": "implementation",
        "payload": {},
    }
    prior = {
        "command_id": "prior-testing-command",
        "app_id": "app-1",
        "stage": "testing",
        "status": "FAILED",
        "payload": {
            "implementation_job_id": "implementation-1",
            "testing_checkpoint": checkpoint,
        },
        "result": {"job": {"status": "COMPLETED", "result": {"passed": False}}},
    }
    job_states = iter(
        [
            {"status": "INTERRUPTED", "checkpoint_retryable": True},
            {"status": "INTERRUPTED", "checkpoint_retryable": True},
            {"status": "COMPLETED", "repair_task_type": "testing-dynamic-functional"},
        ]
    )
    retried: list[str] = []
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: implementation if command_id == "implementation-command" else None,
    )
    monkeypatch.setattr(repository, "latest_command", lambda *_args, **_kwargs: prior)
    monkeypatch.setattr(repository, "update_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workspace_module.implementation_worker, "get", lambda _job_id: next(job_states))
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "retry_failed",
        lambda job_id: retried.append(job_id) or {"job_id": job_id},
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_monitor_implementation",
        lambda _self, _job, **_kwargs: {"job": {"status": "COMPLETED"}},
    )

    def run_testing_command(_self, current, job_id, **_kwargs):
        observed.update(
            job_id=job_id,
            checkpoint=current["payload"].get("testing_checkpoint"),
            reset_checkpoint=_kwargs.get("reset_checkpoint"),
        )
        return {"job": {"status": "COMPLETED"}}

    monkeypatch.setattr(WorkspaceService, "_run_testing_command", run_testing_command)
    service = WorkspaceService()
    try:
        service._dispatch(command)
    finally:
        service.shutdown()

    assert retried == ["implementation-1"]
    assert observed == {
        "job_id": "implementation-1",
        "checkpoint": None,
        "reset_checkpoint": True,
    }
