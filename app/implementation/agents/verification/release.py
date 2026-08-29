from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from urllib.request import Request, urlopen

from app.implementation.config import DEFAULT_CONTAINER_PORT


def verify_container_runtime(
    run_root: Path,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    probe: Callable[[str, int, float], bool] | None = None,
    http_get: Callable[[str, float], tuple[int, str, str]] | None = None,
    startup_timeout_seconds: int = 90,
) -> dict[str, object]:
    """Build the image and prove its backend and packaged frontend are reachable."""
    application = run_root / "application"
    frontend_required = (application / "frontend" / "package.json").is_file()
    separate_frontend = _separate_frontend_mode(run_root)
    dockerfile = application / "Dockerfile"
    report_path = run_root / "reports" / "container-runtime-smoke.json"
    if not dockerfile.is_file():
        report = {
            "schemaVersion": "implementation-container-smoke/v1alpha1",
            "status": "NOT_APPLICABLE",
            "reason": "Generated application has no Dockerfile.",
            "frontendRequired": frontend_required,
            "frontendRuntime": None,
        }
        _write_report(report_path, report)
        return report

    readable = re.sub(r"[^a-z0-9]", "", run_root.name.lower())[-10:] or "run"
    identity = hashlib.sha256(str(run_root.resolve()).encode("utf-8")).hexdigest()[:10]
    suffix = f"{readable}-{identity}"
    image = f"easydep-release-smoke:{suffix}"
    container = f"easydep-release-smoke-{suffix}"
    frontend_image = f"easydep-frontend-smoke:{suffix}"
    frontend_container = f"easydep-frontend-smoke-{suffix}"
    commands: list[list[str]] = []
    started = time.monotonic()
    error = ""
    status = "FAILED"
    port = 0
    probe = probe or _tcp_probe
    http_get = http_get or _http_get
    frontend_runtime: dict[str, object] | None = None
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
                    if frontend_required and not separate_frontend:
                        try:
                            frontend_runtime = _verify_frontend_http(
                                port, http_get
                            )
                        except (OSError, RuntimeError):
                            time.sleep(1)
                            continue
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
        if frontend_required and separate_frontend:
            frontend_runtime = _verify_separate_frontend_container(
                application=application,
                image=frontend_image,
                container=frontend_container,
                commands=commands,
                run_command=run_command,
                probe=probe,
                http_get=http_get,
                startup_timeout_seconds=startup_timeout_seconds,
            )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        status = "FAILED"
        error = str(exc)
    finally:
        for cleanup in (
            ["docker", "stop", "--time", "5", frontend_container],
            ["docker", "stop", "--time", "5", container],
            ["docker", "image", "rm", "--force", frontend_image],
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
        "frontendRequired": frontend_required,
        "frontendRuntime": frontend_runtime,
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


def _separate_frontend_mode(run_root: Path) -> bool:
    """현재 렌더러가 기록한 보고서에서 프런트엔드 실행 방식을 읽는다."""
    intent_path = run_root / "reports" / "deployment-intent.json"
    if not intent_path.is_file():
        return False
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    frontend = intent.get("frontend", {})
    return isinstance(frontend, dict) and frontend.get("mode") == "separate"


def _verify_separate_frontend_container(
    *,
    application: Path,
    image: str,
    container: str,
    commands: list[list[str]],
    run_command: Callable[..., subprocess.CompletedProcess[str]],
    probe: Callable[[str, int, float], bool],
    http_get: Callable[[str, float], tuple[int, str, str]],
    startup_timeout_seconds: int,
) -> dict[str, object]:
    frontend = application / "frontend"
    build = [
        "docker", "build", "--build-arg", "VITE_API_BASE_URL=http://backend.invalid",
        "--tag", image, ".",
    ]
    commands.append(build)
    result = run_command(
        build, cwd=frontend, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1200, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
    start = [
        "docker", "run", "--detach", "--rm", "--name", container,
        "--publish", "127.0.0.1::8080", image,
    ]
    commands.append(start)
    result = run_command(
        start, cwd=frontend, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
    port_command = ["docker", "port", container, "8080/tcp"]
    commands.append(port_command)
    deadline = time.monotonic() + startup_timeout_seconds
    while time.monotonic() < deadline:
        mapped = run_command(
            port_command, cwd=frontend, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
        )
        match = re.search(r":(\d+)\s*$", mapped.stdout.strip())
        if match:
            port = int(match.group(1))
            if probe("127.0.0.1", port, 1.0):
                try:
                    runtime = _verify_frontend_http(port, http_get)
                    runtime["mode"] = "separate"
                    return runtime
                except (OSError, RuntimeError):
                    pass
        time.sleep(1)
    raise RuntimeError("Separate frontend container did not become HTTP-ready")


def _http_get(url: str, timeout: float) -> tuple[int, str, str]:
    request = Request(url, headers={"User-Agent": "EasyDep-runtime-verifier"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback URL
        body = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        return response.status, response.headers.get("Content-Type", ""), body


def _verify_frontend_http(
    port: int,
    http_get: Callable[[str, float], tuple[int, str, str]],
) -> dict[str, object]:
    origin = f"http://127.0.0.1:{port}"
    status, content_type, body = http_get(origin + "/", 3.0)
    if status < 200 or status >= 300 or not re.search(
        r'<div[^>]+id=["\']root["\']', body, re.IGNORECASE
    ):
        raise RuntimeError("Container root did not serve the generated frontend index")
    asset_match = re.search(
        r'<script[^>]+src=["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',
        body,
        re.IGNORECASE,
    )
    if not asset_match:
        raise RuntimeError("Frontend index did not reference a JavaScript bundle")
    asset_path = asset_match.group(1)
    asset_url = asset_path if asset_path.startswith("http") else origin + "/" + asset_path.lstrip("/")
    asset_status, asset_type, asset_body = http_get(asset_url, 3.0)
    if asset_status < 200 or asset_status >= 300 or not asset_body.strip():
        raise RuntimeError("Frontend JavaScript bundle was not served")
    if "javascript" not in asset_type.lower():
        raise RuntimeError(f"Frontend bundle has unexpected content type: {asset_type}")
    return {
        "status": "SUCCEEDED",
        "indexUrl": origin + "/",
        "indexContentType": content_type,
        "assetUrl": asset_url,
        "assetContentType": asset_type,
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
