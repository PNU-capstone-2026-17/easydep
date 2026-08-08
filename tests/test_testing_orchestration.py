from __future__ import annotations

import app.core.orchestration.adapters.testing as testing_module
from app.core.orchestration.adapters.testing import TestingAdapter as VerificationAdapter


def test_testing_stage_runs_application_tests_without_benchmark_evaluation(
    tmp_path, monkeypatch
):
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
    assert set(result) == {"status", "passed", "repository", "unitTests"}


def test_testing_stage_does_not_turn_missing_tools_into_success(tmp_path, monkeypatch):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    adapter = VerificationAdapter()
    monkeypatch.setattr(
        adapter, "_unit_tests", lambda _root: {"status": "unavailable"}
    )
    result = adapter.run(
        implementation_result={"run_root": str(tmp_path / "run")}
    )

    assert result["status"] == "completed"
    assert result["passed"] is False


def test_testing_stage_uses_bundled_gradle_without_requesting_jacoco(
    tmp_path, monkeypatch
):
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
        lambda command, cwd, timeout: calls.append((command, cwd, timeout))
        or {"status": "passed"},
    )

    result = VerificationAdapter().run(
        implementation_result={"run_root": str(tmp_path / "run")}
    )

    assert result["passed"] is True
    assert calls[0][0] == ["bundled-gradle", "test", "--no-daemon"]
