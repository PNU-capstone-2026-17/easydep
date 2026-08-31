from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.implementation.application.feedback import assess_feedback_eligibility
from app.implementation.application.jobs import ImplementationWorker
from app.implementation.application.prototype import PrototypeClient, PrototypeExecutionError
from app.implementation.config import ImplementationSettings
from app.implementation.generation.orchestrator import PrototypeOrchestrator, load_job
from app.implementation.interfaces.http import router
from tests.class_design_fixtures import typed_class_model_payload


def test_initial_job_allows_void_control_with_transport_error_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = ImplementationWorker(settings(tmp_path))
    worker._plan = lambda *_args, **_kwargs: None
    monkeypatch.setattr(
        "app.implementation.application.jobs.design_readiness_report",
        lambda _design: {"status": "READY", "findings": [], "stages": []},
    )
    try:
        record = worker.create_job(
            "app-1",
            {
                "class_diagram_puml": """
                @startuml
                class DropControl <<Control>> {
                  + drop(studentId : String, courseId : String): void
                }
                @enduml
                """,
                "api_spec": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/students/{studentId}/enrollments/{courseId}": {
                            "delete": {
                                "responses": {
                                    "204": {},
                                    "400": {},
                                    "403": {},
                                    "404": {},
                                    "409": {},
                                    "422": {},
                                    "500": {},
                                },
                                "x-easydep-control": {"control": "DropControl", "method": "drop"},
                            }
                        }
                    },
                },
                "extracted_bce_classes": {"Classes": [{"className": "DropControl"}]},
                "sequence_diagram_model": {"Diagrams": [{"use_case_id": "UC1"}]},
                "api_spec_model": {"Endpoints": [{"path": "/students"}]},
            },
            "com.example",
            False,
        )
    finally:
        worker.shutdown()

    assert record["status"] == "QUEUED"
    assert record["design_validation"]["findings"] == []


def test_needs_input_workflow_exposes_the_design_blocker_in_job_error(tmp_path: Path) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    record = {
        "job_id": "job-1",
        "status": "RUNNING",
        "updated_at": "before",
        "run_root": str(tmp_path / "run"),
    }
    try:
        implementation_worker._apply_workflow(
            record,
            {
                "status": "NEEDS_INPUT",
                "blockingReason": "Implementation planning is blocked by unresolved Control persistence contracts.",
                "blockingDetails": [
                    {
                        "control": "CourseCatalogControl",
                        "persistentEntities": ["Course"],
                    }
                ],
            },
        )
    finally:
        implementation_worker.shutdown()

    assert record["status"] == "NEEDS_INPUT"
    assert "Control persistence contracts" in record["error"]
    assert record["blocking_details"] == [
        {
            "control": "CourseCatalogControl",
            "persistentEntities": ["Course"],
        }
    ]


def test_ready_workflow_with_all_tasks_succeeded_is_completed(tmp_path: Path) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    record = {
        "job_id": "job-ready",
        "status": "RUNNING",
        "updated_at": "before",
        "run_root": str(tmp_path / "run"),
    }
    try:
        implementation_worker._apply_workflow(
            record,
            {
                "status": "READY",
                "nextRunnableTasks": [],
                "phases": [{"status": "SUCCEEDED"}, {"status": "UNPLANNED"}],
                "tasks": [{"status": "SUCCEEDED"}],
            },
        )
    finally:
        implementation_worker.shutdown()

    assert record["status"] == "COMPLETED"


def test_ready_workflow_with_pending_tasks_remains_ready(tmp_path: Path) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    record = {
        "job_id": "job-planned",
        "status": "PLANNING",
        "updated_at": "before",
        "run_root": str(tmp_path / "run"),
    }
    try:
        implementation_worker._apply_workflow(
            record,
            {
                "status": "READY",
                "nextRunnableTasks": ["task-1"],
                "phases": [{"status": "PENDING"}],
                "tasks": [{"status": "PENDING"}],
            },
        )
    finally:
        implementation_worker.shutdown()

    assert record["status"] == "READY"


def test_completed_job_is_not_published_before_artifacts_are_persisted(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, str]] = []
    record = {
        "job_id": "job-completed",
        "status": "READY",
        "run_root": str(tmp_path / "run-root"),
        "job_path": str(tmp_path / "job.json"),
    }

    class Client:
        @staticmethod
        def run_phase(*_args: object) -> dict:
            return {
                "status": "COMPLETE",
                "nextRunnableTasks": [],
                "phases": [{"status": "SUCCEEDED"}],
                "tasks": [{"status": "SUCCEEDED"}],
            }

        @staticmethod
        def transmission_request(_run_root: Path) -> None:
            return None

        @staticmethod
        def cancel_all() -> None:
            return None

    worker = ImplementationWorker(settings(tmp_path))
    worker.client = Client()
    worker._read = lambda _job_id: record
    worker._write = lambda current: events.append(("write", current["status"]))
    worker._persist_outputs = lambda current: events.append(("persist", current["status"]))
    worker._fail = lambda _record, error: pytest.fail(str(error))

    try:
        worker._run("job-completed", "approval.json", False)
    finally:
        worker.shutdown()

    assert events == [("write", "RUNNING"), ("persist", "COMPLETED")]


def test_job_execution_lease_rejects_a_second_process(
    tmp_path: Path,
) -> None:
    """같은 Job은 첫 실행권을 반납하기 전까지 다시 시작하지 않는다."""
    worker = ImplementationWorker(settings(tmp_path))
    try:
        first = worker._claim_job_execution("job-one")
        assert first is not None
        assert worker._claim_job_execution("job-one") is None
        worker._release_job_execution("job-one", first)
        second = worker._claim_job_execution("job-one")
        assert second is not None
        worker._release_job_execution("job-one", second)
    finally:
        worker.shutdown()


def test_feedback_eligibility_rejects_design_contract_changes() -> None:
    result = assess_feedback_eligibility("OpenAPI 엔드포인트와 응답 스키마를 변경해줘")

    assert result["status"] == "UNSUITABLE"
    assert result["matches"][0]["code"] == "OPENAPI_CONTRACT_CHANGE"


def test_feedback_eligibility_accepts_existing_contract_behavior_change() -> None:
    result = assess_feedback_eligibility(
        "배송이 시작된 주문은 취소 요청을 거절하고 테스트를 보강해줘"
    )

    assert result["status"] == "ELIGIBLE"


def test_unsuitable_feedback_does_not_create_an_execution_run(monkeypatch, tmp_path: Path) -> None:
    source_snapshot = {
        "version_no": 3,
        "files": {
            "src/main/java/com/example/OrderService.java": {"content": "class OrderService {}"}
        },
    }
    monkeypatch.setattr(
        "app.implementation.application.jobs.artifact_repository.load_file_snapshot",
        lambda *_args, **_kwargs: source_snapshot,
    )
    implementation_worker = ImplementationWorker(settings(tmp_path))
    implementation_worker.client.prepare_feedback_job = lambda *_args, **_kwargs: pytest.fail(
        "Unsuitable feedback must not create a feedback run"
    )
    try:
        record = implementation_worker.create_feedback_job(
            "app-1", {}, "API 명세의 엔드포인트를 추가해줘", "com.example", False
        )
    finally:
        implementation_worker.shutdown()

    assert record["status"] == "NEEDS_INPUT"
    assert record["feedback_eligibility"]["status"] == "UNSUITABLE"
    assert record["prompt"]["kind"] == "upstream_revision_confirmation"
    assert record["prompt"]["requiredStage"] == "design"
    assert record["prompt"]["question"]
    assert (
        tmp_path / ".easydep/implementation-runs" / record["job_id"] / "feedback-eligibility.json"
    ).is_file()


def test_requirement_feedback_asks_before_returning_to_requirements() -> None:
    result = assess_feedback_eligibility("요구사항에 대기자 우선순위 업무 규칙을 추가해줘")

    assert result["status"] == "UNSUITABLE"
    assert result["requiredStage"] == "requirements"
    assert "requirements 단계" in result["confirmationQuestion"]


def test_delegated_approval_covers_initial_and_cross_phase_repair(tmp_path: Path) -> None:
    run = tmp_path / "run_repair"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "input_hash": "input-hash",
                "implementation_tasks": [],
            }
        ),
        encoding="utf-8",
    )
    (reports / "repair-plan.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "revision": 1,
                        "ownerTaskIds": ["repair-api"],
                        "revalidationTaskIds": ["repair-e2e"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps(
            {
                "delegatedRepairApprovals": True,
                "delegationScope": {
                    "runId": run.name,
                    "inputHash": "input-hash",
                    "initialTaskIds": ["initial-wiring"],
                },
            }
        ),
        encoding="utf-8",
    )
    record = {
        "run_root": str(run),
        "transmission_request": {"tasks": [{"taskId": "repair-api"}, {"taskId": "repair-e2e"}]},
        "workflow": {"tasks": [{"attempts": 200}]},
    }

    assert ImplementationWorker._delegated_execution_is_active(record, str(approval))
    record["transmission_request"] = {"tasks": [{"taskId": "initial-wiring"}]}
    assert ImplementationWorker._delegated_execution_is_active(record, str(approval))
    (reports / "repair-plan.json").write_text(
        json.dumps({"status": "STALLED", "entries": []}), encoding="utf-8"
    )
    assert not ImplementationWorker._delegated_execution_is_active(record, str(approval))

    # 자동 승인된 다음 묶음은 AWAITING_APPROVAL을 외부에 노출하지 않고, 앞선 실패
    # task를 실제로 재실행할 수 있도록 retry_failed=True로 이어져야 한다.
    (reports / "repair-plan.json").write_text(
        json.dumps(
            {
                "entries": [{"ownerTaskIds": ["repair-api"], "revalidationTaskIds": []}],
            }
        ),
        encoding="utf-8",
    )
    worker = ImplementationWorker(settings(tmp_path))
    submitted: list[tuple[object, ...]] = []
    worker.executor.submit = lambda *args: submitted.append(args)  # type: ignore[method-assign]
    worker.client.run_phase = lambda *_args: {"status": "READY"}
    worker.client.transmission_request = lambda *_args: {
        "tasks": [{"taskId": "repair-api"}],
    }
    job = {
        "job_id": "delegated-job",
        "app_id": "app-1",
        "job_path": str(tmp_path / "job.json"),
        "run_root": str(run),
        "status": "READY",
        "transmission_request": None,
        "created_at": "now",
        "updated_at": "now",
    }
    worker._write(job)
    try:
        worker._run("delegated-job", str(approval), False)
        resumed = worker._read("delegated-job")
    finally:
        worker.shutdown()

    assert resumed["status"] == "QUEUED"
    assert submitted and submitted[0][-1] is True


def test_cancel_terminates_active_process_and_preserves_cancelled_status(
    monkeypatch, tmp_path: Path
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    cancelled = []
    monkeypatch.setattr(
        implementation_worker.client,
        "cancel",
        lambda job_id: cancelled.append(job_id) or True,
    )
    record = {
        "job_id": "job-cancel",
        "app_id": "app-1",
        "status": "RUNNING",
        "job_path": str(tmp_path / "job.json"),
        "run_root": None,
        "workflow": None,
        "transmission_request": None,
        "error": None,
        "created_at": "now",
        "updated_at": "now",
    }
    implementation_worker._write(record)
    try:
        result = implementation_worker.cancel("job-cancel")
        implementation_worker._fail(record, RuntimeError("terminated subprocess"))
        persisted = implementation_worker._read("job-cancel")
    finally:
        implementation_worker.shutdown()

    assert result["status"] == "CANCELLED"
    assert cancelled == ["job-cancel"]
    assert persisted["status"] == "CANCELLED"


def test_write_uses_unique_temp_and_falls_back_when_windows_replace_is_denied(
    monkeypatch, tmp_path: Path
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    record = {
        "job_id": "job-replace-permission",
        "status": "QUEUED",
        "created_at": "now",
    }
    original_replace = os.replace
    attempts = {"count": 0}

    def deny_replace(source, destination):
        attempts["count"] += 1
        if attempts["count"] <= 3:
            raise PermissionError(5, "Access is denied")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", deny_replace)
    try:
        implementation_worker._write(record)
        assert implementation_worker._read(record["job_id"])["status"] == "QUEUED"
        assert attempts["count"] == 3
        assert not list((tmp_path / record["job_id"]).glob("*.tmp"))
    finally:
        implementation_worker.shutdown()


def settings(repository_root: Path) -> ImplementationSettings:
    repository_root.mkdir(parents=True, exist_ok=True)
    python = repository_root / "python.exe"
    python.write_text("test", encoding="utf-8")
    return ImplementationSettings(
        repository_root=repository_root,
        work_root=repository_root / ".easydep" / "implementation-runs",
        python_executable=python,
        max_workers=1,
        model="nvidia_nim/openai/gpt-oss-120b",
        base_url="https://integrate.api.nvidia.com/v1",
        command_timeout_seconds=60,
    )


def test_run_phase_uses_linux_runner_when_image_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = PrototypeClient(settings(tmp_path))
    run_root = tmp_path / ".easydep" / "run_123"
    job_path = tmp_path / ".easydep" / "job" / "job.json"
    approval_path = job_path.with_name("approval.json")
    for path in (run_root, job_path.parent):
        path.mkdir(parents=True, exist_ok=True)
    job_path.write_text("{}", encoding="utf-8")
    approval_path.write_text("{}", encoding="utf-8")
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "app.implementation.application.prototype.configured_runner_image",
        lambda: "runner:test",
    )

    def fake_call(
        command: list[str], operation_id: str | None, environment: dict[str, str]
    ) -> dict[str, object]:
        observed.update(command=command, operation_id=operation_id, environment=environment)
        return {"status": "RUNNING"}

    monkeypatch.setattr(client, "_call_command", fake_call)

    result = client.run_phase(run_root, job_path, approval_path, retry_failed=True)

    command = observed["command"]
    assert isinstance(command, list)
    assert command[-7:] == [
        "cli",
        "run-workflow",
        "/easydep-workspace/.easydep/run_123",
        "/easydep-workspace/.easydep/job/job.json",
        "--approval",
        "/easydep-workspace/.easydep/job/approval.json",
        "--retry-failed",
    ]
    assert observed["operation_id"] == "job"
    assert result == {"status": "RUNNING"}


def test_prepare_job_materializes_all_available_design_inputs(tmp_path: Path) -> None:
    client = PrototypeClient(settings(tmp_path))
    path = client.prepare_job(
        "job-1",
        "12345678-0000-0000-0000-000000000000",
        {
            "class_diagram_puml": "@startuml\nclass Order\n@enduml",
            "sequence_diagram_puml": (
                "@startuml UC1\nA -> B : first()\n@enduml\n\n"
                "@startuml UC2\nA -> B : second()\n@enduml"
            ),
            "api_spec": {"openapi": "3.0.3", "paths": {}},
            "extracted_bce_classes": typed_class_model_payload(),
            "sequence_diagram_model": {"Diagrams": []},
            "api_spec_model": {"Endpoints": []},
            "erd_puml": "@startuml\nentity orders\n@enduml",
            "deployment_diagram_puml": "@startuml\nnode app\n@enduml",
            "deployment_diagram_bundle": {
                "schemaVersion": "easydep-deployment-diagram",
                "status": "completed",
                "resourceSpec": {"provider": "azure", "resources": []},
                "projections": [],
            },
            "resource_spec": {"cloud": "azure"},
        },
        "com.example.orders",
        False,
    )
    job = json.loads(path.read_text(encoding="utf-8"))
    assert set(job["inputs"]) == {
        "bceClass",
        "sequence",
        "openapi",
        "erd",
        "deployment",
        "deploymentBundle",
        "cloud",
        "bceModel",
        "sequenceModel",
        "apiModel",
    }
    assert job["generation"]["basePackage"] == "com.example.orders"
    assert job["requiredInputs"] == [
        "bceModel",
        "sequenceModel",
        "apiModel",
        "openapi",
    ]
    assert (tmp_path / job["inputs"]["openapi"]).is_file()
    sequence_path = tmp_path / job["inputs"]["sequence"]
    assert sequence_path.name == "sequence-diagrams.puml"
    assert sequence_path.read_text(encoding="utf-8").count("@startuml") == 2
    assert "tools" not in job
    assert job["progressPath"].endswith("generation-progress.json")
    assert job["verification"] == {"compile": False}


def test_live_generation_progress_is_exposed_without_host_path(tmp_path: Path) -> None:
    job_path = tmp_path / "job" / "job.json"
    job_path.parent.mkdir(parents=True)
    job_path.write_text("{}", encoding="utf-8")
    (job_path.parent / "generation-progress.json").write_text(
        json.dumps(
            {
                "status": "VERIFYING",
                "message": "생성된 백엔드를 컴파일하고 패키징하고 있습니다.",
                "updatedAt": "2026-08-16T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    public = ImplementationWorker.public_record(
        ImplementationWorker._with_live_generation_progress(
            {"status": "GENERATING", "job_path": str(job_path), "updated_at": "old"}
        )
    )

    assert public["status"] == "VERIFYING"
    assert public["progress"]["message"].startswith("생성된 백엔드")
    assert "job_path" not in public


@pytest.mark.parametrize("terminal_status", ["FAILED", "NEEDS_PLANNER"])
def test_stopped_job_retries_the_same_approved_checkpoint(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    job_id = "failed-job"
    job_dir = implementation_worker.settings.work_root / job_id
    job_path = job_dir / "job.json"
    run_root = job_dir / "outputs" / "run_checkpoint"
    reports = run_root / "reports"
    reports.mkdir(parents=True)
    job_path.write_text("{}", encoding="utf-8")
    (job_dir / "approval.json").write_text(
        json.dumps({"requestId": "request", "approved": True}),
        encoding="utf-8",
    )
    (reports / "run-manifest.json").write_text(
        json.dumps({"status": "SUCCEEDED"}),
        encoding="utf-8",
    )
    (reports / "workflow-state.json").write_text(
        json.dumps({"status": terminal_status}),
        encoding="utf-8",
    )
    implementation_worker._write(
        {
            "job_id": job_id,
            "app_id": "app-1",
            "status": terminal_status,
            "job_path": str(job_path),
            "run_root": str(run_root),
            "workflow": {"status": terminal_status},
            "error": "provider failed",
            "updated_at": "before",
        }
    )
    submitted: list[tuple[object, ...]] = []
    implementation_worker.executor.submit = (  # type: ignore[method-assign]
        lambda *args, **_kwargs: submitted.append(args)
    )

    try:
        before = implementation_worker.get(job_id)
        retried = implementation_worker.retry_failed(job_id)
        persisted = implementation_worker._read(job_id)
    finally:
        implementation_worker.shutdown()

    assert before["checkpoint_retryable"] is True
    assert retried["job_id"] == job_id
    assert retried["status"] == "QUEUED"
    assert retried["checkpoint_retryable"] is False
    assert persisted["checkpoint_retry_count"] == 1
    assert "error" not in persisted
    assert submitted == [
        (
            implementation_worker._run,
            job_id,
            str(job_dir / "approval.json"),
            True,
        )
    ]


def test_failed_job_without_approved_checkpoint_requires_a_fresh_run(
    tmp_path: Path,
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    job_id = "generation-failed"
    implementation_worker._write(
        {
            "job_id": job_id,
            "app_id": "app-1",
            "status": "FAILED",
            "job_path": str(tmp_path / "missing-job.json"),
            "run_root": None,
            "error": "generation failed",
        }
    )
    try:
        assert implementation_worker.get(job_id)["checkpoint_retryable"] is False
        with pytest.raises(RuntimeError, match="no approved execution checkpoint"):
            implementation_worker.retry_failed(job_id)
    finally:
        implementation_worker.shutdown()


def test_initial_job_is_blocked_when_design_has_no_verifiable_models(tmp_path: Path) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    implementation_worker.client.prepare_job = lambda *_args, **_kwargs: pytest.fail(
        "An unresolved design must not prepare or run an implementation job"
    )
    try:
        record = implementation_worker.create_job(
            "app-1",
            {"class_diagram_puml": "class Cart", "api_spec": {"openapi": "3.1.0", "paths": {}}},
            "com.example",
            False,
        )
    finally:
        implementation_worker.shutdown()

    assert record["status"] == "NEEDS_INPUT"
    assert record["workflow"]["currentPhase"] == "design-validation"
    assert record["design_validation"]["status"] == "NEEDS_INPUT"
    assert "api.operations-present" in record["error"]
    report = (
        tmp_path / ".easydep" / "implementation-runs" / record["job_id"] / "design-readiness.json"
    )
    assert report.is_file()


def test_initial_job_blocks_when_rendered_openapi_has_no_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    implementation_worker.client.prepare_job = lambda *_args, **_kwargs: pytest.fail(
        "An empty rendered OpenAPI document must not reach the prototype process"
    )
    monkeypatch.setattr(
        "app.implementation.application.jobs.design_readiness_report",
        lambda _design: {"status": "NEEDS_INPUT", "findings": [{"finding": "warning"}]},
    )
    try:
        record = implementation_worker.create_job(
            "app-1",
            {
                "class_diagram_puml": "class Cart",
                "api_spec": {"openapi": "3.1.0", "paths": {}},
                "extracted_bce_classes": {"Classes": [{"className": "Cart"}]},
                "sequence_diagram_model": {"Diagrams": [{"use_case_id": "UC1"}]},
                "api_spec_model": {"Endpoints": [{"path": "/carts"}]},
            },
            "com.example",
            False,
        )
    finally:
        implementation_worker.shutdown()

    assert record["status"] == "NEEDS_INPUT"
    assert record["design_validation"]["status"] == "NEEDS_INPUT"
    assert "api.operations-present" in record["error"]


def test_initial_job_blocks_lossy_erd_bce_identifier_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    implementation_worker.client.prepare_job = lambda *_args, **_kwargs: pytest.fail(
        "A lossy ERD/BCE identifier contract must be resolved before generation"
    )
    monkeypatch.setattr(
        "app.implementation.application.jobs.design_readiness_report",
        lambda _design: {
            "status": "NEEDS_INPUT",
            "findings": [
                {
                    "stage": "erd",
                    "finding": (
                        "Session: surrogate key replaces sessionId [erd.surrogate-key-collides]"
                    ),
                }
            ],
        },
    )
    try:
        record = implementation_worker.create_job(
            "app-1",
            {
                "class_diagram_puml": "class Session",
                "api_spec": {
                    "openapi": "3.1.0",
                    "paths": {"/sessions": {"post": {"operationId": "createSession"}}},
                },
                "extracted_bce_classes": {"Classes": [{"className": "Session"}]},
                "sequence_diagram_model": {"Diagrams": [{"use_case_id": "UC1"}]},
                "api_spec_model": {"Endpoints": [{"path": "/sessions"}]},
            },
            "com.example",
            False,
        )
    finally:
        implementation_worker.shutdown()

    assert record["status"] == "NEEDS_INPUT"
    assert record["workflow"]["currentPhase"] == "design-validation"
    assert "erd.surrogate-key-collides" in record["error"]


def test_planning_keeps_validation_needs_input_outcome_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    run_root = tmp_path / "generated-run"
    reports = run_root / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "status": "NEEDS_INPUT",
                "diagnostics": [
                    {
                        "code": "OPENAPI_NO_OPERATIONS",
                        "severity": "ERROR",
                        "message": "OpenAPI paths must contain at least one HTTP operation.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    job_path = tmp_path / "job" / "job.json"
    job_path.parent.mkdir()
    job_path.write_text("{}", encoding="utf-8")
    record = {
        "job_id": "needs-input-run",
        "app_id": "app-1",
        "status": "QUEUED",
        "base_package": "com.example",
        "job_path": str(job_path),
        "run_root": None,
        "workflow": None,
        "transmission_request": None,
        "error": None,
        "created_at": "now",
        "updated_at": "now",
    }
    implementation_worker._write(record)
    monkeypatch.setattr(implementation_worker.client, "generate", lambda _job: run_root)
    monkeypatch.setattr(
        implementation_worker.client,
        "plan_workflow",
        lambda *_args: pytest.fail("Input validation must not invoke implementation planning"),
    )
    try:
        implementation_worker._plan("needs-input-run")
        result = implementation_worker._read("needs-input-run")
    finally:
        implementation_worker.shutdown()

    assert result["status"] == "NEEDS_INPUT"
    assert result["run_root"] == str(run_root)
    assert result["workflow"]["currentPhase"] == "input-validation"
    assert "at least one HTTP operation" in result["workflow"]["blockingReason"]


def test_prepare_feedback_job_materializes_existing_application(tmp_path: Path) -> None:
    client = PrototypeClient(settings(tmp_path))
    path = client.prepare_feedback_job(
        "job-feedback",
        "12345678-0000-0000-0000-000000000000",
        {
            "class_diagram_puml": "@startuml\nclass Order\n@enduml",
            "api_spec": {"openapi": "3.0.3", "paths": {}},
        },
        {
            "src/main/java/com/example/OrderService.java": "class OrderService {}",
            "src/test/java/com/example/OrderServiceTest.java": "class OrderServiceTest {}",
        },
        "Reject shipped order cancellation.",
        "com.example",
        False,
    )
    job = json.loads(path.read_text(encoding="utf-8"))
    assert job["jobType"] == "FEEDBACK_REVISION"
    assert job["requiredInputs"] == ["baseSnapshot"]
    snapshot = json.loads((tmp_path / job["inputs"]["baseSnapshot"]).read_text(encoding="utf-8"))
    assert "application/src/main/java/com/example/OrderService.java" in snapshot["files"]


def test_feedback_orchestrator_restores_snapshot_without_generation_tools(
    tmp_path: Path,
) -> None:
    client = PrototypeClient(settings(tmp_path))
    path = client.prepare_feedback_job(
        "job-feedback",
        "12345678-0000-0000-0000-000000000000",
        {},
        {
            "src/main/java/com/example/OrderService.java": "class OrderService {}",
            "src/test/java/com/example/OrderServiceTest.java": "class OrderServiceTest {}",
        },
        "Rename the service method.",
        "com.example",
        False,
    )
    output = PrototypeOrchestrator(load_job(path)).run()
    assert (output / "application/src/main/java/com/example/OrderService.java").is_file()
    manifest = json.loads((output / "reports/run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "SUCCEEDED"
    assert (output / "reports/generated-source-contracts.json").is_file()
    assert [task["task_id"] for task in manifest["implementation_tasks"]] == [
        "apply-source-feedback"
    ]


def test_prepare_job_rejects_work_root_outside_repository(tmp_path: Path) -> None:
    configured = settings(tmp_path / "agent")
    configured = ImplementationSettings(
        **{**configured.__dict__, "work_root": tmp_path / "outside"}
    )
    with pytest.raises(PrototypeExecutionError, match="inside the EasyDep repository"):
        PrototypeClient(configured).prepare_job("job", "app", {}, "com.example", False)


def test_public_job_record_hides_host_source_paths() -> None:
    record = {
        "job_path": "C:/secret/job.json",
        "run_root": "C:/secret/run",
        "status": "AWAITING_APPROVAL",
        "workflow": {
            "tasks": [{"task_id": "control", "status": "RUNNING"}],
        },
        "transmission_request": {
            "requestId": "a" * 64,
            "tasks": [
                {
                    "taskId": "control",
                    "sourceArtifacts": {"class": "C:/secret/class.puml"},
                    "sourceArtifactHashes": {"class": "hash"},
                }
            ],
        },
    }
    public = ImplementationWorker.public_record(record)
    assert "job_path" not in public and "run_root" not in public
    assert public["workflow"]["tasks"] == [
        {"taskId": "control", "status": "RUNNING"}
    ]
    assert public["transmission_request"]["tasks"][0]["sourceArtifacts"] == ["class"]


def test_implementation_api_downloads_all_file_artifacts_as_zip(monkeypatch) -> None:
    snapshots = {
        "SOURCE_CODE": {
            "artifact_type": "SOURCE_CODE",
            "version_no": 2,
            "files": {"src/main/App.java": {"content": "class App {}", "sha256": "a"}},
        },
        "TEST_CODE": {
            "artifact_type": "TEST_CODE",
            "version_no": 1,
            "files": {"src/test/AppTest.java": {"content": "class AppTest {}", "sha256": "b"}},
        },
    }
    monkeypatch.setattr(
        "app.implementation.interfaces.http.artifact_repository.load_file_snapshot",
        lambda _app_id, artifact_type: snapshots.get(artifact_type),
    )
    application = FastAPI()
    application.include_router(router)

    response = TestClient(application).get("/api/implementation/apps/app-1/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("SOURCE_CODE/src/main/App.java") == b"class App {}"
        assert archive.read("TEST_CODE/src/test/AppTest.java") == b"class AppTest {}"
        manifest = json.loads(archive.read("manifest.json"))
    assert {item["artifact_type"] for item in manifest["artifacts"]} == {
        "SOURCE_CODE",
        "TEST_CODE",
    }


def test_live_source_api_reads_only_safe_files_for_the_matching_job(
    tmp_path: Path, monkeypatch
) -> None:
    implementation_worker = ImplementationWorker(settings(tmp_path))
    job_id = "a" * 32
    job_root = implementation_worker.settings.work_root / job_id
    run_root = job_root / "generated" / "runs" / "run_live"
    application_root = run_root / "application"
    source = application_root / "src/main/java/com/example/App.java"
    frontend = application_root / "frontend/src/App.svelte"
    source.parent.mkdir(parents=True)
    frontend.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    frontend.write_text("<main>Hello</main>", encoding="utf-8")
    (application_root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    build_file = application_root / "build/generated.txt"
    build_file.parent.mkdir(parents=True)
    build_file.write_text("generated", encoding="utf-8")
    reports = run_root / "reports"
    reports.mkdir()
    (reports / "workflow-state.json").write_text(
        json.dumps({"tasks": [{"task_id": "control", "status": "RUNNING"}]}),
        encoding="utf-8",
    )
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "control",
                        "allowed_write_paths": [
                            "application/src/main/java/com/example/App.java",
                            "application/src/test/java/com/example/AppTest.java",
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    implementation_worker._write(
        {
            "job_id": job_id,
            "app_id": "app-1",
            "status": "RUNNING",
            "run_root": str(run_root),
        }
    )
    monkeypatch.setattr("app.implementation.interfaces.http.worker", implementation_worker)
    application = FastAPI()
    application.include_router(router)

    try:
        client = TestClient(application)
        listing = client.get(f"/api/implementation/apps/app-1/jobs/{job_id}/live")
        content = client.get(
            f"/api/implementation/apps/app-1/jobs/{job_id}/live/files/"
            "src/main/java/com/example/App.java"
        )
        wrong_app = client.get(f"/api/implementation/apps/app-2/jobs/{job_id}/live")
        secret = client.get(f"/api/implementation/apps/app-1/jobs/{job_id}/live/files/.env")
        with pytest.raises(FileNotFoundError):
            implementation_worker.live_source_file(job_id, "app-1", "../job.json")
    finally:
        implementation_worker.shutdown()

    assert listing.status_code == 200
    files = {item["path"]: item for item in listing.json()["files"]}
    assert set(files) == {
        "frontend/src/App.svelte",
        "src/main/java/com/example/App.java",
        "src/test/java/com/example/AppTest.java",
    }
    assert files["src/main/java/com/example/App.java"]["status"] == "writing"
    assert files["src/test/java/com/example/AppTest.java"]["exists"] is False
    assert content.json()["content"] == "class App {}"
    assert wrong_app.status_code == 404
    assert secret.status_code == 404
