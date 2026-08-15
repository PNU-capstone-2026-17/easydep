"""Docker transport dedicated to application testing.

This intentionally has no dependency on the implementation/orchestration
runner.  The image is shared, but test execution uses a testing-owned Python
entry point and a minimal environment.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath


CONTAINER_WORKSPACE = PurePosixPath("/easydep-workspace")
RUNNER_IMAGE_ENV = "EASYDEP_MEMBER_RUNNER_IMAGE"


def configured_runner_image(environment: dict[str, str] | None = None) -> str | None:
    value = (environment or os.environ).get(RUNNER_IMAGE_ENV, "").strip()
    return value or None


def to_container_path(path: Path, repository_root: Path) -> PurePosixPath:
    relative = path.resolve().relative_to(repository_root.resolve())
    return CONTAINER_WORKSPACE / relative.as_posix()


def runner_command(
    *,
    image: str,
    repository_root: Path,
    operation: str,
    arguments: Iterable[str],
    environment: dict[str, str],
) -> list[str]:
    """Start the test-only runner inside the fixed Linux image."""
    root = repository_root.resolve()
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--label",
        "easydep.owner=testing-runner",
        "-v",
        f"{root}:{CONTAINER_WORKSPACE.as_posix()}",
        "-e",
        f"PYTHONPATH={CONTAINER_WORKSPACE}",
        "--entrypoint",
        "python",
        image,
        "-B",
        "-m",
        "app.testing.runtime.member_linux_runner",
        operation,
        *arguments,
    ]
    experiment_session = environment.get("EASYDEP_EXPERIMENT_SESSION", "").strip()
    if experiment_session:
        volume_index = command.index("-v")
        command[volume_index:volume_index] = [
            "--label",
            f"easydep.experiment-session={experiment_session}",
        ]
    return command
