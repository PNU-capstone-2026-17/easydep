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
