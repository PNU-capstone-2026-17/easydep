from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import warnings
from pathlib import Path

from app.config import settings
from app.metrics import langsmith as langsmith_metrics
from app.validation import RepairAttempt, RepairLedger, stable_digest

from ..planning.design_context import (
    read_generated_java_contracts,
    referenced_openapi_model_names,
)
from ..workflows.conformance import entity_public_signature_violations
from ..workflows.repair import referenced_source_paths
from .prompts import (
    FRONTEND_SYSTEM_PROMPT,
    IMPLEMENTATION_SYSTEM_PROMPT,
    render_frontend_verification_feedback,
    render_verification_feedback,
)
from .provider import (
    MAX_PROVIDER_RETRIES,
    configured_api_key,
    configured_max_output_tokens,
    configured_model,
    openhands_compatibility,
    provider_retry_delay,
    transient_provider_error,
)
from .verification.build import (
    WorkspaceVerificationError,
    verify_agent_workspace,
)
from .workspace import (
    changed_files,
    cleanup_agent_workspace,
    load_task,
    missing_required_outputs,
    prepare_agent_workspace,
    read_allowed_sources,
    snapshot_files,
    task_base_package,
)

# 하나의 기능 작업은 여러 파일을 함께 만들므로 한 대화가 중간에 끊기지 않을 만큼의
# tool turn을 준다. 두 값 모두 전체 생성·수리 횟수 상한이 아니다. 대화 하나가 멈추는
# 것을 막는 안전 한도이며, 바깥 loop는 build가 통과할 때까지 새 대화로 계속된다.
MAX_AGENT_TURN_ITERATIONS = 32
MAX_REPAIR_TURN_ITERATIONS = 16
MAX_REASONING_BUDGET = 256
_RESTRICTED_EDITOR_REGISTERED = False
_RESTRICTED_EDITOR_REGISTRATION_LOCK = threading.Lock()


def _repair_contract_context(context: dict[str, object]) -> str:
    """수리에 필요한 코드 계약과 해당 유스케이스 근거만 짧게 반환한다."""
    contracts = context.get("generatedJavaContracts")
    if not isinstance(contracts, str) or not contracts.strip():
        contracts = context.get("generatedTypescriptContracts")
    sections: list[str] = []
    if isinstance(contracts, str) and contracts.strip():
        sections.append("Generated contracts:\n" + contracts[:16000])
    evidence = {
        key: context[key]
        for key in ("requirements", "useCaseArtifacts", "scenarios")
        if context.get(key)
    }
    if evidence:
        sections.append(
            "Relevant requirements and scenarios:\n"
            + json.dumps(evidence, ensure_ascii=False, indent=2)[:16000]
        )
    return "\n\n".join(sections)[:28000]


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

    profile_dir = (
        Path(tempfile.gettempdir())
        / f"easydep-openhands-profiles-{os.getpid()}"
    )
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
        if tool_name:
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
    model: str,
    base_url: str,
) -> dict[str, object]:
    compatibility = openhands_compatibility()
    plan = {
        "schemaVersion": "openhands-execution-plan/v1alpha1",
        "mode": requested_mode,
        "runnable": all(
            bool(compatibility[key])
            for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled", "apiKeyConfigured")
        ),
        "compatibility": compatibility,
        "llm": {"provider": "nvidia-nim", "model": model, "baseUrl": base_url},
        "taskOrder": [task["task_id"] for task in tasks],
        "isolation": "copy source-only application to an ASCII temp workspace, restricted file editor, validate source diff, verify Gradle with repair feedback, promote allowed files only",
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
    contract_context: str = "",
    existing_outputs: str = "",
) -> str:
    """Keep missing-output retries small enough for agents that stopped silently."""
    missing_test = any("/src/test/" in path.replace("\\", "/") for path in missing_outputs)
    if missing_test:
        task_hint = (
            "Create the missing focused test from the existing application and generated "
            "contracts. Assert observable behavior; do not copy a prompt or private helper."
        )
    else:
        task_hint = "Use the existing generated application contract; do not inspect or list directories."
    files = "\n".join(f"- `{path}`" for path in missing_outputs)
    contracts = (
        "\n\nExact generated contracts (immutable):\n```text\n"
        + contract_context
        + "\n```"
        if contract_context
        else ""
    )
    existing = (
        "\n\nExisting contracted source (read-only for this repair):\n"
        + existing_outputs[:12000]
        if existing_outputs
        else ""
    )
    return (
        "The previous agent round did not create the required output files. "
        "Use the file editor's create operation now with the exact absolute paths below; "
        "do not reply with an explanation. "
        "Create only the missing files, preserve all existing files, and finish "
        "immediately after writing them. Parent directories already exist. "
        "Do not use /workspace, /application, relative paths, view, or directory-listing tools.\n\n"
        + task_hint
        + "\n\nRequired missing outputs (absolute paths):\n"
        + files
        + contracts
        + existing
    )


def _promote_changed_files(
    sandbox: Path, run_root: Path, changed: set[str]
) -> None:
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
        shutil.copy2(source, target)


def _restore_unauthorized_files(
    sandbox: Path, run_root: Path, unauthorized: list[str]
) -> None:
    """Restore files written outside a task's ownership boundary."""
    for relative in unauthorized:
        sandbox_path = sandbox / relative
        baseline = run_root / relative
        if baseline.is_file():
            sandbox_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(baseline, sandbox_path)
        elif sandbox_path.exists():
            sandbox_path.unlink()


def _execute_openhands_task(run_root: Path, task_id: str) -> dict[str, object]:
    task = load_task(run_root, task_id)
    task_type = str(task.get("task_type", ""))
    app_id = _run_app_id(run_root)
    editable_paths = [str(path) for path in task.get("allowed_write_paths", [])]
    required_paths = [
        str(path)
        for path in task.get("required_output_paths", editable_paths)
    ]
    immutable_paths = {
        str(path).replace("\\", "/")
        for path in task.get("immutable_paths", [])
    }

    compatibility = openhands_compatibility()
    missing = [key for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled", "apiKeyConfigured") if not compatibility[key]]
    if missing:
        raise RuntimeError("OpenHands live mode prerequisites are missing: " + ", ".join(missing))

    sandbox = prepare_agent_workspace(run_root, task)
    before = snapshot_files(sandbox)
    prompt = (run_root / task["prompt_file"]).read_text(encoding="utf-8")
    context = json.loads((run_root / task["context_file"]).read_text(encoding="utf-8"))
    api_model_names = (
        set()
        if task_type == "frontend-implementation"
        else referenced_openapi_model_names(str(context.get("openapi", "")))
    )
    missing_api_models = {
        name for name in api_model_names if f"// api/model/{name}.java" not in prompt
    }
    if missing_api_models:
        prompt += "\n\n## Exact generated OpenAPI model contracts\n\n```java\n"
        prompt += read_generated_java_contracts(
            run_root,
            task_base_package(task),
            set(),
            missing_api_models,
        )
        prompt += "\n```\n"
    allowed_absolute = [str((sandbox / path).resolve()) for path in editable_paths]
    prompt += "\n\n## Enforced absolute write paths\n\n" + "\n".join(
        f"- `{path}`" for path in allowed_absolute
    )

    api_key = configured_api_key()
    assert api_key is not None
    execution_dir = run_root / "reports" / "agent-executions"
    attempt = execution_attempt(run_root, task_id)
    journal = EventJournal(
        execution_dir / f"{task_id}.attempt-{attempt:03d}.events.jsonl"
    )
    started = time.monotonic()
    agent = None
    conversation_warning: str | None = None
    # 같은 작업이 다른 담당 오류 때문에 다시 실행되더라도 이전 수리 실패를 잊지 않는다.
    # 별도 저장 형식을 만들지 않고 이미 남긴 최신 실행 결과의 repairHistory를 재사용한다.
    repair_ledger = load_repair_ledger(execution_dir, task_id)
    try:
        round_prompt = prompt
        round_allowed = allowed_absolute
        round_iteration_limit = MAX_AGENT_TURN_ITERATIONS
        provider_retries = 0
        repair_attempt = 0
        while True:
            reasoning_effort = os.environ.get(
                "OPENHANDS_REPAIR_REASONING_EFFORT" if repair_attempt else "OPENHANDS_REASONING_EFFORT",
                str(
                    task["llm"].get(
                        "repairReasoningEffort" if repair_attempt else "reasoningEffort",
                        settings.implementation_repair_reasoning_effort
                        if repair_attempt
                        else settings.implementation_reasoning_effort,
                    )
                ),
            )
            # A transient provider failure is a transport concern, not a source
            # verification failure. Retry the same conversation round without
            # recording a semantic repair attempt; otherwise a NIM
            # outage is reported misleadingly as missing files and the agent is
            # sent an unnecessary (and expensive) repair prompt.
            while True:
                conversation, agent = create_openhands_conversation(
                    sandbox,
                    round_allowed,
                    api_key,
                    task["llm"],
                    callbacks=[journal],
                    max_iterations=round_iteration_limit,
                    reasoning_effort=reasoning_effort,
                    system_prompt=(
                        FRONTEND_SYSTEM_PROMPT
                        if task_type == "frontend-implementation"
                        else IMPLEMENTATION_SYSTEM_PROMPT
                    ),
                )
                conversation_error: Exception | None = None
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
                        "ls_provider": "nvidia-nim",
                        "ls_model_name": configured_model(str(task["llm"]["model"])),
                    },
                ) as trace:
                    try:
                        conversation.send_message(round_prompt)
                        conversation.run()
                    except Exception as error:
                        # A provider can reject the final turn after the agent has
                        # already written every contracted output. Keep the warning
                        # for the successful result, but retry transient failures
                        # before consulting output or build verification.
                        conversation_error = error
                        conversation_warning = (
                            f"{error.__class__.__name__}: {error}"
                        )
                    finally:
                        usage = _conversation_token_usage(conversation)
                        if usage is not None:
                            trace.set_usage(
                                input_tokens=usage[0], output_tokens=usage[1]
                            )
                        conversation.close()
                if conversation_error is None or not transient_provider_error(
                    conversation_error
                ):
                    break
                # A provider may fail while emitting its final response after
                # the agent has already written every contracted file. In that
                # case do not repeat the generation and risk overwriting valid
                # work; continue to deterministic verification instead.
                if not missing_required_outputs(
                    sandbox, required_paths
                ):
                    break
                provider_retries += 1
                if provider_retries > MAX_PROVIDER_RETRIES:
                    raise RuntimeError(
                        "NVIDIA NIM remained unavailable after "
                        f"{MAX_PROVIDER_RETRIES} transport retries"
                    ) from conversation_error
                time.sleep(provider_retry_delay(provider_retries))

            missing_outputs = missing_required_outputs(
                sandbox, required_paths
            )
            if missing_outputs:
                finding_keys = tuple(f"missing:{path}" for path in missing_outputs)
                candidate_digest = stable_digest(
                    read_allowed_sources(sandbox, editable_paths)
                )
                repeated = repair_attempt > 0 and any(
                    item.candidate_digest == candidate_digest
                    for item in repair_ledger.attempts
                    if item.candidate_digest
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
                    )
                )
                round_allowed = [
                    str((sandbox / path).resolve()) for path in missing_outputs
                ]
                round_iteration_limit = MAX_REPAIR_TURN_ITERATIONS
                round_prompt = _render_missing_output_repair_prompt(
                    round_allowed,
                    _repair_contract_context(context),
                    read_allowed_sources(
                        sandbox,
                        [
                            path
                            for path in editable_paths
                            if path not in missing_outputs
                        ],
                    ),
                )
                if repeated:
                    round_prompt = (
                        "The previous repair turn made no file change. The complete current "
                        "source and repair history are included below. Use create or "
                        "str_replace now; do not finish without changing a repair target.\n\n"
                        + round_prompt
                    )
                round_prompt += (
                    "\n\n## Accumulated repair history\n\n"
                    + repair_ledger.prompt_context()
                )
                repair_attempt += 1
                continue

            changed = changed_files(before, snapshot_files(sandbox))
            allowed = set(editable_paths)
            unauthorized = sorted(path for path in changed if path not in allowed)
            if unauthorized:
                _restore_unauthorized_files(sandbox, run_root, unauthorized)
                changed = {path for path in changed if path in allowed}
            try:
                # EasyDep은 source를 정규식으로 고치지 않는다. OpenHands가 현재 파일과
                # compiler/test 결과를 보고 수정하며, 공개 계약은 최종 conformance 검사에서
                # 별도로 보호한다. 실제 HTTP 흐름 검사는 wiring 작업의 FlowTest에 포함된다.
                controller_body_paths = context.get("controllerBodyPaths", [])
                unfinished_controllers: list[str] = []
                if isinstance(controller_body_paths, list):
                    for path in controller_body_paths:
                        if not isinstance(path, str) or not (sandbox / path).is_file():
                            continue
                        source = (sandbox / path).read_text(encoding="utf-8")
                        if "EASYDEP_CONTROLLER_BODY_REQUIRED" in source:
                            unfinished_controllers.append(path)
                if unfinished_controllers:
                    raise WorkspaceVerificationError(
                        {
                            "command": ["controller-body-completion"],
                            "exitCode": 1,
                            "durationMs": 0,
                            "stdout": "",
                            "stderr": "\n".join(
                                f"Unimplemented Controller body: {path}"
                                for path in unfinished_controllers
                            ),
                            "testResults": "",
                        }
                    )
                editable_entities = [
                    path
                    for path in editable_paths
                    if "/bce/" in "/" + path.replace("\\", "/")
                    and path.endswith(".java")
                ]
                signature_violations = entity_public_signature_violations(
                    run_root, sandbox, editable_entities
                ) if editable_entities else []
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
                verification = verify_agent_workspace(
                    sandbox,
                    task_type,
                    editable_paths,
                )
                changed = changed_files(before, snapshot_files(sandbox))
                break
            except WorkspaceVerificationError as error:
                referenced = referenced_source_paths(error.evidence)
                # 앞 유스케이스가 만든 Service의 잘못된 import처럼 compiler가 실제 원인
                # 파일을 알려 주면 현재 수리 대화에서 함께 고친다. 유스케이스 묶음은
                # coordinator가 순차 실행하므로 다른 agent와 같은 파일을 덮어쓰지 않는다.
                # OpenAPI 같은 생성 계약은 immutable_paths 검사로 계속 보호한다.
                for path in referenced:
                    normalized = path.replace("\\", "/")
                    if (
                        normalized.startswith("application/")
                        and not _path_is_immutable(normalized, immutable_paths)
                        and (sandbox / normalized).is_file()
                        and normalized not in editable_paths
                    ):
                        editable_paths.append(normalized)
                evidence_digest = stable_digest(error.evidence)
                finding_keys = (f"verification:{evidence_digest}",)
                candidate_digest = stable_digest(
                    read_allowed_sources(sandbox, editable_paths)
                )
                repeated = repair_attempt > 0 and any(
                    item.candidate_digest == candidate_digest
                    for item in repair_ledger.attempts
                    if item.candidate_digest
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
                        detail=str(error.evidence)[-4000:],
                    )
                )
                # 유스케이스 test의 컴파일 오류에는 보통 호출한 test 경로만 나오고,
                # 실제로 빠진 Adapter나 Service 경로는 나오지 않는다. 이때 test 하나만
                # 편집하게 하면 가짜 구현을 test 안에 넣도록 유도한다. 유스케이스 작업은
                # planner가 이미 기능 단위로 좁힌 소유 파일 전체에서 원인을 고치게 한다.
                repair_paths = (
                    list(editable_paths)
                    if task_type == "use-case"
                    else select_repair_paths(error.evidence, editable_paths)
                )
                round_allowed = [str((sandbox / path).resolve()) for path in repair_paths]
                round_iteration_limit = MAX_REPAIR_TURN_ITERATIONS
                feedback_renderer = (
                    render_frontend_verification_feedback
                    if task_type == "frontend-implementation"
                    else render_verification_feedback
                )
                # Do not retransmit the original design prompt on every repair
                # conversation.  The diagnostic plus the files selected by the
                # verifier and accumulated history are sufficient for a local correction.
                feedback_kwargs = {}
                repair_contract = _repair_contract_context(context)
                if repair_contract:
                    feedback_kwargs["generated_contracts"] = repair_contract
                if task_type == "wiring":
                    semantic_contract = context.get("semanticContract")
                    if isinstance(semantic_contract, dict):
                        feedback_kwargs["semantic_contract"] = semantic_contract
                round_prompt = feedback_renderer(
                    error.evidence,
                    read_allowed_sources(sandbox, repair_paths),
                    repair_paths,
                    **feedback_kwargs,
                )
                read_only_references = [
                    path
                    for path in referenced
                    if path not in repair_paths
                    and (sandbox / path).is_file()
                ]
                if read_only_references:
                    # 호출하는 쪽에서만 오류가 표시되는 생성자·메서드 불일치도 모델이
                    # 추측하지 않도록 실제 사용 코드를 읽기 전용 참고 자료로 제공한다.
                    round_prompt += (
                        "\n\n## Referenced collaborator sources (read-only)\n\n"
                        + read_allowed_sources(sandbox, read_only_references)
                    )
                if repeated:
                    round_prompt = (
                        "The previous repair turn made no file change. The complete current "
                        "source and repair history are included below. Use create or "
                        "str_replace now; do not finish without changing a repair target.\n\n"
                        + round_prompt
                    )
                round_prompt += (
                    "\n\n## Accumulated repair history\n\n"
                    + repair_ledger.prompt_context()
                )
                repair_attempt += 1
    except Exception as error:
        failure = {
            "taskId": task_id,
            "taskType": task.get("task_type", "control"),
            "promptSha256": task.get("prompt_sha256"),
            "status": "FAILED",
            "effectiveModel": configured_model(str(task["llm"]["model"])),
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
        shutil.copy2(journal.path, execution_dir / f"{task_id}.events.jsonl")
        raise
    _promote_changed_files(sandbox, run_root, changed)
    result = {
        "taskId": task_id,
        "taskType": task.get("task_type", "control"),
        "promptSha256": task.get("prompt_sha256"),
        "effectiveModel": configured_model(str(task["llm"]["model"])),
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
    shutil.copy2(journal.path, execution_dir / f"{task_id}.events.jsonl")
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
                if isinstance(task, dict) and task.get("taskId") == task_id
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
    (execution_dir / f"{task_id}.result.json").write_text(
        content, encoding="utf-8"
    )


def validate_openhands_adapter(run_root: Path, task_id: str) -> dict[str, object]:
    """Initialize the real SDK and restricted tool without making an LLM request."""
    task = load_task(run_root, task_id)
    compatibility = openhands_compatibility()
    missing = [key for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled") if not compatibility[key]]
    if missing:
        raise RuntimeError("OpenHands SDK prerequisites are missing: " + ", ".join(missing))
    sandbox = prepare_agent_workspace(run_root, task)
    allowed = [str((sandbox / path).resolve()) for path in task["allowed_write_paths"]]
    validation_journal = EventJournal(
        run_root / "reports" / f"agent-validation-{task_id}.events.jsonl"
    )
    conversation, agent = create_openhands_conversation(
        sandbox,
        allowed,
        "validation-only-key",
        task["llm"],
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
        set(tools) != {"restricted_file_editor", "finish"}
        or not enforced
        or not unauthorized_blocked
        or not allowed_write_succeeded
        or not allowed_overwrite_succeeded
        or not file_path_alias_accepted
    ):
        raise RuntimeError("Restricted FileEditorTool was not initialized with the exact allowlist")
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
        "maxRepairConversationToolTurns": MAX_REPAIR_TURN_ITERATIONS,
        "stuckDetection": False,
        "verificationRepairPolicy": "history-and-progress/v1",
        "reasoningBudgetCap": MAX_REASONING_BUDGET,
        "reasoningEffort": task["llm"].get(
            "reasoningEffort", settings.implementation_reasoning_effort
        ),
        "repairReasoningEffort": task["llm"].get(
            "repairReasoningEffort", settings.implementation_repair_reasoning_effort
        ),
        "systemPrompt": (
            "focused-frontend-implementation"
            if task.get("task_type") == "frontend-implementation"
            else "focused-java-implementation"
        ),
        "validationEventCount": validation_journal.event_count,
        "allowedWritePaths": allowed,
        "modelCallMade": False,
        "effectiveModel": configured_model(str(task["llm"]["model"])),
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
    callbacks: list[object] | None = None,
    max_iterations: int = MAX_AGENT_TURN_ITERATIONS,
    reasoning_effort: str = "medium",
    system_prompt: str = IMPLEMENTATION_SYSTEM_PROMPT,
):
    global _RESTRICTED_EDITOR_REGISTERED

    from openhands.sdk import LLM, Agent, Conversation, Tool, register_tool
    from openhands.tools.file_editor import FileEditorAction, FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from pydantic import AliasChoices, Field, SecretStr

    _configure_openhands_profile_store()

    class CompatibleFileEditorAction(FileEditorAction):
        """Accept the common file_path spelling without advertising it to the LLM."""

        path: str = Field(
            description="Absolute path to the allowlisted file. Use the argument name path.",
            validation_alias=AliasChoices("path", "file_path"),
            serialization_alias="path",
        )

    class ReplaceableFileEditorExecutor(FileEditorExecutor):
        def __call__(self, action, conversation=None):
            target = Path(action.path).resolve()
            can_replace = bool(
                action.command == "create"
                and action.file_text is not None
                and target.is_file()
                and self.allowed_edits_files is not None
                and target in self.allowed_edits_files
            )
            if not can_replace:
                return super().__call__(action, conversation)

            try:
                old_content = target.read_text(encoding="utf-8")
                target.write_text(action.file_text, encoding="utf-8")
            except OSError as error:
                return FileEditorObservation.from_text(
                    text=f"Could not replace allowlisted file: {error}",
                    command="create",
                    is_error=True,
                )
            return FileEditorObservation.from_text(
                text=f"Allowlisted file replaced successfully at: {target}",
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

    class RestrictedFileEditorTool(FileEditorTool):
        @classmethod
        def create(cls, conv_state, allowed_edits_files):
            instances = super().create(conv_state)
            return [
                instance.model_copy(
                    update={
                        "executor": ReplaceableFileEditorExecutor(
                            workspace_root=conv_state.workspace.working_dir,
                            allowed_edits_files=allowed_edits_files,
                        ),
                        "action_type": CompatibleFileEditorAction,
                        "description": (
                            "Create or edit only the explicitly allowlisted text files. "
                            "Their parent directories already exist and were write-tested. "
                            "Use the absolute paths from the user prompt and create every "
                            "requested file directly; do not browse directories. "
                            "For these allowlisted files only, create may replace an "
                            "existing file when a broad repair is required."
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
    model = configured_model(str(llm_config["model"]))
    is_qwen_coder = "qwen3-coder" in model.lower()
    is_gpt_oss = "gpt-oss" in model.lower()
    chat_template_kwargs = dict(llm_config["chatTemplateKwargs"])
    chat_template_kwargs.update({"enable_thinking": True, "low_effort": True})
    reasoning_budget = min(
        int(llm_config.get("reasoningBudget", MAX_REASONING_BUDGET)),
        MAX_REASONING_BUDGET,
    )
    llm_options: dict[str, object] = {
        "model": model,
        "api_key": SecretStr(api_key),
        "base_url": settings.base_url or str(llm_config["baseUrl"]),
        "temperature": 0.2 if is_qwen_coder else float(llm_config["temperature"]),
        "max_output_tokens": configured_max_output_tokens(int(llm_config["maxOutputTokens"])),
    }
    if is_gpt_oss:
        # GPT-OSS exposes native reasoning_effort and tool calling. Nemotron's
        # chat_template_kwargs/reasoning_budget are not valid for this model.
        llm_options["reasoning_effort"] = reasoning_effort
    elif is_qwen_coder:
        # NVIDIA documents Qwen3-Coder as a non-thinking model and recommends not
        # overriding both temperature and top_p in the same request.
        pass
    else:
        llm_options["top_p"] = float(llm_config["topP"])
        llm_options["litellm_extra_body"] = {
            "chat_template_kwargs": chat_template_kwargs,
            "reasoning_budget": reasoning_budget,
        }
    warnings.filterwarnings(
        "ignore",
        message=r"Cost calculation failed:.*",
        module=r"openhands\.sdk\.llm\.utils\.telemetry",
    )
    agent = Agent(
        llm=LLM(**llm_options),
        tools=[Tool(name=registry_name, params={
            "allowed_edits_files": allowed_files,
        })],
        include_default_tools=["FinishTool"],
        system_prompt=system_prompt,
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(sandbox),
        callbacks=callbacks,
        max_iteration_per_run=max_iterations,
        stuck_detection=False,
        visualizer=None,
    )
    return conversation, agent


def select_repair_paths(
    evidence: dict[str, object], allowed_paths: list[str]
) -> list[str]:
    """Limit compiler repairs to the allowlisted files named in diagnostics.

    Runtime test failures generally have no source path, so they retain the full
    allowlist because either the implementation or its test may need correction.
    """
    if str(evidence.get("testResults", "")).strip():
        # A runtime assertion may expose either an implementation defect or a
        # faulty generated test. Stack traces normally name only the test file,
        # which is not sufficient evidence to lock the implementation out.
        return list(allowed_paths)

    output = "\n".join(str(value) for value in evidence.values())
    selected = [
        relative
        for relative in allowed_paths
        if relative.replace("/", "\\") in output
        or relative.replace("\\", "/") in output
    ]
    return selected or list(allowed_paths)


def _path_is_immutable(path: str, immutable_paths: set[str]) -> bool:
    """파일 경로가 생성 계약 파일 또는 그 하위에 있는지 확인한다."""
    normalized = path.replace("\\", "/").rstrip("/")
    return any(
        normalized == root.rstrip("/")
        or normalized.startswith(root.rstrip("/") + "/")
        for root in immutable_paths
    )
