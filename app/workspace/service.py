from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, cast

from fastapi import HTTPException

from app.design import progress as design_progress
from app.design.graphs.design_graph import has_active_session, session_status
from app.design.graphs.subgraphs import DESIGN_STAGES
from app.design.observability import design_timing_context, log_design_timing
from app.design.service import (
    BatchReviseRequest,
    ReviseRequest,
    resume_design_session,
    retry_design_session,
    revise_design_element,
    revise_design_elements,
    rewind_design_session,
    select_deployment_target_session,
    start_design_session,
)
from app.design.services.common.plantuml import render_plantuml
from app.design.services.common.structured import capture_llm_timings
from app.implementation.application.jobs import (
    worker as implementation_worker,
)
from app.metrics import langsmith as langsmith_metrics
from app.repositories import artifact_repository
from app.requirements.config import settings as requirements_settings
from app.requirements.contracts.request import (
    AnalyzeRequest,
    CloudProvider,
    DeploymentPreferences,
    FeedbackEdit,
    FeedbackStage,
    InitialCloudConstraints,
)
from app.requirements.orchestration.service import (
    analyze_requirements,
    retry_requirements_analysis,
)
from app.requirements.resources.capability_contract import capability_resource_questions
from app.requirements.runtime import telemetry as requirements_telemetry
from app.testing.service import run_testing
from app.validation import stable_digest

from . import repository
from .actions import (
    StagePolicy,
    action_is_offered,
    action_spec,
    offered_actions,
    result_with_contract,
    validate_payload,
)
from .checkpoints import (
    checkpoint_options,
    create_checkpoint_branch,
    create_restart_branch,
)
from .contracts import RestartStage
from .conversation.agent import conversation_agent
from .conversation.context import build_conversation_context
from .conversation.contracts import Clarification, CommandIntent, ConversationIntent, Reply
from .conversation.project_tools import ProjectTools
from .live_preview import live_previews

_log = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "REJECTED",
    "NEEDS_INPUT",
    "NEEDS_PLANNER",
}

# 예전의 길이 제한 표본은 진단용 내부 값으로만 남긴다. 현재 개발 기본 설정은 실제 JSON
# 응답과 reasoning을 별도 ``responseContent``·``reasoningContent`` field로 기록하므로,
# Workspace event에서도 같은 실행의 원문을 확인할 수 있다.
_PRIVATE_DESIGN_TIMING_FIELDS = frozenset({"failureContentPrefix", "failureContentSuffix"})


def _public_design_timing_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """설계 timing 한 건을 Workspace event로 옮기고 예전 중복 표본만 제거한다."""
    return {key: value for key, value in event.items() if key not in _PRIVATE_DESIGN_TIMING_FIELDS}


def _implementation_agent_results(run_path: Path) -> list[dict[str, Any]]:
    """완료된 OpenHands 작업의 답변·수정 파일·검증·수리 이력을 읽는다."""

    execution_dir = run_path / "reports" / "agent-executions"
    results: list[dict[str, Any]] = []
    # ``*.attempt-NNN.result.json``은 이력 보관본이고 ``<task>.result.json``이 최신본이다.
    # 화면에는 작업별 최신본 하나만 보내 중복 표시를 피한다.
    for path in sorted(execution_dir.glob("*.result.json")):
        if ".attempt-" in path.name:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        results.append(
            {
                "task_id": str(payload.get("taskId") or path.name.removesuffix(".result.json")),
                "task_type": str(payload.get("taskType") or ""),
                "status": str(payload.get("status") or ""),
                "raw_response": str(payload.get("rawResponse") or ""),
                "changed_files": list(payload.get("changedFiles") or []),
                "verification": payload.get("verification")
                or payload.get("verificationEvidence")
                or {},
                "repair_history": payload.get("repairHistory") or {},
                "event_journal": str(payload.get("eventJournal") or ""),
            }
        )
    return results


def _resource_questions(
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """리소스 질문 목록과 화면에서 먼저 물을 질문을 함께 고른다."""
    questions = list(result.get("resource_questions") or [])
    selected = next(
        (question for question in questions if question.get("kind") != "suggested"),
        questions[0] if questions else None,
    )
    return questions, selected


def _with_capability_handoff_questions(app_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Backfill choice cards for checkpoints saved before capability UI support.

    Workspace command results are persisted snapshots. Merely deploying the new
    presentation code would otherwise leave an already-blocked application with
    the old empty question list forever. Enrich a copy from the canonical
    artifact state; never rewrite command history during a read.
    """

    if (
        result.get("phase") != "requirements_handoff"
        or result.get("resource_question")
        or result.get("resource_questions")
    ):
        return result
    blockers = result.get("blocking_findings") or []
    if not any(
        isinstance(blocker, dict) and blocker.get("code") == "requirements.capability-contract"
        for blocker in blockers
    ):
        return result
    state = artifact_repository.load_state(app_id)
    questions = capability_resource_questions(state.get("capability_contract") or {})
    if not questions:
        return result
    enriched = dict(result)
    enriched["resource_question"] = questions[0]
    enriched["resource_questions"] = questions
    enriched["message"] = (
        f"A deployment decision is required before design can start. {questions[0]['question']}"
    )
    return enriched


# The implementation worker has two distinct parts: initial deterministic
# generation and the resumable agent workflow.  Keep their user-facing labels
# here so the workspace can report the same stable milestones even when the
# underlying task plan differs by application.
_IMPLEMENTATION_GENERATION_STEPS = (
    ("validate-input", "Validate input and design"),
    ("generate-sources", "Generate base sources"),
    ("prepare-build", "Prepare build environment"),
    ("verify-generated", "Verify initial compilation"),
    ("plan-workflow", "Plan implementation workflow"),
)
_IMPLEMENTATION_WORKFLOW_PHASES = (
    ("persistence", "Persistence implementation"),
    ("use-cases", "Use-case backend implementation"),
    ("frontend", "Frontend implementation"),
    ("wiring", "Verify application wiring and HTTP flow"),
)
_IMPLEMENTATION_DISPLAY_PHASES = (
    (
        "backend",
        "Backend implementation",
        frozenset({"persistence", "use-cases"}),
    ),
    ("frontend", "Frontend implementation", frozenset({"frontend"})),
    ("e2e", "Verify application execution", frozenset({"wiring"})),
)


class WorkspaceService:
    """요구사항부터 테스트까지 한 대화형 명령 흐름으로 실행하는 서비스다."""

    def __init__(self) -> None:
        workers = max(1, min(4, int(os.getenv("EASYDEP_WORKSPACE_WORKERS", "2"))))
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="easydep-workspace"
        )
        self._submission_lock = Lock()

    def startup(self) -> int:
        interrupted = repository.interrupt_unfinished()
        # Testing command payload에 고정 입력과 마지막 검사 경계를 저장하므로 같은 command를
        # 다시 실행하면 해당 경계에서 이어 간다.
        for command in repository.interrupted_testing_commands():
            self._executor.submit(self._execute, str(command["command_id"]))
        return interrupted

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def reconcile_implementation_command(self, app_id: str) -> dict[str, Any] | None:
        """구현 작업은 끝났지만 Workspace 명령만 남은 경우 완료 상태를 맞춘다."""
        command = repository.latest_command(app_id)
        if not command or command.get("status") not in {
            "RUNNING",
            "INTERRUPTED",
            "FAILED",
        }:
            return command
        # 같은 ``retry_implementation`` 이름을 Testing의 자동 수리도 사용한다. 이 메서드는
        # 구현 화면을 새로 열었을 때 끊긴 구현 명령만 맞추는 용도이므로, Testing 명령을
        # 구현 작업 완료와 동시에 끝내면 안 된다. Testing은 이어서 동적 기능 검사를 해야 한다.
        if command.get("stage") != "implementation":
            return command
        if command.get("action") not in {
            "start_implementation",
            "retry_implementation",
            "rerun_implementation",
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
        # READY workflow의 완료 여부는 구현 작업 서비스가 판정하여 공개 상태를
        # COMPLETED로 바꾼다. Workspace가 그 내부 규칙을 다시 구현하지 않는다.
        if job_status != "COMPLETED":
            self._sync_implementation_progress(app_id, str(command["command_id"]), job)
            if job_status in {"FAILED", "NEEDS_PLANNER"}:
                result = {
                    **dict(command.get("result") or {}),
                    "job_id": job_id,
                    "job": job,
                    "checkpoint_retryable": bool(job.get("checkpoint_retryable")),
                }
                result = result_with_contract(
                    {**command, "status": "FAILED"}, result
                )
                return repository.update_command(
                    command["command_id"],
                    status="FAILED",
                    result=result,
                    error=str(job.get("error") or "Implementation needs checkpoint repair."),
                )
            return command
        result = {
            "message": "Review the generated implementation artifacts below.",
            "job_id": job_id,
            "job": job,
            "review_artifacts": True,
        }
        result = result_with_contract(
            {**command, "status": "COMPLETED"}, result
        )
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

    def _sync_implementation_progress(
        self, app_id: str, command_id: str, job: dict[str, Any]
    ) -> None:
        """재시작 뒤에도 저장된 job 상태를 Workspace 진행 이벤트로 복원한다."""
        previous_updates: dict[str, str] = {}
        for event in repository.list_events(app_id):
            if (
                event.get("command_id") != command_id
                or event.get("stage") != "implementation"
                or event.get("kind") != "progress"
            ):
                continue
            metadata = event.get("metadata") or {}
            step = str(metadata.get("step") or "")
            if step:
                previous_updates[step] = "|".join(
                    str(metadata.get(field) or "")
                    for field in (
                        "progress_status",
                        "progress_step_label",
                        "progress_detail",
                    )
                )
        progress = self._implementation_progress_snapshot(job)
        for update in progress.get("updates", []) if progress else []:
            if not isinstance(update, dict):
                continue
            step = str(update.get("step") or "")
            if not step:
                continue
            label = str(update.get("label") or step)
            detail = str(update.get("detail") or "")
            status = str(update.get("status") or "running")
            key = "|".join((status, label, detail))
            if previous_updates.get(step) == key:
                continue
            repository.append_event(
                app_id,
                command_id=command_id,
                stage="implementation",
                kind="progress",
                actor="system",
                text=detail or label,
                metadata={
                    "progress_event": "implementationStepUpdated",
                    "step": step,
                    "progress_step_label": label,
                    "progress_card_label": str(
                        progress.get("progress_card_label") or "Implementation progress"
                    ),
                    "progress_detail": detail,
                    "progress_status": status,
                },
            )

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
                raise TypeError("Each sequence feedback entry must name a target and feedback.")
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
        user_text = str(payload.get("text") or "").strip()
        action, payload, stage = self._prepare_conversational_message(
            app_id,
            action=action,
            payload=payload,
            stage=stage,
        )
        # 분기와 재실행은 원본 앱의 개발 상태를 바꾸지 않는다. 관리 명령이 최신 명령으로
        # 보이더라도 그전에 제공되던 진행 버튼을 그대로 유지한다.
        if action in {"branch_checkpoint", "rerun_from_stage"}:
            previous = repository.latest_command(app_id)
            if previous is not None:
                payload = {
                    **payload,
                    "_conversation_actions": [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in offered_actions(previous)
                    ],
                }
                stage = stage or str(previous.get("stage") or "requirements")
        resolved_stage = stage or self.infer_stage(app_id, action, payload)
        self._validate_payload(action, payload)
        self._validate_action_reference(app_id, action, payload)
        text = user_text or str(payload.get("text") or "").strip()
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
            command = repository.create_command(command_id, app_id, action, resolved_stage, payload)
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

        if (
            action == "message"
            and not payload.get("_conversation_outcome")
            and not payload.get("conversation_intent")
            and context.get("stage")
            and context.get("stage") != resolved_stage
        ):
            result = {
                "action_id": command_id,
                "action": "confirm_change",
                "context": context,
                "message": "This change may affect an earlier stage and its downstream artifacts.",
            }
            result = result_with_contract(
                {**command, "status": "AWAITING_INPUT"}, result
            )
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

    def _prepare_conversational_message(
        self,
        app_id: str,
        *,
        action: str,
        payload: dict[str, Any],
        stage: str | None,
    ) -> tuple[str, dict[str, Any], str | None]:
        """실행 command를 만들기 전에 자연어 발화를 해석한다.

        명시적 버튼, 리소스 답변과 UI에서 대상을 고른 수정은 LLM을 거치지 않는다. 자연어
        command는 backend가 이미 공개한 action과 payload로 바꾸고 답변과 clarification은
        전문 단계를 실행하지 않는 message command로 남긴다.
        """

        if action != "message":
            return action, payload, stage
        text = str(payload.get("text") or "").strip()
        latest = repository.latest_command(app_id)
        if not text or latest is None:
            return action, payload, stage
        if latest.get("status") in repository.ACTIVE_STATUSES:
            raise RuntimeError(
                f"An active workspace command already exists: {latest['command_id']}"
            )
        selected = payload.get("context") or {}
        if any(
            selected.get(key)
            for key in ("element_ref", "target_feedbacks", "validated_target_feedbacks")
        ):
            return action, payload, stage

        action_id = str(payload.get("action_id") or "")
        prior = repository.get_command(action_id) if action_id else latest
        prior_result = (prior or {}).get("result") or {}
        # 전문 단계가 낸 구체적인 질문의 답은 그 단계의 typed 재개 경로를 유지한다.
        # 대화형 clarification의 답만 저장된 문맥과 함께 대화형 에이전트로 돌려보낸다.
        conversation = prior_result.get("conversation")
        if (
            prior is not None
            and prior.get("status") == "AWAITING_INPUT"
            and not isinstance(conversation, dict)
            and (
                prior_result.get("kind") == "question"
                or prior_result.get("resource_question")
                or prior_result.get("questions")
            )
        ):
            return action, payload, str(prior.get("stage") or stage or "requirements")

        actionable = latest
        latest_conversation = (latest.get("result") or {}).get("conversation")
        if isinstance(latest_conversation, dict) and latest_conversation.get("clarification"):
            referenced = repository.get_command(
                str((latest.get("payload") or {}).get("action_id") or "")
            )
            if referenced is not None:
                actionable = referenced
        try:
            outcome = conversation_agent.respond(
                app_id,
                text,
                build_conversation_context(app_id),
                tools=ProjectTools(app_id),
            )
        except Exception:
            _log.exception("Failed to interpret a workspace conversation message")
            outcome = Clarification(
                question=(
                    "I could not interpret that message right now. "
                    "Please retry or use one of the available actions."
                )
            )
        if isinstance(outcome, Reply):
            return (
                "message",
                {
                    **payload,
                    "_conversation_actions": [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in offered_actions(actionable)
                    ],
                    "_conversation_outcome": {
                        "kind": "reply",
                        **outcome.model_dump(mode="json"),
                    },
                },
                stage or str(latest.get("stage") or "requirements"),
            )
        if isinstance(outcome, Clarification):
            return self._clarification_message(payload, outcome, stage, latest)
        return self._route_conversation_intent(app_id, payload, outcome, actionable)

    @staticmethod
    def _clarification_message(
        payload: dict[str, Any],
        outcome: Clarification,
        stage: str | None,
        latest: dict[str, Any],
    ) -> tuple[str, dict[str, Any], str | None]:
        return (
            "message",
            {
                **payload,
                "_conversation_outcome": {
                    "kind": "clarification",
                    **outcome.model_dump(mode="json"),
                },
            },
            stage or str(latest.get("stage") or "requirements"),
        )

    def _route_conversation_intent(
        self,
        app_id: str,
        payload: dict[str, Any],
        intent: CommandIntent,
        latest: dict[str, Any],
    ) -> tuple[str, dict[str, Any], str | None]:
        """자연어 의도를 공개 offer와 검증된 프로젝트 ref로 연결한다."""

        offered = offered_actions(latest)
        intent_name = str(intent.intent)
        if intent_name in {
            ConversationIntent.BRANCH.value,
            ConversationIntent.RERUN.value,
        }:
            option_kind = "branch" if intent_name == ConversationIntent.BRANCH.value else "rerun"
            choices = checkpoint_options(app_id)[option_kind]
            available = {
                str(item["stage"])
                for item in choices
                if item.get("available") is True
            }
            if intent.stage not in available:
                return self._clarification_message(
                    payload,
                    Clarification(
                        question="Choose a stage that has all required checkpoint artifacts.",
                        candidates=[
                            str(item["label"])
                            for item in choices
                            if item.get("available") is True
                        ],
                    ),
                    None,
                    latest,
                )
            field = "checkpoint_stage" if option_kind == "branch" else "restart_stage"
            action = "branch_checkpoint" if option_kind == "branch" else "rerun_from_stage"
            return (
                action,
                {
                    **payload,
                    field: intent.stage,
                    "conversation_intent": intent.model_dump(mode="json"),
                },
                None,
            )
        if intent_name == ConversationIntent.REVISE.value:
            tools = ProjectTools(app_id)
            validation = tools.validate_targets(intent.targets)
            targets = list(validation.get("targets") or [])
            valid_refs = list(validation.get("valid_refs") or [])
            owners = {str(item.get("owner") or "") for item in targets if item.get("valid")}
            if not validation.get("valid") or not valid_refs or len(owners) != 1:
                candidates = [str(item.get("canonical_ref") or item.get("ref") or "") for item in targets]
                return self._clarification_message(
                    payload,
                    Clarification(
                        question="Select editable targets owned by a single delivery stage.",
                        candidates=list(dict.fromkeys(item for item in candidates if item)),
                    ),
                    None,
                    latest,
                )
            owner = owners.pop()
            owner_command = repository.latest_command(app_id, stage=owner)
            routed_payload = {
                **payload,
                "text": intent.instruction,
                "action_id": str((owner_command or latest).get("command_id") or ""),
                "conversation_intent": intent.model_dump(mode="json"),
                "validated_targets": targets,
                "validated_impact": tools.trace_impact(valid_refs, view="editing"),
            }
            if owner == "design":
                routed_payload["context"] = {
                    "validated_target_feedbacks": [
                        {"target": ref, "feedback": intent.instruction}
                        for ref in valid_refs
                    ]
                }
            return "message", routed_payload, owner

        action_candidates = {
            ConversationIntent.ADVANCE.value: {
                "advance",
                "start_design",
                "start_implementation",
                "start_testing",
            },
            ConversationIntent.ANSWER.value: {"message"},
            ConversationIntent.DELEGATE_REPAIR.value: {"delegate_repair"},
        }.get(intent_name, set())
        offer = next(
            (item for item in offered if str(item.action) in action_candidates),
            None,
        )
        if offer is None:
            return self._clarification_message(
                payload,
                Clarification(
                    question=(
                        "That action is not available in the current state. "
                        "Choose one of the available actions."
                    ),
                    candidates=[str(item.label) for item in offered],
                ),
                None,
                latest,
            )
        routed_payload = {
            **payload,
            **dict(offer.payload),
            "conversation_intent": intent.model_dump(mode="json"),
        }
        if str(offer.action) == "message":
            routed_payload["text"] = intent.instruction
        return str(offer.action), routed_payload, None

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

    def present_command(self, app_id: str, command: dict[str, Any] | None) -> dict[str, Any] | None:
        """Return a display-ready command without mutating its stored snapshot."""

        if command is None:
            return None
        presented = dict(command)
        result = command.get("result")
        shaped_result = dict(result) if isinstance(result, dict) else {}
        shaped_result = _with_capability_handoff_questions(app_id, shaped_result)
        presented["result"] = result_with_contract(presented, shaped_result)
        return presented

    @staticmethod
    def _validate_payload(action: str, payload: dict[str, Any]) -> None:
        validate_payload(action, payload)

    def _validate_action_reference(self, app_id: str, action: str, payload: dict[str, Any]) -> None:
        action_id = str(payload.get("action_id") or "")
        if not action_id:
            return
        prior = repository.get_command(action_id)
        if prior is None or prior["app_id"] != app_id:
            raise ValueError("The command to answer could not be found.")
        # 저장된 배포 선택은 내부 재개 trigger다. 같은 질문에 답하지만 choice text 대신
        # 구조화된 값을 전달한다.
        if action == "apply_deployment_preferences":
            result = prior.get("result") or {}
            fields = {
                str(question.get("field") or "")
                for question in result.get("resource_questions") or []
                if isinstance(question, dict)
            }
            if prior["status"] != "AWAITING_INPUT" or not fields.intersection(
                {"provider", "region"}
            ):
                raise ValueError("Deployment preferences do not answer this command.")
            return
        if not action_is_offered(action, payload, prior):
            raise ValueError("This action is not currently offered for the referenced command.")

    def infer_stage(self, app_id: str, action: str, payload: dict[str, Any]) -> str:
        policy = action_spec(action).stage_policy
        if policy in {
            StagePolicy.REQUIREMENTS,
            StagePolicy.DESIGN,
            StagePolicy.IMPLEMENTATION,
            StagePolicy.TESTING,
        }:
            return policy.value
        if policy == StagePolicy.RETRY_IMPLEMENTATION:
            # Testing이 자동으로 만든 구현 수리도 같은 구현 checkpoint다. 이 경우 재개
            # 명령을 Testing 단계에 두면 구현 수리가 끝난 뒤 보존한 기능 계획을 바로 다시
            # 실행할 수 있고, 서버가 중간에 재시작돼도 아래 Testing checkpoint로 이어진다.
            prior = repository.get_command(str(payload.get("action_id") or ""))
            if (
                prior is not None
                and prior.get("action") == "delegate_repair"
                and prior.get("stage") == "testing"
            ):
                return "testing"
            return "implementation"
        if policy == StagePolicy.REFERENCE:
            prior = repository.get_command(str(payload.get("action_id") or ""))
            if prior is not None:
                return str(
                    (prior.get("result") or {}).get("routing_stage")
                    or prior.get("stage")
                    or "requirements"
                )
        latest = repository.latest_command(app_id)
        if action == "message" and payload.get("action_id"):
            prior = repository.get_command(str(payload["action_id"]))
            if prior is not None:
                return str(
                    (prior.get("result") or {}).get("routing_stage")
                    or prior.get("stage")
                    or "requirements"
                )
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
        action = str(command["action"])
        try:
            with langsmith_metrics.trace_scope(
                f"easydep.workspace.{stage}",
                metadata={
                    "thread_id": app_id,
                    "app_id": app_id,
                    "command_id": command_id,
                    "stage": stage,
                    "action": action,
                    "agent": stage,
                    "operation": "workspace_command",
                },
            ):
                self._execute_command(command_id, command)
        except Exception:
            # ``_execute_command`` has already stored the failure for the UI.
            # Letting the exception leave the trace scope marks the LangSmith
            # root run as failed; the background executor must not re-raise it.
            return

    def _execute_command(self, command_id: str, command: dict[str, Any]) -> None:
        """Execute a persisted command inside its Workspace LangSmith trace."""

        app_id = str(command["app_id"])
        stage = str(command["stage"])
        repository.update_command(
            command_id,
            status="RUNNING",
            started_at=repository.now(),
            completed_at=None,
            error=None,
        )
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
            awaiting_input = result.pop("awaiting_input", False) is True
            if awaiting_input:
                result = result_with_contract(
                    {**command, "status": "AWAITING_INPUT"}, result
                )
                routed_stage = str(result.get("routing_stage") or "")
                changes: dict[str, Any] = {
                    "status": "AWAITING_INPUT",
                    "result": result,
                }
                if routed_stage in {"requirements", "design", "implementation", "testing"}:
                    changes["stage"] = routed_stage
                    command["stage"] = routed_stage
                    stage = routed_stage
                repository.update_command(command_id, **changes)
                repository.append_event(
                    app_id,
                    command_id=command_id,
                    stage=stage,
                    kind=str(result.get("kind") or "action_required"),
                    actor="assistant",
                    text=str(result.get("message") or "User input is required."),
                    metadata=result,
                )
                # 의미 검사로 발견한 기술 결함은 사용자가 버튼을 눌러야만 고쳐지는
                # 질문이 아니다. 이미 시작된 수리뿐 아니라 Testing이 처음 발견한 제품·테스트
                # 결함도 ``delegate_repair``로 곧바로 이어 간다. 요구사항 선택·확인 질문과
                # 실행 환경 복구가 필요한 오류는 그대로 사용자에게 남긴다.
                if (
                    (
                        command.get("action") == "delegate_repair"
                        or stage == "testing"
                    )
                    and result.get("requires_revision") is True
                    and result.get("can_delegate_repair") is True
                    and not result.get("resource_question")
                    and not result.get("resource_questions")
                ):
                    repository.append_event(
                        app_id,
                        command_id=command_id,
                        stage=stage,
                        kind="status",
                        actor="system",
                        text="Continuing automatic repair with the accumulated history.",
                        metadata={"status": "AUTO_REPAIR_QUEUED"},
                    )
                    self.submit(
                        app_id,
                        action="delegate_repair",
                        stage=stage,
                        payload={"action_id": command_id},
                    )
                    return
                if stage == "requirements":
                    self.apply_saved_deployment_preferences(app_id)
                return
            result = result_with_contract({**command, "status": "COMPLETED"}, result)
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
                text=str(result.get("message") or f"Completed {self._stage_label(stage)}."),
                metadata={"status": "COMPLETED", **result},
            )
        except Exception as error:
            detail = self._error_text(error)
            latest = repository.get_command(command_id) or command
            failure_result = result_with_contract(
                {**latest, "status": "FAILED"},
                dict(latest.get("result") or {}),
            )
            repository.update_command(
                command_id,
                status="FAILED",
                result=failure_result,
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
            raise

    def _complete_referenced_action(self, command: dict[str, Any]) -> None:
        if command["payload"].get("_conversation_outcome"):
            return
        action_id = str(command["payload"].get("action_id") or "")
        if not action_id:
            return
        prior = repository.get_command(action_id)
        if prior is not None and prior["status"] == "AWAITING_INPUT":
            repository.update_command(action_id, status="COMPLETED", completed_at=repository.now())

    def _dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command["action"])
        handler = action_spec(action).handler
        conversation_outcome = command["payload"].get("_conversation_outcome")
        if action == "message" and isinstance(conversation_outcome, dict):
            kind = str(conversation_outcome.get("kind") or "")
            if kind == "reply":
                reply = Reply.model_validate(
                    {key: value for key, value in conversation_outcome.items() if key != "kind"}
                )
                return {
                    "kind": "reply",
                    "message": reply.text,
                    "conversation": {"reply": reply.model_dump(mode="json")},
                }
            if kind == "clarification":
                clarification = Clarification.model_validate(
                    {key: value for key, value in conversation_outcome.items() if key != "kind"}
                )
                return {
                    "awaiting_input": True,
                    "kind": "question",
                    "message": clarification.question,
                    "conversation": {
                        "clarification": clarification.model_dump(mode="json")
                    },
                }
            raise ValueError("Unknown conversation outcome.")
        # 파일 복원이나 검사 도중 서버가 재시작되었다면 구현 수리부터 반복하지 않는다.
        # 현재 command에 저장한 Testing 체크포인트를 그대로 실행 서비스에 돌려준다.
        checkpoint = command["payload"].get("testing_checkpoint")
        if command.get("stage") == "testing" and isinstance(checkpoint, dict):
            implementation_job_id = str(checkpoint.get("implementation_job_id") or "")
            if not implementation_job_id:
                raise ValueError("The Testing checkpoint has no implementation job ID.")
            return self._run_testing_command(command, implementation_job_id)
        if handler == "stage_message":
            return self._stage_message(command, advance=action in {"advance", "start_design"})
        if handler == "delegate_repair":
            action_id = str(command["payload"].get("action_id") or "")
            prior = repository.get_command(action_id) or {}
            result = prior.get("result") or {}
            blockers = result.get("blocking_findings") or []
            messages = [
                str(blocker.get("message") or "")
                for blocker in blockers
                if isinstance(blocker, dict) and blocker.get("repairable") is not False
            ]
            if not messages:
                raise ValueError("No LLM-repairable blocker is available.")
            if prior.get("stage") == "testing":
                previous_job = result.get("job") or {}
                implementation_job_id = str(
                    previous_job.get("implementation_job_id")
                    or prior.get("payload", {}).get("implementation_job_id")
                    or ""
                )
                previous_run_id = str(result.get("job_id") or previous_job.get("job_id") or "")
                if not implementation_job_id or not previous_run_id:
                    raise ValueError("The failing testing run cannot be resumed.")
                defect_classes = {
                    str(blocker.get("defect_class") or "SUT_DEFECT")
                    for blocker in blockers
                    if isinstance(blocker, dict)
                }
                if "ENVIRONMENT_DEFECT" in defect_classes:
                    return {
                        "awaiting_input": True,
                        "kind": "external_action",
                        "message": (
                            "Testing could not reach a conclusion because its runtime or "
                            "required tool is unavailable. Restore that environment and run "
                            "the same Testing job again; EasyDep will not change product code "
                            "to hide an environment failure."
                        ),
                        "requires_revision": False,
                        "can_delegate_repair": False,
                        "blocking_findings": blockers,
                        "repair_state": {
                            **dict(result.get("repair_state") or {}),
                            "status": "WAITING_EXTERNAL",
                        },
                        "job_id": previous_run_id,
                        "job": previous_job,
                    }
                if "UPSTREAM_AMBIGUITY" in defect_classes:
                    # 이 분류는 고정 요구사항과 OpenAPI 사이에 추적 가능한 endpoint가 없을
                    # 때만 나온다. 가장 가까운 생산자인 API 명세부터 다시 만들고, 이후
                    # 설계 단계는 기존 그래프 순서대로 이어서 진행한다.
                    revised = rewind_design_session(str(command["app_id"]), "api_spec")
                    shaped = self._design_result(revised)
                    shaped["routing_stage"] = "design"
                    shaped["message"] = (
                        "Testing found an ambiguous requirements-to-API mapping, so EasyDep "
                        "regenerated the API specification from the preserved sequence design."
                    )
                    return shaped
                if "SUT_DEFECT" in defect_classes:
                    # 동적 테스트까지 실행된 실패라면 그 계획을 그대로 보존한다. 반대로
                    # 앱 실행처럼 계획이 생기기 전에 실패한 경우에는 보존할
                    # 대상이 없으므로, 고친 구현을 새 Testing 작업으로 검사해야 한다.
                    has_preserved_candidate = any(
                        isinstance(blocker.get("candidate_plan"), dict)
                        and bool(blocker.get("candidate_plan"))
                        for blocker in blockers
                    )
                    original_implementation = implementation_worker.get(implementation_job_id)
                    # Testing 이력에는 HTTP 실패가 남고 최신 source에는 이전 수정 결과가
                    # 반영돼 있다. 다음 수리에는 자유형 agent 답변을 반복하지 않고, 이전
                    # 작업의 변경 파일만 알려 현재 source와 정확한 실패 증거를 읽게 한다.
                    previous_repair_results, older_repair_summaries = (
                        self._implementation_repair_outcomes(original_implementation)
                    )
                    feedback = self._testing_implementation_feedback(
                        result,
                        blockers,
                        previous_repair_results=previous_repair_results,
                        older_repair_summaries=older_repair_summaries,
                    )
                    confirmed_target_refs = list(dict.fromkeys(
                        str(target)
                        for blocker in blockers
                        if isinstance(blocker, dict)
                        for target in blocker.get("target_ids") or []
                        if isinstance(target, str) and target
                    ))
                    repair_job = implementation_worker.create_feedback_job(
                        str(command["app_id"]),
                        cast(
                            dict[str, Any],
                            artifact_repository.load_state(str(command["app_id"])),
                        ),
                        feedback,
                        str(original_implementation.get("base_package") or "com.easydep.app"),
                        True,
                        confirmed_target_refs=confirmed_target_refs,
                    )
                    repair_job_id = str(repair_job.get("job_id") or "")
                    if not repair_job_id:
                        raise RuntimeError("Automatic implementation repair returned no job ID.")
                    # 구현 worker가 실패하거나 서버가 재시작돼도 같은 checkpoint를 다시 찾을
                    # 수 있도록 LLM 실행 전에 job ID를 command에 저장한다.
                    repair_payload = {
                        **dict(command.get("payload") or {}),
                        "job_id": repair_job_id,
                    }
                    command["payload"] = repair_payload
                    repository.update_command(
                        str(command["command_id"]),
                        payload=repair_payload,
                    )
                    repaired = self._monitor_implementation(
                        repair_job,
                        command_id=str(command["command_id"]),
                    )
                    repaired_job = repaired.get("job") or {}
                    repaired_job_id = str(
                        repaired.get("job_id") or repaired_job.get("job_id") or ""
                    )
                    if not repaired_job_id:
                        raise RuntimeError("Automatic implementation repair returned no job ID.")
                    return self._run_testing_command(
                        command,
                        repaired_job_id,
                        previous_job=previous_job,
                        preserve_test=has_preserved_candidate,
                    )
                return self._run_testing_command(
                    command,
                    implementation_job_id,
                    previous_job=previous_job,
                )
            history = dict(result.get("repair_state") or {})
            repair_stage = str(
                result.get("current_stage") or result.get("phase") or prior.get("stage")
            )
            strategy_key = (
                f"delegate:{prior.get('stage')}:{repair_stage}:"
                f"episode-{int(history.get('attempt_count') or 0) + 1}"
            )
            delegated = dict(command)
            delegated["payload"] = {
                **command["payload"],
                "_repair_strategy_key": strategy_key,
                "text": (
                    "Repair the current stage using the accumulated repair history. "
                    f"Use this new strategy identity: {strategy_key}. "
                    "Do not repeat a rejected strategy or candidate. Resolve these blockers:\n- "
                    + "\n- ".join(messages)
                    + "\n\nAccumulated repair history:\n"
                    + json.dumps(history, ensure_ascii=False, sort_keys=True)
                ),
            }
            return self._stage_message(delegated, advance=False)
        if handler == "retry_requirements":
            app_id = str(command["app_id"])
            progress = self._requirements_progress_reporter(app_id, str(command["command_id"]))
            with requirements_telemetry.progress_scope(progress):
                result = retry_requirements_analysis(
                    app_id,
                    app_id=app_id,
                )
            return self._requirements_result(result)
        if handler == "retry_design":
            app_id = str(command["app_id"])
            status = session_status(app_id)
            stage = str(status.get("stage") or "design")
            response = self._run_design_operation(
                command,
                stage=stage,
                label=self._design_stage_label(stage, "Retrying"),
                operation=lambda: retry_design_session(app_id),
            )
            return self._design_result(response)
        if handler == "confirm_change":
            return self._confirm_change(command)
        if handler == "dismiss_change":
            return {"message": "Kept the existing artifacts and dismissed the change request."}
        if handler == "start_implementation":
            if action == "rerun_implementation":
                # The retry starts a new implementation run.  Tell the UI to
                # discard only the previous implementation timeline while
                # preserving requirement and design conversation history.
                repository.append_event(
                    str(command["app_id"]),
                    command_id=str(command["command_id"]),
                    stage="implementation",
                    kind="status",
                    actor="system",
                    text="",
                    metadata={"reset_implementation_timeline": True},
                )
            app_id = str(command["app_id"])
            design_state = cast(dict[str, Any], artifact_repository.load_state(app_id))
            job = implementation_worker.create_job(
                app_id,
                design_state,
                str(command["payload"].get("base_package") or "com.easydep.app"),
                bool(command["payload"].get("allow_assumptions", True)),
            )
            persisted_payload = {
                **dict(command["payload"]),
                "job_id": str(job["job_id"]),
            }
            command["payload"] = persisted_payload
            repository.update_command(
                str(command["command_id"]),
                payload=persisted_payload,
            )
            return self._monitor_implementation(job, command_id=str(command["command_id"]))
        if handler == "retry_implementation":
            payload = command["payload"]
            current_job = implementation_worker.get(str(payload["job_id"]))
            if str(current_job.get("app_id") or "") != str(command["app_id"]):
                raise ValueError("The implementation checkpoint does not belong to this app.")
            # 일반 구현 재시도는 이미 job ID만으로 충분하다. Testing이 만든 구현 수리만
            # 이전 Testing 결과와 고정 기능 계획을 찾아야 하므로 그때만 명령을 조회한다.
            testing_repair = command.get("stage") == "testing"
            prior = (
                repository.get_command(str(payload.get("action_id") or "")) or {}
                if testing_repair
                else {}
            )
            repository.append_event(
                str(command["app_id"]),
                command_id=str(command["command_id"]),
                stage="implementation",
                kind="status",
                actor="system",
                text="Resuming the failed implementation checkpoint.",
                metadata={
                    "status": "CHECKPOINT_RETRY_STARTED",
                    "job_id": str(payload["job_id"]),
                },
            )
            if current_job.get("status") == "COMPLETED" and testing_repair:
                # 구현 저장까지 끝난 직후 Workspace 명령만 끊긴 경우에는 이미 통과한 구현
                # 테스트를 다시 돌리지 않고 아래 기능 회귀 검사부터 이어 간다.
                repaired = {"job_id": str(payload["job_id"]), "job": current_job}
            else:
                job = implementation_worker.retry_failed(str(payload["job_id"]))
                repaired = self._monitor_implementation(
                    job,
                    command_id=str(command["command_id"]),
                )
            if not testing_repair:
                return repaired

            original = repository.get_command(
                str((prior.get("payload") or {}).get("action_id") or "")
            ) or {}
            original_result = original.get("result") or {}
            previous_job = original_result.get("job") or {}
            blockers = original_result.get("blocking_findings") or []
            preserve_test = any(
                isinstance(blocker, dict)
                and isinstance(blocker.get("candidate_plan"), dict)
                and bool(blocker.get("candidate_plan"))
                for blocker in blockers
            )
            repaired_job = repaired.get("job") or {}
            repaired_job_id = str(
                repaired.get("job_id") or repaired_job.get("job_id") or payload["job_id"]
            )
            return self._run_testing_command(
                command,
                repaired_job_id,
                previous_job=previous_job,
                preserve_test=preserve_test,
            )
        if handler == "start_testing":
            return self._run_testing_command(
                command,
                str(command["payload"]["implementation_job_id"]),
            )
        if handler == "branch_checkpoint":
            branch = create_checkpoint_branch(
                str(command["app_id"]),
                str(command["payload"]["checkpoint_stage"]),
            )
            return {
                **branch,
                "message": (
                    f"Created a new app branch after {branch['checkpoint_stage']}."
                ),
            }
        if handler == "rerun_from_stage":
            return self._rerun_from_stage(command)
        raise ValueError(f"Unsupported workspace command: {action}")

    def _rerun_from_stage(self, command: dict[str, Any]) -> dict[str, Any]:
        """선택 단계 직전까지 분기한 새 앱에서 정식 실행 경로를 시작한다."""

        restart_stage = RestartStage(str(command["payload"]["restart_stage"]))
        branch = create_restart_branch(str(command["app_id"]), restart_stage)
        target_app_id = str(branch["target_app_id"])
        entry_command_id = str(branch.get("entry_command_id") or "")

        if restart_stage == RestartStage.REQUIREMENTS:
            state = artifact_repository.load_state(target_app_id)
            next_command = self.submit(
                target_app_id,
                action="message",
                stage="requirements",
                payload={
                    "text": str(state.get("requirements_text") or ""),
                    "resource_constraints_text": str(
                        state.get("resource_constraints_text") or ""
                    ),
                },
            )
        elif restart_stage == RestartStage.DESIGN:
            next_command = self.submit(
                target_app_id,
                action="start_design",
                payload={"action_id": entry_command_id},
            )
        elif restart_stage == RestartStage.IMPLEMENTATION:
            next_command = self.submit(
                target_app_id,
                action="start_implementation",
                payload={"action_id": entry_command_id},
            )
        else:
            next_command = self.submit(
                target_app_id,
                action="start_testing",
                payload={
                    "action_id": entry_command_id,
                    "implementation_job_id": str(branch["implementation_job_id"]),
                },
            )
        return {
            **branch,
            "restart_stage": restart_stage.value,
            "started_command_id": next_command["command_id"],
            "message": f"Created a new branch and started {restart_stage.value} again.",
        }

    def _stage_message(self, command: dict[str, Any], *, advance: bool) -> dict[str, Any]:
        app_id = str(command["app_id"])
        payload = command["payload"]
        text = "" if advance else str(payload.get("text") or "").strip()
        stage = str(command["stage"])
        if advance and stage == "design" and payload.get("auto_approve_method_proposals") is True:
            # Auto mode is an affirmative user choice.  Keep the approval in
            # the same feedback path as a manual decision so reconciliation
            # still applies only the concrete, persisted MethodProposals.
            text = "approve all"
        if stage == "requirements":
            action_id = str(payload.get("action_id") or "")
            previous = repository.get_command(action_id) if action_id else None
            continuation = bool(
                action_id and previous is not None and previous["stage"] == "requirements"
            )
            if continuation:
                assert previous is not None
                previous_result = _with_capability_handoff_questions(
                    app_id, previous.get("result") or {}
                )
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
                        result = analyze_requirements(request)
                    return self._requirements_result(result)
                resource_questions, selected_resource_question = _resource_questions(
                    previous_result
                )
                resource_question = (
                    previous_result.get("resource_question") or selected_resource_question
                )
                resource_field = str((resource_question or {}).get("field") or "")
                if text and resource_field:
                    request = AnalyzeRequest(
                        resource_answers={resource_field: text},
                        thread_id=app_id,
                        app_id=app_id,
                    )
                else:
                    conversation_intent = payload.get("conversation_intent")
                    if (
                        isinstance(conversation_intent, dict)
                        and conversation_intent.get("intent") == "revise"
                    ):
                        refs = [
                            str(ref)
                            for ref in conversation_intent.get("targets") or []
                            if isinstance(ref, str)
                        ]
                        prefixes = {ref.partition(":")[0] for ref in refs}
                        owner_by_prefix: dict[str, FeedbackStage] = {
                            "requirement": "actors",
                            "actor": "actors",
                            "use_case": "use_cases",
                            "use_case_spec": "specs",
                            "relationship": "relationships",
                        }
                        stages = {owner_by_prefix[prefix] for prefix in prefixes}
                        if len(stages) != 1:
                            raise ValueError(
                                "A requirements revision must target one modeling stage."
                            )
                        owner = stages.pop()
                        target_ids = [ref.partition(":")[2] for ref in refs]
                        request = AnalyzeRequest(
                            edit=FeedbackEdit(
                                stage=owner,
                                scope="local",
                                target_ids=target_ids,
                                instruction=text,
                            ),
                            thread_id=app_id,
                            app_id=app_id,
                        )
                    elif command.get("action") == "delegate_repair":
                        repairable = [
                            blocker
                            for blocker in previous_result.get("blocking_findings") or []
                            if isinstance(blocker, dict) and blocker.get("repairable") is not False
                        ]
                        stage_order = {
                            "actors": 0,
                            "use_cases": 1,
                            "specs": 2,
                            "relationships": 3,
                        }
                        owner_value = min(
                            (str(item.get("stage") or "relationships") for item in repairable),
                            key=lambda value: stage_order.get(value, 99),
                            default="relationships",
                        )
                        owner = cast(FeedbackStage, owner_value)
                        targets = sorted(
                            {
                                str(target)
                                for item in repairable
                                if str(item.get("stage") or "") == owner
                                for target in item.get("target_ids") or []
                            }
                        )
                        request = AnalyzeRequest(
                            edit=FeedbackEdit(
                                stage=owner,
                                scope="local" if targets else "broad",
                                target_ids=targets,
                                instruction=text,
                            ),
                            thread_id=app_id,
                            app_id=app_id,
                        )
                    else:
                        request = AnalyzeRequest(answer=text, thread_id=app_id, app_id=app_id)
            else:
                provider = cast(CloudProvider, str(payload.get("provider") or ""))
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
                    resource_constraints_text=str(payload.get("resource_constraints_text") or ""),
                    cloud_constraints=cloud_constraints,
                )
            progress = self._requirements_progress_reporter(app_id, str(command["command_id"]))
            with requirements_telemetry.progress_scope(progress):
                result = analyze_requirements(request)
            return self._requirements_result(result)

        if stage == "design":
            status = session_status(app_id)
            if status.get("retryable") and command.get("action") != "start_design":
                failed_stage = str(status.get("stage") or "design")
                raise ValueError(
                    f"The {failed_stage} step failed. Retry that checkpoint before "
                    "starting or advancing the design pipeline."
                )
            context = payload.get("context") or {}
            action_id = str(payload.get("action_id") or "")
            previous = repository.get_command(action_id) if action_id else None
            previous_result = (previous or {}).get("result") or {}
            resource_question = previous_result.get("resource_question") or {}
            if text and resource_question.get("field") == "deployment.selectedTarget":
                allowed = {
                    str(choice.get("value") or "")
                    for choice in resource_question.get("choices") or []
                    if isinstance(choice, dict)
                }
                if text not in allowed:
                    raise ValueError("Choose one of the stored deployment targets.")
                return self._design_result(select_deployment_target_session(app_id, text))
            validated_feedbacks = context.get("validated_target_feedbacks")
            if validated_feedbacks is not None:
                if not isinstance(validated_feedbacks, list) or not validated_feedbacks:
                    raise ValueError("Validated design feedback requires at least one target.")
                revisions = BatchReviseRequest.model_validate(
                    {"revisions": validated_feedbacks}
                ).revisions
                validated_revision = revise_design_elements(
                    app_id,
                    BatchReviseRequest(revisions=revisions),
                )
                return {
                    "awaiting_input": True,
                    "kind": "action_required",
                    "message": (
                        f"Revised {len(revisions)} validated design element(s) and only "
                        "their trace-linked artifacts. Review the result or continue."
                    ),
                    "current_stage": status.get("stage") or "design",
                    "changed": validated_revision.get("changed") or [],
                    "touched": validated_revision.get("touched") or {},
                    "related": validated_revision.get("related") or [],
                    "design": validated_revision,
                }
            target_feedbacks = self._sequence_target_feedbacks(context)
            revised: dict[str, Any] | None = None
            if target_feedbacks:
                # Every entry has an explicit UC and its own instruction.  The
                # batch service keeps all revisions in memory until all of them
                # succeed, so this command cannot persist a half-applied set.
                revised = revise_design_elements(
                    app_id,
                    BatchReviseRequest(revisions=target_feedbacks),
                )
                revision_message = (
                    f"Revised {len(target_feedbacks)} selected use-case diagrams and "
                    "only their trace-linked artifacts. Review the result or continue."
                )
                related_default: list[Any] | dict[str, Any] = {}
            element_ref = str(context.get("element_ref") or "").strip()
            if revised is None and text and element_ref:
                # A UI-selected element is an explicit local-edit request, not
                # ordinary stage feedback.  In particular, sequence feedback
                # must carry ``sequence_diagram:UCn`` so we never rewind and
                # regenerate every use-case card just to revise one of them.
                revised = revise_design_element(
                    app_id,
                    ReviseRequest(target=element_ref, feedback=text),
                )
                revision_message = (
                    f"Revised the selected {element_ref} and only its "
                    "trace-linked artifacts. Review the result or continue."
                )
                related_default = []
            if revised is not None:
                return {
                    "awaiting_input": bool(status.get("active")),
                    "kind": "action_required",
                    "message": revision_message,
                    "current_stage": status.get("stage") or "design",
                    "changed": revised.get("changed") or [],
                    "touched": revised.get("touched") or {},
                    "related": revised.get("related") or related_default,
                    "design": revised,
                }
            current_stage = str(status.get("stage") or "")
            if text and command.get("action") == "message" and current_stage == "sequence_diagram":
                raise ValueError(
                    "Select one or more use-case targets and provide feedback for each target."
                )
            if command.get("action") == "start_design":
                # start_design은 현재 gate의 '다음' 버튼이 아니라 설계를 처음부터 다시
                # 시작하는 공개 action이다. 기존 checkpoint가 남아 있어도 service가
                # reset한 뒤 반드시 class diagram부터 실행해야 한다.
                operation_stage = DESIGN_STAGES[0]
                verb = "Generating"

                def operation():
                    return start_design_session(app_id)
            elif status.get("active"):
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
                    verb = "Generating" if operation_stage != "design_complete" else "Completing"

                def operation():
                    return resume_design_session(app_id, text)
            else:
                operation_stage = DESIGN_STAGES[0]
                verb = "Generating"

                def operation():
                    return start_design_session(app_id)

            response = self._run_design_operation(
                command,
                stage=operation_stage,
                label=self._design_stage_label(operation_stage, verb),
                operation=operation,
            )
            return self._design_result(response)

        if stage != "implementation":
            raise ValueError("The current stage cannot process a conversational command.")
        if not text:
            raise ValueError("Enter implementation feedback.")
        conversation_intent = payload.get("conversation_intent")
        confirmed_target_refs = (
            [
                str(ref)
                for ref in conversation_intent.get("targets") or []
                if isinstance(ref, str)
            ]
            if isinstance(conversation_intent, dict)
            and conversation_intent.get("intent") == "revise"
            else []
            if command.get("action") == "delegate_repair"
            else None
        )
        job = implementation_worker.create_feedback_job(
            app_id,
            cast(dict[str, Any], artifact_repository.load_state(app_id)),
            text,
            str(payload.get("base_package") or "com.easydep.app"),
            bool(payload.get("allow_assumptions", True)),
            confirmed_target_refs=confirmed_target_refs,
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
    ) -> dict[str, Any]:
        app_id = str(command["app_id"])
        command_id = str(command["command_id"])
        started = time.perf_counter()
        llm_timing_events: list[dict[str, Any]] = []

        def record(
            status: str,
            detail: str,
            metadata: dict[str, Any] | None = None,
        ) -> None:
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
                }
                | dict(metadata or {}),
            )

        def report(event: str, fields: dict[str, Any]) -> None:
            if event != "classDiagramSnapshotAccepted":
                return
            puml = str(fields.get("puml") or "")
            if not puml.strip():
                return
            snapshot = live_previews.publish(
                app_id=app_id,
                command_id=command_id,
                stage="class_diagram",
                puml=puml,
                phase=str(fields.get("phase") or "generation"),
                unit=str(fields.get("unit") or ""),
                completed=int(fields.get("completed") or 0),
                total=int(fields.get("total") or 0),
            )
            try:
                # preview가 나온 시점에 계속 실행 중인 renderer로 SVG를 준비한다. 브라우저가
                # 처음 그림을 열 때까지 JVM 기동과 렌더링을 미루지 않으며, 같은 내용은 공통
                # SHA cache가 재사용한다.
                image = render_plantuml(snapshot.puml, "svg")
                if image:
                    live_previews.cache_svg(
                        app_id,
                        command_id,
                        snapshot.stage,
                        snapshot.revision,
                        image,
                    )
            except Exception as error:
                # 중간 preview 표시 실패가 클래스 모델 생성 자체를 중단해서는 안 된다. 최종
                # 산출물 저장 때 다시 렌더링하며, 실패 유형만 timing event로 남긴다.
                log_design_timing(
                    "plantuml.preview_warmup.failed",
                    error_type=type(error).__name__,
                    preview_revision=snapshot.revision,
                )
            record(
                "running",
                str(fields.get("detail") or "Updating the class diagram"),
                {
                    "progress_event": "classDiagramPreviewUpdated",
                    "preview_revision": snapshot.revision,
                    "preview_unit": snapshot.unit,
                    "preview_completed": snapshot.completed,
                    "preview_total": snapshot.total,
                },
            )

        try:
            with (
                design_timing_context(
                    app_id=app_id,
                    command_id=command_id,
                    requested_stage=stage,
                ),
                capture_llm_timings() as llm_timing_events,
            ):
                record("running", "Started")
                log_design_timing("workspace.design_operation.started", label=label)
                try:
                    with design_progress.progress_scope(report):
                        response = operation()
                except Exception as error:
                    elapsed = time.perf_counter() - started
                    log_design_timing(
                        "workspace.design_operation.failed",
                        elapsed_ms=round(elapsed * 1000, 1),
                        error_type=type(error).__name__,
                    )
                    record(
                        "failed",
                        f"Failed after {elapsed:.1f}s: {WorkspaceService._error_text(error)}",
                    )
                    raise
            elapsed = time.perf_counter() - started
            payload = response
            validation = payload.get("validation") or {}
            stage_validation = validation.get(stage) or {}
            findings = [
                *list(stage_validation.get("errors") or []),
                *list(stage_validation.get("findings") or []),
            ]
            log_design_timing(
                "workspace.design_operation.completed",
                elapsed_ms=round(elapsed * 1000, 1),
                findings_count=len(findings),
            )
            if findings:
                record(
                    "needs_review",
                    f"Draft generated in {elapsed:.1f}s; {len(findings)} findings require revision",
                )
            else:
                record("completed", f"Completed in {elapsed:.1f}s")
            return response
        finally:
            if llm_timing_events:
                # 상세 timing 수집기는 원래 class 최적화 실험에서도 사용하는 event 모양을
                # 그대로 제공한다. 공개 Workspace event에 같은 dict를 넣어 평가 도구가
                # 내부 설계 함수를 직접 호출하지 않고도 호출·token·repair·cache를 셀 수
                # 있게 한다. prompt나 LLM 응답 원문은 이 목록에 포함되지 않는다.
                repository.append_event(
                    app_id,
                    command_id=command_id,
                    stage="design",
                    kind="progress",
                    actor="system",
                    text="Design LLM metrics recorded.",
                    metadata={
                        "progress_event": "designLlmMetrics",
                        "analysis_step": stage,
                        "llm_timing_events": [
                            _public_design_timing_event(event) for event in llm_timing_events
                        ],
                    },
                )
            live_previews.mark_terminal(app_id, command_id)

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
                step_label = step_labels.get(str(current_step), "Running an analysis step")
                text = step_label
                metadata.update(
                    {
                        "analysis_step": current_step,
                        "progress_step_label": step_label,
                        "progress_detail": "Started",
                        "progress_status": "running",
                    }
                )
            elif event == "analysisStepFinished":
                finished_step = str(fields.get("step") or current_step or "")
                label = step_labels.get(finished_step, "Analysis step")
                elapsed = float(fields.get("elapsedSeconds") or 0)
                text = f"{label} completed in {elapsed:.1f}s"
                metadata.update(
                    {
                        "analysis_step": finished_step,
                        "progress_step_label": label,
                        "progress_detail": f"Completed in {elapsed:.1f}s",
                        "progress_status": str(fields.get("status") or "completed"),
                    }
                )
            elif event == "llmOperationStarted":
                operation = str(fields.get("operation") or "")
                with progress_lock:
                    operation_counts[operation] = operation_counts.get(operation, 0) + 1
                    operation_count = operation_counts[operation]
                if operation == "structured:DeploymentNeedsResult":
                    total = max(1, int(requirements_settings.capability_samples))
                    suffix = f" (sample {operation_count} of {total})" if total > 1 else ""
                else:
                    suffix = f" (call {operation_count})" if operation_count > 1 else ""
                label = operation_labels.get(operation, "AI model response")
                text = f"Waiting for {label}{suffix}"
                operation_step = operation_steps.get(operation) or current_step
                step_label = step_labels.get(str(operation_step), "Running requirement analysis")
                metadata.update(
                    {
                        "analysis_step": operation_step,
                        "progress_step_label": step_label,
                        "progress_detail": f"Waiting for {label}{suffix}",
                        "progress_status": "running",
                    }
                )
            elif event == "llmOperationFinished":
                operation = str(fields.get("operation") or "")
                elapsed = float(fields.get("elapsedSeconds") or 0)
                status = str(fields.get("status") or "completed")
                label = operation_labels.get(operation, "AI model response")
                text = f"{label} {status} in {elapsed:.1f}s"
                operation_step = operation_steps.get(operation) or current_step
                step_label = step_labels.get(str(operation_step), "Running requirement analysis")
                metadata.update(
                    {
                        "analysis_step": operation_step,
                        "progress_step_label": step_label,
                        "progress_detail": f"{label} {status} in {elapsed:.1f}s",
                        "progress_status": "running" if status == "completed" else status,
                    }
                )
            elif event in {"specTaskStarted", "specTaskFinished"}:
                name = str(fields.get("useCaseName") or fields.get("useCaseId") or "")
                text = f"Writing the use-case specification: {name}"
                metadata.update(
                    {
                        "analysis_step": current_step,
                        "progress_step_label": step_labels["generate_specs"],
                        "progress_detail": "Generating specifications in parallel",
                        "progress_status": "running",
                    }
                )
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
            if phase == "requirements_handoff":
                blockers = list(result.get("blocking_findings") or [])
                resource_questions, resource_question = _resource_questions(result)
                repairable = [
                    blocker
                    for blocker in blockers
                    if isinstance(blocker, dict) and blocker.get("repairable") is not False
                ]
                return {
                    "awaiting_input": True,
                    "kind": "action_required",
                    "message": (
                        "A deployment decision is required before design can start. "
                        f"{resource_question.get('question')}"
                        if resource_question
                        else (
                            f"Design handoff is blocked by {len(blockers)} unresolved "
                            "requirements finding(s). Review them, provide feedback, or "
                            "delegate the repair to the LLM."
                        )
                    ),
                    "phase": phase,
                    "requires_revision": True,
                    "blocking_findings": blockers,
                    "repair_state": result.get("repair_state")
                    or {
                        "status": "ACTIVE" if repairable else "NEEDS_INPUT",
                        "attempt_count": 0,
                        "accepted_count": 0,
                        "recent_attempts": [],
                    },
                    "can_delegate_repair": bool(repairable),
                    "resource_question": resource_question,
                    "resource_questions": resource_questions,
                    "summary": result.get("feedback_summary"),
                    "review_artifacts": [
                        "Refined requirements",
                        "Use cases",
                        "Use-case specifications",
                        "Use-case diagram",
                    ],
                }
            requirements = list(result.get("requirements") or [])
            functional_count = sum(1 for item in requirements if item.get("type") == "FR")
            non_functional_count = sum(1 for item in requirements if item.get("type") == "NFR")
            requirement_label = "requirement" if len(requirements) == 1 else "requirements"
            lead = {
                "requirements": (
                    f"I refined and classified {len(requirements)} {requirement_label} "
                    f"({functional_count} functional and "
                    f"{non_functional_count} non-functional)."
                ),
                "use_cases": (
                    f"I identified {len(result.get('use_cases') or [])} user-goal use cases."
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
            resource_questions, resource_question = _resource_questions(result)
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
        # The design service reports completion as ``status: completed``.
        # Older stored command results can still contain the two flags below.
        finished = bool(
            result.get("status") == "completed" or result.get("finished") or session.get("finished")
        )
        if finished:
            return {
                "message": "Design artifact generation completed.",
                "design": result,
            }
        stage = (
            session.get("current_stage")
            or session.get("stage")
            or result.get("current_stage")
            or result.get("stage")
        )
        deployment_meta = (result.get("artifact_metadata") or {}).get("deployment_diagram") or {}
        target_choices = [
            {
                "value": str(target.get("id") or ""),
                "label": (
                    f"{str(target.get('provider') or '').upper()} {target.get('region') or ''!s}"
                ).strip(),
                "description": (
                    "Zones: " + ", ".join(str(zone) for zone in target.get("zones") or [])
                    if target.get("zones")
                    else "Use this completed deployment projection."
                ),
            }
            for target in deployment_meta.get("targets") or []
            if isinstance(target, dict) and target.get("status") == "completed" and target.get("id")
        ]
        if (
            stage == "deployment_diagram"
            and (deployment_meta.get("selection") or {}).get("status") == "needsInput"
            and target_choices
        ):
            question = {
                "field": "deployment.selectedTarget",
                "kind": "required",
                "question": "Choose the deployment target to use for the final package.",
                "choices": target_choices,
            }
            return {
                "awaiting_input": True,
                "kind": "question",
                "message": question["question"],
                "current_stage": stage,
                "resource_question": question,
                "resource_questions": [question],
                "design": result,
            }
        stage_validation = (result.get("validation") or {}).get(stage) or {}
        findings = [
            *list(stage_validation.get("errors") or []),
            *list(stage_validation.get("findings") or []),
        ]
        method_proposals = list(stage_validation.get("method_proposals") or [])
        requires_revision = bool(findings)
        repair_history = stage_validation.get("repair_history") or {}
        repair_status = str(repair_history.get("status") or "")
        attempts = list(repair_history.get("attempts") or [])
        repair_state = {
            "status": (
                "WAITING_EXTERNAL"
                if repair_status == "WAITING_EXTERNAL"
                else "STALLED"
                if repair_status == "STALLED"
                else "ACTIVE"
                if findings
                else "COMPLETED"
            ),
            "attempt_count": len(attempts),
            "accepted_count": sum(
                attempt.get("outcome") in {"improved", "clean"}
                for attempt in attempts
                if isinstance(attempt, dict)
            ),
            "recent_attempts": attempts[-5:],
            "tried_strategies": sorted(
                {
                    str(attempt.get("strategy_key") or "")
                    for attempt in attempts
                    if isinstance(attempt, dict) and attempt.get("strategy_key")
                }
            ),
            "rejected_candidate_digests": sorted(
                {
                    str(attempt.get("candidate_digest") or "")
                    for attempt in attempts
                    if isinstance(attempt, dict)
                    and attempt.get("candidate_digest")
                    and attempt.get("outcome") not in {"improved", "clean"}
                }
            ),
            "finding_digest": stable_digest(findings),
            "stall_reason": repair_history.get("stall_reason") or "",
        }
        blocking_findings = [
            {
                "code": "design.validation",
                "stage": str(stage or "design"),
                "target_ids": [],
                "message": str(finding),
                "severity": "error",
                "repairable": True,
            }
            for finding in findings
        ]
        return {
            "awaiting_input": True,
            "kind": "action_required",
            "message": (
                f"The {str(stage or 'design').replace('_', ' ')} draft has "
                f"{len(findings)} findings. Review the draft, provide feedback, or "
                "delegate the repair to the LLM before continuing."
                if requires_revision
                else "Review the current design artifacts, then send revision feedback "
                "or continue to the next stage."
            ),
            "current_stage": stage,
            "requires_revision": requires_revision,
            "blocking_findings": blocking_findings,
            "repair_state": repair_state,
            "can_delegate_repair": requires_revision,
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
            result = revise_design_elements(app_id, BatchReviseRequest(revisions=target_feedbacks))
            return {
                "message": "Revised the selected use-case diagrams and trace-linked artifacts.",
                "design": result,
            }
        element_ref = context.get("element_ref")
        if element_ref:
            result = revise_design_element(
                app_id, ReviseRequest(target=str(element_ref), feedback=feedback)
            )
            return {
                "message": "Revised the selected design element and affected downstream artifacts.",
                "design": result,
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
        rewind_design_session(app_id, stage)
        result = resume_design_session(app_id, feedback)
        return {
            "message": "Returned to the selected design stage and applied the feedback.",
            "design": result,
        }

    @staticmethod
    def _implementation_progress_snapshot(job: dict[str, Any]) -> dict[str, Any]:
        """내구성 체크포인트에서 화면에 표시할 구현 진행 단계를 만든다.

        공개 작업 상태는 phase 완료 시점에만 갱신될 수 있다. 실행 디렉터리의
        ``workflow-state.json``과 agent event journal을 함께 읽어 현재 phase와
        실제 편집 중인 파일을 phase 실행 중에도 표시한다.
        """
        job_id = str(job.get("job_id") or "")
        private_job = job
        if job_id:
            try:
                private_job = implementation_worker._read(job_id)
            except Exception:  # Progress reporting must not interrupt a job.
                private_job = job

        run_root = str(private_job.get("run_root") or job.get("run_root") or "").strip()
        workflow = private_job.get("workflow") or job.get("workflow")
        run_path = Path(run_root) if run_root else None
        if run_path is not None:
            state_path = run_path / "reports" / "workflow-state.json"
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = None
            if isinstance(state, dict):
                workflow = state
        agent_results = _implementation_agent_results(run_path) if run_path else []

        updates: list[dict[str, str]] = []

        def add_update(step: str, label: str, status: str, detail: str = "") -> None:
            updates.append(
                {
                    "step": step,
                    "label": label,
                    "status": status,
                    "detail": detail,
                }
            )

        job_status = str(job.get("status") or private_job.get("status") or "")
        terminal_failure = job_status in {"FAILED", "CANCELLED", "REJECTED"}
        failure_error = str(private_job.get("error") or job.get("error") or "").strip()
        failure_lines = [line.strip() for line in failure_error.splitlines() if line.strip()]
        meaningful_failure_lines = [
            line
            for line in failure_lines
            if re.search(r"\b(error|exception|failed|timeout|denied)\b", line, re.IGNORECASE)
        ]
        failure_detail = (
            (meaningful_failure_lines[-1] if meaningful_failure_lines else failure_lines[-1])[-500:]
            if failure_lines
            else "The implementation job did not complete."
        )
        live_progress = job.get("progress")
        progress_status = (
            str(live_progress.get("status") or "") if isinstance(live_progress, dict) else ""
        )
        progress_message = (
            str(live_progress.get("message") or "") if isinstance(live_progress, dict) else ""
        )
        generation_status = (
            "PLANNING" if job_status == "PLANNING" else progress_status or job_status
        )

        if generation_status in {
            "QUEUED",
            "VALIDATING_INPUT",
            "GENERATING_SOURCES",
            "PREPARING_BUILD",
            "VERIFYING",
            "PLANNING",
        }:
            add_update(
                "prepare-job",
                "Prepare implementation job",
                "running",
                "Preparing the implementation job.",
            )
        else:
            # The job leaves the queue before its first generator checkpoint.
            # Explicitly close this UI-only milestone so it cannot look like a
            # long-running task while source generation or compilation proceeds.
            add_update("prepare-job", "Prepare implementation job", "completed")

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
            add_update("validate-input", "Validate input and design", "completed")
            add_update(
                "reuse-generated-run",
                "Reuse generated output",
                "running",
                progress_message,
            )
        elif generation_status == "PREPARING_FEEDBACK":
            add_update("validate-input", "Validate input and design", "completed")
            add_update("prepare-feedback", "Prepare feedback application", "running", progress_message)

        workflow_complete = False
        if isinstance(workflow, dict):
            workflow_status = str(workflow.get("status") or "")
            current_phase = str(workflow.get("currentPhase") or "")
            tasks = [item for item in workflow.get("tasks", []) if isinstance(item, dict)]
            phase_statuses = {
                str(phase.get("phaseId") or ""): str(phase.get("status") or "").upper()
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
                    task for task in tasks if str(task.get("phase") or "") in phase_ids
                ]
                task_statuses = {str(task.get("status") or "").upper() for task in display_tasks}
                has_phase_work = bool(display_tasks) or any(
                    phase_statuses.get(phase_id) not in {None, "UNPLANNED"}
                    for phase_id in display_phases
                )
                all_succeeded = has_phase_work and all(
                    phase_statuses.get(phase_id) in {"SUCCEEDED", "COMPLETED", "UNPLANNED"}
                    or (
                        any(str(task.get("phase") or "") == phase_id for task in display_tasks)
                        and all(
                            str(task.get("status") or "").upper() in {"SUCCEEDED", "COMPLETED"}
                            for task in display_tasks
                            if str(task.get("phase") or "") == phase_id
                        )
                    )
                    for phase_id in display_phases
                )
                if all_succeeded:
                    add_update(f"phase-{display_id}", label, "completed")
                elif (
                    "FAILED" in task_statuses
                    or any(
                        phase_statuses.get(phase_id) in {"FAILED", "TIMEOUT"}
                        for phase_id in display_phases
                    )
                    or (terminal_failure and current_phase in phase_ids)
                ):
                    add_update(
                        f"phase-{display_id}",
                        label,
                        "failed",
                        failure_detail if terminal_failure else "",
                    )
                elif (
                    workflow_status.upper() == "RUNNING" and current_phase in phase_ids
                ) or "RUNNING" in task_statuses:
                    add_update(
                        f"phase-{display_id}",
                        label,
                        "running",
                        f"{label} is in progress.",
                    )
                if display_id == "backend" and not all_succeeded:
                    tasks_by_phase: dict[str, list[dict[str, Any]]] = {}
                    for task in display_tasks:
                        task_status = str(task.get("status") or "PENDING").lower()
                        if task_status not in {
                            "running",
                            "succeeded",
                            "completed",
                            "failed",
                            "timeout",
                            "needs_review",
                        }:
                            continue
                        tasks_by_phase.setdefault(str(task.get("phase") or ""), []).append(task)
                    for task_phase, phase_tasks in tasks_by_phase.items():
                        if current_phase and task_phase != current_phase:
                            continue
                        statuses = {
                            str(task.get("status") or "PENDING").lower() for task in phase_tasks
                        }
                        if statuses & {"failed", "timeout", "needs_review"}:
                            task_status = next(
                                status
                                for status in ("failed", "timeout", "needs_review")
                                if status in statuses
                            )
                        elif statuses and statuses <= {"succeeded", "completed"}:
                            task_status = "completed"
                        elif "running" in statuses:
                            task_status = "running"
                        else:
                            task_status = "pending"
                        task_label = next(
                            (
                                phase_label
                                for phase_id, phase_label in _IMPLEMENTATION_WORKFLOW_PHASES
                                if phase_id == task_phase
                            ),
                            task_phase,
                        )
                        details = [str(task.get("detail") or "") for task in phase_tasks]
                        detail = next((item for item in details if item), "")
                        add_update(
                            f"sub-backend-{task_phase}",
                            task_label,
                            task_status,
                            detail,
                        )

            workflow_complete = workflow_status == "COMPLETE" or (
                workflow_status == "READY" and implementation_worker._workflow_is_complete(workflow)
            )
            activity = workflow.get("currentActivity")
            if (
                not terminal_failure
                and not workflow_complete
                and isinstance(activity, dict)
                and str(activity.get("id") or "")
            ):
                activity_status = str(activity.get("status") or "running").lower()
                if activity_status == "succeeded":
                    activity_status = "completed"
                activity_id = str(activity["id"])
                activity_phase = activity_id.removeprefix("verify-").removeprefix("audit-")
                if activity_phase == "backend":
                    display_id, display_label = "backend", "Backend implementation"
                else:
                    display_id, display_label, _ = next(
                        (
                            item
                            for item in _IMPLEMENTATION_DISPLAY_PHASES
                            if activity_phase in item[2]
                        ),
                        ("implementation", "Backend implementation", frozenset()),
                    )
                activity_suffix = (
                    "build and unit tests"
                    if activity_id.startswith("verify-")
                    else "output review"
                )
                activity_label = f"{display_label}: {activity_suffix}"
                if activity_id != "completion-audit" and display_id != "backend":
                    add_update(
                        "activity-" + display_id,
                        activity_label,
                        activity_status,
                        str(activity.get("detail") or ""),
                    )
            elif workflow_complete:
                add_update("release-verification", "Final release verification", "completed")

        if terminal_failure:
            add_update(
                "implementation-result",
                "Implementation job failed",
                "failed",
                failure_detail,
            )

        current_file: str | None = None
        if workflow_complete or job_status in TERMINAL_JOB_STATUSES:
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
                    tool_name = str(payload.get("tool") or "") if isinstance(payload, dict) else ""
                    if not isinstance(event, dict):
                        continue
                    path_sources = [event]
                    path_sources.extend(
                        value
                        for value in (event.get("action"), event.get("observation"))
                        if isinstance(value, dict)
                    )
                    path_value = next(
                        (
                            value
                            for source in path_sources
                            for value in (
                                source.get("path"),
                                source.get("file_path"),
                                source.get("filePath"),
                            )
                            if isinstance(value, str) and value.strip()
                        ),
                        None,
                    )
                    if not isinstance(path_value, str) or not path_value.strip():
                        continue
                    if "file_editor" not in tool_name and tool_name not in {
                        "restricted_file_editor",
                        "file_editor",
                    }:
                        continue
                    current_file = path_value.strip().replace("\\", "/")
                    application_marker = "/application/"
                    if application_marker in current_file:
                        current_file = "application/" + current_file.split(application_marker, 1)[1]

        if current_file:
            file_name = Path(current_file).name
            add_update(
                "implementation-file",
                "Current implementation file",
                "running",
                f"Editing {file_name}",
            )

        if not updates:
            return {}
        latest = updates[-1]
        snapshot: dict[str, Any] = {
            "updates": updates,
            "progress_card_label": "Implementation progress",
            "text": latest["detail"] or latest["label"],
            "progress_detail": latest["detail"] or latest["label"],
            "progress_status": latest["status"],
        }
        if current_file:
            file_name = Path(current_file).name
            snapshot["current_file"] = current_file
            snapshot["current_class"] = Path(file_name).stem
        if agent_results:
            snapshot["agent_results"] = agent_results
        return snapshot

    def _implementation_repair_outcomes(
        self,
        latest_job: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """부모 작업을 따라가며 이전 수리의 변경 범위를 짧게 만든다.

        OpenHands의 마지막 답변은 화면과 실행 기록에 그대로 남는다. 다음 수리 LLM은 최신
        source를 직접 읽을 수 있고 현재 실패 증거도 따로 받으므로, 자유형 자기 설명을 다시
        보내지 않는다. 어떤 작업이 어떤 파일을 바꿨는지만 알려 주면 같은 범위를 살피면서도
        이미 존재하는 코드를 기준으로 다른 해결책을 찾을 수 있다.
        """

        recent: list[dict[str, Any]] = []
        older_by_signature: dict[str, dict[str, Any]] = {}
        current = latest_job
        seen_job_ids: set[str] = set()
        while current.get("job_type") == "FEEDBACK_REVISION":
            job_id = str(current.get("job_id") or "")
            if job_id and job_id in seen_job_ids:
                break
            if job_id:
                seen_job_ids.add(job_id)

            progress = self._implementation_progress_snapshot(current)
            agent_results = [
                item
                for item in progress.get("agent_results") or []
                if isinstance(item, dict)
            ]
            changed_files = sorted(
                {
                    str(path)
                    for item in agent_results
                    for path in item.get("changed_files") or []
                    if isinstance(path, str) and path
                }
            )
            outcome = {
                "job_id": job_id,
                "status": str(current.get("status") or ""),
                "changed_files": changed_files,
            }
            if len(recent) < 3:
                recent.append(outcome)
            else:
                compact = {
                    "changed_files": changed_files,
                    "status": str(current.get("status") or ""),
                }
                signature = stable_digest(compact)
                stored = older_by_signature.setdefault(
                    signature,
                    {"signature": signature, "repetitions": 0, **compact},
                )
                stored["repetitions"] = int(stored["repetitions"]) + 1

            parent_job_id = str(current.get("parent_job_id") or "")
            if not parent_job_id:
                break
            try:
                current = implementation_worker.get(parent_job_id)
            except Exception:
                # 오래된 작업 파일이 정리됐더라도 현재 수리까지 막지는 않는다.
                break
        return recent, list(older_by_signature.values())

    @staticmethod
    def _testing_implementation_feedback(
        result: dict[str, Any],
        blockers: list[dict[str, Any]],
        *,
        previous_repair_results: list[dict[str, Any]] | None = None,
        older_repair_summaries: list[dict[str, Any]] | None = None,
    ) -> str:
        """제품 수리 에이전트에 실패 증거와 고정 테스트 계획을 전달한다."""
        evidence = []
        target_ids: list[str] = []
        file_hints: list[str] = []
        trace_refs: list[str] = []
        execution_evidence: list[dict[str, Any]] = []
        candidate_digests: list[str] = []
        candidate_plan: dict[str, Any] = {}
        for blocker in blockers:
            message = str(blocker.get("message") or "").strip()
            if message:
                evidence.append(message)
            target_ids.extend(
                str(item)
                for item in blocker.get("target_ids") or []
                if isinstance(item, str) and item
            )
            file_hints.extend(
                str(item)
                for item in blocker.get("file_hints") or []
                if isinstance(item, str) and item
            )
            trace_refs.extend(
                str(item)
                for item in blocker.get("trace_refs") or []
                if isinstance(item, str) and item
            )
            if isinstance(blocker.get("evidence"), dict) and blocker["evidence"]:
                execution_evidence.append(dict(blocker["evidence"]))
            digest = str(blocker.get("candidate_digest") or "").strip()
            if digest:
                candidate_digests.append(digest)
            if not candidate_plan and isinstance(blocker.get("candidate_plan"), dict):
                candidate_plan = dict(blocker["candidate_plan"])
        history = dict(result.get("repair_state") or {})
        needs_fixture = any(
            item.get("code") == "TEST_PROFILE_DATA_UNAVAILABLE"
            for item in execution_evidence
        )
        parts = [
            (
                "The generated application's test profile lacks prerequisite data for a "
                "preserved functional flow. Add the smallest test-profile-only fixture or "
                "startup setup that makes the documented success path executable. Do not "
                "change production behavior, API contracts, or test acceptance conditions."
                if needs_fixture
                else "The generated application failed a preserved functional test. Repair only "
                "the production implementation. Keep the existing contracts and test "
                "acceptance conditions unchanged."
            ),
            "Failure evidence:\n- " + "\n- ".join(evidence or ["Testing gate failed."]),
        ]
        if target_ids:
            parts.append("Confirmed artifact targets:\n- " + "\n- ".join(dict.fromkeys(target_ids)))
        if trace_refs:
            parts.append("Related artifact references:\n- " + "\n- ".join(dict.fromkeys(trace_refs)))
        if file_hints:
            parts.append("Start investigation with these trace-linked files:\n- " + "\n- ".join(dict.fromkeys(file_hints)))
        if candidate_digests:
            parts.append("Preserved test plan digest: " + ", ".join(dict.fromkeys(candidate_digests)))
        if execution_evidence:
            parts.append(
                "Exact failing HTTP evidence:\n"
                + json.dumps(execution_evidence, ensure_ascii=False, sort_keys=True)
            )
        if candidate_plan:
            parts.append(
                "Preserved functional test plan:\n"
                + json.dumps(candidate_plan, ensure_ascii=False, sort_keys=True)
            )
        if history:
            parts.append(
                "Previous repair history:\n"
                + json.dumps(history, ensure_ascii=False, sort_keys=True)
            )
        previous_outcomes = []
        for previous in previous_repair_results or []:
            if not isinstance(previous, dict):
                continue
            previous_outcomes.append(
                {
                    "job_id": str(previous.get("job_id") or ""),
                    "status": str(previous.get("status") or ""),
                    "changed_files": list(previous.get("changed_files") or []),
                }
            )
        if previous_outcomes:
            parts.append(
                "Most recent implementation repair outcomes (newest first):\n"
                + json.dumps(previous_outcomes, ensure_ascii=False, sort_keys=True)
                + "\nThese changes are already present and the failure evidence above still "
                "occurred. Do not repeat the same edit or merely report success. Trace the "
                "actual runtime response path, make a materially different correction, and "
                "verify the exact failing operation before finishing."
            )
        if older_repair_summaries:
            parts.append(
                "Older implementation repair outcomes, grouped by identical result:\n"
                + json.dumps(
                    older_repair_summaries,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        return "\n\n".join(parts)

    def _monitor_implementation(
        self,
        job: dict[str, Any],
        *,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        job_id = str(job["job_id"])
        app_id = str(job.get("app_id") or "")
        last_status: str | None = None
        last_progress: dict[str, str] = {}
        last_agent_results: dict[str, str] = {}
        while True:
            current = implementation_worker.get(job_id)
            status = str(current.get("status") or "")
            if app_id and command_id:
                if status and status != last_status:
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
                        str(update.get(field) or "") for field in ("status", "label", "detail")
                    )
                    if last_progress.get(step) == progress_key:
                        continue
                    repository.append_event(
                        app_id,
                        command_id=command_id,
                        stage="implementation",
                        kind="progress",
                        actor="system",
                        text=str(
                            update.get("detail")
                            or update.get("label")
                            or "Implementation is in progress."
                        ),
                        metadata={
                            "progress_event": "implementationStepUpdated",
                            "step": step,
                            "progress_step_label": str(update.get("label") or step),
                            "progress_card_label": str(
                                progress.get("progress_card_label") or "Implementation progress"
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
                for result in progress.get("agent_results", []) if progress else []:
                    if not isinstance(result, dict):
                        continue
                    task_id = str(result.get("task_id") or "")
                    if not task_id:
                        continue
                    fingerprint = stable_digest(result)
                    if last_agent_results.get(task_id) == fingerprint:
                        continue
                    raw_response = str(result.get("raw_response") or "").strip()
                    repository.append_event(
                        app_id,
                        command_id=command_id,
                        stage="implementation",
                        kind="progress",
                        actor="system",
                        text=raw_response or f"The result for {task_id} was recorded.",
                        metadata={
                            "progress_event": "implementationAgentResult",
                            **result,
                        },
                    )
                    last_agent_results[task_id] = fingerprint
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

    def _run_testing_command(
        self,
        command: dict[str, Any],
        implementation_job_id: str,
        *,
        previous_job: dict[str, Any] | None = None,
        preserve_test: bool = False,
    ) -> dict[str, Any]:
        """Testing을 실행하고 재시작 checkpoint를 현재 Workspace command에 저장한다."""

        command_id = str(command["command_id"])

        def save_checkpoint(checkpoint: dict[str, Any]) -> None:
            # Testing command와 checkpoint의 수명주기가 같으므로 기존 payload에 함께 저장한다.
            # 다른 command 입력은 그대로 보존한다.
            latest = repository.get_command(command_id)
            if latest is None:
                raise RuntimeError("The Workspace command disappeared during Testing.")
            payload = {
                **dict(latest.get("payload") or {}),
                "testing_checkpoint": checkpoint,
            }
            command["payload"] = payload
            repository.update_command(command_id, payload=payload)

        checkpoint = command.get("payload", {}).get("testing_checkpoint")
        job = run_testing(
            str(command["app_id"]),
            implementation_job_id,
            run_id=command_id,
            previous_job=previous_job,
            preserve_test=preserve_test,
            checkpoint=checkpoint if isinstance(checkpoint, dict) else None,
            progress=save_checkpoint,
        )
        return self._testing_result(job)

    @staticmethod
    def _testing_result(job: dict[str, Any]) -> dict[str, Any]:
        """동기 실행 결과를 기존 Workspace 응답 모양으로 바꾼다."""

        report = job.get("result") or {}
        job_id = str(job.get("job_id") or "")
        if report.get("passed") is False:
            blockers = list(report.get("blocking_findings") or [])
            repairable = any(
                blocker.get("repairable") is not False
                for blocker in blockers
                if isinstance(blocker, dict)
            )
            return {
                "awaiting_input": True,
                "kind": "action_required",
                "message": (
                    f"Testing found {len(blockers)} blocking failure(s). "
                    + (
                        "EasyDep classified the failures and will continue the "
                        "matching automatic repair path."
                        if repairable
                        else "The runtime environment must be restored before the "
                        "same checks can continue."
                    )
                ),
                "requires_revision": True,
                "blocking_findings": blockers,
                "repair_state": report.get("repair_state")
                or {
                    "status": "ACTIVE",
                    "attempt_count": 0,
                    "accepted_count": 0,
                    "recent_attempts": [],
                },
                "can_delegate_repair": repairable,
                "job_id": job_id,
                "job": job,
            }
        return {
            "message": "Testing completed.",
            "job_id": job_id,
            "job": job,
        }

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
