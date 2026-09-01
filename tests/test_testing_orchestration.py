from __future__ import annotations

import pytest

import app.testing.runtime.adapter as testing_module
from app.requirements.resources.application_cloud import infer_application_contract
from app.testing.runtime.adapter import TestingAdapter as VerificationAdapter


@pytest.fixture(autouse=True)
def _use_local_testing_adapter(monkeypatch):
    """이 파일은 컨테이너 transport가 아니라 로컬 테스트 판정만 확인한다."""

    monkeypatch.setattr(testing_module, "configured_runner_image", lambda: None)


def test_testing_preserves_configured_gradle_cache(tmp_path, monkeypatch):
    observed = {}

    def completed(*_args, **kwargs):
        observed.update(kwargs["env"])
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setenv("GRADLE_USER_HOME", str(tmp_path / "shared-gradle"))
    monkeypatch.setattr(testing_module, "run_process_tree", completed)

    result = testing_module._run(["gradle", "test"], tmp_path, 10)

    assert result["status"] == "passed"
    assert observed["GRADLE_USER_HOME"] == str(tmp_path / "shared-gradle")


def test_testing_classifies_missing_hibernate_strategy_as_database_mismatch():
    diagnostics = VerificationAdapter._diagnostics(
        {
            "status": "failed",
            "stdout": (
                "Caused by: org.hibernate.boot.registry.selector.spi."
                "StrategySelectionException\n"
                "Caused by: java.lang.ClassNotFoundException"
            ),
        }
    )

    assert diagnostics == [
        {
            "code": "APP-DB-001",
            "message": "Generated application tests failed.",
        }
    ]


def test_testing_stage_runs_application_tests_without_benchmark_evaluation(tmp_path, monkeypatch):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    adapter = VerificationAdapter()
    monkeypatch.setattr(adapter, "_unit_tests", lambda _root: {"status": "passed"})
    result = adapter.run(
        implementation_result={"run_root": str(tmp_path / "run")},
        case_id="P1-aws",
    )

    assert result["status"] == "completed"
    assert result["passed"] is True
    assert set(result) == {
        "status",
        "passed",
        "repository",
        "unitTests",
        "diagnostics",
    }
    assert result["diagnostics"] == []


def test_testing_stage_does_not_turn_missing_tools_into_success(tmp_path, monkeypatch):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    adapter = VerificationAdapter()
    monkeypatch.setattr(adapter, "_unit_tests", lambda _root: {"status": "unavailable"})
    result = adapter.run(implementation_result={"run_root": str(tmp_path / "run")})

    assert result["status"] == "completed"
    assert result["passed"] is False


def test_testing_stage_classifies_compile_dependency_failure(tmp_path, monkeypatch):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    adapter = VerificationAdapter()
    monkeypatch.setattr(
        adapter,
        "_unit_tests",
        lambda _root: {
            "status": "failed",
            "stderr": "error: package jakarta.persistence does not exist",
        },
    )

    result = adapter.run(implementation_result={"run_root": str(tmp_path / "run")})

    assert result["diagnostics"][0]["code"] == "APP-DEP-001"


def test_compile_failure_routes_to_the_recorded_scaffold_file_owner(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/example/Broken.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Broken {}", encoding="utf-8")
    unit_tests = {
        "status": "failed",
        "stdout": "> Task :compileJava FAILED\nCompilation failed",
        "stderr": f"{source}:18: error: method does not override a supertype",
    }

    diagnostics = VerificationAdapter._diagnostics(
        unit_tests,
        {"scaffold_files": ["src/main/java/example/Broken.java"]},
        application,
    )

    assert diagnostics[0]["code"] == "APP-COMPILE-SCAFFOLD-001"
    assert diagnostics[0]["ownedFailedFiles"] == ["src/main/java/example/Broken.java"]


def test_latest_writer_owns_a_compile_failure_when_logic_modified_scaffold_file(
    tmp_path,
):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/example/Broken.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Broken {}", encoding="utf-8")
    diagnostics = VerificationAdapter._diagnostics(
        {
            "status": "failed",
            "stdout": "> Task :compileJava FAILED\nCompilation failed",
            "stderr": f"{source}:7: error: invalid method",
        },
        {
            "scaffold_files": ["src/main/java/example/Broken.java"],
            "files": ["src/main/java/example/Broken.java"],
        },
        application,
    )

    assert diagnostics[0]["code"] == "APP-COMPILE-LOGIC-001"


def test_unowned_compile_file_does_not_guess_a_repair_owner(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    diagnostics = VerificationAdapter._diagnostics(
        {
            "status": "failed",
            "stdout": "> Task :compileJava FAILED\nCompilation failed",
            "stderr": "src/main/java/example/Unknown.java:2: error: invalid method",
        },
        {"scaffold_files": ["src/main/java/example/Other.java"]},
        application,
    )

    assert diagnostics == [
        {
            "code": "APPLICATION_TESTS_FAILED",
            "message": "Generated application tests failed.",
        }
    ]


def test_member_generated_unowned_test_compile_failure_routes_to_scaffold(tmp_path):
    application = tmp_path / "run" / "application"
    test_source = application / "src/test/java/example/ControllerTest.java"
    test_source.parent.mkdir(parents=True)
    test_source.write_text("class ControllerTest {}", encoding="utf-8")

    diagnostics = VerificationAdapter._diagnostics(
        {
            "status": "failed",
            "stdout": "> Task :compileTestJava FAILED\nCompilation failed",
            "stderr": (
                f"{test_source}:28: error: incompatible types: "
                "OldDependency cannot be converted to NewAdapter"
            ),
        },
        {
            "member_workflow_executed": True,
            "member_workflow_status": "COMPLETE",
            "acceptance_tests": [],
        },
        application,
    )

    assert diagnostics == [
        {
            "code": "APP-COMPILE-MEMBER-TEST-001",
            "message": (
                "Member-generated internal tests no longer compile against the "
                "current production source contract."
            ),
            "repairOwner": "implementation.scaffold",
            "failedFiles": ["src/test/java/example/ControllerTest.java"],
            "ownedFailedFiles": ["src/test/java/example/ControllerTest.java"],
        }
    ]
def test_non_member_unowned_test_compile_failure_routes_to_acceptance_tests(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)

    diagnostics = VerificationAdapter._diagnostics(
        {
            "status": "failed",
            "stdout": "> Task :compileTestJava FAILED\nCompilation failed",
            "stderr": "src/test/java/example/AcceptanceTest.java:9: error: invalid method",
        },
        {"member_workflow_executed": False},
        application,
    )

    assert diagnostics[0]["code"] == "APP-COMPILE-ACCEPTANCE-001"
    assert diagnostics[0]["repairOwner"] == "implementation.acceptance_tests"


def test_member_internal_test_failure_routes_to_scaffold(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)

    diagnostics = VerificationAdapter._diagnostics(
        {
            "status": "failed",
            "stdout": (
                "HealthCheckControllerServiceTest > "
                "buildHealthResponse_callsHealthEndpoint() FAILED\n"
            ),
            "stderr": "2 tests completed, 1 failed",
            "testFiles": ["src/test/java/example/HealthCheckControllerServiceTest.java"],
        },
        {
            "member_workflow_executed": True,
            "acceptance_tests": ["src/test/java/example/FixedAcceptanceTest.java"],
        },
        application,
    )

    assert diagnostics == [
        {
            "code": "APP-MEMBER-TEST-FAILURE-001",
            "message": (
                "Member-generated internal tests fail against the final composed production source."
            ),
            "repairOwner": "implementation.scaffold",
            "failedTestClasses": ["HealthCheckControllerServiceTest"],
            "failedFiles": ["src/test/java/example/HealthCheckControllerServiceTest.java"],
        }
    ]


def test_fixed_acceptance_failure_is_not_routed_to_test_rewrite(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    fixed_test = "src/test/java/example/FixedAcceptanceTest.java"

    diagnostics = VerificationAdapter._diagnostics(
        {
            "status": "failed",
            "stdout": "FixedAcceptanceTest > requiredResult() FAILED\n",
            "stderr": "1 test completed, 1 failed",
            "testFiles": [fixed_test],
        },
        {
            "member_workflow_executed": True,
            "acceptance_tests": [fixed_test],
        },
        application,
    )

    assert diagnostics == [
        {
            "code": "APPLICATION_TESTS_FAILED",
            "message": "Generated application tests failed.",
        }
    ]


def test_testing_stage_uses_bundled_gradle_without_requesting_jacoco(tmp_path, monkeypatch):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    (application / "build.gradle").write_text("plugins { id 'java' }", encoding="utf-8")
    test_file = application / "src" / "test" / "java" / "AcceptanceTest.java"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("class AcceptanceTest {}", encoding="utf-8")
    monkeypatch.setattr(testing_module.shutil, "which", lambda _name: None)
    from app.implementation.agents.verification import build as agent_runtime

    monkeypatch.setattr(agent_runtime, "gradle_command", lambda: ["bundled-gradle"])
    calls = []
    monkeypatch.setattr(
        testing_module,
        "_run",
        lambda command, cwd, timeout, environment_overrides=None: (
            calls.append((command, cwd, timeout, environment_overrides)) or {"status": "passed"}
        ),
    )

    result = VerificationAdapter().run(implementation_result={"run_root": str(tmp_path / "run")})

    assert result["passed"] is True
    assert calls[0][0] == ["bundled-gradle", "test", "--no-daemon"]


def test_testing_stage_isolates_sqlite_file_with_an_environment_override(tmp_path, monkeypatch):
    application = tmp_path / "run" / "application"
    resources = application / "src" / "main" / "resources"
    resources.mkdir(parents=True)
    (resources / "application.yml").write_text(
        "spring:\n  datasource:\n"
        "    url: ${EASYDEP_DATASOURCE_URL:jdbc:sqlite:/var/lib/notes/notes.db}\n",
        encoding="utf-8",
    )
    test_file = application / "src" / "test" / "java" / "AcceptanceTest.java"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("class AcceptanceTest {}", encoding="utf-8")
    monkeypatch.setattr(testing_module.shutil, "which", lambda _name: None)
    from app.implementation.agents.verification import build

    monkeypatch.setattr(build, "gradle_command", lambda: ["bundled-gradle"])
    calls = []
    monkeypatch.setattr(
        testing_module,
        "_run",
        lambda command, cwd, timeout, environment_overrides=None: (
            calls.append(environment_overrides) or {"status": "passed"}
        ),
    )

    contract = infer_application_contract(application).model_dump(mode="json", by_alias=True)
    result = VerificationAdapter().run(
        implementation_result={
            "run_root": str(tmp_path / "run"),
            "application_runtime_contract": contract,
        }
    )

    assert result["passed"] is True
    assert len(calls) == 1
    assert calls[0] is not None
    assert calls[0]["EASYDEP_DATASOURCE_URL"].startswith("jdbc:sqlite:")
    assert ".easydep-test-" in calls[0]["EASYDEP_DATASOURCE_URL"]
