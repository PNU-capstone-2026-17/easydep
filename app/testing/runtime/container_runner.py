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
RUNNER_IMAGE_ENV = "EASYDEP_TOOLCHAIN_IMAGE"
GRADLE_CACHE_VOLUME = "easydep-member-gradle-cache"
TOFU_CACHE_VOLUME = "easydep-tofu-provider-cache"
TOFU_CACHE_PATH = "/app/.cache/opentofu"


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
        # 구현 작업이 이미 받은 Gradle 배포본과 dependency를 Testing에서도 재사용한다.
        # 생성 애플리케이션 source는 고정 snapshot이지만 도구 cache까지 매번 버릴 필요는 없다.
        "-v",
        f"{GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache",
        "-v",
        f"{TOFU_CACHE_VOLUME}:{TOFU_CACHE_PATH}",
        "-e",
        f"PYTHONPATH={CONTAINER_WORKSPACE}",
        "-e",
        "GRADLE_USER_HOME=/tmp/easydep-gradle-cache",
        "-e",
        f"EASYDEP_TOFU_PLUGIN_CACHE={TOFU_CACHE_PATH}",
        "-e",
        f"TF_PLUGIN_CACHE_DIR={TOFU_CACHE_PATH}",
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
