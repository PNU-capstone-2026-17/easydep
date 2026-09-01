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


def _cli(arguments: list[str]) -> int:
    """호스트가 승인한 workflow phase를 현재 Linux 환경에서 그대로 실행한다."""
    _configure_runner_tools()
    from app.implementation.interfaces.cli import main as cli_main

    if not arguments:
        raise SystemExit("cli requires an implementation command")
    runner_arguments = list(arguments)
    if runner_arguments[0] in {"plan-workflow", "run-workflow"}:
        if len(runner_arguments) < 3:
            raise SystemExit(f"{runner_arguments[0]} requires run and job paths")
        # job.json의 workspaceRoot는 호스트 절대 경로다. Linux에서 그 문자열을 그대로
        # Path로 해석하면 존재하지 않는 디렉터리가 되므로, 같은 입력을 가리키는 runner
        # 전용 사본만 만들고 현재 bind mount 경로를 기준으로 읽는다.
        runner_arguments[2] = str(_runner_job(Path(runner_arguments[2])))
    return cli_main(runner_arguments)


def _test(arguments: list[str]) -> int:
    _configure_runner_tools()
    from app.testing.runtime.adapter import TestingAdapter

    if len(arguments) != 1:
        raise SystemExit("test requires an input JSON path")
    request = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    implementation_result = dict(request["implementationResult"])
    implementation_result.pop("member_runner", None)
    result = TestingAdapter(timeout_seconds=int(request.get("timeoutSeconds", 600))).run(
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
        process_result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        observed[name] = {
            "passed": process_result.returncode == 0,
            "version": (process_result.stdout or process_result.stderr).strip()[:1000],
        }
    jars = {
        "openapiGenerator7.24.0": Path("/opt/easydep/openapi-generator-7.24.0.jar").is_file(),
        "openapiGenerator7.14.0": Path("/opt/easydep/openapi-generator-7.14.0.jar").is_file(),
    }
    preflight_result = {
        "schemaVersion": "easydep-member-runner-preflight/v1",
        "workspaceBindPassed": (RUNNER_WORKSPACE / "pyproject.toml").is_file(),
        "tools": observed,
        "artifacts": jars,
    }
    print(json.dumps(preflight_result, ensure_ascii=False))
    return (
        0
        if preflight_result["workspaceBindPassed"]
        and all(item["passed"] for item in observed.values())
        and all(jars.values())
        else 1
    )


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    if not arguments:
        raise SystemExit("usage: member_linux_runner {worker|cli|test|preflight} ...")
    if arguments[0] == "worker":
        return _worker(arguments[1:])
    if arguments[0] == "cli":
        return _cli(arguments[1:])
    if arguments[0] == "test":
        return _test(arguments[1:])
    if arguments[0] == "preflight":
        return _preflight(arguments[1:])
    raise SystemExit(f"unsupported runner operation: {arguments[0]}")


if __name__ == "__main__":
    raise SystemExit(main())
