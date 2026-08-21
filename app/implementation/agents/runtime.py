from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import warnings
from pathlib import Path

from app.core.config import settings
from ..planning.design_context import (
    read_generated_java_contracts,
    referenced_openapi_model_names,
)
from .verification.frontend import (
    frontend_contract_violations,
    has_mutating_operations,
)
from .verification.build import (
    WorkspaceVerificationError,
    persistence_reserved_identifier_markers,
    production_placeholder_markers,
    production_test_library_markers,
    verify_agent_workspace,
)
from .verification.e2e import (
    e2e_contract_violations,
    repair_nested_e2e_members,
    repair_orphaned_java_test_statements,
)
from ..workflows.repair import referenced_source_paths
from .provider import (
    MAX_PROVIDER_RETRIES,
    configured_api_key,
    configured_max_output_tokens,
    configured_model,
    openhands_compatibility,
    provider_retry_delay,
    transient_provider_error,
)
from .prompts import (
    FRONTEND_SYSTEM_PROMPT,
    IMPLEMENTATION_SYSTEM_PROMPT,
    render_frontend_verification_feedback,
    render_verification_feedback,
)
from .workspace import (
    changed_files,
    load_task,
    missing_required_outputs,
    prepare_agent_workspace,
    read_allowed_sources,
    read_persistence_entity_contracts,
    snapshot_files,
    task_base_package,
)


MAX_AGENT_ITERATIONS = 6
MAX_REPAIR_ITERATIONS = 4
MAX_VERIFICATION_REPAIRS = 6
MAX_REASONING_BUDGET = 256
_RESTRICTED_EDITOR_REGISTERED = False
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def __call__(self, event) -> None:
        event_type = event.__class__.__name__
        tool_name = getattr(event, "tool_name", None)
        if tool_name:
            self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1
        payload = {
            "sequence": self.event_count,
            "timestamp": time.time(),
            "type": event_type,
            "source": getattr(event, "source", None),
            "tool": tool_name,
            "event": event.model_dump(mode="json"),
        }
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
    task = load_task(run_root, task_id)
    task_type = str(task.get("task_type", ""))

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
    if str(task.get("task_type", "")) in {
        "persistence-repositories",
        "persistence-mapping",
        "persistence-schema",
    }:
        persistence_contracts = read_persistence_entity_contracts(
            run_root, task_base_package(task)
        )
        prompt += (
            "\n\n## Exact generated JPA persistence entity contracts\n\n"
            "```java\n" + persistence_contracts + "\n```\n"
        )
    allowed_absolute = [str((sandbox / path).resolve()) for path in task["allowed_write_paths"]]
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
    try:
        round_prompt = prompt
        round_allowed = allowed_absolute
        round_iteration_limit = MAX_AGENT_ITERATIONS
        provider_retries = 0
        for repair_attempt in range(MAX_VERIFICATION_REPAIRS + 1):
            reasoning_effort = os.environ.get(
                "OPENHANDS_REPAIR_REASONING_EFFORT" if repair_attempt else "OPENHANDS_REASONING_EFFORT",
                "high" if repair_attempt else "medium",
            )
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
            try:
                conversation.send_message(round_prompt)
                conversation.run()
            except Exception as error:
                # A provider can reject the final turn after the agent has already
                # written every contracted output (for example, while emitting
                # `finish`). Treat the conversation as transport, then let the
                # output boundary and build verification decide whether the task
                # is usable. Never copy unverified files merely because they exist.
                conversation_error = error
                conversation_warning = (
                    f"{error.__class__.__name__}: {error}"
                )
            finally:
                conversation.close()

            missing_outputs = missing_required_outputs(
                sandbox, task["allowed_write_paths"]
            )
            if missing_outputs:
                if conversation_error is not None and transient_provider_error(conversation_error):
                    provider_retries += 1
                    if provider_retries > MAX_PROVIDER_RETRIES:
                        raise RuntimeError(
                            "NVIDIA NIM remained unavailable after "
                            f"{MAX_PROVIDER_RETRIES} transport retries"
                        ) from conversation_error
                    time.sleep(provider_retry_delay(provider_retries))
                if repair_attempt >= MAX_VERIFICATION_REPAIRS:
                    missing_error = RuntimeError(
                        "Agent did not create required task outputs: "
                        + ", ".join(missing_outputs)
                    )
                    if conversation_error is not None:
                        raise RuntimeError(
                            "OpenHands conversation failed before required task "
                            "outputs were created (missing: "
                            + ", ".join(missing_outputs)
                            + f"): {conversation_error}"
                        ) from conversation_error
                    raise missing_error
                round_allowed = [
                    str((sandbox / path).resolve()) for path in missing_outputs
                ]
                round_iteration_limit = MAX_REPAIR_ITERATIONS
                round_prompt = prompt + "\n\n## Missing required outputs\n\n" + (
                    "The previous round did not create every contracted output. "
                    "Create each file listed below now, then call finish immediately. "
                    "Do not rewrite outputs that already exist.\n\n"
                    + "\n".join(f"- `{path}`" for path in missing_outputs)
                )
                continue

            changed = changed_files(before, snapshot_files(sandbox))
            allowed = set(task["allowed_write_paths"])
            unauthorized = sorted(path for path in changed if path not in allowed)
            if unauthorized:
                raise RuntimeError(
                    "Agent changed files outside its boundary: " + ", ".join(unauthorized)
                )
            try:
                if str(task.get("task_type", "")) == "configuration":
                    remove_duplicate_component_adapter_beans(sandbox, task)
                    normalize_spring_boot_repository_discovery(sandbox, task)
                placeholders = production_placeholder_markers(
                    sandbox, task["allowed_write_paths"]
                )
                test_libraries = production_test_library_markers(
                    sandbox, task["allowed_write_paths"]
                )
                if placeholders or test_libraries:
                    violations = []
                    if placeholders:
                        violations.append(
                            "Production outputs contain unresolved TODO/FIXME/placeholder markers:"
                        )
                        violations.extend(placeholders)
                    if test_libraries:
                        violations.append(
                            "Production outputs must not import or call Mockito/JUnit:"
                        )
                        violations.extend(test_libraries)
                    raise WorkspaceVerificationError(
                        {
                            "command": ["production-placeholder-gate"],
                            "exitCode": 1,
                            "durationMs": 0,
                            "stdout": "",
                            "stderr": "\n".join(violations),
                            "testResults": "",
                        }
                    )
                if task_type in {
                    "persistence-entities",
                    "persistence-mapping",
                    "persistence-schema",
                }:
                    reserved_identifiers = persistence_reserved_identifier_markers(
                        sandbox, task["allowed_write_paths"]
                    )
                    if reserved_identifiers:
                        raise WorkspaceVerificationError(
                            {
                                "command": ["persistence-reserved-identifier-gate"],
                                "exitCode": 1,
                                "durationMs": 0,
                                "stdout": "",
                                "stderr": "\n".join(reserved_identifiers),
                                "testResults": "",
                            }
                        )
                if str(task.get("task_type", "")) == "integration-test":
                    e2e_path = sandbox / str(task["allowed_write_paths"][0])
                    repair_nested_e2e_members(e2e_path)
                    repair_orphaned_java_test_statements(e2e_path)
                    context_path = run_root / str(task.get("context_file", ""))
                    semantic_contract = None
                    if context_path.is_file():
                        context = json.loads(context_path.read_text(encoding="utf-8"))
                        candidate = context.get("semanticContract")
                        if isinstance(candidate, dict):
                            semantic_contract = candidate
                    e2e_violations = e2e_contract_violations(
                        e2e_path, semantic_contract
                    )
                    if e2e_violations:
                        raise WorkspaceVerificationError(
                            {
                                "command": ["e2e-semantic-contract-gate"],
                                "exitCode": 1,
                                "durationMs": 0,
                                "stdout": "",
                                "stderr": "\n".join(e2e_violations),
                                "testResults": "",
                            }
                        )
                if task_type == "frontend-implementation":
                    openapi_context = context.get("openapi", {})
                    requires_success_feedback = has_mutating_operations(
                        openapi_context
                    )
                    violations = frontend_contract_violations(
                        sandbox,
                        task["allowed_write_paths"],
                        requires_success_feedback=requires_success_feedback,
                    )
                    if violations:
                        raise WorkspaceVerificationError(
                            {
                                "command": ["frontend-contract-gate"],
                                "exitCode": 1,
                                "durationMs": 0,
                                "stdout": "",
                                "stderr": "\n".join(violations),
                                "testResults": "",
                            }
                        )
                verification = verify_agent_workspace(
                    sandbox, task_type, list(task["allowed_write_paths"])
                )
                break
            except WorkspaceVerificationError as error:
                if _requires_cross_phase_repair(task_type, error.evidence):
                    # The integration test only exposed an upstream persistence
                    # defect. Do not spend local LLM repair rounds rewriting a
                    # test that cannot own the SQL or JPA mapping.
                    raise
                referenced = referenced_source_paths(error.evidence)
                normalized_allowed = {
                    str(path).replace("\\", "/").lower()
                    for path in task["allowed_write_paths"]
                }
                if referenced and not any(
                    path.replace("\\", "/").lower() in normalized_allowed
                    for path in referenced
                ):
                    # This task cannot safely fix a file owned by another phase.
                    # Return the evidence to the workflow repair planner instead
                    # of spending every local repair round on the wrong allowlist.
                    raise
                if repair_attempt >= MAX_VERIFICATION_REPAIRS:
                    raise
                repair_paths = select_repair_paths(
                    error.evidence, task["allowed_write_paths"]
                )
                round_allowed = [str((sandbox / path).resolve()) for path in repair_paths]
                round_iteration_limit = MAX_REPAIR_ITERATIONS
                feedback_renderer = (
                    render_frontend_verification_feedback
                    if task_type == "frontend-implementation"
                    else render_verification_feedback
                )
                round_prompt = prompt + "\n\n## Verification repair\n\n" + feedback_renderer(
                    error.evidence,
                    read_allowed_sources(sandbox, task["allowed_write_paths"]),
                    repair_paths,
                )
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
        }
        if isinstance(error, WorkspaceVerificationError):
            failure["verificationEvidence"] = error.evidence
        write_execution_result(execution_dir, task_id, attempt, failure)
        shutil.copy2(journal.path, execution_dir / f"{task_id}.events.jsonl")
        raise
    for relative in sorted(changed):
        source = sandbox / relative
        target = run_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    result = {
        "taskId": task_id,
        "taskType": task.get("task_type", "control"),
        "promptSha256": task.get("prompt_sha256"),
        "effectiveModel": configured_model(str(task["llm"]["model"])),
        "changedFiles": sorted(changed),
        "outputFiles": list(task["allowed_write_paths"]),
        "verification": verification,
        "tools": sorted(agent._tools) if agent is not None else [],
        "durationMs": int((time.monotonic() - started) * 1000),
        "eventCount": journal.event_count,
        "toolCounts": journal.tool_counts,
        "eventJournal": str(journal.path.relative_to(run_root)).replace("\\", "/"),
        "status": "SUCCEEDED",
    }
    if conversation_warning is not None:
        result["conversationWarning"] = conversation_warning
    write_execution_result(execution_dir, task_id, attempt, result)
    shutil.copy2(journal.path, execution_dir / f"{task_id}.events.jsonl")
    return result


def _requires_cross_phase_repair(
    task_type: str, evidence: dict[str, object]
) -> bool:
    if task_type != "integration-test":
        return False
    output = "\n".join(
        str(evidence.get(key, ""))
        for key in ("stdout", "stderr", "testResults")
    ).lower()
    return any(
        marker in output
        for marker in (
            "jdbcsqlsyntaxerror",
            "syntax error in sql statement",
            'expected "identifier"',
            "reserved keyword",
            # An E2E test is allowed to create only its test source.  A missing
            # Spring Data repository bean is owned by persistence discovery or
            # application wiring, so retrying the test agent cannot resolve it.
            "nosuchbeandefinitionexception",
            "no qualifying bean of type",
            "expected at least 1 bean which qualifies",
            "qualifies as autowire candidate",
        )
    )


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
        "maxIterations": MAX_AGENT_ITERATIONS,
        "maxRepairIterations": MAX_REPAIR_ITERATIONS,
        "stuckDetection": False,
        "verificationRepairLimit": MAX_VERIFICATION_REPAIRS,
        "reasoningBudgetCap": MAX_REASONING_BUDGET,
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
    max_iterations: int = MAX_AGENT_ITERATIONS,
    reasoning_effort: str = "medium",
    system_prompt: str = IMPLEMENTATION_SYSTEM_PROMPT,
):
    global _RESTRICTED_EDITOR_REGISTERED

    from pydantic import AliasChoices, Field, SecretStr
    from openhands.sdk import Agent, Conversation, LLM, Tool, register_tool
    from openhands.tools.file_editor import FileEditorAction, FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor

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
        int(llm_config["reasoningBudget"]), MAX_REASONING_BUDGET
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
        tools=[Tool(name=registry_name, params={"allowed_edits_files": allowed_files})],
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


def remove_duplicate_component_adapter_beans(sandbox: Path, task: dict[str, object]) -> None:
    """Remove manual beans for port adapters already discovered by component scanning.

    The wiring task owns ApplicationConfiguration, whereas adapter tasks own their
    classes.  Retaining both a scanned adapter and an LLM-created ``@Bean`` for its
    port makes Spring injection non-deterministic.  This normalization deliberately
    touches only the configuration output owned by the current task.
    """
    configuration = next(
        (
            sandbox / str(relative)
            for relative in task.get("allowed_write_paths", [])
            if str(relative).endswith("/config/ApplicationConfiguration.java")
        ),
        None,
    )
    if configuration is None or not configuration.is_file():
        return
    java_root = sandbox / "application" / "src" / "main" / "java"
    component_ports: set[str] = set()
    for source in java_root.rglob("*.java"):
        if source == configuration:
            continue
        text = source.read_text(encoding="utf-8")
        if not re.search(r"@(Component|Service|Repository|RestController)\b", text):
            continue
        match = re.search(r"\bimplements\s+([^\{]+)", text)
        if not match:
            continue
        component_ports.update(
            item.strip().split("<", 1)[0].rsplit(".", 1)[-1]
            for item in match.group(1).split(",")
            if item.strip()
        )
    text = configuration.read_text(encoding="utf-8")
    # The wiring generator can leave explanatory comments such as "return an
    # empty string as a placeholder" after it has emitted an otherwise valid
    # factory method.  That wording is not a repair task for the LLM: remove it
    # deterministically before the production-source gate runs.
    text, placeholder_comments_removed = remove_placeholder_comments(text)
    bean = re.compile(
        r"(?ms)^\s*@Bean(?:\s*\([^)]*\))?\s*"
        r"(?:public\s+)?([A-Za-z_]\w*)\s+\w+\s*\([^)]*\)\s*\{"
    )
    removals: list[tuple[int, int]] = []
    for match in bean.finditer(text):
        if match.group(1) not in component_ports:
            continue
        depth = 1
        index = match.end()
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        if depth == 0:
            removals.append((match.start(), index))
    for start, end in reversed(removals):
        text = text[:start] + "\n" + text[end:]
    # Plain persistence mappers are generated without Spring stereotypes.  They
    # have no external side effects and are required constructor dependencies of
    # component-scanned persistence adapters, so register their no-arg instances
    # deterministically when the LLM omitted them from the configuration.
    mapper_root = java_root / "persistence" / "mapper"
    mapper_types: list[str] = []
    if mapper_root.is_dir():
        for mapper in sorted(mapper_root.glob("*.java")):
            mapper_text = mapper.read_text(encoding="utf-8")
            class_match = re.search(r"\bpublic\s+class\s+(\w+)", mapper_text)
            package_match = re.search(r"(?m)^package\s+([\w.]+);", mapper_text)
            if not class_match or not package_match:
                continue
            if re.search(r"@(Component|Service|Repository)\b", mapper_text):
                continue
            mapper_types.append(f"{package_match.group(1)}.{class_match.group(1)}")
    for mapper_type in mapper_types:
        simple = mapper_type.rsplit(".", 1)[-1]
        if re.search(rf"\b{re.escape(simple)}\s+\w+\s*\(", text):
            continue
        method = (
            "\n    @Bean\n"
            f"    public {mapper_type} {simple[0].lower() + simple[1:]}() {{\n"
            f"        return new {mapper_type}();\n"
            "    }\n"
        )
        text = text.rsplit("}", 1)[0] + method + "}\n"
    # Factory method parameters form the Spring construction graph.  An LLM can
    # accidentally make a Boundary callback part of that graph (Control ->
    # Boundary -> Control).  Detect cycles from the actual @Bean declarations
    # and break only the corresponding parameter edge with @Lazy.  This does
    # not enable Spring's global circular-reference escape hatch.
    text, lazy_added = break_configuration_cycles(text)
    if removals or mapper_types or lazy_added or placeholder_comments_removed:
        configuration.write_text(text, encoding="utf-8")


def normalize_spring_boot_repository_discovery(
    sandbox: Path, task: dict[str, object]
) -> None:
    """Keep Spring Data repositories enabled in the generated application.

    The wiring agent occasionally adds ``exclude`` attributes for JPA
    auto-configuration after being shown an earlier context failure.  That
    makes every ``JpaRepository`` disappear from the application context, so
    the end-to-end test fails before it can exercise the flow.  Repository
    discovery is part of the generated contract and the wiring prompt already
    forbids this workaround; normalize the entry point deterministically so a
    transient LLM deviation cannot ship a broken context.
    """
    entrypoint = next(
        (
            sandbox / str(relative)
            for relative in task.get("allowed_write_paths", [])
            if str(relative).endswith("Application.java")
        ),
        None,
    )
    if entrypoint is None or not entrypoint.is_file():
        return
    text = entrypoint.read_text(encoding="utf-8")
    original = text
    text = re.sub(
        r"(?m)^\s*import\s+org\.springframework\.boot\.autoconfigure\.(?:orm\.jpa\.HibernateJpaAutoConfiguration|data\.jpa\.JpaRepositoriesAutoConfiguration);\s*\n?",
        "",
        text,
    )
    def remove_jpa_exclusions(match: re.Match[str]) -> str:
        annotation = match.group(0)
        if re.search(
            r"(?:HibernateJpaAutoConfiguration|JpaRepositoriesAutoConfiguration)",
            annotation,
        ):
            return "@SpringBootApplication"
        return annotation

    text = re.sub(
        r"@SpringBootApplication\s*\(\s*exclude\s*=\s*(?:\{[^}]*\}|[^)]*)\s*\)",
        remove_jpa_exclusions,
        text,
        flags=re.DOTALL,
    )
    if text != original:
        entrypoint.write_text(text, encoding="utf-8")


def remove_placeholder_comments(text: str) -> tuple[str, bool]:
    """Remove line comments that call generated configuration a placeholder.

    This only alters comments in the wiring output owned by the current task;
    it never changes the generated Java expression or any other task's source.
    """
    normalized = re.sub(
        r"//[^\r\n]*\bplaceholder\b[^\r\n]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return normalized, normalized != text


def break_configuration_cycles(text: str) -> tuple[str, bool]:
    """Add ``@Lazy`` only to @Bean parameters that close a dependency cycle."""
    bean = re.compile(
        r"(?ms)^\s*@Bean(?:\s*\([^)]*\))?\s*"
        r"(?:public\s+)?(?P<return>[A-Za-z_]\w*(?:\s*<[^>{}]*>)?)\s+"
        r"(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)\s*\{"
    )
    methods = list(bean.finditer(text))
    if not methods:
        return text, False

    return_to_name = {
        _simple_java_type(match.group("return")): match.group("name") for match in methods
    }
    edges: dict[str, set[str]] = {}
    for match in methods:
        edges[match.group("name")] = {
            return_to_name[param_type]
            for param_type in _bean_parameter_types(match.group("params"))
            if param_type in return_to_name
        }

    cyclic_edges: set[tuple[str, str]] = set()
    for source, targets in edges.items():
        for target in targets:
            if source == target or _has_path(edges, target, source, {target}):
                cyclic_edges.add((source, target))
    if not cyclic_edges:
        return text, False

    def rewrite(match: re.Match[str]) -> str:
        source = match.group("name")
        params = match.group("params")
        additions = {
            return_type
            for return_type, target in return_to_name.items()
            if (source, target) in cyclic_edges
        }
        if not additions:
            return match.group(0)
        rewritten = re.sub(
            r"(?<!@Lazy\s)(?P<parameter>(?:@[A-Za-z_]\w*(?:\([^)]*\))?\s+)*(?:final\s+)?(?:[\w.]+(?:\s*<[^>{}]*>)?)\s+[A-Za-z_]\w*)",
            lambda parameter: (
                "@Lazy " + parameter.group("parameter")
                if _simple_java_type(parameter.group("parameter")) in additions
                and "@Lazy" not in parameter.group("parameter")
                else parameter.group("parameter")
            ),
            params,
        )
        return match.group(0).replace(params, rewritten, 1)

    rewritten = bean.sub(rewrite, text)
    if rewritten == text:
        return text, False
    if not re.search(r"(?m)^import\s+org\.springframework\.context\.annotation\.Lazy;", rewritten):
        anchor = "import org.springframework.context.annotation.Configuration;"
        if anchor in rewritten:
            rewritten = rewritten.replace(anchor, anchor + "\nimport org.springframework.context.annotation.Lazy;", 1)
        else:
            package = re.search(r"(?m)^package\s+[^;]+;", rewritten)
            if package:
                rewritten = rewritten[: package.end()] + "\n\nimport org.springframework.context.annotation.Lazy;" + rewritten[package.end() :]
    return rewritten, True


def _simple_java_type(value: str) -> str:
    cleaned = re.sub(r"@[A-Za-z_]\w*(?:\([^)]*\))?\s*", "", value)
    cleaned = re.sub(r"\bfinal\s+", "", cleaned).strip()
    return cleaned.split()[0].split("<", 1)[0].rsplit(".", 1)[-1] if cleaned else ""


def _bean_parameter_types(params: str) -> set[str]:
    return {_simple_java_type(parameter) for parameter in params.split(",") if parameter.strip()}


def _has_path(edges: dict[str, set[str]], current: str, target: str, seen: set[str]) -> bool:
    for next_node in edges.get(current, set()):
        if next_node == target or (next_node not in seen and _has_path(edges, next_node, target, seen | {next_node})):
            return True
    return False


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
