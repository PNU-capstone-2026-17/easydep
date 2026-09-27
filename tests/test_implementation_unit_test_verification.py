from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.implementation.agents.runtime as runtime_module
import app.implementation.workflows.repair as repair_module
from app.implementation.agents.runtime import (
    OWNER_TASK_TYPES,
    OwnerAccessContract,
    _execute_frozen_unit_recheck,
    _preserve_failed_unit_candidate,
    _candidate_application_changes,
    _owner_evidence_boundary_message,
    _unit_test_subject_evidence,
)
from app.implementation.agents.verification.build import (
    WorkspaceVerificationError,
    task_verification_command,
    verify_agent_workspace,
)
from app.implementation.agents.verification.frontend import (
    run_frontend_unit_test_verification,
)
from app.implementation.workflows.coordinator import phase_for_task
from app.implementation.workflows.coordinator import reconcile_workflow_state
from app.implementation.workflows.repair import schedule_cross_phase_repair


def test_unit_task_types_are_owner_phases_with_subject_evidence(tmp_path: Path) -> None:
    subject = tmp_path / "application/src/main/java/example/CourseService.java"
    subject.parent.mkdir(parents=True)
    subject.write_text("package example; class CourseService {}", encoding="utf-8")

    assert {"backend-unit-test", "frontend-unit-test"} <= OWNER_TASK_TYPES
    assert phase_for_task("backend-unit-test") == "backend"
    assert phase_for_task("frontend-unit-test") == "frontend"
    evidence = _unit_test_subject_evidence(
        tmp_path,
        {"unitTestSubjectPaths": ["application/src/main/java/example/CourseService.java"]},
    )
    assert "CourseService" in evidence
    assert "read-only unit-test subject" in evidence
    (tmp_path / "context.json").write_text("{}", encoding="utf-8")
    access = OwnerAccessContract.build(
        sandbox=tmp_path,
        run_root=tmp_path,
        task={
            "context_file": "context.json",
            "verification_profile": {
                "unitTestSubjectPaths": [
                    "application/src/main/java/example/CourseService.java"
                ]
            },
        },
        context={"readSourcePaths": []},
        task_type="backend-unit-test",
        editable_paths=[],
        editable_roots=[],
        immutable=[],
        bounded_evidence=True,
    )
    assert str(subject.resolve()) in access.read_hints
    assert "focused unit-test authoring task" in _owner_evidence_boundary_message(
        [], "backend-unit-test"
    )


def test_backend_unit_command_and_junit_execution_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class_name = "com.example.CourseServiceTest"
    assert task_verification_command(
        ["gradlew"], "backend-unit-test", verification_profile={"unitTestClass": class_name}
    ) == ["gradlew", "test", "--tests", class_name, "--build-cache"]

    reports = tmp_path / "application/build/test-results/test"
    reports.mkdir(parents=True)

    def run_success(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (reports / "TEST-course.xml").write_text(
            '<testsuite><testcase classname="com.example.CourseServiceTest" name="works" />'
            '</testsuite>',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "app.implementation.agents.verification.build.gradle_command", lambda: ["gradlew"]
    )
    monkeypatch.setattr(
        "app.implementation.agents.verification.build.subprocess.run", run_success
    )
    evidence = verify_agent_workspace(
        tmp_path,
        "backend-unit-test",
        ["application/src/test/java/com/example/CourseServiceTest.java"],
        {"unitTestClass": class_name},
    )
    assert evidence["unitTestResults"] == {"total": 1, "failed": 0, "skipped": 0}

    def run_skipped(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (reports / "TEST-course.xml").write_text(
            '<testsuite><testcase classname="com.example.CourseServiceTest" name="works"><skipped />'
            '</testcase></testsuite>',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "app.implementation.agents.verification.build.subprocess.run", run_skipped
    )
    with pytest.raises(WorkspaceVerificationError, match="executed passing test"):
        verify_agent_workspace(
            tmp_path,
            "backend-unit-test",
            ["application/src/test/java/com/example/CourseServiceTest.java"],
            {"unitTestClass": class_name},
        )

    def run_failed_assertion(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        (reports / "TEST-course.xml").write_text(
            '<testsuite><testcase classname="com.example.CourseServiceTest" name="works">'
            '<failure message="expected true" /></testcase></testsuite>',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "app.implementation.agents.verification.build.subprocess.run", run_failed_assertion
    )
    with pytest.raises(WorkspaceVerificationError) as failure:
        verify_agent_workspace(
            tmp_path,
            "backend-unit-test",
            ["application/src/test/java/com/example/CourseServiceTest.java"],
            {"unitTestClass": class_name},
        )
    assert failure.value.evidence["unitTestResults"]["failed"] == 1

    def run_nonzero_assertion(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        (reports / "TEST-course.xml").write_text(
            '<testsuite tests="4" failures="2">'
            '<testcase classname="com.example.CourseServiceTest" name="works">'
            '<failure message="expected 7" /></testcase>'
            '<testcase classname="com.example.CourseServiceTest" name="negative">'
            '<failure message="expected -3" /></testcase>'
            '<testcase classname="com.example.CourseServiceTest" name="zero" />'
            '<testcase classname="com.example.CourseServiceTest" name="decimal" />'
            '</testsuite>',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 1, "> Task :test FAILED\n", "BUILD FAILED")

    monkeypatch.setattr(
        "app.implementation.agents.verification.build.subprocess.run", run_nonzero_assertion
    )
    with pytest.raises(WorkspaceVerificationError) as nonzero_failure:
        verify_agent_workspace(
            tmp_path,
            "backend-unit-test",
            ["application/src/test/java/com/example/CourseServiceTest.java"],
            {"unitTestClass": class_name},
        )
    assert nonzero_failure.value.evidence["unitTestResults"] == {
        "total": 4,
        "failed": 2,
        "skipped": 0,
    }

    def run_compile_failure(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "> Task :testClasses FAILED\n", "compile error")

    monkeypatch.setattr(
        "app.implementation.agents.verification.build.subprocess.run", run_compile_failure
    )
    with pytest.raises(WorkspaceVerificationError) as compile_failure:
        verify_agent_workspace(
            tmp_path,
            "backend-unit-test",
            ["application/src/test/java/com/example/CourseServiceTest.java"],
            {"unitTestClass": class_name},
        )
    assert "unitTestResults" not in compile_failure.value.evidence


def test_frontend_unit_verification_requires_fresh_nonempty_vitest_report(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    run_root = tmp_path / "run"
    frontend = sandbox / "application/frontend"
    (frontend / "node_modules").mkdir(parents=True)
    (frontend / "node_modules/.package-lock.json").write_text("", encoding="utf-8")
    (frontend / "package.json").write_text(
        '{"scripts": {"test:unit": "vitest run"}}', encoding="utf-8"
    )
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []
    report_path = run_root / "reports/agent-executions/frontend-unit.vitest.json"

    def passing(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output_path = Path(command[5].removeprefix("--outputFile="))
        assert output_path == report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            '{"numTotalTests": 2, "numFailedTests": 0, "numPendingTests": 1}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    target = "application/frontend/src/features/CourseView.test.tsx"
    passed = run_frontend_unit_test_verification(
        sandbox, [target], passing, report_path=report_path
    )
    assert passed["exitCode"] == 0
    assert passed["unitTestResults"] == {"total": 2, "failed": 0, "skipped": 1}
    assert commands[-1][1:4] == ["run", "test:unit", "--"]
    assert commands[-1][-1] == "src/features/CourseView.test.tsx"
    assert report_path.is_file()
    assert not (frontend / "reports/easydep-vitest-unit.json").exists()
    baseline_frontend = run_root / "application/frontend"
    baseline_frontend.mkdir(parents=True)
    (baseline_frontend / "package.json").write_text(
        (frontend / "package.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (baseline_frontend / "package-lock.json").write_text(
        (frontend / "package-lock.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    baseline_test = run_root / target
    baseline_test.parent.mkdir(parents=True)
    baseline_test.write_text("before", encoding="utf-8")
    (sandbox / target).parent.mkdir(parents=True)
    (sandbox / target).write_text("after", encoding="utf-8")
    assert _candidate_application_changes(sandbox, run_root) == {target}

    def failing(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        report_path.write_text(
            '{"numTotalTests": 1, "numFailedTests": 1, "numPendingTests": 0}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    failed = run_frontend_unit_test_verification(
        sandbox, [target], failing, report_path=report_path
    )
    assert failed["exitCode"] == 1
    assert "executed passing test" in str(failed["stderr"])

    def failing_nonzero(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        report_path.write_text(
            '{"numTotalTests": 3, "numFailedTests": 2, "numPendingTests": 0}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 1, "", "Vitest assertion failure")

    failed_with_report = run_frontend_unit_test_verification(
        sandbox, [target], failing_nonzero, report_path=report_path
    )
    assert failed_with_report["exitCode"] == 1
    assert failed_with_report["unitTestResults"] == {
        "total": 3,
        "failed": 2,
        "skipped": 0,
    }

    def failed_without_report(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "runner failed before tests")

    failed_without_fresh_report = run_frontend_unit_test_verification(
        sandbox, [target], failed_without_report, report_path=report_path
    )
    assert failed_without_fresh_report["exitCode"] == 1
    assert failed_without_fresh_report["unitTestResults"] == {}


def test_failed_unit_assertions_repair_parent_source_and_require_same_test_recheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = tmp_path / "run"
    sandbox = tmp_path / "failed-unit-sandbox"
    reports = run / "reports"
    executions = reports / "agent-executions"
    executions.mkdir(parents=True)
    source_path = "application/src/main/java/example/Calculator.java"
    test_path = "application/src/test/java/example/CalculatorTest.java"
    source = run / source_path
    test_source = run / test_path
    source.parent.mkdir(parents=True)
    test_source.parent.mkdir(parents=True)
    source.write_text("class Calculator { int add(int a, int b) { return a-b; } }", encoding="utf-8")
    test_source.write_text("class CalculatorTest { /* frozen */ }", encoding="utf-8")
    frozen_test = test_source.read_bytes()
    failed_test = sandbox / test_path
    failed_test.parent.mkdir(parents=True)
    failed_test.write_bytes(frozen_test)
    parent = {
        "task_id": "calculator-implementation",
        "task_type": "backend-implementation",
        "owner": "backend",
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
        "prompt_sha256": "parent-prompt",
    }
    unit = {
        "task_id": "calculator-unit-test",
        "task_type": "backend-unit-test",
        "owner": "backend",
        "depends_on": [parent["task_id"]],
        "allowed_write_paths": [test_path],
        "required_test_paths": [test_path],
        "required_output_paths": [test_path],
        "verification_profile": {"unitTestSubjectPaths": [source_path]},
        "prompt_sha256": "unit-prompt",
    }
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [parent, unit]}), encoding="utf-8"
    )
    evidence = {
        "command": ["gradle", "test", "--tests", "example.CalculatorTest"],
        "exitCode": 1,
        "stderr": f"{test_path}: expected 7 but was 1; {source_path}",
        "unitTestResults": {"total": 3, "failed": 1, "skipped": 0},
    }
    frozen_candidate = _preserve_failed_unit_candidate(
        sandbox, run, unit, unit["task_id"], evidence
    )
    assert frozen_candidate is not None
    evidence["frozenTestCandidate"] = frozen_candidate
    monkeypatch.setattr(
        repair_module,
        "_unit_failure_review",
        lambda *_args: {
            "classification": "implementation",
            "rationale": "The contract-backed arithmetic assertion is violated.",
            "evidence": ["selected JUnit failure"],
            "preserve_assertions": ["CalculatorTest.add"],
            "correction_instruction": "Correct only the assigned implementation source.",
        },
    )

    repair = schedule_cross_phase_repair(run, unit["task_id"], evidence)

    assert repair is not None
    assert repair["ownerTaskIds"] == [parent["task_id"]]
    assert repair["repairPaths"] == [source_path]
    assert repair["recheckTaskIds"] == [unit["task_id"]]
    assert repair["unitFailureReview"]["classification"] == "implementation"
    assert test_source.read_bytes() == frozen_test

    for task in (parent, unit):
        result = {
            "status": "SUCCEEDED",
            "promptSha256": task["prompt_sha256"],
        }
        (executions / f"{task['task_id']}.result.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "READY_TO_FINALIZE",
                "tasks": [
                    {"task_id": parent["task_id"], "status": "SUCCEEDED"},
                    {"task_id": unit["task_id"], "status": "SUCCEEDED"},
                ],
                "phases": [],
            }
        ),
        encoding="utf-8",
    )

    reconciled = reconcile_workflow_state(run)
    task_states = {task["task_id"]: task["status"] for task in reconciled["tasks"]}
    assert task_states[parent["task_id"]] == "SUCCEEDED"
    assert task_states[unit["task_id"]] == "PENDING"
    assert unit["task_id"] in reconciled["nextRunnableTasks"]


def test_test_oracle_review_repairs_only_declared_test_and_keeps_subject_readonly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    sandbox = tmp_path / "sandbox"
    reports = run / "reports"
    reports.mkdir(parents=True)
    source_path = "application/frontend/src/features/Drop.tsx"
    test_path = "application/frontend/src/features/Drop.test.tsx"
    source = run / source_path
    source.parent.mkdir(parents=True)
    source.write_text("export function Drop() { return <button>Drop</button>; }", encoding="utf-8")
    candidate = sandbox / test_path
    candidate.parent.mkdir(parents=True)
    candidate.write_text("it('uses a bad locator', () => {});", encoding="utf-8")
    parent = {
        "task_id": "drop-implementation",
        "task_type": "frontend-implementation",
        "owner": "frontend",
        "allowed_write_paths": [source_path],
    }
    unit = {
        "task_id": "drop-unit-test",
        "task_type": "frontend-unit-test",
        "owner": "frontend",
        "depends_on": [parent["task_id"]],
        "allowed_write_paths": [test_path],
        "required_output_paths": [test_path],
        "required_test_paths": [test_path],
        "verification_profile": {"unitTestSubjectPaths": [source_path]},
    }
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [parent, unit]}), encoding="utf-8"
    )
    evidence: dict[str, object] = {
        "command": ["npm", "run", "test:unit"],
        "exitCode": 1,
        "stderr": "invalid form landmark query",
        "unitTestResults": {"total": 7, "failed": 1, "skipped": 0},
    }
    frozen = _preserve_failed_unit_candidate(sandbox, run, unit, unit["task_id"], evidence)
    assert frozen is not None
    evidence["frozenTestCandidate"] = frozen
    monkeypatch.setattr(
        repair_module,
        "_unit_failure_review",
        lambda *_args: {
            "classification": "test_oracle",
            "rationale": "The locator contradicts the rendered accessible tree.",
            "evidence": ["raw Vitest assertion"],
            "preserve_assertions": ["pending button is disabled"],
            "correction_instruction": "Change only the assigned test locator.",
        },
    )

    repair = schedule_cross_phase_repair(run, unit["task_id"], evidence)

    assert repair is not None
    assert repair["ownerTaskIds"] == [unit["task_id"]]
    assert repair["recheckTaskIds"] == []
    assert repair["repairPaths"] == [test_path]
    assert repair["frozenTestCandidate"] == frozen
    assert repair["unitFailureReview"]["classification"] == "test_oracle"
    assert runtime_module._task_execution_scope(unit, repair) == ([test_path], [], [])
    assert source_path not in repair["repairPaths"]


def test_undetermined_unit_review_does_not_schedule_any_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    source_path = "application/src/main/java/example/Calculator.java"
    test_path = "application/src/test/java/example/CalculatorTest.java"
    source = tmp_path / source_path
    source.parent.mkdir(parents=True)
    source.write_text("class Calculator {}", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    candidate = sandbox / test_path
    candidate.parent.mkdir(parents=True)
    candidate.write_text("class CalculatorTest {}", encoding="utf-8")
    parent = {
        "task_id": "calculator-implementation",
        "task_type": "backend-implementation",
        "owner": "backend",
        "allowed_write_paths": [source_path],
    }
    unit = {
        "task_id": "calculator-unit-test",
        "task_type": "backend-unit-test",
        "owner": "backend",
        "depends_on": [parent["task_id"]],
        "allowed_write_paths": [test_path],
        "required_output_paths": [test_path],
        "required_test_paths": [test_path],
        "verification_profile": {"unitTestSubjectPaths": [source_path]},
    }
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [parent, unit]}), encoding="utf-8"
    )
    evidence: dict[str, object] = {
        "unitTestResults": {"total": 1, "failed": 1, "skipped": 0},
    }
    frozen = _preserve_failed_unit_candidate(sandbox, tmp_path, unit, unit["task_id"], evidence)
    assert frozen is not None
    evidence["frozenTestCandidate"] = frozen
    monkeypatch.setattr(
        repair_module,
        "_unit_failure_review",
        lambda *_args: {
            "classification": "undetermined",
            "rationale": "The available contract does not establish the assertion.",
            "evidence": [],
            "preserve_assertions": [],
            "correction_instruction": "Do not edit code.",
        },
    )

    assert schedule_cross_phase_repair(tmp_path, unit["task_id"], evidence) is None


def test_unit_failure_review_rejects_tampered_frozen_candidate_before_calling_model(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    candidate = tmp_path / "reports/agent-executions/test.frozen"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("tampered", encoding="utf-8")
    failed = {
        "task_id": "unit",
        "task_type": "frontend-unit-test",
        "verification_profile": {"unitTestSubjectPaths": ["application/frontend/src/Subject.tsx"]},
    }
    evidence = {
        "unitTestResults": {"total": 1, "failed": 1, "skipped": 0},
        "frozenTestCandidate": {
            "path": "reports/agent-executions/test.frozen",
            "sha256": "0" * 64,
            "sourcePath": "application/frontend/src/Subject.test.tsx",
        },
    }

    caplog.set_level("INFO", logger=repair_module.__name__)
    assert repair_module._unit_failure_review(tmp_path, failed, evidence) is None
    assert "frozen_candidate_missing_or_sha_mismatch" in caplog.text
    assert "tampered" not in caplog.text


def _unit_failure_review_fixture(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    subject_path = "application/src/main/java/example/Subject.java"
    test_path = "application/src/test/java/example/SubjectTest.java"
    candidate_path = "reports/agent-executions/unit.frozen-test.java"
    prompt_path = "reports/implementation-tasks/unit.prompt.md"
    context_path = "reports/implementation-tasks/unit.context.json"
    subject_context_path = "reports/implementation-tasks/subject-context.json"
    operation_path = "reports/implementation-tasks/operation-contract.json"
    files = {
        subject_path: "package example; class Subject {}\n",
        candidate_path: "package example; class SubjectTest {}\n",
        prompt_path: "Write focused tests for the declared subject.\n",
        context_path: json.dumps({"subjectContextPath": subject_context_path}),
        subject_context_path: json.dumps(
            {"generatedOperationContractsPath": operation_path}
        ),
        operation_path: '{"operation":"sum","behavior":"returns the sum"}',
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    failed: dict[str, object] = {
        "task_id": "unit",
        "task_type": "backend-unit-test",
        "allowed_write_paths": [test_path],
        "required_test_paths": [test_path],
        "prompt_file": prompt_path,
        "context_file": context_path,
        "verification_profile": {"unitTestSubjectPaths": [subject_path]},
        "depends_on": ["subject-owner"],
    }
    evidence: dict[str, object] = {
        "unitTestResults": {"total": 2, "failed": 1, "skipped": 0},
        "testResults": "SubjectTest.testSum: expected 9 but was 8",
        "stdout": "> Task :test FAILED",
        "diagnosticPaths": [],
        "frozenTestCandidate": {
            "path": candidate_path,
            "sha256": repair_module.hashlib.sha256(
                (root / candidate_path).read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest(),
            "sourcePath": test_path,
        },
    }
    return failed, evidence


def test_unit_failure_review_corrects_one_invalid_json_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed, evidence = _unit_failure_review_fixture(tmp_path)
    valid_review = {
        "classification": "test_oracle",
        "rationale": "The supplied contract does not guarantee the asserted order.",
        "evidence": ["The operation contract has no ordering requirement."],
        "preserve_assertions": ["Retain the sum behavior assertion."],
        "correction_instruction": "Change only the assigned test assertion without weakening behavior coverage.",
    }
    first_response = '{"classification":"test_oracle","rationale":"order is unspecified","evidence":[],"preserve_assertions":[]}'
    second_response = json.dumps(
        {
            "classification": "test_oracle",
            "rationale": "The order is unspecified.",
            "evidence": [],
            "preserve_assertions": [],
            "correction_instruction": 12,
        }
    )
    replies = [first_response, second_response, json.dumps(valid_review)]
    calls: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            content = replies.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    monkeypatch.setattr(repair_module, "build_llm_connection", lambda: SimpleNamespace(
        model="openai/gpt-oss-120b",
        api_key="synthetic-test-key",
        base_url="https://example.invalid/v1",
        default_headers=lambda: {},
    ))
    monkeypatch.setattr(
        repair_module,
        "OpenAI",
        lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )
    monkeypatch.setattr(repair_module.time, "sleep", lambda _delay: None)

    result = repair_module._unit_failure_review(tmp_path, failed, evidence)

    assert result == valid_review
    assert len(calls) == 3
    second_messages = calls[1]["messages"]
    assert second_messages[-2] == {"role": "assistant", "content": first_response}
    third_messages = calls[2]["messages"]
    assert third_messages[-2] == {"role": "assistant", "content": second_response}
    assert first_response not in json.dumps(third_messages, ensure_ascii=False)
    correction = third_messages[-1]["content"]
    assert '"loc": ["correction_instruction"]' in correction
    assert "weaken or remove contract-backed assertions" in correction


def test_unit_failure_review_interruption_propagates_from_retry_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed, evidence = _unit_failure_review_fixture(tmp_path)
    invalid = '{"classification":"undetermined","rationale":"unclear"}'
    calls: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=invalid))]
            )

    monkeypatch.setattr(repair_module, "build_llm_connection", lambda: SimpleNamespace(
        model="openai/gpt-oss-120b",
        api_key="synthetic-test-key",
        base_url="https://example.invalid/v1",
        default_headers=lambda: {},
    ))
    monkeypatch.setattr(
        repair_module,
        "OpenAI",
        lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )
    def interrupt_sleep(_delay: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(repair_module.time, "sleep", interrupt_sleep)

    with pytest.raises(KeyboardInterrupt):
        repair_module._unit_failure_review(tmp_path, failed, evidence)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "unit_results",
    [
        {"total": 0, "failed": 0, "skipped": 0},
        {"total": 2, "failed": 0, "skipped": 2},
        {"total": 2, "failed": 0, "skipped": 0},
    ],
)
def test_non_assertion_unit_results_do_not_select_parent_source_repair(
    tmp_path: Path, unit_results: dict[str, int]
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    source_path = "application/frontend/src/Calculator.tsx"
    test_path = "application/frontend/src/Calculator.test.tsx"
    parent = {
        "task_id": "calculator-frontend",
        "task_type": "frontend-implementation",
        "owner": "frontend",
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
    }
    unit = {
        "task_id": "calculator-frontend-unit",
        "task_type": "frontend-unit-test",
        "owner": "frontend",
        "depends_on": [parent["task_id"]],
        "allowed_write_paths": [test_path],
        "required_output_paths": [test_path],
        "verification_profile": {"unitTestSubjectPaths": [source_path]},
    }
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [parent, unit]}), encoding="utf-8"
    )

    repair = schedule_cross_phase_repair(
        tmp_path,
        unit["task_id"],
        {
            "command": ["npm", "run", "test:unit"],
            "exitCode": 1,
            "stderr": "The focused unit check could not establish an assertion failure.",
            "unitTestResults": unit_results,
        },
    )

    assert repair is not None
    assert repair["ownerTaskIds"] == [unit["task_id"]]
    assert repair["recheckTaskIds"] == []


def test_frozen_unit_candidate_survives_cleanup_and_promotes_only_after_canonical_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    sandbox = tmp_path / "sandbox"
    reports = run / "reports"
    reports.mkdir(parents=True)
    source_path = "application/src/main/java/example/Calculator.java"
    test_path = "application/src/test/java/example/CalculatorTest.java"
    unit_test = sandbox / test_path
    unit_test.parent.mkdir(parents=True)
    unit_test_bytes = b"class CalculatorTest { assertEquals(7, service.add(3, 4)); }"
    unit_test.write_bytes(unit_test_bytes)
    parent = {
        "task_id": "calculator-implementation",
        "task_type": "backend-implementation",
        "owner": "backend",
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
    }
    unit = {
        "task_id": "calculator-unit-test",
        "task_type": "backend-unit-test",
        "owner": "backend",
        "depends_on": [parent["task_id"]],
        "allowed_write_paths": [test_path],
        "required_test_paths": [test_path],
        "required_output_paths": [test_path],
        "verification_profile": {"unitTestSubjectPaths": [source_path]},
    }
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [parent, unit]}), encoding="utf-8"
    )
    failed_evidence: dict[str, object] = {
        "command": ["gradle", "test", "--tests", "example.CalculatorTest"],
        "exitCode": 1,
        "stderr": f"{test_path}: expected 7 but was 8; {source_path}",
        "unitTestResults": {"total": 4, "failed": 1, "skipped": 0},
    }

    candidate = _preserve_failed_unit_candidate(
        sandbox, run, unit, unit["task_id"], failed_evidence
    )
    assert candidate is not None
    candidate_path = run / candidate["path"]
    assert candidate_path.read_bytes() == unit_test_bytes
    failed_evidence["frozenTestCandidate"] = candidate
    repair = schedule_cross_phase_repair(run, unit["task_id"], failed_evidence)
    assert repair is not None
    assert repair["ownerTaskIds"] == [parent["task_id"]]
    assert repair["recheckTaskIds"] == [unit["task_id"]]
    assert not (run / test_path).exists()
    shutil.rmtree(sandbox)

    def prepare_recheck_workspace(*_args: object, **_kwargs: object) -> Path:
        sandbox.mkdir(parents=True)
        return sandbox

    monkeypatch.setattr(runtime_module, "prepare_agent_workspace", prepare_recheck_workspace)

    def pass_frozen_check(current_sandbox: Path, *_args: object) -> dict[str, object]:
        assert (current_sandbox / test_path).read_bytes() == unit_test_bytes
        return {
            "command": ["gradle", "test", "--tests", "example.CalculatorTest"],
            "exitCode": 0,
            "unitTestResults": {"total": 4, "failed": 0, "skipped": 0},
        }

    monkeypatch.setattr(runtime_module, "verify_agent_workspace", pass_frozen_check)
    monkeypatch.setattr(runtime_module, "cleanup_agent_workspace", lambda *_args, **_kwargs: None)

    result = _execute_frozen_unit_recheck(run, unit, unit["task_id"], candidate)

    promoted_test = run / test_path
    assert promoted_test.read_bytes() == unit_test_bytes
    assert candidate_path.read_bytes() == unit_test_bytes
    assert result["status"] == "SUCCEEDED"
    assert result["agentInvoked"] is False
    assert result["frozenRecheckCandidate"] == candidate


def test_attributed_source_repair_schedules_only_its_declared_unit_recheck(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    source_path = "application/src/main/java/example/CourseService.java"
    test_path = "application/src/test/java/example/CourseServiceTest.java"
    unrelated_source = "application/src/main/java/example/OtherService.java"
    unrelated_test = "application/src/test/java/example/OtherServiceTest.java"
    integration = {
        "task_id": "integration-owner",
        "task_type": "integration-implementation",
        "owner": "integration",
        "allowed_write_paths": ["application/src/test/java/example/IntegrationTest.java"],
    }
    source_owner = {
        "task_id": "course-service-owner",
        "task_type": "backend-implementation",
        "owner": "backend",
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
    }
    linked_unit = {
        "task_id": "course-service-unit",
        "task_type": "backend-unit-test",
        "owner": "backend",
        "depends_on": [source_owner["task_id"]],
        "required_test_paths": [test_path],
        "verification_profile": {"unitTestSubjectPaths": [source_path]},
    }
    other_owner = {
        "task_id": "other-service-owner",
        "task_type": "backend-implementation",
        "owner": "backend",
        "allowed_write_paths": [unrelated_source],
        "required_output_paths": [unrelated_source],
    }
    unrelated_unit_task = {
        "task_id": "other-service-unit",
        "task_type": "backend-unit-test",
        "owner": "backend",
        "depends_on": [other_owner["task_id"]],
        "required_test_paths": [unrelated_test],
        "verification_profile": {"unitTestSubjectPaths": [unrelated_source]},
    }
    historical_owner = {
        "task_id": "historical-owner",
        "task_type": "backend-implementation",
        "owner": "backend",
        "allowed_write_paths": ["application/src/main/java/example/OldService.java"],
        "required_output_paths": ["application/src/main/java/example/OldService.java"],
    }
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    integration,
                    source_owner,
                    linked_unit,
                    other_owner,
                    unrelated_unit_task,
                    historical_owner,
                ]
            }
        ),
        encoding="utf-8",
    )
    test_bytes = b"class CourseServiceTest { void checksContract() {} }"
    source_file = tmp_path / source_path
    source_file.parent.mkdir(parents=True)
    source_file.write_text("class CourseService {}", encoding="utf-8")
    test_file = tmp_path / test_path
    test_file.parent.mkdir(parents=True)
    test_file.write_bytes(test_bytes)
    unrelated_file = tmp_path / unrelated_test
    unrelated_file.parent.mkdir(parents=True, exist_ok=True)
    unrelated_file.write_text("class OtherServiceTest {}", encoding="utf-8")
    (tmp_path / unrelated_source).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / unrelated_source).write_text("class OtherService {}", encoding="utf-8")

    evidence = {
        "repairTaskId": source_owner["task_id"],
        "attributedTargetFile": source_path,
        "stderr": f"Integration failure attributed to {source_path}",
    }
    plan = schedule_cross_phase_repair(tmp_path, integration["task_id"], evidence)

    assert plan is not None
    assert plan["ownerTaskIds"] == [source_owner["task_id"]]
    assert plan["recheckTaskIds"] == [linked_unit["task_id"]]
    candidate = plan["frozenTestCandidate"]
    assert candidate["sourcePath"] == test_path
    frozen = tmp_path / str(candidate["path"])
    assert frozen.read_bytes() == test_bytes
    assert candidate["sha256"] == hashlib.sha256(test_bytes).hexdigest()
    assert unrelated_unit_task["task_id"] not in plan["recheckTaskIds"]
    assert unrelated_file.read_text(encoding="utf-8") == "class OtherServiceTest {}"

    # An already-scheduled source repair is also upgraded before coordinator
    # reconciliation, so a normal checkpoint retry cannot reuse stale unit success.
    plan_path = reports / "repair-plan.json"
    stored_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    stored_entry = stored_plan["entries"][-1]
    stored_entry.pop("frozenTestCandidate")
    stored_entry["recheckTaskIds"] = []
    plan_path.write_text(json.dumps(stored_plan), encoding="utf-8")
    repair_module.apply_repair_directives(tmp_path)
    resumed_entry = json.loads(plan_path.read_text(encoding="utf-8"))["entries"][-1]
    assert resumed_entry["recheckTaskIds"] == [linked_unit["task_id"]]
    assert (tmp_path / resumed_entry["frozenTestCandidate"]["path"]).read_bytes() == test_bytes
    first_candidate_path = resumed_entry["frozenTestCandidate"]["path"]
    next_repair = schedule_cross_phase_repair(tmp_path, integration["task_id"], evidence)
    assert next_repair is not None
    assert next_repair["recheckTaskIds"] == [linked_unit["task_id"]]
    assert next_repair["frozenTestCandidate"]["path"] != first_candidate_path

    # A later Testing repair batch should activate both declared owners and
    # keep each source's own frozen test, without reviving the prior entry.
    stale = schedule_cross_phase_repair(
        tmp_path,
        historical_owner["task_id"],
        {"owner": "backend", "stderr": "Historical unrelated source failure"},
    )
    assert stale is not None
    batch = repair_module.schedule_cross_phase_repair_batch(
        tmp_path,
        [
            (integration["task_id"], evidence),
            (
                integration["task_id"],
                {
                    "repairTaskId": other_owner["task_id"],
                    "attributedTargetFile": unrelated_source,
                    "stderr": f"Integration failure attributed to {unrelated_source}",
                },
            ),
        ],
    )
    assert batch is not None and len(batch) == 2
    assert {item["batchId"] for item in batch} == {
        json.loads(plan_path.read_text(encoding="utf-8"))["activeBatchId"]
    }
    assert repair_module.repair_task_ids(tmp_path) == {
        source_owner["task_id"], other_owner["task_id"]
    }
    assert repair_module.repair_recheck_task_ids(tmp_path) == {
        linked_unit["task_id"], unrelated_unit_task["task_id"]
    }
    assert repair_module.active_repair_for_task(tmp_path, historical_owner["task_id"]) is None
    expected = {
        linked_unit["task_id"]: (test_path, test_bytes),
        unrelated_unit_task["task_id"]: (
            unrelated_test,
            b"class OtherServiceTest {}",
        ),
    }
    for task_id, (subject_path, body) in expected.items():
        entry = repair_module.repair_recheck_for_task(tmp_path, task_id)
        assert entry is not None
        candidate = entry["frozenTestCandidate"]
        assert candidate["sourcePath"] == subject_path
        assert (tmp_path / candidate["path"]).read_bytes() == body
        assert candidate["sha256"] == hashlib.sha256(body).hexdigest()
