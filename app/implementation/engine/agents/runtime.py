from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

from ..planning.design_context import (
    read_generated_java_contracts,
    referenced_openapi_model_names,
)
from ..domain.implementation_ir import remove_readonly
from .verification.frontend import (
    frontend_contract_violations,
    has_mutating_operations,
    run_frontend_verification,
)
from ..quality_gates import e2e_contract_violations
from ..repair_planner import referenced_source_paths
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
    verification_failure_hints,
)


MAX_AGENT_ITERATIONS = 6
MAX_REPAIR_ITERATIONS = 4
MAX_VERIFICATION_REPAIRS = 6
MAX_REASONING_BUDGET = 256
_RESTRICTED_EDITOR_REGISTERED = False


def gradle_command() -> list[str]:
    """Use EasyDep's pinned wrapper instead of a machine-global Gradle."""
    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    wrapper = (
        Path(__file__).resolve().parent.parent.parent
        / "tools"
        / "gradle"
        / wrapper_name
    )
    if not wrapper.is_file():
        raise RuntimeError(f"Bundled Gradle Wrapper is missing: {wrapper}")
    return [str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]


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


class WorkspaceVerificationError(RuntimeError):
    def __init__(self, evidence: dict[str, object]):
        self.evidence = evidence
        output = str(
            evidence.get("testResults")
            or evidence.get("stderr")
            or evidence.get("stdout")
            or ""
        )
        super().__init__("Agent workspace verification failed: " + output[-1000:])


def missing_required_outputs(sandbox: Path, relative_paths: list[str]) -> list[str]:
    """Return contracted task outputs that the agent has not created as files."""
    return [relative for relative in relative_paths if not (sandbox / relative).is_file()]


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
    journal = EventJournal(execution_dir / f"{task_id}.events.jsonl")
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
                        raise missing_error from conversation_error
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
                placeholders = production_placeholder_markers(
                    sandbox, task["allowed_write_paths"]
                )
                if placeholders:
                    raise WorkspaceVerificationError(
                        {
                            "command": ["production-placeholder-gate"],
                            "exitCode": 1,
                            "durationMs": 0,
                            "stdout": "",
                            "stderr": (
                                "Production outputs contain unresolved TODO/FIXME/placeholder "
                                "markers:\n" + "\n".join(placeholders)
                            ),
                            "testResults": "",
                        }
                    )
                if str(task.get("task_type", "")) == "integration-test":
                    e2e_path = sandbox / str(task["allowed_write_paths"][0])
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
                verification = verify_agent_workspace(sandbox, task_type)
                break
            except WorkspaceVerificationError as error:
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
        (execution_dir / f"{task_id}.result.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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
    (execution_dir / f"{task_id}.result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


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
        "base_url": os.environ.get("LLM_BASE_URL", str(llm_config["baseUrl"])),
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


def load_task(run_root: Path, task_id: str) -> dict[str, object]:
    task_dir = run_root / "reports" / "implementation-tasks"
    for candidate in task_dir.glob("*.task.json"):
        task = json.loads(candidate.read_text(encoding="utf-8"))
        if task["task_id"] == task_id:
            return task
    raise ValueError(f"Unknown task: {task_id}")


def task_base_package(task: dict[str, object]) -> str:
    package_markers = {
        "application", "persistence", "adapter", "integration", "config", "bce", "api"
    }
    for output in task["allowed_write_paths"]:
        relative = Path(str(output))
        parts = relative.parts
        if "java" not in parts:
            continue
        java_index = parts.index("java")
        marker_index = next(
            (
                index for index in range(java_index + 1, len(parts))
                if parts[index] in package_markers
            ),
            None,
        )
        if marker_index is not None and marker_index > java_index + 1:
            return ".".join(parts[java_index + 1 : marker_index])
    raise ValueError("Cannot derive base package from task outputs")


def read_persistence_entity_contracts(run_root: Path, base_package: str) -> str:
    root = (
        run_root
        / "application"
        / "src"
        / "main"
        / "java"
        / Path(base_package.replace(".", "/"))
        / "persistence"
        / "entity"
    )
    contracts: list[str] = []
    for path in sorted(root.glob("*Entity.java")):
        contracts.append(
            f"// persistence/entity/{path.name}\n"
            + path.read_text(encoding="utf-8").strip()
        )
    return "\n\n".join(contracts) or "// No persistence entity contracts found"


def prepare_agent_workspace(run_root: Path, task: dict[str, object]) -> Path:
    run_key = run_root.name.removeprefix("run_")[:12]
    task_key = str(task["task_id"]).removeprefix("implement-")
    sandbox_base = Path(tempfile.gettempdir()) / "easydep-agent-workspaces" / run_key / task_key
    sandbox = sandbox_base
    suffix = 1
    while sandbox.exists():
        try:
            shutil.rmtree(sandbox, onerror=remove_readonly)
        except PermissionError:
            # A Gradle/IDE process can briefly lock files on Windows. Keep the
            # locked transient workspace and isolate this attempt in a sibling.
            suffix += 1
            sandbox = sandbox_base.with_name(f"{sandbox_base.name}-{suffix}")
            continue
        break
    shutil.copytree(
        run_root / "application",
        sandbox / "application",
        ignore=shutil.ignore_patterns(
            "deployment-bundle", "build", ".gradle", "node_modules", "dist"
        ),
    )
    for relative in task["allowed_write_paths"]:
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and len(str(target.resolve())) > 240:
            raise ValueError(f"Agent write path exceeds safe Windows path budget: {target}")
    return sandbox


def verify_run_workspace(run_root: Path) -> dict[str, object]:
    """Verify all promoted sources from a short ASCII-safe workspace."""
    sandbox = prepare_agent_workspace(
        run_root,
        {"task_id": "final-verification", "allowed_write_paths": []},
    )
    verification = verify_agent_workspace(sandbox)
    frontend_verification = None
    if (sandbox / "application" / "frontend" / "package.json").is_file():
        frontend_verification = verify_frontend_workspace(sandbox)
    result = {
        "status": "SUCCEEDED",
        "workspace": str(sandbox),
        "verification": verification,
        "frontendVerification": frontend_verification,
    }
    report = run_root / "reports" / "final-verification.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def verify_agent_workspace(
    sandbox: Path, task_type: str = ""
) -> dict[str, object]:
    if task_type == "frontend-implementation":
        return verify_frontend_workspace(sandbox)
    executable = gradle_command()
    started = time.monotonic()
    result = subprocess.run(
        [*executable, "compileJava", "bootJar", "test", "--no-daemon"],
        cwd=sandbox / "application",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    evidence = {
        "command": [*executable, "compileJava", "bootJar", "test", "--no-daemon"],
        "exitCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": result.stdout[-16000:],
        "stderr": result.stderr[-16000:],
        "testResults": read_gradle_test_failures(sandbox),
    }
    if result.returncode != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def verify_frontend_workspace(sandbox: Path) -> dict[str, object]:
    evidence = run_frontend_verification(sandbox, subprocess.run)
    if evidence["exitCode"] != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def production_placeholder_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Reject unresolved implementation markers in contracted production Java outputs."""
    evidence: list[str] = []
    pattern = re.compile(r"\b(?:TODO|FIXME|PLACEHOLDER)\b", re.IGNORECASE)
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if "/src/main/java/" not in f"/{normalized}" or not normalized.endswith(".java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                evidence.append(f"{normalized}:{number}: {line.strip()}")
    return evidence


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
    if removals or mapper_types or lazy_added:
        configuration.write_text(text, encoding="utf-8")


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


def read_allowed_sources(sandbox: Path, relative_paths: list[str]) -> str:
    sections: list[str] = []
    for relative in relative_paths:
        path = sandbox / relative
        content = path.read_text(encoding="utf-8") if path.is_file() else "// File missing"
        sections.append(f"### {relative}\n```java\n{content}\n```")
    return "\n\n".join(sections)


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


def read_gradle_test_failures(sandbox: Path) -> str:
    result_dir = sandbox / "application" / "build" / "test-results" / "test"
    reports: list[str] = []
    for report in sorted(result_dir.glob("*.xml")):
        try:
            root = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        for case in root.findall("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            message = problem.get("message") or "test failed"
            detail = (problem.text or "").strip()
            if detail:
                message += "\n" + summarize_test_failure(detail)
            reports.append(f"{case.get('classname')}.{case.get('name')}: {message}")
    return _truncate_log_snippet("\n\n".join(reports), max_chars=8000)


def _truncate_log_snippet(text: str, max_chars: int = 8000) -> str:
    """Safely truncate log snippets to a maximum character count."""
    return text[-max_chars:] if len(text) > max_chars else text


def summarize_test_failure(detail: str) -> str:
    """Keep causal exception lines, rather than only the end of a long trace."""
    lines = [line.rstrip() for line in detail.splitlines() if line.strip()]
    causal = [
        line
        for line in lines
        if re.search(
            r"(?:Caused by:|Suppressed:|Error creating bean|Requested bean is currently in creation|"
            r"NoSuchBeanDefinitionException|NoUniqueBeanDefinitionException|UnsatisfiedDependencyException|"
            r"BeanCurrentlyInCreationException)",
            line,
        )
    ]
    selected = causal or lines[:30]
    # Preserve a small tail for Gradle/JUnit-specific context without allowing a
    # stack trace to evict the causal message from the repair prompt.
    selected.extend(lines[-8:])
    return _truncate_log_snippet("\n".join(dict.fromkeys(selected)), max_chars=8000)


def snapshot_files(root: Path) -> dict[str, str]:
    import hashlib

    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root)
            if path.name == "package-lock.json" or path.name.endswith(".tsbuildinfo"):
                # Dependency setup and TypeScript verification can update these
                # deterministic build inputs. They are not agent-authored outputs,
                # so exclude them only from the agent change boundary.
                continue
            if any(
                part in {"build", ".gradle", "node_modules", "dist"}
                for part in relative.parts
            ):
                continue
            result[str(relative).replace("\\", "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def changed_files(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
