from __future__ import annotations

import subprocess
from pathlib import Path

from app.implementation.agents.verification.release import verify_container_runtime
from app.implementation.workflows.release import write_release_manifest


def test_container_runtime_smoke_builds_starts_probes_and_cleans(tmp_path: Path) -> None:
    application = tmp_path / "application"
    application.mkdir()
    (application / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs):
        commands.append(command)
        stdout = "127.0.0.1:49152\n" if command[1] == "port" else "ok"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    report = verify_container_runtime(
        tmp_path,
        run_command=run,
        probe=lambda host, port, timeout: (host, port, timeout)
        == ("127.0.0.1", 49152, 1.0),
    )

    assert report["status"] == "SUCCEEDED"
    assert report["hostPort"] == 49152
    assert any(command[1] == "build" for command in commands)
    assert any(command[1:3] == ["image", "rm"] for command in commands)


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


def test_container_runtime_smoke_builds_separate_frontend_image(
    tmp_path: Path,
) -> None:
    application = tmp_path / "application"
    frontend = application / "frontend"
    k8s = application / "k8s"
    frontend.mkdir(parents=True)
    k8s.mkdir()
    (application / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    (frontend / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (k8s / "deployment-intent.json").write_text(
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
        verification={"status": "SUCCEEDED", "frontendVerification": None},
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
        verification={"status": "SUCCEEDED", "frontendVerification": None},
        conformance={"status": "PASSED"},
        traceability={"summary": {"missing": 1}},
        deployment=None,
        iac=None,
        container_smoke={"status": "NOT_APPLICABLE"},
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["failedChecks"] == ["traceability"]


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
        verification={"status": "SUCCEEDED", "frontendVerification": None},
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
