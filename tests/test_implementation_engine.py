from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.implementation.agents.workspace as workspace_module
from app.design.services.erd.mapping import build_logical_model
from app.implementation.agents import execute_openhands_task
from app.implementation.agents.runtime import (
    OWNER_TURN_ITERATIONS,
    NoActionResponseGuard,
    OwnerConversationIncomplete,
    _conversation_terminal_failure,
    _owner_continuation_required,
    _owner_message_required,
    _owner_workspace_guidance,
    _seed_owner_task_tracker,
    _sync_owner_task_tracker,
    _task_execution_scope,
    create_openhands_conversation,
)
from app.implementation.agents.task_check import (
    TaskCheckSession,
    consume_successful_task_check,
    run_task_check,
)
from app.implementation.agents.verification.build import (
    WorkspaceVerificationError,
    read_gradle_test_failures,
    task_verification_command,
    verify_agent_workspace,
    verify_run_workspace,
    verify_use_case_scenarios,
)
from app.implementation.agents.workspace import (
    _apply_fixed_runner_permissions,
    _harden_control_tree,
    cleanup_agent_workspace,
    grant_owner_file_access,
    path_is_editable,
    prepare_agent_workspace,
)
from app.implementation.delivery.terraform import render_iac
from app.implementation.domain.models import JobSpec
from app.implementation.runtime.linux_runner_transport import OWNER_CONTROL_ROOT_ENV
from app.implementation.workflows.completion import audit_run_completion
from app.implementation.workflows.conformance import (
    SourceDesignConformanceError,
    capture_generated_contracts,
    verify_source_design_conformance,
)
from app.implementation.workflows.coordinator import (
    _execute_task_batch,
    plan_workflow,
    reconcile_workflow_state,
    run_workflow,
)
from app.implementation.workflows.repair import (
    apply_repair_directives,
    schedule_cross_phase_repair,
)
from app.llm_connection import LlmConnection
from tests.class_design_fixtures import (
    typed_class_model_payload,
    typed_sequence_model_payload,
)


class _FakeConversationStats:
    def model_dump(self, *, mode: str, context: dict[str, object]) -> dict[str, object]:
        assert mode == "json"
        assert context == {"use_snapshot": True}
        return {"usage": {"promptTokens": 21, "completionTokens": 8}}


def test_final_workspace_verification_publishes_success_report(
    tmp_path: Path,
) -> None:
    """작업자가 생성한 소스를 최종 검증하고 공개 보고서를 남기는 흐름을 확인한다."""
    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")
    verification = {"exitCode": 0, "testResults": ""}
    with (
        patch(
            "app.implementation.agents.verification.build.verify_agent_workspace",
            return_value=verification,
        ),
    ):
        result = verify_run_workspace(run)

    report = json.loads((run / "reports/final-verification.json").read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert report["verification"] == verification


def test_feedback_regression_succeeds_when_http_scenarios_are_deferred(
    tmp_path: Path,
) -> None:
    """구현 테스트가 통과하면 HTTP 검사를 Testing에 맡기고 수리를 끝낸다."""
    run = tmp_path / "generated" / "runs" / "run_feedback"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")

    with patch(
        "app.implementation.agents.verification.build.verify_agent_workspace",
        return_value={"exitCode": 0, "testResults": ""},
    ):
        result = verify_run_workspace(
            run,
            "feedback-regression.json",
            verify_frontend=False,
            verify_end_to_end=False,
        )

    assert result["status"] == "SUCCEEDED"
    assert result["scenarioVerification"]["status"] == "NOT_CHECKED"


def test_one_scenario_method_can_cover_multiple_use_cases(tmp_path: Path) -> None:
    """한 흐름으로 여러 유스케이스를 검사한 테스트를 개수 부족으로 거절하지 않는다."""
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-backend-application",
                        "task_type": "backend-implementation",
                        "owner": "backend",
                        "use_case_ids": ["UC1", "UC2", "UC3"],
                        "required_test_paths": [
                            "application/src/test/java/com/example/BackendApplicationTest.java"
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    junit = tmp_path / "sandbox/application/build/test-results/test/TEST-flow.xml"
    junit.parent.mkdir(parents=True)
    junit.write_text(
        """<testsuite tests="2" failures="0" errors="0" skipped="0">
<testcase classname="com.example.BackendApplicationTest" name="fullApplicationFlow"/>
</testsuite>""",
        encoding="utf-8",
    )

    result = verify_use_case_scenarios(tmp_path / "sandbox", run)

    assert result["status"] == "PASSED"
    assert result["coveredUseCaseIds"] == ["UC1", "UC2", "UC3"]
    assert [task["requiredPassedCases"] for task in result["tasks"]] == [1]


def test_agent_workspace_refresh_preserves_ignored_build_outputs(
    tmp_path: Path,
) -> None:
    """재시도 준비는 Windows가 잠글 수 있는 Gradle 산출물을 건드리지 않는다."""
    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")
    task = {"task_id": "locked-build", "allowed_write_paths": []}

    with patch(
        "app.implementation.agents.workspace.tempfile.gettempdir",
        return_value=str(tmp_path / "temp"),
    ), patch(
        "app.implementation.agents.workspace._restore_coordinator_access"
    ) as restore_access:
        sandbox = prepare_agent_workspace(run, task)
        build_output = sandbox / "application/build/test-results/test/binary/output.bin"
        build_output.parent.mkdir(parents=True)
        build_output.write_bytes(b"test output")
        stale_source = sandbox / "application/src/Stale.java"
        stale_source.write_text("class Stale {}", encoding="utf-8")

        refreshed = prepare_agent_workspace(run, task)

    assert refreshed == sandbox
    restore_access.assert_called_once_with(sandbox)
    assert build_output.read_bytes() == b"test output"
    assert not stale_source.exists()


def test_fixed_runner_hands_the_whole_disposable_sandbox_to_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_root = tmp_path / "job"
    run = control_root / "generated/runs/run_123"
    sandbox = run / "reports/agent-workspaces/backend"
    ordinary = sandbox / "application/src/main/java/example/Service.java"
    generated = sandbox / "application/src/main/java/example/api/Contract.java"
    build_output = sandbox / "application/build/classes/Service.class"
    for path in (ordinary, generated, build_output):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")
    (control_root / "job.json").write_text("{}", encoding="utf-8")

    ownership: list[tuple[Path, int, int]] = []
    modes: list[tuple[Path, int]] = []
    hardened: list[tuple[Path, Path]] = []
    native_walk = workspace_module.os.walk
    fake_os = SimpleNamespace(
        name="posix",
        environ={
            "EASYDEP_OWNER_TERMINAL_USER": "appuser",
            "EASYDEP_OWNER_CONTROL_ROOT": str(control_root),
        },
        geteuid=lambda: 0,
        walk=native_walk,
        chown=lambda path, uid, gid: ownership.append((Path(path).resolve(), uid, gid)),
    )
    monkeypatch.setattr(workspace_module, "os", fake_os)
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda self, mode: modes.append((self.resolve(), mode)),
    )
    monkeypatch.setattr(
        workspace_module,
        "_harden_control_tree",
        lambda root, candidate: hardened.append((root, candidate)),
    )

    with patch.dict(
        "sys.modules",
        {"pwd": SimpleNamespace(getpwnam=lambda _name: SimpleNamespace(pw_uid=1001, pw_gid=1002))},
    ):
        _apply_fixed_runner_permissions(
            run,
            sandbox,
            {
                "allowed_write_paths": [ordinary.relative_to(sandbox).as_posix()],
                "immutable_paths": [generated.relative_to(sandbox).as_posix()],
            },
        )

    sandbox_paths = {
        sandbox.resolve(),
        *(
            path.resolve()
            for path in sandbox.rglob("*")
            if not any(part in {"build", ".gradle", "node_modules", "dist"} for part in path.parts)
        ),
    }
    assert {path for path, _uid, _gid in ownership} == sandbox_paths
    assert {(uid, gid) for _path, uid, gid in ownership} == {(1001, 1002)}
    assert (generated.resolve(), 0o644) in modes
    assert build_output.resolve() not in {path for path, _mode in modes}
    assert (sandbox.resolve(), 0o755) in modes
    assert (control_root / "job.json").resolve() not in {
        path for path, _uid, _gid in ownership
    }
    assert hardened == [(control_root.resolve(), sandbox.resolve())]


def test_persistent_owner_workspace_uses_short_control_root_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows-hosted retry name must not exhaust the owner source path budget."""
    with tempfile.TemporaryDirectory(prefix="easydep-owner-path-") as temporary:
        control = Path(temporary) / "implementation-runs" / ("a" * 36)
        run = control / "generated" / "runs" / "run_123456789abc_retry_1"
        source = run / "application/src/main/java/com/easydep/app/LongService.java"
        source.parent.mkdir(parents=True)
        source.write_text("class LongService {}", encoding="utf-8")
        monkeypatch.setenv(OWNER_CONTROL_ROOT_ENV, str(control))
        task = {
            "task_id": "implement-backend-application",
            "allowed_write_paths": [
                "application/src/main/java/com/easydep/app/application/impl/"
                "VeryLongGeneratedApplicationService.java"
            ],
            "allowed_write_roots": ["application/src/main/java/com/easydep/app"],
            "immutable_paths": [],
        }

        sandbox = prepare_agent_workspace(run, task, persistent=True)

        assert sandbox.parent == (control / "w").resolve()
        assert (sandbox / source.relative_to(run)).is_file()
        cleanup_agent_workspace(sandbox, run_root=run)


def test_control_tree_is_root_owned_outside_owner_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_root = (tmp_path / "job").resolve()
    sandbox = control_root / "generated/runs/run_123/reports/agent-workspaces/backend"
    candidate = sandbox / "application/src/Main.java"
    secret = control_root / "control/private.json"
    for path in (candidate, secret):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")

    ownership: list[tuple[Path, int, int]] = []
    final_modes: dict[Path, int] = {}
    native_walk = workspace_module.os.walk
    fake_os = SimpleNamespace(
        walk=native_walk,
        chown=lambda path, uid, gid: ownership.append((Path(path).resolve(), uid, gid)),
    )
    monkeypatch.setattr(workspace_module, "os", fake_os)
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda self, mode: final_modes.__setitem__(self.resolve(), mode),
    )

    _harden_control_tree(control_root, sandbox)

    touched = {path for path, _uid, _gid in ownership}
    assert all((uid, gid) == (0, 0) for _path, uid, gid in ownership)
    assert secret.resolve() in touched
    assert final_modes[secret.resolve()] == 0o600
    assert final_modes[secret.parent.resolve()] == 0o700
    assert final_modes[control_root] == 0o711
    assert final_modes[sandbox.parent.resolve()] == 0o711
    assert sandbox.resolve() not in touched
    assert candidate.resolve() not in touched
    assert sandbox.resolve() not in final_modes
    assert candidate.resolve() not in final_modes


def test_editor_result_is_handed_back_only_within_owner_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = (tmp_path / "sandbox").resolve()
    generated = sandbox / "application/generated/Contract.java"
    generated.parent.mkdir(parents=True)
    generated.write_text("contract", encoding="utf-8")
    outside = tmp_path / "job.json"
    outside.write_text("{}", encoding="utf-8")

    ownership: list[tuple[Path, int, int]] = []
    modes: list[tuple[Path, int]] = []
    fake_os = SimpleNamespace(
        name="posix",
        environ={"EASYDEP_OWNER_TERMINAL_USER": "appuser"},
        geteuid=lambda: 0,
        chown=lambda path, uid, gid: ownership.append((Path(path).resolve(), uid, gid)),
    )
    monkeypatch.setattr(workspace_module, "os", fake_os)
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda self, mode: modes.append((self.resolve(), mode)),
    )

    with patch.dict(
        "sys.modules",
        {"pwd": SimpleNamespace(getpwnam=lambda _name: SimpleNamespace(pw_uid=1001, pw_gid=1002))},
    ):
        grant_owner_file_access(generated, sandbox)
        granted_count = len(ownership)
        grant_owner_file_access(outside, sandbox)

    expected = {
        generated.resolve(),
        generated.parent.resolve(),
        generated.parent.parent.resolve(),
        sandbox.resolve(),
    }
    assert {path for path, _uid, _gid in ownership} == expected
    assert len(ownership) == granted_count
    assert {(uid, gid) for _path, uid, gid in ownership} == {(1001, 1002)}
    assert (generated.resolve(), 0o644) in modes
    assert (sandbox.resolve(), 0o755) in modes
    assert outside.resolve() not in {path for path, _uid, _gid in ownership}


def test_work_unit_verification_runs_related_tests_directly_with_cache() -> None:
    """작업 검증은 관련 test 하나를 직접 실행해 중복 compile 단계를 줄인다."""
    assert task_verification_command(
        ["gradlew"],
        "control",
        ["application/src/test/java/com/example/OrderScenarioTest.java"],
    ) == ["gradlew", "test", "--tests", "*OrderScenarioTest", "--build-cache"]
    assert task_verification_command(["gradlew"]) == [
        "gradlew",
        "test",
        "--build-cache",
    ]
    assert task_verification_command(
        ["gradlew"],
        "backend-implementation",
        ["application/src/test/java/com/example/BackendApplicationTest.java"],
    ) == ["gradlew", "test", "--build-cache"]


def test_dynamic_testing_repair_reruns_preserved_arazzo_workflow(
    tmp_path: Path,
) -> None:
    """A dynamic repair must pass the exact preserved workflow before completion."""

    source_path = "application/src/main/java/com/example/OrderService.java"
    (tmp_path / "application").mkdir()
    evidence = {
        "command": ["testing-dynamicFunctional"],
        "exitCode": 0,
        "gateStatus": "PASS",
    }
    with patch(
        "app.testing.repair_check.verify_testing_repair_gate",
        return_value=evidence,
    ) as dynamic_gate:
        profile = {
            "candidate_plan": {"arazzo": "1.1.0"},
            "failed_workflow_id": "workflow-UC-1",
        }
        result = verify_agent_workspace(
            tmp_path,
            "testing-dynamic-functional",
            [source_path],
            profile,
        )

    assert result == evidence
    dynamic_gate.assert_called_once_with(
        tmp_path,
        "testing-dynamic-functional",
        profile,
    )


def test_agent_task_check_returns_real_focused_verification_result(
    tmp_path: Path,
) -> None:
    """코딩 에이전트의 검사 도구가 별도 명령 없이 기존 검증 결과를 돌려준다."""
    evidence = {
        "command": ["gradlew", "test", "--tests", "*OrderScenarioTest"],
        "exitCode": 1,
        "durationMs": 321,
        "stderr": "OrderService.java:42: incompatible types",
        "testResults": "OrderScenarioTest.placesOrder: assertion failed",
        "diagnosticPaths": [str(tmp_path / "application/build/test-results/test")],
    }
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        side_effect=WorkspaceVerificationError(evidence),
    ) as verify:
        passed, output = run_task_check(
            tmp_path,
            "use-case",
            ["application/src/test/java/com/example/OrderScenarioTest.java"],
        )

    assert passed is False
    assert "TASK CHECK FAILED" in output
    assert "OrderScenarioTest.placesOrder: assertion failed" in output
    assert "Full diagnostics" not in output
    assert str(tmp_path / "application/build/test-results/test") not in output
    verify.assert_called_once_with(
        tmp_path,
        "use-case",
        ["application/src/test/java/com/example/OrderScenarioTest.java"],
    )


def test_agent_task_check_compacts_duplicate_framework_traces(tmp_path: Path) -> None:
    """같은 Spring trace가 여러 출력에 있어도 핵심 원인은 한 번만 전달한다."""
    root_cause = "Caused by: NoSuchBeanDefinitionException: OrderRepository"
    framework_trace = "\n".join(
        [root_cause, *[f"at org.springframework.example.Frame{i}" for i in range(500)]]
    )
    evidence = {
        "command": ["gradlew", "test"],
        "exitCode": 1,
        "testResults": framework_trace,
        "stderr": framework_trace,
    }
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        side_effect=WorkspaceVerificationError(evidence),
    ):
        passed, output = run_task_check(tmp_path, "use-case", [])

    assert passed is False
    assert output.count(root_cause) == 1
    assert len(output) < 8500


def test_gradle_failure_summary_prefers_the_deepest_root_cause(tmp_path: Path) -> None:
    """반복된 Spring context 실패보다 실제 Hibernate 원인을 먼저 전달한다."""
    result_dir = tmp_path / "application/build/test-results/test"
    result_dir.mkdir(parents=True)
    (result_dir / "TEST-example.xml").write_text(
        """<testsuite tests="2" failures="2">
<testcase classname="example.FlowTest" name="first">
  <failure message="Failed to load ApplicationContext">java.lang.IllegalStateException
Caused by: jakarta.persistence.PersistenceException: session factory failed
Caused by: org.hibernate.MappingException: Column 'student_id' is duplicated</failure>
</testcase>
<testcase classname="example.FlowTest" name="second">
  <failure message="failure threshold exceeded">ApplicationContext failure threshold exceeded</failure>
</testcase>
</testsuite>""",
        encoding="utf-8",
    )

    summary = read_gradle_test_failures(tmp_path)

    assert "Column 'student_id' is duplicated" in summary
    assert summary.index("MappingException") < summary.index("IllegalStateException")
    assert "Other failing tests: example.FlowTest.second" in summary


def test_agent_task_check_requires_a_source_change_before_retry(
    tmp_path: Path,
) -> None:
    """같은 실패 상태에서는 Gradle을 다시 돌리지 않고 먼저 수정을 요구한다."""
    source = tmp_path / "application/src/main/java/com/example/OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    evidence = {
        "command": ["gradlew", "compileJava"],
        "exitCode": 1,
        "stderr": "cannot find symbol",
    }
    session = TaskCheckSession(tmp_path, "use-case", [])
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        side_effect=WorkspaceVerificationError(evidence),
    ) as verify:
        first_passed, _ = session.run()
        second_passed, second_output = session.run()

    assert first_passed is False
    assert second_passed is False
    assert "source has not changed" in second_output
    verify.assert_called_once()


def test_successful_agent_check_is_reused_only_for_the_same_source(
    tmp_path: Path,
) -> None:
    """에이전트가 통과시킨 동일 검사를 대화 종료 직후 다시 실행하지 않는다."""
    source = tmp_path / "application/src/main/java/com/example/OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    evidence = {"command": ["gradlew", "test"], "exitCode": 0}
    paths = ["application/src/main/java/com/example/OrderService.java"]
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        return_value=evidence,
    ) as verify:
        passed, _ = run_task_check(tmp_path, "use-case", paths)
        reused = consume_successful_task_check(tmp_path, "use-case", paths)

    assert passed is True
    assert reused == {**evidence, "reusedFromTaskCheck": True}
    verify.assert_called_once()


def test_use_case_scope_allows_owned_package_but_protects_other_features() -> None:
    """기능 전용 package 안의 새 파일은 허용하고 다른 기능과 생성 계약은 보호한다."""
    roots = ["application/src/main/java/com/example/application"]
    immutable = [
        "application/src/main/java/com/example/api",
        "application/src/main/java/com/example/bce/OrderControl.java",
    ]

    assert path_is_editable(
        "application/src/main/java/com/example/application/OrderService.java",
        [],
        roots,
        immutable,
    )
    assert not path_is_editable(
        "application/src/main/java/com/example/persistence/OrderRepository.java",
        [],
        roots,
        immutable,
    )
    assert not path_is_editable(
        "application/src/main/java/com/example/api/OrdersApi.java",
        [],
        roots,
        immutable,
    )
    assert not path_is_editable(
        "application/src/main/java/com/example/bce/OrderControl.java",
        [],
        roots,
        immutable,
    )


def _write_minimal_agent_task(tmp_path: Path) -> tuple[Path, str, str, Path]:
    """Conversation 수리 테스트가 함께 쓰는 작은 구현 작업을 만든다."""
    run = tmp_path / "run_abcdef123456"
    task_id = "implement-order"
    source_path = "application/src/main/java/com/example/application/OrderService.java"
    source = run / source_path
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    reports = run / "reports"
    tasks = reports / "implementation-tasks"
    tasks.mkdir(parents=True)
    prompt = tasks / "order.prompt.md"
    context = tasks / "order.context.json"
    prompt.write_text("Implement the order use case.", encoding="utf-8")
    context.write_text("{}", encoding="utf-8")
    task = {
        "task_id": task_id,
        "task_type": "use-case",
        "prompt_file": prompt.relative_to(run).as_posix(),
        "context_file": context.relative_to(run).as_posix(),
        "prompt_sha256": "prompt-hash",
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
        "immutable_paths": [],
        "llm": {
            "temperature": 0.2,
            "maxOutputTokens": 1024,
            "reasoningEffort": "medium",
        },
    }
    (tasks / "order.task.json").write_text(json.dumps(task), encoding="utf-8")
    return run, task_id, source_path, source


def test_runner_does_not_duplicate_openhands_provider_retries(
    tmp_path: Path,
) -> None:
    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)
    source.unlink()

    class FakeConversation:
        def __init__(self, sandbox: Path, number: int) -> None:
            self.sandbox = sandbox
            self.number = number
            self.messages: list[str] = []
            self.run_count = 0
            self.close_count = 0

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            if self.number == 1:
                raise RuntimeError(
                    "BadRequestError: Tool call validation failed: attempted to call "
                    "tool 'made_up_tool' which was not in request.tools"
                )
            (self.sandbox / source_path).write_text(
                "class OrderService { int recovered; }",
                encoding="utf-8",
            )

        def close(self) -> None:
            self.close_count += 1

    created: list[FakeConversation] = []

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        conversation = FakeConversation(sandbox, len(created) + 1)
        created.append(conversation)
        return conversation, SimpleNamespace(_tools={})

    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                api_key="approved-key",
                provider="openrouter",
                model="openai/gpt-4o-mini",
                display_name=lambda: "OpenRouter",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            return_value={"command": ["gradlew", "compileJava"], "exitCode": 0},
        ),
        pytest.raises(RuntimeError, match="made_up_tool"),
    ):
        execute_openhands_task(run, task_id)

    assert len(created) == 1
    assert created[0].run_count == 1
    assert created[0].close_count == 1
    assert len(created[0].messages) == 1
    assert not source.exists()
    failure = json.loads(
        (run / f"reports/agent-executions/{task_id}.result.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["status"] == "FAILED"
    assert failure["conversationStats"] is None


def test_failed_verification_is_not_promoted_and_keeps_the_sandbox(
    tmp_path: Path,
) -> None:
    """A failed final guard keeps the accepted source and resumable sandbox separate."""
    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox
            self.messages: list[str] = []
            self.run_count = 0
            self.close_count = 0
            self.conversation_stats = _FakeConversationStats()

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            if self.run_count == 1:
                (self.sandbox / source_path).write_text(
                    "class OrderService { int brokenCandidate; }",
                    encoding="utf-8",
                )

        def close(self) -> None:
            self.close_count += 1

    created: list[FakeConversation] = []

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        conversation = FakeConversation(sandbox)
        created.append(conversation)
        return conversation, SimpleNamespace(_tools={})

    failure = WorkspaceVerificationError(
        {
            "command": ["gradlew", "compileJava"],
            "exitCode": 1,
            "stderr": f"{source_path}: cannot find symbol",
        }
    )
    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                api_key="approved-key",
                provider="openrouter",
                model="openai/gpt-4o-mini",
                display_name=lambda: "OpenRouter",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ) as create,
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            side_effect=failure,
        ),
        pytest.raises(WorkspaceVerificationError),
    ):
        execute_openhands_task(run, task_id)

    assert create.call_count == 1
    assert len(created[0].messages) == 1
    assert created[0].run_count == 1
    assert created[0].close_count == 1
    assert source.read_text(encoding="utf-8") == "class OrderService {}"
    assert "brokenCandidate" in (created[0].sandbox / source_path).read_text(
        encoding="utf-8"
    )
    failure_result = json.loads(
        (run / f"reports/agent-executions/{task_id}.result.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure_result["status"] == "FAILED"
    assert failure_result["verificationEvidence"]["exitCode"] == 1
    assert failure_result["conversationStats"] == {
        "usage": {"promptTokens": 21, "completionTokens": 8}
    }


def test_owner_candidate_contract_change_is_rejected_before_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    contract_path = "application/src/main/java/com/example/api/OrdersApi.java"
    contract = run / contract_path
    contract.parent.mkdir(parents=True)
    contract.write_text("interface OrdersApi {}", encoding="utf-8")
    task.update(
        {
            "task_type": "backend-implementation",
            "owner": "backend",
            "allowed_write_roots": [str(Path(source_path).parent.as_posix())],
            "immutable_paths": ["application/src/main/java/com/example/api"],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    source.write_text("// EASYDEP-IMPLEMENT: pending\n", encoding="utf-8")
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")

    sandboxes: list[Path] = []

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox
            sandboxes.append(sandbox)

        def send_message(self, _message: str) -> None:
            pass

        def run(self) -> None:
            (self.sandbox / source_path).write_text(
                "class OrderService { int candidate; }",
                encoding="utf-8",
            )
            (self.sandbox / contract_path).write_text(
                "interface OrdersApi { void changed(); }",
                encoding="utf-8",
            )

        def close(self) -> None:
            pass

    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                api_key="approved-key",
                provider="openrouter",
                model="openai/gpt-4o-mini",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
            patch(
                "app.implementation.agents.runtime.create_openhands_conversation",
                side_effect=lambda sandbox, *_args, **_kwargs: (
                    FakeConversation(sandbox),
                    SimpleNamespace(_tools={}),
                ),
            ),
            patch("app.implementation.agents.runtime._sync_owner_task_tracker"),
            patch(
                "app.implementation.agents.runtime.verify_agent_workspace"
            ) as verify,
        pytest.raises(WorkspaceVerificationError),
    ):
        execute_openhands_task(run, task_id)

    verify.assert_not_called()
    assert source.read_text(encoding="utf-8") == "// EASYDEP-IMPLEMENT: pending\n"
    assert contract.read_text(encoding="utf-8") == "interface OrdersApi {}"
    failure = json.loads(
        (run / f"reports/agent-executions/{task_id}.result.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["verificationEvidence"]["unauthorizedChanges"] == [contract_path]
    candidate = sandboxes[0] / contract_path
    assert "void changed" in candidate.read_text(encoding="utf-8")


def test_testing_repair_uses_one_openhands_run(tmp_path: Path) -> None:
    """EasyDep does not add a second repair loop around OpenHands."""

    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["task_type"] = "testing-dynamic-functional"
    task_path.write_text(json.dumps(task), encoding="utf-8")

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox
            self.messages: list[str] = []
            self.run_count = 0
            self.conversation_stats = _FakeConversationStats()

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            if self.run_count == 2:
                (self.sandbox / source_path).write_text(
                    "class OrderService { int repairedRuntimePath; }",
                    encoding="utf-8",
                )

        def close(self) -> None:
            pass

    conversation: FakeConversation | None = None

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        nonlocal conversation
        conversation = FakeConversation(sandbox)
        return conversation, SimpleNamespace(_tools={})

    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                api_key="approved-key",
                provider="openrouter",
                model="openai/gpt-4o-mini",
                display_name=lambda: "OpenRouter",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            return_value={"command": ["gradlew", "compileJava"], "exitCode": 0},
        ) as verify,
    ):
        result = execute_openhands_task(run, task_id)

    assert result["status"] == "SUCCEEDED"
    assert conversation is not None
    assert conversation.run_count == 1
    assert len(conversation.messages) == 1
    verify.assert_called_once()
    assert source.read_text(encoding="utf-8") == "class OrderService {}"
    assert result["conversationStats"] == {
        "usage": {"promptTokens": 21, "completionTokens": 8}
    }


def test_successful_retry_promotes_changes_preserved_from_failed_sandbox(
    tmp_path: Path,
) -> None:
    """A later successful check promotes unchanged edits retained from the failed attempt."""
    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    source_root = Path(source_path).parent.as_posix()
    task["allowed_write_roots"] = [source_root]
    task_path.write_text(json.dumps(task), encoding="utf-8")
    helper_path = f"{source_root}/OptionalHelper.java"

    class FakeConversation:
        def __init__(self, sandbox: Path, attempt: int) -> None:
            self.sandbox = sandbox
            self.attempt = attempt

        def send_message(self, _message: str) -> None:
            pass

        def run(self) -> None:
            if self.attempt == 1:
                (self.sandbox / source_path).write_text(
                    "class OrderService { OptionalHelper helper; }",
                    encoding="utf-8",
                )
                (self.sandbox / helper_path).write_text(
                    "class OptionalHelper {}",
                    encoding="utf-8",
                )

        def close(self) -> None:
            pass

    created: list[FakeConversation] = []

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        conversation = FakeConversation(sandbox, len(created) + 1)
        created.append(conversation)
        return conversation, SimpleNamespace(_tools={})

    failed_check = WorkspaceVerificationError(
        {"command": ["gradlew", "test"], "exitCode": 1, "stderr": "first attempt"}
    )
    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                api_key="approved-key",
                provider="openrouter",
                model="openai/gpt-4o-mini",
                display_name=lambda: "OpenRouter",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            side_effect=[
                failed_check,
                {"command": ["gradlew", "test"], "exitCode": 0},
            ],
        ),
    ):
        with pytest.raises(WorkspaceVerificationError):
            execute_openhands_task(run, task_id)
        assert source.read_text(encoding="utf-8") == "class OrderService {}"
        result = execute_openhands_task(run, task_id)

    assert result["status"] == "SUCCEEDED"
    assert source.read_text(encoding="utf-8") == (
        "class OrderService { OptionalHelper helper; }"
    )
    assert (run / helper_path).read_text(encoding="utf-8") == "class OptionalHelper {}"
    assert {source_path, helper_path} <= set(result["changedFiles"])


def test_verified_candidate_deletion_is_promoted(tmp_path: Path) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    source_root = Path(source_path).parent.as_posix()
    obsolete_path = f"{source_root}/ObsoleteHelper.java"
    obsolete = run / obsolete_path
    obsolete.write_text("class ObsoleteHelper {}", encoding="utf-8")
    task["allowed_write_roots"] = [source_root]
    task_path.write_text(json.dumps(task), encoding="utf-8")

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox

        def send_message(self, _message: str) -> None:
            pass

        def run(self) -> None:
            (self.sandbox / obsolete_path).unlink()

        def close(self) -> None:
            pass

    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                provider="openrouter",
                model="openai/gpt-4o-mini",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=lambda sandbox, *_args, **_kwargs: (
                FakeConversation(sandbox),
                SimpleNamespace(_tools={}),
            ),
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            return_value={"command": ["gradle", "test"], "exitCode": 0},
        ),
    ):
        result = execute_openhands_task(run, task_id)

    assert result["status"] == "SUCCEEDED"
    assert obsolete_path in result["changedFiles"]
    assert obsolete.exists() is False


def test_terminal_openhands_failure_is_persisted_without_a_fresh_conversation(
    tmp_path: Path,
) -> None:
    """OpenHands terminal state is checkpointed without an EasyDep restart heuristic."""
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)

    class FakeConversation:
        def __init__(self, sandbox: Path, number: int) -> None:
            self.sandbox = sandbox
            self.number = number
            self.messages: list[str] = []
            self.run_count = 0
            self.close_count = 0
            self.state = SimpleNamespace(
                execution_status=ConversationExecutionStatus.IDLE
            )

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            if self.number == 1:
                # 실제 OpenHands SDK는 한도 도달을 예외로 던지지 않고 ERROR 상태로
                # 기록한 뒤 run()을 반환한다.
                self.state.execution_status = ConversationExecutionStatus.ERROR
                return
            (self.sandbox / source_path).write_text(
                "class OrderService { int repairedInFreshContext; }",
                encoding="utf-8",
            )
            self.state.execution_status = ConversationExecutionStatus.FINISHED

        def close(self) -> None:
            self.close_count += 1

    created: list[FakeConversation] = []

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        conversation = FakeConversation(sandbox, len(created) + 1)
        created.append(conversation)
        return conversation, SimpleNamespace(_tools={})

    failure = WorkspaceVerificationError(
        {
            "command": ["gradlew", "compileJava"],
            "exitCode": 1,
            "stderr": f"{source_path}: cannot find symbol",
        }
    )
    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                api_key="approved-key",
                provider="openrouter",
                model="openai/gpt-4o-mini",
                display_name=lambda: "OpenRouter",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            side_effect=[
                failure,
                {"command": ["gradlew", "compileJava"], "exitCode": 0},
            ],
        ) as verify,
        pytest.raises(WorkspaceVerificationError, match="did not finish"),
    ):
        execute_openhands_task(run, task_id)

    assert len(created) == 1
    assert created[0].run_count == 1
    assert created[0].close_count == 1
    assert len(created[0].messages) == 1
    verify.assert_not_called()
    assert source.read_text(encoding="utf-8") == "class OrderService {}"
    failure_result = json.loads(
        (run / f"reports/agent-executions/{task_id}.result.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure_result["status"] == "FAILED"
    assert failure_result["verificationEvidence"]["command"] == [
        "openhands",
        "conversation",
    ]


def test_typed_stuck_state_resumes_once_in_the_same_owner_conversation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "backend-implementation",
            "owner": "backend",
            "allowed_write_roots": [Path(source_path).parent.as_posix()],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    source.write_text("// EASYDEP-IMPLEMENT: pending\n", encoding="utf-8")
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox
            self.messages: list[str] = []
            self.run_count = 0
            self.state = SimpleNamespace(
                execution_status=ConversationExecutionStatus.IDLE
            )

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            if self.run_count == 1:
                self.state.execution_status = ConversationExecutionStatus.STUCK
                return
            (self.sandbox / source_path).write_text(
                "class OrderService { int completedAfterStuck; }",
                encoding="utf-8",
            )
            self.state.execution_status = ConversationExecutionStatus.FINISHED

        def close(self) -> None:
            pass

    conversation: FakeConversation | None = None

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        nonlocal conversation
        conversation = FakeConversation(sandbox)
        return conversation, SimpleNamespace(_tools={})

    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                provider="openrouter",
                model="openai/gpt-4o-mini",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch("app.implementation.agents.runtime._sync_owner_task_tracker"),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            return_value={"command": ["gradle", "test"], "exitCode": 0},
        ),
    ):
        result = execute_openhands_task(run, task_id)

    assert conversation is not None
    assert conversation.run_count == 2
    assert len(conversation.messages) == 3
    assert "canonical" in conversation.messages[-2]
    assert "Continue the current owner item" in conversation.messages[-1]
    assert result["stuckRecoveryUsed"] is True
    assert result["executionStatus"] == "finished"
    assert "completedAfterStuck" in source.read_text(encoding="utf-8")


def test_openhands_conversation_enables_stuck_detection_and_condensation(
    tmp_path: Path,
) -> None:
    """공식 SDK의 반복 감지와 context condenser를 기본 실행에 연결한다."""
    source = tmp_path / "OrderService.java"
    source.write_text("class OrderService {}", encoding="utf-8")
    llm = {
        "temperature": 0.2,
        "maxOutputTokens": 1024,
    }

    connection = LlmConnection(
        provider="openrouter",
        api_key="approved-key",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    model = connection.litellm_model()
    from openhands.sdk.llm.utils.model_features import SEND_REASONING_CONTENT_MODELS

    reasoning_models_before = tuple(SEND_REASONING_CONTENT_MODELS)
    conversation, agent = create_openhands_conversation(
        tmp_path,
        connection,
        llm,
    )
    try:
        conversation.send_message("Initialize tools without running the model.")
        assert conversation.stuck_detector is not None
        assert agent.condenser.__class__.__name__ == "LLMSummarizingCondenser"
        assert agent.condenser.max_size == 80
        assert agent.condenser.keep_first == 4
        assert agent.llm.usage_id == "implementation_agent"
        assert set(conversation.llm_registry.list_usage_ids()) == {
            "implementation_agent",
            "implementation_condenser",
        }
        assert (
            conversation.conversation_stats.usage_to_metrics["implementation_agent"]
            is agent.llm.metrics
        )
        assert (
            conversation.conversation_stats.usage_to_metrics["implementation_condenser"]
            is agent.condenser.llm.metrics
        )
        agent.llm.metrics.add_token_usage(
            prompt_tokens=101,
            completion_tokens=23,
            cache_read_tokens=17,
            cache_write_tokens=5,
            reasoning_tokens=11,
            context_window=131072,
            response_id="provider-response",
        )
        usage = conversation.conversation_stats.model_dump(
            mode="json", context={"use_snapshot": True}
        )["usage_to_metrics"]["implementation_agent"]["accumulated_token_usage"]
        assert usage == {
            "model": model,
            "prompt_tokens": 101,
            "completion_tokens": 23,
            "cache_read_tokens": 17,
            "cache_write_tokens": 5,
            "reasoning_tokens": 11,
            "context_window": 131072,
            "per_turn_token": 124,
            "response_id": "",
        }
        # OpenHands의 공개 LLM 설정이 중앙 연결의 모델·URL·key를 그대로 사용한다.
        # OpenRouter 경로에는 NVIDIA 전용 extra body를 섞지 않는다.
        assert agent.llm.model == model
        assert agent.llm.base_url == "https://openrouter.ai/api/v1"
        assert agent.llm.api_key.get_secret_value() == "approved-key"
        assert agent.llm.litellm_extra_body == {}
        assert tuple(SEND_REASONING_CONTENT_MODELS) == reasoning_models_before
        assert "file_editor" in agent._tools
        assert "restricted_file_editor" not in agent._tools
        assert "grep" in agent._tools
        from openhands.tools.grep import GrepAction

        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        observation = agent._tools["grep"].executor(
            GrepAction(pattern="OrderService", path=str(outside.resolve()))
        )
        assert observation.is_error is True
        assert "outside the assigned workspace" in observation.text
        missing_observation = agent._tools["grep"].executor(
            GrepAction(pattern="RequiredService", path=str(tmp_path.resolve()))
        )
        assert "contracted output" not in missing_observation.text
    finally:
        conversation.close()


def test_provider_tool_validation_uses_openhands_native_recovery_error(
    tmp_path: Path,
) -> None:
    """A provider-prevalidated tool call reaches Agent.step's existing handler."""

    from openhands.sdk import LLM
    from openhands.sdk.llm.exceptions import (
        FunctionCallValidationError,
        LLMBadRequestError,
    )

    provider_error = LLMBadRequestError(
        "litellm.BadRequestError: OpenAIException - Error code: 400 - "
        "{'errors': [{'message': \"Model execution failed (User Input Error): "
        "Tool call validation failed: parameters for tool grep did not match schema: "
        "errors: [missing properties: 'pattern']\", 'code': 7003}], "
        "'success': False, 'result': {}, 'messages': []}"
    )
    generic_bad_request = LLMBadRequestError(
        "litellm.BadRequestError: OpenAIException - Error code: 400 - "
        "{'errors': [{'message': 'Invalid model parameter', 'code': 7001}]}"
    )
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
    )
    try:
        with (
            patch.object(LLM, "_handle_error", side_effect=provider_error),
            pytest.raises(FunctionCallValidationError, match="missing properties"),
        ):
            agent.llm._handle_error(RuntimeError("provider failure"), lambda _: None)

        with (
            patch.object(LLM, "_handle_error", side_effect=generic_bad_request),
            pytest.raises(LLMBadRequestError, match="Invalid model parameter"),
        ):
            agent.llm._handle_error(RuntimeError("provider failure"), lambda _: None)
    finally:
        conversation.close()


def test_canonical_editor_uses_standard_action_schema(tmp_path: Path) -> None:
    """The model sees OpenHands' standard file_editor schema, not aliases."""
    source = tmp_path / "Offering.java"
    source.write_text(
        "// 수강 편성 정보를 나타내는 엔티티다.\nclass Offering {}\n",
        encoding="utf-8",
    )
    llm = {
        "temperature": 0.2,
        "maxOutputTokens": 1024,
    }
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        llm,
    )
    try:
        from openhands.tools.file_editor import FileEditorAction

        conversation.send_message("Initialize tools without running the model.")
        assert "file_editor" in agent._tools
        assert "restricted_file_editor" not in agent._tools
        observation = agent._tools["file_editor"].executor(
            FileEditorAction(command="view", path=str(source.resolve()))
        )
        with pytest.raises(Exception):
            agent._tools["file_editor"].action_type.model_validate(
                {"command": "view", "file_path": str(source.resolve())}
            )
    finally:
        conversation.close()

    assert observation.is_error is False
    assert "수강 편성 정보" in str(observation)


def test_live_owner_terminal_requires_the_isolated_runner_shell(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("EASYDEP_OWNER_TERMINAL_SHELL", raising=False)

    with pytest.raises(RuntimeError, match="EASYDEP_OWNER_TERMINAL_SHELL"):
        create_openhands_conversation(
            tmp_path,
            LlmConnection(
                provider="openrouter",
                api_key="validation-only-key",
                base_url="https://openrouter.ai/api/v1",
                model="openai/gpt-oss-20b",
                litellm_provider="openrouter",
            ),
            {"temperature": 0.2, "maxOutputTokens": 1024},
            editable_roots=[str(tmp_path.resolve())],
            native_owner_tools=True,
            enable_native_terminal=True,
        )


def test_owner_conversation_reopens_the_same_openhands_checkpoint(tmp_path: Path) -> None:
    persistence = tmp_path / "conversations"
    conversation_id = uuid.uuid4()
    connection = LlmConnection(
        provider="openrouter",
        api_key="validation-only-key",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    options = {
        "editable_roots": [str(tmp_path.resolve())],
        "native_owner_tools": True,
        "enable_native_terminal": False,
        "persistence_dir": persistence,
        "conversation_id": conversation_id,
    }
    first, _agent = create_openhands_conversation(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        **options,
    )
    first.send_message("Keep this owner checkpoint.")
    first.close()

    resumed, _agent = create_openhands_conversation(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        **options,
    )
    try:
        assert resumed.state.id == conversation_id
        assert (persistence / conversation_id.hex / "base_state.json").is_file()
        assert not _owner_message_required(
            resumed=True,
            conversation=resumed,
            prompt="Keep this owner checkpoint.",
        )
        assert _owner_message_required(
            resumed=True,
            conversation=resumed,
            prompt="A newly assigned repair message.",
        )
        resumed.send_message("A newly assigned repair message.")
        _sync_owner_task_tracker(
            _agent,
            [
                {
                    "title": "Resume the current implementation item",
                    "notes": "The complete list is restored before the next run.",
                    "status": "in_progress",
                }
            ],
        )
        from openhands.tools.task_tracker import TaskTrackerAction

        observation = _agent._tools["task_tracker"].executor(
            TaskTrackerAction(command="view")
        )
        assert observation.task_list[0].status == "in_progress"
    finally:
        resumed.close()


def test_owner_conversation_uses_native_task_tracker(tmp_path: Path) -> None:
    persistence = tmp_path / "backend-conversation"
    conversation_id = uuid.uuid4()
    source = tmp_path / "application/src/main/java/example/Service.java"
    source.parent.mkdir(parents=True)
    source.write_text("// EASYDEP-IMPLEMENT: pending\n", encoding="utf-8")
    _seed_owner_task_tracker(
        persistence,
        conversation_id,
        tmp_path,
        tmp_path,
        [source.relative_to(tmp_path).as_posix()],
    )
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
        editable_roots=[str(tmp_path.resolve())],
        native_owner_tools=True,
        enable_native_terminal=False,
        persistence_dir=persistence,
        conversation_id=conversation_id,
    )
    try:
        conversation.send_message("Track this multi-component implementation.")
        assert "task_tracker" in agent._tools

        from openhands.tools.task_tracker import TaskTrackerAction

        observation = agent._tools["task_tracker"].executor(
            TaskTrackerAction(command="view")
        )
        assert observation.is_error is False
        assert len(observation.task_list) == 2
        assert source.relative_to(tmp_path).as_posix() in observation.task_list[0].title
        assert observation.task_list[0].status == "in_progress"
        assert (persistence / conversation_id.hex / "TASKS.json").is_file()
    finally:
        conversation.close()


def test_owner_task_tracker_is_seeded_from_every_marker_file(tmp_path: Path) -> None:
    sandbox = tmp_path / "workspace"
    first = sandbox / "application/src/main/java/example/application/impl/AService.java"
    second = sandbox / "application/src/main/java/example/adapter/in/AController.java"
    test = sandbox / "application/src/test/java/example/BackendApplicationTest.java"
    unrelated = sandbox / "application/src/main/java/example/Unrelated.java"
    for path, content in (
        (first, "// EASYDEP-IMPLEMENT: one\n// EASYDEP-IMPLEMENT: two\n"),
        (second, "// EASYDEP_CONTROLLER_BODY_REQUIRED:GET:/items\n"),
        (test, "// EASYDEP-IMPLEMENT: assertions\n"),
        (unrelated, "class Unrelated {}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    persistence = tmp_path / "conversations"
    conversation_id = uuid.uuid4()
    task_file = _seed_owner_task_tracker(
        persistence,
        conversation_id,
        sandbox,
        sandbox,
        [
            first.relative_to(sandbox).as_posix(),
            second.relative_to(sandbox).as_posix(),
            test.relative_to(sandbox).as_posix(),
            unrelated.relative_to(sandbox).as_posix(),
        ],
    )

    assert task_file == persistence / conversation_id.hex / "TASKS.json"
    tasks = json.loads(task_file.read_text(encoding="utf-8"))
    assert [item["status"] for item in tasks] == [
        "in_progress",
        "todo",
        "todo",
        "todo",
    ]
    assert first.relative_to(sandbox).as_posix() in tasks[0]["title"]
    assert "2 original marker occurrence(s); 2 remain" in tasks[0]["notes"]
    assert second.relative_to(sandbox).as_posix() in tasks[1]["title"]
    assert test.relative_to(sandbox).as_posix() in tasks[2]["title"]
    assert "canonical owner verification" in tasks[3]["title"]
    assert all(unrelated.name not in item["title"] for item in tasks)


def test_owner_task_tracker_restores_full_plan_from_workspace_progress(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "contract"
    sandbox = tmp_path / "workspace"
    relative = Path("application/src/main/java/example/Service.java")
    source = contract / relative
    completed = sandbox / relative
    source.parent.mkdir(parents=True)
    completed.parent.mkdir(parents=True)
    source.write_text("// EASYDEP-IMPLEMENT: pending\n", encoding="utf-8")
    completed.write_text("class Service {}\n", encoding="utf-8")
    persistence = tmp_path / "conversations"
    conversation_id = uuid.uuid4()
    task_file = persistence / conversation_id.hex / "TASKS.json"
    task_file.parent.mkdir(parents=True)
    existing = [{"title": "Partial model plan", "notes": "stale", "status": "todo"}]
    task_file.write_text(json.dumps(existing), encoding="utf-8")

    result = _seed_owner_task_tracker(
        persistence,
        conversation_id,
        contract,
        sandbox,
        [relative.as_posix()],
    )

    assert result == task_file
    tasks = json.loads(task_file.read_text(encoding="utf-8"))
    assert [item["status"] for item in tasks] == ["done", "in_progress"]
    assert relative.as_posix() in tasks[0]["title"]
    assert "canonical owner verification" in tasks[1]["title"]
    assert all(item["title"] != "Partial model plan" for item in tasks)


def test_owner_runs_marker_files_as_focused_turns_before_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, task_id, first_path, _first = _write_minimal_agent_task(tmp_path)
    second_path = "application/src/main/java/com/example/application/OtherService.java"
    for relative in (first_path, second_path):
        target = run / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// EASYDEP-IMPLEMENT: pending\n", encoding="utf-8")
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "backend-implementation",
            "owner": "backend",
            "allowed_write_roots": [
                "application/src/main/java/com/example/application"
            ],
            "required_output_paths": [first_path, second_path],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox
            self.messages: list[str] = []
            self.run_count = 0

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            current = self.messages[-1]
            for relative in (first_path, second_path):
                if f"`{relative}`" in current:
                    (self.sandbox / relative).write_text(
                        f"class {Path(relative).stem} {{}}\n",
                        encoding="utf-8",
                    )

        def close(self) -> None:
            pass

    conversations: list[FakeConversation] = []
    terminal_flags: list[bool] = []
    tracker_updates: list[list[dict[str, object]]] = []

    def create_conversation(sandbox: Path, *_args, **kwargs):
        conversation = FakeConversation(sandbox)
        conversations.append(conversation)
        terminal_flags.append(bool(kwargs["enable_native_terminal"]))
        return conversation, SimpleNamespace(_tools={})

    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            return_value=SimpleNamespace(
                provider="openrouter",
                model="openai/gpt-4o-mini",
                litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
            ),
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch(
            "app.implementation.agents.runtime._sync_owner_task_tracker",
            side_effect=lambda _agent, tasks: tracker_updates.append(tasks),
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            return_value={"command": ["gradle", "test"], "exitCode": 0},
        ) as verify,
    ):
        result = execute_openhands_task(run, task_id)

    assert terminal_flags == [False]
    assert [conversation.run_count for conversation in conversations] == [2]
    assert first_path in conversations[0].messages[1]
    assert second_path in conversations[0].messages[2]
    assert [item["status"] for item in tracker_updates[0]] == [
        "in_progress",
        "todo",
        "todo",
    ]
    assert [item["status"] for item in tracker_updates[-1]] == [
        "done",
        "done",
        "in_progress",
    ]
    verify.assert_called_once()
    assert result["status"] == "SUCCEEDED"
    assert "EASYDEP-IMPLEMENT" not in (run / first_path).read_text(encoding="utf-8")
    assert "EASYDEP-IMPLEMENT" not in (run / second_path).read_text(encoding="utf-8")


def test_owner_retry_continues_after_a_completed_unverified_turn(tmp_path: Path) -> None:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message, TextContent

    prompt = "Keep working on the owner task."
    user_event = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=prompt)]),
    )
    agent_event = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant",
            content=[TextContent(text="I could not finish the verification.")],
        ),
    )
    conversation = SimpleNamespace(
        state=SimpleNamespace(events=[user_event, agent_event])
    )

    assert not _owner_message_required(
        resumed=True,
        conversation=conversation,
        prompt=prompt,
    )
    assert _owner_continuation_required(
        resumed=True,
        conversation=conversation,
        prompt=prompt,
    )


def test_owner_retry_sends_task_message_when_checkpoint_has_no_user_event(
    tmp_path: Path,
) -> None:
    conversation, _agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
        editable_roots=[str(tmp_path.resolve())],
        native_owner_tools=True,
        enable_native_terminal=False,
    )
    try:
        assert _owner_message_required(
            resumed=True,
            conversation=conversation,
            prompt="Task message not yet persisted.",
        )
    finally:
        conversation.close()


def test_owner_repair_paths_do_not_expand_write_scope() -> None:
    task = {
        "task_type": "backend-implementation",
        "allowed_write_paths": ["application/src/main/java/example/Owned.java"],
        "allowed_write_roots": ["application/src/test/java/example"],
        "immutable_paths": ["application/src/main/java/example/api"],
    }
    repair = {
        "repairPaths": [
            "application/src/main/java/other/Unowned.java",
            "application/src/main/java/example/api/FrozenApi.java",
        ]
    }

    assert _task_execution_scope(task, repair) == (
        ["application/src/main/java/example/Owned.java"],
        ["application/src/test/java/example"],
        ["application/src/main/java/example/api"],
    )


def test_owner_workspace_guidance_states_runner_facts_without_error_history(
    tmp_path: Path,
) -> None:
    guidance = _owner_workspace_guidance(
        "backend-implementation",
        tmp_path,
        ["application/src/main/java/com/example"],
    )

    assert f"`{tmp_path.resolve()}`" in guidance
    assert "Backend project root: `application`" in guidance
    assert f"cd {tmp_path.resolve() / 'application'} && gradle test --build-cache" in guidance
    assert "SPRING_PROFILES_ACTIVE=test" in guidance
    assert "run the canonical verification once" in guidance
    assert "Do not disable tests or alter test reporting" in guidance
    assert "permission denied" not in guidance.casefold()
    assert OWNER_TURN_ITERATIONS < 500


def test_repeated_typed_no_action_responses_stop_at_openhands_threshold() -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus
    from openhands.sdk.conversation.types import StuckDetectionThresholds
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message, TextContent

    guard = NoActionResponseGuard()
    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    guard.bind(conversation)
    empty = MessageEvent(
        source="agent",
        llm_message=Message(role="assistant", content=[]),
    )
    corrective_user_message = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text="SDK corrective feedback")],
        ),
    )

    for _ in range(StuckDetectionThresholds().monologue):
        guard(empty)
        guard(corrective_user_message)

    assert guard.triggered is True
    assert guard.max_consecutive_count == StuckDetectionThresholds().monologue
    assert conversation.state.execution_status is ConversationExecutionStatus.STUCK
    assert _conversation_terminal_failure(conversation) is True


def test_scoped_editor_applies_workspace_and_contract_guards(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Unlisted source stays editable while workspace escape and contracts stay blocked."""
    source_root = tmp_path / "application/src/main/java/example"
    generated = source_root / "generated"
    generated.mkdir(parents=True)
    immutable = generated / "OrdersApi.java"
    immutable.write_text("interface OrdersApi {}", encoding="utf-8")
    exact_file = tmp_path / "application/src/test/java/example/ScenarioTest.java"
    exact_file.parent.mkdir(parents=True)
    granted: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        "app.implementation.agents.runtime.grant_owner_file_access",
        lambda path, allowed: granted.append((path, allowed)),
    )
    connection = LlmConnection(
        provider="openrouter",
        api_key="validation-only-key",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    conversation, agent = create_openhands_conversation(
        tmp_path,
        connection,
        {"temperature": 0.2, "maxOutputTokens": 1024},
        editable_files=[str(exact_file.resolve())],
        editable_roots=[str(source_root.resolve())],
        immutable_paths=[str(generated.resolve())],
    )
    try:
        from openhands.tools.file_editor import FileEditorAction

        conversation.send_message("Initialize tools without running the model.")
        editor = agent._tools["file_editor"].executor
        related = source_root / "RelatedService.java"
        created = editor(
            FileEditorAction(
                command="create",
                path=str(related.resolve()),
                file_text="class RelatedService {}",
            )
        )
        created_exact = editor(
            FileEditorAction(
                command="create",
                path=str(exact_file.resolve()),
                file_text="class ScenarioTest {}",
            )
        )
        blocked_sibling = editor(
            FileEditorAction(
                command="create",
                path=str((exact_file.parent / "SiblingTest.java").resolve()),
                file_text="class SiblingTest {}",
            )
        )
        blocked_contract = editor(
            FileEditorAction(
                command="str_replace",
                path=str(immutable.resolve()),
                old_str="interface OrdersApi {}",
                new_str="interface OrdersApi { void changed(); }",
            )
        )
        outside = tmp_path.parent / "outside.java"
        outside.write_text("class Outside {}", encoding="utf-8")
        blocked_escape = editor(
            FileEditorAction(command="view", path=str(outside.resolve()))
        )
    finally:
        conversation.close()

    assert created.is_error is False
    assert related.is_file()
    assert created_exact.is_error is False
    assert exact_file.is_file()
    assert [path for path, _allowed in granted] == [related.resolve(), exact_file.resolve()]
    assert granted[0][1] == tmp_path.resolve()
    assert granted[1][1] == tmp_path.resolve()
    assert blocked_sibling.is_error is True
    assert blocked_contract.is_error is True
    assert immutable.read_text(encoding="utf-8") == "interface OrdersApi {}"
    assert blocked_escape.is_error is True


def test_owner_editor_allows_candidate_changes_but_blocks_workspace_escape(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "application/src/main/java/example"
    immutable = source_root / "api/OrdersApi.java"
    immutable.parent.mkdir(parents=True)
    immutable.write_text("interface OrdersApi {}", encoding="utf-8")
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        {"temperature": 0.2, "maxOutputTokens": 1024},
        editable_roots=[str(source_root.resolve())],
        immutable_paths=[str(immutable.parent.resolve())],
        native_owner_tools=True,
        enable_native_terminal=False,
    )
    try:
        from openhands.tools.file_editor import FileEditorAction

        conversation.send_message("Initialize tools without running the model.")
        editor = agent._tools["file_editor"].executor
        outside_owner_root = tmp_path / "application/frontend/src/App.tsx"
        outside_owner_root.parent.mkdir(parents=True)
        changed_candidate = editor(
            FileEditorAction(
                command="create",
                path=str(outside_owner_root.resolve()),
                file_text="export default function App() { return null; }",
            )
        )
        changed_contract = editor(
            FileEditorAction(
                command="str_replace",
                path=str(immutable.resolve()),
                old_str="interface OrdersApi {}",
                new_str="interface OrdersApi { void changed(); }",
            )
        )
        outside = tmp_path.parent / f"{tmp_path.name}-outside.java"
        escaped = editor(
            FileEditorAction(
                command="create",
                path=str(outside.resolve()),
                file_text="class Outside {}",
            )
        )
    finally:
        conversation.close()

    assert editor.enforce_write_scope is False
    assert changed_candidate.is_error is False
    assert changed_contract.is_error is False
    assert escaped.is_error is True
    assert outside.exists() is False


def test_canonical_editor_has_no_build_directory_heuristic(tmp_path: Path) -> None:
    """The adapter does not add content-selection heuristics to OpenHands tools."""
    report = tmp_path / "application/build/reports/problems/problems-report.html"
    report.parent.mkdir(parents=True)
    report.write_text("x" * 100_000, encoding="utf-8")
    source = tmp_path / "application/src/main/java/example/Service.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Service {}", encoding="utf-8")
    llm = {
        "temperature": 0.2,
        "maxOutputTokens": 1024,
    }
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="openrouter",
            api_key="validation-only-key",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-oss-20b",
            litellm_provider="openrouter",
        ),
        llm,
    )
    try:
        from openhands.tools.file_editor import FileEditorAction

        conversation.send_message("Initialize tools without running the model.")
        observation = agent._tools["file_editor"].executor(
            FileEditorAction(command="view", path=str(report.resolve()))
        )
    finally:
        conversation.close()

    assert "run_task_check" not in str(observation)


def test_completion_audit_rejects_unfinished_generated_bodies(
    tmp_path: Path,
) -> None:
    """파일이 있어도 생성기가 남긴 미완성 표식은 구현 완료로 보지 않는다."""
    run = tmp_path / "run"
    reports = run / "reports"
    controller = run / "application/src/main/java/example/OrdersApiController.java"
    context = reports / "implementation-tasks/orders.context.json"
    controller.parent.mkdir(parents=True)
    context.parent.mkdir(parents=True)
    controller.write_text(
        '// EASYDEP-IMPLEMENT: complete the generated body\n'
        'throw new UnsupportedOperationException("EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/orders");',
        encoding="utf-8",
    )
    context.write_text(
        json.dumps(
            {"controllerPaths": ["application/src/main/java/example/OrdersApiController.java"]}
        ),
        encoding="utf-8",
    )
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-orders",
                        "task_type": "use-case",
                        "context_file": "reports/implementation-tasks/orders.context.json",
                        "required_output_paths": [
                            "application/src/main/java/example/OrdersApiController.java"
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = audit_run_completion(run)

    assert report["status"] == "INCOMPLETE"
    assert "Unimplemented Controller body remains" in report["backlog"][0]["evidence"][0]
    assert any(
        "Unresolved implementation marker remains" in item
        for item in report["backlog"][0]["evidence"]
    )


@pytest.mark.parametrize(
    ("old_status", "result_prompt"),
    [("SUCCEEDED", "prompt-v1"), ("RUNNING", "prompt-before-replay")],
)
def test_resume_keeps_previous_success_after_shared_file_changes(
    tmp_path: Path, old_status: str, result_prompt: str
) -> None:
    """공유 파일 변경이나 중단된 재실행이 있어도 이전 성공 결과를 재사용한다."""
    reports = tmp_path / "reports"
    executions = reports / "agent-executions"
    executions.mkdir(parents=True)
    shared = tmp_path / "application/src/main/java/com/example/SharedAdapter.java"
    shared.parent.mkdir(parents=True)
    shared.write_text("class SharedAdapter { void laterChange() {} }", encoding="utf-8")
    task_id = "implement-backend-application"
    relative = shared.relative_to(tmp_path).as_posix()
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": task_id,
                        "task_type": "backend-implementation",
                        "owner": "backend",
                        "prompt_sha256": "prompt-v1",
                        "required_output_paths": [relative],
                        "allowed_write_paths": [relative],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": task_id,
                        "status": old_status,
                        "attempts": 1,
                        "outputHashes": {relative: "hash-before-later-task"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (executions / f"{task_id}.result.json").write_text(
        json.dumps({"status": "SUCCEEDED", "promptSha256": result_prompt}),
        encoding="utf-8",
    )

    state = reconcile_workflow_state(tmp_path)

    assert state["tasks"][0]["status"] == "SUCCEEDED"
    assert state["tasks"][0]["attempts"] == 1


def test_retry_hides_previous_error_as_soon_as_task_is_running(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-use-case",
                        "allowed_write_paths": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    task = {
        "task_id": "implement-use-case",
        "status": "FAILED",
        "attempts": 1,
        "lastError": "failure from the previous attempt",
        "phase": "use-cases",
    }
    state = {"tasks": [task]}
    observed: dict[str, object] = {}

    def execute(_run_root: Path, _task_id: str) -> dict[str, object]:
        live = json.loads(
            (tmp_path / "reports/workflow-state.json").read_text(encoding="utf-8")
        )
        observed.update(live["tasks"][0])
        return {"status": "SUCCEEDED"}

    failures = _execute_task_batch(
        tmp_path,
        state,
        [task],
        execute,
        max_workers=1,
    )

    assert failures == []
    assert observed["status"] == "RUNNING"
    assert observed["attempts"] == 2
    assert observed["lastError"] is None


def test_planned_manifest_uses_work_units_and_scopes_repairs_to_contracts(
    tmp_path: Path,
) -> None:
    """공개 계획 결과가 작업 종류와 각 작업의 편집 경계를 보존한다."""
    design = tmp_path / "design"
    design.mkdir()
    bce = design / "class.puml"
    bce.write_text(
        """class OrderBoundary <<Boundary>> {
  + submit(request: OrderRequest): Receipt
}
class OrderControl <<Control>> {
  + place(request: OrderRequest): void
}
class CancelControl <<Control>> {}
class Order <<Entity>> { - id: UUID }
""",
        encoding="utf-8",
    )
    class_model_payload = typed_class_model_payload()
    class_model_payload["Classes"].extend(
        [
            {
                "className": "Order",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "identifier": ["id"],
                "fields": ["id : UUID"],
                "operations": [],
            },
            {
                "className": "CancelControl",
                "stereotype": "Control",
                "use_case_ids": ["UC2"],
                "operations": [],
            },
            {
                # 유스케이스나 operation이 없는 보조 Entity는 결정론적 골격만으로
                # 충분하다. 별도의 모호한 OpenHands 작업을 만들면 안 된다.
                "className": "OrderWindow",
                "stereotype": "Entity",
                "use_case_ids": [],
                "identifier": [],
                "fields": ["opensAt : LocalDateTime", "closesAt : LocalDateTime"],
                "operations": [],
            },
        ]
    )
    class_model = design / "class-model.json"
    class_model.write_text(json.dumps(class_model_payload), encoding="utf-8")
    erd_logical_model = design / "erd-logical-model.json"
    erd_logical_model.write_text(
        json.dumps(build_logical_model(class_model_payload)),
        encoding="utf-8",
    )
    sequence_model = design / "sequence-model.json"
    sequence_model.write_text(json.dumps(typed_sequence_model_payload()), encoding="utf-8")
    sequence = design / "sequence.puml"
    sequence.write_text("OrderBoundary -> OrderControl : place(request)\n", encoding="utf-8")
    requirements = design / "requirements.json"
    requirements.write_text(
        json.dumps(
            [
                {
                    "id": "FR-ORDER",
                    "text": "The customer can place an order.",
                    "type": "FR",
                    "use_case_ids": ["UC1"],
                    "repair_history": {"marker": "INTERNAL-REPAIR-MARKER"},
                },
                {
                    "id": "FR-CANCEL",
                    "text": "The customer can cancel an order.",
                    "type": "FR",
                    "use_case_ids": ["UC2"],
                },
            ]
        ),
        encoding="utf-8",
    )
    use_case_specs = design / "use-case-specs.json"
    use_case_specs.write_text(
        json.dumps(
            [
                {
                    "id": "UC1",
                    "use_case_id": "UC1",
                    "name": "Place order",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "The customer places an order."}
                    ],
                    "repair_iters": 7,
                    "repair_history": {"marker": "INTERNAL-USE-CASE-REPAIR"},
                },
                {"id": "UC2", "use_case_id": "UC2", "name": "Cancel order"},
            ]
        ),
        encoding="utf-8",
    )
    erd = design / "erd.puml"
    erd.write_text('entity "Order" as Order {\n  * id : UUID\n}\n', encoding="utf-8")
    openapi = design / "openapi.json"
    openapi.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "paths": {
                    "/orders": {
                        "post": {
                            "operationId": "placeOrder",
                            "responses": {"201": {"description": "Created"}},
                        }
                    },
                    "/orders/{id}": {
                        "delete": {
                            "operationId": "cancelOrder",
                            "responses": {"204": {"description": "Cancelled"}},
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    api_model = design / "api-model.json"
    api_model.write_text(
        json.dumps(
            {
                "Endpoints": [
                    {
                        "method": "POST",
                        "path": "/orders",
                        "operation_id": "placeOrder",
                        "use_case_ids": ["UC1"],
                        "control_binding": {"control": "OrderControl"},
                    },
                    {
                        "method": "DELETE",
                        "path": "/orders/{id}",
                        "operation_id": "cancelOrder",
                        "use_case_ids": ["UC2"],
                        "control_binding": {"control": "CancelControl"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    run = tmp_path / "run"
    package_root = run / "application/src/main/java/com/example/orders"
    (package_root / "api").mkdir(parents=True)
    (package_root / "bce").mkdir(parents=True)
    (package_root / "api/OrdersApi.java").write_text(
        "package com.example.orders.api;\n"
        'public interface OrdersApi { String PATH = "/orders"; '
        "void placeOrder(); }\n",
        encoding="utf-8",
    )
    (package_root / "api/CancelApi.java").write_text(
        "package com.example.orders.api;\n"
        'public interface CancelApi { String PATH = "/orders/{id}"; '
        "void cancelOrder(); }\n",
        encoding="utf-8",
    )
    for name in (
        "OrderBoundary",
        "OrderControl",
        "CancelControl",
        "Order",
        "OrderWindow",
    ):
        (package_root / f"bce/{name}.java").write_text(
            f"package com.example.orders.bce; public interface {name} {{}}\n",
            encoding="utf-8",
        )
    (run / "application/build.gradle").parent.mkdir(parents=True, exist_ok=True)
    (run / "application/build.gradle").write_text(
        "dependencies {\n"
        "    implementation 'org.springframework.boot:spring-boot-starter-validation'\n"
        "    testImplementation 'org.springframework.boot:spring-boot-starter-test'\n"
        "}\n",
        encoding="utf-8",
    )
    generated = run / "application/frontend/src/generated/apis"
    generated.mkdir(parents=True)
    for name in ("OrdersApi", "CancelApi"):
        (generated / f"{name}.ts").write_text(f"export class {name} {{}}\n", encoding="utf-8")
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-use-cases-stale-common",
                        "task_type": "use-case",
                        "allowed_write_paths": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="orders",
        workspace_root=tmp_path,
        inputs={
            "bceClass": bce,
            "bceModel": class_model,
            "sequence": sequence,
            "sequenceModel": sequence_model,
            "erd": erd,
            "erdBceModel": class_model,
            "erdLogicalModel": erd_logical_model,
            "openapi": openapi,
            "apiModel": api_model,
            "refinedRequirements": requirements,
            "useCaseSpec": use_case_specs,
        },
        required_inputs=[],
        base_package="com.example.orders",
        allow_assumptions=True,
        verify_compile=False,
        output_root=tmp_path / "generated-runs",
        agent_mode="plan-only",
        agent_temperature=0.0,
        agent_max_output_tokens=1000,
    )

    state = plan_workflow(run, spec)
    manifest = json.loads((run / "reports/run-manifest.json").read_text(encoding="utf-8"))
    tasks = manifest["implementation_tasks"]
    task_types = {task["task_type"] for task in tasks}
    assert task_types == {"backend-implementation", "frontend-implementation"}
    assert (
        run / "application/src/main/java/com/example/orders/persistence/entity/OrderEntity.java"
    ).is_file()
    assert (
        run
        / "application/src/main/java/com/example/orders/persistence/repository/OrderRepository.java"
    ).is_file()
    assert (run / "application/src/main/resources/db/migration/V1__initial_schema.sql").is_file()
    assert not any(
        "BcePersistenceMapper" in path.as_posix() for path in (run / "application").rglob("*.java")
    )
    for task in tasks:
        assert set(task["required_output_paths"]) <= set(task["allowed_write_paths"])

    backend = next(task for task in tasks if task["owner"] == "backend")
    frontend = next(task for task in tasks if task["owner"] == "frontend")
    assert len(tasks) == 2
    assert not any(task["task_id"] == "implement-use-cases-stale-common" for task in tasks)
    assert backend["task_id"] == "implement-backend-application"
    assert set(backend["use_case_ids"]) == {"UC1", "UC2"}
    assert frontend["depends_on"] == ["implement-backend-application"]
    assert state["nextRunnableTasks"] == ["implement-backend-application"]
    context = json.loads((run / backend["context_file"]).read_text(encoding="utf-8"))
    assert context["allowAssumptions"] is True
    assert set(context["useCaseIds"]) == {"UC1", "UC2"}
    assert set(context["requirementIds"]) == {"FR-ORDER", "FR-CANCEL"}
    assert "application/src/main/java/com/example/orders/bce/Order.java" in set(
        backend["allowed_write_paths"]
    )
    generated_api = {
        "application/src/main/java/com/example/orders/api/OrdersApi.java",
        "application/src/main/java/com/example/orders/api/CancelApi.java",
    }
    assert not set(backend["allowed_write_paths"]).intersection(generated_api)
    immutable_bce = {
        "application/src/main/java/com/example/orders/bce/OrderBoundary.java",
        "application/src/main/java/com/example/orders/bce/OrderControl.java",
        "application/src/main/java/com/example/orders/bce/CancelControl.java",
    }
    assert not set(backend["allowed_write_paths"]).intersection(immutable_bce)
    persistence_root = (
        "application/src/main/java/com/example/orders/persistence"
    )
    assert persistence_root in backend["immutable_paths"]
    assert "application/src/main/resources/db/migration" in backend["immutable_paths"]
    assert not any(
        path == persistence_root or path.startswith(persistence_root + "/")
        for path in backend["allowed_write_paths"]
    )
    assert backend["allowed_write_roots"]
    assert frontend["allowed_write_roots"] == ["application/frontend"]
    source_index = json.loads(
        (run / context["sourceIndexPath"]).read_text(encoding="utf-8")
    )
    assert source_index["hintsOnly"] is True
    assert source_index["startingSourcePaths"]
    assert source_index["methodContexts"]
    assert context["methodContextRoot"].endswith("method-context")
    assert all(
        (run / item["path"]).is_file()
        for item in source_index["methodContexts"]
    )
    method_context = json.loads(
        (run / source_index["methodContexts"][0]["path"]).read_text(encoding="utf-8")
    )
    assert method_context["refs"]
    assert method_context["designInputs"] == source_index["designInputs"]
    assert context["sourceIndexPath"] in context["readSourcePaths"]
    assert all(
        path not in context["readSourcePaths"]
        for path in source_index["startingSourcePaths"]
    )
    assert {"api:placeOrder", "api:cancelOrder"} <= set(backend["source_refs"])
    prompt = (run / backend["prompt_file"]).read_text(encoding="utf-8")
    assert "The customer can place an order." not in prompt
    assert '"call_id"' not in prompt
    assert '"control_binding"' not in prompt
    assert context["sourceIndexPath"] in prompt
    assert context["methodContextRoot"] in prompt
    assert "choose the simplest behavior consistent with the frozen contracts" in prompt
    assert "INTERNAL-REPAIR-MARKER" not in prompt
    assert "INTERNAL-USE-CASE-REPAIR" not in prompt


def test_completed_workflow_hands_full_verification_to_testing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """구현은 산출물을 완성하고 전체 build·container 검사는 실행하지 않는다."""
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: {
            "status": "COMPLETE",
            "tasks": [
                {
                    "task_id": "implement-order-use-cases",
                    "status": "SUCCEEDED",
                    "phase": "use-cases",
                }
            ],
            "phases": [{"phaseId": "use-cases", "status": "SUCCEEDED"}],
            "nextRunnableTasks": [],
        },
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_source_design_conformance",
        lambda *_args: {"status": "PASSED"},
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator._render_deployment_if_configured",
        lambda *_args: (None, None),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.build_rtm_traceability_map",
        lambda *_args: {"summary": {"missing": 0}},
    )

    result = run_workflow(
        run,
        SimpleNamespace(
            app_id="app-1",
            inputs={},
            job_type="INITIAL_IMPLEMENTATION",
        ),
        auditor=lambda _run: {"status": "COMPLETE"},
    )

    assert result["status"] == "COMPLETE"
    assert result["testingRequired"] is True
    assert (run / "application/Dockerfile").is_file()
    assert not (reports / "final-verification.json").exists()
    assert not (reports / "container-runtime-smoke.json").exists()


def test_owner_execution_boundary_preserves_checkpoint_without_source_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Iteration/stuck 중단은 코드 결함 증거가 아니므로 수리 prompt를 만들지 않는다."""
    run = tmp_path / "run"
    (run / "reports").mkdir(parents=True)
    task = {
        "task_id": "implement-backend-application",
        "task_type": "backend-implementation",
        "phase": "backend",
        "status": "PENDING",
        "attempts": 0,
    }
    initial_state = {
        "status": "READY",
        "tasks": [dict(task)],
        "phases": [{"phaseId": "backend", "status": "PENDING"}],
        "nextRunnableTasks": [task["task_id"]],
    }
    paused_state = {
        "status": "FAILED",
        "tasks": [{**task, "status": "FAILED", "attempts": 1}],
        "phases": [{"phaseId": "backend", "status": "FAILED"}],
        "nextRunnableTasks": [task["task_id"]],
    }
    plan_calls = 0

    def plan(_run: Path, _spec: object) -> dict[str, object]:
        nonlocal plan_calls
        plan_calls += 1
        return dict(initial_state if plan_calls == 1 else paused_state)

    repair_calls: list[object] = []
    monkeypatch.setattr("app.implementation.workflows.coordinator.plan_workflow", plan)
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.schedule_cross_phase_repair",
        lambda *_args, **_kwargs: repair_calls.append(_args),
    )

    def stop_at_execution_boundary(_run: Path, _task_id: str) -> dict[str, object]:
        raise OwnerConversationIncomplete(
            {
                "exitCode": 1,
                "stderr": "The owner conversation did not finish before its execution boundary.",
            }
        )

    result = run_workflow(
        run,
        SimpleNamespace(app_id="app-1", inputs={}, job_type="INITIAL_IMPLEMENTATION"),
        executor=stop_at_execution_boundary,
    )

    assert result["status"] == "FAILED"
    assert "execution boundary" in result["blockingReason"]
    assert repair_calls == []
    assert not (run / "reports/repair-plan.json").exists()


def test_feedback_revision_runs_one_full_backend_test_gate_before_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """피드백 수리는 최종 backend test를 한 번 거친 뒤에만 완료한다."""
    run = tmp_path / "feedback-run"
    (run / "reports").mkdir(parents=True)
    state = {
        "status": "READY_TO_FINALIZE",
        "tasks": [],
        "phases": [],
        "nextRunnableTasks": [],
    }
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: dict(state),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_source_design_conformance",
        lambda *_args: {"status": "PASSED"},
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator._render_deployment_if_configured",
        lambda *_args: (None, None),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.build_rtm_traceability_map",
        lambda *_args: {"summary": {"missing": 0}},
    )
    calls: list[tuple[Path, str, bool, bool]] = []

    def full_backend_gate(
        run_root: Path,
        report_name: str,
        *,
        verify_frontend: bool,
        verify_end_to_end: bool,
    ) -> dict[str, object]:
        calls.append((run_root, report_name, verify_frontend, verify_end_to_end))
        return {
            "status": "SUCCEEDED",
            "verification": {"command": ["gradlew", "test", "--build-cache"]},
        }

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_run_workspace",
        full_backend_gate,
    )

    result = run_workflow(
        run,
        SimpleNamespace(job_type="FEEDBACK_REVISION", app_id="app-1", inputs={}),
        auditor=lambda _run: {"status": "COMPLETE"},
    )

    assert result["status"] == "COMPLETE"
    assert calls == [(run.resolve(), "feedback-regression.json", False, False)]
    assert result["feedbackRegression"] == "reports/feedback-regression.json"


def test_feedback_revision_test_gate_failure_returns_to_source_feedback_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """피드백 후 전체 test 실패는 자동 apply-source-feedback 수리 상태로 돌아간다."""
    run = tmp_path / "feedback-run"
    (run / "reports").mkdir(parents=True)
    initial_state = {
        "status": "READY_TO_FINALIZE",
        "tasks": [],
        "phases": [],
        "nextRunnableTasks": [],
    }
    repaired_state = {
        "status": "READY",
        "tasks": [],
        "phases": [],
        "nextRunnableTasks": ["apply-source-feedback"],
    }
    plan_calls = 0

    def plan(_run: Path, _spec: object) -> dict[str, object]:
        nonlocal plan_calls
        plan_calls += 1
        return dict(initial_state if plan_calls == 1 else repaired_state)

    monkeypatch.setattr("app.implementation.workflows.coordinator.plan_workflow", plan)
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_source_design_conformance",
        lambda *_args: {"status": "PASSED"},
    )
    failure = WorkspaceVerificationError(
        {
            "command": ["gradlew", "test", "--build-cache"],
            "exitCode": 1,
            "stderr": "OrderServiceTest failed",
        }
    )
    gate_calls: list[tuple[Path, str]] = []

    def failed_backend_gate(run_root: Path, report_name: str, **_kwargs: object) -> None:
        gate_calls.append((run_root, report_name))
        raise failure

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_run_workspace",
        failed_backend_gate,
    )
    repair_calls: list[tuple[Path, str, dict[str, object]]] = []

    def schedule_repair(
        run_root: Path,
        task_id: str,
        evidence: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        repair_calls.append((run_root, task_id, evidence))
        return {"taskId": "apply-source-feedback"}

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.schedule_cross_phase_repair",
        schedule_repair,
    )

    result = run_workflow(
        run,
        SimpleNamespace(job_type="FEEDBACK_REVISION", app_id="app-1", inputs={}),
        auditor=lambda _run: {"status": "COMPLETE"},
    )

    assert result["status"] == "READY"
    assert result["repairPlan"] == "reports/repair-plan.json"
    assert gate_calls == [(run.resolve(), "feedback-regression.json")]
    assert repair_calls == [(run.resolve(), "apply-source-feedback", failure.evidence)]


def test_repair_uses_a_small_prompt_and_restores_the_accepted_source(
    tmp_path: Path,
) -> None:
    """자동 수리는 전체 초기 설명과 실패한 임시 코드를 다음 대화로 넘기지 않는다."""
    run = tmp_path / "run_repair_prompt"
    reports = run / "reports"
    task_dir = reports / "implementation-tasks"
    task_dir.mkdir(parents=True)
    source_path = "application/src/main/java/com/example/ApplicationConfiguration.java"
    source = run / source_path
    source.parent.mkdir(parents=True)
    source.write_text("class ApplicationConfiguration { /* accepted */ }", encoding="utf-8")
    prompt_path = task_dir / "backend.md"
    initial_prompt = "INITIAL IMPLEMENTATION CONTEXT\n" + ("all requirements\n" * 100)
    prompt_path.write_text(initial_prompt, encoding="utf-8")
    task = {
        "task_id": "implement-backend-application",
        "task_type": "backend-implementation",
        "owner": "backend",
        "prompt_file": str(prompt_path.relative_to(run)).replace("\\", "/"),
        "allowed_write_paths": [source_path],
        "allowed_write_roots": ["application/src/main/java/com/example"],
        "required_output_paths": [source_path],
        "immutable_paths": ["application/src/main/java/com/example/api"],
    }
    (task_dir / "backend.task.json").write_text(json.dumps(task), encoding="utf-8")
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )

    entry = schedule_cross_phase_repair(
        run,
        "verify-container-runtime",
        {
            "owner": "backend",
            "command": ["docker", "runtime-smoke"],
            "stderr": (
                "application/src/main/java/com/example/ApplicationConfiguration.java: "
                "HTTP 401 Unauthorized; inspect "
                "application/src/main/java/com/example/api/Contract.java and "
                "application/frontend/src/App.tsx"
            ),
        },
    )
    assert entry is not None
    assert entry["repairPaths"] == [source_path]
    assert "application/src/main/java/com/example/api/Contract.java" in entry["relatedPaths"]
    assert "application/frontend/src/App.tsx" in entry["relatedPaths"]
    execution_dir = reports / "agent-executions"
    execution_dir.mkdir()
    (execution_dir / "implement-backend-application.result.json").write_text(
        json.dumps(
            {
                "repairHistory": {
                    "attempts": [
                        {
                            "strategy_key": "verification_correction",
                            "outcome": "no_improvement",
                            "candidate_digest": "candidate-401",
                            "detail": (
                                "SecurityConfiguration.java was changed, but HTTP 401 persists"
                            ),
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repeated_entries = [entry]
    for _ in range(5):
        repeated = schedule_cross_phase_repair(
            run,
            "verify-container-runtime",
            {
                "owner": "backend",
                "command": ["docker", "runtime-smoke"],
                "stderr": (
                    "application/src/main/java/com/example/ApplicationConfiguration.java: "
                    "HTTP 401 Unauthorized; inspect "
                    "application/src/main/java/com/example/api/Contract.java and "
                    "application/frontend/src/App.tsx"
                ),
            },
        )
        assert repeated is not None
        repeated_entries.append(repeated)
    apply_repair_directives(run)

    stored_task = json.loads((task_dir / "backend.task.json").read_text(encoding="utf-8"))
    repair_prompt = (run / stored_task["repair_prompt_file"]).read_text(encoding="utf-8")
    assert prompt_path.read_text(encoding="utf-8") == initial_prompt
    assert "INITIAL IMPLEMENTATION CONTEXT" not in repair_prompt
    assert "401 Unauthorized" in repair_prompt
    assert "SecurityConfiguration.java was changed, but HTTP 401 persists" in repair_prompt
    assert "Use the terminal to reproduce the failure" in repair_prompt
    assert "State new diagnostic hypothesis 2" in repair_prompt
    assert len({item["strategy"] for item in repeated_entries}) == 6
    assert source_path in repair_prompt
    assert entry["acceptedSourceRoot"] == "application"
    assert entry["acceptedSourceDigest"]

    sandbox = prepare_agent_workspace(run, stored_task)
    (sandbox / source_path).write_text("class Broken {}", encoding="utf-8")
    extra = sandbox / "application/src/main/java/com/example/Unrelated.java"
    extra.write_text("class Unrelated {}", encoding="utf-8")
    restored = prepare_agent_workspace(run, stored_task, preserve_failed_edits=False)
    assert (restored / source_path).read_text(encoding="utf-8") == (
        "class ApplicationConfiguration { /* accepted */ }"
    )
    assert not extra.exists()
    cleanup_agent_workspace(restored)


def test_source_conformance_rejects_agent_changes_to_generated_contract(
    tmp_path: Path,
) -> None:
    """에이전트가 생성된 BCE 계약을 바꾸면 최종 검증에서 실패시킨다."""
    java = tmp_path / "application/src/main/java/com/example/demo"
    (java / "bce").mkdir(parents=True)
    (java / "application").mkdir()
    contract = java / "bce/CheckoutGateway.java"
    contract.write_text(
        "package com.example.demo.bce;\n"
        "public interface CheckoutGateway {\n"
        "    String charge(String purchaseId);\n"
        "}\n",
        encoding="utf-8",
    )
    (java / "application/CheckoutService.java").write_text(
        "package com.example.demo.application; "
        "class CheckoutServiceImpl implements CheckoutService { "
        'CheckoutGateway gateway; void run() { gateway.charge("order-1"); } }',
        encoding="utf-8",
    )
    bce = tmp_path / "class.puml"
    bce.write_text(
        "class CheckoutService <<Control>> { + run() }\n"
        "class CheckoutGateway <<Gateway>> { + charge() }\n",
        encoding="utf-8",
    )
    sequence = tmp_path / "sequence.puml"
    sequence.write_text(
        "CheckoutService -> CheckoutGateway: charge()\n",
        encoding="utf-8",
    )
    spec = SimpleNamespace(
        base_package="com.example.demo",
        inputs={"bceClass": bce, "sequence": sequence},
    )
    capture_generated_contracts(tmp_path, "com.example.demo")

    assert verify_source_design_conformance(tmp_path, spec)["status"] == "PASSED"

    contract.write_text(
        contract.read_text(encoding="utf-8").replace(
            "String charge(String purchaseId)",
            "Integer charge(String purchaseId)",
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceDesignConformanceError):
        verify_source_design_conformance(tmp_path, spec)

    report = json.loads(
        (tmp_path / "reports/source-design-conformance.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "FAILED"
    assert "GENERATED_CONTRACT_CHANGED" in {item["code"] for item in report["violations"]}


def test_entity_can_add_helpers_while_preserving_generated_public_signatures(
    tmp_path: Path,
) -> None:
    """Entity 구현용 메서드는 추가해도 설계가 정한 기존 호출 계약은 유지한다."""
    bce = tmp_path / "application/src/main/java/com/example/demo/bce"
    bce.mkdir(parents=True)
    entity = bce / "Order.java"
    entity.write_text(
        "public class Order { public String rename(String value) { return value; } }",
        encoding="utf-8",
    )
    class_model = tmp_path / "class.puml"
    class_model.write_text(
        "class Order <<Entity>> { + rename(value: string): string }", encoding="utf-8"
    )
    sequence = tmp_path / "sequence.puml"
    sequence.write_text("", encoding="utf-8")
    spec = SimpleNamespace(
        base_package="com.example.demo",
        inputs={"bceClass": class_model, "sequence": sequence},
    )
    capture_generated_contracts(tmp_path, spec.base_package)

    entity.write_text(
        entity.read_text(encoding="utf-8").replace(
            "return value",
            'return value.trim(); } public String normalized() { return "ok"',
        ),
        encoding="utf-8",
    )
    assert verify_source_design_conformance(tmp_path, spec)["status"] == "PASSED"

    entity.write_text(
        entity.read_text(encoding="utf-8").replace("String value", "Integer value"),
        encoding="utf-8",
    )
    with pytest.raises(SourceDesignConformanceError):
        verify_source_design_conformance(tmp_path, spec)


def test_legacy_cloud_spec_cannot_bypass_selected_resource_plan(tmp_path: Path) -> None:
    """이전 cloud JSON만으로는 IaC를 만들지 않는다."""
    cloud = tmp_path / "cloud.json"
    cloud.write_text(
        json.dumps(
            {
                "provider": "aws",
                "resources": [{"type": "AWS::EC2::VPC", "name": "platform"}],
            }
        ),
        encoding="utf-8",
    )
    spec = SimpleNamespace(name="orders", inputs={"cloud": cloud})
    run = tmp_path / "run"

    with pytest.raises(ValueError, match="deployment diagram bundle"):
        render_iac(run, spec)

    assert not (run / "application").exists()
