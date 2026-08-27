"""호스트 오케스트레이터와 고정 Linux 멤버 runner 사이의 전송 경계."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

CONTAINER_WORKSPACE = PurePosixPath("/easydep-workspace")
RUNNER_IMAGE_ENV = "EASYDEP_MEMBER_RUNNER_IMAGE"
TRANSMITTED_ENVIRONMENT = (
    "API_KEY",
    "LLM_API_KEY",
    "LLM_MODEL",
    "NVIDIA_API_KEY",
    "NVIDIA_NIM_API_KEY",
    "OPENHANDS_MODEL",
    "OPENHANDS_MAX_OUTPUT_TOKENS",
    "OPENHANDS_PROVIDER_RETRY_BASE_SECONDS",
    "OPENHANDS_PROVIDER_RETRY_MAX_SECONDS",
    "IMPLEMENTATION_AGENT_MODEL",
    "IMPLEMENTATION_AGENT_BASE_URL",
    "IMPLEMENTATION_COMMAND_TIMEOUT_SECONDS",
    "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS",
    "IMPLEMENTATION_MAX_TASK_ATTEMPTS",
    "EASYDEP_MEMBER_CHECKPOINT_RUN",
)


def configured_runner_image(environment: dict[str, str] | None = None) -> str | None:
    value = (environment or os.environ).get(RUNNER_IMAGE_ENV, "").strip()
    return value or None


def to_container_path(path: Path, repository_root: Path) -> PurePosixPath:
    relative = path.resolve().relative_to(repository_root.resolve())
    return CONTAINER_WORKSPACE / relative.as_posix()


def to_host_path(value: str, repository_root: Path) -> str:
    normalized = value.replace("\\", "/")
    prefix = CONTAINER_WORKSPACE.as_posix()
    if normalized == prefix:
        return str(repository_root.resolve())
    if normalized.startswith(prefix + "/"):
        return str(repository_root.resolve() / normalized[len(prefix) + 1 :])
    return value


def runner_command(
    *,
    image: str,
    repository_root: Path,
    operation: str,
    arguments: Iterable[str],
    environment: dict[str, str],
) -> list[str]:
    root = repository_root.resolve()
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--label",
        "easydep.owner=member-runner",
        "-v",
        f"{root}:{CONTAINER_WORKSPACE.as_posix()}",
        "-e",
        f"PYTHONPATH={CONTAINER_WORKSPACE}/app/implementation/runtime/runtime_hooks:{CONTAINER_WORKSPACE}",
        "-e",
        "EASYDEP_FIXED_LINUX_RUNNER=1",
    ]
    experiment_session = environment.get("EASYDEP_EXPERIMENT_SESSION", "").strip()
    if experiment_session:
        volume_index = command.index("-v")
        command[volume_index:volume_index] = [
            "--label",
            f"easydep.experiment-session={experiment_session}",
        ]
    for name in TRANSMITTED_ENVIRONMENT:
        if environment.get(name):
            command.extend(["-e", name])
    command.extend([image, operation, *arguments])
    return command
