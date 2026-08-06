"""Application-test boundary for the generated implementation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.orchestration.process import run_process_tree


def _run(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    environment = os.environ.copy()
    # Reuse the wrapper distribution already used by the member implementation
    # workflow. An empty run-local cache would force a network download at the
    # testing boundary and make an offline rerun look like a test failure.
    environment["GRADLE_USER_HOME"] = str(Path.home() / ".gradle")
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
        return {"status": "timeout", "command": command, "error": str(error)}
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "exitCode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
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

    def _unit_tests(self, repository: Path) -> dict[str, Any]:
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
            result = _run(
                [str(wrapper), "test", "--no-daemon"],
                repository,
                self.timeout_seconds,
            )
            result["testFiles"] = [str(path.relative_to(repository)) for path in test_files]
            return result
        gradle = shutil.which("gradle")
        if gradle and (repository / "build.gradle").is_file():
            result = _run(
                [gradle, "test", "--no-daemon"],
                repository,
                self.timeout_seconds,
            )
            result["testFiles"] = [str(path.relative_to(repository)) for path in test_files]
            return result
        try:
            from app.implementation.engine.agent_runtime import gradle_command

            bundled = gradle_command()
        except RuntimeError as error:
            return {"status": "unavailable", "reason": str(error)}
        result = _run(
            [*bundled, "test", "--no-daemon"],
            repository,
            self.timeout_seconds,
        )
        result["testFiles"] = [str(path.relative_to(repository)) for path in test_files]
        return result

    def run(
        self,
        *,
        implementation_result: dict[str, Any],
        case_id: str = "adhoc",  # noqa: ARG002 - stable adapter contract
    ) -> dict[str, Any]:
        repository = self._repository(implementation_result)
        unit_tests = self._unit_tests(repository)
        return {
            "status": "completed",
            "passed": unit_tests.get("status") == "passed",
            "repository": str(repository),
            "unitTests": unit_tests,
        }
