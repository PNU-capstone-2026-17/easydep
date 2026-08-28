"""공개 HTTP 경로와 MySQL 저장소를 함께 사용하는 제품 시나리오 통합 테스트다.

이 테스트는 실제 데이터베이스에 행을 추가하므로 평소 ``pytest`` 실행에서는 건너뛴다.
``EASYDEP_RUN_MYSQL_SYSTEM_TESTS=1``을 명시하고, DB 이름에 ``test``가 들어간 전용
데이터베이스를 설정했을 때만 실행된다. 테스트가 만든 앱은 다른 실행과 ID가 겹치지 않으며,
공유 환경에서 뜻밖의 데이터를 지우지 않도록 종료 시 행을 삭제하지 않는다.

Workspace 명령과 이벤트, 산출물 버전, 구현 파일 snapshot, TestingInput의 고정 및 임시
복원은 실제 코드를 사용한다. 외부 LLM과 구현 실행 환경만 빠른 가짜 구현으로 바꾸므로,
평가 실행기가 제품과 다른 내부 서비스 경로를 사용하는 회귀를 찾을 수 있다.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import artifacts_api
from app.config import settings
from app.db import session as db_session
from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_SOURCE_CODE
from app.implementation.application.jobs import worker as implementation_worker
from app.implementation.interfaces import http as implementation_http
from app.repositories import artifact_repository
from app.requirements.schemas import AnalyzeResponse
from app.testing import api as testing_api
from app.workspace import api as workspace_api
from app.workspace import service as workspace_service_module
from evaluation.easydep.product_scenario import ProductScenarioRunner

RUN_MYSQL_SYSTEM_TESTS = os.getenv("EASYDEP_RUN_MYSQL_SYSTEM_TESTS") == "1"


def _test_database_name() -> str:
    """현재 process가 실제로 사용할 데이터베이스 이름을 반환한다."""
    return str(settings.db_name).strip()


def _has_safe_test_database_name() -> bool:
    """운영·개발 DB를 실수로 선택하지 않도록 이름에 test가 있는지 확인한다."""
    name = _test_database_name().casefold()
    return bool(name) and "test" in name and name != "easydep"


pytestmark = pytest.mark.skipif(
    not RUN_MYSQL_SYSTEM_TESTS or not _has_safe_test_database_name(),
    reason=(
        "전용 MySQL에서만 실행한다: EASYDEP_RUN_MYSQL_SYSTEM_TESTS=1과 "
        "이름에 test가 들어간 DB_NAME을 설정하세요."
    ),
)


class _TestClientTransport:
    """ProductScenarioRunner 요청을 FastAPI TestClient로 보내는 transport다."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def _json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """HTTP 오류가 있으면 응답 본문을 포함해 바로 테스트를 실패시킨다."""
        response = self.client.request(method, path, json=dict(body) if body else None)
        assert response.status_code < 300, response.text
        payload = response.json()
        assert isinstance(payload, dict), payload
        return payload

    def create_app(self, message: str) -> Mapping[str, Any]:
        return self._json("POST", "/api/workspace/apps", body={"message": message})

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        return self._json("GET", f"/api/workspace/apps/{app_id}")

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._json(
            "POST", f"/api/workspace/apps/{app_id}/commands", body=payload
        )

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]:
        """끝나지 않는 SSE 대신 같은 공개 snapshot의 event 목록을 cursor로 읽는다."""
        assert timeout_seconds >= 0
        snapshot = self.get_workspace(app_id)
        events = snapshot.get("events") or []
        return [
            item
            for item in events
            if isinstance(item, dict) and int(item.get("event_id") or 0) > after
        ]

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]:
        return self._json("GET", f"/api/apps/{app_id}")

    def get_stage_versions(
        self, app_id: str, stage: str
    ) -> Sequence[Mapping[str, Any]]:
        payload = self._json(
            "GET", f"/api/apps/{app_id}/stages/{stage}/versions"
        )
        versions = payload.get("versions") or []
        return [item for item in versions if isinstance(item, dict)]

    def get_stage_version(
        self, app_id: str, stage: str, version_no: int
    ) -> Mapping[str, Any]:
        return self._json(
            "GET", f"/api/apps/{app_id}/stages/{stage}/versions/{version_no}"
        )

    def get_file_artifact(
        self, app_id: str, artifact_type: str
    ) -> Mapping[str, Any] | None:
        response = self.client.get(
            f"/api/implementation/apps/{app_id}/artifacts/{artifact_type}"
        )
        if response.status_code == 404:
            return None
        assert response.status_code < 300, response.text
        payload = response.json()
        assert isinstance(payload, dict), payload
        return payload

    def get_artifact_file(
        self, app_id: str, artifact_type: str, path: str
    ) -> Mapping[str, Any]:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        return self._json(
            "GET",
            f"/api/implementation/apps/{app_id}/artifacts/"
            f"{artifact_type}/files/{encoded_path}",
        )


def _install_requirement_and_design_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """외부 LLM만 생략하고 단계 완료 결과는 실제 산출물 저장소에 기록한다."""

    def analyze(request: Any) -> AnalyzeResponse:
        app_id = str(request.app_id)
        requirement = {
            "id": "FR-SYSTEM-1",
            "text": "사용자는 서비스 상태를 확인할 수 있다.",
            "type": "FR",
        }
        artifact_repository.save_stage(
            app_id,
            "refined_requirements",
            {"refined_requirements": [requirement]},
        )
        return AnalyzeResponse(
            thread_id=str(request.thread_id or app_id),
            phase="requirements_handoff",
            status="completed",
            requirements=[requirement],
            saved_stages=["refined_requirements"],
        )

    def start_design(app_id: str, _request: Any) -> JSONResponse:
        # 설계 provider가 완료됐다는 공개 응답 모양만 재현한다. 이 테스트의 목적은 설계
        # 품질 평가가 아니라 Workspace의 다음 단계 전환과 구현 파일 출처 확인이다.
        return JSONResponse(
            content={
                "app_id": app_id,
                "status": "completed",
                "session": {"finished": True, "current_stage": "design_complete"},
            }
        )

    monkeypatch.setattr(workspace_service_module, "analyze_endpoint", analyze)
    monkeypatch.setattr(
        workspace_service_module,
        "session_status",
        lambda _app_id: {"active": False, "retryable": False, "finished": False},
    )
    monkeypatch.setattr(workspace_service_module, "start_design_session", start_design)


def _install_implementation_worker_fake(
    monkeypatch: pytest.MonkeyPatch,
    run_root: Path,
) -> dict[str, dict[str, Any]]:
    """승인 경계는 유지하면서 외부 구현 실행만 메모리 worker로 대신한다."""
    jobs: dict[str, dict[str, Any]] = {}

    def create_job(
        app_id: str,
        _design: dict[str, Any],
        base_package: str,
        _allow_assumptions: bool,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        record = {
            "job_id": job_id,
            "app_id": app_id,
            "status": "AWAITING_APPROVAL",
            "base_package": base_package,
            "run_root": str(run_root / job_id),
            "transmission_request": {
                # 실제 ApprovalRequest와 같은 최소 64자 계약을 사용한다.
                "requestId": job_id * 2,
                "tasks": [{"id": "generate", "label": "테스트용 구현 생성"}],
            },
            "artifact_version_ids": None,
            "completed_at": None,
            "error": None,
        }
        jobs[job_id] = record
        return dict(record)

    def get(job_id: str) -> dict[str, Any]:
        return dict(jobs[job_id])

    def approve(
        job_id: str,
        request_id: str,
        approved: bool,
        _approved_by: str,
        _retry_failed: bool,
        _delegate_repair_approvals: bool,
    ) -> dict[str, Any]:
        record = jobs[job_id]
        assert request_id == record["transmission_request"]["requestId"]
        assert approved is True
        metadata = {"implementation_job_id": job_id}
        source_version_id = artifact_repository.save_file_snapshot(
            str(record["app_id"]),
            TYPE_SOURCE_CODE,
            {
                "src/main/java/com/easydep/app/StatusApplication.java": (
                    "package com.easydep.app;\n"
                    "public class StatusApplication { }\n"
                )
            },
            metadata=metadata,
        )
        deployment_version_id = artifact_repository.save_file_snapshot(
            str(record["app_id"]),
            TYPE_DEPLOYMENT_FILE,
            {"Dockerfile": "FROM eclipse-temurin:21-jre-alpine\n"},
            metadata=metadata,
        )
        record.update(
            {
                "status": "COMPLETED",
                "artifact_version_ids": {
                    TYPE_SOURCE_CODE: source_version_id,
                    TYPE_DEPLOYMENT_FILE: deployment_version_id,
                },
                "completed_at": datetime.now(UTC).isoformat(),
                "transmission_request": None,
            }
        )
        return dict(record)

    def get_testing_input(job_id: str) -> dict[str, Any]:
        record = jobs[job_id]
        return {
            "job_id": job_id,
            "app_id": record["app_id"],
            "status": record["status"],
            "run_root": record["run_root"],
            "artifact_version_ids": record["artifact_version_ids"],
            "completed_at": record["completed_at"],
        }

    monkeypatch.setattr(implementation_worker, "create_job", create_job)
    monkeypatch.setattr(implementation_worker, "get", get)
    monkeypatch.setattr(implementation_worker, "approve", approve)
    monkeypatch.setattr(
        implementation_worker, "get_testing_input", get_testing_input
    )
    return jobs


def _install_testing_runtime_fakes(
    monkeypatch: pytest.MonkeyPatch,
    observed_files: dict[str, str],
) -> None:
    """DB 복원 뒤의 언어별 테스트와 컨테이너 실행만 빠른 통과 결과로 바꾼다."""

    class PassingTestingAdapter:
        def run(self, implementation_result: Mapping[str, Any]) -> dict[str, Any]:
            application = Path(str(implementation_result["run_root"])) / "application"
            source = application / "src/main/java/com/easydep/app/StatusApplication.java"
            dockerfile = application / "Dockerfile"
            # 이 파일들은 원래 구현 폴더가 아니라 DB의 고정 snapshot에서 복원되어야 한다.
            observed_files["source"] = source.read_text(encoding="utf-8")
            observed_files["deployment"] = dockerfile.read_text(encoding="utf-8")
            return {"passed": True, "diagnostics": []}

    def passing_verification(**kwargs: Any) -> dict[str, Any]:
        testing_input = kwargs["testing_input"]
        observed_files["implementation_job_id"] = (
            testing_input.implementation_job_id
        )
        observed_files["source_version"] = str(
            testing_input.snapshot_for(TYPE_SOURCE_CODE).version_no
        )
        return {
            "passed": True,
            "blockingReason": None,
            "diagnostics": [],
            "reports": {"dynamicFunctional": {"status": "PASSED"}},
        }

    monkeypatch.setattr(testing_api, "TestingAdapter", PassingTestingAdapter)
    monkeypatch.setattr(testing_api, "run_verification_graph", passing_verification)


def _application() -> FastAPI:
    """제품 시나리오가 사용하는 실제 router만 조립한 테스트 애플리케이션이다."""
    application = FastAPI()
    application.include_router(artifacts_api.router)
    application.include_router(implementation_http.router)
    application.include_router(testing_api.router)
    application.include_router(workspace_api.router)
    return application


def test_product_scenario_uses_actual_routers_and_fixed_mysql_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """앱 생성부터 Testing 완료와 파일 검증까지 공개 API로 한 번 완주한다."""
    # skip 조건을 우회하는 실행 설정이 생겨도 개발 DB에는 쓰지 않도록 본문에서 한 번 더
    # 검사한다. 이 테스트는 행 삭제로 되돌리지 않으므로 이 확인이 특히 중요하다.
    assert RUN_MYSQL_SYSTEM_TESTS
    assert _has_safe_test_database_name(), _test_database_name()
    db_session.init_db()

    fresh_workspace_service = workspace_service_module.WorkspaceService()
    monkeypatch.setattr(workspace_api, "workspace_service", fresh_workspace_service)
    _install_requirement_and_design_fakes(monkeypatch)
    jobs = _install_implementation_worker_fake(monkeypatch, tmp_path)
    observed_files: dict[str, str] = {}
    _install_testing_runtime_fakes(monkeypatch, observed_files)

    try:
        with TestClient(_application()) as client:
            runner = ProductScenarioRunner(
                _TestClientTransport(client),
                timeout_seconds=30,
                poll_interval_seconds=0.02,
                event_wait_seconds=0,
            )
            result = runner.run("상태 확인 API가 있는 간단한 서비스를 만들어 주세요.")

            # 구현과 Testing 작업 조회도 실제 HTTP router를 통과시켜 응답 모양을 확인한다.
            implementation_response = client.get(
                f"/api/implementation/jobs/{result.implementation_job_id}"
            )
            testing_response = client.get(f"/api/testing/jobs/{result.testing_job_id}")
            assert implementation_response.status_code == 200
            assert testing_response.status_code == 200
            assert testing_response.json()["result"]["passed"] is True
    finally:
        fresh_workspace_service.shutdown()

    implementation_job = jobs[result.implementation_job_id]
    assert implementation_job["status"] == "COMPLETED"
    assert set(result.artifact_references) == {
        "refined_requirements",
        TYPE_SOURCE_CODE,
        TYPE_DEPLOYMENT_FILE,
    }
    assert result.artifact_references[TYPE_SOURCE_CODE].verified_file_count == 1
    assert result.artifact_references[TYPE_DEPLOYMENT_FILE].verified_file_count == 1
    assert observed_files["implementation_job_id"] == result.implementation_job_id
    assert "StatusApplication" in observed_files["source"]
    assert "eclipse-temurin:21-jre-alpine" in observed_files["deployment"]
    assert observed_files["source_version"] == str(
        result.artifact_references[TYPE_SOURCE_CODE].version_no
    )
