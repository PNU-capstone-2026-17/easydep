"""구현 작업의 생성, 승인, 실행, 결과 저장을 관리한다.

설계 산출물이 구현에 필요한 수준인지 먼저 확인한 뒤 별도 프로세스에서 코드 생성과
검증을 실행한다. 각 작업의 상태는 ``easydep-job-state.json``에 저장하므로 서버가
재시작되어도 승인 정보가 남아 있는 작업은 이어서 실행할 수 있다. 여러 요청이 동시에
들어올 수 있어 실제 실행은 크기가 제한된 thread pool에 맡긴다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_IAC_CODE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
)
from app.design.validation import design_readiness_report
from app.repositories import artifact_repository

from ..config import ImplementationSettings
from .feedback import assess_feedback_eligibility
from .prototype import PrototypeClient
from .source_files import (
    classify_source_path,
    is_visible_source_path,
    iter_application_sources,
    read_application_source,
)

# 일부 설계 finding은 구현을 진행하면서 구체적인 코드와 함께 확인할 수 있다. 그러나
# 아래 규칙의 오류는 입력 계약 자체를 만들 수 없게 하거나 BCE 필드와 JPA 필드의 대응을
# 잃게 만든다. 구현기가 임의로 데이터 저장 방식을 정하지 않도록 이 경우만 시작 전에 막는다.
_IMPLEMENTATION_BLOCKING_DESIGN_RULES = frozenset({
    "api.operations-present",
    # API 추적, 타입, 응답 finding은 보고서에 남긴 채 구현을 진행할 수 있다. 다만 HTTP
    # operation이 하나도 없으면 구현할 endpoint가 없으므로 작업 자체를 시작할 수 없다.
    "erd.surrogate-key-collides",
})
_OPENAPI_HTTP_METHODS = frozenset({
    "delete", "get", "head", "options", "patch", "post", "put", "trace",
})
def _now() -> str:
    """작업 기록에 사용할 현재 UTC 시각을 ISO 8601 문자열로 반환한다."""
    return datetime.now(UTC).isoformat()


def _has_implementation_blocking_design_finding(readiness: dict[str, Any]) -> bool:
    """구현 계약을 안전하게 만들 수 없는 설계 finding이 있는지 확인한다.

    필드 이름을 추측하지 않고 검사기가 finding에 넣은 rule ID를 확인한다. 예를 들어 ERD
    검사기가 자동 생성 surrogate key와 기존 필드의 충돌을 보고했다면, 그대로 진행할 경우
    BCE 객체를 JPA Entity로 옮기는 mapper가 어느 값을 보존해야 할지 결정할 수 없다.
    """
    return any(
        rule_id in str(finding.get("finding") or "")
        for finding in readiness.get("findings") or []
        if isinstance(finding, dict)
        for rule_id in _IMPLEMENTATION_BLOCKING_DESIGN_RULES
    )


def _has_rendered_openapi_operation(api_spec: object) -> bool:
    """중간 endpoint 모델이 아니라 최종 OpenAPI에 HTTP operation이 있는지 확인한다."""
    if not isinstance(api_spec, dict):
        return False
    paths = api_spec.get("paths")
    return isinstance(paths, dict) and any(
        isinstance(path_item, dict)
        and any(
            str(method).lower() in _OPENAPI_HTTP_METHODS
            and isinstance(operation, dict)
            for method, operation in path_item.items()
        )
        for path_item in paths.values()
    )


def _missing_openapi_operation_report(readiness: dict[str, Any]) -> dict[str, Any]:
    """OpenAPI에 operation이 없다는 finding을 구현 준비도 보고서에 추가한다."""
    finding = (
        "OpenAPI paths에 구현 가능한 HTTP operation이 없음 — 유스케이스·BCE Control·"
        "시퀀스 호출에 근거한 endpoint를 생성해야 함 [api.operations-present]"
    )
    if any(
        "api.operations-present" in str(item.get("finding") or "")
        for item in readiness.get("findings") or []
        if isinstance(item, dict)
    ):
        return readiness

    result = {**readiness}
    findings = list(readiness.get("findings") or [])
    findings.append({"stage": "api_spec", "finding": finding})
    result["findings"] = findings
    stages = [dict(item) for item in readiness.get("stages") or [] if isinstance(item, dict)]
    api_stage = next((item for item in stages if item.get("stage") == "api_spec"), None)
    if api_stage is None:
        stages.append({"stage": "api_spec", "status": "NEEDS_INPUT", "findings": [finding]})
    else:
        api_stage["status"] = "NEEDS_INPUT"
        api_stage["findings"] = [*list(api_stage.get("findings") or []), finding]
    result["stages"] = stages
    result["status"] = "NEEDS_INPUT"
    return result


class JobNotFound(KeyError):
    """요청한 구현 작업 ID의 상태 파일이 없을 때 발생한다."""


class InvalidJobState(RuntimeError):
    """현재 작업 상태에서는 요청한 동작을 수행할 수 없을 때 발생한다."""


class ImplementationWorker:
    """구현 작업 상태 파일과 크기가 제한된 로컬 실행 queue를 관리한다."""

    def __init__(self, settings: ImplementationSettings | None = None):
        """실행 경로와 worker pool을 준비하고 중단된 작업을 복구한다."""
        self.settings = settings or ImplementationSettings.from_env()
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        self.client = PrototypeClient(self.settings)
        self.executor = ThreadPoolExecutor(max_workers=self.settings.max_workers, thread_name_prefix="easydep-implementation")
        self.warmup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="easydep-warmup")
        self.lock = threading.RLock()
        self._warmup_lock = threading.Lock()
        self._warmup_started = False
        self._recover_pending_jobs()

    def create_job(self, app_id: str, design: dict[str, Any], base_package: str, allow_assumptions: bool) -> dict[str, Any]:
        """설계를 검사한 뒤 새 구현 작업을 등록하고 비동기 planning을 시작한다."""
        missing = [key for key in ("class_diagram_puml", "api_spec") if design.get(key) in (None, "", {})]
        if missing:
            raise InvalidJobState("Missing required design artifacts: " + ", ".join(missing))
        missing_models = [
            key for key in (
                "extracted_bce_classes", "sequence_diagram_model", "api_spec_model",
            )
            if not isinstance(design.get(key), dict) or not design[key]
        ]
        class_model = design.get("extracted_bce_classes")
        has_entity = bool(
            isinstance(class_model, dict)
            and any(
                isinstance(item, dict) and item.get("stereotype") == "Entity"
                for item in class_model.get("Classes", [])
            )
        )
        if has_entity and (
            not isinstance(design.get("erd_bce_classes"), dict)
            or not design["erd_bce_classes"]
        ):
            missing_models.append("erd_bce_classes")
        readiness = design_readiness_report(design)
        # 중간 모델 누락보다 최종 OpenAPI의 operation 누락을 먼저 알린다. 사용자가 실제
        # 산출물에서 확인할 수 있고, 두 OpenAPI 생성 경로가 모두 거부하는 직접적인 이유다.
        if not _has_rendered_openapi_operation(design.get("api_spec")):
            return self._create_design_blocked_job(
                app_id,
                base_package,
                _missing_openapi_operation_report(readiness),
            )
        if missing_models:
            return self._create_design_blocked_job(
                app_id, base_package, self._missing_design_model_report(missing_models)
            )
        if _has_implementation_blocking_design_finding(readiness):
            return self._create_design_blocked_job(app_id, base_package, readiness)
        job_id = uuid.uuid4().hex
        job_path = self.client.prepare_job(job_id, app_id, design, base_package, allow_assumptions)
        record = {
            "job_id": job_id, "app_id": app_id, "status": "QUEUED", "base_package": base_package,
            "job_path": str(job_path), "run_root": None, "workflow": None,
            # 시작을 막지 않는 설계 finding도 구현 보고서에서 확인할 수 있도록 함께 넘긴다.
            "design_validation": readiness,
            "transmission_request": None, "error": None, "created_at": _now(), "updated_at": _now(),
        }
        self._write(record)
        self.executor.submit(self._plan, job_id)
        return self.public_record(record)

    def _create_design_blocked_job(
        self, app_id: str, base_package: str, readiness: dict[str, Any]
    ) -> dict[str, Any]:
        """코드 생성기를 실행하지 않고, 해결할 설계 문제를 작업 기록으로 남긴다."""
        job_id = uuid.uuid4().hex
        report_path = self.settings.work_root / job_id / "design-readiness.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(readiness, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        findings = readiness.get("findings", [])
        summary = "; ".join(
            str(item.get("finding", "")) for item in findings[:3]
            if isinstance(item, dict)
        )
        record = {
            "job_id": job_id,
            "app_id": app_id,
            "status": "NEEDS_INPUT",
            "base_package": base_package,
            "run_root": None,
            "workflow": {
                "schemaVersion": "implementation-workflow/v1alpha1",
                "status": "NEEDS_INPUT",
                "currentPhase": "design-validation",
                "updatedAt": _now(),
                "phases": [],
                "tasks": [],
                "nextRunnableTasks": [],
                "blockingReason": "Resolve the design mismatches before implementation can start.",
            },
            "design_validation": readiness,
            "transmission_request": None,
            "error": "설계 불일치가 남아 구현 작업을 시작하지 않았습니다. " + summary,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._write(record)
        return self.public_record(record)

    @staticmethod
    def _missing_design_model_report(missing_models: list[str]) -> dict[str, Any]:
        """구조화 모델이 없어 API와 Control의 연결을 검사할 수 없다는 보고서를 만든다."""
        findings = [
            {
                "stage": "api_spec",
                "finding": (
                    f"검증 가능한 설계 모델 '{name}'이 없어 API·Control·시퀀스 "
                    "정합성을 증명할 수 없음 — 설계 단계를 다시 생성하거나 수정하세요."
                ),
            }
            for name in missing_models
        ]
        return {
            "schemaVersion": "easydep-design-readiness/v1alpha1",
            "status": "NEEDS_INPUT",
            "stages": [{"stage": "api_spec", "status": "NEEDS_INPUT", "findings": [
                item["finding"] for item in findings
            ]}],
            "findings": findings,
        }

    def create_feedback_job(
        self,
        app_id: str,
        design: dict[str, Any],
        feedback: str,
        base_package: str,
        allow_assumptions: bool,
    ) -> dict[str, Any]:
        """저장된 구현 파일에 사용자 피드백을 적용하는 새 작업을 만든다.

        먼저 피드백이 구현 코드만 고쳐서 해결할 수 있는지 확인한다. 설계 변경이 필요하면
        코드를 생성하지 않고 어느 설계 단계로 돌아가야 하는지 사용자에게 알려 준다.
        """
        source_snapshot = artifact_repository.load_file_snapshot(
            app_id, TYPE_SOURCE_CODE
        )
        if not source_snapshot:
            raise InvalidJobState(
                "No generated source snapshot is available for feedback"
            )

        eligibility = assess_feedback_eligibility(feedback, design)
        job_id = uuid.uuid4().hex
        if eligibility["status"] == "UNSUITABLE":
            report_path = self.settings.work_root / job_id / "feedback-eligibility.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(eligibility, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            record = {
                "job_id": job_id,
                "job_type": "FEEDBACK_REVISION",
                "app_id": app_id,
                "status": "NEEDS_INPUT",
                "base_package": base_package,
                "feedback": feedback,
                "workflow": None,
                "transmission_request": None,
                "feedback_eligibility": eligibility,
                "prompt": {
                    "kind": "upstream_revision_confirmation",
                    "requiredStage": eligibility.get("requiredStage"),
                    "question": eligibility.get("confirmationQuestion"),
                },
                "eligibility_report": str(report_path.relative_to(self.settings.work_root)),
                "error": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._write(record)
            return self.public_record(record)

        snapshots = {
            path: item["content"]
            for path, item in source_snapshot.get("files", {}).items()
        }
        base_versions = {
            TYPE_SOURCE_CODE: source_snapshot["version_no"],
        }
        for artifact_type in (
            TYPE_FRONTEND_SOURCE_CODE,
            TYPE_TEST_CODE,
            TYPE_DEPLOYMENT_FILE,
            TYPE_IAC_CODE,
        ):
            snapshot = artifact_repository.load_file_snapshot(app_id, artifact_type)
            if snapshot:
                snapshots.update(
                    {
                        path: item["content"]
                        for path, item in snapshot.get("files", {}).items()
                    }
                )
                base_versions[artifact_type] = snapshot["version_no"]

        job_path = self.client.prepare_feedback_job(
            job_id,
            app_id,
            design,
            snapshots,
            feedback,
            base_package,
            allow_assumptions,
        )
        metadata = source_snapshot.get("metadata", {})
        record = {
            "job_id": job_id,
            "job_type": "FEEDBACK_REVISION",
            "parent_job_id": metadata.get("implementation_job_id"),
            "app_id": app_id,
            "status": "QUEUED",
            "base_package": base_package,
            "base_versions": base_versions,
            "feedback": feedback,
            "feedback_eligibility": eligibility,
            "job_path": str(job_path),
            "run_root": None,
            "workflow": None,
            "transmission_request": None,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._write(record)
        self.executor.submit(self._plan, job_id)
        return self.public_record(record)

    def get(self, job_id: str) -> dict[str, Any]:
        """호스트 내부 경로를 제외한 구현 작업 상태를 반환한다."""
        record = self._with_live_generation_progress(self._read(job_id))
        record["checkpoint_retryable"] = self._checkpoint_retryable(record)
        return self.public_record(record)

    def live_sources(self, job_id: str, app_id: str) -> dict[str, Any]:
        """실행 중 폴더에서 화면에 보여 줄 소스 목록을 만든다.

        HTTP 계층에는 ``run_root``를 넘기지 않는다. 작업 ID와 앱 ID가 모두 맞는지 확인한 뒤
        해당 job 폴더 안의 ``application``만 읽으며, 현재 OpenHands 작업의 출력 예정 경로도
        합쳐 아직 생성되지 않은 파일을 ``writing`` 상태로 보여 준다.
        """

        record, run_root, application = self._live_application(job_id, app_id)
        writing_paths = self._running_write_paths(run_root)
        files = {
            item.workspace_path: {
                "path": item.workspace_path,
                "artifact_type": item.artifact_type,
                "artifact_path": item.artifact_path,
                "sha256": item.sha256,
                "size": item.size,
                "exists": True,
                "status": (
                    "writing" if item.workspace_path in writing_paths else "available"
                ),
            }
            for item in iter_application_sources(application)
        }
        for workspace_path in writing_paths:
            if workspace_path in files or not is_visible_source_path(workspace_path):
                continue
            artifact_type, artifact_path = classify_source_path(workspace_path)
            files[workspace_path] = {
                "path": workspace_path,
                "artifact_type": artifact_type,
                "artifact_path": artifact_path,
                "sha256": "",
                "size": 0,
                "exists": False,
                "status": "writing",
            }
        ordered = [files[path] for path in sorted(files)]
        revision_input = "\n".join(
            f"{item['path']}:{item['status']}:{item['sha256']}" for item in ordered
        )
        return {
            "job_id": job_id,
            "run_id": run_root.name,
            "status": str(record.get("status") or ""),
            "revision": hashlib.sha256(revision_input.encode("utf-8")).hexdigest(),
            "files": ordered,
        }

    def live_source_file(
        self, job_id: str, app_id: str, workspace_path: str
    ) -> dict[str, Any]:
        """실행 중 애플리케이션의 UTF-8 text 파일 하나를 반환한다."""

        _record, _run_root, application = self._live_application(job_id, app_id)
        item = read_application_source(application, workspace_path)
        return {
            "path": item.workspace_path,
            "artifact_type": item.artifact_type,
            "artifact_path": item.artifact_path,
            "content": item.content,
            "sha256": item.sha256,
            "size": item.size,
        }

    def _live_application(
        self, job_id: str, app_id: str
    ) -> tuple[dict[str, Any], Path, Path]:
        """job 기록을 확인하고 허용된 실제 application 폴더를 찾는다."""

        if re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
            raise JobNotFound(job_id)
        record = self._read(job_id)
        if str(record.get("app_id")) != app_id:
            raise JobNotFound(job_id)
        run_root_value = record.get("run_root")
        if not isinstance(run_root_value, str):
            raise JobNotFound(job_id)
        job_root = (self.settings.work_root / job_id).resolve()
        run_root = Path(run_root_value).resolve()
        try:
            run_root.relative_to(job_root)
        except ValueError as error:
            raise JobNotFound(job_id) from error
        application = run_root / "application"
        if not application.is_dir():
            raise JobNotFound(job_id)
        return record, run_root, application

    @staticmethod
    def _running_write_paths(run_root: Path) -> set[str]:
        """현재 RUNNING task가 작성할 application 상대 경로를 읽는다."""

        try:
            state = json.loads(
                (run_root / "reports" / "workflow-state.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest = json.loads(
                (run_root / "reports" / "run-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            return set()
        running_ids = {
            str(task.get("taskId"))
            for task in state.get("tasks", [])
            if isinstance(task, dict) and task.get("status") == "RUNNING"
        }
        paths = {
            str(path).replace("\\", "/").lstrip("/")
            for task in manifest.get("implementation_tasks", [])
            if isinstance(task, dict) and str(task.get("task_id")) in running_ids
            for path in task.get("allowed_write_paths", [])
        }
        return {
            path.removeprefix("application/")
            for path in paths
            if path.startswith("application/")
        }

    def retry_failed(self, job_id: str) -> dict[str, Any]:
        """승인된 checkpoint에서 실패했거나 감사에 멈춘 단계만 다시 시작한다."""
        record = self._read(job_id)
        if record.get("status") not in {"FAILED", "NEEDS_PLANNER"}:
            raise InvalidJobState(
                "Only a failed or audit-blocked implementation job can be retried: "
                f"{record.get('status')}"
            )
        if not self._checkpoint_retryable(record):
            raise InvalidJobState(
                "The failed implementation job has no approved execution checkpoint; "
                "start a fresh implementation run instead."
            )

        approval_path = Path(record["job_path"]).parent / "approval.json"
        record["status"] = "QUEUED"
        record["checkpoint_retry_count"] = int(
            record.get("checkpoint_retry_count", 0)
        ) + 1
        record["updated_at"] = _now()
        record.pop("error", None)
        record.pop("blocking_details", None)
        self._write(record)
        self.executor.submit(
            self._run,
            job_id,
            str(approval_path),
            True,
        )
        record["checkpoint_retryable"] = False
        return self.public_record(record)

    @staticmethod
    def _checkpoint_retryable(record: dict[str, Any]) -> bool:
        """승인된 실행 checkpoint를 같은 Job에서 안전하게 재사용할 수 있는지 확인한다."""
        if record.get("status") not in {"FAILED", "NEEDS_PLANNER"}:
            return False
        job_path_value = record.get("job_path")
        run_root_value = record.get("run_root")
        if not isinstance(job_path_value, str) or not isinstance(run_root_value, str):
            return False
        job_path = Path(job_path_value)
        run_root = Path(run_root_value)
        return (
            job_path.is_file()
            and (job_path.parent / "approval.json").is_file()
            and (run_root / "reports" / "run-manifest.json").is_file()
            and (run_root / "reports" / "workflow-state.json").is_file()
        )

    def get_testing_input(self, job_id: str) -> dict[str, Any]:
        """Testing API가 버전이 고정된 입력을 만들 때 필요한 정보를 반환한다.

        Testing API는 공개 작업 응답을 다시 해석하지 않고, 구현 작업이 저장한 snapshot의
        DB 식별자를 직접 받는다. 이 식별자로 파일을 한 번 복원하면 같은 앱에 새 구현이
        저장되어도 Testing 작업의 입력이 바뀌지 않는다.
        """
        record = self._read(job_id)
        return {
            "job_id": record["job_id"],
            "app_id": record["app_id"],
            "status": record["status"],
            "artifact_version_ids": dict(record.get("artifact_version_ids") or {}),
        }

    def cancel(self, job_id: str) -> dict[str, Any]:
        """종료되지 않은 작업을 취소하고 실행 중인 하위 프로세스도 중지한다."""
        record = self._read(job_id)
        if record["status"] in {"COMPLETED", "FAILED", "CANCELLED", "REJECTED"}:
            raise InvalidJobState(f"Job is already in a terminal state: {record['status']}")
        record["status"] = "CANCELLED"
        record["error"] = "Job execution was cancelled by user request."
        record["updated_at"] = _now()
        self._write(record)
        self.client.cancel(job_id)
        return self.public_record(record)

    def approve(self, job_id: str, request_id: str, approved: bool, approved_by: str, retry_failed: bool, delegate_repair_approvals: bool = True) -> dict[str, Any]:
        """현재 전송 요청을 승인하거나 거절하고, 승인 시 실행 phase를 시작한다.

        ``request_id``가 현재 요청과 같은지 확인해 오래된 화면에서 누른 승인이 새 요청에
        적용되지 않게 한다. 자동 repair 승인 범위도 파일에 기록해 재시작 뒤 검증할 수 있다.
        """
        record = self._read(job_id)
        request = record.get("transmission_request") or {}
        if record["status"] != "AWAITING_APPROVAL":
            raise InvalidJobState(f"Job is not awaiting approval: {record['status']}")
        if request.get("requestId") != request_id:
            raise InvalidJobState("Approval does not match the current transmission request")
        if not approved:
            record["status"] = "REJECTED"
            record["updated_at"] = _now()
            self._write(record)
            return self.public_record(record)
        approval_path = Path(record["job_path"]).parent / "approval.json"
        manifest = json.loads((Path(record["run_root"]) / "reports" / "run-manifest.json").read_text(encoding="utf-8"))
        approval_path.write_text(json.dumps({
            "requestId": request_id, "approved": True, "approvedAt": _now(), "approvedBy": approved_by,
            "delegatedRepairApprovals": delegate_repair_approvals,
            "delegationScope": {
                "runId": Path(record["run_root"]).name,
                "inputHash": manifest.get("input_hash"),
                "initialTaskIds": sorted(
                    str(task["task_id"])
                    for task in manifest.get("implementation_tasks", [])
                ),
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        record["status"] = "QUEUED"
        record["updated_at"] = _now()
        self._write(record)
        self.executor.submit(self._run, job_id, str(approval_path), retry_failed)
        return self.public_record(record)

    def _plan(self, job_id: str) -> None:
        """하위 프로세스에서 코드를 생성한 뒤 실행할 task와 phase를 계획한다."""
        record = self._read(job_id)
        try:
            self._set_status(record, "GENERATING")
            run_root = self.client.generate(Path(record["job_path"]))
            # 생성은 별도 프로세스에서 실행된다. 프로세스가 끝나기 직전에 사용자가 취소했을
            # 수 있으므로 결과를 반영하기 전에 최신 상태를 다시 읽는다.
            if self._read(job_id).get("status") == "CANCELLED":
                return
            record["run_root"] = str(run_root)
            manifest_path = run_root / "reports" / "run-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("status") == "NEEDS_INPUT":
                # 입력 계약 오류는 사용자가 설계를 고쳐 해결할 수 있는 정상적인 중단이다.
                # 오류 보고서를 보존하고, 생성된 코드가 필요한 planner는 실행하지 않는다.
                diagnostics = [
                    str(item.get("message") or "")
                    for item in manifest.get("diagnostics") or []
                    if isinstance(item, dict) and item.get("severity") == "ERROR"
                ]
                workflow = {
                    "schemaVersion": "implementation-workflow/v1alpha1",
                    "status": "NEEDS_INPUT",
                    "currentPhase": "input-validation",
                    "updatedAt": _now(),
                    "phases": [],
                    "tasks": [],
                    "nextRunnableTasks": [],
                    "blockingReason": (
                        "; ".join(diagnostics)
                        or "Implementation input validation requires design changes."
                    ),
                }
                self._apply_workflow(record, workflow)
                return
            self._set_status(record, "PLANNING")
            workflow = self.client.plan_workflow(run_root, Path(record["job_path"]))
            if self._read(job_id).get("status") == "CANCELLED":
                return
            self._apply_workflow(record, workflow)
        except Exception as error:
            self._fail(record, error)

    def _run(self, job_id: str, approval_path: str, retry_failed: bool) -> None:
        """승인된 workflow를 실행하고 완료된 파일을 산출물 저장소에 보관한다."""
        record = self._read(job_id)
        try:
            self._set_status(record, "RUNNING")
            workflow = self.client.run_phase(Path(record["run_root"]), Path(record["job_path"]), Path(approval_path), retry_failed)
            # 수리 결과로 새 task 묶음이 생기면 workflow는 다음 전송 요청과 함께 READY를
            # 반환한다. 위임 범위 안의 요청은 AWAITING_APPROVAL로 저장하지 않고 바로 큐에
            # 넣어, 화면이 순간적으로 사용자 승인을 요구하거나 monitor가 먼저 종료하지
            # 않게 한다. 이전 실패 task도 다시 실행해야 하므로 retry_failed는 True가 된다.
            request = self.client.transmission_request(Path(record["run_root"]))
            if request:
                record["workflow"] = workflow
                record["transmission_request"] = request
                if self._delegated_execution_is_active(record, approval_path):
                    record["status"] = "QUEUED"
                    record["updated_at"] = _now()
                    self._write(record)
                    self.executor.submit(self._run, job_id, approval_path, True)
                    return
            self._apply_workflow(record, workflow)
            if record["status"] == "COMPLETED":
                self._persist_outputs(record)
        except Exception as error:
            self._fail(record, error)

    def _apply_workflow(self, record: dict[str, Any], workflow: dict[str, Any]) -> None:
        """외부 실행기의 workflow 상태를 EasyDep 구현 작업 상태로 변환한다."""
        record["workflow"] = workflow
        request = self.client.transmission_request(Path(record["run_root"]))
        record["transmission_request"] = request
        status = str(workflow.get("status", "FAILED"))
        if request:
            record["status"] = "AWAITING_APPROVAL"
        elif status == "COMPLETE" or (
            status == "READY" and self._workflow_is_complete(workflow)
        ):
            record["status"] = "COMPLETED"
        elif status in {"NEEDS_INPUT", "NEEDS_PLANNER", "FAILED"}:
            record["status"] = status
            record["error"] = str(
                workflow.get("blockingReason")
                or workflow.get("error")
                or f"Implementation job {status}"
            )
            details = workflow.get("blockingDetails")
            record["blocking_details"] = details if isinstance(details, list) else []
        else:
            record["status"] = status
        if status == "COMPLETE":
            record.pop("error", None)
            record.pop("blocking_details", None)
        record["updated_at"] = _now()
        self._write(record)

    @staticmethod
    def _workflow_is_complete(workflow: dict[str, Any]) -> bool:
        """남은 task가 없는 ``READY`` workflow를 완료로 판단한다.

        일부 workflow runner는 모든 task가 성공한 뒤에도 ``COMPLETE``가 아니라 ``READY``를
        반환한다. 새로 계획만 끝난 때도 같은 값을 사용하므로, 실제 task와 phase가 있고
        실행 가능하거나 차단된 일이 하나도 없을 때만 ``COMPLETED``로 바꾼다.
        """
        tasks = workflow.get("tasks")
        phases = workflow.get("phases")
        if not isinstance(tasks, list) or not tasks:
            return False
        if not isinstance(phases, list) or not phases:
            return False
        if workflow.get("nextRunnableTasks"):
            return False
        if workflow.get("blockingReason") or workflow.get("blockingDetails"):
            return False
        return all(
            isinstance(task, dict) and task.get("status") == "SUCCEEDED"
            for task in tasks
        ) and all(
            isinstance(phase, dict)
            and phase.get("status") in {"SUCCEEDED", "UNPLANNED"}
            for phase in phases
        )

    def _persist_outputs(self, record: dict[str, Any]) -> None:
        """생성된 파일을 종류별 snapshot으로 나누어 산출물 저장소에 저장한다."""
        application = Path(record["run_root"]) / "application"
        groups: dict[str, dict[str, str]] = {
            kind: {}
            for kind in (
                TYPE_SOURCE_CODE,
                TYPE_FRONTEND_SOURCE_CODE,
                TYPE_TEST_CODE,
                TYPE_DEPLOYMENT_FILE,
                TYPE_IAC_CODE,
            )
        }
        for source in iter_application_sources(application):
            groups[source.artifact_type][source.artifact_path] = source.content
        metadata = {
            "implementation_job_id": record["job_id"],
            "run_id": Path(record["run_root"]).name,
            "job_type": record.get("job_type", "INITIAL_IMPLEMENTATION"),
            "parent_job_id": record.get("parent_job_id"),
            "base_versions": record.get("base_versions", {}),
            "feedback": record.get("feedback"),
        }
        version_ids = {}
        for artifact_type, files in groups.items():
            if files:
                version_ids[artifact_type] = artifact_repository.save_file_snapshot(
                    record["app_id"], artifact_type, files, metadata=metadata
                )
        # ``save_file_snapshot``이 반환한 DB 식별자만 Testing API에 넘긴다. Testing은
        # 이 ID가 가리키는 파일 묶음을 한 번 복원하고 모든 검사를 같은 폴더에서 실행한다.
        record["artifact_version_ids"] = version_ids
        record["updated_at"] = _now()
        self._write(record)

    @staticmethod
    def _delegated_execution_is_active(record: dict[str, Any], approval_path: str) -> bool:
        """현재 repair 요청이 사용자가 위임한 자동 승인 범위 안인지 확인한다.

        승인 파일의 run ID와 입력 hash가 현재 실행과 같아야 한다. 최초 task 또는 검증된
        repair plan에 포함된 task만 허용해 이전 실행의 승인이 다른 코드에 재사용되지 않게 한다.
        """
        try:
            approval = json.loads(Path(approval_path).read_text(encoding="utf-8"))
            if approval.get("delegatedRepairApprovals") is not True:
                return False
            scope = approval.get("delegationScope")
            run_root = Path(str(record.get("run_root", "")))
            request = record.get("transmission_request") or {}
            if not isinstance(scope, dict) or scope.get("runId") != run_root.name:
                return False
            manifest = json.loads(
                (run_root / "reports" / "run-manifest.json").read_text(encoding="utf-8")
            )
            if scope.get("inputHash") != manifest.get("input_hash"):
                return False
            plan_path = run_root / "reports" / "repair-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
            if plan.get("status") == "STALLED":
                return False
            entries = [item for item in plan.get("entries", []) if isinstance(item, dict)]
            planned_ids = {
                str(task_id)
                for entry in entries
                for task_id in [*entry.get("ownerTaskIds", []), *entry.get("revalidationTaskIds", [])]
            }
            request_ids = {str(item.get("taskId")) for item in request.get("tasks", [])}
            initial_ids = {str(task_id) for task_id in scope.get("initialTaskIds", [])}
            return (
                bool(request_ids)
                and (
                    request_ids.issubset(initial_ids)
                    or request_ids.issubset(planned_ids)
                )
            )
        except (OSError, json.JSONDecodeError):
            return False

    def _set_status(self, record: dict[str, Any], status: str) -> None:
        """상태와 수정 시각을 함께 바꾸고 즉시 디스크에 저장한다."""
        record["status"] = status
        record["updated_at"] = _now()
        self._write(record)

    def _fail(self, record: dict[str, Any], error: Exception) -> None:
        """취소된 작업은 되살리지 않고 나머지 오류를 ``FAILED``로 기록한다."""
        try:
            if self._read(str(record["job_id"])).get("status") == "CANCELLED":
                return
        except JobNotFound:
            pass
        record["status"] = "FAILED"
        record["error"] = str(error)[-4000:]
        record["updated_at"] = _now()
        self._write(record)

    def _record_path(self, job_id: str) -> Path:
        return self.settings.work_root / job_id / "easydep-job-state.json"

    def start_warmup(self) -> bool:
        """사용자 작업용 worker를 차지하지 않는 별도 thread에서 warm-up을 시작한다."""
        if not self.settings.startup_warmup:
            return False
        with self._warmup_lock:
            if self._warmup_started:
                return False
            self._warmup_started = True
            self.warmup_executor.submit(self._warmup)
            return True

    def _warmup(self) -> None:
        try:
            report = self.client.warmup_runtime()
            print(f"[startup] 구현 런타임 워밍업: {report['status']}")
        except Exception as error:
            # warm-up은 첫 요청의 지연을 줄이는 보조 작업이다. 실패해도 서버 시작이나 이후
            # 사용자 요청을 막지 않고 실제 요청이 들어왔을 때 다시 준비하도록 둔다.
            print(f"[startup] 구현 런타임 워밍업 실패(요청 시 재시도): {error}")

    def _recover_pending_jobs(self) -> None:
        """서버 재시작 뒤 디스크에 남은 승인 파일을 확인해 중단된 작업을 재개한다.

        실행을 이미 시작한 작업은 승인 파일이 있을 때만 이어 간다. 메모리에만 있던 승인을
        추측해 실행하지 않으며, 승인 파일이 없으면 이유를 남기고 실패 처리한다.
        """
        for path in self.settings.work_root.glob("*/easydep-job-state.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            status = record.get("status")
            if status not in {"QUEUED", "GENERATING", "PLANNING", "RUNNING"}:
                continue
            if record.get("run_root"):
                approval = Path(record["job_path"]).parent / "approval.json"
                if approval.is_file():
                    self.executor.submit(self._run, record["job_id"], str(approval), True)
                else:
                    record["status"] = "FAILED"
                    record["error"] = "Interrupted run has no durable approval file"
                    self._write(record)
            else:
                self.executor.submit(self._plan, record["job_id"])

    def _read(self, job_id: str) -> dict[str, Any]:
        """작업 상태 JSON을 읽으며 파일이 없으면 :class:`JobNotFound`를 발생시킨다."""
        path = self._record_path(job_id)
        if not path.is_file():
            raise JobNotFound(job_id)
        with self.lock:
            return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _with_live_generation_progress(record: dict[str, Any]) -> dict[str, Any]:
        """호스트 경로를 숨긴 채 하위 프로세스의 세부 진행 상태를 응답에 합친다."""
        if record.get("status") not in {"GENERATING", "PLANNING"}:
            return record
        job_path = record.get("job_path")
        if not isinstance(job_path, str):
            return record
        progress_path = Path(job_path).parent / "generation-progress.json"
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return record
        status = progress.get("status")
        if not isinstance(status, str):
            return record
        result = dict(record)
        result["progress"] = {
            key: progress[key]
            for key in ("status", "message", "updatedAt")
            if isinstance(progress.get(key), str)
        }
        # 생성 함수가 반환하면 저장 상태가 PLANNING으로 바뀐다. 그전에는 하위 프로세스가
        # 기록한 자세한 phase를 보여 주어 긴 생성 구간을 한 상태로만 표시하지 않게 한다.
        if record.get("status") == "GENERATING" and status in {
            "VALIDATING_INPUT",
            "REUSING_GENERATED_RUN",
            "PREPARING_FEEDBACK",
            "GENERATING_SOURCES",
            "PREPARING_BUILD",
            "VERIFYING",
            "PLANNING",
        }:
            result["status"] = status
            result["updated_at"] = progress.get("updatedAt", record.get("updated_at"))
        return result

    def _write(self, record: dict[str, Any]) -> None:
        """작업 상태를 UTF-8 JSON으로 쓰고 가능한 경우 원자적으로 교체한다."""
        path = self._record_path(record["job_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            payload = json.dumps(record, ensure_ascii=False, indent=2)
            # 고정된 .tmp 이름은 다른 서버 프로세스나 백신이 파일을 여는 순간 충돌할 수 있다.
            # 매번 고유한 임시 파일을 만들고 os.replace를 재시도한다. Windows가 기존 파일의
            # 교체만 잠시 거부하면 마지막 수단으로 대상 파일에 직접 덮어쓴다.
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(payload, encoding="utf-8")
                last_error: PermissionError | None = None
                for attempt in range(3):
                    try:
                        os.replace(temporary, path)
                        last_error = None
                        break
                    except PermissionError as error:
                        last_error = error
                        if attempt < 2:
                            time.sleep(0.05 * (attempt + 1))
                if last_error is not None:
                    # 일부 Windows 파일 공유 설정은 기존 파일을 열어 쓰는 것은 허용하면서
                    # 교체는 거부한다. 작업 전체를 실패시키지 않고 상태 기록을 남기는 쪽을 택한다.
                    path.write_text(payload, encoding="utf-8")
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def public_record(record: dict[str, Any]) -> dict[str, Any]:
        """내부 경로와 전체 source 내용을 제거한 HTTP 응답용 작업 정보를 만든다."""
        result = {key: value for key, value in record.items() if key not in {"job_path", "run_root"}}
        request = result.get("transmission_request")
        if isinstance(request, dict):
            result["transmission_request"] = {
                **{key: value for key, value in request.items() if key != "tasks"},
                "tasks": [
                    {
                        **{key: value for key, value in task.items() if key != "sourceArtifacts"},
                        "sourceArtifacts": sorted((task.get("sourceArtifacts") or {}).keys()),
                    }
                    for task in request.get("tasks", [])
                ],
            }
        return result

    def shutdown(self) -> None:
        """새 작업 접수를 멈추고 실행 중인 하위 프로세스와 future를 정리한다."""
        self.client.cancel_all()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.warmup_executor.shutdown(wait=False, cancel_futures=True)


worker = ImplementationWorker()
