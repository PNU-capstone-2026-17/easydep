from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import warnings
from pathlib import Path
from typing import Any

from app.config import settings
from app.llm_connection import build_llm_connection
from app.llm_profiles import canonical_model_id, profile_for
from app.metrics import langsmith as langsmith_metrics
from app.validation import RepairAttempt, RepairLedger, stable_digest

from ..workflows.conformance import entity_public_signature_violations
from ..workflows.repair import active_repair_for_task
from .prompts import (
    FRONTEND_SYSTEM_PROMPT,
    IMPLEMENTATION_SYSTEM_PROMPT,
    render_frontend_verification_feedback,
    render_verification_feedback,
)
from .provider import (
    MAX_PROVIDER_RETRIES,
    configured_api_key,
    configured_base_url,
    configured_headers,
    configured_max_output_tokens,
    configured_model,
    configured_provider_name,
    openhands_compatibility,
    provider_retry_delay,
    transient_provider_error,
)
from .task_check import (
    TASK_CHECK_TOOL_NAME,
    consume_successful_task_check,
    register_task_check_tool,
)
from .verification.build import (
    WorkspaceVerificationError,
    compact_verification_evidence,
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

# 하나의 기능 작업은 여러 파일을 함께 만들므로 한 번의 Conversation run이 의미 있는 편집과
# focused 검사까지 진행할 만큼의 tool turn을 준다. 이 값은 전체 수리 횟수 상한이 아니다.
# 한도나 context에 닿으면 workspace는 유지하고 짧은 인계문으로 새 Conversation을 연다.
MAX_AGENT_TURN_ITERATIONS = 32
_RESTRICTED_EDITOR_REGISTERED = False
_RESTRICTED_GREP_REGISTERED = False
_RESTRICTED_EDITOR_REGISTRATION_LOCK = threading.Lock()


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
    compatibility = openhands_compatibility()
    connection = build_llm_connection()
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


def _render_missing_output_repair_prompt(
    missing_outputs: list[str],
) -> str:
    """Keep missing-output retries small enough for agents that stopped silently."""
    missing_test = any("/src/test/" in path.replace("\\", "/") for path in missing_outputs)
    if missing_test:
        task_hint = (
            "Create the missing focused test from the existing application and generated "
            "contracts. Assert observable behavior; do not copy a prompt or private helper."
        )
    else:
        task_hint = (
            "Use the existing generated application contract; do not inspect or list directories."
        )
    files = "\n".join(f"- `{path}`" for path in missing_outputs)
    return (
        "The previous agent round did not create the required output files. "
        "Use the file editor's create operation now with the exact absolute paths below; "
        "do not reply with an explanation. "
        "Create only the missing files, preserve all existing files, then run "
        "run_task_check and repair any reported error before finishing. "
        "Parent directories already exist. "
        "Do not use /workspace, /application, relative paths, view, or directory-listing tools.\n\n"
        + task_hint
        + "\n\nRequired missing outputs (absolute paths):\n"
        + files
    )


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


def _repeated_failure(
    ledger: RepairLedger,
    candidate_digest: str,
    finding_keys: tuple[str, ...],
) -> bool:
    """같은 source와 같은 검사 오류를 이미 수리하려 했는지 확인한다."""
    return any(
        attempt.candidate_digest == candidate_digest and attempt.finding_keys_before == finding_keys
        for attempt in ledger.attempts
        if attempt.candidate_digest
    )


def _repair_restart_evidence(
    evidence: dict[str, object], candidate_digest: str
) -> dict[str, object]:
    """coordinator가 성공 source에서 새 대화를 열 수 있는 근거를 덧붙인다."""
    return {
        **evidence,
        "repairControl": {
            "action": "restart_from_accepted_source",
            "reason": "same_failure_and_source",
            "rejectedCandidateDigest": candidate_digest,
        },
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
    task = load_task(run_root, task_id)
    task_type = str(task.get("task_type", ""))
    app_id = _run_app_id(run_root)
    active_repair = active_repair_for_task(run_root, task_id)
    editable_paths, editable_roots, immutable = _task_execution_scope(task, active_repair)
    required_paths = [str(path) for path in task.get("required_output_paths", editable_paths)]
    immutable_paths = set(immutable)
    task = {
        **task,
        "allowed_write_paths": editable_paths,
        "allowed_write_roots": editable_roots,
        "immutable_paths": immutable,
    }

    compatibility = openhands_compatibility()
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
        # 실패한 임시 파일은 아직 승인된 source가 아니다. 새 수리 대화는 마지막으로
        # 검사를 통과해 run에 반영된 source에서 시작한다.
        preserve_failed_edits=active_repair is None,
    )
    before = snapshot_files(sandbox)
    missing_at_start = missing_required_outputs(sandbox, required_paths)
    # 새 파일 자체가 빠진 경우에는 짧은 오류 문장만으로 구현을 다시 시작할 수 없다.
    # 원래 작업 설명에는 요구사항, 정확한 Java 계약과 저장소 선언이 들어 있으므로 이를
    # 다시 제공한다. 이미 존재하는 파일의 compile/test 오류를 고칠 때만 짧은 수리 설명을
    # 사용해 불필요하게 큰 문맥을 반복하지 않는다.
    prompt_file = (
        task.get("prompt_file")
        if active_repair is None or missing_at_start
        else task.get("repair_prompt_file")
    )
    if not isinstance(prompt_file, str) or not (run_root / prompt_file).is_file():
        prompt_file = str(task["prompt_file"])
    prompt = (run_root / prompt_file).read_text(encoding="utf-8")
    if active_repair is not None and missing_at_start:
        prompt += (
            "\n\n## Retry focus\n\n"
            "A previous round stopped before creating these contracted outputs. "
            "Create them before optional exploration, then run the focused task check:\n"
            + "\n".join(f"- `{path}`" for path in missing_at_start)
        )
    context = json.loads((run_root / task["context_file"]).read_text(encoding="utf-8"))
    read_source_paths = context.get("readSourcePaths", [])
    readable_absolute: list[str] = []
    if isinstance(read_source_paths, list):
        sandbox_root = sandbox.resolve()
        for value in read_source_paths:
            if not isinstance(value, str):
                continue
            candidate = (sandbox / value).resolve()
            try:
                candidate.relative_to(sandbox_root)
            except ValueError:
                continue
            if candidate.exists():
                readable_absolute.append(str(candidate))
    allowed_absolute = [str((sandbox / path).resolve()) for path in editable_paths]
    editable_root_absolute = [str((sandbox / path).resolve()) for path in editable_roots]
    immutable_absolute = [str((sandbox / path).resolve()) for path in sorted(immutable_paths)]
    prompt += "\n\n## Enforced absolute write paths\n\n" + "\n".join(
        f"- `{path}`" for path in allowed_absolute
    )
    if editable_root_absolute:
        prompt += "\n\n## Enforced writable implementation roots\n\n" + "\n".join(
            f"- `{path}`" for path in editable_root_absolute
        )
    if immutable_absolute and task_type != "use-case":
        prompt += "\n\n## Read-only generated contract paths\n\n" + "\n".join(
            f"- `{path}`" for path in immutable_absolute
        )
    elif immutable_absolute:
        # 실제 편집기는 아래 경로를 계속 차단한다. 기능 task에는 관련 계약이 이미 본문에
        # 있으므로 모든 금지 파일명을 나열해 불필요한 탐색 후보를 늘리지 않는다.
        prompt += (
            "\n\nOther generated API and BCE contracts are read-only. "
            "Do not search for unrelated implementations.\n"
        )
    if readable_absolute:
        # 선행 작업이 만든 실제 source는 프롬프트에 오래된 사본으로 넣지 않는다. 실행
        # workspace의 위치만 알려 주면 OpenHands가 view로 최신 선언을 읽고 구현을 정한다.
        prompt += "\n\n## Inspect these current sources before editing\n\n" + "\n".join(
            f"- `{path}`" for path in readable_absolute
        )

    api_key = configured_api_key()
    assert api_key is not None
    execution_dir = run_root / "reports" / "agent-executions"
    attempt = execution_attempt(run_root, task_id)
    journal = EventJournal(execution_dir / f"{task_id}.attempt-{attempt:03d}.events.jsonl")
    started = time.monotonic()
    agent = None
    conversation_warning: str | None = None
    # 같은 작업이 다른 담당 오류 때문에 다시 실행되더라도 이전 수리 실패를 잊지 않는다.
    # 별도 저장 형식을 만들지 않고 이미 남긴 최신 실행 결과의 repairHistory를 재사용한다.
    repair_ledger = load_repair_ledger(execution_dir, task_id)
    conversation = None
    try:
        round_prompt = prompt
        round_allowed = allowed_absolute
        provider_retries = 0
        repair_attempt = 0
        reasoning_effort = os.environ.get(
            "OPENHANDS_REASONING_EFFORT",
            str(
                task["llm"].get(
                    "reasoningEffort",
                    settings.implementation_reasoning_effort,
                )
            ),
        )
        def open_conversation():
            """현재 workspace를 유지한 채 독립된 OpenHands 대화를 연다."""

            return create_openhands_conversation(
                sandbox,
                allowed_absolute,
                api_key,
                task["llm"],
                task_type=task_type,
                verification_paths=editable_paths,
                editable_roots=editable_root_absolute,
                immutable_paths=immutable_absolute,
                callbacks=[journal],
                max_iterations=MAX_AGENT_TURN_ITERATIONS,
                reasoning_effort=reasoning_effort,
                system_prompt=(
                    FRONTEND_SYSTEM_PROMPT
                    if task_type == "frontend-implementation"
                    else IMPLEMENTATION_SYSTEM_PROMPT
                ),
            )

        while True:
            if conversation is None:
                conversation, agent = open_conversation()
            _extend_conversation_write_files(agent, round_allowed)
            restart_after_verification = False
            # A transient provider failure is a transport concern, not a source
            # verification failure. 같은 Conversation의 현재 메시지부터 재개해 이미 읽은
            # source와 판단을 버리지 않는다.
            message_sent = False
            while True:
                conversation_error: Exception | None = None
                usage_before = _conversation_token_usage(conversation) or (0, 0)
                connection = build_llm_connection()
                with langsmith_metrics.trace_scope(
                    "easydep.implementation.openhands_conversation",
                    run_type="llm",
                    metadata={
                        "agent": "implementation",
                        "operation": "openhands_conversation",
                        "run_id": run_root.name,
                        "task_id": task_id,
                        "app_id": app_id,
                        "repair_attempt": repair_attempt,
                        "ls_provider": connection.provider,
                        "ls_model_name": connection.model,
                    },
                ) as trace:
                    try:
                        if not message_sent:
                            conversation.send_message(round_prompt)
                            message_sent = True
                        conversation.run()
                        # OpenHands 1.36은 iteration 한도와 반복 감지를 예외로 던지지
                        # 않고 Conversation 상태만 ERROR로 바꾼 뒤 run()을 반환한다.
                        # 이 상태를 놓치면 다음 수리 문장을 같은 긴 대화에 붙이게 된다.
                        if _conversation_finished_with_error(conversation):
                            restart_after_verification = True
                    except Exception as error:
                        # A provider can reject the final turn after the agent has
                        # already written every contracted output. Keep the warning
                        # for the successful result, but retry transient failures
                        # before consulting output or build verification.
                        conversation_error = error
                        conversation_warning = f"{error.__class__.__name__}: {error}"
                    finally:
                        usage = _conversation_token_usage(conversation)
                        if usage is not None:
                            trace.set_usage(
                                input_tokens=max(0, usage[0] - usage_before[0]),
                                output_tokens=max(0, usage[1] - usage_before[1]),
                            )
                if conversation_error is None:
                    provider_retries = 0
                    break
                if not transient_provider_error(conversation_error):
                    # iteration/context/stuck 오류가 난 Conversation에는 메시지를 더 넣지 않는다.
                    # 이미 작성한 source가 있으면 아래 결정론적 검사로 살릴 수 있는지 먼저 보고,
                    # 수리가 필요할 때 같은 workspace에서 새 Conversation을 연다.
                    if _conversation_needs_fresh_context(conversation_error):
                        restart_after_verification = True
                        break
                    if not missing_required_outputs(sandbox, required_paths):
                        restart_after_verification = True
                        break
                    raise conversation_error
                # A provider may fail while emitting its final response after
                # the agent has already written every contracted file. In that
                # case do not repeat the generation and risk overwriting valid
                # work; continue to deterministic verification instead.
                if not missing_required_outputs(sandbox, required_paths):
                    restart_after_verification = True
                    break
                provider_retries += 1
                if provider_retries > MAX_PROVIDER_RETRIES:
                    raise RuntimeError(
                        f"{configured_provider_name()} remained unavailable after "
                        f"{MAX_PROVIDER_RETRIES} transport retries"
                    ) from conversation_error
                time.sleep(provider_retry_delay(provider_retries))

            missing_outputs = missing_required_outputs(sandbox, required_paths)
            if missing_outputs:
                finding_keys = tuple(f"missing:{path}" for path in missing_outputs)
                candidate_digest = stable_digest(snapshot_files(sandbox))
                repeated = repair_attempt > 0 and _repeated_failure(
                    repair_ledger,
                    candidate_digest,
                    finding_keys,
                )
                repair_ledger.record(
                    RepairAttempt(
                        stage=f"implementation.{task_type}",
                        target_ids=tuple(missing_outputs),
                        strategy_key=(
                            "initial_generation"
                            if repair_attempt == 0
                            else "create_missing_outputs"
                        ),
                        input_digest=stable_digest(
                            {
                                "task": task_id,
                                "candidate": candidate_digest,
                                "findings": finding_keys,
                            }
                        ),
                        candidate_digest=candidate_digest,
                        finding_keys_before=finding_keys,
                        finding_keys_after=finding_keys,
                        outcome="repeated_candidate" if repeated else "no_improvement",
                        detail="Missing required outputs: " + ", ".join(missing_outputs),
                    )
                )
                if repeated:
                    raise WorkspaceVerificationError(
                        _repair_restart_evidence(
                            {
                                "command": ["required-task-outputs"],
                                "exitCode": 1,
                                "stderr": "Missing required outputs: " + ", ".join(missing_outputs),
                            },
                            candidate_digest,
                        )
                    )
                round_allowed = [str((sandbox / path).resolve()) for path in missing_outputs]
                round_prompt = _render_missing_output_repair_prompt(
                    round_allowed,
                )
                round_prompt += (
                    "\n\n## Accumulated repair history\n\n"
                    + _implementation_repair_history(repair_ledger)
                )
                repair_attempt += 1
                if restart_after_verification:
                    conversation.close()
                    conversation = None
                    agent = None
                continue

            changed = changed_files(before, snapshot_files(sandbox))
            unauthorized = sorted(
                path
                for path in changed
                if not path_is_editable(
                    path,
                    editable_paths,
                    editable_roots,
                    immutable_paths,
                )
            )
            if unauthorized:
                _restore_unauthorized_files(sandbox, run_root, unauthorized)
                changed = {
                    path
                    for path in changed
                    if path_is_editable(
                        path,
                        editable_paths,
                        editable_roots,
                        immutable_paths,
                    )
                }
            try:
                # EasyDep은 source를 정규식으로 고치지 않는다. OpenHands가 현재 파일과
                # compiler/test 결과를 보고 수정하며, 공개 계약은 최종 conformance 검사에서
                # 별도로 보호한다. 실제 HTTP 흐름 검사는 wiring 작업의 FlowTest에 포함된다.
                controller_paths = context.get("controllerPaths", [])
                controller_markers = context.get("controllerBodyMarkers", [])
                controller_sources = (
                    {
                        path: (sandbox / path).read_text(encoding="utf-8")
                        for path in controller_paths
                        if isinstance(path, str) and (sandbox / path).is_file()
                    }
                    if isinstance(controller_paths, list)
                    else {}
                )
                unfinished_controllers = (
                    [
                        (marker, path)
                        for marker in controller_markers
                        if isinstance(marker, str)
                        for path, source in controller_sources.items()
                        if marker in source
                    ]
                    if isinstance(controller_markers, list)
                    else []
                )
                if unfinished_controllers:
                    raise WorkspaceVerificationError(
                        {
                            "command": ["controller-body-completion"],
                            "exitCode": 1,
                            "durationMs": 0,
                            "stdout": "",
                            "stderr": "\n".join(
                                f"Unimplemented Controller body {marker}: {path}"
                                for marker, path in unfinished_controllers
                            ),
                            "testResults": "",
                        }
                    )
                editable_entities = [
                    path
                    for path in changed
                    if "/bce/" in "/" + path.replace("\\", "/") and path.endswith(".java")
                ]
                signature_violations = (
                    entity_public_signature_violations(run_root, sandbox, editable_entities)
                    if editable_entities
                    else []
                )
                if signature_violations:
                    raise WorkspaceVerificationError(
                        {
                            "command": ["generated-entity-public-contract"],
                            "exitCode": 1,
                            "durationMs": 0,
                            "stdout": "",
                            "stderr": "\n".join(signature_violations),
                            "testResults": "",
                        }
                    )
                # OpenHands가 방금 같은 source에서 run_task_check를 통과했다면 Gradle을
                # 즉시 한 번 더 실행하지 않는다. 검사 뒤 source가 바뀐 경우에는 cache가
                # 일치하지 않아 아래 실제 검사가 실행된다.
                verification = consume_successful_task_check(
                    sandbox,
                    task_type,
                    editable_paths,
                ) or verify_agent_workspace(sandbox, task_type, editable_paths)
                changed = changed_files(before, snapshot_files(sandbox))
                break
            except WorkspaceVerificationError as error:
                diagnostic = compact_verification_evidence(
                    error.evidence,
                    max_chars=4000,
                )
                evidence_digest = stable_digest(
                    {
                        "command": error.evidence.get("command"),
                        "exitCode": error.evidence.get("exitCode"),
                        "diagnostic": diagnostic,
                    }
                )
                finding_keys = (f"verification:{evidence_digest}",)
                candidate_digest = stable_digest(snapshot_files(sandbox))
                repeated = repair_attempt > 0 and _repeated_failure(
                    repair_ledger,
                    candidate_digest,
                    finding_keys,
                )
                repair_ledger.record(
                    RepairAttempt(
                        stage=f"implementation.{task_type}",
                        target_ids=tuple(editable_paths),
                        strategy_key=(
                            "initial_generation"
                            if repair_attempt == 0
                            else "verification_correction"
                        ),
                        input_digest=stable_digest(
                            {
                                "task": task_id,
                                "candidate": candidate_digest,
                                "findings": finding_keys,
                            }
                        ),
                        candidate_digest=candidate_digest,
                        finding_keys_before=finding_keys,
                        finding_keys_after=finding_keys,
                        outcome="repeated_candidate" if repeated else "no_improvement",
                        detail=diagnostic,
                    )
                )
                if repeated:
                    # 같은 대화에 경고만 추가하면 모델 상태와 source가 그대로라 같은 실패가
                    # 계속된다. coordinator로 근거를 돌려보내 새 대화와 승인 source 복구를
                    # 실제로 실행하게 한다.
                    raise WorkspaceVerificationError(
                        _repair_restart_evidence(error.evidence, candidate_digest)
                    ) from error
                # 같은 대화는 이미 현재 작업의 편집 범위를 가지고 있다. 오류 문자열로
                # 파일을 다시 좁히지 않고 그 범위 안에서 실제 원인을 찾게 한다.
                repair_paths = list(editable_paths)
                round_allowed = [str((sandbox / path).resolve()) for path in repair_paths]
                feedback_renderer = (
                    render_frontend_verification_feedback
                    if task_type == "frontend-implementation"
                    else render_verification_feedback
                )
                round_prompt = feedback_renderer(
                    error.evidence,
                    repair_paths,
                )
                round_prompt += (
                    "\n\n## Accumulated repair history\n\n"
                    + _implementation_repair_history(repair_ledger)
                )
                repair_attempt += 1
                if restart_after_verification:
                    conversation.close()
                    conversation = None
                    agent = None
        conversation.close()
        conversation = None
    except Exception as error:
        if conversation is not None:
            conversation.close()
        failure = {
            "taskId": task_id,
            "taskType": task.get("task_type", "control"),
            "promptSha256": task.get("prompt_sha256"),
            "status": "FAILED",
            "effectiveModel": configured_model(),
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
        if repair_ledger.attempts:
            failure["repairHistory"] = repair_ledger.model_dump(mode="json")
        write_execution_result(execution_dir, task_id, attempt, failure)
        shutil.copyfile(journal.path, execution_dir / f"{task_id}.events.jsonl")
        raise
    # 실패 뒤 재사용한 임시 작업 공간에는 필수 결과가 이미 존재할 수 있다. 그런 파일은
    # 이번 대화 시작 시점과 비교하면 changed가 아니지만, 실제 run에는 아직 없을 수 있다.
    # 성공한 작업의 계약 결과는 항상 run으로 복사해 체크포인트와 source를 일치시킨다.
    promoted_files = changed | {path for path in required_paths if (sandbox / path).is_file()}
    _promote_changed_files(sandbox, run_root, promoted_files)
    if task_type == "frontend-implementation":
        # task 검사가 만든 production bundle은 현재 source와 함께 검증됐다. run에 한 번만
        # 보존하면 최종 검사와 통합 Docker image가 같은 npm build를 반복하지 않는다.
        store_frontend_build(run_root, sandbox, verification)
    result = {
        "taskId": task_id,
        "taskType": task.get("task_type", "control"),
        "promptSha256": task.get("prompt_sha256"),
        "effectiveModel": configured_model(),
        "changedFiles": sorted(changed),
        "outputFiles": required_paths,
        "verification": verification,
        "tools": sorted(agent._tools) if agent is not None else [],
        "durationMs": int((time.monotonic() - started) * 1000),
        "eventCount": journal.event_count,
        "toolCounts": journal.tool_counts,
        "eventJournal": str(journal.path.relative_to(run_root)).replace("\\", "/"),
        "rawResponse": journal.latest_agent_message,
        "status": "SUCCEEDED",
    }
    if repair_ledger.attempts:
        repair_ledger.status = "COMPLETED"
        result["repairHistory"] = repair_ledger.model_dump(mode="json")
    if conversation_warning is not None:
        result["conversationWarning"] = conversation_warning
    write_execution_result(execution_dir, task_id, attempt, result)
    shutil.copyfile(journal.path, execution_dir / f"{task_id}.events.jsonl")
    cleanup_agent_workspace(sandbox)
    return result


def _conversation_token_usage(conversation) -> tuple[int, int] | None:
    """작업 흐름에는 영향을 주지 않고 OpenHands의 누적 token 사용량을 읽는다."""

    try:
        metrics = conversation.conversation_stats.get_combined_metrics()
        usage = metrics.accumulated_token_usage
        if usage is None:
            return None
        return (
            max(0, int(getattr(usage, "prompt_tokens", 0) or 0)),
            max(0, int(getattr(usage, "completion_tokens", 0) or 0)),
        )
    except Exception:  # noqa: BLE001 - observability is optional
        return None


def _conversation_needs_fresh_context(error: Exception) -> bool:
    """같은 OpenHands Conversation에서 재실행하면 안 되는 오류인지 확인한다.

    네트워크 오류는 provider retry가 담당한다. 반면 iteration/context/stuck 오류는 대화 기록
    자체가 이미 한계에 닿았다는 뜻이므로 같은 객체에 메시지를 추가할수록 악화된다. 오류 class는
    SDK와 LiteLLM 버전에 따라 달라질 수 있어 안정적으로 노출되는 문구만 사용한다.
    """

    text = f"{error.__class__.__name__}: {error}".lower()
    return any(
        marker in text
        for marker in (
            "maximum iteration",
            "max iterations",
            "context window",
            "maximum context length",
            "max_tokens must be at least 1",
            "token limit",
            "agent is stuck",
            "execution status is stuck",
            "no tool call and no content",
        )
    )


def _conversation_finished_with_error(conversation: object) -> bool:
    """SDK가 예외 없이 끝낸 실패 대화인지 확인한다.

    OpenHands의 로컬 Conversation은 iteration 한도나 stuck detector가 동작하면
    ``run()``을 정상 반환하면서 ``execution_status``만 ``ERROR``로 기록한다. SDK
    enum을 모듈 import 시점에 의존하지 않고 값만 읽어, 테스트용 대화와 호스트의
    선택적 OpenHands 설치도 그대로 지원한다.
    """

    state = getattr(conversation, "state", None)
    status = getattr(state, "execution_status", None)
    value = getattr(status, "value", status)
    return str(value or "").rsplit(".", 1)[-1].upper() == "ERROR"


def _implementation_repair_history(ledger: RepairLedger) -> str:
    """현재 진단을 가리지 않을 정도로만 이전 수리 결과를 요약한다.

    compiler와 test 원문은 실행 결과 JSON에 남는다. 대화에는 후보와 결과, 대표 진단만 넣어
    이미 고친 과거 오류를 다시 추적하거나 같은 긴 로그를 token으로 반복 소비하지 않는다.
    """
    if not ledger.attempts:
        return "No previous repair attempt."
    lines: list[str] = []
    first = max(0, len(ledger.attempts) - 3)
    for index, attempt in enumerate(ledger.attempts[-3:], start=first + 1):
        detail = _representative_repair_diagnostic(
            attempt.detail.strip() or ", ".join(attempt.finding_keys_before)
        )
        lines.append(
            f"Attempt {index}: result={attempt.outcome}, "
            f"candidate={attempt.candidate_digest[:12]}, diagnostic={detail}"
        )
    if first:
        lines.insert(0, f"{first} older attempt(s) omitted.")
    return "\n".join(lines)


def _representative_repair_diagnostic(value: str, limit: int = 320) -> str:
    """이전 검사 원문에서 다음 시도에 유용한 실패 한 줄만 고른다."""
    lines = [" ".join(line.split()) for line in value.splitlines() if line.strip()]
    markers = ("error:", "failed", "failure", "expected:", "violation", "missing")
    selected = next(
        (line for line in lines if any(marker in line.lower() for marker in markers)),
        lines[0] if lines else "no diagnostic",
    )
    return selected[:limit]


def _extend_conversation_write_files(agent, absolute_paths: list[str]) -> None:
    """같은 대화의 편집기에 새로 확인된 작업 파일을 추가한다."""
    tools = getattr(agent, "_tools", {})
    editor = tools.get("restricted_file_editor") if isinstance(tools, dict) else None
    executor = getattr(editor, "executor", None)
    allowed = getattr(executor, "allowed_edits_files", None)
    if isinstance(allowed, set):
        allowed.update(Path(path).resolve() for path in absolute_paths)


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


def load_repair_ledger(execution_dir: Path, task_id: str) -> RepairLedger:
    """이전 실행 결과에 저장된 같은 작업의 수리 이력을 이어서 사용한다."""

    candidates = [execution_dir / f"{task_id}.result.json"]
    candidates.extend(
        sorted(
            execution_dir.glob(f"{task_id}.attempt-*.result.json"),
            reverse=True,
        )
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            history = result.get("repairHistory")
            if not isinstance(history, dict):
                continue
            ledger = RepairLedger.model_validate(history)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        ledger.status = "ACTIVE"
        ledger.stall_reason = ""
        ledger.next_retry_at = None
        return ledger
    return RepairLedger()


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
    """Initialize the real SDK and restricted tool without making an LLM request."""
    task = load_task(run_root, task_id)
    compatibility = openhands_compatibility()
    missing = [
        key
        for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled")
        if not compatibility[key]
    ]
    if missing:
        raise RuntimeError("OpenHands SDK prerequisites are missing: " + ", ".join(missing))
    task_type = str(task.get("task_type", ""))
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
        allowed,
        "validation-only-key",
        task["llm"],
        task_type=task_type,
        verification_paths=[str(path) for path in task.get("allowed_write_paths", [])],
        editable_roots=allowed_roots,
        immutable_paths=immutable,
        callbacks=[validation_journal],
        system_prompt=(
            FRONTEND_SYSTEM_PROMPT
            if task.get("task_type") == "frontend-implementation"
            else IMPLEMENTATION_SYSTEM_PROMPT
        ),
    )
    try:
        conversation.send_message("Initialize this validation conversation; do not run it.")
        tools = sorted(agent._tools)
        file_editor = agent._tools.get("restricted_file_editor")
        enforced = bool(
            file_editor
            and file_editor.executor
            and getattr(file_editor.executor, "allowed_edits_files", None)
            == {Path(path).resolve() for path in allowed}
        )
        alias_action = file_editor.action_type.model_validate(
            {"command": "view", "file_path": allowed[0]}
        )
        file_path_alias_accepted = alias_action.path == allowed[0]
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
                command="create",
                path=str(probe_path),
                file_text="/* restricted editor validation probe */\n",
            )
        )
        allowed_write_succeeded = not probe_observation.is_error and probe_path.is_file()
        overwrite_observation = file_editor.executor(
            FileEditorAction(
                command="create",
                path=str(probe_path),
                file_text="/* restricted editor overwrite probe */\n",
            )
        )
        allowed_overwrite_succeeded = bool(
            not overwrite_observation.is_error
            and probe_path.read_text(encoding="utf-8")
            == "/* restricted editor overwrite probe */\n"
        )
        if probe_path.exists():
            probe_path.unlink()
    finally:
        conversation.close()
    if (
        set(tools) != {"restricted_file_editor", "grep", TASK_CHECK_TOOL_NAME, "finish"}
        or not enforced
        or not unauthorized_blocked
        or not allowed_write_succeeded
        or not allowed_overwrite_succeeded
        or not file_path_alias_accepted
    ):
        raise RuntimeError("Restricted FileEditorTool was not initialized with the exact allowlist")
    profile = profile_for(
        configured_model(),
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
        "allowlistEnforced": enforced,
        "unauthorizedWriteBlocked": unauthorized_blocked,
        "allowedWriteSucceeded": allowed_write_succeeded,
        "allowedOverwriteSucceeded": allowed_overwrite_succeeded,
        "filePathAliasAccepted": file_path_alias_accepted,
        "maxConversationToolTurns": MAX_AGENT_TURN_ITERATIONS,
        "stuckDetection": True,
        "contextCondenser": "LLMSummarizingCondenser",
        "verificationRepairPolicy": "history-and-progress/v1",
        "reasoningBudget": profile.reasoning_budget,
        "reasoningEffort": profile.resolve_reasoning(str(configured_reasoning)),
        "temperature": profile.temperature,
        "maxOutputTokens": profile.completion_limit(
            settings.implementation_agent_max_output_tokens
        ),
        "systemPrompt": (
            "focused-frontend-implementation"
            if task.get("task_type") == "frontend-implementation"
            else "focused-java-implementation"
        ),
        "validationEventCount": validation_journal.event_count,
        "allowedWritePaths": allowed,
        "modelCallMade": False,
        "effectiveModel": configured_model(),
        "llm": task["llm"],
    }
    report = run_root / "reports" / f"agent-validation-{task_id}.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def create_openhands_conversation(
    sandbox: Path,
    allowed_files: list[str],
    api_key: str,
    llm_config: dict[str, object],
    *,
    task_type: str = "",
    verification_paths: list[str] | None = None,
    editable_roots: list[str] | None = None,
    immutable_paths: list[str] | None = None,
    callbacks: list[object] | None = None,
    max_iterations: int = MAX_AGENT_TURN_ITERATIONS,
    reasoning_effort: str = "medium",
    system_prompt: str = IMPLEMENTATION_SYSTEM_PROMPT,
):
    global _RESTRICTED_EDITOR_REGISTERED, _RESTRICTED_GREP_REGISTERED

    from openhands.sdk import LLM, Agent, Conversation, Tool, register_tool
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from openhands.tools.file_editor import FileEditorAction, FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.exceptions import ToolError
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from openhands.tools.grep import GrepObservation, GrepTool
    from openhands.tools.grep.impl import GrepExecutor
    from pydantic import AliasChoices, Field, SecretStr

    _configure_openhands_profile_store()

    class CompatibleFileEditorAction(FileEditorAction):
        """Accept the common file_path spelling without advertising it to the LLM."""

        path: str = Field(
            description=(
                "Path inside the assigned workspace. Prefer an absolute path from the "
                "prompt and use the argument name path."
            ),
            validation_alias=AliasChoices("path", "file_path"),
            serialization_alias="path",
        )

    class ReplaceableFileEditorExecutor(FileEditorExecutor):
        def __init__(
            self,
            workspace_root,
            allowed_edits_files,
            allowed_edit_roots,
            immutable_edit_paths,
        ):
            # SDK의 기본 executor는 파일 목록만 지원한다. 실제 편집 호출은 아래에서
            # 검사하므로 부모에는 제한을 넘기지 않고, 검증 보고서용 속성은 유지한다.
            super().__init__(workspace_root=workspace_root)
            self.allowed_edits_files = {Path(path).resolve() for path in allowed_edits_files}
            self.allowed_edit_roots = {Path(path).resolve() for path in allowed_edit_roots}
            self.immutable_edit_paths = {Path(path).resolve() for path in immutable_edit_paths}
            self.easydep_workspace_root = Path(workspace_root).resolve()

        def _can_edit(self, target: Path) -> bool:
            if any(target == path or path in target.parents for path in self.immutable_edit_paths):
                return False
            return target in (self.allowed_edits_files or set()) or any(
                target == root or root in target.parents for root in self.allowed_edit_roots
            )

        def __call__(self, action, conversation=None):
            supplied = Path(action.path)
            target = (
                supplied.resolve()
                if supplied.is_absolute()
                else (self.easydep_workspace_root / supplied).resolve()
            )
            try:
                relative = target.relative_to(self.easydep_workspace_root)
            except ValueError:
                return FileEditorObservation.from_text(
                    text=f"Path is outside the assigned workspace: {target}",
                    command=action.command,
                    is_error=True,
                )
            if action.command == "view" and any(
                part in {"build", ".gradle", "node_modules", "dist"}
                for part in relative.parts
            ):
                return FileEditorObservation.from_text(
                    text=(
                        "Generated build and dependency outputs are not source context. "
                        "Use the concise result returned by run_task_check, inspect the "
                        "named source files, and repair those files instead."
                    ),
                    command="view",
                    is_error=True,
                )
            if action.command != "view" and not self._can_edit(target):
                return FileEditorObservation.from_text(
                    text=(
                        f"Operation '{action.command}' is not allowed on '{target}'. "
                        "Edit only the assigned files or implementation roots and do "
                        "not change generated contract paths."
                    ),
                    command=action.command,
                    is_error=True,
                )
            can_replace = bool(
                action.command == "create"
                and action.file_text is not None
                and target.is_file()
                and self._can_edit(target)
            )
            if can_replace:
                try:
                    old_content = target.read_text(encoding="utf-8")
                    target.write_text(action.file_text, encoding="utf-8")
                except OSError as error:
                    return FileEditorObservation.from_text(
                        text=f"Could not replace editable file: {error}",
                        command="create",
                        is_error=True,
                    )
                return FileEditorObservation.from_text(
                    text=f"Editable file replaced successfully at: {target}",
                    command="create",
                    is_error=False,
                ).model_copy(
                    update={
                        "path": str(target),
                        "prev_exist": True,
                        "old_content": old_content,
                        "new_content": action.file_text,
                    }
                )

            # OpenHands 기본 편집기의 binary 판별은 UTF-8 한글 주석이 많은 Java 파일을
            # binary로 오인할 수 있다. EasyDep이 만든 source는 UTF-8 계약이므로 파일
            # 조회만 직접 처리한다. 실제 binary나 잘못된 인코딩은 decode 단계에서 그대로
            # 거절하고, 디렉터리 목록과 편집 명령은 SDK 기본 구현을 계속 사용한다.
            if action.command == "view" and target.is_file():
                try:
                    content = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as error:
                    return FileEditorObservation.from_text(
                        text=f"Could not read UTF-8 text file: {error}",
                        command="view",
                        is_error=True,
                    )
                lines = content.splitlines()
                start = 1
                end = len(lines)
                if action.view_range:
                    start = max(1, int(action.view_range[0]))
                    requested_end = int(action.view_range[1])
                    end = len(lines) if requested_end == -1 else min(len(lines), requested_end)
                numbered = "\n".join(
                    f"{number:6}\t{lines[number - 1]}"
                    for number in range(start, end + 1)
                )
                return FileEditorObservation.from_text(
                    text=(
                        f"Here's the UTF-8 text in {target} "
                        f"(lines {start}-{end}):\n{numbered}"
                    ),
                    command="view",
                    is_error=False,
                ).model_copy(update={"path": str(target)})

            try:
                return self.editor(
                    command=action.command,
                    path=action.path,
                    file_text=action.file_text,
                    view_range=action.view_range,
                    old_str=action.old_str,
                    new_str=action.new_str,
                    insert_line=action.insert_line,
                )
            except ToolError as error:
                return FileEditorObservation.from_text(
                    text=error.message,
                    command=action.command,
                    is_error=True,
                )

    class RestrictedFileEditorTool(FileEditorTool):
        @classmethod
        def create(
            cls,
            conv_state,
            allowed_edits_files,
            allowed_edit_roots,
            immutable_edit_paths,
        ):
            instances = super().create(conv_state)
            return [
                instance.model_copy(
                    update={
                        "executor": ReplaceableFileEditorExecutor(
                            workspace_root=conv_state.workspace.working_dir,
                            allowed_edits_files=allowed_edits_files,
                            allowed_edit_roots=allowed_edit_roots,
                            immutable_edit_paths=immutable_edit_paths,
                        ),
                        "action_type": CompatibleFileEditorAction,
                        "description": (
                            "Create or edit text files inside the assigned file list or "
                            "implementation roots. Read-only generated contract paths are "
                            "always rejected. The view command may inspect the current files "
                            "and directories listed in the prompt; descend into a listed "
                            "directory when you need a declaration. Generated build, Gradle, "
                            "dependency, and distribution outputs cannot be viewed; use the "
                            "concise run_task_check result instead. Use absolute paths from the "
                            "user prompt. Create may replace an existing editable file."
                        ),
                    }
                )
                for instance in instances
            ]

    class WorkspaceGrepExecutor(GrepExecutor):
        """검색 범위를 이 작업의 임시 workspace 안으로 제한한다."""

        def __init__(self, working_dir, missing_output_paths):
            super().__init__(working_dir=working_dir)
            self.missing_output_paths = {
                Path(path).resolve() for path in missing_output_paths
            }

        def __call__(self, action, conversation=None):
            search_path = Path(action.path).resolve() if action.path else self.working_dir
            try:
                relative = search_path.relative_to(self.working_dir)
            except ValueError:
                return GrepObservation.from_text(
                    text=(
                        "Search outside the assigned workspace is not allowed. "
                        "Use the current task prompt and source paths."
                    ),
                    matches=[],
                    pattern=action.pattern,
                    search_path=str(search_path),
                    include_pattern=action.include,
                    is_error=True,
                )
            pattern = action.pattern.casefold()
            missing_target = next(
                (
                    path
                    for path in self.missing_output_paths
                    if not path.is_file()
                    and (
                        path.name.casefold() in pattern
                        or path.stem.casefold() in pattern
                    )
                ),
                None,
            )
            if missing_target is not None:
                return GrepObservation.from_text(
                    text=(
                        f"{missing_target.name} is a contracted output and does not exist "
                        "yet. Do not search for it. Create it with restricted_file_editor "
                        "after reading the embedded contracts and named source files."
                    ),
                    matches=[],
                    pattern=action.pattern,
                    search_path=str(search_path),
                    include_pattern=action.include,
                    is_error=True,
                )
            if any(
                part in {"build", ".gradle", "node_modules", "dist"}
                for part in relative.parts
            ):
                return GrepObservation.from_text(
                    text=(
                        "Generated build and dependency outputs are not searchable source "
                        "context. Use the concise run_task_check result instead."
                    ),
                    matches=[],
                    pattern=action.pattern,
                    search_path=str(search_path),
                    include_pattern=action.include,
                    is_error=True,
                )
            return super().__call__(action, conversation)

    class RestrictedGrepTool(GrepTool):
        # registry 이름은 충돌을 피하기 위해 별도 값을 쓰지만, LLM에는 익숙한 ``grep``
        # 하나만 보인다. 도구 이름을 새로 가르칠 필요가 없어 prompt도 짧게 유지된다.
        name = "grep"

        @classmethod
        def create(cls, conv_state, missing_output_paths):
            instances = super().create(conv_state)
            return [
                instance.model_copy(
                    update={
                        "executor": WorkspaceGrepExecutor(
                            working_dir=conv_state.workspace.working_dir,
                            missing_output_paths=missing_output_paths,
                        ),
                        "description": (
                            "Search source text only inside the assigned workspace. "
                            "Generated build, Gradle, dependency, and distribution "
                            "directories are excluded. Use an absolute directory path from "
                            "the current task prompt."
                        ),
                    }
                )
                for instance in instances
            ]

    registry_name = "easydep_restricted_file_editor"
    if not _RESTRICTED_EDITOR_REGISTERED:
        with _RESTRICTED_EDITOR_REGISTRATION_LOCK:
            if not _RESTRICTED_EDITOR_REGISTERED:
                register_tool(registry_name, RestrictedFileEditorTool)
                _RESTRICTED_EDITOR_REGISTERED = True
    grep_registry_name = "easydep_restricted_grep"
    if not _RESTRICTED_GREP_REGISTERED:
        with _RESTRICTED_EDITOR_REGISTRATION_LOCK:
            if not _RESTRICTED_GREP_REGISTERED:
                register_tool(grep_registry_name, RestrictedGrepTool)
                _RESTRICTED_GREP_REGISTERED = True
    task_check_tool_name = register_task_check_tool()
    model = configured_model()
    raw_temperature = llm_config["temperature"]
    raw_max_output = llm_config["maxOutputTokens"]
    if not isinstance(raw_temperature, (int, float, str)):
        raise TypeError("implementation LLM temperature must be numeric")
    if not isinstance(raw_max_output, (int, str)):
        raise TypeError("implementation LLM maxOutputTokens must be an integer")
    profile = profile_for(
        model,
        fallback_temperature=float(raw_temperature),
        fallback_max_tokens=int(raw_max_output),
    )
    if profile.preserve_reasoning_on_tool_turn:
        # OpenHands는 지원 모델의 assistant reasoning_content를 다음 tool turn에 다시
        # 싣는 기능이 있지만, proxy 접두사가 붙은 최신 모델 ID는 내장 목록에 늦게 반영될
        # 수 있다. 실제 요청 모델은 바꾸지 않고 정확한 canonical ID만 기능 목록에 보탠다.
        from openhands.sdk.llm.utils.model_features import SEND_REASONING_CONTENT_MODELS

        reasoning_model = canonical_model_id(model)
        if reasoning_model not in SEND_REASONING_CONTENT_MODELS:
            SEND_REASONING_CONTENT_MODELS.append(reasoning_model)
    is_qwen_coder = "qwen3-coder" in model.lower()
    requested_output = configured_max_output_tokens(int(raw_max_output))
    llm_options: dict[str, Any] = {
        "model": model,
        "api_key": SecretStr(api_key),
        "base_url": configured_base_url(),
        "extra_headers": configured_headers(),
        "temperature": profile.temperature,
        "max_output_tokens": profile.completion_limit(requested_output),
    }
    if is_qwen_coder:
        # NVIDIA documents Qwen3-Coder as a non-thinking model and recommends not
        # overriding both temperature and top_p in the same request.
        pass
    else:
        if profile.top_p is not None:
            llm_options["top_p"] = profile.top_p
        if resolved_reasoning := profile.resolve_reasoning(reasoning_effort):
            llm_options["reasoning_effort"] = resolved_reasoning
        if extra_body := profile.extra_body():
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
                name=registry_name,
                params={
                    "allowed_edits_files": allowed_files,
                    "allowed_edit_roots": editable_roots or [],
                    "immutable_edit_paths": immutable_paths or [],
                },
            ),
            Tool(
                name=grep_registry_name,
                params={
                    "missing_output_paths": [
                        path for path in allowed_files if not Path(path).is_file()
                    ]
                },
            ),
            Tool(
                name=task_check_tool_name,
                params={
                    "task_type": task_type,
                    "allowed_write_paths": verification_paths or [],
                },
            ),
        ],
        include_default_tools=["FinishTool"],
        system_prompt=system_prompt,
        # OpenHands가 오래된 도구 기록을 요약하도록 공식 condenser를 그대로 사용한다.
        # 별도 요약 상태를 만들지 않으며 최초 지시와 최근 작업은 SDK 기본값으로 보존한다.
        condenser=LLMSummarizingCondenser(
            llm=llm.model_copy(update={"usage_id": "implementation_condenser"}),
        ),
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(sandbox),
        callbacks=callbacks,
        max_iteration_per_run=max_iterations,
        # 반복 action/error와 같은 파일 편집 루프는 SDK가 먼저 감지한다. 전체 자동 수리
        # 횟수와는 별개이며, 감지 뒤에는 위 실행부가 새 Conversation으로 작업을 인계한다.
        stuck_detection=True,
        visualizer=None,
    )
    return conversation, agent


def _path_is_immutable(path: str, immutable_paths: set[str]) -> bool:
    """파일 경로가 생성 계약 파일 또는 그 하위에 있는지 확인한다."""
    normalized = path.replace("\\", "/").rstrip("/")
    return any(
        normalized == root.rstrip("/") or normalized.startswith(root.rstrip("/") + "/")
        for root in immutable_paths
    )
