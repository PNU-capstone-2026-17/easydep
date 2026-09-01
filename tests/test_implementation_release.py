from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.implementation.agents.verification.build import WorkspaceVerificationError
from app.implementation.agents.verification.release import verify_container_runtime
from app.implementation.workflows.release import write_release_manifest


def _passed_scenario() -> dict[str, object]:
    return {
        "status": "PASSED",
        "tasks": [{"taskId": "course-search", "status": "PASSED"}],
        "coveredUseCaseIds": ["UC1"],
    }


def test_container_runtime_smoke_builds_starts_probes_and_cleans(tmp_path: Path) -> None:
    application = tmp_path / "application"
    application.mkdir()
    (application / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    commands: list[list[str]] = []
    health_gets: list[str] = []

    def run(command: list[str], **_kwargs):
        commands.append(command)
        stdout = "127.0.0.1:49152\n" if command[1] == "port" else "ok"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def http_get(url: str, _timeout: float) -> tuple[int, str, str]:
        health_gets.append(url)
        return 200, "application/json", '{"status":"UP"}'

    report = verify_container_runtime(
        tmp_path,
        run_command=run,
        probe=lambda host, port, timeout: (host, port, timeout)
        == ("127.0.0.1", 49152, 1.0),
        http_get=http_get,
    )

    assert report["status"] == "SUCCEEDED"
    assert report["hostPort"] == 49152
    assert health_gets
    assert any(command[1] == "build" for command in commands)
    start = next(command for command in commands if command[1] == "run")
    assert "SPRING_PROFILES_ACTIVE=test" in start
    assert any(value.startswith("SPRING_DATASOURCE_URL=jdbc:h2:mem:") for value in start)
    assert any(command[1:3] == ["image", "rm"] for command in commands)


def test_container_build_failure_is_not_sent_to_source_repair(tmp_path: Path) -> None:
    application = tmp_path / "application"
    application.mkdir()
    (application / "Dockerfile").write_text("FROM scratch", encoding="utf-8")

    def run(command: list[str], **_kwargs):
        if command[1] == "build":
            return subprocess.CompletedProcess(command, 1, "", "Docker daemon unavailable")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(RuntimeError, match="Container runtime preparation failed") as raised:
        verify_container_runtime(tmp_path, run_command=run)

    assert not isinstance(raised.value, WorkspaceVerificationError)


def test_container_runtime_smoke_proves_frontend_index_and_bundle(
    tmp_path: Path,
) -> None:
    application = tmp_path / "application"
    frontend = application / "frontend"
    frontend.mkdir(parents=True)
    (application / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    (frontend / "package.json").write_text("{}", encoding="utf-8")

    def run(command: list[str], **_kwargs):
        stdout = "127.0.0.1:49152\n" if command[1] == "port" else "ok"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def http_get(url: str, _timeout: float) -> tuple[int, str, str]:
        if url.endswith("/assets/index.js"):
            return 200, "application/javascript", "console.log('ready')"
        return (
            200,
            "text/html",
            '<div id="root"></div><script src="/assets/index.js"></script>',
        )

    report = verify_container_runtime(
        tmp_path,
        run_command=run,
        probe=lambda *_args: True,
        http_get=http_get,
    )

    assert report["frontendRuntime"]["status"] == "SUCCEEDED"


def test_container_runtime_failure_keeps_the_last_http_probe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """프론트엔드 인증 오류를 wiring 수리에 전달할 수 있게 실제 원인을 보존한다."""
    application = tmp_path / "application"
    frontend = application / "frontend"
    frontend.mkdir(parents=True)
    (application / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    (frontend / "package.json").write_text("{}", encoding="utf-8")

    def run(command: list[str], **_kwargs):
        stdout = "127.0.0.1:49152\n" if command[1] == "port" else ""
        return subprocess.CompletedProcess(command, 0, stdout, "container log")

    calls = 0

    def http_get(url: str, _timeout: float) -> tuple[int, str, str]:
        nonlocal calls
        calls += 1
        if url.endswith("/actuator/health"):
            return 200, "application/json", '{"status":"UP"}'
        raise OSError("HTTP 401 Unauthorized")

    monkeypatch.setattr(
        "app.implementation.agents.verification.release.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(WorkspaceVerificationError) as raised:
        verify_container_runtime(
            tmp_path,
            run_command=run,
            probe=lambda *_args: True,
            http_get=http_get,
            startup_timeout_seconds=0.01,
        )

    # health 한 번과 frontend 한 번 뒤 바로 수리로 넘어간다. 같은 401을 90초 동안
    # 반복해서 묻지 않는다.
    assert calls == 2
    assert "frontend HTTP probe failed: HTTP 401 Unauthorized" in str(
        raised.value.evidence["stderr"]
    )


def test_container_runtime_smoke_builds_separate_frontend_image(
    tmp_path: Path,
) -> None:
    application = tmp_path / "application"
    frontend = application / "frontend"
    reports = tmp_path / "reports"
    frontend.mkdir(parents=True)
    reports.mkdir()
    (application / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    (frontend / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (reports / "deployment-intent.json").write_text(
        '{"frontend":{"mode":"separate"}}', encoding="utf-8"
    )
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs):
        commands.append(command)
        if command[1] == "port":
            stdout = "127.0.0.1:49153\n" if "frontend" in command[2] else "127.0.0.1:49152\n"
        else:
            stdout = "ok"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def http_get(url: str, _timeout: float) -> tuple[int, str, str]:
        if url.endswith(".js"):
            return 200, "application/javascript", "export {}"
        return 200, "text/html", '<div id="root"></div><script src="/app.js"></script>'

    report = verify_container_runtime(
        tmp_path,
        run_command=run,
        probe=lambda *_args: True,
        http_get=http_get,
    )

    assert report["status"] == "SUCCEEDED"
    assert report["frontendRuntime"]["mode"] == "separate"
    builds = [command for command in commands if command[1] == "build"]
    assert len(builds) == 2


def test_release_manifest_requires_every_verification_gate(tmp_path: Path) -> None:
    manifest = write_release_manifest(
        tmp_path,
        workflow={"status": "COMPLETE"},
        audit={"status": "COMPLETE"},
        verification={
            "status": "SUCCEEDED",
            "frontendVerification": None,
            "scenarioVerification": _passed_scenario(),
        },
        conformance={"status": "PASSED"},
        traceability={"summary": {"missing": 0}},
        deployment=None,
        iac=None,
        container_smoke={"status": "NOT_APPLICABLE"},
    )
    assert manifest["status"] == "RELEASABLE"
    assert manifest["deploymentStatus"] == "NOT_CONFIGURED"
    assert (tmp_path / "reports/release-manifest.json").is_file()

    blocked = write_release_manifest(
        tmp_path,
        workflow={"status": "COMPLETE"},
        audit={"status": "COMPLETE"},
        verification={
            "status": "SUCCEEDED",
            "frontendVerification": None,
            "scenarioVerification": _passed_scenario(),
        },
        conformance={"status": "PASSED"},
        traceability={"summary": {"missing": 1}},
        deployment=None,
        iac=None,
        container_smoke={"status": "NOT_APPLICABLE"},
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["failedChecks"] == ["traceability"]

    scenario_blocked = write_release_manifest(
        tmp_path,
        workflow={"status": "COMPLETE"},
        audit={"status": "COMPLETE"},
        verification={
            "status": "SUCCEEDED",
            "frontendVerification": None,
            "scenarioVerification": {
                "status": "PASSED",
                "tasks": [],
                "coveredUseCaseIds": [],
            },
        },
        conformance={"status": "PASSED"},
        traceability={"summary": {"missing": 0}},
        deployment=None,
        iac=None,
        container_smoke={"status": "NOT_APPLICABLE"},
    )
    assert scenario_blocked["status"] == "BUILDABLE"
    assert "useCaseScenarios" in scenario_blocked["failedChecks"]


def test_release_manifest_requires_frontend_build_and_http_runtime(
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "application/frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")

    blocked = write_release_manifest(
        tmp_path,
        workflow={"status": "COMPLETE"},
        audit={"status": "COMPLETE"},
        verification={
            "status": "SUCCEEDED",
            "frontendVerification": None,
            "scenarioVerification": _passed_scenario(),
        },
        conformance={"status": "PASSED"},
        traceability={"summary": {"missing": 0}},
        deployment=None,
        iac=None,
        container_smoke={"status": "NOT_APPLICABLE"},
    )

    assert blocked["status"] == "BLOCKED"
    assert blocked["failedChecks"] == [
        "frontendRuntime",
        "frontendVerification",
    ]
