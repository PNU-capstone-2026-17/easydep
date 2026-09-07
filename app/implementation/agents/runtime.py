from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.config import settings
from app.llm_connection import LlmConnection
from app.llm_profiles import profile_for
from app.metrics import langsmith as langsmith_metrics

from ..workflows.repair import active_repair_for_task
from .provider import (
    configured_max_output_tokens,
    openhands_compatibility,
    openhands_connection,
)
from .task_check import (
    TASK_CHECK_TOOL_NAME,
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
    load_task,
    missing_required_outputs,
    path_is_editable,
    prepare_agent_workspace,
    snapshot_files,
)

# OpenHands owns the tool/action loop. This only bounds one task conversation.
MAX_AGENT_TURN_ITERATIONS = 32
_SANDBOX_TOOLS_REGISTERED = False
_SANDBOX_TOOLS_REGISTRATION_LOCK = threading.Lock()


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
    """검증된 source 내용만 run으로 옮긴다.

    run 폴더는 Windows host와 Linux toolchain 사이의 공유 경로일 수 있다. ``copy2``는
    내용 뒤에 Linux 권한과 시간 정보까지 쓰려 하므로 정상적으로 복사한 뒤에도 EPERM을
    낼 수 있다. 생성 source 계약에는 파일 내용만 필요하므로 metadata를 복사하지 않는다.
    """
    for relative in sorted(changed):
        source = sandbox / relative
        if not source.is_file():
            # An agent may delete a file after the snapshot used to calculate
            # ``changed``.  Do not turn that race into an unrelated WinError 2;
            # the required-output/reconciliation gates will report the missing
            # artifact with its owning task.
            continue
        target = run_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _restore_unauthorized_files(sandbox: Path, run_root: Path, unauthorized: list[str]) -> None:
    """Restore files written outside a task's ownership boundary."""
    for relative in unauthorized:
        sandbox_path = sandbox / relative
        baseline = run_root / relative
        if baseline.is_file():
            sandbox_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(baseline, sandbox_path)
        elif sandbox_path.exists():
            sandbox_path.unlink()


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
    immutable = {
        str(path).replace("\\", "/") for path in task.get("immutable_paths", [])
    }
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
    roots = [
        str(path).replace("\\", "/")
        for path in task.get("allowed_write_roots", [])
    ]
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
    if immutable_absolute:
        prompt += (
            "\n\nGenerated contracts are readable but write-protected by the sandbox. "
            "Inspect them on demand instead of copying their contents into the conversation."
        )
    if read_hints:
        prompt += "\n\nSuggested source hints:\n" + "\n".join(
            f"- `{path}`" for path in read_hints
        )

    execution_dir = run_root / "reports" / "agent-executions"
    attempt = execution_attempt(run_root, task_id)
    journal = EventJournal(execution_dir / f"{task_id}.attempt-{attempt:03d}.events.jsonl")
    started = time.monotonic()
    conversation = None
    agent = None
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
            callbacks=[journal],
            max_iterations=MAX_AGENT_TURN_ITERATIONS,
            reasoning_effort=reasoning_effort,
        )
        conversation.send_message(prompt)
        conversation.run()
        if _conversation_terminal_failure(conversation):
            raise WorkspaceVerificationError(
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
        changed = changed_files(before, snapshot_files(sandbox))
        unauthorized = [
            path
            for path in changed
            if not path_is_editable(path, editable_paths, editable_roots, immutable)
        ]
        if unauthorized:
            _restore_unauthorized_files(sandbox, run_root, unauthorized)
            raise WorkspaceVerificationError(
                {
                    "command": ["implementation-write-boundary"],
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": "Writes outside the task's implementation roots: " + ", ".join(sorted(unauthorized)),
                    "testResults": "",
                }
            )
        verification = consume_successful_task_check(
            sandbox, task_type, editable_paths, verification_profile
        ) or verify_agent_workspace(sandbox, task_type, editable_paths, verification_profile)
    except Exception as error:
        if conversation is not None:
            conversation.close()
        failure = {
            "taskId": task_id,
            "taskType": task_type,
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
        }
        if isinstance(error, WorkspaceVerificationError):
            failure["verificationEvidence"] = error.evidence
        failure["conversationStats"] = _conversation_stats_snapshot(conversation)
        write_execution_result(execution_dir, task_id, attempt, failure)
        shutil.copyfile(journal.path, execution_dir / f"{task_id}.events.jsonl")
        raise
    conversation.close()
    changed = {
        f"application/{path}"
        for path in changed_files(
            snapshot_files(run_root / "application"),
            snapshot_files(sandbox / "application"),
        )
        if path_is_editable(f"application/{path}", editable_paths, editable_roots, immutable)
    }
    promoted_files = changed | {path for path in required_paths if (sandbox / path).is_file()}
    _promote_changed_files(sandbox, run_root, promoted_files)
    if task_type == "frontend-implementation":
        store_frontend_build(run_root, sandbox, verification)
    result = {
        "taskId": task_id,
        "taskType": task_type,
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
        "conversationStats": _conversation_stats_snapshot(conversation),
        "status": "SUCCEEDED",
    }
    write_execution_result(execution_dir, task_id, attempt, result)
    shutil.copyfile(journal.path, execution_dir / f"{task_id}.events.jsonl")
    cleanup_agent_workspace(sandbox)
    return result


def _conversation_terminal_failure(conversation: object) -> bool:
    """OpenHands owns recovery; EasyDep only records a terminal state."""

    state = getattr(conversation, "state", None)
    status = getattr(state, "execution_status", None)
    value = getattr(status, "value", status)
    return str(value or "").rsplit(".", 1)[-1].upper() not in {"FINISHED", ""}


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


def validate_openhands_adapter(run_root: Path, task_id: str) -> dict[str, object]:
    """Initialize the real SDK and sandboxed standard tools without an LLM request."""
    task = load_task(run_root, task_id)
    connection = openhands_connection()
    compatibility = openhands_compatibility(connection)
    missing = [
        key
        for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled")
        if not compatibility[key]
    ]
    if missing:
        raise RuntimeError("OpenHands SDK prerequisites are missing: " + ", ".join(missing))
    task_type = str(task.get("task_type", ""))
    raw_verification_profile = task.get("verification_profile")
    verification_profile = (
        dict(raw_verification_profile)
        if isinstance(raw_verification_profile, dict) and raw_verification_profile
        else None
    )
    active_repair = active_repair_for_task(run_root, task_id)
    validation_allowed, validation_roots, validation_immutable = _task_execution_scope(
        task, active_repair
    )
    task = {
        **task,
        "allowed_write_paths": validation_allowed,
        "allowed_write_roots": validation_roots,
        "immutable_paths": validation_immutable,
    }
    sandbox = prepare_agent_workspace(run_root, task)
    allowed = [str((sandbox / path).resolve()) for path in validation_allowed]
    allowed_roots = [str((sandbox / path).resolve()) for path in validation_roots]
    immutable = [str((sandbox / path).resolve()) for path in validation_immutable]
    validation_journal = EventJournal(
        run_root / "reports" / f"agent-validation-{task_id}.events.jsonl"
    )
    conversation, agent = create_openhands_conversation(
        sandbox,
        # 준비 검사는 네트워크를 호출하지 않는다. 실제 key를 SDK 객체 안에 복사할
        # 이유가 없으므로 provider·URL·모델은 그대로 두고 key만 검사값으로 바꾼다.
        replace(connection, api_key="validation-only-key"),
        task["llm"],
        task_type=task_type,
        verification_paths=[str(path) for path in task.get("allowed_write_paths", [])],
        verification_profile=verification_profile,
        editable_files=allowed,
        editable_roots=allowed_roots,
        immutable_paths=immutable,
        callbacks=[validation_journal],
    )
    try:
        conversation.send_message("Initialize this validation conversation; do not run it.")
        tools = sorted(agent._tools)
        file_editor = agent._tools.get("file_editor")
        enforced = bool(
            file_editor
            and file_editor.executor
            and getattr(file_editor.executor, "writable_files", None)
            == {Path(path).resolve() for path in allowed}
            and getattr(file_editor.executor, "writable_roots", None)
            == {Path(path).resolve() for path in allowed_roots}
        )
        from openhands.tools.file_editor import FileEditorAction

        blocked_observation = file_editor.executor(
            FileEditorAction(
                command="create",
                path=str((sandbox / "application" / "unauthorized.java").resolve()),
                file_text="should not be written",
            )
        )
        unauthorized_blocked = bool(blocked_observation.is_error)
        probe_path = Path(allowed[0])
        probe_observation = file_editor.executor(
            FileEditorAction(
                command="str_replace",
                path=str(probe_path),
                old_str=probe_path.read_text(encoding="utf-8"),
                new_str="/* sandbox editor validation probe */\n",
            )
        )
        allowed_write_succeeded = not probe_observation.is_error and probe_path.is_file()
        if probe_path.exists():
            probe_path.unlink()
    finally:
        conversation.close()
    if (
        set(tools) != {"file_editor", "grep", TASK_CHECK_TOOL_NAME, "finish"}
        or not enforced
        or not unauthorized_blocked
        or not allowed_write_succeeded
    ):
        raise RuntimeError("Sandboxed standard OpenHands tools were not initialized correctly")
    profile = profile_for(
        connection.model,
        fallback_temperature=settings.implementation_agent_temperature,
        fallback_max_tokens=settings.implementation_agent_max_output_tokens,
    )
    task_llm = task.get("llm")
    configured_reasoning = (
        task_llm.get("reasoningEffort", settings.implementation_reasoning_effort)
        if isinstance(task_llm, dict)
        else settings.implementation_reasoning_effort
    )
    result = {
        "taskId": task_id,
        "status": "READY",
        "workspace": str(sandbox),
        "tools": tools,
        "writeBoundaryEnforced": enforced,
        "unauthorizedWriteBlocked": unauthorized_blocked,
        "allowedWriteSucceeded": allowed_write_succeeded,
        "canonicalEditor": "file_editor",
        "maxConversationToolTurns": MAX_AGENT_TURN_ITERATIONS,
        "stuckDetection": True,
        "contextCondenser": "openhands-default",
        "reasoningBudget": profile.reported_reasoning_budget(connection.provider),
        "reasoningEffort": profile.resolve_reasoning(str(configured_reasoning)),
        "temperature": profile.temperature,
        "maxOutputTokens": profile.completion_limit(
            settings.implementation_agent_max_output_tokens
        ),
        "systemPrompt": "openhands-default",
        "validationEventCount": validation_journal.event_count,
        "allowedWritePaths": allowed,
        "modelCallMade": False,
        "effectiveModel": connection.litellm_model(),
        "llm": task["llm"],
    }
    report = run_root / "reports" / f"agent-validation-{task_id}.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


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
):
    global _SANDBOX_TOOLS_REGISTERED

    from openhands.sdk import LLM, Agent, Conversation, Tool, register_tool
    from openhands.sdk.context.condenser import default_condenser
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from openhands.tools.grep import GrepObservation, GrepTool
    from openhands.tools.grep.impl import GrepExecutor
    from pydantic import SecretStr

    _configure_openhands_profile_store()

    class SandboxFileEditorExecutor(FileEditorExecutor):
        """Apply only EasyDep's filesystem boundary to the canonical editor."""

        def __init__(
            self,
            workspace_root: str,
            writable_files: list[str],
            writable_roots: list[str],
            immutable: list[str],
        ):
            super().__init__(workspace_root=workspace_root)
            self.workspace_root = Path(workspace_root).resolve()
            self.writable_files = {Path(path).resolve() for path in writable_files}
            self.writable_roots = {Path(path).resolve() for path in writable_roots}
            self.immutable = {Path(path).resolve() for path in immutable}

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
            if action.command != "view":
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
            return super().__call__(action, conversation)

    class SandboxFileEditorTool(FileEditorTool):
        name = "file_editor"

        @classmethod
        def create(cls, conv_state, writable_files, writable_roots, immutable_paths):
            return [
                instance.model_copy(
                    update={
                        "executor": SandboxFileEditorExecutor(
                            conv_state.workspace.working_dir,
                            writable_files,
                            writable_roots,
                            immutable_paths,
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
    task_check_tool_name = register_task_check_tool()
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
    llm = LLM(**llm_options)
    agent = Agent(
        llm=llm,
        tools=[
            Tool(
                name=editor_registry_name,
                params={
                    "writable_files": editable_files or [],
                    "writable_roots": editable_roots or [],
                    "immutable_paths": immutable_paths or [],
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
        ],
        include_default_tools=["FinishTool"],
        # Do not override OpenHands' built-in system behavior. EasyDep's
        # task-specific constraints are appended to the user task message.
        condenser=default_condenser(
            llm=llm.model_copy(update={"usage_id": "implementation_condenser"}),
        ),
    )
    return (
        Conversation(
            agent=agent,
            workspace=str(sandbox),
            callbacks=callbacks,
            max_iteration_per_run=max_iterations,
            stuck_detection=True,
            visualizer=None,
        ),
        agent,
    )


def _path_is_immutable(path: str, immutable_paths: set[str]) -> bool:
    """파일 경로가 생성 계약 파일 또는 그 하위에 있는지 확인한다."""
    normalized = path.replace("\\", "/").rstrip("/")
    return any(
        normalized == root.rstrip("/") or normalized.startswith(root.rstrip("/") + "/")
        for root in immutable_paths
    )
