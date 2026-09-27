from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.implementation.agents.canary as canary_module
from app.implementation.agents.canary import (
    classify_canary_exception,
    ensure_model_tool_canary,
    open_endpoint_circuit,
)
from app.implementation.agents.evaluation import (
    _journal_metrics,
    compare_harness_runs,
    evaluate_harness_run,
)
from app.implementation.agents.harness import (
    HARNESS_POLICY_VERSION,
    HarnessCompatibilityError,
    HarnessErrorGuard,
    HarnessProgressTracker,
    build_harness_manifest,
    classify_harness_error_text,
    owner_prompt_path,
    owner_tool_names,
    render_harness_error,
    tool_schema_hash,
    verify_or_store_harness_manifest,
)
from app.implementation.agents.runtime import create_openhands_conversation
from app.implementation.agents.source_replace_tool import (
    ExactSourceEdit,
    SourceEditAction,
    SourceEditExecutor,
    SourceReplaceAction,
    SourceReplaceExecutor,
)
from app.implementation.agents.upstream_gap_tool import (
    UPSTREAM_GAP_TOOL_NAME,
    UpstreamGapAction,
    reported_upstream_gap,
)
from app.implementation.agents.workspace import preflight_owner_workspace
from app.llm_connection import LlmConnection


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "attempted to call tool 'repo_browser.exec' which was not in request.tools",
            "TOOL_NOT_AVAILABLE",
        ),
        (
            "attempted to call tool 'terminal<|channel|>commentary' which was not in request.tools",
            "TOOL_PROTOCOL_TOKEN_LEAK",
        ),
        (
            "parameters for tool file_editor did not match schema",
            "TOOL_SCHEMA_INVALID",
        ),
        ("Path is outside the assigned workspace: /other", "PATH_OUTSIDE_WORKSPACE"),
    ],
)
def test_harness_classifies_recorded_failure_shapes(message: str, expected: str) -> None:
    error = classify_harness_error_text(message)

    assert error is not None
    assert error.code == expected


def test_sanitized_recorded_failure_fixture_keeps_run_and_sequence_mapping() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/openhands_harness_failures.json").read_text(
            encoding="utf-8"
        )
    )

    assert fixture["sourceRunId"] == "run_0b7e5a6306fe"
    assert [event["sequence"] for event in fixture["events"]] == [41, 57, 73, 88]
    for event in fixture["events"]:
        classified = classify_harness_error_text(event["message"])
        assert classified is not None
        assert classified.code == event["expectedErrorCode"]


def test_harness_error_has_a_machine_readable_first_line() -> None:
    rendered = render_harness_error(
        "PATH_OUTSIDE_WORKSPACE",
        "Use a relative path.",
        retryable=True,
        workspace="/work",
    )

    prefix, detail = rendered.splitlines()
    payload = json.loads(prefix.removeprefix("EASYDEP_HARNESS_ERROR "))
    assert payload == {
        "errorCode": "PATH_OUTSIDE_WORKSPACE",
        "nextAction": "Use an absolute path rooted at the logical workspace.",
        "retryable": True,
        "workspace": "/work",
    }
    assert detail == "Use a relative path."


def test_harness_guard_stops_a_protocol_leak_without_retry() -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    class Event:
        def model_dump(self, **_kwargs):
            return {
                "message": (
                    "attempted to call tool "
                    "'terminal<|channel|>commentary' which was not in request.tools"
                )
            }

    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    guard = HarnessErrorGuard()
    guard.bind(conversation)

    guard(Event())

    assert guard.terminal_code == "TOOL_PROTOCOL_TOKEN_LEAK"
    assert conversation.state.execution_status is ConversationExecutionStatus.ERROR


def test_protocol_token_documentation_is_not_misclassified() -> None:
    assert (
        classify_harness_error_text(
            "The Harmony documentation describes the <|channel|> control token."
        )
        is None
    )


def test_harness_guard_stops_a_protocol_token_in_an_agent_message() -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    event_type = type(
        "MessageEvent",
        (),
        {
            "source": "agent",
            "model_dump": lambda self, **_kwargs: {
                "source": "agent",
                "content": "malformed <|recipient|> output",
            },
        },
    )
    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    guard = HarnessErrorGuard()
    guard.bind(conversation)

    guard(event_type())

    assert guard.terminal_code == "TOOL_PROTOCOL_TOKEN_LEAK"
    assert conversation.state.execution_status is ConversationExecutionStatus.ERROR


def test_harness_guard_allows_only_one_recoverable_tool_error() -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    class Event:
        def model_dump(self, **_kwargs):
            return {
                "message": "attempted to call tool 'exec' which was not in request.tools"
            }

    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    guard = HarnessErrorGuard()
    guard.bind(conversation)

    guard(Event())
    assert conversation.state.execution_status is ConversationExecutionStatus.RUNNING
    guard(Event())

    assert guard.terminal_code == "TOOL_NOT_AVAILABLE"
    assert conversation.state.execution_status is ConversationExecutionStatus.ERROR


def test_progress_tracker_stops_an_identical_read_loop(tmp_path: Path) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    source = tmp_path / "application/App.java"
    source.parent.mkdir()
    source.write_text("class App {}\n", encoding="utf-8")
    tracker = HarnessProgressTracker(tmp_path)
    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    tracker.bind(conversation)

    event_type = type(
        "ActionEvent",
        (),
        {
            "tool_name": "file_editor",
            "model_dump": lambda self, **_kwargs: {
                "tool_name": "file_editor",
                "action": {"command": "view", "path": "application/App.java"},
            },
        },
    )
    for _ in range(4):
        tracker(event_type())

    assert tracker.terminal_code == "NO_PROGRESS_REPEAT"
    assert tracker.snapshot()["maxRepeatedIdenticalRead"] == 4
    assert conversation.state.execution_status is ConversationExecutionStatus.ERROR


def test_progress_tracker_requests_an_edit_after_distinct_read_budget(
    tmp_path: Path,
) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    source = tmp_path / "application/App.java"
    source.parent.mkdir()
    source.write_text("class App {}\n", encoding="utf-8")
    tracker = HarnessProgressTracker(tmp_path, max_reads_without_change=3)
    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    tracker.bind(conversation)

    event_type = type(
        "ActionEvent",
        (),
        {
            "tool_name": "file_editor",
            "model_dump": lambda self, **_kwargs: {
                "tool_name": "file_editor",
                "action": {
                    "command": "view",
                    "path": f"application/Dependency{self.index}.java",
                },
            },
        },
    )
    for index in range(3):
        event = event_type()
        event.index = index
        tracker(event)

    assert tracker.terminal_code == "NO_PROGRESS_READ_BUDGET"
    assert tracker.snapshot()["maxReadsWithoutSourceChange"] == 3
    assert conversation.state.execution_status is ConversationExecutionStatus.STUCK


def test_progress_tracker_records_nested_observation_failure(tmp_path: Path) -> None:
    (tmp_path / "application").mkdir()
    tracker = HarnessProgressTracker(tmp_path)
    event_type = type(
        "ObservationEvent",
        (),
        {
            "model_dump": lambda self, **_kwargs: {
                "observation": {"is_error": True, "text": "compile failed"}
            },
        },
    )

    tracker(event_type())

    assert tracker.snapshot()["lastFailureFingerprint"] is not None


def test_harness_manifest_rejects_a_changed_tool_contract(tmp_path: Path) -> None:
    path = tmp_path / "harness.json"
    connection = LlmConnection(
        provider="openrouter",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    restricted = build_harness_manifest(
        connection,
        owner_tool_mode="restricted",
        reasoning_effort="medium",
    )
    verify_or_store_harness_manifest(path, restricted)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["harnessPolicyVersion"] == HARNESS_POLICY_VERSION
    assert persisted["toolNames"] == list(owner_tool_names("restricted"))
    assert persisted["toolSchemaHash"] == tool_schema_hash("restricted")

    terminal = build_harness_manifest(
        connection,
        owner_tool_mode="terminal",
        reasoning_effort="medium",
    )
    with pytest.raises(HarnessCompatibilityError, match="different harness contract"):
        verify_or_store_harness_manifest(path, terminal)


def test_harness_manifest_rejects_a_changed_canary_contract(tmp_path: Path) -> None:
    path = tmp_path / "harness.json"
    connection = LlmConnection(
        provider="openrouter",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    first = build_harness_manifest(
        connection,
        owner_tool_mode="restricted",
        reasoning_effort="medium",
        canary_result_id="canary-a",
    )
    verify_or_store_harness_manifest(path, first)
    changed = {**first, "canaryResultId": "canary-b"}

    with pytest.raises(HarnessCompatibilityError, match="canaryResultId"):
        verify_or_store_harness_manifest(path, changed)


def test_owner_workspace_preflight_checks_a_real_writable_path(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    editable = sandbox / "application/src/main/java/example/App.java"
    immutable = sandbox / "application/src/main/java/example/api"
    editable.parent.mkdir(parents=True)
    immutable.mkdir(parents=True)

    result = preflight_owner_workspace(
        sandbox,
        editable_files=[str(editable)],
        editable_roots=[str(editable.parent)],
        immutable_paths=[str(immutable)],
    )

    assert result["passed"] is True
    assert not (editable.parent / ".easydep-owner-preflight").exists()


def test_owner_workspace_preflight_rejects_an_assigned_path_escape(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    with pytest.raises(RuntimeError, match="ENV_WORKSPACE_PERMISSION"):
        preflight_owner_workspace(
            sandbox,
            editable_files=[str(tmp_path / "outside.java")],
            editable_roots=[],
            immutable_paths=[],
        )


def test_restricted_owner_uses_the_minimal_tools_and_custom_prompt(tmp_path: Path) -> None:
    source_root = tmp_path / "application/src/main/java/example"
    source_root.mkdir(parents=True)
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
        task_type="backend-implementation",
        verification_paths=["application/src/main/java/example/App.java"],
        editable_roots=[str(source_root.resolve())],
        native_owner_tools=True,
        owner_tool_mode="restricted",
        owner_system_context="Complete workspace: /work",
    )
    try:
        conversation.send_message("Initialize tools without calling the model.")
        assert sorted(agent._tools) == ["file_editor", "finish", "grep", "run_task_check"]
        assert agent.system_prompt.strip() == owner_prompt_path().read_text(encoding="utf-8").strip()
        prompt = conversation.state.events[0].system_prompt.text
        assert "a short path below `/work`" in prompt
        assert "Requirements, caller-visible APIs, and observable behavior are hard constraints" in prompt
        assert "one writable source containing an assigned completion marker" in prompt
        assert "local declarations and assigned task behavior" in prompt
        assert "missing collaborator or wiring entry alone is not an upstream gap" in prompt
        assert "required public input, output, or externally visible behavior" in prompt
        assert "Upstream Requirements and Design are admitted and frozen" not in prompt
        assert "PULL_REQUESTS" not in prompt
        assert agent.llm.timeout == 300
        assert agent.llm.num_retries == 3
        assert agent.llm.retry_min_wait == 1
        assert agent.llm.retry_max_wait == 8
        assert agent.llm.retry_multiplier == 1.0
    finally:
        conversation.close()


def test_editor_owner_exposes_exact_edit_replace_and_finish(tmp_path: Path) -> None:
    source = tmp_path / "application/src/main/java/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
        task_type="backend-implementation",
        editable_files=[str(source.resolve())],
        native_owner_tools=True,
        owner_tool_mode="editor",
    )
    try:
        conversation.send_message("Initialize tools without calling the model.")
        assert sorted(agent._tools) == ["edit_source", "finish", "replace_source"]
    finally:
        conversation.close()


def test_replace_source_rejects_empty_and_out_of_scope_paths(tmp_path: Path) -> None:
    source = tmp_path / "application/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    executor = SourceReplaceExecutor(tmp_path, [str(source)])

    assert executor(SourceReplaceAction(path="application/App.java", source=" ")).is_error
    assert executor(SourceReplaceAction(path="other.java", source="class Other {}")).is_error
    assert not executor(
        SourceReplaceAction(path="application/App.java", source="class App { int x; }")
    ).is_error
    assert source.read_text(encoding="utf-8") == "class App { int x; }"


def test_replace_source_creates_an_exact_missing_allowed_path(tmp_path: Path) -> None:
    source = tmp_path / "application/test/AppTest.java"
    executor = SourceReplaceExecutor(tmp_path, [str(source)])

    assert not executor(
        SourceReplaceAction(path="application/test/AppTest.java", source="class AppTest {}")
    ).is_error
    assert source.read_text(encoding="utf-8") == "class AppTest {}"


def test_edit_source_applies_replacement_insertion_and_deletion_atomically(tmp_path: Path) -> None:
    source = tmp_path / "application/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {\n  int oldValue;\n  int removeMe;\n}\n", encoding="utf-8")
    executor = SourceEditExecutor(tmp_path, [str(source)])

    result = executor(
        SourceEditAction(
            path="application/App.java",
            edits=[
                ExactSourceEdit(old_text="int oldValue;", new_text="int newValue;"),
                ExactSourceEdit(old_text="  int removeMe;\n", new_text=""),
                ExactSourceEdit(old_text="class App {\n", new_text="class App {\n  int added;\n"),
            ],
        )
    )

    assert not result.is_error
    assert result.source_sha256
    assert source.read_text(encoding="utf-8") == "class App {\n  int added;\n  int newValue;\n}\n"


@pytest.mark.parametrize(
    ("original", "old_text", "failure"),
    [
        ("class App { int value; }", "missing", "EDIT_CONTEXT_STALE"),
        ("class App { x + x; }", "x", "EDIT_CONTEXT_AMBIGUOUS"),
    ],
)
def test_edit_source_rejects_stale_or_ambiguous_context_without_partial_write(
    tmp_path: Path, original: str, old_text: str, failure: str
) -> None:
    source = tmp_path / "application/App.java"
    source.parent.mkdir(parents=True)
    source.write_text(original, encoding="utf-8")
    executor = SourceEditExecutor(tmp_path, [str(source)])

    result = executor(
        SourceEditAction(
            path="application/App.java",
            edits=[
                    ExactSourceEdit(old_text="class App", new_text="class Changed"),
                ExactSourceEdit(old_text=old_text, new_text="replacement"),
            ],
        )
    )

    assert result.is_error
    assert result.failure_code == failure
    assert source.read_text(encoding="utf-8") == original


def test_openhands_completion_has_one_wall_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.sdk import LLM

    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(LLM, "acompletion", never_finishes)
    monkeypatch.setattr(
        "app.implementation.agents.runtime.settings.llm_wall_timeout_seconds",
        0.01,
    )
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
    )
    try:
        with pytest.raises(TimeoutError, match="PROVIDER_TIMEOUT"):
            asyncio.run(agent.llm.acompletion([]))
    finally:
        conversation.close()


def test_bounded_restricted_owner_exposes_and_records_upstream_gap_only(tmp_path: Path) -> None:
    source_root = tmp_path / "application/src/main/java/example"
    source_root.mkdir(parents=True)
    connection = LlmConnection(
        provider="openrouter",
        api_key="validation-only-key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    conversation, agent = create_openhands_conversation(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        task_type="backend-implementation",
        editable_roots=[str(source_root.resolve())],
        native_owner_tools=True,
        owner_tool_mode="restricted",
        upstream_gap_source_refs=["UC-12"],
    )
    try:
        conversation.send_message("Initialize tools without calling the model.")
        assert UPSTREAM_GAP_TOOL_NAME in agent._tools
        assert "UC-12" in agent._tools[UPSTREAM_GAP_TOOL_NAME].description
        assert "omits or ambiguously defines required" in agent._tools[
            UPSTREAM_GAP_TOOL_NAME
        ].description
        executor = agent._tools[UPSTREAM_GAP_TOOL_NAME].executor
        invalid = executor(
            UpstreamGapAction(summary="Missing behavior", source_ref="UC-unknown"),
            conversation,
        )
        assert invalid.is_error is True
        assert reported_upstream_gap(agent) is None

        accepted = executor(
            UpstreamGapAction(summary="The response rule is not specified.", source_ref="UC-12"),
            conversation,
        )
        assert accepted.is_error is False
        assert reported_upstream_gap(agent).as_result() == {
            "summary": "The response rule is not specified.",
            "sourceRef": "UC-12",
        }
    finally:
        conversation.close()


def test_restricted_owner_applies_only_an_explicit_read_evidence_boundary(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "application/evidence/Allowed.java"
    forbidden_sibling = tmp_path / "application/evidence/ForbiddenSibling.java"
    unrelated = tmp_path / "application/unrelated/Other.java"
    evidence.parent.mkdir(parents=True)
    unrelated.parent.mkdir(parents=True)
    evidence.write_text("class Allowed { String needle; }\n", encoding="utf-8")
    forbidden_sibling.write_text(
        "class ForbiddenSibling { String needle; }\n",
        encoding="utf-8",
    )
    unrelated.write_text("class Other { String needle; }\n", encoding="utf-8")
    connection = LlmConnection(
        provider="openrouter",
        api_key="validation-only-key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    conversation, agent = create_openhands_conversation(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        native_owner_tools=True,
        owner_tool_mode="restricted",
        editable_roots=[str((tmp_path / "application").resolve())],
        readable_files=[str(evidence.resolve())],
    )
    try:
        from openhands.tools.file_editor import FileEditorAction
        from openhands.tools.grep import GrepAction

        conversation.send_message("Initialize tools without calling the model.")
        allowed_view = agent._tools["file_editor"].executor(
            FileEditorAction(command="view", path=str(evidence.resolve()))
        )
        allowed_grep = agent._tools["grep"].executor(
            GrepAction(pattern="needle", path=str(evidence.resolve()))
        )
        assert allowed_view.is_error is False
        assert allowed_grep.is_error is False
        allowed_directory_grep = agent._tools["grep"].executor(
            GrepAction(pattern="needle", path=str(evidence.parent.resolve()))
        )
        assert allowed_directory_grep.is_error is False
        assert "Allowed.java" in allowed_directory_grep.text
        assert "ForbiddenSibling.java" not in allowed_directory_grep.text

        rejected = [
            agent._tools["file_editor"].executor(
                FileEditorAction(command="view", path=str(unrelated.resolve()))
            ),
            agent._tools["grep"].executor(
                GrepAction(pattern="needle", path=str(unrelated.resolve()))
            ),
            agent._tools["file_editor"].executor(
                FileEditorAction(command="view", path=str(evidence.parent.resolve()))
            ),
            agent._tools["grep"].executor(
                GrepAction(pattern="needle", path=str(unrelated.parent.resolve()))
            ),
            agent._tools["grep"].executor(GrepAction(pattern="needle")),
        ]
        assert all(observation.is_error is True for observation in rejected)
        assert all(
            "READ_OUTSIDE_TASK_EVIDENCE" in observation.text
            for observation in rejected
        )
        classified = classify_harness_error_text(rejected[0].text)
        assert classified is not None
        assert classified.retryable is True
    finally:
        conversation.close()

    # Existing restricted owners do not receive an evidence allowlist and keep
    # their established workspace-wide read/search behavior.
    conversation, agent = create_openhands_conversation(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        native_owner_tools=True,
        owner_tool_mode="restricted",
        editable_roots=[str((tmp_path / "application").resolve())],
        readable_files=None,
    )
    try:
        from openhands.tools.file_editor import FileEditorAction
        from openhands.tools.grep import GrepAction

        conversation.send_message("Initialize tools without calling the model.")
        unbounded_view = agent._tools["file_editor"].executor(
            FileEditorAction(command="view", path=str(unrelated.resolve()))
        )
        unbounded_grep = agent._tools["grep"].executor(
            GrepAction(pattern="needle")
        )
        assert unbounded_view.is_error is False
        assert unbounded_grep.is_error is False
    finally:
        conversation.close()


def test_provider_output_parse_failure_enters_the_safe_sdk_retry_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="cloudflare",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="openai/gpt-oss-120b",
            litellm_provider="openai",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
    )
    base_llm = type(agent.llm).__mro__[1]

    def parsing_failure(_self, **_kwargs):
        raise RuntimeError(
            "HTTP 400 code 7003: Parsing failed. "
            "The model generated output that could not be parsed."
        )

    monkeypatch.setattr(base_llm, "_transport_call", parsing_failure)
    try:
        from openhands.sdk.llm.exceptions import LLMNoResponseError

        with pytest.raises(LLMNoResponseError, match="PROVIDER_OUTPUT_PARSE_TRANSIENT"):
            agent.llm._transport_call(messages=[])
    finally:
        conversation.close()


@pytest.mark.parametrize(
    ("owner_tool_mode", "expected_tool_choice"),
    [("editor", "required"), ("restricted", None)],
)
def test_cloudflare_chat_transport_requires_tools_only_for_editor_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_tool_mode: str,
    expected_tool_choice: str | None,
) -> None:
    source = tmp_path / "application/src/main/java/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="cloudflare",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="openai/gpt-oss-120b",
            litellm_provider="openai",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
        task_type="backend-implementation",
        editable_files=[str(source.resolve())],
        native_owner_tools=True,
        owner_tool_mode=owner_tool_mode,
    )
    base_llm = type(agent.llm).__mro__[1]
    captured: list[dict[str, object]] = []

    def capture_sync(_self, **kwargs):
        captured.append(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    async def capture_async(_self, **kwargs):
        captured.append(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    try:
        monkeypatch.setattr(base_llm, "_transport_call", capture_sync)
        agent.llm._transport_call(messages=[], tools=[{"type": "function"}])
        monkeypatch.setattr(base_llm, "_atransport_call", capture_async)
        asyncio.run(agent.llm._atransport_call(messages=[], tools=[{"type": "function"}]))
        assert [call.get("tool_choice") for call in captured] == [
            expected_tool_choice,
            expected_tool_choice,
        ]
    finally:
        conversation.close()


@pytest.mark.parametrize(
    ("response", "empty"),
    [
        (
            {"choices": [{"message": {"content": None, "tool_calls": []}}]},
            True,
        ),
        (
            {"choices": [{"message": {"content": "implemented", "tool_calls": []}}]},
            False,
        ),
        (
            {
                "choices": [
                    {"message": {"content": None, "tool_calls": [{"id": "call-1"}]}}
                ]
            },
            False,
        ),
        (
            {
                "choices": [
                    {"message": {"content": None, "reasoning_content": "inspect"}}
                ]
            },
            False,
        ),
    ],
)
def test_empty_successful_completion_enters_sdk_retry_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    empty: bool,
) -> None:
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="cloudflare",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="openai/gpt-oss-120b",
            litellm_provider="openai",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
    )
    base_llm = type(agent.llm).__mro__[1]
    monkeypatch.setattr(
        base_llm,
        "_validate_chat_response",
        lambda _self, result, **_kwargs: result,
    )
    try:
        from openhands.sdk.llm.exceptions import LLMNoResponseError

        if empty:
            with pytest.raises(
                LLMNoResponseError,
                match="PROVIDER_EMPTY_RESPONSE_TRANSIENT",
            ):
                agent.llm._validate_chat_response(response)
        else:
            assert agent.llm._validate_chat_response(response) is response
    finally:
        conversation.close()


def test_restricted_editor_rejects_a_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = tmp_path / "application/link.txt"
    link.parent.mkdir()
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable in this test environment: {error}")

    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
        native_owner_tools=True,
        owner_tool_mode="restricted",
        editable_roots=[str((tmp_path / "application").resolve())],
    )
    try:
        from openhands.tools.file_editor import FileEditorAction

        observation = agent._tools["file_editor"].executor(
            FileEditorAction(command="view", path="application/link.txt")
        )
        assert observation.is_error is True
        assert "PATH_OUTSIDE_WORKSPACE" in observation.text
    finally:
        conversation.close()


def test_model_tool_canary_runs_three_times_and_reuses_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def passed_attempt(*_args, **_kwargs):
        calls.append(1)
        return {
            "passed": True,
            "executionStatus": "finished",
            "actions": ["easydep_canary_read", "easydep_canary_check", "finish"],
            "eventCount": 7,
            "harnessErrorCounts": {},
            "terminationReason": None,
            "durationMs": 1,
        }

    monkeypatch.setattr(canary_module, "_canary_attempt", passed_attempt)
    connection = LlmConnection(
        provider="openrouter",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )

    first = ensure_model_tool_canary(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        owner_tool_mode="restricted",
        reasoning_effort="medium",
        repetitions=3,
    )
    second = ensure_model_tool_canary(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        owner_tool_mode="restricted",
        reasoning_effort="medium",
        repetitions=3,
    )

    assert first == second
    assert len(calls) == 3
    assert first["passed"] is True


def test_model_tool_canary_persists_a_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        canary_module,
        "_canary_attempt",
        lambda *_args, **_kwargs: {
            "passed": False,
            "executionStatus": "error",
            "actions": [],
            "eventCount": 1,
            "harnessErrorCounts": {"TOOL_PROTOCOL_TOKEN_LEAK": 1},
            "terminationReason": "TOOL_PROTOCOL_TOKEN_LEAK",
            "durationMs": 1,
        },
    )
    connection = LlmConnection(
        provider="openrouter",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )

    with pytest.raises(HarnessCompatibilityError, match="TOOL_PROTOCOL_TOKEN_LEAK"):
        ensure_model_tool_canary(
            tmp_path,
            connection,
            {"temperature": 0.2, "maxOutputTokens": 1024},
            owner_tool_mode="restricted",
            reasoning_effort="medium",
            repetitions=3,
        )

    result_files = list((tmp_path / "reports/openhands-harness").glob("canary-*.json"))
    assert len(result_files) == 1
    assert json.loads(result_files[0].read_text(encoding="utf-8"))["passed"] is False

    monkeypatch.setattr(
        canary_module,
        "_canary_attempt",
        lambda *_args, **_kwargs: pytest.fail("cached protocol failure was rerun"),
    )
    with pytest.raises(HarnessCompatibilityError, match="cached OpenHands canary"):
        ensure_model_tool_canary(
            tmp_path,
            connection,
            {"temperature": 0.2, "maxOutputTokens": 1024},
            owner_tool_mode="restricted",
            reasoning_effort="medium",
            repetitions=3,
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("provider timed out"), "PROVIDER_TIMEOUT"),
        (
            RuntimeError("ConversationRunError: PROVIDER_TIMEOUT: provider wall timeout exceeded"),
            "PROVIDER_TIMEOUT",
        ),
        (ConnectionError("DNS blocked"), "NETWORK_CONNECTION_ERROR"),
        (RuntimeError("HTTP 429 rate limit"), "PROVIDER_RATE_LIMIT"),
        (
            RuntimeError(
                "Conversation run failed: litellm.NotFoundError: Error code: 404 - "
                "{'errors': [{'message': 'Model not found: "
                "zai-org/glm-5.3-flash', 'code': 7003}]}"
            ),
            "MODEL_NOT_FOUND",
        ),
        (
            RuntimeError("tool call validation failed for request.tools"),
            "MODEL_TOOL_PROTOCOL_INCOMPATIBLE",
        ),
        (
            RuntimeError("Parsing failed. The model generated output could not be parsed."),
            "PROVIDER_OUTPUT_PARSE_TRANSIENT",
        ),
    ],
)
def test_canary_separates_transport_and_protocol_failures(
    error: Exception, expected: str
) -> None:
    assert classify_canary_exception(error) == expected


def test_canary_recovers_transient_failures_until_it_has_three_successes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passed = {
        "passed": True,
        "executionStatus": "finished",
        "actions": ["easydep_canary_read", "easydep_canary_check", "finish"],
        "eventCount": 7,
        "harnessErrorCounts": {},
        "terminationReason": None,
        "durationMs": 1,
    }
    responses: list[object] = [
        RuntimeError(
            "Parsing failed. The model generated output that could not be parsed."
        ),
        passed,
        passed,
        passed,
    ]

    def attempt(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return dict(response)

    delays: list[float] = []
    monkeypatch.setattr(canary_module, "_canary_attempt", attempt)
    monkeypatch.setattr(canary_module.random, "uniform", lambda _low, high: high)
    monkeypatch.setattr(canary_module.time, "sleep", delays.append)
    connection = LlmConnection(
        provider="cloudflare",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-120b",
        litellm_provider="openai",
    )

    result = ensure_model_tool_canary(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        owner_tool_mode="restricted",
        reasoning_effort="medium",
        repetitions=3,
        max_attempts=5,
        retry_min_wait_seconds=1,
        retry_max_wait_seconds=8,
        retry_multiplier=1,
    )

    assert result["passed"] is True
    assert result["endpointHealth"] == "recovered"
    assert result["attemptCount"] == 4
    assert result["successfulAttempts"] == 3
    assert result["transientFailureCount"] == 1
    assert delays == [1]
    assert result["attempts"][0]["backoffMs"] == 1000


def test_canary_treats_model_not_found_as_permanent_configuration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def model_not_found(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError(
            "Conversation run failed: litellm.NotFoundError: Error code: 404 - "
            "{'errors': [{'message': 'Model not found: zai-org/glm-5.3-flash', "
            "'code': 7003}]}"
        )

    monkeypatch.setattr(canary_module, "_canary_attempt", model_not_found)
    connection = LlmConnection(
        provider="cloudflare",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="zai-org/glm-5.3-flash",
        litellm_provider="openai",
    )
    arguments = {
        "owner_tool_mode": "restricted",
        "reasoning_effort": "medium",
        "repetitions": 3,
        "max_attempts": 5,
    }

    with pytest.raises(HarnessCompatibilityError, match="MODEL_NOT_FOUND"):
        ensure_model_tool_canary(
            tmp_path,
            connection,
            {"temperature": 0.2, "maxOutputTokens": 1024},
            **arguments,
        )

    result_files = list((tmp_path / "reports/openhands-harness").glob("canary-*.json"))
    assert len(result_files) == 1
    result = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert result["endpointHealth"] == "misconfigured"
    assert result["cacheExpiresAt"] is None
    assert result["attemptCount"] == 1
    assert result["attempts"][0]["terminationReason"] == "MODEL_NOT_FOUND"
    assert not list(
        (tmp_path / "reports/openhands-harness").glob("endpoint-circuit-*.json")
    )

    with pytest.raises(
        HarnessCompatibilityError,
        match="MODEL_NOT_FOUND: cached OpenHands canary failure",
    ):
        ensure_model_tool_canary(
            tmp_path,
            connection,
            {"temperature": 0.2, "maxOutputTokens": 1024},
            **arguments,
        )

    assert calls == [1]


def test_canary_transient_failure_opens_a_ttl_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def timeout(*_args, **_kwargs):
        calls.append(1)
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(canary_module, "_canary_attempt", timeout)
    monkeypatch.setattr(canary_module.random, "uniform", lambda _low, _high: 0)
    monkeypatch.setattr(canary_module.time, "sleep", lambda _delay: None)
    connection = LlmConnection(
        provider="openrouter",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-120b",
        litellm_provider="openrouter",
    )
    arguments = {
        "owner_tool_mode": "restricted",
        "reasoning_effort": "medium",
        "repetitions": 1,
        "max_attempts": 2,
        "transient_failure_ttl_seconds": 60,
        "retry_min_wait_seconds": 0,
        "retry_max_wait_seconds": 0,
    }

    with pytest.raises(HarnessCompatibilityError, match="ENDPOINT_DEGRADED"):
        ensure_model_tool_canary(
            tmp_path,
            connection,
            {"temperature": 0.2, "maxOutputTokens": 1024},
            **arguments,
        )
    with pytest.raises(HarnessCompatibilityError, match="cached transient"):
        ensure_model_tool_canary(
            tmp_path,
            connection,
            {"temperature": 0.2, "maxOutputTokens": 1024},
            **arguments,
        )

    assert len(calls) == 2


def test_canary_marks_an_in_sequence_llm_retry_as_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        canary_module,
        "_canary_attempt",
        lambda *_args, **_kwargs: {
            "passed": True,
            "executionStatus": "finished",
            "actions": ["easydep_canary_read", "easydep_canary_check", "finish"],
            "eventCount": 7,
            "harnessErrorCounts": {},
            "terminationReason": None,
            "durationMs": 1,
            "endpointRetries": {
                "retryCount": 1,
                "reasons": {"PROVIDER_OUTPUT_PARSE_TRANSIENT": 1},
                "events": [],
            },
        },
    )
    connection = LlmConnection(
        provider="cloudflare",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-120b",
        litellm_provider="openai",
    )

    result = ensure_model_tool_canary(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        owner_tool_mode="restricted",
        reasoning_effort="medium",
        repetitions=1,
        max_attempts=1,
    )

    assert result["passed"] is True
    assert result["endpointRetryCount"] == 1
    assert result["endpointHealth"] == "recovered"


def test_task_level_endpoint_circuit_blocks_a_cached_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = LlmConnection(
        provider="cloudflare",
        api_key="key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-120b",
        litellm_provider="openai",
    )
    common = {
        "owner_tool_mode": "restricted",
        "reasoning_effort": "medium",
        "repetitions": 1,
        "max_attempts": 1,
    }
    monkeypatch.setattr(
        canary_module,
        "_canary_attempt",
        lambda *_args, **_kwargs: {
            "passed": True,
            "executionStatus": "finished",
            "actions": ["easydep_canary_read", "easydep_canary_check", "finish"],
            "eventCount": 7,
            "harnessErrorCounts": {},
            "terminationReason": None,
            "durationMs": 1,
        },
    )
    ensure_model_tool_canary(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        **common,
    )
    circuit = open_endpoint_circuit(
        tmp_path,
        connection,
        owner_tool_mode="restricted",
        reasoning_effort="medium",
        ttl_seconds=60,
        reason="PROVIDER_OUTPUT_PARSE_TRANSIENT",
    )
    monkeypatch.setattr(
        canary_module,
        "_canary_attempt",
        lambda *_args, **_kwargs: pytest.fail("open circuit must block the canary"),
    )

    with pytest.raises(HarnessCompatibilityError, match="task-level endpoint circuit"):
        ensure_model_tool_canary(
            tmp_path,
            connection,
            {"temperature": 0.2, "maxOutputTokens": 1024},
            **common,
        )

    assert circuit["status"] == "open"

    open_endpoint_circuit(
        tmp_path,
        connection,
        owner_tool_mode="restricted",
        reasoning_effort="medium",
        ttl_seconds=0,
        reason="PROVIDER_OUTPUT_PARSE_TRANSIENT",
    )
    refreshes: list[int] = []

    def refreshed_attempt(*_args, **_kwargs):
        refreshes.append(1)
        return {
            "passed": True,
            "executionStatus": "finished",
            "actions": ["easydep_canary_read", "easydep_canary_check", "finish"],
            "eventCount": 7,
            "harnessErrorCounts": {},
            "terminationReason": None,
            "durationMs": 1,
        }

    monkeypatch.setattr(canary_module, "_canary_attempt", refreshed_attempt)
    refreshed = ensure_model_tool_canary(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        **common,
    )

    assert refreshed["passed"] is True
    assert refreshes == [1]


def _write_evaluation_fixture(run_root: Path) -> None:
    execution_dir = run_root / "reports/agent-executions"
    execution_dir.mkdir(parents=True)
    journal = execution_dir / "backend.attempt-001.events.jsonl"
    records = [
        {
            "sequence": 0,
            "timestamp": 10.0,
            "type": "ActionEvent",
            "tool": "file_editor",
            "event": {
                "llm_response_id": "response-1",
                "action": {"command": "str_replace", "path": "application/App.java"},
            },
        },
        {
            "sequence": 1,
            "timestamp": 11.0,
            "type": "ObservationEvent",
            "tool": "terminal",
            "event": {
                "observation": {
                    "exit_code": 0,
                    "is_error": False,
                    "content": [{"text": "grep: ..: Permission denied\n0"}],
                }
            },
        },
        {
            "sequence": 2,
            "timestamp": 12.0,
            "type": "ObservationEvent",
            "tool": "file_editor",
            "event": {
                "observation": {
                    "is_error": True,
                    "content": [
                        {
                            "text": "Path is outside the assigned workspace: /other"
                        }
                    ],
                }
            },
        },
    ]
    journal.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    result = {
        "taskId": "backend",
        "status": "SUCCEEDED",
        "promptSha256": "prompt",
        "effectiveModel": "model",
        "durationMs": 5000,
        "eventJournal": "reports/agent-executions/backend.attempt-001.events.jsonl",
        "conversationStats": {
            "usage_to_metrics": {
                "implementation_agent": {
                    "accumulated_token_usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                    }
                }
            }
        },
    }
    (execution_dir / "backend.attempt-001.result.json").write_text(
        json.dumps(result), encoding="utf-8"
    )


def test_harness_evaluation_reports_quality_errors_time_and_usage(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_evaluation_fixture(baseline)
    _write_evaluation_fixture(candidate)

    report = evaluate_harness_run(candidate)
    metrics = report["metrics"]
    assert metrics["tokens"] == 120
    assert metrics["durationMs"] == 5000
    assert metrics["toolCalls"] == 1
    assert metrics["firstValidEditMs"] == 0
    assert metrics["errorRecoveryMs"] == 0
    assert metrics["workspaceViolations"] == 1
    assert metrics["falseSuccessStates"] == 1
    assert metrics["finalTaskSuccessRate"] == 1.0
    assert compare_harness_runs(baseline, candidate)["comparable"] is True


def test_journal_metrics_counts_only_valid_direct_source_replacements(tmp_path: Path) -> None:
    journal = tmp_path / "direct-editor.events.jsonl"
    records = [
        {"timestamp": 10.0, "type": "MessageEvent", "event": {}},
        {
            "timestamp": 12.5,
            "type": "DirectEditorAction",
            "event": {
                "path": "application/App.java",
                "sourceSha256": "a" * 64,
            },
        },
        {
            "timestamp": 14.0,
            "type": "DirectEditorAction",
            "event": {"path": "application/Bad.java", "sourceSha256": "not-a-hash"},
        },
        {
            "timestamp": 15.0,
            "type": "CustomAction",
            "event": {"path": "application/Other.java", "sourceSha256": "b" * 64},
        },
    ]
    journal.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    metrics = _journal_metrics(journal)

    assert metrics["toolCalls"] == 1
    assert metrics["firstValidEditMs"] == 2500
