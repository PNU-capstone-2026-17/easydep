"""Outer demo-only Testing fallback contract."""
from __future__ import annotations

import pytest

from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_SOURCE_CODE
from app.testing import service as testing_service
from app.testing.schemas.testing_input import TestingInput as FrozenTestingInput


def _checkpoint() -> dict:
    return {
        "testing_input": FrozenTestingInput(
            app_id="app-1",
            implementation_job_id="implementation-1",
            artifact_version_ids={TYPE_SOURCE_CODE: 1, TYPE_DEPLOYMENT_FILE: 2},
        ).model_dump(mode="json"),
    }


def _checkpoint_with_workflows() -> dict:
    requirements = [
        {"id": "FR-1", "type": "FR", "statement": "The user can check status."},
        {"id": "FR-2", "type": "FR", "statement": "The user can list status."},
    ]
    use_cases = {
        "use_case_specs": [
            {"use_case_id": "UC-1", "requirement_ids": ["FR-1"], "name": "Check status"},
            {"use_case_id": "UC-2", "requirement_ids": ["FR-2"], "name": "List status"},
        ],
        "traceability": {
            "requirements": {"FR-1": {"use_cases": ["UC-1"]}, "FR-2": {"use_cases": ["UC-2"]}}
        },
    }
    openapi = {
        "openapi": "3.0.3",
        "info": {"title": "Status", "version": "1.0.0"},
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "x-easydep-use-case-ids": ["UC-1", "UC-2"],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    return {
        "testing_input": FrozenTestingInput(
            app_id="app-1",
            implementation_job_id="implementation-1",
            artifact_version_ids={TYPE_SOURCE_CODE: 1, TYPE_DEPLOYMENT_FILE: 2},
            contract_artifacts={
                "requirements": {"content": requirements},
                "use_cases": {"content": use_cases},
                "openapi": {"content": openapi},
            },
        ).model_dump(mode="json"),
    }


def _checkpoint_with_saved_plans() -> dict:
    """Model a resumed run whose real multi-step plan cannot be rebuilt."""

    checkpoint = _checkpoint()
    checkpoint["testing_progress"] = {
        "plans": {
            "workflow-UC-1": {
                "workflow_id": "workflow-UC-1",
                "use_case_id": "UC-1",
                "use_case_name": "Student signs in",
                "status": "PENDING",
            },
            "workflow-UC-2": {
                "workflow_id": "workflow-UC-2",
                "use_case_id": "UC-2",
                "use_case_name": "Student views attendance",
                "status": "PENDING",
            },
        },
        "plan_counts": {"total": 2, "pending": 2},
    }
    return checkpoint


def _assert_demo_pass(job: dict) -> None:
    report = job["result"]
    verification = report["verification"]
    dynamic = verification["reports"]["dynamicFunctional"]
    assert job["status"] == "COMPLETED"
    assert report["passed"] is True
    assert report["validationSkipped"] is True
    assert report["demoFallback"] is True
    assert report["executedWorkflowCount"] == 0
    assert report["blocking_findings"] == []
    assert verification["errors"] == []
    assert dynamic["candidatePlan"]["workflows"] == []
    assert dynamic["executedWorkflowCount"] == 0
    for item in (
        verification["reports"]["static"],
        verification["reports"]["static"]["trivyScan"],
        verification["reports"]["static"]["deploymentPackage"],
        verification["reports"]["iac"],
        dynamic,
    ):
        assert item["status"] == "PASSED"
        assert item["gateStatus"] == "PASS"
    assert verification["reports"]["static"]["trivyScan"]["commands"][0]["planned"] is True
    assert verification["reports"]["static"]["deploymentPackage"]["checkNames"]


def test_demo_fallback_handles_an_early_planning_exception(monkeypatch):
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    monkeypatch.setattr(
        testing_service.implementation_worker,
        "get_testing_input",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError("planning unavailable")),
    )

    job = testing_service.run_testing("app-1", "implementation-1", run_id="testing-1")

    _assert_demo_pass(job)


def test_demo_fallback_handles_a_late_verification_exception(monkeypatch):
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    monkeypatch.setattr(
        testing_service,
        "_run_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("executor unavailable")),
    )

    checkpoints: list[dict] = []
    job = testing_service.run_testing(
        "app-1", "implementation-1", run_id="testing-1", checkpoint=_checkpoint(),
        progress=checkpoints.append,
    )

    _assert_demo_pass(job)
    assert job["testing_input"]["app_id"] == "app-1"
    terminal = [
        checkpoint for checkpoint in checkpoints
        if checkpoint.get("current_node") == "verification_complete"
    ]
    assert len(terminal) == 1
    assert terminal[0]["result"] == job["result"]
    assert terminal[0]["repair_history"] == job["repair_history"]


def test_demo_fallback_persists_each_terminal_workflow_before_completion(monkeypatch):
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    monkeypatch.setattr(
        testing_service,
        "_run_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("executor unavailable")),
    )
    checkpoints: list[dict] = []

    job = testing_service.run_testing(
        "app-1",
        "implementation-1",
        run_id="testing-1",
        checkpoint=_checkpoint_with_workflows(),
        progress=checkpoints.append,
    )

    workflow_events = [
        checkpoint["testing_progress"]["last_event"]
        for checkpoint in checkpoints
        if checkpoint.get("current_node") == "verification"
        and checkpoint.get("testing_progress", {}).get("last_event", {}).get("scope") == "workflow"
    ]
    assert [
        (
            event["workflow_id"],
            event["status"],
            event.get("completed_workflows"),
            event["total_workflows"],
        )
        for event in workflow_events
    ] == [
        ("workflow-UC-1", "PENDING", None, 2),
        ("workflow-UC-2", "PENDING", None, 2),
        ("workflow-UC-1", "PASS", 1, 2),
        ("workflow-UC-2", "PASS", 2, 2),
    ]
    assert job["testing_progress"]["workflow_counts"]["passed"] == 2
    assert job["testing_progress"]["workflow_counts"]["completed"] == 2
    lifecycle = [
        (
            checkpoint["current_node"],
            checkpoint["testing_progress"]["last_event"]["phase"],
            checkpoint["testing_progress"]["last_event"]["scope"],
        )
        for checkpoint in checkpoints
        if checkpoint.get("current_node") in {"verification", "verification_complete"}
    ]
    assert lifecycle == [
        ("verification", "dynamic", "workflow"),
        ("verification", "dynamic", "workflow"),
        ("verification", "dynamic", "workflow"),
        ("verification", "dynamic", "workflow"),
        ("verification", "summary", "phase"),
        ("verification_complete", "summary", "phase"),
    ]
    assert sum(
        checkpoint.get("current_node") == "verification_complete"
        for checkpoint in checkpoints
    ) == 1


def test_demo_fallback_uses_saved_plan_identities_when_plan_cannot_be_rebuilt(monkeypatch):
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    monkeypatch.setattr(
        testing_service,
        "_run_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("executor unavailable")),
    )
    checkpoints: list[dict] = []

    job = testing_service.run_testing(
        "app-1",
        "implementation-1",
        run_id="testing-1",
        checkpoint=_checkpoint_with_saved_plans(),
        progress=checkpoints.append,
    )

    dynamic = job["result"]["verification"]["reports"]["dynamicFunctional"]
    assert [item["workflowId"] for item in dynamic["workflows"]] == [
        "workflow-UC-1",
        "workflow-UC-2",
    ]
    assert [item["useCaseName"] for item in dynamic["workflows"]] == [
        "Student signs in",
        "Student views attendance",
    ]
    workflow_events = [
        checkpoint["testing_progress"]["last_event"]
        for checkpoint in checkpoints
        if checkpoint.get("current_node") == "verification"
        and checkpoint.get("testing_progress", {}).get("last_event", {}).get("scope") == "workflow"
    ]
    assert [
        (
            event["workflow_id"],
            event["status"],
            event.get("completed_workflows"),
            event["total_workflows"],
        )
        for event in workflow_events
    ] == [
        ("workflow-UC-1", "PENDING", None, 2),
        ("workflow-UC-2", "PENDING", None, 2),
        ("workflow-UC-1", "PASS", 1, 2),
        ("workflow-UC-2", "PASS", 2, 2),
    ]
    assert job["testing_progress"]["workflow_counts"]["completed"] == 2
    assert job["testing_progress"]["workflow_counts"]["passed"] == 2


def test_demo_fallback_empty_plan_emits_terminal_progress_event(monkeypatch):
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    monkeypatch.setattr(
        testing_service,
        "_run_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("executor unavailable")),
    )
    checkpoints: list[dict] = []

    job = testing_service.run_testing(
        "app-1",
        "implementation-1",
        run_id="testing-1",
        checkpoint=_checkpoint(),
        progress=checkpoints.append,
    )

    assert job["testing_progress"]["last_event"]["phase"] == "summary"
    assert job["testing_progress"]["last_event"]["status"] == "PASS"
    terminal = [
        checkpoint for checkpoint in checkpoints
        if checkpoint.get("current_node") == "verification_complete"
    ]
    assert len(terminal) == 1
    assert terminal[0]["testing_progress"]["last_event"]["phase"] == "summary"


def test_demo_fallback_does_not_mask_interrupt_or_disabled_failures(monkeypatch):
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    monkeypatch.setattr(
        testing_service,
        "_run_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    interrupt_checkpoints: list[dict] = []
    with pytest.raises(KeyboardInterrupt):
        testing_service.run_testing(
            "app-1", "implementation-1", run_id="testing-1", checkpoint=_checkpoint(),
            progress=interrupt_checkpoints.append,
        )
    assert not [
        checkpoint for checkpoint in interrupt_checkpoints
        if checkpoint.get("current_node") == "verification_complete"
    ]

    class RuntimeInterruptedError(RuntimeError):
        status = "INTERRUPTED"

    monkeypatch.setattr(
        testing_service,
        "_run_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeInterruptedError()),
    )
    _assert_demo_pass(testing_service.run_testing(
        "app-1", "implementation-1", run_id="testing-1", checkpoint=_checkpoint(),
    ))

    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "false")
    monkeypatch.setattr(
        testing_service,
        "_run_test",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("verification failed")),
    )
    with pytest.raises(RuntimeError, match="verification failed"):
        testing_service.run_testing(
            "app-1", "implementation-1", run_id="testing-1", checkpoint=_checkpoint(),
        )
