from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
import warnings
from pathlib import Path
from typing import Any

from app.config import settings
from app.llm_connection import LlmConnection
from app.llm_profiles import profile_for
from app.metrics import langsmith as langsmith_metrics

from ..runtime.linux_runner_transport import (
    LLM_CREDENTIAL_ENVIRONMENT,
    OWNER_NPM_CACHE,
    OWNER_TERMINAL_HOME,
    OWNER_TERMINAL_SHELL_ENV,
)
from ..workflows.repair import active_repair_for_task
from .provider import (
    configured_max_output_tokens,
    openhands_compatibility,
    openhands_connection,
)
from .task_check import (
    consume_successful_task_check,
    register_task_check_tool,
)
from .verification.build import (
    WorkspaceVerificationError,
    verify_agent_workspace,
)
from .verification.frontend import store_frontend_build
from .workspace import (
    changed_files,
    cleanup_agent_workspace,
    grant_owner_file_access,
    load_task,
    missing_required_outputs,
    path_is_editable,
    prepare_agent_workspace,
    snapshot_files,
)

# OpenHands owns the tool/action loop. This only bounds one task conversation.
MAX_AGENT_TURN_ITERATIONS = 32
# The failed real-app baseline spent 238 tool calls without completing after the
# useful first draft was already present around call 28.  A clean real-app run
# reached its first complete implementation at call 61, so 96 leaves one local
# build-and-repair pass without inheriting OpenHands' 500 iteration default.  A
# retry resumes the same persisted conversation.
OWNER_TURN_ITERATIONS = 96
OWNER_TASK_TYPES = frozenset({"backend-implementation", "frontend-implementation"})
OWNER_CONTINUATION_MESSAGE = (
    "Continue from the current candidate; do not restart repository discovery. Run the "
    "canonical verification command now, inspect only its concrete compiler or test failures, "
    "fix them, rerun verification, and call finish when it passes."
)
OWNER_STUCK_RECOVERY_MESSAGE = (
    "OpenHands detected a repeated-action loop. Continue in this same conversation with a "
    "different action. Use the absolute project path from the workspace facts, run the "
    "canonical verification command, and fix only its concrete failures."
)
_SANDBOX_TOOLS_REGISTERED = False
_SANDBOX_TOOLS_REGISTRATION_LOCK = threading.Lock()


class OwnerConversationIncomplete(WorkspaceVerificationError):
    """An owner stopped at an SDK execution boundary, not a source-code gate."""


def _is_owner_task(task_type: str) -> bool:
    return task_type in OWNER_TASK_TYPES


def _owner_conversation_identity(run_root: Path, task_id: str) -> tuple[Path, uuid.UUID]:
    """Return stable OpenHands persistence coordinates for one implementation owner."""

    try:
        job_id = run_root.parents[2].name
    except IndexError:
        job_id = run_root.parent.name
    conversation_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"easydep://implementation/{job_id}/{run_root.name}/{task_id}",
    )
    return run_root / "reports" / "openhands-conversations", conversation_id


def _owner_message_required(
    *,
    resumed: bool,
    conversation: object,
    prompt: str,
) -> bool:
    """Return whether the current task message is absent from persisted history."""

    if not resumed:
        return True
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import content_to_str

    events = conversation.state.events
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if not isinstance(event, MessageEvent) or event.source != "user":
            continue
        if "".join(content_to_str(event.llm_message.content)) == prompt:
            return False
    return True


def _owner_continuation_required(
    *,
    resumed: bool,
    conversation: object,
    prompt: str,
) -> bool:
    """Continue a completed owner turn whose deterministic verification failed."""

    if not resumed:
        return False
    from openhands.sdk.event import ActionEvent, MessageEvent
    from openhands.sdk.llm import content_to_str

    events = conversation.state.events
    matching_user_index: int | None = None
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if not isinstance(event, MessageEvent) or event.source != "user":
            continue
        if "".join(content_to_str(event.llm_message.content)) == prompt:
            matching_user_index = index
            break
    if matching_user_index is None:
        return False
    for index in range(matching_user_index + 1, len(events)):
        event = events[index]
        if isinstance(event, (ActionEvent, MessageEvent)) and event.source == "agent":
            return True
    return False


def _configure_openhands_profile_store() -> None:
    """Keep OpenHands' implicit profile lock out of the user's home directory.

    OpenHands' built-in vision/switch tools instantiate ``LLMProfileStore()``
    without a directory argument, which defaults to ``~/.openhands/profiles``.
    On Windows that directory can be owned by another server/elevation context,
    causing every agent to fail before it writes any task output.  A shared
    process-local temporary directory is writable and still allows concurrent
    tasks to coordinate through OpenHands' file lock.
    """
    from openhands.sdk.llm import llm_profile_store

    profile_dir = Path(tempfile.gettempdir()) / f"easydep-openhands-profiles-{os.getpid()}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.chmod(0o700)
    llm_profile_store._DEFAULT_PROFILE_DIR = profile_dir


class EventJournal:
    def __init__(self, path: Path):
        self.path = path
        self.event_count = 0
        self.tool_counts: dict[str, int] = {}
        self.latest_agent_message = ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def __call__(self, event) -> None:
        event_type = event.__class__.__name__
        tool_name = getattr(event, "tool_name", None)
        # SDK는 한 번의 도구 사용을 ActionEvent와 ObservationEvent 두 개로 남긴다.
        # 사용량에는 실제 요청인 ActionEvent만 세어 화면에 두 배로 보이지 않게 한다.
        if tool_name and event_type == "ActionEvent":
            self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1
        event_payload = event.model_dump(mode="json")
        payload = {
            "sequence": self.event_count,
            "timestamp": time.time(),
            "type": event_type,
            "source": getattr(event, "source", None),
            "tool": tool_name,
            "event": event_payload,
        }
        # Workspace 화면에는 숨겨진 reasoning이 아니라 모델이 사용자에게 반환한 마지막
        # assistant 텍스트만 보여 준다. 실행 중 한 번 저장해 두므로 진행 조회 때 큰 journal을
        # 매번 다시 읽지 않아도 된다.
        if event_type == "MessageEvent" and event_payload.get("source") == "agent":
            message = event_payload.get("llm_message")
            content = message.get("content") if isinstance(message, dict) else None
            text_parts = [
                str(item.get("text"))
                for item in content or []
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]
            if text_parts:
                self.latest_agent_message = "\n".join(text_parts)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.event_count += 1


class NoActionResponseGuard:
    """Close the SDK gap where corrective nudges hide repeated empty responses.

    OpenHands already classifies model responses and supplies the canonical stuck
    threshold.  EasyDep observes those typed events only; it does not inspect model
    text or provider error strings.
    """

    def __init__(self) -> None:
        from openhands.sdk.conversation.types import StuckDetectionThresholds

        self.threshold = StuckDetectionThresholds().monologue
        self.consecutive_count = 0
        self.max_consecutive_count = 0
        self.triggered = False
        self._conversation: object | None = None

    def bind(self, conversation: object) -> None:
        self._conversation = conversation

    def reset(self) -> None:
        self.consecutive_count = 0
        self.triggered = False

    def __call__(self, event: object) -> None:
        from openhands.sdk.agent.response_dispatch import (
            LLMResponseType,
            classify_response,
        )
        from openhands.sdk.conversation.state import ConversationExecutionStatus
        from openhands.sdk.event import ActionEvent, MessageEvent

        if isinstance(event, ActionEvent) and event.source == "agent":
            self.consecutive_count = 0
            return
        if not isinstance(event, MessageEvent) or event.source != "agent":
            return
        response_type = classify_response(event.llm_message)
        if response_type not in {
            LLMResponseType.EMPTY,
            LLMResponseType.REASONING_ONLY,
        }:
            self.consecutive_count = 0
            return
        self.consecutive_count += 1
        self.max_consecutive_count = max(
            self.max_consecutive_count,
            self.consecutive_count,
        )
        if self.consecutive_count < self.threshold or self._conversation is None:
            return
        self.triggered = True
        self._conversation.state.execution_status = ConversationExecutionStatus.STUCK


def _owner_workspace_guidance(
    task_type: str,
    sandbox: Path,
    owner_roots: list[str],
) -> str:
    """Return stable runner facts, not implementation instructions."""

    workspace = sandbox.resolve()
    common = [
        "## EasyDep implementation workspace",
        "",
        f"- Workspace and terminal starting directory: `{sandbox.resolve()}`",
        "- The terminal session preserves `cd` and environment changes between calls.",
        "- Use `file_editor` for source edits and `terminal` for inspection, search, build, and tests.",
        "- Source locations and RTM references are investigation hints, not a required edit list.",
        "- Candidate contract copies may be inspected, but promotion rejects changes to generated contracts.",
        "- Start from generated skeletons and their local context; open raw design inputs only for a concrete contract gap.",
        "- Batch related source reads into as few terminal calls as practical, and use build/test results rather than file counts as completion evidence.",
        "- After an edit batch, run the canonical verification once. If it fails, inspect that output and its existing diagnostic files before rerunning; do not rerun only to obtain more detail.",
        "- When canonical verification passes, finish immediately. Do not disable tests or alter test reporting to hide a failure.",
        "- Prefer the lowest-cost test level that proves the behavior; avoid restarting a full application context for every assertion.",
        "- Use English for source comments and user-visible text.",
    ]
    if task_type == "backend-implementation":
        common.extend(
            [
                "- Backend project root: `application`.",
                "- Gradle is installed as `gradle`; this project has no Gradle wrapper.",
                f"- Canonical backend verification: `cd {workspace / 'application'} && gradle test --build-cache`.",
                "- The terminal exports `SPRING_PROFILES_ACTIVE=test` and Gradle uses the shared `GRADLE_USER_HOME` cache.",
            ]
        )
    elif task_type == "frontend-implementation":
        common.extend(
            [
                "- Frontend project root: `application/frontend`.",
                "- If dependencies are absent, run `npm ci --ignore-scripts --no-audit --no-fund --prefer-offline` once.",
                f"- Canonical frontend verification: `cd {workspace / 'application' / 'frontend'} && npm run build`.",
                f"- npm uses the shared cache at `{OWNER_NPM_CACHE}`.",
            ]
        )
    common.extend(["", "Owner source roots:"])
    common.extend(f"- `{root}`" for root in owner_roots)
    if not owner_roots:
        common.append("- none")
    return "\n".join(common)


def write_execution_plan(
    run_root: Path,
    tasks: list[dict[str, object]],
    requested_mode: str,
) -> dict[str, object]:
    connection = openhands_connection()
    compatibility = openhands_compatibility(connection)
    plan = {
        "schemaVersion": "openhands-execution-plan/v1alpha1",
        "mode": requested_mode,
        "runnable": all(
            bool(compatibility[key])
            for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled", "apiKeyConfigured")
        ),
        "compatibility": compatibility,
        "llm": {
            "provider": connection.provider,
            "model": connection.model,
            "baseUrl": connection.base_url,
        },
        "taskOrder": [task["task_id"] for task in tasks],
        "isolation": "copy source-only application to an ASCII temp workspace, edit only assigned implementation paths, run focused checks inside OpenHands, protect generated contracts, promote verified files only",
    }
    target = run_root / "reports" / "agent-execution-plan.json"
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def execute_openhands_task(run_root: Path, task_id: str) -> dict[str, object]:
    """Execute one implementation agent task and publish only safe task metrics."""

    app_id = _run_app_id(run_root)
    with langsmith_metrics.trace_scope(
        "easydep.implementation.openhands_task",
        metadata={
            "agent": "implementation",
            "operation": "openhands_task",
            "run_id": run_root.name,
            "task_id": task_id,
            "app_id": app_id,
        },
    ):
        return _execute_openhands_task(run_root, task_id)


def _promote_changed_files(sandbox: Path, run_root: Path, changed: set[str]) -> None:
    """검증된 candidate manifest를 run으로 옮긴다.

    run 폴더는 Windows host와 Linux toolchain 사이의 공유 경로일 수 있다. ``copy2``는
    내용 뒤에 Linux 권한과 시간 정보까지 쓰려 하므로 정상적으로 복사한 뒤에도 EPERM을
    낼 수 있다. 생성 source 계약에는 파일 내용만 필요하므로 metadata를 복사하지 않는다.
    """
    for relative in sorted(changed):
        source = sandbox / relative
        target = run_root / relative
        if not source.is_file():
            # Deletion is part of the verified candidate manifest.  Silently
            # retaining the accepted copy would publish a tree different from
            # the one that passed verification.
            if target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _candidate_application_changes(sandbox: Path, run_root: Path) -> set[str]:
    """Return every source add, modification, and deletion in the candidate."""

    return {
        f"application/{path}"
        for path in changed_files(
            snapshot_files(run_root / "application"),
            snapshot_files(sandbox / "application"),
        )
    }


def _owned_directory_roots(paths: list[str]) -> list[str]:
    """명시된 wiring 파일과 같은 패키지에는 새 구현 파일을 만들 수 있게 한다.

    전체 ``main/java``가 아니라 이미 작업에 배정된 파일의 바로 위 디렉터리만 연다.
    따라서 OpenHands는 같은 ``config`` 패키지에서 Security 설정을 별도 클래스로 만들지,
    기존 설정 클래스에 합칠지 스스로 고를 수 있다.
    """
    return sorted(
        {
            Path(path.replace("\\", "/")).parent.as_posix()
            for path in paths
            if path.startswith("application/")
        }
    )


def _active_repair_scope(
    task: dict[str, object], active_repair: dict[str, object]
) -> tuple[list[str], list[str], list[str]]:
    """통합 수리에 필요한 정확한 파일만 일시적으로 편집 가능하게 만든다.

    wiring 작업은 평소 업무 코드를 건드리지 못한다. 다만 최종 검사에서 서로 다른 기능의
    파일이 함께 실패하면 수리 계획이 그 파일들을 wiring에 배정할 수 있다. 이때 기존
    ``immutable_paths``를 그대로 적용하면 계획에는 파일이 보이지만 편집 도구가 다시 막는
    모순이 생긴다.

    공개 BCE/API 계약과 결정론적으로 만든 persistence 파일은 계속 보호한다. 단일 기능
    수리는 원래 작업의 관련 파일과 전용 디렉터리를 유지하고, 여러 기능을 잇는 wiring 수리는
    오류에서 확인한 ``repairPaths``만 추가한다.
    """
    base_paths = [str(path).replace("\\", "/") for path in task.get("allowed_write_paths", [])]
    base_roots = [
        str(path).replace("\\", "/")
        for path in task.get("allowed_write_roots", [])
    ]
    immutable = {
        str(path).replace("\\", "/") for path in task.get("immutable_paths", [])
    }
    # Owner repair paths are RTM/failure navigation hints. The backend and
    # frontend owners already have broad source roots, so repair evidence must
    # never grant new write authority or unfreeze a generated contract.
    if _is_owner_task(str(task.get("task_type", ""))):
        return base_paths, base_roots, sorted(immutable)
    requested_paths = [
        str(path).replace("\\", "/")
        for path in active_repair.get("repairPaths", [])
        if isinstance(path, str) and path.startswith("application/")
    ]
    protected_parts = ("/api/", "/persistence/")
    protected_prefixes = ("application/src/main/resources/db/migration/",)
    repair_paths = [
        path
        for path in requested_paths
        if not any(part in "/" + path for part in protected_parts)
        and not path.startswith(protected_prefixes)
        # 기능 작업이 원래 소유한 Entity 본문은 고칠 수 있다. 반면 wiring이 공개 BCE
        # 계약을 새로 소유하게 만들지는 않는다.
        and ("/bce/" not in "/" + path or path in base_paths)
    ]
    # 한 기능이 원래 소유한 관련 파일은 함께 열어 두어 test 실패를 Service나 Entity에서
    # 고칠 수 있게 한다. 여러 기능을 합치는 wiring 수리는 repair plan이 실제 오류 파일만
    # 추가하므로 main/java 전체로 넓어지지 않는다.
    editable = list(dict.fromkeys([*base_paths, *repair_paths]))
    # exact repair 파일 위에 놓인 넓은 ownership 경로만 해제한다. 편집기 자체는 editable
    # 파일 목록을 다시 검사하므로 같은 package의 관련 없는 기존 파일까지 열리지 않는다.
    immutable = {
        path
        for path in immutable
        if not any(_path_is_immutable(repair_path, {path}) for repair_path in repair_paths)
    }
    roots = base_roots
    if str(task.get("task_type")) == "wiring":
        roots = _owned_directory_roots(base_paths)
    return editable, roots, sorted(immutable)


def _task_execution_scope(
    task: dict[str, object], active_repair: dict[str, object] | None
) -> tuple[list[str], list[str], list[str]]:
    """사전 점검과 실제 실행이 함께 사용할 편집 범위를 계산한다."""

    if active_repair is not None:
        return _active_repair_scope(task, active_repair)
    return (
        [str(path).replace("\\", "/") for path in task.get("allowed_write_paths", [])],
        [str(path).replace("\\", "/") for path in task.get("allowed_write_roots", [])],
        sorted(
            str(path).replace("\\", "/")
            for path in task.get("immutable_paths", [])
        ),
    )


def _execute_openhands_task(run_root: Path, task_id: str) -> dict[str, object]:
    """Run one OpenHands conversation and keep EasyDep at the safety boundary."""

    task = load_task(run_root, task_id)
    task_type = str(task.get("task_type", ""))
    owner_task = _is_owner_task(task_type)
    if owner_task and os.environ.get("EASYDEP_FIXED_LINUX_RUNNER") != "1":
        raise RuntimeError(
            "Autonomous OpenHands owners require the isolated EasyDep Linux runner."
        )
    active_repair = active_repair_for_task(run_root, task_id)
    editable_paths, editable_roots, immutable = _task_execution_scope(task, active_repair)
    required_paths = [str(path) for path in task.get("required_output_paths", editable_paths)]
    task = {
        **task,
        "allowed_write_paths": editable_paths,
        "allowed_write_roots": editable_roots,
        "immutable_paths": immutable,
    }
    connection = openhands_connection()
    compatibility = openhands_compatibility(connection)
    missing = [
        key
        for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled", "apiKeyConfigured")
        if not compatibility[key]
    ]
    if missing:
        raise RuntimeError("OpenHands live mode prerequisites are missing: " + ", ".join(missing))

    sandbox = prepare_agent_workspace(
        run_root,
        task,
        preserve_failed_edits=True,
        persistent=owner_task,
    )
    before = snapshot_files(sandbox)
    prompt_file = task.get("repair_prompt_file") if active_repair is not None else task.get("prompt_file")
    if not isinstance(prompt_file, str) or not (run_root / prompt_file).is_file():
        prompt_file = str(task["prompt_file"])
    prompt = (run_root / prompt_file).read_text(encoding="utf-8")
    context = json.loads((run_root / task["context_file"]).read_text(encoding="utf-8"))
    verification_profile = task.get("verification_profile")
    verification_profile = (
        dict(verification_profile)
        if isinstance(verification_profile, dict) and verification_profile
        else None
    )
    sandbox_root = sandbox.resolve()
    read_hints = [
        str((sandbox / value).resolve())
        for value in context.get("readSourcePaths", [])
        if isinstance(value, str)
        and (sandbox / value).resolve().is_relative_to(sandbox_root)
        and (sandbox / value).exists()
    ]
    writable_files = [str((sandbox / path).resolve()) for path in editable_paths]
    writable_roots = [str((sandbox / root).resolve()) for root in editable_roots]
    immutable_absolute = [str((sandbox / path).resolve()) for path in immutable]
    if owner_task:
        prompt += "\n\n" + _owner_workspace_guidance(
            task_type,
            sandbox,
            editable_roots,
        )
    else:
        prompt += (
            "\n\n## EasyDep task constraints\n\n"
            "Use the provided source locations as investigation hints, not edit limits. "
            "Keep generated contracts unchanged. Work only inside the sandbox and writable roots. "
            "Run run_task_check until it passes before finish. Use English for source comments "
            "and user-visible text.\n\nWritable task files:\n"
            + ("\n".join(f"- `{path}`" for path in writable_files) or "- none")
            + "\n\nAdditional writable roots:\n"
            + ("\n".join(f"- `{path}`" for path in writable_roots) or "- none")
        )
    if immutable_absolute and not owner_task:
        prompt += (
            "\n\nGenerated contracts are readable but write-protected by the sandbox. "
            "Inspect them on demand instead of copying their contents into the conversation."
        )
    if read_hints and not owner_task:
        prompt += "\n\nSuggested source hints:\n" + "\n".join(
            f"- `{path}`" for path in read_hints
        )

    execution_dir = run_root / "reports" / "agent-executions"
    attempt = execution_attempt(run_root, task_id)
    journal = EventJournal(execution_dir / f"{task_id}.attempt-{attempt:03d}.events.jsonl")
    no_action_guard = NoActionResponseGuard() if owner_task else None
    stuck_recovery_used = False
    started = time.monotonic()
    conversation = None
    agent = None
    persistence_dir: Path | None = None
    conversation_id: uuid.UUID | None = None
    resumed_conversation = False
    if owner_task:
        persistence_dir, conversation_id = _owner_conversation_identity(run_root, task_id)
        resumed_conversation = (
            persistence_dir / conversation_id.hex / "base_state.json"
        ).is_file()
    try:
        reasoning_effort = os.environ.get(
            "OPENHANDS_REASONING_EFFORT",
            str(task["llm"].get("reasoningEffort", settings.implementation_reasoning_effort)),
        )
        conversation, agent = create_openhands_conversation(
            sandbox,
            connection,
            task["llm"],
            task_type=task_type,
            verification_paths=editable_paths,
            verification_profile=verification_profile,
            editable_files=writable_files,
            editable_roots=writable_roots,
            immutable_paths=immutable_absolute,
            callbacks=[journal, *([no_action_guard] if no_action_guard else [])],
            max_iterations=(
                OWNER_TURN_ITERATIONS if owner_task else MAX_AGENT_TURN_ITERATIONS
            ),
            reasoning_effort=reasoning_effort,
            native_owner_tools=owner_task,
            enable_native_terminal=owner_task,
            persistence_dir=persistence_dir,
            conversation_id=conversation_id,
        )
        if no_action_guard is not None:
            no_action_guard.bind(conversation)
        # The SDK loads persisted events when the stable conversation exists.
        # Compare the exact user message in that public event history: a new
        # repair is appended, while a crash after event persistence resumes
        # without duplicating the potentially large task message.
        message_required = _owner_message_required(
            resumed=resumed_conversation,
            conversation=conversation,
            prompt=prompt,
        )
        if message_required:
            conversation.send_message(prompt)
        elif _owner_continuation_required(
            resumed=resumed_conversation,
            conversation=conversation,
            prompt=prompt,
        ):
            conversation.send_message(OWNER_CONTINUATION_MESSAGE)
        conversation.run()
        if owner_task and _conversation_is_stuck(conversation):
            stuck_recovery_used = True
            if no_action_guard is not None:
                no_action_guard.reset()
            conversation.send_message(OWNER_STUCK_RECOVERY_MESSAGE)
            conversation.run()
        if _conversation_terminal_failure(conversation):
            raise OwnerConversationIncomplete(
                {
                    "command": ["openhands", "conversation"],
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": journal.latest_agent_message or "OpenHands conversation did not finish.",
                    "testResults": "",
                }
            )
        missing_outputs = missing_required_outputs(sandbox, required_paths)
        if missing_outputs:
            raise WorkspaceVerificationError(
                {
                    "command": ["required-task-outputs"],
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": "Missing required outputs: " + ", ".join(missing_outputs),
                    "testResults": "",
                }
            )
        attempt_changes = changed_files(before, snapshot_files(sandbox))
        candidate_changes = _candidate_application_changes(sandbox, run_root)
        unauthorized = sorted(
            {
                path
                for path in candidate_changes
                if not path_is_editable(path, editable_paths, editable_roots, immutable)
            }
            | {
                path
                for path in attempt_changes
                if not path.startswith("application/")
            }
        )
        if unauthorized:
            raise WorkspaceVerificationError(
                {
                    "command": ["implementation-promotion-boundary"],
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": "Candidate changes outside the owner's promotion boundary: "
                    + ", ".join(unauthorized),
                    "testResults": "",
                    "unauthorizedChanges": unauthorized,
                }
            )
        verification = (
            None
            if owner_task
            else consume_successful_task_check(
                sandbox, task_type, editable_paths, verification_profile
            )
        ) or verify_agent_workspace(sandbox, task_type, editable_paths, verification_profile)
    except Exception as error:
        if conversation is not None:
            conversation.close()
        failure = {
            "taskId": task_id,
            "taskType": task_type,
            "owner": str(task.get("owner") or ""),
            "promptSha256": task.get("prompt_sha256"),
            "status": "FAILED",
            "effectiveModel": connection.litellm_model(),
            "errorType": error.__class__.__name__,
            "error": str(error),
            "durationMs": int((time.monotonic() - started) * 1000),
            "eventCount": journal.event_count,
            "toolCounts": journal.tool_counts,
            "eventJournal": str(journal.path.relative_to(run_root)).replace("\\", "/"),
            "rawResponse": journal.latest_agent_message,
            "conversationId": str(conversation_id) if conversation_id else None,
            "conversationCheckpoint": (
                str(persistence_dir.relative_to(run_root)).replace("\\", "/")
                if persistence_dir is not None
                else None
            ),
            "resumedConversation": resumed_conversation,
            "executionStatus": _conversation_execution_status(conversation),
            "terminationReason": (
                "consecutive_no_action_responses"
                if no_action_guard is not None and no_action_guard.triggered
                else None
            ),
            "maxConsecutiveNoActionResponses": (
                no_action_guard.max_consecutive_count
                if no_action_guard is not None
                else 0
            ),
            "stuckRecoveryUsed": stuck_recovery_used,
        }
        if isinstance(error, WorkspaceVerificationError):
            failure["verificationEvidence"] = error.evidence
        failure["conversationStats"] = _conversation_stats_snapshot(conversation)
        write_execution_result(execution_dir, task_id, attempt, failure)
        shutil.copyfile(journal.path, execution_dir / f"{task_id}.events.jsonl")
        raise
    conversation.close()
    changed = candidate_changes
    promoted_files = changed | {path for path in required_paths if (sandbox / path).is_file()}
    _promote_changed_files(sandbox, run_root, promoted_files)
    if task_type == "frontend-implementation":
        store_frontend_build(run_root, sandbox, verification)
    result = {
        "taskId": task_id,
        "taskType": task_type,
        "owner": str(task.get("owner") or ""),
        "promptSha256": task.get("prompt_sha256"),
        "effectiveModel": connection.litellm_model(),
        "changedFiles": sorted(changed),
        "outputFiles": required_paths,
        "verification": verification,
        "tools": sorted(agent._tools) if agent is not None else [],
        "durationMs": int((time.monotonic() - started) * 1000),
        "eventCount": journal.event_count,
        "toolCounts": journal.tool_counts,
        "eventJournal": str(journal.path.relative_to(run_root)).replace("\\", "/"),
        "rawResponse": journal.latest_agent_message,
        "conversationId": str(conversation_id) if conversation_id else None,
        "conversationCheckpoint": (
            str(persistence_dir.relative_to(run_root)).replace("\\", "/")
            if persistence_dir is not None
            else None
        ),
        "resumedConversation": resumed_conversation,
        "executionStatus": _conversation_execution_status(conversation),
        "terminationReason": None,
        "maxConsecutiveNoActionResponses": (
            no_action_guard.max_consecutive_count if no_action_guard is not None else 0
        ),
        "stuckRecoveryUsed": stuck_recovery_used,
        "conversationStats": _conversation_stats_snapshot(conversation),
        "status": "SUCCEEDED",
    }
    write_execution_result(execution_dir, task_id, attempt, result)
    shutil.copyfile(journal.path, execution_dir / f"{task_id}.events.jsonl")
    cleanup_agent_workspace(sandbox, run_root=run_root if owner_task else None)
    return result


def _conversation_execution_status(conversation: object | None) -> str | None:
    if conversation is None:
        return None
    status = getattr(getattr(conversation, "state", None), "execution_status", None)
    value = getattr(status, "value", None)
    return str(value) if value is not None else None


def _conversation_is_stuck(conversation: object) -> bool:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    return (
        getattr(getattr(conversation, "state", None), "execution_status", None)
        is ConversationExecutionStatus.STUCK
    )


def _conversation_terminal_failure(conversation: object) -> bool:
    """OpenHands owns recovery; EasyDep only consumes its typed terminal state."""

    from openhands.sdk.conversation.state import ConversationExecutionStatus

    state = getattr(conversation, "state", None)
    if state is None:
        return False
    status = getattr(state, "execution_status", None)
    if status is None:
        return False
    if not isinstance(status, ConversationExecutionStatus):
        raise TypeError("OpenHands conversation returned an untyped execution status")
    return status is not ConversationExecutionStatus.FINISHED


def _tool_validation_message(error_text: str) -> str | None:
    """Extract the provider's structured tool-validation message without its envelope."""

    marker = "Error code: 400 - "
    _, found, encoded_payload = error_text.partition(marker)
    if not found:
        return None
    payload: object
    try:
        payload = json.loads(encoded_payload)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(encoded_payload)
        except (SyntaxError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return None
    for item in errors:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if isinstance(message, str) and "tool call validation failed" in message.casefold():
            return message.strip()
    return None


def _conversation_stats_snapshot(conversation: object | None) -> dict[str, object] | None:
    """Persist OpenHands' own metrics without inventing unavailable values."""

    stats = getattr(conversation, "conversation_stats", None)
    if stats is None:
        return None
    try:
        snapshot = stats.model_dump(mode="json", context={"use_snapshot": True})
    except (AttributeError, TypeError, ValueError):
        try:
            snapshot = stats.model_dump()
        except (AttributeError, TypeError, ValueError):
            return None
    return snapshot if isinstance(snapshot, dict) else None


def _register_native_llm_usage(conversation: object, agent: object) -> None:
    """Register EasyDep LLM subclasses with OpenHands' native metrics registry.

    OpenHands SDK 1.36 only discovers objects whose concrete type is its base
    ``LLM`` class. EasyDep's provider-error adapter subclasses that class, so
    register the existing SDK LLMs explicitly instead of duplicating token
    accounting outside OpenHands.
    """

    registry = getattr(conversation, "llm_registry", None)
    stats = getattr(conversation, "conversation_stats", None)
    subscribe = getattr(registry, "subscribe", None)
    add = getattr(registry, "add", None)
    list_usage_ids = getattr(registry, "list_usage_ids", None)
    register_llm = getattr(stats, "register_llm", None)
    if not all(callable(item) for item in (subscribe, add, list_usage_ids, register_llm)):
        return

    subscribe(register_llm)
    registered = set(list_usage_ids())
    condenser = getattr(agent, "condenser", None)
    for llm in (getattr(agent, "llm", None), getattr(condenser, "llm", None)):
        usage_id = getattr(llm, "usage_id", None)
        if not isinstance(usage_id, str) or not usage_id or usage_id in registered:
            continue
        add(llm)
        registered.add(usage_id)


def _run_app_id(run_root: Path) -> str | None:
    """구현 실행에 저장된 변경되지 않는 앱 ID를 읽는다."""

    try:
        manifest = json.loads(
            (run_root / "reports" / "run-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    app_id = manifest.get("app_id")
    return str(app_id) if app_id else None


def execution_attempt(run_root: Path, task_id: str) -> int:
    state_path = run_root / "reports" / "workflow-state.json"
    if not state_path.is_file():
        return 1
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    return max(
        1,
        next(
            (
                int(task.get("attempts", 1))
                for task in state.get("tasks", [])
                if isinstance(task, dict) and task.get("task_id") == task_id
            ),
            1,
        ),
    )


def write_execution_result(
    execution_dir: Path,
    task_id: str,
    attempt: int,
    result: dict[str, object],
) -> None:
    content = json.dumps(result, ensure_ascii=False, indent=2)
    (execution_dir / f"{task_id}.attempt-{attempt:03d}.result.json").write_text(
        content, encoding="utf-8"
    )
    # Keep the stable path as a latest-result compatibility pointer/copy.
    (execution_dir / f"{task_id}.result.json").write_text(content, encoding="utf-8")


def create_openhands_conversation(
    sandbox: Path,
    connection: LlmConnection,
    llm_config: dict[str, object],
    *,
    task_type: str = "",
    verification_paths: list[str] | None = None,
    verification_profile: dict[str, object] | None = None,
    editable_files: list[str] | None = None,
    editable_roots: list[str] | None = None,
    immutable_paths: list[str] | None = None,
    callbacks: list[object] | None = None,
    max_iterations: int = MAX_AGENT_TURN_ITERATIONS,
    reasoning_effort: str = "medium",
    native_owner_tools: bool = False,
    enable_native_terminal: bool = False,
    persistence_dir: Path | None = None,
    conversation_id: uuid.UUID | None = None,
):
    global _SANDBOX_TOOLS_REGISTERED

    from openhands.sdk import LLM, Agent, Conversation, Tool, register_tool
    from openhands.sdk.context.condenser import default_condenser
    from openhands.sdk.llm.exceptions import (
        FunctionCallValidationError,
        LLMBadRequestError,
    )
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from openhands.tools.grep import GrepObservation, GrepTool
    from openhands.tools.grep.impl import GrepExecutor
    from openhands.tools.terminal import TerminalTool
    from pydantic import SecretStr

    _configure_openhands_profile_store()
    owner_terminal_shell: str | None = None
    if enable_native_terminal:
        owner_terminal_shell = os.environ.get(OWNER_TERMINAL_SHELL_ENV, "").strip()
        if not owner_terminal_shell:
            raise RuntimeError(
                f"Autonomous OpenHands terminal requires {OWNER_TERMINAL_SHELL_ENV}."
            )
        exposed_credentials = [
            name for name in LLM_CREDENTIAL_ENVIRONMENT if os.environ.get(name)
        ]
        if exposed_credentials:
            raise RuntimeError(
                "OpenHands terminal credential environment was not scrubbed: "
                + ", ".join(exposed_credentials)
            )

    def raise_provider_tool_validation(error: LLMBadRequestError) -> None:
        message = _tool_validation_message(str(error))
        if message is not None:
            raise FunctionCallValidationError(message) from error

    class ProviderToolValidationLLM(LLM):
        """Route provider-side validation 400s into OpenHands' native recovery."""

        def _handle_error(self, error, fallback_call_fn):
            try:
                return super()._handle_error(error, fallback_call_fn)
            except LLMBadRequestError as mapped_error:
                raise_provider_tool_validation(mapped_error)
                raise

        async def _ahandle_error(self, error, fallback_call_fn):
            try:
                return await super()._ahandle_error(error, fallback_call_fn)
            except LLMBadRequestError as mapped_error:
                raise_provider_tool_validation(mapped_error)
                raise

    class SandboxFileEditorExecutor(FileEditorExecutor):
        """Apply only EasyDep's filesystem boundary to the canonical editor."""

        def __init__(
            self,
            workspace_root: str,
            writable_files: list[str],
            writable_roots: list[str],
            immutable: list[str],
            enforce_write_scope: bool,
        ):
            super().__init__(workspace_root=workspace_root)
            self.workspace_root = Path(workspace_root).resolve()
            self.writable_files = {Path(path).resolve() for path in writable_files}
            self.writable_roots = {Path(path).resolve() for path in writable_roots}
            self.immutable = {Path(path).resolve() for path in immutable}
            self.enforce_write_scope = enforce_write_scope

        def __call__(self, action, conversation=None):
            supplied = Path(action.path)
            target = (
                supplied.resolve()
                if supplied.is_absolute()
                else (self.workspace_root / supplied).resolve()
            )
            try:
                target.relative_to(self.workspace_root)
            except ValueError:
                return FileEditorObservation.from_text(
                    text=f"Path is outside the assigned workspace: {target}",
                    command=action.command,
                    is_error=True,
                )
            if action.command != "view" and self.enforce_write_scope:
                if any(target == path or path in target.parents for path in self.immutable):
                    return FileEditorObservation.from_text(
                        text=f"Generated contract is read-only: {target}",
                        command=action.command,
                        is_error=True,
                    )
                if target not in self.writable_files and not any(
                    target == root or root in target.parents for root in self.writable_roots
                ):
                    return FileEditorObservation.from_text(
                        text=f"Write is outside the assigned implementation roots: {target}",
                        command=action.command,
                        is_error=True,
                    )
            observation = super().__call__(action, conversation)
            if action.command != "view" and not getattr(observation, "is_error", False):
                grant_owner_file_access(target, self.workspace_root)
            return observation

    class SandboxFileEditorTool(FileEditorTool):
        name = "file_editor"

        @classmethod
        def create(
            cls,
            conv_state,
            writable_files,
            writable_roots,
            immutable_paths,
            enforce_write_scope,
        ):
            return [
                instance.model_copy(
                    update={
                        "executor": SandboxFileEditorExecutor(
                            conv_state.workspace.working_dir,
                            writable_files,
                            writable_roots,
                            immutable_paths,
                            enforce_write_scope,
                        )
                    }
                )
                for instance in super().create(conv_state)
            ]

    class SandboxGrepExecutor(GrepExecutor):
        def __call__(self, action, conversation=None):
            supplied = Path(action.path) if action.path else None
            target = (
                supplied.resolve()
                if supplied is not None and supplied.is_absolute()
                else (self.working_dir / supplied).resolve()
                if supplied is not None
                else self.working_dir
            )
            try:
                target.relative_to(self.working_dir)
            except ValueError:
                return GrepObservation.from_text(
                    text=f"Search path is outside the assigned workspace: {target}",
                    matches=[],
                    pattern=action.pattern,
                    search_path=str(target),
                    include_pattern=action.include,
                    is_error=True,
                )
            return super().__call__(action, conversation)

    class SandboxGrepTool(GrepTool):
        name = "grep"

        @classmethod
        def create(cls, conv_state):
            return [
                instance.model_copy(
                    update={"executor": SandboxGrepExecutor(conv_state.workspace.working_dir)}
                )
                for instance in super().create(conv_state)
            ]

    editor_registry_name = "easydep_sandbox_file_editor"
    grep_registry_name = "easydep_sandbox_grep"
    if not _SANDBOX_TOOLS_REGISTERED:
        with _SANDBOX_TOOLS_REGISTRATION_LOCK:
            if not _SANDBOX_TOOLS_REGISTERED:
                register_tool(editor_registry_name, SandboxFileEditorTool)
                register_tool(grep_registry_name, SandboxGrepTool)
                _SANDBOX_TOOLS_REGISTERED = True
    model = connection.litellm_model()
    raw_temperature = llm_config["temperature"]
    raw_max_output = llm_config["maxOutputTokens"]
    if not isinstance(raw_temperature, (int, float, str)):
        raise TypeError("implementation LLM temperature must be numeric")
    if not isinstance(raw_max_output, (int, str)):
        raise TypeError("implementation LLM maxOutputTokens must be an integer")
    profile = profile_for(
        connection.model,
        fallback_temperature=float(raw_temperature),
        fallback_max_tokens=int(raw_max_output),
    )
    llm_options: dict[str, Any] = {
        "model": model,
        "usage_id": "implementation_agent",
        "api_key": SecretStr(connection.api_key),
        "base_url": connection.base_url,
        "extra_headers": connection.default_headers(),
        "temperature": profile.temperature,
        "max_output_tokens": profile.completion_limit(
            configured_max_output_tokens(int(raw_max_output))
        ),
    }
    llm_options.update(connection.openhands_options())
    if profile.top_p is not None:
        llm_options["top_p"] = profile.top_p
    if resolved_reasoning := profile.resolve_reasoning(reasoning_effort):
        llm_options["reasoning_effort"] = resolved_reasoning
    if extra_body := profile.extra_body(connection.provider):
        llm_options["litellm_extra_body"] = extra_body
    warnings.filterwarnings(
        "ignore",
        message=r"Cost calculation failed:.*",
        module=r"openhands\.sdk\.llm\.utils\.telemetry",
    )
    llm = ProviderToolValidationLLM(**llm_options)
    if native_owner_tools:
        tools = [
            Tool(
                name=editor_registry_name,
                params={
                    "writable_files": editable_files or [],
                    "writable_roots": editable_roots or [],
                    "immutable_paths": immutable_paths or [],
                    "enforce_write_scope": False,
                },
            ),
        ]
        if enable_native_terminal:
            tools.append(
                Tool(
                    name=TerminalTool.name,
                    params={
                        "terminal_type": "subprocess",
                        "shell_path": owner_terminal_shell,
                        "env": {
                            "HOME": OWNER_TERMINAL_HOME,
                            "npm_config_cache": OWNER_NPM_CACHE,
                        },
                    },
                )
            )
    else:
        task_check_tool_name = register_task_check_tool()
        tools = [
            Tool(
                name=editor_registry_name,
                params={
                    "writable_files": editable_files or [],
                    "writable_roots": editable_roots or [],
                    "immutable_paths": immutable_paths or [],
                    "enforce_write_scope": True,
                },
            ),
            Tool(name=grep_registry_name, params={}),
            Tool(
                name=task_check_tool_name,
                params={
                    "task_type": task_type,
                    "allowed_write_paths": verification_paths or [],
                    "verification_profile": verification_profile or {},
                },
            ),
        ]
    agent = Agent(
        llm=llm,
        tools=tools,
        include_default_tools=["FinishTool"],
        # Do not override OpenHands' built-in system behavior. EasyDep's
        # task-specific constraints are appended to the user task message.
        condenser=default_condenser(
            llm=llm.model_copy(update={"usage_id": "implementation_condenser"}),
        ),
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(sandbox),
        callbacks=callbacks,
        max_iteration_per_run=max_iterations,
        stuck_detection=True,
        visualizer=None,
        persistence_dir=persistence_dir,
        conversation_id=conversation_id,
        delete_on_close=False,
    )
    _register_native_llm_usage(conversation, conversation.agent)
    return conversation, conversation.agent


def _path_is_immutable(path: str, immutable_paths: set[str]) -> bool:
    """파일 경로가 생성 계약 파일 또는 그 하위에 있는지 확인한다."""
    normalized = path.replace("\\", "/").rstrip("/")
    return any(
        normalized == root.rstrip("/") or normalized.startswith(root.rstrip("/") + "/")
        for root in immutable_paths
    )
