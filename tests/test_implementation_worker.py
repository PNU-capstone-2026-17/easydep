from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.implementation.interfaces.http import router
from app.implementation.config import ImplementationSettings
from app.implementation.agents.verification.build import gradle_command
from app.implementation.generation.orchestrator import PrototypeOrchestrator, load_job
from app.implementation.application.feedback import assess_feedback_eligibility
from app.implementation.application.prototype import PrototypeClient, PrototypeExecutionError
from app.implementation.interfaces.schemas import (
    CreateImplementationFeedbackJobRequest,
    CreateImplementationJobRequest,
)
from app.implementation.application.jobs import ImplementationWorker, InvalidJobState


def test_job_contract_preserves_automated_placeholder_policy() -> None:
    assert CreateImplementationJobRequest().allow_assumptions is True


def test_feedback_request_trims_feedback() -> None:
    request = CreateImplementationFeedbackJobRequest(feedback="  rename the service  ")
    assert request.feedback == "rename the service"


def test_feedback_eligibility_rejects_design_contract_changes() -> None:
    result = assess_feedback_eligibility("OpenAPI 엔드포인트와 응답 스키마를 변경해줘")

    assert result["status"] == "UNSUITABLE"
    assert result["matches"][0]["code"] == "OPENAPI_CONTRACT_CHANGE"


def test_feedback_eligibility_accepts_existing_contract_behavior_change() -> None:
    result = assess_feedback_eligibility("배송이 시작된 주문은 취소 요청을 거절하고 테스트를 보강해줘")

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
    assert (tmp_path / ".easydep/implementation-runs" / record["job_id"] / "feedback-eligibility.json").is_file()


def test_requirement_feedback_asks_before_returning_to_requirements() -> None:
    result = assess_feedback_eligibility("요구사항에 대기자 우선순위 업무 규칙을 추가해줘")

    assert result["status"] == "UNSUITABLE"
    assert result["requiredStage"] == "requirements"
    assert "requirements 단계" in result["confirmationQuestion"]


def test_delegated_approval_covers_initial_and_cross_phase_repair(tmp_path: Path) -> None:
    run = tmp_path / "run_repair"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(json.dumps({"input_hash": "input-hash"}), encoding="utf-8")
    (reports / "repair-plan.json").write_text(json.dumps({
        "entries": [{"revision": 1, "ownerTaskIds": ["repair-api"], "revalidationTaskIds": ["repair-e2e"]}]
    }), encoding="utf-8")
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({
        "delegatedRepairApprovals": True,
        "delegationScope": {"runId": run.name, "inputHash": "input-hash", "initialTaskIds": ["initial-wiring"], "maxRepairRounds": 3, "maxTaskAttempts": 50},
    }), encoding="utf-8")
    record = {
        "run_root": str(run),
        "transmission_request": {"tasks": [{"taskId": "repair-api"}, {"taskId": "repair-e2e"}]},
        "workflow": {"tasks": [{"attempts": 2}]},
    }

    assert ImplementationWorker._delegated_execution_is_active(record, str(approval))
    record["transmission_request"] = {"tasks": [{"taskId": "initial-wiring"}]}
    assert ImplementationWorker._delegated_execution_is_active(record, str(approval))


def test_settings_ignore_legacy_external_project_paths(monkeypatch) -> None:
    monkeypatch.setenv("IMPLEMENTATION_AGENT_ROOT", "C:/old/prototype")
    monkeypatch.setenv("IMPLEMENTATION_AGENT_PYTHON", "C:/old/python.exe")
    monkeypatch.setenv("IMPLEMENTATION_WORK_ROOT", "C:/old/work")
    configured = ImplementationSettings.from_env()
    expected_root = Path(__file__).resolve().parents[1]
    assert configured.repository_root == expected_root
    assert configured.python_executable == Path(sys.executable).resolve()
    assert configured.work_root == expected_root / ".easydep" / "implementation-runs"


def test_engine_uses_repository_gradle_wrapper() -> None:
    command = " ".join(gradle_command()).replace("\\", "/")
    assert "app/implementation/tools/gradle/gradlew" in command


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
            "erd_puml": "@startuml\nentity orders\n@enduml",
            "deployment_diagram_puml": "@startuml\nnode app\n@enduml",
            "resource_spec": {"cloud": "azure"},
        },
        "com.example.orders",
        False,
    )
    job = json.loads(path.read_text(encoding="utf-8"))
    assert set(job["inputs"]) == {
        "bceClass", "sequence", "openapi", "erd", "deployment", "cloud",
    }
    assert job["generation"]["basePackage"] == "com.example.orders"
    assert (tmp_path / job["inputs"]["openapi"]).is_file()
    sequence_path = tmp_path / job["inputs"]["sequence"]
    assert sequence_path.name == "sequence-diagrams.puml"
    assert sequence_path.read_text(encoding="utf-8").count("@startuml") == 2
    assert job["tools"]["puml2codeRoot"].startswith("app/implementation/tools/")
    assert "openapiGeneratorJar" not in job["tools"]


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
    snapshot = json.loads(
        (tmp_path / job["inputs"]["baseSnapshot"]).read_text(encoding="utf-8")
    )
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
    assert (
        output / "application/src/main/java/com/example/OrderService.java"
    ).is_file()
    manifest = json.loads(
        (output / "reports/run-manifest.json").read_text(encoding="utf-8")
    )
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


def test_cli_parser_uses_last_json_line(monkeypatch, tmp_path: Path) -> None:
    client = PrototypeClient(settings(tmp_path))
    captured = {}

    def completed(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="OpenHands banner\n{\"status\": \"READY\"}\n", stderr=""
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        completed,
    )
    assert client._call(["workflow-status", "run"])["status"] == "READY"
    assert "app.implementation.interfaces.cli" in captured["command"]
    assert captured["cwd"] == tmp_path


def test_public_job_record_hides_host_source_paths() -> None:
    record = {
        "job_path": "C:/secret/job.json",
        "run_root": "C:/secret/run",
        "status": "AWAITING_APPROVAL",
        "transmission_request": {
            "requestId": "a" * 64,
            "tasks": [{
                "taskId": "control",
                "sourceArtifacts": {"class": "C:/secret/class.puml"},
                "sourceArtifactHashes": {"class": "hash"},
            }],
        },
    }
    public = ImplementationWorker.public_record(record)
    assert "job_path" not in public and "run_root" not in public
    assert public["transmission_request"]["tasks"][0]["sourceArtifacts"] == ["class"]


def test_implementation_api_enqueues_job(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.implementation.interfaces.http.artifact_repository.load_state",
        lambda app_id: {"class_diagram_puml": "class X", "api_spec": {"paths": {}}},
    )
    monkeypatch.setattr(
        "app.implementation.interfaces.http.worker.create_job",
        lambda app_id, design, base_package, allow_assumptions: {
            "job_id": "job-1",
            "app_id": app_id,
            "status": "QUEUED",
        },
    )
    application = FastAPI()
    application.include_router(router)
    response = TestClient(application).post(
        "/api/implementation/apps/app-1/jobs",
        json={"base_package": "com.example.orders"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"


def test_implementation_feedback_api_enqueues_revision_job(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.implementation.interfaces.http.artifact_repository.load_state",
        lambda app_id: {"class_diagram_puml": "class X", "api_spec": {"paths": {}}},
    )
    monkeypatch.setattr(
        "app.implementation.interfaces.http.worker.create_feedback_job",
        lambda app_id, design, feedback, base_package, allow_assumptions: {
            "job_id": "feedback-1",
            "job_type": "FEEDBACK_REVISION",
            "app_id": app_id,
            "status": "QUEUED",
            "feedback": feedback,
        },
    )
    application = FastAPI()
    application.include_router(router)
    response = TestClient(application).post(
        "/api/implementation/apps/app-1/feedback-jobs",
        json={
            "feedback": "Reject shipped order cancellation.",
            "base_package": "com.example.orders",
        },
    )
    assert response.status_code == 202
    assert response.json()["job_type"] == "FEEDBACK_REVISION"


def test_implementation_api_returns_conflict_for_stale_approval(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.implementation.interfaces.http.worker.approve",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            InvalidJobState("Approval does not match")
        ),
    )
    application = FastAPI()
    application.include_router(router)
    response = TestClient(application).post(
        "/api/implementation/jobs/job-1/approval",
        json={"request_id": "a" * 64, "approved": True},
    )
    assert response.status_code == 409
