"""Testing 서비스의 고정 입력과 Workspace 체크포인트 계약을 확인한다."""

from __future__ import annotations

from typing import Any

import pytest

from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_SOURCE_CODE
from app.testing import service as testing_service
from app.testing.schemas.testing_input import TestingInput as FrozenTestingInput


def _input(implementation_job_id: str) -> FrozenTestingInput:
    return FrozenTestingInput(
        app_id="app-1",
        implementation_job_id=implementation_job_id,
        artifact_version_ids={TYPE_SOURCE_CODE: 1, TYPE_DEPLOYMENT_FILE: 2},
    )


def _completed_implementation(implementation_job_id: str) -> dict[str, Any]:
    return {
        "app_id": "app-1",
        "job_id": implementation_job_id,
        "status": "COMPLETED",
        "artifact_version_ids": {TYPE_SOURCE_CODE: 1, TYPE_DEPLOYMENT_FILE: 2},
        "contract_artifacts": {},
    }


def test_run_testing_freezes_input_before_running(monkeypatch) -> None:
    fixed_input = _input("implementation-1")
    checkpoints: list[dict[str, Any]] = []
    monkeypatch.setattr(
        testing_service.implementation_worker,
        "get_testing_input",
        lambda _job_id: _completed_implementation("implementation-1"),
    )
    monkeypatch.setattr(
        testing_service,
        "capture_testing_input",
        lambda *_args, **_kwargs: fixed_input,
    )

    def run(_run_id, received_input, **kwargs):
        assert received_input == fixed_input
        kwargs["progress"](
            {
                "current_node": "verification",
                "result": {"applicationReport": {"passed": True}},
            }
        )
        return {"passed": True, "blocking_findings": []}, {"status": "COMPLETED"}

    monkeypatch.setattr(testing_service, "_run_test", run)

    job = testing_service.run_testing(
        "app-1",
        "implementation-1",
        run_id="command-1",
        progress=checkpoints.append,
    )

    assert job["job_id"] == "command-1"
    assert job["testing_input"] == fixed_input.model_dump(mode="json")
    assert checkpoints[0]["current_node"] == "queued"
    assert checkpoints[-1]["current_node"] == "verification"


def test_checkpoint_reuses_saved_input_and_application_report(monkeypatch) -> None:
    fixed_input = _input("implementation-1")
    checkpoint = {
        "implementation_job_id": "implementation-1",
        "testing_input": fixed_input.model_dump(mode="json"),
        "current_node": "verification",
        "result": {"applicationReport": {"passed": True}},
        "repair_history": {},
        "previous_findings": [],
    }
    monkeypatch.setattr(
        testing_service.implementation_worker,
        "get_testing_input",
        lambda _job_id: pytest.fail("a checkpoint must not reload the implementation"),
    )

    def run(_run_id, received_input, **kwargs):
        assert received_input == fixed_input
        assert kwargs["resume_node"] == "verification"
        assert kwargs["partial_result"] == checkpoint["result"]
        return {"passed": True}, {"status": "COMPLETED"}

    monkeypatch.setattr(testing_service, "_run_test", run)

    job = testing_service.run_testing(
        "app-1",
        "implementation-1",
        run_id="command-1",
        checkpoint=checkpoint,
    )

    assert job["status"] == "COMPLETED"


def test_implementation_repair_preserves_the_failing_test(monkeypatch) -> None:
    fixed_input = _input("implementation-2")
    previous = {
        "job_id": "command-1",
        "app_id": "app-1",
        "implementation_job_id": "implementation-1",
        "status": "COMPLETED",
        "testing_input": _input("implementation-1").model_dump(mode="json"),
        "repair_history": {},
        "result": {
            "passed": False,
            "verification": {
                "reports": {
                    "dynamicFunctional": {"candidateCode": "def test_saved():\n    assert 1 == 2\n"}
                }
            },
        },
    }
    monkeypatch.setattr(
        testing_service.implementation_worker,
        "get_testing_input",
        lambda _job_id: _completed_implementation("implementation-2"),
    )
    monkeypatch.setattr(
        testing_service,
        "capture_testing_input",
        lambda *_args, **_kwargs: fixed_input,
    )

    def run(_run_id, _testing_input, **kwargs):
        assert "test_saved" in kwargs["partial_result"]["preservedCandidateCode"]
        return {"passed": True}, {"status": "COMPLETED"}

    monkeypatch.setattr(testing_service, "_run_test", run)

    result = testing_service.run_testing(
        "app-1",
        "implementation-2",
        run_id="command-2",
        previous_job=previous,
        preserve_test=True,
    )

    assert result["implementation_job_id"] == "implementation-2"


def test_repair_rejects_a_successful_previous_result(monkeypatch) -> None:
    fixed_input = _input("implementation-1")
    monkeypatch.setattr(
        testing_service.implementation_worker,
        "get_testing_input",
        lambda _job_id: _completed_implementation("implementation-1"),
    )
    monkeypatch.setattr(
        testing_service,
        "capture_testing_input",
        lambda *_args, **_kwargs: fixed_input,
    )

    with pytest.raises(ValueError, match="completed failing"):
        testing_service.run_testing(
            "app-1",
            "implementation-1",
            run_id="command-2",
            previous_job={
                "app_id": "app-1",
                "implementation_job_id": "implementation-1",
                "status": "COMPLETED",
                "result": {"passed": True},
            },
        )
