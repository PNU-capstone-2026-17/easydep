from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_IAC_CODE, TYPE_SOURCE_CODE, TYPE_TEST_CODE
from app.repositories import artifact_repository
from .config import ImplementationSettings
from .feedback_impact import assess_feedback_eligibility
from .prototype_client import PrototypeClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobNotFound(KeyError):
    pass


class InvalidJobState(RuntimeError):
    pass


class ImplementationWorker:
    """Persistent job registry plus a bounded local execution queue."""

    def __init__(self, settings: ImplementationSettings | None = None):
        self.settings = settings or ImplementationSettings.from_env()
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        self.client = PrototypeClient(self.settings)
        self.executor = ThreadPoolExecutor(max_workers=self.settings.max_workers, thread_name_prefix="easydep-implementation")
        self.lock = threading.RLock()
        self._recover_pending_jobs()

    def create_job(self, app_id: str, design: dict[str, Any], base_package: str, allow_assumptions: bool) -> dict[str, Any]:
        missing = [key for key in ("class_diagram_puml", "api_spec") if design.get(key) in (None, "", {})]
        if missing:
            raise InvalidJobState("Missing required design artifacts: " + ", ".join(missing))
        job_id = uuid.uuid4().hex
        job_path = self.client.prepare_job(job_id, app_id, design, base_package, allow_assumptions)
        record = {
            "job_id": job_id, "app_id": app_id, "status": "QUEUED", "base_package": base_package,
            "job_path": str(job_path), "run_root": None, "workflow": None,
            "transmission_request": None, "error": None, "created_at": _now(), "updated_at": _now(),
        }
        self._write(record)
        self.executor.submit(self._plan, job_id)
        return self.public_record(record)

    def create_feedback_job(
        self,
        app_id: str,
        design: dict[str, Any],
        feedback: str,
        base_package: str,
        allow_assumptions: bool,
    ) -> dict[str, Any]:
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
                "status": "REJECTED",
                "base_package": base_package,
                "feedback": feedback,
                "workflow": None,
                "transmission_request": None,
                "feedback_eligibility": eligibility,
                "eligibility_report": str(report_path.relative_to(self.settings.work_root)),
                "error": "Feedback requires a design-contract change and is unsuitable for implementation feedback.",
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
        return self.public_record(self._read(job_id))

    def approve(self, job_id: str, request_id: str, approved: bool, approved_by: str, retry_failed: bool, delegate_repair_approvals: bool = True) -> dict[str, Any]:
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
                "maxRepairRounds": 3,
                "maxTaskAttempts": 50,
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        record["status"] = "QUEUED"
        record["updated_at"] = _now()
        self._write(record)
        self.executor.submit(self._run, job_id, str(approval_path), retry_failed)
        return self.public_record(record)

    def _plan(self, job_id: str) -> None:
        record = self._read(job_id)
        try:
            self._set_status(record, "PLANNING")
            run_root, workflow = self.client.generate_and_plan(Path(record["job_path"]))
            record["run_root"] = str(run_root)
            self._apply_workflow(record, workflow)
        except Exception as error:
            self._fail(record, error)

    def _run(self, job_id: str, approval_path: str, retry_failed: bool) -> None:
        record = self._read(job_id)
        try:
            self._set_status(record, "RUNNING")
            workflow = self.client.run_phase(Path(record["run_root"]), Path(record["job_path"]), Path(approval_path), retry_failed)
            self._apply_workflow(record, workflow)
            if (
                record["status"] == "AWAITING_APPROVAL"
                and self._delegated_execution_is_active(record, approval_path)
            ):
                record["status"] = "QUEUED"
                record["updated_at"] = _now()
                self._write(record)
                self.executor.submit(self._run, job_id, approval_path, retry_failed)
                return
            if record["status"] == "COMPLETED":
                self._persist_outputs(record)
        except Exception as error:
            self._fail(record, error)

    def _apply_workflow(self, record: dict[str, Any], workflow: dict[str, Any]) -> None:
        record["workflow"] = workflow
        request = self.client.transmission_request(Path(record["run_root"]))
        record["transmission_request"] = request
        status = str(workflow.get("status", "FAILED"))
        if request:
            record["status"] = "AWAITING_APPROVAL"
        elif status == "COMPLETE":
            record["status"] = "COMPLETED"
        elif status in {"NEEDS_INPUT", "NEEDS_PLANNER", "FAILED"}:
            record["status"] = status
        else:
            record["status"] = status
        record["updated_at"] = _now()
        self._write(record)

    def _persist_outputs(self, record: dict[str, Any]) -> None:
        application = Path(record["run_root"]) / "application"
        groups: dict[str, dict[str, str]] = {kind: {} for kind in (TYPE_SOURCE_CODE, TYPE_TEST_CODE, TYPE_DEPLOYMENT_FILE, TYPE_IAC_CODE)}
        for path in application.rglob("*"):
            if not path.is_file() or "build" in path.parts or ".gradle" in path.parts:
                continue
            relative = path.relative_to(application).as_posix()
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            lowered = relative.lower()
            if relative.startswith("deployment-bundle/"):
                kind = TYPE_DEPLOYMENT_FILE
            elif "/test/" in f"/{lowered}":
                kind = TYPE_TEST_CODE
            elif relative == ".dockerignore" or any(
                token in lowered for token in ("k8s/", "dockerfile", "helm/")
            ):
                kind = TYPE_DEPLOYMENT_FILE
            elif any(token in lowered for token in ("terraform/", ".tf", "pulumi/")):
                kind = TYPE_IAC_CODE
            else:
                kind = TYPE_SOURCE_CODE
            groups[kind][relative] = content
        metadata = {
            "implementation_job_id": record["job_id"],
            "run_id": Path(record["run_root"]).name,
            "job_type": record.get("job_type", "INITIAL_IMPLEMENTATION"),
            "parent_job_id": record.get("parent_job_id"),
            "base_versions": record.get("base_versions", {}),
            "feedback": record.get("feedback"),
        }
        versions = {}
        for artifact_type, files in groups.items():
            if files:
                versions[artifact_type] = artifact_repository.save_file_snapshot(record["app_id"], artifact_type, files, metadata=metadata)
        record["artifact_versions"] = versions
        record["updated_at"] = _now()
        self._write(record)

    @staticmethod
    def _delegated_execution_is_active(record: dict[str, Any], approval_path: str) -> bool:
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
            entries = [item for item in plan.get("entries", []) if isinstance(item, dict)]
            planned_ids = {
                str(task_id)
                for entry in entries
                for task_id in [*entry.get("ownerTaskIds", []), *entry.get("revalidationTaskIds", [])]
            }
            request_ids = {str(item.get("taskId")) for item in request.get("tasks", [])}
            initial_ids = {str(task_id) for task_id in scope.get("initialTaskIds", [])}
            attempts = sum(
                int(task.get("attempts", 0))
                for task in (record.get("workflow") or {}).get("tasks", [])
                if isinstance(task, dict)
            )
            rounds = max((int(entry.get("revision", 0)) for entry in entries), default=0)
            return (
                bool(request_ids)
                and (request_ids.issubset(initial_ids) or request_ids.issubset(planned_ids))
                and rounds <= int(scope.get("maxRepairRounds", 0))
                and attempts < int(scope.get("maxTaskAttempts", 0))
            )
        except (OSError, json.JSONDecodeError):
            return False

    def _set_status(self, record: dict[str, Any], status: str) -> None:
        record["status"] = status
        record["updated_at"] = _now()
        self._write(record)

    def _fail(self, record: dict[str, Any], error: Exception) -> None:
        record["status"] = "FAILED"
        record["error"] = str(error)[-4000:]
        record["updated_at"] = _now()
        self._write(record)

    def _record_path(self, job_id: str) -> Path:
        return self.settings.work_root / job_id / "easydep-job-state.json"

    def _recover_pending_jobs(self) -> None:
        """Resume queued work after a server restart using only durable approvals."""
        for path in self.settings.work_root.glob("*/easydep-job-state.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            status = record.get("status")
            if status not in {"QUEUED", "PLANNING", "RUNNING"}:
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
        path = self._record_path(job_id)
        if not path.is_file():
            raise JobNotFound(job_id)
        with self.lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, record: dict[str, Any]) -> None:
        path = self._record_path(record["job_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with self.lock:
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    @staticmethod
    def public_record(record: dict[str, Any]) -> dict[str, Any]:
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
        self.executor.shutdown(wait=False, cancel_futures=True)


worker = ImplementationWorker()
