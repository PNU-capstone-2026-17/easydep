"""고정 Linux 환경에서 멤버 구현 작업과 최종 테스트를 실행한다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.implementation.runtime.runner_compat import gradle_command, install

RUNNER_WORKSPACE = Path("/easydep-workspace")


def _configure_runner_tools() -> None:
    install()


def _runner_job(job_path: Path) -> Path:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["workspaceRoot"] = str(RUNNER_WORKSPACE)
    target = job_path.with_name("runner-job.json")
    target.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _worker(arguments: list[str]) -> int:
    _configure_runner_tools()
    from app.implementation.runtime.scaffold_worker import main as worker_main

    if not arguments:
        raise SystemExit("worker requires a job path")
    return worker_main([str(_runner_job(Path(arguments[0]))), *arguments[1:]])


def _test(arguments: list[str]) -> int:
    _configure_runner_tools()
    from app.orchestration.adapters.testing import TestingAdapter

    if len(arguments) != 1:
        raise SystemExit("test requires an input JSON path")
    request = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    implementation_result = dict(request["implementationResult"])
    implementation_result.pop("member_runner", None)
    result = TestingAdapter(
        timeout_seconds=int(request.get("timeoutSeconds", 600))
    ).run(
        implementation_result=implementation_result,
        case_id=str(request.get("caseId", "adhoc")),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _preflight(arguments: list[str]) -> int:
    if arguments:
        raise SystemExit("preflight accepts no arguments")
    commands = {
        "python": ["python", "--version"],
        "java": ["java", "-version"],
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "gradle": [
            *gradle_command(),
            "--version",
            "--no-daemon",
        ],
    }
    observed: dict[str, dict[str, object]] = {}
    for name, command in commands.items():
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        observed[name] = {
            "passed": result.returncode == 0,
            "version": (result.stdout or result.stderr).strip()[:1000],
        }
    jars = {
        "openapiGenerator7.24.0": Path("/opt/easydep/openapi-generator-7.24.0.jar").is_file(),
        "openapiGenerator7.14.0": Path("/opt/easydep/openapi-generator-7.14.0.jar").is_file(),
    }
    result = {
        "schemaVersion": "easydep-member-runner-preflight/v1",
        "workspaceBindPassed": (RUNNER_WORKSPACE / "pyproject.toml").is_file(),
        "tools": observed,
        "artifacts": jars,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["workspaceBindPassed"] and all(
        item["passed"] for item in observed.values()
    ) and all(jars.values()) else 1


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    if not arguments:
        raise SystemExit("usage: member_linux_runner {worker|test|preflight} ...")
    if arguments[0] == "worker":
        return _worker(arguments[1:])
    if arguments[0] == "test":
        return _test(arguments[1:])
    if arguments[0] == "preflight":
        return _preflight(arguments[1:])
    raise SystemExit(f"unsupported runner operation: {arguments[0]}")


if __name__ == "__main__":
    raise SystemExit(main())
