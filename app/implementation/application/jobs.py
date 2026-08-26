from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_IAC_CODE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
)
from app.repositories import artifact_repository
from app.design.validation import design_readiness_report
from ..config import ImplementationSettings
from ..workflows.repair import repair_rounds
from .feedback import assess_feedback_eligibility
from .prototype import PrototypeClient


# Most design findings can remain visible while implementation proceeds: they
# may concern an incomplete alternate sequence path or a review preference.
# These rules are different.  Their mapping deterministically removes or
# changes a BCE scalar before Java contracts are generated, so no mapper can
# repair the result without inventing a persistence decision.
_IMPLEMENTATION_BLOCKING_DESIGN_RULES = frozenset({
    "api.operations-present",
    "erd.surrogate-key-collides",
    "class.contract-types-exist",
})
_OPENAPI_HTTP_METHODS = frozenset({
    "delete", "get", "head", "options", "patch", "post", "put", "trace",
})
_JAVA_CONTRACT_TYPES = frozenset({
    "String", "Object", "boolean", "Boolean", "byte", "Byte", "char", "Character",
    "short", "Short", "int", "Integer", "long", "Long", "float", "Float", "double",
    "Double", "void", "Void", "List", "Set", "Map", "Collection", "Iterable",
    "Optional", "Page", "UUID", "Date", "LocalDate", "LocalDateTime", "OffsetDateTime",
    "Instant", "BigDecimal",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_implementation_blocking_design_finding(readiness: dict[str, Any]) -> bool:
    """Return whether a design finding would make generated contracts lossy.

    This intentionally checks stable rule IDs embedded in the readiness text,
    not field-name heuristics.  The ERD checker has already established that a
    scalar field would be displaced by a generated surrogate key; proceeding
    would otherwise create an unrepresentable BCE-to-JPA mapper.
    """
    return any(
        rule_id in str(finding.get("finding") or "")
        for finding in readiness.get("findings") or []
        if isinstance(finding, dict)
        for rule_id in _IMPLEMENTATION_BLOCKING_DESIGN_RULES
    )


def _missing_bce_contract_types(class_diagram: object) -> list[str]:
    """Find custom Java types used by BCE signatures but not declared in the BCE diagram.

    A missing request type is otherwise silently downgraded by OpenAPI generation to
    ``Object``.  The API adapter cannot then convert the request to the Control input
    without inventing a contract, so this must be reported before any LLM task starts.
    """
    source = str(class_diagram or "")
    declarations = set(re.findall(
        r"(?im)^\s*(?:class|interface|entity)\s+(?:\"[^\"]+\"\s+as\s+)?([A-Za-z_]\w*)",
        source,
    ))
    if not declarations:
        return []
    missing: set[str] = set()
    in_class = False
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if line.startswith(("class ", "interface ", "entity ")):
            in_class = True
            continue
        if in_class and line == "}":
            in_class = False
            continue
        if not in_class or not line.startswith(("+", "-", "#", "~")) or ":" not in line:
            continue
        # Only inspect the declared type portions of fields, parameters, and returns.
        type_text = line.split(":", 1)[1]
        for token in re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", type_text):
            if token not in declarations and token not in _JAVA_CONTRACT_TYPES:
                missing.add(token)
    return sorted(missing)


def _append_bce_contract_type_report(
    readiness: dict[str, Any], class_diagram: object
) -> dict[str, Any]:
    missing = _missing_bce_contract_types(class_diagram)
    if not missing:
        return readiness
    finding = (
        "BCE method signatures reference undeclared type(s): "
        + ", ".join(missing)
        + " — declare the type in the class diagram before implementation "
        "[class.contract-types-exist]"
    )
    result = {**readiness, "status": "NEEDS_INPUT"}
    result["findings"] = [*list(readiness.get("findings") or []), {
        "stage": "class_diagram", "finding": finding,
    }]
    stages = [dict(item) for item in readiness.get("stages") or [] if isinstance(item, dict)]
    stage = next((item for item in stages if item.get("stage") == "class_diagram"), None)
    if stage is None:
        stages.append({"stage": "class_diagram", "status": "NEEDS_INPUT", "findings": [finding]})
    else:
        stage["status"] = "NEEDS_INPUT"
        stage["findings"] = [*list(stage.get("findings") or []), finding]
    result["stages"] = stages
    return result


def _has_rendered_openapi_operation(api_spec: object) -> bool:
    """Check the rendered artifact, not just the intermediate endpoint model."""
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
    """Add a deterministic rendered-contract finding to a readiness report."""
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
        self.warmup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="easydep-warmup")
        self.lock = threading.RLock()
        self._warmup_lock = threading.Lock()
        self._warmup_started = False
        self._recover_pending_jobs()

    def create_job(self, app_id: str, design: dict[str, Any], base_package: str, allow_assumptions: bool) -> dict[str, Any]:
        missing = [key for key in ("class_diagram_puml", "api_spec") if design.get(key) in (None, "", {})]
        if missing:
            raise InvalidJobState("Missing required design artifacts: " + ", ".join(missing))
        missing_models = [
            key for key in (
                "extracted_bce_classes", "sequence_diagram_model", "api_spec_model",
            )
            if not isinstance(design.get(key), dict) or not design[key]
        ]
        readiness = _append_bce_contract_type_report(
            design_readiness_report(design), design.get("class_diagram_puml")
        )
        # Prefer the concrete rendered-contract defect over a generic missing
        # model report: this is the exact reason both OpenAPI generators would
        # reject the hand-off.
        if not _has_rendered_openapi_operation(design.get("api_spec")):
            return self._create_design_blocked_job(
                app_id,
                base_package,
                _missing_openapi_operation_report(readiness),
            )
        if missing_models and not self._has_substantial_rendered_design(design):
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
            # Deterministic design findings remain visible to the implementation
            # run, but a complete design artifact set is sufficient to proceed.
            "design_validation": readiness,
            "transmission_request": None, "error": None, "created_at": _now(), "updated_at": _now(),
        }
        self._write(record)
        self.executor.submit(self._plan, job_id)
        return self.public_record(record)

    @staticmethod
    def _has_substantial_rendered_design(design: dict[str, Any]) -> bool:
        """Allow implementation to proceed when rendered artifacts are usable.

        Derived design models are useful for readiness checks, but their absence
        must not discard a complete class diagram/OpenAPI pair.  The generator
        consumes those rendered artifacts directly and records any remaining
        contract gaps in its reports.  Tiny placeholder inputs (or an OpenAPI
        document with no operations) remain blocked.
        """
        class_diagram = design.get("class_diagram_puml")
        api_spec = design.get("api_spec")
        if not isinstance(class_diagram, str) or "@startuml" not in class_diagram:
            return False
        if not isinstance(api_spec, dict):
            return False
        return _has_rendered_openapi_operation(api_spec)

    def _create_design_blocked_job(
        self, app_id: str, base_package: str, readiness: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist an actionable hand-off block without starting any generator."""
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
        """Old rendered-only artifacts cannot prove the API-to-Control contract."""
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
        return self.public_record(self._with_live_generation_progress(self._read(job_id)))

    def get_testing_input(self, job_id: str) -> dict[str, Any]:
        """Return the minimum private execution context needed by the test adapter.

        ``get`` deliberately removes ``run_root`` from the browser-facing job
        record.  The testing API is a trusted in-process consumer, so it gets a
        narrow context instead of relying on the public record or exposing the
        workspace path over HTTP.
        """
        record = self._read(job_id)
        return {
            "job_id": record["job_id"],
            "app_id": record["app_id"],
            "status": record["status"],
            "run_root": record.get("run_root"),
        }

    def cancel(self, job_id: str) -> dict[str, Any]:
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
            self._set_status(record, "GENERATING")
            run_root = self.client.generate(Path(record["job_path"]))
            # Generation runs in a separate process.  Do not revive a job the
            # user cancelled while that process was finishing.
            if self._read(job_id).get("status") == "CANCELLED":
                return
            record["run_root"] = str(run_root)
            manifest_path = run_root / "reports" / "run-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("status") == "NEEDS_INPUT":
                # Input-contract defects are expected and actionable.  Keep
                # their immutable report and hand the job back to design
                # instead of attempting planners that require generated code.
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
        """Treat a fully drained READY workflow as a completed execution.

        Older workflow runners return ``READY`` after the final audit even
        though every task has succeeded.  ``READY`` is also used for a newly
        planned workflow, so only a task-bearing workflow with no runnable or
        blocked work can be promoted to the job's terminal COMPLETED state.
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
        for path in application.rglob("*"):
            if not path.is_file() or "build" in path.parts or ".gradle" in path.parts:
                continue
            relative = path.relative_to(application).as_posix()
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            lowered = relative.lower()
            if relative.startswith("frontend/"):
                kind = TYPE_FRONTEND_SOURCE_CODE
                relative = relative.removeprefix("frontend/")
            elif relative.startswith("deployment-bundle/"):
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
            rounds = repair_rounds(plan)
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
        """Start best-effort warm-up without consuming a user-job worker slot."""
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
            # Warming improves latency but must never make the service unhealthy.
            print(f"[startup] 구현 런타임 워밍업 실패(요청 시 재시도): {error}")

    def _recover_pending_jobs(self) -> None:
        """Resume queued work after a server restart using only durable approvals."""
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
        path = self._record_path(job_id)
        if not path.is_file():
            raise JobNotFound(job_id)
        with self.lock:
            return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _with_live_generation_progress(record: dict[str, Any]) -> dict[str, Any]:
        """Overlay subprocess progress without exposing its host-side path."""
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
        # Once generation returns, the durable job status becomes PLANNING.
        # Before then, expose the finer phase emitted by the child process.
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
        self.warmup_executor.shutdown(wait=False, cancel_futures=True)


worker = ImplementationWorker()
