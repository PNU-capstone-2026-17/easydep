from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from app.implementation.config import DEFAULT_CONTAINER_PORT


def verify_container_runtime(
    run_root: Path,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    probe: Callable[[str, int, float], bool] | None = None,
    startup_timeout_seconds: int = 90,
) -> dict[str, object]:
    """Build the generated image and prove that its application port accepts TCP."""
    application = run_root / "application"
    dockerfile = application / "Dockerfile"
    report_path = run_root / "reports" / "container-runtime-smoke.json"
    if not dockerfile.is_file():
        report = {
            "schemaVersion": "implementation-container-smoke/v1alpha1",
            "status": "NOT_APPLICABLE",
            "reason": "Generated application has no Dockerfile.",
        }
        _write_report(report_path, report)
        return report

    readable = re.sub(r"[^a-z0-9]", "", run_root.name.lower())[-10:] or "run"
    identity = hashlib.sha256(str(run_root.resolve()).encode("utf-8")).hexdigest()[:10]
    suffix = f"{readable}-{identity}"
    image = f"easydep-release-smoke:{suffix}"
    container = f"easydep-release-smoke-{suffix}"
    commands: list[list[str]] = []
    started = time.monotonic()
    error = ""
    status = "FAILED"
    port = 0
    probe = probe or _tcp_probe
    try:
        build = ["docker", "build", "--tag", image, "."]
        commands.append(build)
        result = run_command(
            build,
            cwd=application,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1200,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])

        start = [
            "docker", "run", "--detach", "--rm", "--name", container,
            "--publish", f"127.0.0.1::{DEFAULT_CONTAINER_PORT}", image,
        ]
        commands.append(start)
        result = run_command(
            start,
            cwd=application,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])

        port_command = [
            "docker", "port", container, f"{DEFAULT_CONTAINER_PORT}/tcp"
        ]
        commands.append(port_command)
        deadline = time.monotonic() + startup_timeout_seconds
        while time.monotonic() < deadline:
            mapped = run_command(
                port_command,
                cwd=application,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            match = re.search(r":(\d+)\s*$", mapped.stdout.strip())
            if match:
                port = int(match.group(1))
                if probe("127.0.0.1", port, 1.0):
                    status = "SUCCEEDED"
                    break
            time.sleep(1)
        if status != "SUCCEEDED":
            logs = run_command(
                ["docker", "logs", "--tail", "200", container],
                cwd=application,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            raise RuntimeError(
                "Container did not accept connections before timeout: "
                + (logs.stderr[-3000:] or logs.stdout[-3000:])
            )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        error = str(exc)
    finally:
        for cleanup in (
            ["docker", "stop", "--time", "5", container],
            ["docker", "image", "rm", "--force", image],
        ):
            try:
                run_command(
                    cleanup,
                    cwd=application,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass

    report = {
        "schemaVersion": "implementation-container-smoke/v1alpha1",
        "status": status,
        "durationMs": int((time.monotonic() - started) * 1000),
        "containerPort": DEFAULT_CONTAINER_PORT,
        "hostPort": port or None,
        "commands": commands,
        "error": error or None,
    }
    _write_report(report_path, report)
    if status != "SUCCEEDED":
        raise RuntimeError("Generated container runtime smoke failed: " + error)
    return report


def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
