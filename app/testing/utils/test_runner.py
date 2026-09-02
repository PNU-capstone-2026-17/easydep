"""Run generated tests in the prebuilt EasyDep testing toolchain."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.testing.runtime.process import run_process_tree

TOOLCHAIN_IMAGE_ENV = "EASYDEP_TESTING_TOOLCHAIN_IMAGE"
DEFAULT_TOOLCHAIN_IMAGE = "easydep-testing-toolchain:local"


def configured_toolchain_image(environment: dict[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    testing_image = str(source.get(TOOLCHAIN_IMAGE_ENV) or "").strip()
    if testing_image:
        return testing_image
    # 명시적으로 기존 단일 이미지를 지정한 환경은 그대로 지원한다. 기본 개발 환경만
    # 브라우저가 분리된 Testing 전용 이미지를 선택한다.
    configured_shared = str(source.get("EASYDEP_TOOLCHAIN_IMAGE") or "").strip()
    return configured_shared or DEFAULT_TOOLCHAIN_IMAGE


def _network_name(target_url: str) -> str:
    suffix = hashlib.sha256(f"{target_url}\0{uuid.uuid4().hex}".encode()).hexdigest()[:20]
    return f"easydep-testing-{suffix}"


def _docker(arguments: list[str], *, cwd: Path | None = None, timeout: int = 60):
    return run_process_tree(
        ["docker", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def run_dynamic_test(
    code: str,
    target_url: str,
    repository_root: Path,
    *,
    timeout_seconds: int = 300,
    toolchain_image: str | None = None,
    network_name: str | None = None,
) -> dict[str, Any]:
    """Execute candidate code without package installation or writable source mounts.

    The generated application is mounted read-only. Only a system temporary output
    directory is writable, and Docker resources are unique to this invocation.
    """
    application = Path(repository_root).resolve()
    if not application.is_dir():
        return {"status": "unavailable", "gateStatus": "INCONCLUSIVE", "reason": f"Application directory is absent: {application}"}
    target = str(target_url)
    if "localhost" in target or "127.0.0.1" in target:
        target = target.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
    image = (toolchain_image or configured_toolchain_image()).strip()
    network = network_name or _network_name(target)
    owns_network = network_name is None
    with tempfile.TemporaryDirectory(prefix="easydep-testing-output-") as output:
        output_dir = Path(output)
        test_path = output_dir / "test_dynamic.py"
        report_path = output_dir / "report.json"
        test_path.write_text(code, encoding="utf-8")
        if owns_network:
            try:
                created = _docker(["network", "create", network], timeout=60)
            except Exception as error:
                return {"status": "unavailable", "gateStatus": "INCONCLUSIVE", "reason": f"Testing network could not be created: {error}"}
            if created.returncode != 0:
                return {"status": "unavailable", "gateStatus": "INCONCLUSIVE", "reason": "Testing network could not be created", "stderr": (created.stderr or "")[-2000:]}
        try:
            command = [
                "run",
                "--rm",
                "--init",
                "--network",
                network,
                "--add-host=host.docker.internal:host-gateway",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev",  # noqa: S108 - container-local tmpfs
                "--cpus=1.0",
                "--memory=1g",
                "--pids-limit=128",
                "--mount",
                f"type=bind,src={application},dst=/easydep-app,readonly",
                "--mount",
                f"type=bind,src={output_dir.resolve()},dst=/easydep-output,rw",
                "-e",
                f"TARGET_URL={target}",
                "-e",
                "EASYDEP_TEST_USERNAME=easydep-test",
                "-e",
                "EASYDEP_TEST_PASSWORD=easydep-test",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                image,
                "python",
                "-B",
                "-m",
                "pytest",
                "/easydep-output/test_dynamic.py",
                "--json-report",
                "--json-report-file=/easydep-output/report.json",
                "-q",
            ]
            try:
                completed = _docker(command, cwd=application, timeout=timeout_seconds)
            except Exception as error:
                return {"status": "unavailable", "gateStatus": "INCONCLUSIVE", "reason": f"Testing runner failed to start: {error}"}
            report_data: dict[str, Any] = {}
            if report_path.is_file():
                try:
                    parsed = json.loads(report_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        report_data = parsed
                except json.JSONDecodeError:
                    report_data = {}
            status = "passed" if completed.returncode == 0 else "failed"
            result = {
                "status": status,
                "gateStatus": "PASS" if completed.returncode == 0 else "FAIL",
                "exit_code": completed.returncode,
                "stdout": (completed.stdout or "")[-4000:],
                "stderr": (completed.stderr or "")[-4000:],
                "report": report_data,
                "network": network,
                "toolchainImage": image,
            }
            # A process can exit successfully while pytest-json-report is absent or
            # malformed. The generated test was not evidenced in that case.
            if completed.returncode == 0 and not report_data:
                result.update({"status": "unavailable", "gateStatus": "INCONCLUSIVE", "reason": "Pytest did not produce a JSON report."})
            return result
        finally:
            if owns_network:
                try:
                    _docker(["network", "rm", network], timeout=60)
                except Exception:
                    pass


__all__ = ["DEFAULT_TOOLCHAIN_IMAGE", "configured_toolchain_image", "run_dynamic_test"]
