"""Application-test boundary for the generated implementation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core.orchestration.app_cloud_contracts import (
    ApplicationRuntimeContract,
    test_environment,
)
from app.core.orchestration.linux_runner_transport import (
    configured_runner_image,
    runner_command,
    to_container_path,
)
from app.core.orchestration.process import run_process_tree

_COMPILED_SOURCE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^\r\n:]*?\.(?:java|kt))(?::\d+)?(?:\s|:|$)",
    re.IGNORECASE,
)
_COMPILE_FAILURE_MARKERS = (
    "compilation failed",
    "compilejava failed",
    "compilekotlin failed",
)
_OWNER_FILE_FIELDS = (
    ("implementation.logic", "files"),
    ("implementation.acceptance_tests", "acceptance_tests"),
    ("implementation.scaffold", "scaffold_files"),
)
_OWNER_DIAGNOSTICS = {
    "implementation.logic": "APP-COMPILE-LOGIC-001",
    "implementation.acceptance_tests": "APP-COMPILE-ACCEPTANCE-001",
    "implementation.scaffold": "APP-COMPILE-SCAFFOLD-001",
}


def _relative_source_paths(output: str, repository: Path) -> list[str]:
    root = repository.resolve()
    found: set[str] = set()
    for match in _COMPILED_SOURCE.finditer(output):
        raw = match.group("path").strip().strip('"').replace("\\", "/")
        path = Path(raw)
        if path.is_absolute():
            try:
                raw = path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
        else:
            marker = "/application/"
            normalized = "/" + raw.lstrip("/")
            if marker in normalized:
                raw = normalized.split(marker, 1)[1]
            raw = raw.lstrip("./")
        if raw.startswith("src/main/") or raw.startswith("src/test/"):
            found.add(raw)
    return sorted(found)


def _compile_owner_diagnostic(
    unit_tests: dict[str, Any],
    implementation_result: dict[str, Any],
    repository: Path,
) -> dict[str, Any] | None:
    output = "\n".join(
        str(unit_tests.get(field) or "") for field in ("stdout", "stderr")
    )
    lowered = output.lower()
    if not any(marker in lowered for marker in _COMPILE_FAILURE_MARKERS):
        return None
    failed_files = _relative_source_paths(output, repository)
    if not failed_files:
        return None
    failed = set(failed_files)
    for owner, field in _OWNER_FILE_FIELDS:
        owned = {
            str(item).replace("\\", "/").lstrip("./")
            for item in implementation_result.get(field) or []
        }
        matched = sorted(failed & owned)
        if matched:
            return {
                "code": _OWNER_DIAGNOSTICS[owner],
                "message": "Generated source compilation failed in files owned by an implementation subtask.",
                "repairOwner": owner,
                "failedFiles": failed_files,
                "ownedFailedFiles": matched,
            }
    return None


def _run(
    command: list[str],
    cwd: Path,
    timeout: int,
    environment_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    environment = os.environ.copy()
    started_at = datetime.now(UTC)
    started = perf_counter()
    # Reuse the wrapper distribution already used by the member implementation
    # workflow. An empty run-local cache would force a network download at the
    # testing boundary and make an offline rerun look like a test failure.
    environment.setdefault("GRADLE_USER_HOME", str(Path.home() / ".gradle"))
    environment.update(environment_overrides or {})
    try:
        completed = run_process_tree(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "status": "timeout",
            "command": command,
            "error": str(error),
            "startedAt": started_at.isoformat(),
            "finishedAt": datetime.now(UTC).isoformat(),
            "elapsedSeconds": round(perf_counter() - started, 6),
        }
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "exitCode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "startedAt": started_at.isoformat(),
        "finishedAt": datetime.now(UTC).isoformat(),
        "elapsedSeconds": round(perf_counter() - started, 6),
    }


class TestingAdapter:
    """Run the generated application's tests without benchmark knowledge."""

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _repository(implementation_result: dict[str, Any]) -> Path:
        run_root = Path(str(implementation_result.get("run_root") or ""))
        repository = run_root / "application"
        if not repository.is_dir():
            raise ValueError(f"Generated application repository is absent: {repository}")
        return repository

    def _unit_tests(
        self,
        repository: Path,
        runtime_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        test_root = repository / "src" / "test"
        test_files = (
            [path for path in test_root.rglob("*") if path.suffix in {".java", ".kt"}]
            if test_root.is_dir()
            else []
        )
        if not test_files:
            return {
                "status": "failed",
                "reason": "No Java or Kotlin acceptance tests were generated below src/test.",
                "testFiles": [],
            }

        wrapper = repository / "gradlew.bat"
        if wrapper.is_file():
            command = [str(wrapper), "test", "--no-daemon"]
        else:
            gradle = shutil.which("gradle")
            if gradle and (repository / "build.gradle").is_file():
                command = [gradle, "test", "--no-daemon"]
            else:
                try:
                    from app.implementation.agents.verification.build import gradle_command

                    command = [*gradle_command(), "test", "--no-daemon"]
                except RuntimeError as error:
                    return {"status": "unavailable", "reason": str(error)}
        contract = ApplicationRuntimeContract.model_validate(runtime_contract or {})
        with tempfile.TemporaryDirectory(
            prefix=".easydep-test-", dir=repository
        ) as temporary:
            environment = test_environment(contract, Path(temporary)) or None
            result = _run(
                command,
                repository,
                self.timeout_seconds,
                environment,
            )
        result["testFiles"] = [str(path.relative_to(repository)) for path in test_files]
        return result

    def run(
        self,
        *,
        implementation_result: dict[str, Any],
        case_id: str = "adhoc",  # noqa: ARG002 - stable adapter contract
    ) -> dict[str, Any]:
        member_runner = implementation_result.get("member_runner") or {}
        runner_image = member_runner.get("image") or configured_runner_image()
        if runner_image:
            return self._run_in_member_runner(
                implementation_result, case_id, str(runner_image)
            )
        repository = self._repository(implementation_result)
        runtime_contract = implementation_result.get("application_runtime_contract")
        unit_tests = (
            self._unit_tests(repository, runtime_contract)
            if runtime_contract
            else self._unit_tests(repository)
        )
        diagnostics = self._diagnostics(unit_tests, implementation_result, repository)
        return {
            "status": "completed",
            "passed": unit_tests.get("status") == "passed",
            "repository": str(repository),
            "unitTests": unit_tests,
            "diagnostics": diagnostics,
        }

    def _run_in_member_runner(
        self,
        implementation_result: dict[str, Any],
        case_id: str,
        image: str,
    ) -> dict[str, Any]:
        repository = self._repository(implementation_result)
        run_root = repository.parent.resolve()
        project_root = Path(__file__).resolve().parents[4]
        request_result = dict(implementation_result)
        request_result["run_root"] = str(to_container_path(run_root, project_root))
        request_path = run_root / "reports" / "outer-test-request.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(
            json.dumps(
                {
                    "implementationResult": request_result,
                    "caseId": case_id,
                    "timeoutSeconds": self.timeout_seconds,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        command = runner_command(
            image=image,
            repository_root=project_root,
            operation="test",
            arguments=[str(to_container_path(request_path, project_root))],
            environment=environment,
        )
        completed = run_process_tree(
            command,
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds + 60,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "status": "completed",
                "passed": False,
                "repository": str(repository),
                "unitTests": {
                    "status": "failed",
                    "command": command,
                    "exitCode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                },
                "diagnostics": [
                    {
                        "code": "APPLICATION_RUNNER_FAILED",
                        "message": "Linux runner에서 애플리케이션 테스트를 실행하지 못했습니다.",
                    }
                ],
            }
        for line in reversed(completed.stdout.splitlines()):
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict) and "passed" in result:
                result["repository"] = str(repository)
                result["memberRunner"] = {"kind": "linux-container", "image": image}
                return result
        raise RuntimeError("Linux member runner returned no structured test result")

    @staticmethod
    def _diagnostics(
        unit_tests: dict[str, Any],
        implementation_result: dict[str, Any] | None = None,
        repository: Path | None = None,
    ) -> list[dict[str, Any]]:
        if unit_tests.get("status") == "passed":
            return []
        if implementation_result is not None and repository is not None:
            ownership = _compile_owner_diagnostic(
                unit_tests, implementation_result, repository
            )
            if ownership is not None:
                return [ownership]
        output = "\n".join(
            str(unit_tests.get(field) or "") for field in ("stdout", "stderr", "reason")
        ).lower()
        if any(
            marker in output
            for marker in (
                " does not exist",
                "cannot find symbol",
                "could not resolve all files",
            )
        ):
            code = "APP-DEP-001"
        elif any(
            marker in output
            for marker in (
                "no suitable driver",
                "unable to determine dialect",
                "failed to determine a suitable driver",
                "classnotfoundexception: org.sqlite",
                "hibernate.dialect",
            )
        ) or (
            "strategyselectionexception" in output
            and "classnotfoundexception" in output
        ):
            code = "APP-DB-001"
        else:
            code = "APPLICATION_TESTS_FAILED"
        return [{"code": code, "message": "Generated application tests failed."}]
