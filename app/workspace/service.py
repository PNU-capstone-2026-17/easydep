from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.design.api import (
    FeedbackRequest,
    ReviseRequest,
    RewindRequest,
    StageRequest,
    resume_design_session,
    retry_design_session,
    revise_design_element,
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
        command_id = str(uuid.uuid4())
        with self._submission_lock:
            command = repository.create_command(
                command_id, app_id, action, resolved_stage, payload
            )
        text = str(payload.get("text") or "").strip()
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

        context = payload.get("context") or {}
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
            or not fields.intersection({"provider", "region", "monthlyBudgetUSD"})
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
            if prior["status"] != "FAILED" or prior["stage"] != "design":
                raise ValueError("Only a failed design command can be retried.")
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
        if action == "start_implementation":
            request = CreateImplementationJobRequest(
                base_package=str(
                    command["payload"].get("base_package") or "com.easydep.app"
                ),
                allow_assumptions=bool(
                    command["payload"].get("allow_assumptions", True)
                ),
            )
            job = create_job(str(command["app_id"]), request)
            return self._monitor_implementation(job)
        if action in {"approve_implementation", "reject_implementation"}:
            payload = command["payload"]
            job = approve_job(
                str(payload["job_id"]),
                ApprovalRequest(
                    request_id=str(payload["request_id"]),
                    approved=action == "approve_implementation",
                    approved_by="EasyDep Workspace",
                    retry_failed=bool(payload.get("retry_failed", False)),
                    delegate_repair_approvals=bool(
                        payload.get("delegate_repair_approvals", True)
                    ),
                ),
            )
            return self._monitor_implementation(job)
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
            current_stage = str(status.get("stage") or "")
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
                return {
                    "awaiting_input": True,
                    "kind": "question",
                    "message": (
                        f"{lead} Before I continue, I need one deployment detail: "
                        + str(
                            resource_question.get("question")
                            or "Please provide the missing deployment information."
                        )
                    ),
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
        blocking_findings = [
            *list(stage_validation.get("errors") or []),
            *list(stage_validation.get("findings") or []),
        ]
        requires_revision = bool(blocking_findings)
        return {
            "awaiting_input": True,
            "kind": "action_required",
            "message": (
                f"The {str(stage or 'design').replace('_', ' ')} draft has "
                f"{len(blocking_findings)} findings. Review the draft and send revision "
                "feedback before continuing."
                if requires_revision
                else "Review the current design artifacts, then send revision feedback "
                "or continue to the next stage."
            ),
            "current_stage": stage,
            "requires_revision": requires_revision,
            "blocking_findings": blocking_findings,
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

    def _monitor_implementation(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = str(job["job_id"])
        while True:
            current = implementation_worker.get(job_id)
            status = str(current.get("status") or "")
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
                    "message": "Implementation completed.",
                    "job_id": job_id,
                    "job": current,
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
