from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

from .design_context import read_generated_java_contracts, referenced_openapi_model_names
from .quality_gates import e2e_contract_violations
from .repair_planner import referenced_source_paths


MAX_AGENT_ITERATIONS = 6
MAX_REPAIR_ITERATIONS = 4
MAX_VERIFICATION_REPAIRS = 6
MAX_REASONING_BUDGET = 256
MAX_PROVIDER_RETRIES = 3
_RESTRICTED_EDITOR_REGISTERED = False
IMPLEMENTATION_SYSTEM_PROMPT = """You are a focused Java implementation worker.
The user prompt contains the complete relevant design context and the exact writable files.
Use only the restricted file editor. Its file argument is named path, not file_path. Never attempt shell commands, commits, or broad repository exploration.
All relevant generated Java contracts are embedded in the user prompt. The writable parent directories are already created and verified; do not browse directories before creating the files.
Do not edit generated contracts.
For Control tasks, generated BCE Controls are application port interfaces: implement the matching Control interface and inject other ports through the constructor. For persistence tasks, follow the task-specific JPA, repository, mapper, and schema rules. For API adapter tasks, implement the exact generated OpenAPI interface and delegate only through existing application Control ports. Never edit generated BCE or OpenAPI contracts.
Use only methods present in the embedded Java contracts and preserve their exact return types. Never assign the result of a void method. Mockito mocks already do nothing for void methods by default: never put a void call inside when(...), including when(...).thenReturn(...) or when(...).thenAnswer(...). If custom void behavior is genuinely required, use doAnswer(...).when(mock).method(...).
Java has no import aliases. When API and BCE packages contain the same simple class name, use a fully qualified class name. Never invent Bce-prefixed aliases, use reflection to bypass contracts, or access private generated fields.
If generated types do not expose data through exact public methods, omit that mapping, leave a focused TODO, and keep the known flow compilable. Never assume conventional getters or setters.
Write each contracted file in its required format. Java files must contain valid Java with // or /* */ comments; SQL migration files must contain valid SQL with -- or /* */ comments. Never mix Markdown into source files.
Keep source comments concise and implementation-focused. Never place chain-of-thought, self-dialogue, repeated design analysis, or speculative question-and-answer text in Java comments.
Before writing a complete replacement, ensure each Java method signature appears only once in its class; never append a second copy of an existing helper method.
Create every contracted output file, then call finish immediately. For a small correction use exact str_replace; when most of a file is wrong, use create to replace the existing allowlisted file completely.
Create as many requested files in the same response as the output limit permits. When tests are requested, keep them focused to 3-5 meaningful scenarios and do not verify incidental logging calls. Never use verifyNoInteractions or verifyNoMoreInteractions. For negative Mockito verification, the only valid form is verify(mock, never()).method(...); never invent verifyNever. Use matchers for every argument when any matcher is used, including eq(value) for otherwise raw arguments; never concatenate a matcher into a String or other value. Stub the same method only once per test; use chained thenReturn(first, second) only when the implementation actually calls it repeatedly. Never invoke a mock in test setup unless the invocation is part of when(...) or do...when(...). Ensure every verification matches a branch the implementation actually executes.
Never mock, spy, or call Mockito verify(...) on the service under test. Invoke the real service and verify only its mocked collaborators. Derive invocation counts from the exact implementation path; do not guess with times(...), duplicate verification of the same invocation, or use atLeast to hide uncertainty. Do not create stubs that the tested path does not consume.
If a required contract is absent or contradictory, leave a focused TODO in an allowed file and finish.
"""


def gradle_command() -> list[str]:
    """Use EasyDep's pinned wrapper instead of a machine-global Gradle."""
    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    wrapper = Path(__file__).resolve().parent.parent / "tools" / "gradle" / wrapper_name
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


def render_verification_feedback(
    evidence: dict[str, object],
    current_sources: str = "",
    repair_targets: list[str] | None = None,
) -> str:
    output = (
        str(evidence.get("stdout", ""))
        + "\n"
        + str(evidence.get("stderr", ""))
        + "\n"
        + str(evidence.get("testResults", ""))
    )[-20000:]
    hints = verification_failure_hints(output)
    target_text = "\n".join(f"- `{path}`" for path in (repair_targets or []))
    return f"""The orchestrator compiled and tested your files, and verification failed.
Fix every reported error in the existing allowed files, including test compilation errors.
Generated contracts are authoritative: never assume a return value or method that is absent from their exact signatures.
Do not invent Java aliases, access private fields, or use reflection to bypass a generated contract.
For a cannot-find-symbol diagnostic, remove the absent call or field access. Do not invent a replacement accessor; leave a focused TODO when the contract exposes no equivalent.
For an already-defined-method diagnostic, keep one implementation of that signature and remove the duplicate.
For Mockito negative verification, use verify(mock, never()).method(...); verifyNever does not exist.
For a 'void type not allowed here' diagnostic, delete the unnecessary when(mock.voidMethod(...)) stub. If custom behavior is required, use Mockito doAnswer(...).when(mock).voidMethod(...).
Use create to replace each affected allowlisted file completely. Do not call view or str_replace during this repair round; the current sources are included below.
Write only the following repair targets; do not rewrite any other file:
{target_text}
Emit all required create calls in one response, then call finish immediately.

Failure-specific guidance:
{hints}

Gradle output:
```text
{output}
```

Current allowlisted sources:
{current_sources}
"""


def verification_failure_hints(output: str) -> str:
    hints: list[str] = []
    if "TooManyActualInvocations" in output:
        hints.append(
            "- TooManyActualInvocations: do not verify a broad matcher once when the "
            "method is legitimately called with multiple arguments. Remove incidental log "
            "verification or verify an exact argument."
        )
    if "TooFewActualInvocations" in output:
        hints.append(
            "- TooFewActualInvocations: the test expects more calls than the implementation "
            "actually makes. Remove duplicate verification or reduce times(n) to the exact "
            "observed count; do not add production calls solely to satisfy a mock count."
        )
    if "UnnecessaryStubbingException" in output or "Unnecessary stubbings detected" in output:
        hints.append(
            "- UnnecessaryStubbingException: delete every stubbing identified as unused. "
            "Do not make it lenient and do not add production behavior just to consume it."
        )
    if "NotAMockException" in output or "Argument passed to verify() is of type" in output:
        hints.append(
            "- Mockito verify requires a mock collaborator. Never verify the real service "
            "under test; call it normally and assert state or verify its mocked dependencies."
        )
    if "InvalidUseOfMatchersException" in output or "matchers expected" in output:
        hints.append(
            "- InvalidUseOfMatchersException: if one argument uses a matcher, wrap every "
            "argument in that invocation with a matcher. For example, use "
            "verify(timer).startTimer(eq(30), anyString()), not startTimer(30, anyString())."
        )
    if "ConnectionFails_HandlesFailure" in output and "Wanted but not invoked" in output:
        hints.append(
            "- Connection failure scenario: arrange the void openConnection call with "
            "doThrow(new RuntimeException(...)).when(webConnectionManager).openConnection(...). "
            "The service should catch that runtime failure, invoke handleFailure, and stop before "
            "captureResponse. Do not expect failure after arranging a successful void call."
        )
    if "Wanted but not invoked" in output:
        hints.append(
            "- Wanted but not invoked: trace the implementation branch. Remove conflicting "
            "stubs of the same method and do not verify a branch the arranged return values skip."
        )
    if "void type not allowed here" in output:
        hints.append(
            "- Void Mockito method: remove when(mock.voidMethod(...)); void mocks need no stub "
            "unless doAnswer(...).when(mock).voidMethod(...) is genuinely required."
        )
    return "\n".join(hints) or "- Fix the reported compiler or test failure without weakening meaningful assertions."


def configured_api_key() -> str | None:
    return (
        os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NVIDIA_NIM_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or windows_user_environment("NVIDIA_API_KEY")
        or windows_user_environment("NVIDIA_NIM_API_KEY")
    )


def configured_model(default: str) -> str:
    return os.environ.get("OPENHANDS_MODEL") or os.environ.get("LLM_MODEL") or default


def configured_max_output_tokens(default: int) -> int:
    raw = os.environ.get("OPENHANDS_MAX_OUTPUT_TOKENS")
    return int(raw) if raw else default


def transient_provider_error(error: Exception) -> bool:
    """Recognize retryable NIM/OpenAI-compatible transport failures."""
    text = f"{error.__class__.__name__}: {error}".lower()
    return any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "too many requests",
            "timeout",
            "timed out",
            "connection error",
            "temporarily unavailable",
            "service unavailable",
            "overloaded",
            "bad gateway",
            "gateway timeout",
            "502",
            "503",
            "504",
        )
    )


def provider_retry_delay(retry_number: int) -> float:
    base = float(os.environ.get("OPENHANDS_PROVIDER_RETRY_BASE_SECONDS", "1"))
    cap = float(os.environ.get("OPENHANDS_PROVIDER_RETRY_MAX_SECONDS", "30"))
    return min(cap, base * (2 ** max(0, retry_number - 1)))


def missing_required_outputs(sandbox: Path, relative_paths: list[str]) -> list[str]:
    """Return contracted task outputs that the agent has not created as files."""
    return [relative for relative in relative_paths if not (sandbox / relative).is_file()]


def windows_user_environment(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value if isinstance(value, str) and value else None
    except (FileNotFoundError, OSError):
        return None


def openhands_compatibility() -> dict[str, object]:
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "pythonCompatible": sys.version_info >= (3, 12),
        "sdkInstalled": module_available("openhands.sdk"),
        "toolsInstalled": module_available("openhands.tools"),
        "apiKeyConfigured": bool(configured_api_key()),
    }


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


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

    compatibility = openhands_compatibility()
    missing = [key for key in ("pythonCompatible", "sdkInstalled", "toolsInstalled", "apiKeyConfigured") if not compatibility[key]]
    if missing:
        raise RuntimeError("OpenHands live mode prerequisites are missing: " + ", ".join(missing))

    sandbox = prepare_agent_workspace(run_root, task)
    before = snapshot_files(sandbox)
    prompt = (run_root / task["prompt_file"]).read_text(encoding="utf-8")
    context = json.loads((run_root / task["context_file"]).read_text(encoding="utf-8"))
    api_model_names = referenced_openapi_model_names(str(context.get("openapi", "")))
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
                verification = verify_agent_workspace(sandbox)
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
                round_prompt = prompt + "\n\n## Verification repair\n\n" + render_verification_feedback(
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
        "systemPrompt": "focused-java-implementation",
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
        system_prompt=IMPLEMENTATION_SYSTEM_PROMPT,
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
            shutil.rmtree(sandbox, onerror=_remove_readonly)
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
        ignore=shutil.ignore_patterns("build", ".gradle"),
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
    result = {
        "status": "SUCCEEDED",
        "workspace": str(sandbox),
        "verification": verification,
    }
    report = run_root / "reports" / "final-verification.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _remove_readonly(function, path: str, _error) -> None:
    """Retry sandbox cleanup after making a Windows read-only entry writable."""
    os.chmod(path, stat.S_IRWXU)
    function(path)


def verify_agent_workspace(sandbox: Path) -> dict[str, object]:
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
    if not component_ports:
        return
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
    # A Boundary adapter may call back into the Control being constructed.  Make
    # those factory-method injection points lazy so Spring can materialize the
    # Control graph without enabling global circular references.
    text = re.sub(
        r"(?<!@Lazy\s)(\bStockPurchaseController\s+\w+)",
        r"@Lazy \1",
        text,
    )
    text = re.sub(
        r"(?<!@Lazy\s)(\b\w+Screen\s+\w+)",
        r"@Lazy \1",
        text,
    )
    if removals:
        configuration.write_text(text, encoding="utf-8")
    elif mapper_types:
        configuration.write_text(text, encoding="utf-8")


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
                message += "\n" + detail[-12000:]
            reports.append(f"{case.get('classname')}.{case.get('name')}: {message}")
    return "\n\n".join(reports)[-8000:]


def snapshot_files(root: Path) -> dict[str, str]:
    import hashlib

    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root)
            if any(part in {"build", ".gradle"} for part in relative.parts):
                continue
            result[str(relative).replace("\\", "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def changed_files(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
