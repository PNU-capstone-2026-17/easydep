from __future__ import annotations

import json
import hashlib
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.implementation.agents.runtime as runtime_module
import app.implementation.agents.workspace as workspace_module
from app.design.services.erd.mapping import build_logical_model
from app.implementation.agents import execute_openhands_task
from app.implementation.agents.admission import integration_evidence_paths
from app.implementation.agents.runtime import (
    OWNER_GAP_RECOVERY_MESSAGE,
    OWNER_INITIAL_ACTION_MESSAGE,
    OWNER_STUCK_RECOVERY_MESSAGE,
    OWNER_TURN_ITERATIONS,
    NoActionResponseGuard,
    OwnerAccessContract,
    OwnerConversationIncomplete,
    SuccessfulTaskCheckGuard,
    _conversation_needs_finish_recovery,
    _conversation_terminal_failure,
    _editor_read_source_evidence,
    _owner_continuation_required,
    _owner_evidence_boundary_message,
    _owner_message_required,
    _owner_workspace_guidance,
    _task_execution_scope,
    create_openhands_conversation,
)
from app.implementation.agents.source_replace_tool import (
    ExactSourceEdit,
    SourceEditAction,
    SourceReplaceAction,
)
from app.implementation.agents.task_check import (
    TaskCheckSession,
    consume_successful_task_check,
    run_task_check,
)
from app.implementation.agents.upstream_gap_tool import (
    UPSTREAM_GAP_TOOL_NAME,
    UpstreamGap,
)
from app.implementation.agents.verification.build import (
    WorkspaceVerificationError,
    compact_verification_evidence,
    read_gradle_test_failures,
    task_verification_command,
    verify_agent_workspace,
    verify_frontend_workspace,
    verify_run_workspace,
    verify_use_case_scenarios,
)
from app.implementation.agents.workspace import (
    _apply_fixed_runner_permissions,
    _copy_read_sources,
    _harden_control_tree,
    cleanup_agent_workspace,
    grant_owner_file_access,
    path_is_editable,
    prepare_agent_workspace,
)
from app.implementation.delivery.terraform import render_iac
from app.implementation.domain.models import JobSpec
from app.implementation.generation.orchestrator import plan_frontend_tasks
from app.implementation.planning.design_context import TaskSpec
from app.implementation.runtime.linux_runner_transport import OWNER_CONTROL_ROOT_ENV
from app.implementation.workflows.completion import audit_run_completion
from app.implementation.workflows.conformance import (
    SourceDesignConformanceError,
    capture_generated_contracts,
    verify_source_design_conformance,
)
from app.implementation.workflows.coordinator import (
    _execute_task_batch,
    _regression_owner_task_id,
    materialize_owner_tasks,
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


def test_materialize_owner_tasks_plans_without_workflow_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    (run / "reports").mkdir(parents=True)
    calls: list[str] = []
    spec = SimpleNamespace(job_type="INITIAL_IMPLEMENTATION", inputs={"erdBceModel": "erd.json"})

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.build_implementation_ir",
        lambda *_args: calls.append("ir"),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_persistence_tasks",
        lambda *_args: calls.append("persistence"),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_backend_owner_task",
        lambda *_args: calls.append("backend"),
    )

    def plan_frontend(*_args: object) -> None:
        calls.append("frontend")
        (run / "reports" / "run-manifest.json").write_text(
            json.dumps({"implementation_tasks": [{"task_id": "owner-1"}, "not-a-task"]}),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_frontend_tasks", plan_frontend
    )
    for name in (
        "write_execution_plan",
        "build_rtm_traceability_map",
        "apply_repair_directives",
        "reconcile_workflow_state",
    ):
        monkeypatch.setattr(
            f"app.implementation.workflows.coordinator.{name}",
            lambda *_args, _name=name: pytest.fail(f"{_name} must not be called"),
        )

    assert materialize_owner_tasks(run, spec) == [{"task_id": "owner-1"}]
    assert calls == ["ir", "persistence", "backend", "frontend"]


def test_plan_workflow_keeps_materialization_and_workflow_effect_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text("{}", encoding="utf-8")
    calls: list[str] = []
    spec = SimpleNamespace(job_type="INITIAL_IMPLEMENTATION", agent_mode="plan-only")
    tasks = [{"task_id": "owner-1"}]

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.materialize_owner_tasks",
        lambda *_args: calls.append("materialize") or tasks,
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.write_execution_plan",
        lambda *_args: calls.append("execution-plan") or {"status": "PLANNED"},
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator._write_json_atomic",
        lambda *_args: calls.append("write-manifest"),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.build_rtm_traceability_map",
        lambda *_args: calls.append("rtm"),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.apply_repair_directives",
        lambda *_args: calls.append("repair"),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.reconcile_workflow_state",
        lambda *_args: calls.append("reconcile") or {"status": "READY"},
    )

    assert plan_workflow(run, spec) == {"status": "READY"}
    assert calls == ["materialize", "execution-plan", "write-manifest", "rtm", "repair", "reconcile"]


def test_materialize_owner_tasks_rejects_feedback_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.build_implementation_ir",
        lambda *_args: pytest.fail("feedback revision must not build owner tasks"),
    )

    with pytest.raises(ValueError, match="initial implementation"):
        materialize_owner_tasks(
            tmp_path / "run", SimpleNamespace(job_type="FEEDBACK_REVISION", inputs={})
        )


def test_lazy_frontend_planning_inherits_stable_backend_llm_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    selected_llm = {
        "provider": "selected-provider",
        "model": "selected-model",
        "baseUrl": "https://selected.example/v1",
        "temperature": 0.17,
        "maxOutputTokens": 4321,
        "reasoningEffort": "low",
    }
    other_llm = {**selected_llm, "model": "other-model"}
    backend_tasks = [
        {"task_id": "z-backend", "task_type": "backend-implementation", "llm": other_llm},
        {"task_id": "a-backend", "task_type": "backend-implementation", "llm": selected_llm},
    ]
    manifest_path = reports / "run-manifest.json"
    manifest_path.write_text(json.dumps({"implementation_tasks": backend_tasks}), encoding="utf-8")

    def task(task_id: str, task_type: str, owner: str) -> TaskSpec:
        return TaskSpec(
            task_id=task_id,
            control=task_id,
            prompt_file=f"reports/{task_id}.prompt.md",
            context_file=f"reports/{task_id}.context.json",
            allowed_write_paths=[],
            immutable_paths=[],
            source_artifacts={},
            prompt_sha256="test",
            llm={"model": "fallback"},
            owner=owner,
            task_type=task_type,
        )

    monkeypatch.setattr(
        "app.implementation.generation.orchestrator.generate_frontend_tasks",
        lambda _spec, _run: [task("frontend", "frontend-implementation", "frontend")],
    )
    monkeypatch.setattr(
        "app.implementation.generation.orchestrator.generate_frontend_unit_test_tasks",
        lambda _spec, _run, _subjects: [],
    )
    monkeypatch.setattr(
        "app.implementation.generation.orchestrator.generate_vertical_integration_task",
        lambda _spec, _run, _prior: task("integration", "integration-implementation", "implementation"),
    )

    plan_frontend_tasks(SimpleNamespace(), run)

    tasks = json.loads(manifest_path.read_text(encoding="utf-8"))["implementation_tasks"]
    frontend = next(item for item in tasks if item["task_type"] == "frontend-implementation")
    integration = next(item for item in tasks if item["task_type"] == "integration-implementation")
    backend = next(item for item in tasks if item["task_id"] == "a-backend")
    assert frontend["llm"] == selected_llm
    assert integration["llm"] == selected_llm
    assert frontend["llm"] is not backend["llm"]
    assert integration["llm"] is not backend["llm"]
    assert backend["llm"] == selected_llm


def test_final_workspace_verification_publishes_success_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """작업자가 생성한 소스를 최종 검증하고 공개 보고서를 남기는 흐름을 확인한다."""
    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    verification = {"exitCode": 0, "testResults": ""}
    with (
        patch(
            "app.implementation.agents.verification.build.prepare_agent_workspace",
            wraps=prepare_agent_workspace,
        ) as prepare,
        patch(
            "app.implementation.agents.verification.build.verify_agent_workspace",
            return_value=verification,
        ) as verify,
    ):
        result = verify_run_workspace(run)

    assert prepare.call_args.kwargs["requires_owner_terminal"] is False
    verify.assert_called_once()
    report = json.loads((run / "reports/final-verification.json").read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert report["verification"] == verification


def test_validation_flag_disabled_runs_backend_and_frontend_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "application").mkdir()
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    with patch(
        "app.implementation.agents.verification.build.subprocess.run",
        return_value=SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    ) as backend:
        verify_agent_workspace(tmp_path)
    with patch(
        "app.implementation.agents.verification.build.run_frontend_verification",
        return_value={"exitCode": 0},
    ) as frontend:
        verify_frontend_workspace(tmp_path)

    backend.assert_called_once()
    frontend.assert_called_once()


def test_validation_flag_skips_implementation_backend_frontend_and_final_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "application").mkdir()
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    with patch(
        "app.implementation.agents.verification.build.subprocess.run",
    ) as backend:
        backend_evidence = verify_agent_workspace(tmp_path)
    with patch(
        "app.implementation.agents.verification.build.run_frontend_verification",
    ) as frontend:
        frontend_evidence = verify_frontend_workspace(tmp_path)
    run = tmp_path / "run"
    (run / "application").mkdir(parents=True)
    with patch(
        "app.implementation.agents.verification.build.verify_agent_workspace",
    ) as final_backend, patch(
        "app.implementation.agents.verification.build.verify_frontend_workspace",
    ) as final_frontend:
        final_result = verify_run_workspace(run)

    assert backend.call_count == 0
    assert frontend.call_count == 0
    assert final_backend.call_count == 0
    assert final_frontend.call_count == 0
    assert backend_evidence == {"status": "SKIPPED", "reason": "demo-validation-skip"}
    assert frontend_evidence == backend_evidence
    assert final_result["status"] == "SUCCEEDED"
    assert final_result["verification"] == backend_evidence


def test_validation_skip_does_not_bypass_testing_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    failure = {"command": ["testing-dynamic"], "exitCode": 1, "gateStatus": "FAIL"}
    with patch(
        "app.testing.repair_check.verify_testing_repair_gate",
        return_value=failure,
    ) as dynamic_gate, pytest.raises(WorkspaceVerificationError):
        verify_agent_workspace(tmp_path, "testing-dynamic-functional")

    dynamic_gate.assert_called_once()


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


def test_thin_integration_check_runs_backend_then_frontend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    application = tmp_path / "application"
    application.mkdir()
    calls: list[str] = []

    def passed_backend(*_args: object, **_kwargs: object):
        calls.append("backend")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def passed_frontend(_sandbox: Path) -> dict[str, object]:
        calls.append("frontend")
        return {
            "command": ["npm", "run", "build"],
            "exitCode": 0,
            "verificationKind": "production-build",
        }

    @contextmanager
    def ready_application(*_args: object, **_kwargs: object):
        calls.append("startup")
        yield "http://localhost", {"healthPath": "/healthz"}
        calls.append("cleanup")

    with (
        patch(
            "app.implementation.agents.verification.build.subprocess.run",
            side_effect=passed_backend,
        ),
        patch(
            "app.implementation.agents.verification.build.verify_frontend_workspace",
            side_effect=passed_frontend,
        ),
        patch(
            "app.testing.runtime.app_container.running_application",
            side_effect=ready_application,
        ),
    ):
        result = verify_agent_workspace(
            tmp_path,
            task_type="integration-implementation",
        )

    assert calls == ["backend", "frontend", "startup", "cleanup"]
    assert result["exitCode"] == 0
    assert result["backendVerification"]["exitCode"] == 0
    assert result["frontendVerification"]["exitCode"] == 0
    assert result["frontendVerification"]["verificationKind"] == "production-build"
    assert result["applicationStartup"] == {
        "status": "SUCCEEDED",
        "runtime": {"healthPath": "/healthz"},
    }
    assert result["command"][-2:] == ["application-startup", "health-check"]


def test_thin_integration_startup_failure_preserves_diagnostics_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.testing.runtime.app_container import ApplicationLaunchError

    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    calls: list[str] = []

    @contextmanager
    def failed_application(*_args: object, **_kwargs: object):
        calls.append("startup")
        try:
            raise ApplicationLaunchError(
                "Application exited before health check.",
                defect_class="SUT_DEFECT",
                application_log=(
                    "Spring startup output\n"
                    "ERROR: STARTUP_ROOT_CAUSE_SENTINEL\n"
                    "\tat app.bootstrap.GeneratedConfiguration.load(GeneratedConfiguration.java:42)"
                ),
            )
            yield "http://localhost", {}
        finally:
            calls.append("cleanup")

    with (
        patch(
            "app.implementation.agents.verification.build.verify_agent_workspace",
            return_value={"exitCode": 0},
        ),
        patch(
            "app.implementation.agents.verification.build.verify_frontend_workspace",
            return_value={"exitCode": 0, "verificationKind": "production-build"},
        ),
        patch(
            "app.testing.runtime.app_container.running_application",
            side_effect=failed_application,
        ),
        pytest.raises(WorkspaceVerificationError) as raised,
    ):
        verify_agent_workspace(tmp_path, task_type="integration-implementation")

    assert calls == ["startup", "cleanup"]
    assert raised.value.evidence["command"] == ["application-startup", "health-check"]
    assert raised.value.evidence["applicationStartup"] == {
        "status": "FAILED",
        "defectClass": "SUT_DEFECT",
        "applicationLog": (
            "Spring startup output\n"
            "ERROR: STARTUP_ROOT_CAUSE_SENTINEL\n"
            "\tat app.bootstrap.GeneratedConfiguration.load(GeneratedConfiguration.java:42)"
        ),
    }
    assert "STARTUP_ROOT_CAUSE_SENTINEL" in raised.value.evidence["stderr"]
    assert "STARTUP_ROOT_CAUSE_SENTINEL" in compact_verification_evidence(
        raised.value.evidence
    )


def test_frontend_marker_contract_fails_before_build_and_cleared_marker_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    source = tmp_path / "application/frontend/src/features/orders.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("// TODO_IMPLEMENT_ORDER\n", encoding="utf-8")
    profile = {
        "requiredAbsentMarkers": [
            {
                "path": "application/frontend/src/features/orders.tsx",
                "markers": ["TODO_IMPLEMENT_ORDER"],
            }
        ]
    }

    with patch(
        "app.implementation.agents.verification.build.verify_frontend_typecheck_workspace"
    ) as frontend, pytest.raises(WorkspaceVerificationError):
        verify_agent_workspace(
            tmp_path, "frontend-implementation", verification_profile=profile
        )
    frontend.assert_not_called()

    source.write_text("export const orderReady = true;\n", encoding="utf-8")
    with patch(
        "app.implementation.agents.verification.build.verify_frontend_typecheck_workspace",
        return_value={"exitCode": 0},
    ) as frontend:
        result = verify_agent_workspace(
            tmp_path, "frontend-implementation", verification_profile=profile
        )
    frontend.assert_called_once_with(tmp_path)
    assert result["exitCode"] == 0


def test_integration_marker_contract_fails_before_backend_or_frontend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    source = tmp_path / "application/frontend/src/features/orders.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("// TODO_INTEGRATION_ORDER\n", encoding="utf-8")
    profile = {
        "requiredAbsentMarkers": [
            {
                "path": "application/frontend/src/features/orders.tsx",
                "markers": ["TODO_INTEGRATION_ORDER"],
            }
        ]
    }

    with patch(
        "app.implementation.agents.verification.build.subprocess.run"
    ) as backend, patch(
        "app.implementation.agents.verification.build.verify_frontend_workspace"
    ) as frontend, pytest.raises(WorkspaceVerificationError):
        verify_agent_workspace(
            tmp_path, "integration-implementation", verification_profile=profile
        )

    backend.assert_not_called()
    frontend.assert_not_called()


def test_backend_marker_contract_still_fails_before_gradle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    source = tmp_path / "application/src/main/java/Orders.java"
    source.parent.mkdir(parents=True)
    source.write_text("// TODO_IMPLEMENT_ORDER\n", encoding="utf-8")
    profile = {
        "requiredAbsentMarkers": [
            {
                "path": "application/src/main/java/Orders.java",
                "markers": ["TODO_IMPLEMENT_ORDER"],
            }
        ]
    }

    with patch(
        "app.implementation.agents.verification.build.subprocess.run"
    ) as backend, pytest.raises(WorkspaceVerificationError):
        verify_agent_workspace(
            tmp_path, "backend-implementation", verification_profile=profile
        )

    backend.assert_not_called()


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


def test_restricted_persistent_owner_refreshes_system_files_and_preserves_candidate(
    tmp_path: Path,
) -> None:
    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")
    tsconfig = run / "application" / "tsconfig.json"
    tsconfig.write_text('{"exclude":["test"]}', encoding="utf-8")
    task = {
        "task_id": "restricted-owner",
        "allowed_write_paths": ["application/src/Main.java"],
    }

    with patch(
        "app.implementation.agents.workspace.tempfile.gettempdir",
        return_value=str(tmp_path / "temp"),
    ), patch(
        "app.implementation.agents.workspace._restore_coordinator_access"
    ) as restore_access, patch(
        "app.implementation.agents.workspace._apply_fixed_runner_permissions"
    ) as apply_permissions:
        sandbox = prepare_agent_workspace(
            run,
            task,
            persistent=True,
            requires_owner_terminal=False,
        )
        refreshed = prepare_agent_workspace(
            run,
            task,
            persistent=True,
            requires_owner_terminal=False,
        )
        candidate = sandbox / "application/src/Main.java"
        candidate.write_text("class Main { int candidate; }", encoding="utf-8")
        (sandbox / "application/tsconfig.json").write_text(
            '{"exclude":[]}', encoding="utf-8"
        )
        refreshed_again = prepare_agent_workspace(
            run,
            task,
            persistent=True,
            requires_owner_terminal=False,
        )

    assert refreshed == sandbox
    restore_access.assert_not_called()
    apply_permissions.assert_not_called()
    assert refreshed_again == sandbox
    assert candidate.read_text(encoding="utf-8") == "class Main { int candidate; }"
    assert (sandbox / "application/tsconfig.json").read_text(encoding="utf-8") == (
        '{"exclude":["test"]}'
    )


def test_shared_owner_workspace_reuses_caches_but_restores_unaccepted_source(
    tmp_path: Path,
) -> None:
    """A stopped owner cannot pass an unpromoted edit to the next owner."""

    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    accepted = run / "application" / "src" / "Accepted.java"
    rejected = run / "application" / "src" / "Rejected.java"
    for path in (accepted, rejected):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"class {path.stem} {{}}", encoding="utf-8")
    rejected.write_text("class Rejected { int aa; }", encoding="utf-8")
    first_task = {
        "task_id": "implement-first",
        "allowed_write_paths": [
            "application/src/Accepted.java",
            "application/src/Rejected.java",
        ],
    }
    second_task = {
        "task_id": "implement-second",
        # Deliberately overlap the failed owner's writable file: task identity,
        # not just path scope, prevents the unaccepted body from leaking.
        "allowed_write_paths": ["application/src/Rejected.java"],
    }

    with patch(
        "app.implementation.agents.workspace.tempfile.gettempdir",
        return_value=str(tmp_path / "temp"),
    ):
        sandbox = prepare_agent_workspace(
            run,
            first_task,
            persistent=True,
            shared_owner_workspace=True,
            requires_owner_terminal=False,
        )
        assert not (sandbox / ".easydep-shared-owner-task").exists()
        assert workspace_module._shared_owner_task_marker(sandbox).is_file()
        # These outputs are intentionally retained to warm the next serial owner.
        cache_paths = {
            "application/build/test-results/test/binary/output.bin": b"build",
            "application/.gradle/local.bin": b"gradle",
            "application/node_modules/pkg/index.js": b"node",
            "application/frontend/tsconfig.tsbuildinfo": b"typescript",
        }
        for relative, content in cache_paths.items():
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        # This simulates a verified first-owner promotion, while the rejected
        # file represents an edit left behind by a stopped/failed owner.
        (sandbox / "application/src/Accepted.java").write_text(
            "class Accepted { int promoted; }", encoding="utf-8"
        )
        accepted.write_text("class Accepted { int promoted; }", encoding="utf-8")
        rejected_candidate = sandbox / "application/src/Rejected.java"
        rejected_candidate.write_text(
            "class Rejected { int bb; }", encoding="utf-8"
        )
        # Equal size and canonical mtime must not let an unaccepted body pass
        # the next-task refresh fast path.
        canonical_stat = rejected.stat()
        workspace_module.os.utime(
            rejected_candidate,
            ns=(canonical_stat.st_atime_ns, canonical_stat.st_mtime_ns),
        )
        retried = prepare_agent_workspace(
            run,
            first_task,
            persistent=True,
            shared_owner_workspace=True,
            requires_owner_terminal=False,
        )
        assert retried == sandbox
        assert rejected_candidate.read_text(encoding="utf-8") == "class Rejected { int bb; }"

        refreshed = prepare_agent_workspace(
            run,
            second_task,
            persistent=True,
            shared_owner_workspace=True,
            requires_owner_terminal=False,
        )

    assert refreshed == sandbox
    assert (sandbox / "application/src/Accepted.java").read_text(encoding="utf-8") == (
        "class Accepted { int promoted; }"
    )
    assert (sandbox / "application/src/Rejected.java").read_text(encoding="utf-8") == (
        "class Rejected { int aa; }"
    )
    for relative, content in cache_paths.items():
        assert (sandbox / relative).read_bytes() == content


def test_workspace_refresh_and_snapshot_prune_ignored_directories(
    tmp_path: Path,
) -> None:
    """Retained dependency/build trees must not trigger recursive Path scans."""

    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")
    task = {"task_id": "owner", "allowed_write_paths": []}

    with patch(
        "app.implementation.agents.workspace.tempfile.gettempdir",
        return_value=str(tmp_path / "temp"),
    ):
        sandbox = prepare_agent_workspace(
            run, task, requires_owner_terminal=False
        )
        for relative in (
            "application/build/trap.bin",
            "application/.gradle/trap.bin",
            "application/node_modules/pkg/trap.js",
            "application/dist/trap.js",
        ):
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"ignored")

        with patch.object(
            Path,
            "rglob",
            side_effect=AssertionError("workspace scans must prune before recursion"),
        ):
            refreshed = prepare_agent_workspace(
                run, task, requires_owner_terminal=False
            )
            hashes = workspace_module.snapshot_files(refreshed)

    assert refreshed == sandbox
    assert hashes == {"application/src/Main.java": hashlib.sha256(b"class Main {}").hexdigest()}


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
    """Legacy task checks stay focused; the backend owner only compiles production source."""
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
    ) == ["gradlew", "compileJava", "--build-cache"]
    assert task_verification_command(
        ["gradlew"],
        "backend-implementation",
        ["application/src/test/java/com/example/OrderService.java"],
    ) == ["gradlew", "compileJava", "--build-cache"]


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


def _configure_editor_owner_task(run: Path) -> None:
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "backend-implementation",
            "owner": "backend",
            "owner_tool_mode": "editor",
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")


def test_editor_owner_applies_one_direct_response_then_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    _configure_editor_owner_task(run)
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    action = SourceReplaceAction(
        path=source_path, source="class OrderService { int implemented; }"
    )
    with ExitStack() as stack:
        for manager in (
            patch("app.implementation.agents.runtime.openhands_connection", return_value=LlmConnection("cloudflare", "approved-key", "https://example.invalid/v1", "@cf/zai-org/glm-5.3-flash", "openai")),
            patch("app.implementation.agents.runtime._request_direct_editor_action", return_value=action),
            patch("app.implementation.agents.runtime.run_task_check", return_value=(True, "passed")),
            patch("app.implementation.agents.runtime.verify_agent_workspace", return_value={"exitCode": 0}),
        ):
            stack.enter_context(manager)
        result = execute_openhands_task(run, task_id)
    assert result["status"] == "SUCCEEDED"
    assert result["tools"] == ["replace_source"]


@pytest.mark.parametrize(
    ("task_type", "source_path", "test_path"),
    [
        (
            "frontend-unit-test",
            "application/frontend/src/features/dropregistration.tsx",
            "application/frontend/src/features/dropregistration.test.tsx",
        ),
        (
            "backend-unit-test",
            "application/src/main/java/com/example/application/OrderService.java",
            "application/src/test/java/com/example/application/OrderServiceTest.java",
        ),
    ],
)
def test_direct_editor_unit_request_contains_authored_contract_and_readonly_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
    source_path: str,
    test_path: str,
) -> None:
    run, task_id, _old_path, _old_source = _write_minimal_agent_task(tmp_path)
    task_file = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_file.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": task_type,
            "owner": "frontend" if task_type == "frontend-unit-test" else "backend",
            "owner_tool_mode": "editor",
            "allowed_write_paths": [test_path],
            "required_output_paths": [test_path],
            "required_test_paths": [test_path],
            "immutable_paths": [source_path],
            "depends_on": ["completed-implementation"],
            "verification_profile": {"unitTestSubjectPaths": [source_path]},
        }
    )
    task_file.write_text(json.dumps(task), encoding="utf-8")
    source = run / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    existing_source = source.read_text(encoding="utf-8") if source.is_file() else (
        "export const DropRegistration = () => null;\n"
        if task_type == "frontend-unit-test"
        else "class OrderService {}\n"
    )
    body = "// SUBJECT_CONTRACT: actual source supplied read-only\n" + existing_source
    source.write_text(body, encoding="utf-8")
    prompt_file = run / "reports/implementation-tasks/order.prompt.md"
    prompt_file.write_text("Write one focused test for the supplied subject.", encoding="utf-8")
    captured: list[str] = []

    def request(
        _connection: object, prompt: str, _llm: object, _allowed_paths: list[str]
    ) -> SourceReplaceAction:
        captured.append(prompt)
        return SourceReplaceAction(path=test_path, source="describe('assigned unit test', () => {});")

    class PassingTaskCheck:
        last_evidence = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self) -> tuple[bool, str]:
            return True, "passed"

    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    with ExitStack() as stack:
        for manager in (
            patch(
                "app.implementation.agents.runtime.openhands_connection",
                return_value=LlmConnection(
                    "cloudflare", "approved-key", "https://example.invalid/v1", "@cf/zai-org/glm-5.3-flash", "openai"
                ),
            ),
            patch("app.implementation.agents.runtime._request_direct_editor_action", side_effect=request),
            patch("app.implementation.agents.runtime.TaskCheckSession", PassingTaskCheck),
            patch("app.implementation.agents.runtime.verify_agent_workspace", return_value={"exitCode": 0}),
        ):
            stack.enter_context(manager)
        result = execute_openhands_task(run, task_id)

    assert result["status"] == "SUCCEEDED"
    assert len(captured) == 1
    request_text = captured[0]
    assert "This is a focused unit-test authoring task" in request_text
    assert "supplied implementation source and API evidence are read-only" in request_text
    assert test_path in request_text
    assert "SUBJECT_CONTRACT: actual source supplied read-only" in request_text
    if task_type == "frontend-unit-test":
        assert "an HTML tag alone does not guarantee a role" in request_text
        assert "an unnamed form is not a form landmark" in request_text


def test_editor_pending_candidate_allows_same_body_retry_but_not_canonical_noop(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    sandbox = tmp_path / "sandbox"
    relative = Path("application/frontend/src/Calculator.test.tsx")
    canonical = run_root / relative
    candidate = sandbox / relative
    canonical.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    canonical.write_text("export const test = 'old';\n", encoding="utf-8")
    candidate.write_text("export const test = 'candidate';\n", encoding="utf-8")

    # A repeat replace_source with the same body has no attempt delta, but the
    # persistent sandbox still differs from canonical and must reach the normal
    # verifier/promotion boundary.
    attempt_changes: set[str] = set()
    candidate_changes = runtime_module._candidate_application_changes(
        sandbox, run_root
    )
    assert candidate_changes == {relative.as_posix()}
    assert not (not attempt_changes and not candidate_changes)

    # Once canonical matches, no new attempt delta and no pending candidate must
    # retain the existing no-source-change stop.
    canonical.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
    candidate_changes = runtime_module._candidate_application_changes(
        sandbox, run_root
    )
    assert candidate_changes == set()
    assert not attempt_changes and not candidate_changes


def test_editor_owner_repairs_once_after_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    _configure_editor_owner_task(run)
    source_path = "application/frontend/src/features/orders.tsx"
    target = run / source_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "// SCAFFOLD_ONLY\nexport const Orders = () => null;", encoding="utf-8"
    )
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "frontend-implementation",
            "owner": "frontend",
            "required_completion_markers": ["EASYDEP-IMPLEMENT: GET /orders (getOrders)"],
            "allowed_write_paths": [source_path],
            "required_output_paths": [source_path],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    context_path = run / "reports/implementation-tasks/order.context.json"
    operation_path = run / "reports/implementation-tasks/frontend-operation-context/get-orders.json"
    operation_path.parent.mkdir(parents=True, exist_ok=True)
    operation_path.write_text(
        json.dumps(
            {
                "generatedClient": {
                    "resolved": True,
                    "generatedMethodPath": "application/frontend/src/generated/src/apis/DefaultApi.ts",
                    "requestType": "GetOrdersRequest",
                    "responseType": "Order",
                },
                "referencedComponents": {"#/components/schemas/Order": {"type": "object"}},
            }
        ),
        encoding="utf-8",
    )
    api_path = run / "application/frontend/src/api.ts"
    api_path.parent.mkdir(parents=True, exist_ok=True)
    api_path.write_text("export async function getOrders() { return []; }", encoding="utf-8")
    generated_api = run / "application/frontend/src/generated/src/apis/DefaultApi.ts"
    generated_api.parent.mkdir(parents=True, exist_ok=True)
    generated_api.write_text(
        "export interface GetOrdersRequest { id: string; }\n"
        "export class DefaultApi {\n"
        "  async getOrders(requestParameters: GetOrdersRequest): Promise<Order> {\n"
        "    return {} as Order;\n"
        "  }\n"
        "}",
        encoding="utf-8",
    )
    generated_model = run / "application/frontend/src/generated/src/models/Order.ts"
    generated_model.parent.mkdir(parents=True, exist_ok=True)
    generated_model.write_text(
        "export interface Order { createdAt: Date; }\n"
        "export function OrderFromJSON(value: unknown): Order { return value as Order; }",
        encoding="utf-8",
    )
    context_path.write_text(
        json.dumps(
            {
                "readSourcePaths": [
                    "application/frontend/src/api.ts",
                    "reports/implementation-tasks/frontend-operation-context/get-orders.json",
                ],
                "operationIds": ["getOrders"],
                "operationContextPaths": [
                    "reports/implementation-tasks/frontend-operation-context/get-orders.json"
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    actions = [
        SourceReplaceAction(path=source_path, source="// BROKEN_CURRENT\nexport const Orders = () => null;"),
        SourceReplaceAction(path=source_path, source="// REPAIRED_CURRENT\nexport const Orders = () => null;"),
    ]
    prompts: list[str] = []

    def capture_prompt(
        _connection: object, prompt: str, _llm: object, _allowed_paths: list[str]
    ) -> SourceReplaceAction:
        prompts.append(prompt)
        return actions[len(prompts) - 1]

    direct_request = patch(
        "app.implementation.agents.runtime._request_direct_editor_action",
        side_effect=capture_prompt,
    )
    with ExitStack() as stack:
        for manager in (
            patch("app.implementation.agents.runtime.openhands_connection", return_value=LlmConnection("cloudflare", "approved-key", "https://example.invalid/v1", "@cf/zai-org/glm-5.3-flash", "openai")),
            patch("app.implementation.agents.runtime.run_task_check", return_value=(True, "passed")),
            patch("app.implementation.agents.runtime.verify_agent_workspace", return_value={"exitCode": 0}),
            patch(
                "app.implementation.agents.task_check.verify_agent_workspace",
                side_effect=[
                    WorkspaceVerificationError(
                        {"command": ["npm", "run", "build"], "exitCode": 1, "stderr": "exact diagnosis"}
                    ),
                    {"command": ["npm", "run", "build"], "exitCode": 0},
                ],
            ),
        ):
            stack.enter_context(manager)
        direct = stack.enter_context(direct_request)
        result = execute_openhands_task(run, task_id)
    assert result["status"] == "SUCCEEDED"
    assert result["executionStatus"] == "finished"
    assert result["finishRecoveryUsed"] is False
    assert direct.call_count == 2
    assert "async getOrders(requestParameters: GetOrdersRequest): Promise<Order>" in prompts[0]
    assert "Implement the order use case." in prompts[0]
    assert source_path in prompts[0]
    repair_prompt = prompts[1]
    assert "Implement the order use case." in repair_prompt
    assert source_path in repair_prompt
    assert "exact edit_source contexts" in repair_prompt
    assert "replace_source for a broad rewrite" in repair_prompt
    assert "BROKEN_CURRENT" in repair_prompt
    assert "Exact diagnosis" in repair_prompt
    assert "generated TypeScript SDK declaration" in repair_prompt
    assert "async getOrders(requestParameters: GetOrdersRequest): Promise<Order>" in repair_prompt
    assert "export interface Order { createdAt: Date; }" in repair_prompt


def test_editor_no_change_repair_retains_initial_check_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    _configure_editor_owner_task(run)
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    unchanged_repair = SourceReplaceAction(
        path=source_path, source="class OrderService { int broken; }"
    )
    initial_evidence = {
        "command": ["gradle", "test"],
        "exitCode": 1,
        "stdout": "",
        "stderr": "selected assertion failed",
        "unitTestResults": {"total": 1, "failed": 1, "skipped": 0},
    }
    with ExitStack() as stack:
        for manager in (
            patch(
                "app.implementation.agents.runtime.openhands_connection",
                return_value=LlmConnection(
                    "cloudflare", "approved-key", "https://example.invalid/v1",
                    "@cf/zai-org/glm-5.3-flash", "openai",
                ),
            ),
            patch(
                "app.implementation.agents.runtime._request_direct_editor_action",
                side_effect=[unchanged_repair, unchanged_repair],
            ),
            patch(
                "app.implementation.agents.task_check.verify_agent_workspace",
                side_effect=WorkspaceVerificationError(initial_evidence),
            ),
        ):
            stack.enter_context(manager)
        with pytest.raises(OwnerConversationIncomplete) as raised:
            execute_openhands_task(run, task_id)

    evidence = raised.value.evidence
    assert evidence["stderr"] == "Editor repair made no source change."
    assert "selected assertion failed" in evidence["initialTaskCheckDiagnosis"]
    assert evidence["initialTaskCheckEvidence"] == initial_evidence


def test_direct_editor_repair_prompt_keeps_initial_contract_without_history() -> None:
    conversation = runtime_module._DirectEditorConversation(
        Path("."),
        LlmConnection("cloudflare", "key", "https://example.invalid/v1", "model", "openai"),
        {},
        ["application/frontend/src/features/orders.tsx"],
        [],
    )
    initial = "INITIAL_TASK_CONTRACT: orders.tsx is the only writable target"
    first_repair = "FIRST_REPAIR: stale diagnosis and source"
    latest_repair = "LATEST_REPAIR: updated source and exact diagnosis"

    conversation.send_message(initial)
    assert conversation.prompt == initial
    conversation.send_message(first_repair)
    assert initial in conversation.prompt
    assert first_repair in conversation.prompt
    conversation.send_message(latest_repair)

    assert initial in conversation.prompt
    assert latest_repair in conversation.prompt
    assert first_repair not in conversation.prompt


def test_editor_owner_retries_out_of_scope_path_without_weakening_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, task_id, _source_path, _source = _write_minimal_agent_task(tmp_path)
    _configure_editor_owner_task(run)
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    source_path = "application/src/main/java/com/example/application/OrderService.java"
    outside_path = "application/Other.java"
    outside = run / outside_path
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("class Other {}", encoding="utf-8")
    actions = iter(
        [
            SourceReplaceAction(path=outside_path, source="class Other { int changed; }"),
            SourceReplaceAction(path=source_path, source="class OrderService { int fixed; }"),
        ]
    )
    prompts: list[str] = []

    class PassingTaskCheck:
        last_evidence = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self) -> tuple[bool, str]:
            return True, "passed"

    def request(
        _connection: object, prompt: str, _llm: object, allowed: list[str]
    ) -> SourceReplaceAction:
        prompts.append(prompt)
        assert allowed == [source_path]
        return next(actions)

    sleep = patch("app.implementation.agents.runtime.time.sleep")
    with ExitStack() as stack:
        sleep_mock = stack.enter_context(sleep)
        for manager in (
            patch("app.implementation.agents.runtime.openhands_connection", return_value=LlmConnection("cloudflare", "approved-key", "https://example.invalid/v1", "@cf/zai-org/glm-5.3-flash", "openai")),
            patch("app.implementation.agents.runtime._request_direct_editor_action", side_effect=request),
            patch("app.implementation.agents.runtime.TaskCheckSession", PassingTaskCheck),
            patch("app.implementation.agents.runtime.verify_agent_workspace", return_value={"exitCode": 0}),
        ):
            stack.enter_context(manager)
        result = execute_openhands_task(run, task_id)

    assert result["status"] == "SUCCEEDED"
    assert outside.read_text(encoding="utf-8") == "class Other {}"
    assert (run / source_path).read_text(encoding="utf-8") == "class OrderService { int fixed; }"
    assert len(prompts) == 2
    assert prompts[0] in prompts[1]
    assert '"failureCode": "INVALID_PATH_ARGUMENT"' in prompts[1]
    assert source_path in prompts[1]
    sleep_mock.assert_called_once_with(1)
    journal = run / "reports/agent-executions" / f"{task_id}.attempt-001.events.jsonl"
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    rejected = events[0]["event"]
    assert events[0]["type"] == "DirectEditorActionRejected"
    assert rejected["rejectedPath"] == outside_path
    assert rejected["allowedPaths"] == [source_path]
    assert rejected["sourceSha256"] == hashlib.sha256(
        b"class Other { int changed; }"
    ).hexdigest()
    assert "class Other { int changed; }" not in journal.read_text(encoding="utf-8")


def test_direct_editor_response_retries_until_cancelled_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "sandbox"
    target = sandbox / "application/frontend/src/features/item.tsx"
    target.parent.mkdir(parents=True)
    target.write_text("export const Item = 'original';\n", encoding="utf-8")
    journal = runtime_module.EventJournal(tmp_path / "events.jsonl")
    conversation = runtime_module._DirectEditorConversation(
        sandbox,
        LlmConnection("cloudflare", "key", "https://example.invalid/v1", "model", "openai"),
        {"maxOutputTokens": 128},
        [str(target)],
        [journal],
    )
    conversation.send_message("TASK CONTRACT: only item.tsx is writable")
    prompts: list[str] = []

    def rejected_then_cancelled(*args: object) -> SourceReplaceAction:
        prompts.append(str(args[1]))
        if len(prompts) == 3:
            raise KeyboardInterrupt
        raise runtime_module.DirectEditorResponseError(
            "invalid tool JSON",
            failure_code="INVALID_TOOL_JSON",
            retryable=True,
        )

    sleeps: list[int] = []
    monkeypatch.setattr(
        "app.implementation.agents.runtime._request_direct_editor_action",
        rejected_then_cancelled,
    )
    monkeypatch.setattr("app.implementation.agents.runtime.time.sleep", sleeps.append)

    with pytest.raises(KeyboardInterrupt):
        conversation.run()

    assert sleeps == [1, 2]
    assert target.read_text(encoding="utf-8") == "export const Item = 'original';\n"
    assert len(prompts) == 3
    assert "TASK CONTRACT" in prompts[1]
    assert "INVALID_TOOL_JSON" in prompts[1]
    assert "INVALID_TOOL_JSON" in prompts[2]
    assert prompts[1].count("INVALID_TOOL_JSON") == 1
    events = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2
    assert all(event["event"]["failureCode"] == "INVALID_TOOL_JSON" for event in events)
    assert all("source" not in event["event"] for event in events)

def test_direct_editor_requests_named_low_reasoning_replace_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = SimpleNamespace(completions=self)

        def create(self, **request: object) -> object:
            captured.update(request)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    function=SimpleNamespace(
                                        name="replace_source",
                                        arguments=json.dumps(
                                            {
                                                "path": "application/OrderService.java",
                                                "source": "class OrderService {}",
                                            }
                                        ),
                                    )
                                )
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr("openai.OpenAI", Client)
    action = runtime_module._request_direct_editor_action(
        LlmConnection(
            "cloudflare",
            "approved-key",
            "https://example.invalid/v1",
            "@cf/zai-org/glm-5.3-flash",
            "openai",
        ),
        "replace this source",
        {"maxOutputTokens": 1024, "reasoningEffort": "none"},
        ["application/OrderService.java"],
    )
    assert action.path == "application/OrderService.java"
    assert captured["model"] == "@cf/zai-org/glm-5.3-flash"
    assert captured["max_completion_tokens"] == 1024
    assert captured["reasoning_effort"] == "none"
    assert captured["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in captured["tools"]] == [
        "replace_source", "edit_source"
    ]
    for tool in captured["tools"]:
        path_schema = tool["function"]["parameters"]["properties"]["path"]
        assert path_schema["enum"] == ["application/OrderService.java"]
    assert runtime_module._direct_editor_reasoning_effort({"reasoningEffort": "medium"}) == "low"


def test_direct_editor_can_select_edit_source_with_exact_path_enum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = SimpleNamespace(completions=self)

        def create(self, **request: object) -> object:
            captured.update(request)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    function=SimpleNamespace(
                                        name="edit_source",
                                        arguments=json.dumps(
                                            {
                                                "path": "application/OrderService.java",
                                                "edits": [
                                                    {"old_text": "old", "new_text": "new"}
                                                ],
                                            }
                                        ),
                                    )
                                )
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr("openai.OpenAI", Client)
    action = runtime_module._request_direct_editor_action(
        LlmConnection(
            "cloudflare", "approved-key", "https://example.invalid/v1",
            "@cf/zai-org/glm-5.3-flash", "openai",
        ),
        "Make a small correction",
        {"maxOutputTokens": 1024},
        ["application/OrderService.java"],
    )
    assert isinstance(action, SourceEditAction)
    assert action.edits[0].old_text == "old"
    edit_schema = captured["tools"][1]["function"]["parameters"]
    assert edit_schema["required"] == ["path", "edits"]
    assert edit_schema["properties"]["path"]["enum"] == ["application/OrderService.java"]


def test_direct_editor_edit_dispatch_writes_once_and_journals_hash_only(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    target = sandbox / "application/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService { int old; }", encoding="utf-8")
    journal = runtime_module.EventJournal(tmp_path / "events.jsonl")

    observation = runtime_module._apply_direct_editor_action(
        sandbox,
        [str(target)],
        SourceEditAction(
            path="application/OrderService.java",
            edits=[ExactSourceEdit(old_text="int old;", new_text="int updated;")],
        ),
        journal,
    )

    assert not observation.is_error
    assert target.read_text(encoding="utf-8") == "class OrderService { int updated; }"
    events = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["tool"] == "edit_source"
    assert events[0]["event"]["editCount"] == 1
    assert events[0]["event"]["sourceSha256"] == hashlib.sha256(
        target.read_bytes()
    ).hexdigest()
    assert "old_text" not in journal.path.read_text(encoding="utf-8")
    assert journal.tool_counts["edit_source_applied"] == 1


def test_direct_editor_accepts_one_marked_tsx_source_and_supplies_bounded_evidence(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    target = sandbox / "application/frontend/src/features/orders.tsx"
    api = sandbox / "application/frontend/src/api.ts"
    index = sandbox / "application/frontend/src/index.tsx"
    operation = sandbox / "application/frontend/reports/get-orders.context.json"
    unrelated = sandbox / "application/frontend/src/features/customers.tsx"
    generated_api = sandbox / "application/frontend/src/generated/src/apis/DefaultApi.ts"
    generated_model = sandbox / "application/frontend/src/generated/src/models/Order.ts"
    for path, body in (
        (target, "// EASYDEP-IMPLEMENT: GET /orders (getOrders)\nexport const Orders = () => null;"),
        (api, "export async function getOrders() { return []; }"),
        (index, "import React from 'react';"),
        (
            operation,
            json.dumps(
                {
                    "operationId": "getOrders",
                    "generatedClient": {
                        "resolved": True,
                        "generatedMethodPath": "application/frontend/src/generated/src/apis/DefaultApi.ts",
                        "requestType": "GetOrdersRequest",
                        "responseType": "Order",
                    },
                    "referencedComponents": {
                        "#/components/schemas/Order": {"type": "object"}
                    },
                }
            ),
        ),
        (unrelated, "export const Customers = () => null;"),
        (
            generated_api,
            "export interface GetOrdersRequest { id: string; }\n"
            "export class DefaultApi {\n"
            "  async getOrders(requestParameters: GetOrdersRequest): Promise<Order> {\n"
            "    return {} as Order;\n"
            "  }\n"
            "}",
        ),
        (
            generated_model,
            "export interface Order { createdAt: Date; }\n"
            "export function OrderFromJSON(value: unknown): Order { return value as Order; }",
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    task = {
        "required_completion_markers": ["EASYDEP-IMPLEMENT: GET /orders (getOrders)"]
    }
    writable = [str(target)]
    assert runtime_module._direct_editor_source_is_eligible(
        task, "frontend-implementation", writable, sandbox
    )
    evidence = runtime_module._editor_read_source_evidence(
        sandbox,
        {
            "readSourcePaths": [
                "application/frontend/src/api.ts",
                "application/frontend/src/index.tsx",
                "application/frontend/reports/get-orders.context.json",
            ],
            "operationIds": ["getOrders"],
            "operationContextPaths": [
                "application/frontend/reports/get-orders.context.json"
            ],
        },
        writable,
    )
    assert "application/frontend/src/features/orders.tsx" in evidence
    assert "current writable source" in evidence
    assert "getOrders()" in evidence
    assert "React from" in evidence
    assert '"operationId": "getOrders"' in evidence
    assert "GetOrdersRequest { id: string; }" in evidence
    assert "async getOrders(requestParameters: GetOrdersRequest): Promise<Order>" in evidence
    assert "export interface Order { createdAt: Date; }" in evidence
    assert "Generated TypeScript SDK declarations below define application-facing value types." in evidence
    assert "wire schema" in evidence
    assert "customers.tsx" not in evidence


def test_direct_editor_rejects_multiple_or_oversized_sources(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    first = sandbox / "src/first.tsx"
    second = sandbox / "src/second.tsx"
    first.parent.mkdir(parents=True)
    first.write_text("export {}", encoding="utf-8")
    second.write_text("export {}", encoding="utf-8")
    assert not runtime_module._direct_editor_source_is_eligible(
        {}, "backend-implementation", [str(first), str(second)], sandbox
    )
    first.write_bytes(b"x" * (runtime_module.EDITOR_WRITABLE_SOURCE_MAX_BYTES + 1))
    assert not runtime_module._direct_editor_source_is_eligible(
        {}, "frontend-implementation", [str(first)], sandbox
    )


def test_direct_editor_accepts_missing_single_unit_test_target(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    target = sandbox / "application/src/test/java/ExampleTest.java"

    assert runtime_module._direct_editor_source_is_eligible(
        {}, "backend-unit-test", [str(target)], sandbox
    )


def test_direct_editor_rejects_completion_without_named_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = SimpleNamespace(completions=self)

        def create(self, **_request: object) -> object:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))]
            )

    monkeypatch.setattr("openai.OpenAI", Client)
    with pytest.raises(runtime_module.DirectEditorResponseError, match="exactly one"):
        runtime_module._request_direct_editor_action(
            LlmConnection(
                "cloudflare",
                "approved-key",
                "https://example.invalid/v1",
                "@cf/zai-org/glm-5.3-flash",
                "openai",
            ),
            "replace this source",
            {"maxOutputTokens": 1024},
            ["application/OrderService.java"],
        )


def test_owner_access_contract_bounds_exact_immutable_files(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    context_file = sandbox / "reports/task.context.json"
    read_hint = sandbox / "reports/evidence.json"
    writable = sandbox / "application/OrderService.java"
    immutable_file = sandbox / "application/GeneratedContract.java"
    immutable_directory = sandbox / "application/contracts"
    read_directory = sandbox / "reports/evidence-directory"
    for path in (context_file, read_hint, writable, immutable_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence", encoding="utf-8")
    immutable_directory.mkdir(parents=True)
    read_directory.mkdir(parents=True)

    contract = OwnerAccessContract.build(
        sandbox=sandbox,
        run_root=sandbox,
        task={"context_file": "reports/task.context.json"},
        context={"readSourcePaths": ["reports/evidence.json", "reports/evidence-directory"]},
        task_type="backend-implementation",
        editable_paths=["application/OrderService.java"],
        editable_roots=["application"],
        immutable=[
            "application/GeneratedContract.java",
            "application/contracts",
            "application/MissingContract.java",
        ],
        bounded_evidence=True,
    )

    assert str(context_file.resolve()) in contract.readable_files
    assert str(read_hint.resolve()) in contract.readable_files
    assert str(read_directory.resolve()) not in contract.readable_files
    assert str(writable.resolve()) in contract.readable_files
    assert str(immutable_file.resolve()) not in contract.readable_files
    assert str(immutable_directory.resolve()) not in contract.readable_files
    assert str((sandbox / "application/MissingContract.java").resolve()) not in contract.readable_files
    with pytest.raises(RuntimeError):
        OwnerAccessContract.build(
            sandbox=sandbox,
            run_root=sandbox,
            task={"context_file": "reports/task.context.json"},
            context={},
            task_type="backend-implementation",
            editable_paths=["application/GeneratedContract.java"],
            editable_roots=[],
            immutable=["application/GeneratedContract.java"],
            bounded_evidence=True,
        )
    with pytest.raises(RuntimeError):
        OwnerAccessContract.build(
            sandbox=sandbox,
            run_root=sandbox,
            task={"context_file": "reports/task.context.json"},
            context={},
            task_type="backend-implementation",
            editable_paths=["../escape.java"],
            editable_roots=[],
            immutable=[],
            bounded_evidence=True,
        )

    with pytest.raises(RuntimeError):
        OwnerAccessContract.build(
            sandbox=sandbox,
            run_root=sandbox,
            task={"context_file": "reports/task.context.json"},
            context={},
            task_type="backend-implementation",
            editable_paths=["application/contracts/Locked.java"],
            editable_roots=[],
            immutable=["application/contracts"],
            bounded_evidence=True,
        )
    root_contract = OwnerAccessContract.build(
        sandbox=sandbox,
        run_root=sandbox,
        task={"context_file": "reports/task.context.json"},
        context={},
        task_type="backend-implementation",
        editable_paths=[],
        editable_roots=["application"],
        immutable=["application/contracts"],
        bounded_evidence=True,
    )
    assert str((sandbox / "application").resolve()) in root_contract.writable_roots

def test_copy_read_sources_includes_task_context_and_external_source(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    sandbox = tmp_path / "sandbox"
    context_path = run / "reports" / "implementation-tasks" / "orders.context.json"
    source_path = run / "reports" / "testing-runtime.log"
    context_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(
        json.dumps({"readSourcePaths": ["reports/testing-runtime.log"]}),
        encoding="utf-8",
    )
    source_path.write_text("test evidence", encoding="utf-8")

    _copy_read_sources(
        run,
        sandbox,
        {"context_file": "reports/implementation-tasks/orders.context.json"},
    )

    assert (sandbox / "reports/implementation-tasks/orders.context.json").read_text(
        encoding="utf-8"
    ) == context_path.read_text(encoding="utf-8")
    assert (sandbox / "reports/testing-runtime.log").read_text(encoding="utf-8") == (
        "test evidence"
    )


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


def test_bounded_owner_upstream_gap_after_recovery_preserves_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "backend-implementation",
            "owner": "backend",
            "allowed_write_roots": [str(Path(source_path).parent)],
            "source_refs": ["UC-12"],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    context_path = run / task["context_file"]
    context_path.write_text(json.dumps({"readSourcePaths": []}), encoding="utf-8")
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    monkeypatch.setattr(
        "app.implementation.agents.runtime.settings.implementation_openhands_canary",
        False,
    )

    gap_session = SimpleNamespace(result=None)

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox
            self.state = SimpleNamespace(execution_status=None)
            self.run_count = 0

        def send_message(self, _message: str) -> None:
            pass

        def run(self) -> None:
            from openhands.sdk.conversation.state import ConversationExecutionStatus

            self.run_count += 1
            (self.sandbox / source_path).write_text(
                "class OrderService { int unpromotedCandidate; }", encoding="utf-8"
            )
            if self.run_count == 1:
                self.state.execution_status = ConversationExecutionStatus.STUCK
                return
            gap_session.result = UpstreamGap(
                summary="The response rule is not specified.", source_ref="UC-12"
            )
            self.state.execution_status = ConversationExecutionStatus.FINISHED

        def close(self) -> None:
            pass

    received_source_refs: list[str] | None = None

    def create_conversation(sandbox: Path, *_args, **kwargs):
        nonlocal received_source_refs
        received_source_refs = kwargs["upstream_gap_source_refs"]
        executor = SimpleNamespace(session=gap_session)
        return FakeConversation(sandbox), SimpleNamespace(
            _tools={UPSTREAM_GAP_TOOL_NAME: SimpleNamespace(executor=executor)}
        )

    connection = LlmConnection(
        provider="openrouter",
        api_key="approved-key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
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
        patch("app.implementation.agents.runtime.openhands_connection", return_value=connection),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch("app.implementation.agents.runtime.verify_agent_workspace") as verify,
    ):
        result = execute_openhands_task(run, task_id)

    verify.assert_not_called()
    assert result["status"] == "NEEDS_INPUT"
    assert received_source_refs == ["UC-12"]
    assert result["upstreamGap"]["sourceRef"] == "UC-12"
    assert result["candidateEvidence"]["changedFiles"] == [source_path]
    assert source.read_text(encoding="utf-8") == "class OrderService {}"


def test_verify_or_repair_passes_before_openhands_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    task_path = run / 'reports/implementation-tasks/order.task.json'
    task = json.loads(task_path.read_text(encoding='utf-8'))
    task.update(
        {
            'task_type': 'integration-implementation',
            'owner': 'implementation',
            'completion_mode': 'verify-or-repair',
            'source_refs': ['use_case:UC-12'],
        }
    )
    task_path.write_text(json.dumps(task), encoding='utf-8')
    (run / 'reports/run-manifest.json').write_text(
        json.dumps({'implementation_tasks': [task]}), encoding='utf-8'
    )
    (run / task['context_file']).write_text(
        json.dumps({'readSourcePaths': [source_path]}), encoding='utf-8'
    )
    monkeypatch.setenv('EASYDEP_FIXED_LINUX_RUNNER', '1')
    monkeypatch.delenv('EASYDEP_DEMO_SKIP_VALIDATION', raising=False)
    evidence = {'command': ['thin-integration'], 'exitCode': 0}

    with (
        patch(
            'app.implementation.agents.task_check.verify_agent_workspace',
            return_value=evidence,
        ) as verify,
        patch(
            'app.implementation.agents.runtime.openhands_connection',
            side_effect=AssertionError('verify-only completion must not create a connection'),
        ) as connection,
    ):
        result = execute_openhands_task(run, task_id)

    verify.assert_called_once()
    assert verify.call_args.args[0] != run
    assert verify.call_args.args[0].name != 'application'
    connection.assert_not_called()
    assert result['status'] == 'SUCCEEDED'
    assert result['completionPath'] == 'verify-only'
    assert result['agentInvoked'] is False
    assert result['initialVerification']['status'] == 'PASSED'
    assert result['verification']['reusedFromTaskCheck'] is True
    assert result['conversationId'] is None
    assert not verify.call_args.args[0].exists()


def test_verify_or_repair_npm_infrastructure_failure_skips_openhands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    task_path = run / 'reports/implementation-tasks/order.task.json'
    task = json.loads(task_path.read_text(encoding='utf-8'))
    task.update({'task_type': 'integration-implementation', 'owner': 'implementation', 'completion_mode': 'verify-or-repair'})
    task_path.write_text(json.dumps(task), encoding='utf-8')
    (run / 'reports/run-manifest.json').write_text(json.dumps({'implementation_tasks': [task]}), encoding='utf-8')
    (run / task['context_file']).write_text(json.dumps({'readSourcePaths': [source_path]}), encoding='utf-8')
    monkeypatch.setenv('EASYDEP_FIXED_LINUX_RUNNER', '1')
    checked: list[Path] = []

    def timed_out(session: TaskCheckSession) -> tuple[bool, str]:
        checked.append(session.sandbox)
        return False, 'npm ci timed out'

    with (
        patch('app.implementation.agents.runtime.TaskCheckSession.run', timed_out),
        patch('app.implementation.agents.runtime.openhands_connection', side_effect=AssertionError('must not repair infrastructure')) as connection,
        pytest.raises(OwnerConversationIncomplete, match='npm ci timed out'),
    ):
        execute_openhands_task(run, task_id)
    connection.assert_not_called()
    assert checked and not checked[0].exists()


def test_verify_or_repair_failure_result_preserves_initial_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "integration-implementation",
            "owner": "implementation",
            "completion_mode": "verify-or-repair",
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    (run / "reports/run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    (run / task["context_file"]).write_text(
        json.dumps({"readSourcePaths": [source_path]}), encoding="utf-8"
    )
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    diagnosis = "application startup failed: RootCauseSentinel"

    class IncompleteConversation:
        def __init__(self) -> None:
            self.state = SimpleNamespace(
                execution_status=ConversationExecutionStatus.IDLE
            )

        def send_message(self, _message: str) -> None:
            pass

        def run(self) -> None:
            self.state.execution_status = ConversationExecutionStatus.ERROR

        def close(self) -> None:
            pass

    connection = SimpleNamespace(
        api_key="approved-key",
        provider="openrouter",
        model="openai/gpt-4o-mini",
        display_name=lambda: "OpenRouter",
        litellm_model=lambda: "openrouter/openai/gpt-4o-mini",
    )
    with (
        patch(
            "app.implementation.agents.runtime.TaskCheckSession.run",
            return_value=(False, diagnosis),
        ),
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
            return_value=connection,
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            return_value=(IncompleteConversation(), SimpleNamespace(_tools={})),
        ),
        pytest.raises(OwnerConversationIncomplete),
    ):
        execute_openhands_task(run, task_id)

    result = json.loads(
        (run / f"reports/agent-executions/{task_id}.result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["status"] == "INTERRUPTED"
    assert result["completionPath"] == "repair-agent"
    assert result["initialVerification"] == {
        "status": "FAILED",
        "diagnosis": diagnosis,
    }


def test_verify_or_repair_typed_startup_environment_failure_skips_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "integration-implementation",
            "owner": "implementation",
            "completion_mode": "verify-or-repair",
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    (run / "reports/run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    (run / task["context_file"]).write_text(
        json.dumps({"readSourcePaths": [source_path]}), encoding="utf-8"
    )
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)
    evidence = {
        "command": ["application-startup", "health-check"],
        "exitCode": 1,
        "stderr": "The Docker daemon is unavailable",
        "applicationStartup": {
            "status": "FAILED",
            "defectClass": "ENVIRONMENT_DEFECT",
            "applicationLog": "",
        },
    }

    def fail_with_environment_evidence(session: TaskCheckSession):
        session._last_evidence = evidence
        return False, "TASK CHECK FAILED\nThe Docker daemon is unavailable"

    with (
        patch(
            "app.implementation.agents.runtime.TaskCheckSession.run",
            fail_with_environment_evidence,
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=AssertionError("environment failure must not reach the agent"),
        ) as create_conversation,
        pytest.raises(OwnerConversationIncomplete) as raised,
    ):
        execute_openhands_task(run, task_id)

    create_conversation.assert_not_called()
    assert raised.value.evidence["applicationStartup"]["defectClass"] == (
        "ENVIRONMENT_DEFECT"
    )


def test_semantic_admission_does_not_block_integration_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, task_id, source_path, _source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "integration-implementation",
            "owner": "implementation",
            "completion_mode": "verify-or-repair",
            "source_refs": ["use_case_spec:UC-12"],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    (run / "reports/run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    context = {"readSourcePaths": [source_path]}
    (run / task["context_file"]).write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)

    with (
        patch(
            "app.implementation.agents.task_check.verify_agent_workspace",
            return_value={"command": ["thin-integration"], "exitCode": 0},
        ) as verify,
        patch(
            "app.implementation.agents.runtime.openhands_connection",
            side_effect=AssertionError("successful integration check must not invoke an agent"),
        ) as connection,
    ):
        result = execute_openhands_task(run, task_id)

    verify.assert_called_once()
    connection.assert_not_called()
    assert result["status"] == "SUCCEEDED"
    assert result["completionPath"] == "verify-only"
    assert result["agentInvoked"] is False


def test_integration_fqcn_rtm_routes_unique_failure_owner_without_widening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    task_dir = reports / "implementation-tasks"
    task_dir.mkdir()
    integration_id = "implement-vertical-integration"
    source_id = "implement-backend-service"
    source_path = (
        "application/src/main/java/com/easydep/app/application/impl/"
        "WaitlistControlService.java"
    )
    integration_path = "application/frontend/src/api.ts"
    source = run / source_path
    source.parent.mkdir(parents=True)
    source.write_text("class WaitlistControlService {}", encoding="utf-8")
    integration = {
        "task_id": integration_id,
        "task_type": "integration-implementation",
        "owner": "integration",
        "completion_mode": "verify-or-repair",
        "prompt_file": "reports/implementation-tasks/integration.prompt.md",
        "context_file": "reports/implementation-tasks/integration.context.json",
        "prompt_sha256": "prompt-hash",
        "llm": {"temperature": 0.0, "maxOutputTokens": 1024},
        "verification_profile": {},
        "allowed_write_paths": [integration_path],
        "required_output_paths": [integration_path],
    }
    backend = {
        "task_id": source_id,
        "task_type": "backend-implementation",
        "owner": "backend",
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
        "source_refs": ["use_case:UC-1"],
    }
    (task_dir / "integration.prompt.md").write_text("Integrate the application.", encoding="utf-8")
    (task_dir / "integration.context.json").write_text(
        json.dumps({"readSourcePaths": [integration_path]}), encoding="utf-8"
    )
    (task_dir / "integration.task.json").write_text(json.dumps(integration), encoding="utf-8")
    manifest = reports / "run-manifest.json"
    manifest.write_text(json.dumps({"implementation_tasks": [integration, backend]}), encoding="utf-8")
    rtm = reports / "rtm-traceability-map.json"
    rtm.write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "target_file": source_path,
                        "taskId": source_id,
                        "sourceRefs": ["use_case:UC-1"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    evidence: dict[str, object] = {
        "command": ["application-startup", "health-check"],
        "exitCode": 1,
        "stderr": "Cannot subclass final class com.easydep.app.application.impl.WaitlistControlService",
        "applicationStartup": {"status": "FAILED", "applicationLog": "startup failed"},
    }
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    monkeypatch.delenv("EASYDEP_DEMO_SKIP_VALIDATION", raising=False)

    def failed_precheck(session: TaskCheckSession):
        session._last_evidence = evidence
        return False, str(evidence["stderr"])

    with (
        patch("app.implementation.agents.runtime.TaskCheckSession.run", failed_precheck),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=AssertionError("uniquely attributed failures bypass the integration editor"),
        ) as create_conversation,
        pytest.raises(WorkspaceVerificationError) as raised,
    ):
        execute_openhands_task(run, integration_id)
    create_conversation.assert_not_called()
    routed_evidence = raised.value.evidence
    assert routed_evidence["repairTaskId"] == source_id
    assert routed_evidence["attributedTargetFile"] == source_path
    repair = schedule_cross_phase_repair(run, integration_id, routed_evidence)
    assert repair is not None
    assert repair["ownerTaskIds"] == [source_id]
    assert repair["repairPaths"] == [source_path]
    assert integration_path not in repair["repairPaths"]

    ambiguous = json.loads(rtm.read_text(encoding="utf-8"))
    ambiguous["mappings"].append(
        {
            "target_file": source_path,
            "taskId": "another-declared-owner",
            "sourceRefs": ["use_case:UC-2"],
        }
    )
    rtm.write_text(json.dumps(ambiguous), encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    integration,
                    backend,
                    {
                        **backend,
                        "task_id": "another-declared-owner",
                        "source_refs": ["use_case:UC-2"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert runtime_module._integration_source_owner_attribution(
        run, integration_id, evidence, str(evidence["stderr"])
    ) is None
    fallback = schedule_cross_phase_repair(
        run,
        integration_id,
        {key: value for key, value in evidence.items() if key not in {"repairTaskId", "attributedTargetFile"}},
    )
    assert fallback is not None
    assert fallback["ownerTaskIds"] == [integration_id]
    assert fallback["repairPaths"] == []


def test_demo_skip_avoids_upstream_gap_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, task_id, _source_path, _source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task.update(
        {
            "task_type": "integration-implementation",
            "owner": "implementation",
            "source_refs": ["use_case_spec:UC-12"],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    (run / "reports/run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    context = {"readSourcePaths": []}
    (run / task["context_file"]).write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    monkeypatch.setenv("EASYDEP_DEMO_SKIP_VALIDATION", "true")
    monkeypatch.setattr(
        "app.implementation.agents.runtime.settings.implementation_openhands_canary",
        False,
    )

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            from openhands.sdk.conversation.state import ConversationExecutionStatus

            self.sandbox = sandbox
            self.state = SimpleNamespace(
                execution_status=ConversationExecutionStatus.FINISHED
            )

        def send_message(self, _message: str) -> None:
            pass

        def run(self) -> None:
            pass

        def close(self) -> None:
            pass

    connection = LlmConnection(
        provider="openrouter",
        api_key="approved-key",
        base_url="https://example.invalid/v1",
        model="openai/gpt-oss-20b",
        litellm_provider="openrouter",
    )
    conversation_options: dict[str, object] = {}

    def create_conversation(sandbox: Path, *_args: object, **kwargs: object):
        conversation_options.update(kwargs)
        return FakeConversation(sandbox), SimpleNamespace(_tools={})

    with (
        patch("app.implementation.agents.runtime.register_upstream_gap_tool") as register_gap,
        patch("app.implementation.agents.runtime.reported_upstream_gap") as reported_gap,
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch("app.implementation.agents.runtime.openhands_connection", return_value=connection),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ) as create,
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            return_value={"exitCode": 0},
        ),
    ):
        result = execute_openhands_task(run, task_id)

    register_gap.assert_not_called()
    reported_gap.assert_not_called()
    create.assert_called_once()
    assert conversation_options["upstream_gap_source_refs"] is None
    assert result["status"] == "SUCCEEDED"


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
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace"
        ) as verify,
        pytest.raises(WorkspaceVerificationError),
    ):
        execute_openhands_task(run, task_id)

    verify.assert_not_called()
    assert source.read_text(encoding="utf-8") == "class OrderService {}"
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


def test_stuck_after_successful_check_promotes_without_finish_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)
    task_path = run / "reports/implementation-tasks/order.task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    immutable_file = "application/src/main/java/com/example/bce/GeneratedContract.java"
    immutable_directory = "application/src/main/java/com/example/bce/generated"
    missing_immutable = "application/src/main/java/com/example/bce/MissingContract.java"
    immutable_source = run / immutable_file
    immutable_source.parent.mkdir(parents=True, exist_ok=True)
    immutable_source.write_text("interface GeneratedContract {}", encoding="utf-8")
    task.update(
        {
            "task_type": "backend-implementation",
            "owner": "backend",
            "allowed_write_roots": [Path(source_path).parent.as_posix()],
            "immutable_paths": [immutable_file, immutable_directory, missing_immutable],
        }
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")
    (run / task["context_file"]).write_text(
        json.dumps({"readSourcePaths": []}), encoding="utf-8"
    )
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
            self.state.execution_status = ConversationExecutionStatus.STUCK

        def close(self) -> None:
            pass

    conversation: FakeConversation | None = None
    conversation_options: dict[str, object] = {}

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        nonlocal conversation
        conversation_options.update(_kwargs)
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
        patch(
            "app.implementation.agents.runtime.has_successful_task_check",
            return_value=True,
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            return_value={"command": ["gradle", "test"], "exitCode": 0},
        ),
    ):
        result = execute_openhands_task(run, task_id)

    assert conversation is not None
    assert conversation.run_count == 1
    assert len(conversation.messages) == 1
    assert result["stuckRecoveryUsed"] is False
    assert result["finishRecoveryUsed"] is False
    assert result["executionStatus"] == "stuck"
    assert source.read_text(encoding="utf-8") == "class OrderService {}"
    # A bounded backend owner starts from its task context and owned source.
    # Broader evidence remains an explicit handoff instead of an open-ended scan.
    assert conversation_options["readable_files"] == sorted(
        [
            str((conversation.sandbox / task["context_file"]).resolve()),
            str((conversation.sandbox / source_path).resolve()),
            str((conversation.sandbox / immutable_file).resolve()),
        ]
    )
    assert str((conversation.sandbox / immutable_directory).resolve()) not in conversation_options["readable_files"]
    assert str((conversation.sandbox / missing_immutable).resolve()) not in conversation_options["readable_files"]
    assert conversation_options["editable_files"] == [
        str((conversation.sandbox / source_path).resolve())
    ]
    assert conversation_options["editable_roots"] == [
        str((conversation.sandbox / Path(source_path).parent).resolve())
    ]


def test_successful_task_check_guard_stops_post_check_owner_exploration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    guard = SuccessfulTaskCheckGuard(
        tmp_path,
        "frontend-implementation",
        ["application/frontend/src/App.tsx"],
    )
    guard.bind(conversation)
    monkeypatch.setattr(
        "app.implementation.agents.runtime.has_successful_task_check",
        lambda *_args: True,
    )

    guard(
        SimpleNamespace(
            tool_name="run_task_check",
            observation=SimpleNamespace(is_error=False),
        )
    )

    assert guard.triggered is True
    assert conversation.state.execution_status is ConversationExecutionStatus.STUCK


def test_successful_task_check_guard_ignores_failed_check_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    guard = SuccessfulTaskCheckGuard(
        tmp_path,
        "frontend-implementation",
        ["application/frontend/src/App.tsx"],
    )
    guard.bind(conversation)
    monkeypatch.setattr(
        "app.implementation.agents.runtime.has_successful_task_check",
        lambda *_args: pytest.fail("failed checks must not be reused"),
    )

    guard(
        SimpleNamespace(
            tool_name="run_task_check",
            observation=SimpleNamespace(is_error=True),
        )
    )

    assert guard.triggered is False
    assert conversation.state.execution_status is ConversationExecutionStatus.RUNNING


def test_successful_task_check_guard_ignores_changed_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    guard = SuccessfulTaskCheckGuard(
        tmp_path,
        "frontend-implementation",
        ["application/frontend/src/App.tsx"],
    )
    guard.bind(conversation)
    monkeypatch.setattr(
        "app.implementation.agents.runtime.has_successful_task_check",
        lambda *_args: False,
    )

    guard(
        SimpleNamespace(
            tool_name="run_task_check",
            observation=SimpleNamespace(is_error=False),
        )
    )

    assert guard.triggered is False
    assert conversation.state.execution_status is ConversationExecutionStatus.RUNNING


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
    finally:
        resumed.close()


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
        owner_files=["application/src/main/java/com/example/OrderService.java"],
    )

    assert f"`{tmp_path.resolve()}`" in guidance
    assert "Backend project root: `application`" in guidance
    assert f"cd {tmp_path.resolve() / 'application'} && gradle test --build-cache" in guidance
    assert "SPRING_PROFILES_ACTIVE=test" in guidance
    assert "run the canonical verification once" in guidance
    assert "Do not disable tests or alter test reporting" in guidance
    assert "never change or delete an existing public signature" in guidance
    assert "Choose one legal conventional implementation" in guidance
    assert "Agent: Implementation" in guidance
    assert "Current state: EXECUTE" in guidance
    assert "Writable task files (authoritative exact-file scope)" in guidance
    assert str(
        tmp_path.resolve()
        / "application/src/main/java/com/example/OrderService.java"
    ) in guidance
    assert str(
        tmp_path.resolve() / "application/src/main/java/com/example"
    ) in guidance
    assert "Completion markers identify required bodies" in guidance
    assert "Every path not listed above" in guidance
    assert 'command="str_replace" with old_str and new_str' in guidance
    assert 'command="create" with file_text' in guidance
    assert 'command="edit" and old_string/new_string are invalid' in guidance
    assert "report_upstream_gap" not in guidance
    assert "permission denied" not in guidance.casefold()
    assert OWNER_TURN_ITERATIONS < 500

    bounded = _owner_workspace_guidance(
        "backend-implementation",
        tmp_path,
        [],
        owner_files=["application/src/main/java/com/example/Order.java"],
        read_files=["/work/task/application/src/main/java/com/example/OrderRepository.java"],
        bounded_evidence=True,
    )
    assert "Requirements, caller-visible APIs, and observable behavior are hard constraints" in bounded
    assert "class, sequence, RTM, collaborator, and wiring details" in bounded
    assert "may be incomplete" in bounded
    assert "one writable source containing an assigned completion marker" in bounded
    assert "first legal edit from local declarations and assigned task behavior" in bounded
    assert "consult only the listed operation contract and declared dependency sources" in bounded
    assert "Interaction hints are behavioral evidence" in bounded
    assert "do not inject dependencies or alter BCE ownership solely because of a hint" in bounded
    assert "missing collaborator or wiring entry alone is not an upstream gap" in bounded
    assert "existing dependency APIs" in bounded
    assert "required public input, output, or externally visible behavior" in bounded
    assert "absent or contradictory" in bounded
    assert "Do not reread unchanged files" in bounded
    assert "implementation marker not assigned to this task" in bounded
    assert "Readable evidence files (authoritative exact-file scope)" in bounded
    assert "/work/task/application/src/main/java/com/example/OrderRepository.java" in bounded
    assert "use `grep` when searching a containing directory" in bounded
    assert "readSourcePaths" not in bounded
    assert "direct-call argument" not in bounded
    assert "effect owners" not in bounded
    assert "assigned main-source markers" not in bounded
    assert "source indexes" not in bounded
    assert "raw design inputs" not in bounded
    assert "unrelated generated files" not in bounded

    for unit_task, framework in (
        ("backend-unit-test", "JUnit"),
        ("frontend-unit-test", "Vitest"),
    ):
        unit_guidance = _owner_workspace_guidance(
            unit_task,
            tmp_path,
            [],
            owner_tool_mode="editor",
            owner_files=["assigned-test-file"],
            read_files=["application/src/main/java/com/example/OrderService.java"],
            bounded_evidence=True,
        )
        assert "assigned completion marker" not in unit_guidance
        assert "This task authors one focused" in unit_guidance
        assert framework in unit_guidance
        assert "implementation subject" in unit_guidance
        unit_boundary = _owner_evidence_boundary_message([], task_type=unit_task)
        assert "create the assigned test file" in unit_boundary
        assert "source and API evidence are read-only" in unit_boundary


def test_owner_prompt_requires_an_immediate_edit_and_narrow_stuck_recovery() -> None:
    assert "operation contract" in OWNER_INITIAL_ACTION_MESSAGE
    assert "one writable source" in OWNER_INITIAL_ACTION_MESSAGE
    assert "assigned completion marker" in OWNER_INITIAL_ACTION_MESSAGE
    assert "local declarations and assigned task behavior" in OWNER_INITIAL_ACTION_MESSAGE
    assert "listed operation contract and declared dependency sources" in OWNER_INITIAL_ACTION_MESSAGE
    assert "Interaction hints are behavioral evidence" in OWNER_INITIAL_ACTION_MESSAGE
    assert "do not inject dependencies or alter BCE ownership solely because of a hint" in OWNER_INITIAL_ACTION_MESSAGE
    assert "first legal file_editor edit" in OWNER_INITIAL_ACTION_MESSAGE
    assert "Do not restate the task" in OWNER_STUCK_RECOVERY_MESSAGE
    assert "already inspected writable target" in OWNER_STUCK_RECOVERY_MESSAGE
    assert "missing evidence concisely" in OWNER_STUCK_RECOVERY_MESSAGE
    assert "reread" not in OWNER_STUCK_RECOVERY_MESSAGE
    assert "missing collaborator or wiring entry alone is not an upstream gap" in OWNER_GAP_RECOVERY_MESSAGE
    assert "required public input, output, or externally visible behavior" in OWNER_GAP_RECOVERY_MESSAGE
    assert "absent or contradictory" in OWNER_GAP_RECOVERY_MESSAGE


def test_owner_evidence_boundary_prompt_handles_empty_and_focused_test_paths() -> None:
    empty = _owner_evidence_boundary_message([])
    assert "complete boundary" in empty
    assert "Do not guess or probe" in empty
    assert "No focused test is supplied" in empty
    assert "Do not search test directories" in empty

    focused = _owner_evidence_boundary_message([
        "application/src/test/java/example/CalculationControlServiceTest.java",
    ])
    assert "Focused test paths are supplied" in focused
    assert "CalculationControlServiceTest.java" in focused
    assert "Use only these focused test paths" in focused


def test_editor_prompt_evidence_embeds_only_bounded_dependency_java_sources(
    tmp_path: Path,
) -> None:
    target = tmp_path / "application/src/main/java/example/Target.java"
    dependency = tmp_path / "application/src/main/java/example/Dependency.java"
    outside = tmp_path.parent / "outside.java"
    target.parent.mkdir(parents=True)
    target.write_text("class Target {}\n", encoding="utf-8")
    dependency.write_text("class Dependency {}\n", encoding="utf-8")
    outside.write_text("class Outside {}\n", encoding="utf-8")

    evidence = _editor_read_source_evidence(
        tmp_path,
        {
            "readSourcePaths": [
                "application/src/main/java/example/Target.java",
                "application/src/main/java/example/Dependency.java",
                "../outside.java",
                "reports/operation.json",
            ]
        },
        [str(target.resolve())],
    )

    assert "`application/src/main/java/example/Dependency.java`" in evidence
    assert "class Dependency" in evidence
    assert "Target.java`" not in evidence
    assert "Outside" not in evidence
    assert "1 included, 0 omitted" in evidence


def test_editor_prompt_evidence_prioritizes_small_sources_within_finite_body_cap(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "application/src/main/java/example"
    target = source_root / "Target.java"
    large = source_root / "A.java"
    first = source_root / "B.java"
    second = source_root / "C.java"
    source_root.mkdir(parents=True)
    target.write_text("class Target {}", encoding="utf-8")
    large.write_text("a" * (64 * 1024), encoding="utf-8")
    first.write_text("class CourseEntity { String id; }", encoding="utf-8")
    second.write_text("interface CourseRepository {}", encoding="utf-8")

    evidence = _editor_read_source_evidence(
        tmp_path,
        {"readSourcePaths": [
            "application/src/main/java/example/A.java",
            "application/src/main/java/example/C.java",
            "application/src/main/java/example/B.java",
        ]},
        [str(target.resolve())],
    )

    assert "`application/src/main/java/example/A.java`" not in evidence
    assert "`application/src/main/java/example/B.java`" in evidence
    assert "`application/src/main/java/example/C.java`" in evidence
    assert "CourseEntity" in evidence
    assert "CourseRepository" in evidence
    assert "3 included, 1 omitted" in evidence
    assert "UTF-8 body cap 65536 bytes" in evidence


def test_typed_no_action_response_starts_bounded_recovery() -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus
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

    guard(empty)
    guard(corrective_user_message)

    assert guard.triggered is True
    assert guard.max_consecutive_count == 1
    assert conversation.state.execution_status is ConversationExecutionStatus.STUCK
    assert _conversation_terminal_failure(conversation) is True


def test_owner_finish_recovery_only_targets_nonterminal_nonstuck_statuses() -> None:
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    conversation = SimpleNamespace(
        state=SimpleNamespace(execution_status=ConversationExecutionStatus.RUNNING)
    )
    assert _conversation_needs_finish_recovery(conversation) is True

    conversation.state.execution_status = ConversationExecutionStatus.FINISHED
    assert _conversation_needs_finish_recovery(conversation) is False

    conversation.state.execution_status = ConversationExecutionStatus.STUCK
    assert _conversation_needs_finish_recovery(conversation) is False


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
    )

    assert failures == []
    assert observed["status"] == "RUNNING"
    assert observed["attempts"] == 2
    assert observed["lastError"] is None


def test_task_batch_persists_executor_timing_for_each_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.implementation.workflows.coordinator as coordinator_module

    task = {"task_id": "implement-backend", "status": "PENDING", "attempts": 0}
    state = {"tasks": [task]}
    persisted_states: list[dict[str, object]] = []

    monkeypatch.setattr(
        coordinator_module,
        "_write_json_atomic",
        lambda _path, value: persisted_states.append(json.loads(json.dumps(value))),
    )
    monkeypatch.setattr(coordinator_module, "_task_output_hashes", lambda *_args: {})

    def execute(_run_root: Path, _task_id: str) -> dict[str, object]:
        timing = persisted_states[-1]["tasks"][0]["executionAttempts"][-1]
        assert timing["executorReturnedAt"] is None
        return {"status": "SUCCEEDED"}

    failures = _execute_task_batch(Path("unused-run-root"), state, [task], execute)

    # The workflow caller's next existing state write persists timings measured
    # after dispatch/completion writes without adding a write inside the batch.
    coordinator_module._write_json_atomic(Path("unused-state"), state)

    assert failures == []
    timing = task["executionAttempts"][-1]
    assert timing["attempt"] == 1
    assert timing["dispatchedAt"]
    assert timing["executorReturnedAt"] >= timing["dispatchedAt"]
    assert isinstance(timing["executorDurationMs"], float)
    assert isinstance(timing["dispatchStateWriteDurationMs"], float)
    assert isinstance(timing["outputHashDurationMs"], float)
    assert isinstance(timing["completionStateWriteDurationMs"], float)
    assert persisted_states[-1]["tasks"][0]["executionAttempts"] == task[
        "executionAttempts"
    ]


def test_reconcile_preserves_a_typed_upstream_gap_as_needs_input(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    executions = reports / "agent-executions"
    executions.mkdir(parents=True)
    task_id = "implement-backend-application"
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": task_id,
                        "task_type": "backend-implementation",
                        "prompt_sha256": "prompt-v1",
                        "required_output_paths": [],
                        "allowed_write_paths": [],
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
                    {"task_id": task_id, "status": "SUCCEEDED", "attempts": 1}
                ]
            }
        ),
        encoding="utf-8",
    )
    (executions / f"{task_id}.result.json").write_text(
        json.dumps(
            {
                "status": "NEEDS_INPUT",
                "promptSha256": "prompt-v1",
                "upstreamGap": {
                    "summary": "The required response rule is absent.",
                    "sourceRef": "UC-12",
                },
                "candidateEvidence": {"changedFiles": ["application/OrderService.java"]},
            }
        ),
        encoding="utf-8",
    )

    state = reconcile_workflow_state(tmp_path)

    assert state["status"] == "NEEDS_INPUT"
    assert state["tasks"][0]["status"] == "NEEDS_INPUT"
    assert state["blockingDetails"] == [
        {
            "kind": "upstream_contract_gap",
            "taskId": task_id,
            "sourceRef": "UC-12",
            "summary": "The required response rule is absent.",
        }
    ]


def test_task_batch_keeps_needs_input_out_of_failed_tasks(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    task = {"task_id": "implement-backend-application", "status": "PENDING", "attempts": 0}
    state = {"tasks": [task]}

    failures = _execute_task_batch(
        tmp_path,
        state,
        [task],
        lambda *_args: {
            "status": "NEEDS_INPUT",
            "upstreamGap": {"summary": "Missing rule", "sourceRef": "UC-12"},
            "candidateEvidence": {"changedFiles": []},
        },
    )

    assert failures == []
    assert task["status"] == "NEEDS_INPUT"
    assert task["upstreamGap"]["sourceRef"] == "UC-12"


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
                "operations": [
                    {
                        "operationId": "Order::describe()",
                        "name": "describe",
                        "parameters": [],
                        "returnType": "String",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
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
                    "requirement_ids": ["FR-ORDER"],
                    "main_scenario": [
                        {"step_number": 1, "sentence": "The customer places an order."}
                    ],
                    "repair_iters": 7,
                    "repair_history": {"marker": "INTERNAL-USE-CASE-REPAIR"},
                },
                {
                    "id": "UC2",
                    "use_case_id": "UC2",
                    "name": "Cancel order",
                    "requirement_ids": ["FR-CANCEL"],
                },
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
                        "control_binding": {"control": "OrderControl", "method": "place"},
                    },
                    {
                        "method": "DELETE",
                        "path": "/orders/{id}",
                        "operation_id": "cancelOrder",
                        "use_case_ids": ["UC2"],
                        "control_binding": {"control": "CancelControl", "method": "cancel"},
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
    assert task_types == {
        "backend-implementation",
        "backend-unit-test",
        "frontend-implementation",
        "frontend-unit-test",
        "integration-implementation",
    }
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

    backends = [task for task in tasks if task["task_type"] == "backend-implementation"]
    backend_tests = [task for task in tasks if task["task_type"] == "backend-unit-test"]
    frontends = [task for task in tasks if task["task_type"] == "frontend-implementation"]
    frontend_tests = [task for task in tasks if task["task_type"] == "frontend-unit-test"]
    frontend = frontends[0]
    integration = next(
        task for task in tasks if task["task_type"] == "integration-implementation"
    )
    assert len(tasks) == len(backends) + len(backend_tests) + len(frontends) + len(frontend_tests) + 1
    assert len(backends) >= 3
    assert len(backend_tests) == len(backends)
    assert len(frontends) >= 1
    assert len(frontend_tests) == len(frontends)
    assert not any(task["task_id"] == "implement-use-cases-stale-common" for task in tasks)
    controller_backends = [
        task
        for task in backends
        if any("/adapter/in/web/" in path for path in task["required_output_paths"])
    ]
    domain_backends = [task for task in backends if task not in controller_backends]
    assert len(controller_backends) >= 2
    assert len(domain_backends) >= 1
    assert [task["depends_on"] for task in backends] == [[] for _ in backends]
    assert all(len(task["depends_on"]) == 1 for task in backend_tests)
    assert all(len(task["depends_on"]) == 1 for task in frontend_tests)
    assert all(task["allowed_write_paths"] == task["required_test_paths"] for task in backend_tests)
    assert all(task["allowed_write_paths"] == task["required_test_paths"] for task in frontend_tests)
    assert state["nextRunnableTasks"] == [task["task_id"] for task in backends]
    assert {
        use_case_id
        for task in backends
        for use_case_id in task["use_case_ids"]
    } == {"UC1", "UC2"}
    assert frontend["depends_on"] == []
    assert integration["owner"] == "implementation"
    assert integration["depends_on"] == [
        *(task["task_id"] for task in backends),
        *(task["task_id"] for task in frontends),
        *(task["task_id"] for task in backend_tests),
        *(task["task_id"] for task in frontend_tests),
    ]
    assert next(
        phase for phase in state["phases"] if phase["phaseId"] == "integration"
    )["taskIds"] == [integration["task_id"]]
    assert integration["required_output_paths"] == []
    assert integration["allowed_write_roots"] == []
    assert set(integration["allowed_write_paths"]) <= {
        "application/frontend/src/api.ts",
        "application/frontend/src/config.ts",
    }
    assert "application/src/main/resources/application.yml" not in integration[
        "allowed_write_paths"
    ]
    assert all("/src/test/" not in path for path in integration["allowed_write_paths"])
    assert not any(
        path.startswith("application/frontend/src/generated")
        for path in integration["allowed_write_paths"]
    )
    assert frontend['completion_mode'] == 'agent'
    assert integration['completion_mode'] == 'verify-or-repair'
    integration_context = json.loads(
        (run / integration["context_file"]).read_text(encoding="utf-8")
    )
    assert integration_context["batchUseCaseIds"] == ["UC1", "UC2"]
    assert integration_context["traceEvidence"]["ownerTaskIds"] == integration[
        "depends_on"
    ]
    assert "ownerContextPaths" not in integration_context
    assert "priorVerificationPaths" not in integration_context
    assert not {task["context_file"] for task in [*backends, *frontends]}.intersection(
        integration_context["readSourcePaths"]
    )
    assert all(
        set(task["required_output_paths"]) <= set(integration_context["readSourcePaths"])
        for task in [*backends, *frontends]
    )
    integration_prompt = (run / integration["prompt_file"]).read_text(
        encoding="utf-8"
    )
    assert "one representative happy path" in integration_prompt
    assert "Semantic integration admission has already accepted" in integration_prompt
    assert "resolve only concrete connector mechanics" in integration_prompt
    assert "defect in any read-only owner" in integration_prompt
    assert "Requirements, Design, OpenAPI, generated clients" in integration_prompt
    assert "application/deployment/runtime/compose.yaml" not in integration[
        "allowed_write_paths"
    ]
    if (run / "application/deployment/runtime/compose.yaml").exists():
        assert "application/deployment/runtime/compose.yaml" in integration_context[
            "runtimeConfigPaths"
        ]
    if (run / "application/src/main/resources/application.yml").exists():
        assert "application/src/main/resources/application.yml" in integration_context[
            "readSourcePaths"
        ]
    if (run / "application/frontend/package.json").exists():
        assert "application/frontend/package.json" in integration_context[
            "readSourcePaths"
        ]
    integration_state = next(
        task for task in state["tasks"] if task["task_id"] == integration["task_id"]
    )
    assert integration_state["promptSha256"] != integration["prompt_sha256"]
    executions = run / "reports/agent-executions"
    executions.mkdir(exist_ok=True)
    for owner_task in [*backends, *frontends]:
        (executions / f"{owner_task['task_id']}.result.json").write_text(
            json.dumps(
                {
                    "status": "SUCCEEDED",
                    "promptSha256": owner_task["prompt_sha256"],
                }
            ),
            encoding="utf-8",
        )
    new_component = run / "application/frontend/src/components/OrderResult.tsx"
    new_component.parent.mkdir(parents=True, exist_ok=True)
    new_component.write_text("export const OrderResult = () => null;", encoding="utf-8")
    frontend_result_path = executions / f"{frontend['task_id']}.result.json"
    frontend_result = json.loads(frontend_result_path.read_text(encoding="utf-8"))
    frontend_result["changedFiles"] = [
        "application/frontend/src/components/OrderResult.tsx",
        "application/../outside.txt",
        "application/frontend/src/generated/apis/OrdersApi.ts",
    ]
    frontend_result_path.write_text(json.dumps(frontend_result), encoding="utf-8")
    evidence_paths = integration_evidence_paths(run, integration, integration_context)
    assert "application/frontend/src/components/OrderResult.tsx" in evidence_paths
    assert not {
        "application/../outside.txt",
        "application/frontend/src/generated/apis/OrdersApi.ts",
    }.intersection(evidence_paths)
    owners_completed = reconcile_workflow_state(run)
    current_integration = next(
        task
        for task in owners_completed["tasks"]
        if task["task_id"] == integration["task_id"]
    )
    (executions / f"{integration['task_id']}.result.json").write_text(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "promptSha256": current_integration["promptSha256"],
            }
        ),
        encoding="utf-8",
    )
    workflow_state_path = run / "reports/workflow-state.json"
    executed_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
    next(
        task
        for task in executed_state["tasks"]
        if task["task_id"] == integration["task_id"]
    )["status"] = "SUCCEEDED"
    workflow_state_path.write_text(json.dumps(executed_state), encoding="utf-8")
    reused = reconcile_workflow_state(run)
    assert next(
        task
        for task in reused["tasks"]
        if task["task_id"] == integration["task_id"]
    )["status"] == "SUCCEEDED"
    new_component.write_text(
        "export const OrderResult = () => <div>updated</div>;", encoding="utf-8"
    )
    invalidated = reconcile_workflow_state(run)
    assert next(
        task
        for task in invalidated["tasks"]
        if task["task_id"] == integration["task_id"]
    )["status"] == "PENDING"
    new_component.unlink()
    assert "application/frontend/src/components/OrderResult.tsx" in integration_evidence_paths(
        run, integration, integration_context
    )
    contexts = [
        json.loads((run / task["context_file"]).read_text(encoding="utf-8"))
        for task in backends
    ]
    assert all(context["useCaseIds"] == ["UC1", "UC2"] for context in contexts)
    assert {requirement_id for task in backends for requirement_id in task["requirement_ids"]} == {
        "FR-ORDER",
        "FR-CANCEL",
    }
    assert all("behaviorCapsule" not in context for context in contexts)
    assert all("requiredTestPath" not in context for context in contexts)
    assert all(task["required_test_paths"] == [] for task in backends)
    assert all(
        "focusedTestPaths" not in task.get("verification_profile", {})
        for task in backends
    )
    assert _regression_owner_task_id(
        run,
        {"testResults": "OrderApplicationTest.implementsContract: assertion failed"},
    ) == "backend-regression"
    generated_api = {
        "application/src/main/java/com/example/orders/api/OrdersApi.java",
        "application/src/main/java/com/example/orders/api/CancelApi.java",
    }
    assert all(
        not set(task["allowed_write_paths"]).intersection(generated_api)
        for task in backends
    )
    immutable_bce = {
        "application/src/main/java/com/example/orders/bce/OrderBoundary.java",
        "application/src/main/java/com/example/orders/bce/OrderControl.java",
        "application/src/main/java/com/example/orders/bce/CancelControl.java",
    }
    assert all(
        not set(task["allowed_write_paths"]).intersection(immutable_bce)
        for task in backends
    )
    persistence_root = (
        "application/src/main/java/com/example/orders/persistence"
    )
    assert all(persistence_root in task["immutable_paths"] for task in backends)
    assert all(
        "application/src/main/resources/db/migration" in task["immutable_paths"]
        for task in backends
    )
    assert not any(
        path == persistence_root or path.startswith(persistence_root + "/")
        for task in backends
        for path in task["allowed_write_paths"]
    )
    assert all(task["allowed_write_roots"] == [] for task in backends)
    assert all(task["allowed_write_roots"] == [] for task in frontends)
    assert all(
        len(task["allowed_write_paths"]) == 1
        and task["allowed_write_paths"][0].startswith("application/frontend/src/features/")
        and task["required_output_paths"] == task["allowed_write_paths"]
        and len(task["required_completion_markers"]) == 1
        for task in frontends
    )
    assert integration["verification_profile"]["requiredAbsentMarkers"] == [
        {
            "path": task["required_output_paths"][0],
            "markers": task["required_completion_markers"],
        }
        for task in frontends
    ]
    assert all("application/frontend/src/api.ts" in task["immutable_paths"] for task in frontends)
    assert "application/frontend/src/api.ts" in integration["allowed_write_paths"]
    domain_source_indexes = []
    for candidate in domain_backends:
        candidate_context = json.loads(
            (run / candidate["context_file"]).read_text(encoding="utf-8")
        )
        candidate_index = json.loads(
            (run / candidate_context["sourceIndexPath"]).read_text(encoding="utf-8")
        )
        if candidate_index["startingSourcePaths"] and candidate_index["methodContexts"]:
            domain_source_indexes.append(candidate_index)
    assert domain_source_indexes
    source_index = domain_source_indexes[0]
    assert source_index["hintsOnly"] is True
    assert source_index["startingSourcePaths"]
    assert source_index["methodContexts"]
    assert all(
        (run / item["path"]).is_file()
        for item in source_index["methodContexts"]
    )
    method_context = json.loads(
        (run / source_index["methodContexts"][0]["path"]).read_text(encoding="utf-8")
    )
    assert method_context["refs"]
    assert method_context["designInputs"] == source_index["designInputs"]
    assert {"api:placeOrder", "api:cancelOrder"} <= {
        source_ref for task in backends for source_ref in task["source_refs"]
    }
    assert {"use_case:UC1", "use_case:UC2"} <= {
        source_ref for task in backends for source_ref in task["source_refs"]
    }
    prompts = [
        (run / task["prompt_file"]).read_text(encoding="utf-8")
        for task in backends
    ]
    assert all('"call_id"' not in prompt for prompt in prompts)
    assert all('"control_binding"' not in prompt for prompt in prompts)
    assert all("INTERNAL-REPAIR-MARKER" not in prompt for prompt in prompts)
    assert all("INTERNAL-USE-CASE-REPAIR" not in prompt for prompt in prompts)

    for backend in backends:
        backend["depends_on"] = ["missing-backend-task"]
    (run / "reports/run-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    orphaned = reconcile_workflow_state(run)
    assert orphaned["status"] == "NEEDS_PLANNER"
    assert orphaned["nextRunnableTasks"] == []
    assert orphaned["blockingReason"]
    assert orphaned["blockingDetails"]


def test_completed_workflow_runs_one_backend_regression_gate_before_testing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """구현은 backend 전체 test를 한 번 통과한 뒤 runtime 검사를 Testing에 넘긴다."""
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: {
                "status": "READY_TO_FINALIZE",
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
    gate_calls: list[tuple[str, bool, bool]] = []

    def backend_gate(
        _run: Path,
        report_name: str,
        *,
        verify_frontend: bool,
        verify_end_to_end: bool,
    ) -> dict[str, object]:
        gate_calls.append((report_name, verify_frontend, verify_end_to_end))
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_run_workspace",
        backend_gate,
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
    assert gate_calls == [("backend-regression.json", False, False)]
    assert result["backendRegression"] == "reports/backend-regression.json"
    assert (run / "application/Dockerfile").is_file()
    assert not (reports / "final-verification.json").exists()
    assert not (reports / "container-runtime-smoke.json").exists()

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: dict(result),
    )
    resumed = run_workflow(
        run,
        SimpleNamespace(app_id="app-1", inputs={}, job_type="INITIAL_IMPLEMENTATION"),
        auditor=lambda _run: pytest.fail("completed checkpoint must be reused"),
    )
    assert resumed["status"] == "COMPLETE"
    assert gate_calls == [("backend-regression.json", False, False)]


def test_successful_integration_owner_result_is_reused_for_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reconciled integration-owner result is the final gate; do not rerun it."""
    run = tmp_path / "run"
    reports = run / "reports"
    executions = reports / "agent-executions"
    executions.mkdir(parents=True)
    task_id = "implement-vertical-integration"
    result_rel = f"reports/agent-executions/{task_id}.result.json"
    task = {
        "task_id": task_id,
        "task_type": "integration-implementation",
        "owner": "implementation",
        "depends_on": [],
        "context_file": "reports/tasks/integration.context.json",
        "prompt_sha256": "integration-prompt",
        "allowed_write_paths": [],
        "required_output_paths": [],
    }
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    context = run / task["context_file"]
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"readSourcePaths": []}), encoding="utf-8")
    (executions / f"{task_id}.result.json").write_text(
        json.dumps({"status": "SUCCEEDED", "promptSha256": "integration-prompt"}),
        encoding="utf-8",
    )
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "READY_TO_FINALIZE",
                "tasks": [],
                "phases": [{"phaseId": "integration", "status": "SUCCEEDED"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.effective_task_prompt_sha256",
        lambda *_args: "integration-prompt",
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda run_root, _spec: reconcile_workflow_state(run_root),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.audit_run_completion",
        lambda _run: {"status": "COMPLETE"},
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
        "app.implementation.workflows.coordinator.render_local_container",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.build_rtm_traceability_map",
        lambda *_args: {"summary": {"missing": 0}},
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_run_workspace",
        lambda *_args, **_kwargs: pytest.fail("successful integration result must be reused"),
    )

    result = run_workflow(
        run,
        SimpleNamespace(
            app_id="app-1", inputs={}, job_type="INITIAL_IMPLEMENTATION"
        ),
        auditor=lambda _run: {"status": "COMPLETE"},
    )

    assert result["status"] == "COMPLETE"
    assert result["backendRegression"] == result_rel
    assert result["testingRequired"] is True
    assert next(item for item in result["tasks"] if item["task_id"] == task_id)["status"] == "SUCCEEDED"
    persisted = json.loads((reports / "workflow-state.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "COMPLETE"
    assert persisted["backendRegression"] == result_rel


def test_failed_integration_owner_cannot_finalize_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An integration owner failure remains blocking and never reaches completion."""
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    task_id = "implement-vertical-integration"
    task = {
        "task_id": task_id,
        "task_type": "integration-implementation",
        "owner": "implementation",
        "depends_on": [],
        "context_file": "reports/tasks/integration.context.json",
        "prompt_sha256": "integration-prompt",
        "allowed_write_paths": [],
        "required_output_paths": [],
    }
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    context = run / task["context_file"]
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"readSourcePaths": []}), encoding="utf-8")
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "READY",
                "tasks": [],
                "phases": [{"phaseId": "integration", "status": "PENDING"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.effective_task_prompt_sha256",
        lambda *_args: "integration-prompt",
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda run_root, _spec: reconcile_workflow_state(run_root),
    )
    completion_calls: list[Path] = []

    def fail_integration(_run: Path, _task_id: str) -> dict[str, object]:
        raise RuntimeError("integration check failed")

    with pytest.raises(RuntimeError, match="integration check failed"):
        run_workflow(
            run,
            SimpleNamespace(
                app_id="app-1", inputs={}, job_type="INITIAL_IMPLEMENTATION"
            ),
            executor=fail_integration,
            auditor=lambda path: completion_calls.append(path) or {"status": "COMPLETE"},
        )

    persisted = json.loads((reports / "workflow-state.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "FAILED"
    assert persisted["tasks"][0]["status"] == "FAILED"
    assert completion_calls == []
    assert persisted.get("testingRequired") is not True


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
        "status": "INTERRUPTED",
        "tasks": [{**task, "status": "INTERRUPTED", "attempts": 1}],
        "phases": [{"phaseId": "backend", "status": "INTERRUPTED"}],
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

    assert result["status"] == "INTERRUPTED"
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
    context_path = task_dir / "backend.context.json"
    context_path.write_text(
        json.dumps(
            {
                "readSourcePaths": [source_path],
            }
        ),
        encoding="utf-8",
    )
    task = {
        "task_id": "implement-backend-application",
        "task_type": "backend-implementation",
        "owner": "backend",
        "prompt_file": str(prompt_path.relative_to(run)).replace("\\", "/"),
        "context_file": str(context_path.relative_to(run)).replace("\\", "/"),
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
    assert entry["ownerTaskIds"] == ["implement-backend-application"]
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
    assert "with the file editor" in repair_prompt
    assert "`run_task_check`" in repair_prompt
    assert "terminal" not in repair_prompt
    assert "trace evidence, not extra read permission" in repair_prompt
    assert "only when the task context already lists it" in repair_prompt
    assert "Use the terminal to reproduce the failure" not in repair_prompt
    assert "not an exhaustive list of relevant source" not in repair_prompt
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
