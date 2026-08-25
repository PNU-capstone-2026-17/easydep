from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.design.api import (
    BatchReviseRequest,
    FeedbackRequest,
    ReviseRequest,
    RewindRequest,
    StageRequest,
    resume_design_session,
    retry_design_session,
    revise_design_element,
    revise_design_elements,
    rewind_design_session,
    start_design_session,
)
from app.design.graphs.design_graph import has_active_session, session_status
from app.design.graphs.subgraphs import DESIGN_STAGES
from app.implementation.application.jobs import worker as implementation_worker
from app.implementation.interfaces.http import (
    approve_job,
    cancel_job,
    create_feedback_job,
    create_job,
)
from app.implementation.interfaces.schemas import (
    ApprovalRequest,
    CreateImplementationFeedbackJobRequest,
    CreateImplementationJobRequest,
)
from app.repositories import artifact_repository
from app.requirements.api import analyze_endpoint
from app.requirements.common import telemetry as requirements_telemetry
from app.requirements.config import settings as requirements_settings
from app.requirements.schemas import (
    AnalyzeRequest,
    DeploymentPreferences,
    InitialCloudConstraints,
)
from app.testing.api import CreateTestingJobRequest, create_testing_job, get_testing_job

from . import repository

TERMINAL_JOB_STATUSES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "REJECTED",
    "NEEDS_INPUT",
    "NEEDS_PLANNER",
}


# The implementation worker has two distinct parts: initial deterministic
# generation and the resumable agent workflow.  Keep their user-facing labels
# here so the workspace can report the same stable milestones even when the
# underlying task plan differs by application.
_IMPLEMENTATION_GENERATION_STEPS = (
    ("validate-input", "입력 및 설계 검증"),
    ("generate-sources", "기본 소스 생성"),
    ("prepare-build", "빌드 환경 구성"),
    ("verify-generated", "초기 컴파일 검증"),
    ("plan-workflow", "구현 작업 계획"),
)
_IMPLEMENTATION_WORKFLOW_PHASES = (
    ("control", "Control 구현"),
    ("persistence", "Repository 구현"),
    ("api-adapters", "API Adapter 구현"),
    ("boundary-adapters", "Boundary Adapter 구현"),
    ("outbound-adapters", "Outbound Adapter 구현"),
    ("wiring", "Application Setup"),
    ("frontend", "Frontend 구현"),
    ("end-to-end", "E2E Test 실행"),
)
_IMPLEMENTATION_DISPLAY_PHASES = (
    (
        "backend",
        "Backend 구현",
        frozenset(
            {
                "control",
                "persistence",
                "api-adapters",
                "boundary-adapters",
                "outbound-adapters",
                "wiring",
            }
        ),
    ),
    ("frontend", "Frontend 구현", frozenset({"frontend"})),
    ("e2e", "E2E 통합 테스트 실행", frozenset({"end-to-end"})),
)
def _json_response(response: JSONResponse) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


class WorkspaceService:
    """기존 단계 API를 한 대화형 명령 경계로 묶는 얇은 비동기 서비스다."""

    def __init__(self) -> None:
        workers = max(1, min(4, int(os.getenv("EASYDEP_WORKSPACE_WORKERS", "2"))))
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="easydep-workspace"
        )
        self._submission_lock = Lock()

    def startup(self) -> int:
        return repository.interrupt_unfinished()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def reconcile_implementation_command(self, app_id: str) -> dict[str, Any] | None:
        """Close a stale workspace command after the durable worker completed.

        A completed workflow can be persisted as ``READY`` by older runners.
        The implementation worker is authoritative, so reconcile that state
        when the workspace is read instead of leaving the UI in an endless
        running state after a server restart or monitor interruption.
        """
        command = repository.latest_command(app_id)
        if not command or command.get("status") not in {"RUNNING", "INTERRUPTED"}:
            return command
        if command.get("action") not in {
            "start_implementation",
            "rerun_implementation",
            "approve_implementation",
        }:
            return command
        payload = command.get("payload") or {}
        job_id = str(payload.get("job_id") or "")
        if not job_id:
            return command
        try:
            job = implementation_worker.get(job_id)
        except Exception:
            return command
        job_status = str(job.get("status") or "")
        workflow = job.get("workflow")
        workflow_complete = (
            isinstance(workflow, dict)
            and implementation_worker._workflow_is_complete(workflow)
        )
        if job_status != "COMPLETED" and not (
            job_status == "READY" and workflow_complete
        ):
            return command
        result = {
            "message": "Review the generated implementation artifacts below.",
            "job_id": job_id,
            "job": job,
            "review_artifacts": True,
        }
        updated = repository.update_command(
            command["command_id"],
            status="COMPLETED",
            result=result,
            completed_at=repository.now(),
            error=None,
        )
        repository.append_event(
            app_id,
            command_id=command["command_id"],
            stage="implementation",
            kind="status",
            actor="system",
            text="Implementation completed.",
            metadata={"status": "COMPLETED", "job_id": job_id},
        )
        return updated

    @staticmethod
    def _sequence_target_feedbacks(context: dict[str, Any]) -> list[ReviseRequest]:
        """Parse UI-provided, per-UC feedback without inferring any target."""
        raw_entries = context.get("target_feedbacks")
        if raw_entries is None:
            return []
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("Select at least one sequence-diagram feedback target.")
        revisions: list[ReviseRequest] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise ValueError("Each sequence feedback entry must name a target and feedback.")
            target = str(raw.get("target") or "").strip()
            feedback = str(raw.get("feedback") or "").strip()
            if not target.startswith("sequence_diagram:") or target == "sequence_diagram:":
                raise ValueError("Sequence feedback targets must be selected use-case diagrams.")
            if not feedback:
                raise ValueError(f"Feedback for {target} cannot be empty.")
            revisions.append(ReviseRequest(target=target, feedback=feedback))
        # Pydantic applies duplicate-target validation at the command boundary
        # before any individual revision can run.
        return BatchReviseRequest(revisions=revisions).revisions

    def submit(
        self,
        app_id: str,
        *,
        action: str,
        payload: dict[str, Any],
        stage: str | None = None,
    ) -> dict[str, Any]:
        artifact_repository.ensure_app_exists(app_id)
        resolved_stage = stage or self.infer_stage(app_id, action, payload)
        self._validate_payload(action, payload)
        self._validate_action_reference(app_id, action, payload)
        text = str(payload.get("text") or "").strip()
        context = payload.get("context") or {}
        if (
            action == "message"
            and text
            and context.get("artifact_stage") == "sequence_diagram"
            and not context.get("target_feedbacks")
            and not str(context.get("element_ref") or "").strip()
        ):
            raise ValueError(
                "Select at least one use-case target and enter feedback for each selected target."
            )
        command_id = str(uuid.uuid4())
        with self._submission_lock:
            command = repository.create_command(
                command_id, app_id, action, resolved_stage, payload
            )
        if text:
            repository.append_event(
                app_id,
                command_id=command_id,
                stage=resolved_stage,
                kind="message",
                actor="user",
                text=text,
                metadata={"context": payload.get("context")},
            )

        if action == "message" and context and context.get("stage") != resolved_stage:
            result = {
                "action_id": command_id,
                "action": "confirm_change",
                "context": context,
                "message": "This change may affect an earlier stage and its downstream artifacts.",
            }
            repository.update_command(
                command_id,
                status="AWAITING_INPUT",
                result=result,
                started_at=repository.now(),
            )
            repository.append_event(
                app_id,
                command_id=command_id,
                stage=str(context.get("stage") or resolved_stage),
                kind="action_required",
                actor="system",
                text="Confirm whether to return to the earlier stage and apply the change.",
                metadata=result,
            )
            return repository.get_command(command_id) or command

        self._executor.submit(self._execute, command_id)
        return command

    def apply_saved_deployment_preferences(self, app_id: str) -> dict[str, Any] | None:
        """Resume only a requirements gate that is waiting for cloud coordinates."""
        preferences = repository.get_deployment_preferences(app_id)
        latest = repository.latest_command(app_id)
        if not preferences or latest is None:
            return None
        result = latest.get("result") or {}
        questions = list(result.get("resource_questions") or [])
        fields = {str(question.get("field") or "") for question in questions}
        if (
            latest.get("stage") != "requirements"
            or latest.get("status") != "AWAITING_INPUT"
            or not fields.intersection({"provider", "region"})
        ):
            return None
        try:
            return self.submit(
                app_id,
                action="apply_deployment_preferences",
                payload={
                    "action_id": latest["command_id"],
                    "deployment_preferences": preferences,
                },
                stage="requirements",
            )
        except RuntimeError:
            # A concurrent caller may already have queued the same resume operation.
            return None

    @staticmethod
    def _validate_payload(action: str, payload: dict[str, Any]) -> None:
        required = {
            "advance": ("action_id",),
            "retry_design": ("action_id",),
            "rerun_implementation": (),
            "confirm_change": ("action_id",),
            "dismiss_change": ("action_id",),
            "approve_implementation": ("action_id", "job_id", "request_id"),
            "reject_implementation": ("action_id", "job_id", "request_id"),
            "cancel_implementation": ("job_id",),
            "start_testing": ("implementation_job_id",),
            "apply_deployment_preferences": (
                "action_id",
                "deployment_preferences",
            ),
        }
        missing = [name for name in required.get(action, ()) if not payload.get(name)]
        if missing:
            raise ValueError(f"Missing values for the {action} command: {', '.join(missing)}")

    def _validate_action_reference(
        self, app_id: str, action: str, payload: dict[str, Any]
    ) -> None:
        action_id = str(payload.get("action_id") or "")
        if not action_id:
            return
        prior = repository.get_command(action_id)
        if prior is None or prior["app_id"] != app_id:
            raise ValueError("The command to answer could not be found.")
        if action == "retry_design":
            if prior["status"] not in {"FAILED", "INTERRUPTED"} or prior["stage"] != "design":
                raise ValueError("Only a failed or interrupted design command can be retried.")
            return
        if prior["status"] != "AWAITING_INPUT":
            raise ValueError("The command was already handled or is not awaiting a response.")
        result = prior.get("result") or {}
        if action in {"confirm_change", "dismiss_change"} and result.get("action") != "confirm_change":
            raise ValueError("This command is not awaiting change confirmation.")

    def infer_stage(self, app_id: str, action: str, payload: dict[str, Any]) -> str:
        if action == "apply_deployment_preferences":
            return "requirements"
        if action in {"start_design", "retry_design"}:
            return "design"
        if action in {
            "start_implementation",
            "rerun_implementation",
            "approve_implementation",
            "reject_implementation",
            "cancel_implementation",
        }:
            return "implementation"
        if action == "start_testing":
            return "testing"
        if action in {"confirm_change", "dismiss_change"}:
            prior = repository.get_command(str(payload.get("action_id") or ""))
            return str((prior or {}).get("stage") or "design")
        latest = repository.latest_command(app_id)
        if action == "advance" and latest is not None:
            return str(latest["stage"])
        state = artifact_repository.load_state(app_id)
        if not state.get("refined_requirements") or (
            latest is not None
            and latest["stage"] == "requirements"
            and latest["status"] == "AWAITING_INPUT"
        ):
            return "requirements"
        if not state.get("deployment_diagram_puml") or has_active_session(app_id):
            return "design"
        return "implementation"

    def _execute(self, command_id: str) -> None:
        command = repository.get_command(command_id)
        if command is None:
            return
        app_id = str(command["app_id"])
        stage = str(command["stage"])
        repository.update_command(command_id, status="RUNNING", started_at=repository.now())
        repository.append_event(
            app_id,
            command_id=command_id,
            stage=stage,
            kind="status",
            actor="system",
            text=f"Started {self._stage_label(stage)}.",
            metadata={"status": "RUNNING", "action": command["action"]},
        )
        try:
            result = self._dispatch(command)
            self._complete_referenced_action(command)
            if result.get("awaiting_input"):
                repository.update_command(command_id, status="AWAITING_INPUT", result=result)
                repository.append_event(
                    app_id,
                    command_id=command_id,
                    stage=stage,
                    kind=str(result.get("kind") or "action_required"),
                    actor="assistant",
                    text=str(result.get("message") or "User input is required."),
                    metadata=result,
                )
                if stage == "requirements":
                    self.apply_saved_deployment_preferences(app_id)
                return
            repository.update_command(
                command_id,
                status="COMPLETED",
                result=result,
                completed_at=repository.now(),
            )
            repository.append_event(
                app_id,
                command_id=command_id,
                stage=stage,
                kind="status",
                actor="assistant",
                text=str(
                    result.get("message")
                    or f"Completed {self._stage_label(stage)}."
                ),
                metadata={"status": "COMPLETED", **result},
            )
        except Exception as error:
            detail = self._error_text(error)
            repository.update_command(
                command_id,
                status="FAILED",
                error=detail,
                completed_at=repository.now(),
            )
            repository.append_event(
                app_id,
                command_id=command_id,
                stage=stage,
                kind="error",
                actor="system",
                text=detail,
                metadata={"status": "FAILED", "error_type": type(error).__name__},
            )

    def _complete_referenced_action(self, command: dict[str, Any]) -> None:
        action_id = str(command["payload"].get("action_id") or "")
        if not action_id:
            return
        prior = repository.get_command(action_id)
        if prior is not None and prior["status"] == "AWAITING_INPUT":
            repository.update_command(
                action_id, status="COMPLETED", completed_at=repository.now()
            )

    def _dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command["action"])
        if action in {"message", "advance", "apply_deployment_preferences"}:
            return self._stage_message(command, advance=action == "advance")
        if action == "start_design":
            return self._stage_message(command, advance=True)
        if action == "retry_design":
            app_id = str(command["app_id"])
            status = session_status(app_id)
            stage = str(status.get("stage") or "design")
            response = self._run_design_operation(
                command,
                stage=stage,
                label=self._design_stage_label(stage, "Retrying"),
                operation=lambda: retry_design_session(app_id, StageRequest()),
            )
            return self._design_result(_json_response(response))
        if action == "confirm_change":
            return self._confirm_change(command)
        if action == "dismiss_change":
            return {"message": "Kept the existing artifacts and dismissed the change request."}
        if action in {"start_implementation", "rerun_implementation"}:
            request = CreateImplementationJobRequest(
                base_package=str(
                    command["payload"].get("base_package") or "com.easydep.app"
                ),
                allow_assumptions=bool(
                    command["payload"].get("allow_assumptions", True)
                ),
            )
            job = create_job(str(command["app_id"]), request)
            return self._monitor_implementation(job, command_id=str(command["command_id"]))
        if action in {"approve_implementation", "reject_implementation"}:
            payload = command["payload"]
            app_id = str(command["app_id"])
            approved = action == "approve_implementation"
            repository.append_event(
                app_id,
                command_id=str(command["command_id"]),
                stage="implementation",
                kind="status",
                actor="system",
                text=(
                    "Implementation approval received; resuming execution."
                    if approved
                    else "Implementation execution rejected."
                ),
                metadata={"status": "APPROVAL_RECEIVED" if approved else "REJECTED"},
            )
            job = approve_job(
                str(payload["job_id"]),
                ApprovalRequest(
                    request_id=str(payload["request_id"]),
                    approved=approved,
                    approved_by="EasyDep Workspace",
                    retry_failed=bool(payload.get("retry_failed", False)),
                    delegate_repair_approvals=bool(
                        payload.get("delegate_repair_approvals", True)
                    ),
                ),
            )
            return self._monitor_implementation(job, command_id=str(command["command_id"]))
        if action == "cancel_implementation":
            job = cancel_job(str(command["payload"]["job_id"]))
            return {"message": "Cancelled the implementation job.", "job": job}
        if action == "start_testing":
            job = create_testing_job(
                str(command["app_id"]),
                CreateTestingJobRequest(
                    implementation_job_id=str(
                        command["payload"]["implementation_job_id"]
                    )
                ),
            )
            return self._monitor_testing(job)
        raise ValueError(f"Unsupported workspace command: {action}")

    def _stage_message(
        self, command: dict[str, Any], *, advance: bool
    ) -> dict[str, Any]:
        app_id = str(command["app_id"])
        payload = command["payload"]
        text = "" if advance else str(payload.get("text") or "").strip()
        stage = str(command["stage"])
        if (
            advance
            and stage == "design"
            and payload.get("auto_approve_method_proposals") is True
        ):
            # Auto mode is an affirmative user choice.  Keep the approval in
            # the same feedback path as a manual decision so reconciliation
            # still applies only the concrete, persisted MethodProposals.
            text = "approve all"
        if stage == "requirements":
            action_id = str(payload.get("action_id") or "")
            previous = repository.get_command(action_id) if action_id else None
            continuation = bool(
                action_id
                and previous is not None
                and previous["stage"] == "requirements"
            )
            if continuation:
                previous_result = previous.get("result") or {}
                if command.get("action") == "apply_deployment_preferences":
                    preferences = DeploymentPreferences.model_validate(
                        payload.get("deployment_preferences") or {}
                    )
                    request = AnalyzeRequest(
                        deployment_preferences=preferences,
                        thread_id=app_id,
                        app_id=app_id,
                    )
                    progress = self._requirements_progress_reporter(
                        app_id, str(command["command_id"])
                    )
                    with requirements_telemetry.progress_scope(progress):
                        result = analyze_endpoint(request).model_dump(mode="json")
                    return self._requirements_result(result)
                resource_questions = list(
                    previous_result.get("resource_questions") or []
                )
                resource_question = previous_result.get("resource_question") or next(
                    (
                        question
                        for question in resource_questions
                        if question.get("kind") != "suggested"
                    ),
                    resource_questions[0] if resource_questions else None,
                )
                resource_field = str((resource_question or {}).get("field") or "")
                if text and resource_field:
                    request = AnalyzeRequest(
                        resource_answers={resource_field: text},
                        thread_id=app_id,
                        app_id=app_id,
                    )
                else:
                    request = AnalyzeRequest(answer=text, thread_id=app_id, app_id=app_id)
            else:
                provider = str(payload.get("provider") or "")
                region = str(payload.get("region") or "")
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                cloud_constraints = (
                    InitialCloudConstraints(
                        provider=provider,
                        region=region,
                        monthly_budget_amount=payload.get("monthly_budget_amount"),
                        monthly_budget_currency=str(
                            payload.get("monthly_budget_currency") or "USD"
                        ),
                    )
                    if provider and region
                    else None
                )
                request = AnalyzeRequest(
                    requirements=lines or [text],
                    thread_id=app_id,
                    app_id=app_id,
                    feedback_gates=True,
                    resource_constraints_text=str(
                        payload.get("resource_constraints_text") or ""
                    ),
                    cloud_constraints=cloud_constraints,
                )
            progress = self._requirements_progress_reporter(
                app_id, str(command["command_id"])
            )
            with requirements_telemetry.progress_scope(progress):
                result = analyze_endpoint(request).model_dump(mode="json")
            return self._requirements_result(result)

        if stage == "design":
            status = session_status(app_id)
            if status.get("retryable"):
                failed_stage = str(status.get("stage") or "design")
                raise ValueError(
                    f"The {failed_stage} step failed. Retry that checkpoint before "
                    "starting or advancing the design pipeline."
                )
            context = payload.get("context") or {}
            target_feedbacks = self._sequence_target_feedbacks(context)
            if target_feedbacks:
                # Every entry has an explicit UC and its own instruction.  The
                # batch API keeps all revisions in memory until all of them
                # succeed, so this command cannot persist a half-applied set.
                revised = _json_response(
                    revise_design_elements(
                        app_id,
                        BatchReviseRequest(revisions=target_feedbacks),
                    )
                )
                return {
                    "awaiting_input": bool(status.get("active")),
                    "kind": "action_required",
                    "message": (
                        f"Revised {len(target_feedbacks)} selected use-case diagrams and "
                        "only their trace-linked artifacts. Review the result or continue."
                    ),
                    "current_stage": status.get("stage") or "design",
                    "changed": revised.get("changed") or [],
                    "touched": revised.get("touched") or {},
                    "related": revised.get("related") or {},
                    "design": revised,
                }
            element_ref = str(context.get("element_ref") or "").strip()
            if text and element_ref:
                # A UI-selected element is an explicit local-edit request, not
                # ordinary stage feedback.  In particular, sequence feedback
                # must carry ``sequence_diagram:UCn`` so we never rewind and
                # regenerate every use-case card just to revise one of them.
                revised = _json_response(
                    revise_design_element(
                        app_id,
                        ReviseRequest(target=element_ref, feedback=text),
                    )
                )
                return {
                    "awaiting_input": bool(status.get("active")),
                    "kind": "action_required",
                    "message": (
                        f"Revised the selected {element_ref} and only its "
                        "trace-linked artifacts. Review the result or continue."
                    ),
                    "current_stage": status.get("stage") or "design",
                    "changed": revised.get("changed") or [],
                    "touched": revised.get("touched") or {},
                    "related": revised.get("related") or [],
                    "design": revised,
                }
            current_stage = str(status.get("stage") or "")
            if (
                text
                and command.get("action") == "message"
                and current_stage == "sequence_diagram"
            ):
                raise ValueError(
                    "Select one or more use-case targets and provide feedback for each target."
                )
            if status.get("active"):
                if text:
                    operation_stage = current_stage
                    verb = "Revising"
                else:
                    index = DESIGN_STAGES.index(current_stage)
                    operation_stage = (
                        DESIGN_STAGES[index + 1]
                        if index + 1 < len(DESIGN_STAGES)
                        else "design_complete"
                    )
                    verb = (
                        "Generating"
                        if operation_stage != "design_complete"
                        else "Completing"
                    )

                def operation():
                    return resume_design_session(
                        app_id, FeedbackRequest(feedback=text)
                    )
            else:
                operation_stage = DESIGN_STAGES[0]
                verb = "Generating"
                def operation():
                    return start_design_session(app_id, StageRequest())
            response = self._run_design_operation(
                command,
                stage=operation_stage,
                label=self._design_stage_label(operation_stage, verb),
                operation=operation,
            )
            return self._design_result(_json_response(response))

        if stage != "implementation":
            raise ValueError("The current stage cannot process a conversational command.")
        if not text:
            raise ValueError("Enter implementation feedback.")
        job = create_feedback_job(
            app_id,
            CreateImplementationFeedbackJobRequest(
                feedback=text,
                base_package=str(payload.get("base_package") or "com.easydep.app"),
                allow_assumptions=bool(payload.get("allow_assumptions", True)),
            ),
        )
        return self._monitor_implementation(job)

    @staticmethod
    def _design_stage_label(stage: str, verb: str) -> str:
        label = {
            "class_diagram": "class diagram",
            "sequence_diagram": "sequence diagram",
            "api_spec": "API specification",
            "erd": "ERD",
            "deployment_diagram": "deployment diagram",
            "design_complete": "design review",
        }.get(stage, stage.replace("_", " "))
        return f"{verb} the {label}"

    @staticmethod
    def _run_design_operation(
        command: dict[str, Any],
        *,
        stage: str,
        label: str,
        operation,
    ) -> JSONResponse:
        app_id = str(command["app_id"])
        command_id = str(command["command_id"])
        started = time.perf_counter()

        def record(status: str, detail: str) -> None:
            repository.append_event(
                app_id,
                command_id=command_id,
                stage="design",
                kind="progress",
                actor="system",
                text=label,
                metadata={
                    "progress_event": "designStageProgress",
                    "analysis_step": stage,
                    "current_stage": stage,
                    "design": {"stage": stage},
                    "progress_step_label": label,
                    "progress_detail": detail,
                    "progress_status": status,
                    "progress_card_label": "Design generation",
                },
            )

        record("running", "Started")
        try:
            response = operation()
        except Exception as error:
            elapsed = time.perf_counter() - started
            record(
                "failed",
                f"Failed after {elapsed:.1f}s: {WorkspaceService._error_text(error)}",
            )
            raise
        elapsed = time.perf_counter() - started
        payload = _json_response(response) if isinstance(response, JSONResponse) else {}
        validation = payload.get("validation") or {}
        stage_validation = validation.get(stage) or {}
        findings = [
            *list(stage_validation.get("errors") or []),
            *list(stage_validation.get("findings") or []),
        ]
        if findings:
            record(
                "needs_review",
                f"Draft generated in {elapsed:.1f}s; {len(findings)} findings require revision",
            )
        else:
            record("completed", f"Completed in {elapsed:.1f}s")
        return response

    @staticmethod
    def _requirements_progress_reporter(app_id: str, command_id: str):
        operation_counts: dict[str, int] = {}
        active_spec_tasks: dict[str, dict[str, str]] = {}
        active_analysis_steps: set[str] = set()
        progress_lock = Lock()
        current_step: str | None = None

        step_labels = {
            "intake": "Reading the submitted requirements",
            "clarify": "Refining ambiguous or compound requirements",
            "classify": "Classifying functional and non-functional requirements",
            "analyze_cloud_inputs": "Analyzing deployment and cloud inputs",
            "derive_deployment_needs": "Deriving deployment capabilities from requirement evidence",
            "extract_resource_constraints": "Reading additional cloud constraints",
            "build_resource_spec": "Structuring cloud and resource constraints",
            "identify_actors": "Identifying external actors",
            "identify_use_cases": "Identifying user-goal use cases",
            "review_model": "Reviewing the use-case model",
            "check_coverage": "Checking requirement coverage",
            "generate_specs": "Writing use-case specifications",
            "check_specs": "Checking use-case specifications",
            "identify_relationships": "Identifying use-case relationships",
            "check_relationships": "Checking use-case relationships",
            "render_diagram": "Rendering the use-case diagram",
        }
        operation_labels = {
            "structured:ClarifyOnlyResult": "AI requirement refinement",
            "structured:DeploymentNeedsResult": "AI deployment-capability sample",
            "structured:CloudConstraintExtraction": "AI cloud-constraint extraction",
            "resource_agent": "resource-constraint agent response",
            "structured:ActorResult": "AI actor identification",
            "structured:UseCaseResult": "AI use-case modeling",
            "structured:UseCaseSpec": "AI use-case specification",
            "structured:RelationshipModel": "AI relationship modeling",
        }
        operation_steps = {
            "structured:DeploymentNeedsResult": "derive_deployment_needs",
            "structured:CloudConstraintExtraction": "extract_resource_constraints",
            "resource_agent": "extract_resource_constraints",
        }

        def report(event: str, fields: dict[str, Any]) -> None:
            nonlocal current_step
            with progress_lock:
                event_step = str(fields.get("step") or "")
                if event == "analysisStepStarted" and event_step:
                    active_analysis_steps.add(event_step)
                    current_step = event_step
                elif event == "analysisStepFinished" and event_step:
                    active_analysis_steps.discard(event_step)
                use_case_id = str(fields.get("useCaseId") or "")
                if event == "specTaskStarted" and use_case_id:
                    active_spec_tasks[use_case_id] = {
                        "id": use_case_id,
                        "name": str(fields.get("useCaseName") or use_case_id),
                    }
                elif event == "specTaskFinished" and use_case_id:
                    active_spec_tasks.pop(use_case_id, None)
                active_snapshot = list(active_spec_tasks.values())
                analysis_snapshot = sorted(active_analysis_steps)

            metadata = {
                "progress_event": event,
                **fields,
                "active_spec_tasks": active_snapshot,
                "active_analysis_steps": analysis_snapshot,
            }
            if event == "analysisStepStarted":
                step_label = step_labels.get(
                    str(current_step), "Running an analysis step"
                )
                text = step_label
                metadata.update({
                    "analysis_step": current_step,
                    "progress_step_label": step_label,
                    "progress_detail": "Started",
                    "progress_status": "running",
                })
            elif event == "analysisStepFinished":
                finished_step = str(fields.get("step") or current_step or "")
                label = step_labels.get(finished_step, "Analysis step")
                elapsed = float(fields.get("elapsedSeconds") or 0)
                text = f"{label} completed in {elapsed:.1f}s"
                metadata.update({
                    "analysis_step": finished_step,
                    "progress_step_label": label,
                    "progress_detail": f"Completed in {elapsed:.1f}s",
                    "progress_status": str(fields.get("status") or "completed"),
                })
            elif event == "llmOperationStarted":
                operation = str(fields.get("operation") or "")
                with progress_lock:
                    operation_counts[operation] = operation_counts.get(operation, 0) + 1
                    operation_count = operation_counts[operation]
                if operation == "structured:DeploymentNeedsResult":
                    total = max(1, int(requirements_settings.capability_samples))
                    suffix = (
                        f" (sample {operation_count} of {total})"
                        if total > 1
                        else ""
                    )
                else:
                    suffix = (
                        f" (call {operation_count})"
                        if operation_count > 1
                        else ""
                    )
                label = operation_labels.get(operation, "AI model response")
                text = f"Waiting for {label}{suffix}"
                operation_step = operation_steps.get(operation) or current_step
                step_label = step_labels.get(
                    str(operation_step), "Running requirement analysis"
                )
                metadata.update({
                    "analysis_step": operation_step,
                    "progress_step_label": step_label,
                    "progress_detail": f"Waiting for {label}{suffix}",
                    "progress_status": "running",
                })
            elif event == "llmOperationFinished":
                operation = str(fields.get("operation") or "")
                elapsed = float(fields.get("elapsedSeconds") or 0)
                status = str(fields.get("status") or "completed")
                label = operation_labels.get(operation, "AI model response")
                text = f"{label} {status} in {elapsed:.1f}s"
                operation_step = operation_steps.get(operation) or current_step
                step_label = step_labels.get(
                    str(operation_step), "Running requirement analysis"
                )
                metadata.update({
                    "analysis_step": operation_step,
                    "progress_step_label": step_label,
                    "progress_detail": f"{label} {status} in {elapsed:.1f}s",
                    "progress_status": "running" if status == "completed" else status,
                })
            elif event in {"specTaskStarted", "specTaskFinished"}:
                name = str(fields.get("useCaseName") or fields.get("useCaseId") or "")
                text = f"Writing the use-case specification: {name}"
                metadata.update({
                    "analysis_step": current_step,
                    "progress_step_label": step_labels["generate_specs"],
                    "progress_detail": "Generating specifications in parallel",
                    "progress_status": "running",
                })
            else:
                return
            repository.append_event(
                app_id,
                command_id=command_id,
                stage="requirements",
                kind="progress",
                actor="system",
                text=text,
                metadata=metadata,
            )

        return report

    def _requirements_result(self, result: dict[str, Any]) -> dict[str, Any]:
        status = result.get("status")
        if status == "need_clarification":
            questions = result.get("questions") or []
            return {
                "awaiting_input": True,
                "kind": "question",
                "message": "\n".join(str(item) for item in questions),
                "questions": questions,
                "phase": result.get("phase"),
            }
        if status == "need_feedback":
            phase = str(result.get("phase") or "requirements")
            requirements = list(result.get("requirements") or [])
            functional_count = sum(
                1 for item in requirements if item.get("type") == "FR"
            )
            non_functional_count = sum(
                1 for item in requirements if item.get("type") == "NFR"
            )
            requirement_label = (
                "requirement" if len(requirements) == 1 else "requirements"
            )
            lead = {
                "requirements": (
                    f"I refined and classified {len(requirements)} {requirement_label} "
                    f"({functional_count} functional and "
                    f"{non_functional_count} non-functional)."
                ),
                "use_cases": (
                    f"I identified {len(result.get('use_cases') or [])} "
                    "user-goal use cases."
                ),
                "specs": (
                    f"I wrote and checked {len(result.get('use_case_specs') or [])} "
                    "use-case specifications."
                ),
                "relationships": "I completed the use-case relationships and diagram.",
            }.get(phase, "I completed this requirements-analysis step.")
            review_artifacts = [
                label
                for key, label in (
                    ("requirements", "Refined requirements"),
                    ("use_cases", "Use cases"),
                    ("use_case_specs", "Use-case specifications"),
                    ("diagram", "Use-case diagram"),
                )
                if result.get(key)
            ]
            resource_questions = list(result.get("resource_questions") or [])
            resource_question = next(
                (
                    question
                    for question in resource_questions
                    if question.get("kind") != "suggested"
                ),
                resource_questions[0] if resource_questions else None,
            )
            if resource_question:
                field = str(resource_question.get("field") or "")
                question = str(
                    resource_question.get("question")
                    or "Please provide the missing deployment information."
                )
                if field == "provider":
                    message = f"{lead} Waiting for deployment details."
                else:
                    message = f"{lead} {question}"
                return {
                    "awaiting_input": True,
                    "kind": "question",
                    "message": message,
                    "phase": result.get("phase"),
                    "resource_question": resource_question,
                    "resource_questions": resource_questions,
                    "review_artifacts": review_artifacts,
                }
            listed = ", ".join(review_artifacts) or "the available requirements artifacts"
            return {
                "awaiting_input": True,
                "kind": "action_required",
                "message": (
                    f"{lead} Review: {listed}. "
                    "Send revision feedback, "
                    "or continue to the next analysis stage."
                ),
                "phase": result.get("phase"),
                "summary": result.get("feedback_summary"),
                "review_artifacts": review_artifacts,
            }
        return {
            "message": "Requirements analysis completed.",
            "saved_stages": result.get("saved_stages") or [],
            "phase": result.get("phase"),
        }

    def _design_result(self, result: dict[str, Any]) -> dict[str, Any]:
        session = result.get("session") or {}
        # The public design API reports completion as ``status: completed``.
        # Keep legacy flags only for previously stored response shapes.
        finished = bool(
            result.get("status") == "completed"
            or result.get("finished")
            or session.get("finished")
        )
        if finished:
            return {"message": "Design artifact generation completed.", "design": result}
        stage = (
            session.get("current_stage")
            or session.get("stage")
            or result.get("current_stage")
            or result.get("stage")
        )
        stage_validation = (result.get("validation") or {}).get(stage) or {}
        findings = [
            *list(stage_validation.get("errors") or []),
            *list(stage_validation.get("findings") or []),
        ]
        method_proposals = list(stage_validation.get("method_proposals") or [])
        artifact = (result.get("artifacts") or {}).get(stage)
        if artifact is None:
            config = artifact_repository.STAGE_ARTIFACTS.get(str(stage), {})
            artifact = result.get(config.get("state_key", ""))
        # The design response can omit derived artifacts when it crosses a
        # checkpoint, even though persist_* has already stored the model. Use
        # the repository as the final source of truth so a generated draft is
        # never presented as a blocking, artifact-less failure.
        if not artifact:
            app_id = str(result.get("app_id") or "").strip()
            if app_id:
                try:
                    persisted = artifact_repository.load_state(app_id)
                    config = artifact_repository.STAGE_ARTIFACTS.get(str(stage), {})
                    artifact = persisted.get(config.get("state_key", ""))
                except Exception:  # noqa: BLE001 - response shaping must not fail
                    artifact = None
        artifact_exists = bool(artifact.strip()) if isinstance(artifact, str) else bool(artifact)
        requires_revision = bool(findings) and not artifact_exists
        return {
            "awaiting_input": True,
            "kind": "action_required",
            "message": (
                f"The {str(stage or 'design').replace('_', ' ')} draft has "
                f"{len(findings)} findings. The generated artifact can still be reviewed "
                "and continued to the next stage."
                if findings and artifact_exists
                else f"The {str(stage or 'design').replace('_', ' ')} draft has "
                f"{len(findings)} findings. Review the draft and send revision feedback "
                "before continuing."
                if requires_revision
                else "Review the current design artifacts, then send revision feedback "
                "or continue to the next stage."
            ),
            "current_stage": stage,
            "requires_revision": requires_revision,
            "blocking_findings": findings if requires_revision else [],
            "findings": findings,
            # Keep the pending approval decision on the workspace command as
            # well as in the artifact payload so the UI can offer an explicit
            # approval action instead of requiring a magic text phrase.
            "method_proposals": method_proposals,
            "design": result,
        }

    def _confirm_change(self, command: dict[str, Any]) -> dict[str, Any]:
        action_id = str(command["payload"].get("action_id") or "")
        original = repository.get_command(action_id)
        if original is None or original["status"] != "AWAITING_INPUT":
            raise ValueError("The change request is missing or was already handled.")
        context = original["payload"].get("context") or {}
        feedback = str(original["payload"].get("text") or "").strip()
        app_id = str(command["app_id"])
        target_feedbacks = self._sequence_target_feedbacks(context)
        if target_feedbacks:
            result = revise_design_elements(
                app_id, BatchReviseRequest(revisions=target_feedbacks)
            )
            return {
                "message": "Revised the selected use-case diagrams and trace-linked artifacts.",
                "design": _json_response(result),
            }
        element_ref = context.get("element_ref")
        if element_ref:
            result = revise_design_element(
                app_id, ReviseRequest(target=str(element_ref), feedback=feedback)
            )
            return {
                "message": "Revised the selected design element and affected downstream artifacts.",
                "design": _json_response(result),
            }
        stage = str(context.get("artifact_stage") or context.get("stage") or "")
        if stage not in {
            "sequence_diagram",
            "api_spec",
            "erd",
            "deployment_diagram",
        }:
            raise ValueError(
                "Only a traceable design element or design stage can currently be rewound."
            )
        rewind_design_session(app_id, RewindRequest(stage=stage))
        result = resume_design_session(app_id, FeedbackRequest(feedback=feedback))
        return {
            "message": "Returned to the selected design stage and applied the feedback.",
            "design": _json_response(result),
        }

    @staticmethod
    def _implementation_progress_snapshot(job: dict[str, Any]) -> dict[str, Any]:
        """Build user-facing implementation milestones from durable checkpoints.

        ``generation-progress.json`` covers the deterministic generator, while
        ``workflow-state.json`` is checkpointed before and after every agent
        task.  Reading both lets the chat show useful progress without exposing
        the implementation run directory to the browser.
        """
        job_id = str(job.get("job_id") or "")
        private_job = job
        if job_id:
            try:
                private_job = implementation_worker._read(job_id)
            except Exception:  # Progress reporting must not interrupt a job.
                private_job = job

        run_root = str(
            private_job.get("run_root") or job.get("run_root") or ""
        ).strip()
        workflow = private_job.get("workflow")
        run_path = Path(run_root) if run_root else None
        if run_path is not None:
            state_path = run_path / "reports" / "workflow-state.json"
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = None
            if isinstance(state, dict):
                workflow = state

        updates: list[dict[str, str]] = []

        def add_update(
            step: str, label: str, status: str, detail: str = ""
        ) -> None:
            updates.append(
                {
                    "step": step,
                    "label": label,
                    "status": status,
                    "detail": detail,
                }
            )

        job_status = str(job.get("status") or private_job.get("status") or "")
        live_progress = job.get("progress")
        progress_status = (
            str(live_progress.get("status") or "")
            if isinstance(live_progress, dict)
            else ""
        )
        progress_message = (
            str(live_progress.get("message") or "")
            if isinstance(live_progress, dict)
            else ""
        )
        generation_status = (
            "PLANNING"
            if job_status == "PLANNING"
            else progress_status or job_status
        )

        if generation_status == "QUEUED":
            add_update("prepare-job", "구현 작업 준비", "running", "작업을 시작하고 있습니다.")
        else:
            # The job leaves the queue before its first generator checkpoint.
            # Explicitly close this UI-only milestone so it cannot look like a
            # long-running task while source generation or compilation proceeds.
            add_update("prepare-job", "구현 작업 준비", "completed")

        if generation_status in {
            "VALIDATING_INPUT",
            "GENERATING_SOURCES",
            "PREPARING_BUILD",
            "VERIFYING",
            "PLANNING",
            "SUCCEEDED",
        }:
            status_to_index = {
                "VALIDATING_INPUT": 0,
                "GENERATING_SOURCES": 1,
                "PREPARING_BUILD": 2,
                "VERIFYING": 3,
                "PLANNING": 4,
                "SUCCEEDED": len(_IMPLEMENTATION_GENERATION_STEPS),
            }
            active_index = status_to_index[generation_status]
            for index, (step, label) in enumerate(_IMPLEMENTATION_GENERATION_STEPS):
                if index < active_index:
                    add_update(step, label, "completed")
                elif index == active_index and generation_status != "SUCCEEDED":
                    add_update(step, label, "running", progress_message)
        elif generation_status == "REUSING_GENERATED_RUN":
            add_update("validate-input", "입력 및 설계 검증", "completed")
            add_update(
                "reuse-generated-run",
                "기존 생성 결과 재사용",
                "running",
                progress_message,
            )
        elif generation_status == "PREPARING_FEEDBACK":
            add_update("validate-input", "입력 및 설계 검증", "completed")
            add_update("prepare-feedback", "피드백 적용 준비", "running", progress_message)

        workflow_complete = False
        if isinstance(workflow, dict):
            workflow_status = str(workflow.get("status") or "")
            current_phase = str(workflow.get("currentPhase") or "")
            tasks = [item for item in workflow.get("tasks", []) if isinstance(item, dict)]
            phase_statuses = {
                str(phase.get("phaseId") or ""): str(phase.get("status") or "")
                for phase in workflow.get("phases", [])
                if isinstance(phase, dict)
            }
            for display_id, label, phase_ids in _IMPLEMENTATION_DISPLAY_PHASES:
                display_phases = [
                    phase_id
                    for phase_id, _phase_label in _IMPLEMENTATION_WORKFLOW_PHASES
                    if phase_id in phase_ids
                ]
                display_tasks = [
                    task
                    for task in tasks
                    if str(task.get("phase") or "") in phase_ids
                ]
                task_statuses = {
                    str(task.get("status") or "") for task in display_tasks
                }
                all_succeeded = all(
                    phase_statuses.get(phase_id) == "SUCCEEDED"
                    or (
                        any(
                            str(task.get("phase") or "") == phase_id
                            for task in display_tasks
                        )
                        and all(
                            str(task.get("status") or "") == "SUCCEEDED"
                            for task in display_tasks
                            if str(task.get("phase") or "") == phase_id
                        )
                    )
                    for phase_id in display_phases
                )
                if all_succeeded:
                    add_update(f"phase-{display_id}", label, "completed")
                elif "FAILED" in task_statuses or any(
                    phase_statuses.get(phase_id) == "FAILED"
                    for phase_id in display_phases
                ):
                    add_update(f"phase-{display_id}", label, "failed")
                elif (
                    workflow_status == "RUNNING"
                    and current_phase in phase_ids
                ) or "RUNNING" in task_statuses:
                    add_update(
                        f"phase-{display_id}",
                        label,
                        "running",
                        f"{label}을 진행하고 있습니다.",
                    )

            workflow_complete = workflow_status == "COMPLETE" or (
                workflow_status == "READY"
                and implementation_worker._workflow_is_complete(workflow)
            )
            activity = workflow.get("currentActivity")
            if (
                not workflow_complete
                and isinstance(activity, dict)
                and str(activity.get("id") or "")
            ):
                activity_status = str(activity.get("status") or "running").lower()
                if activity_status == "succeeded":
                    activity_status = "completed"
                activity_id = str(activity["id"])
                activity_phase = activity_id.removeprefix("verify-").removeprefix("audit-")
                display_id, display_label, _ = next(
                    (
                        item
                        for item in _IMPLEMENTATION_DISPLAY_PHASES
                        if activity_phase in item[2]
                    ),
                    ("implementation", "Backend 구현", frozenset()),
                )
                activity_prefix = "빌드 및 Unit Test" if activity_id.startswith("verify-") else "구현 결과 확인"
                add_update(
                    "activity-" + display_id,
                    f"{display_label} {activity_prefix}",
                    activity_status,
                    str(activity.get("detail") or ""),
                )
            elif workflow_complete:
                add_update("release-verification", "최종 릴리스 검증", "completed")

        current_file: str | None = None
        if workflow_complete or job_status in TERMINAL_JOB_STATUSES:
            # Agent event journals retain the last edited file after the run
            # drains. Do not append a synthetic running step that can mask the
            # completed implementation message in the progress card.
            run_path = None
        if run_path is not None:
            events_dir = run_path / "reports" / "agent-executions"
            latest_path: Path | None = None
            for candidate in sorted(events_dir.glob("*.events.jsonl")):
                try:
                    if (
                        latest_path is None
                        or candidate.stat().st_mtime >= latest_path.stat().st_mtime
                    ):
                        latest_path = candidate
                except OSError:
                    continue
            if latest_path is not None:
                try:
                    lines = latest_path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event = payload.get("event") if isinstance(payload, dict) else None
                    tool_name = (
                        str(payload.get("tool") or "")
                        if isinstance(payload, dict)
                        else ""
                    )
                    if not isinstance(event, dict):
                        continue
                    path_value = (
                        event.get("path")
                        or event.get("file_path")
                        or event.get("filePath")
                    )
                    if not isinstance(path_value, str) or not path_value.strip():
                        continue
                    if (
                        "file_editor" not in tool_name
                        and tool_name not in {"restricted_file_editor", "file_editor"}
                    ):
                        continue
                    current_file = path_value.strip()

        if current_file:
            file_name = Path(current_file).name
            add_update(
                "implementation-file",
                "현재 구현 파일",
                "running",
                f"Editing {file_name}",
            )

        if not updates:
            return {}
        latest = updates[-1]
        snapshot: dict[str, Any] = {
            "updates": updates,
            "progress_card_label": "구현 진행 상황",
            "text": latest["detail"] or latest["label"],
            "progress_detail": latest["detail"] or latest["label"],
            "progress_status": latest["status"],
        }
        if current_file:
            file_name = Path(current_file).name
            snapshot["current_file"] = current_file
            snapshot["current_class"] = Path(file_name).stem
        return snapshot

    def _monitor_implementation(self, job: dict[str, Any], *, command_id: str | None = None) -> dict[str, Any]:
        job_id = str(job["job_id"])
        app_id = str(job.get("app_id") or "")
        last_status: str | None = None
        last_progress: dict[str, str] = {}
        while True:
            current = implementation_worker.get(job_id)
            status = str(current.get("status") or "")
            if app_id and command_id:
                if status and status != last_status and status not in {"AWAITING_APPROVAL"}:
                    repository.append_event(
                        app_id,
                        command_id=command_id,
                        stage="implementation",
                        kind="status",
                        actor="system",
                        text=f"Implementation job status: {status}.",
                        metadata={"status": status, "job_id": job_id},
                    )
                    last_status = status
                progress = self._implementation_progress_snapshot(current)
                for update in progress.get("updates", []) if progress else []:
                    if not isinstance(update, dict):
                        continue
                    step = str(update.get("step") or "")
                    if not step:
                        continue
                    progress_key = "|".join(
                        str(update.get(field) or "")
                        for field in ("status", "label", "detail")
                    )
                    if last_progress.get(step) == progress_key:
                        continue
                    repository.append_event(
                        app_id,
                        command_id=command_id,
                        stage="implementation",
                        kind="progress",
                        actor="system",
                        text=str(update.get("detail") or update.get("label") or "구현을 진행하고 있습니다."),
                        metadata={
                            "progress_event": "implementationStepUpdated",
                            "step": step,
                            "progress_step_label": str(update.get("label") or step),
                            "progress_card_label": str(
                                progress.get("progress_card_label") or "구현 진행 상황"
                            ),
                            "progress_detail": str(update.get("detail") or ""),
                            "progress_status": str(update.get("status") or "running"),
                            **{
                                key: progress[key]
                                for key in ("current_file", "current_class")
                                if isinstance(progress.get(key), str)
                            },
                        },
                    )
                    last_progress[step] = progress_key
            if status == "AWAITING_APPROVAL":
                request = current.get("transmission_request") or {}
                return {
                    "awaiting_input": True,
                    "kind": "action_required",
                    "message": "Implementation execution requires approval.",
                    "job_id": job_id,
                    "request_id": request.get("requestId"),
                    "tasks": request.get("tasks") or [],
                }
            if status in TERMINAL_JOB_STATUSES:
                if status != "COMPLETED":
                    raise RuntimeError(str(current.get("error") or f"Implementation job {status}"))
                return {
                    "message": "Review the generated implementation artifacts below.",
                    "job_id": job_id,
                    "job": current,
                    "review_artifacts": True,
                }
            time.sleep(1)

    def _monitor_testing(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = str(job["job_id"])
        while True:
            current = get_testing_job(job_id)
            status = str(current.get("status") or "")
            if status in {"COMPLETED", "FAILED"}:
                if status == "FAILED":
                    raise RuntimeError(
                        str(current.get("error") or "The testing job failed.")
                    )
                return {
                    "message": "Testing completed.",
                    "job_id": job_id,
                    "job": current,
                }
            time.sleep(1)

    @staticmethod
    def _error_text(error: Exception) -> str:
        if isinstance(error, HTTPException):
            detail = error.detail
            if isinstance(detail, dict):
                return str(detail.get("message") or detail)
            return str(detail)
        return str(error) or type(error).__name__

    @staticmethod
    def _stage_label(stage: str) -> str:
        return {
            "requirements": "requirements analysis",
            "design": "system design",
            "implementation": "system implementation",
            "testing": "system testing",
        }.get(stage, stage)


workspace_service = WorkspaceService()
