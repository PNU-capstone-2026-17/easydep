"""호스트 오케스트레이터와 고정 Linux 멤버 runner 사이의 전송 경계."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from app.config import settings

CONTAINER_WORKSPACE = PurePosixPath("/easydep-workspace")
RUNNER_IMAGE_ENV = "EASYDEP_TOOLCHAIN_IMAGE"
RUNNER_GRADLE_CACHE_VOLUME = "easydep-member-gradle-cache"
RUNNER_TOFU_CACHE_VOLUME = "easydep-tofu-provider-cache"
RUNNER_TOFU_CACHE_PATH = "/app/.cache/opentofu"
TRANSMITTED_ENVIRONMENT = (
    "API_KEY",
    "BASE_URL",
    "MODEL",
    "OPENHANDS_MAX_OUTPUT_TOKENS",
    "OPENHANDS_PROVIDER_RETRY_BASE_SECONDS",
    "OPENHANDS_PROVIDER_RETRY_MAX_SECONDS",
    "IMPLEMENTATION_COMMAND_TIMEOUT_SECONDS",
    "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS",
    "IMPLEMENTATION_MAX_TASK_ATTEMPTS",
    "EASYDEP_MEMBER_CHECKPOINT_RUN",
)


def configured_runner_image(environment: dict[str, str] | None = None) -> str | None:
    source = os.environ if environment is None else environment
    value = source.get(RUNNER_IMAGE_ENV, "").strip()
    if not value and environment is None:
        value = (settings.easydep_toolchain_image or "").strip()
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
        # 컨테이너가 끝나도 Gradle 배포본과 Maven dependency를 남긴다. 구현 Job마다
        # 130MB가 넘는 배포본을 다시 받거나 Windows bind mount에서 수천 파일을 읽지 않는다.
        "-v",
        f"{RUNNER_GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache",
        # OpenTofu Provider는 용량이 크므로 작업 컨테이너마다 다시 받지 않는다. 이미지에
        # 넣는 대신 named volume에 한 번 내려받아 구현과 Testing runner가 함께 사용한다.
        "-v",
        f"{RUNNER_TOFU_CACHE_VOLUME}:{RUNNER_TOFU_CACHE_PATH}",
        "-e",
        f"PYTHONPATH={CONTAINER_WORKSPACE}/app/implementation/runtime/runtime_hooks:{CONTAINER_WORKSPACE}",
        "-e",
        "EASYDEP_FIXED_LINUX_RUNNER=1",
        # 위 named volume을 Gradle의 공용 저장소로 사용한다. 오래된 이미지가 Windows
        # bind mount 아래를 cache로 선택하더라도 이 값으로 덮어쓴다.
        "-e",
        "GRADLE_USER_HOME=/tmp/easydep-gradle-cache",
        "-e",
        f"EASYDEP_TOFU_PLUGIN_CACHE={RUNNER_TOFU_CACHE_PATH}",
        "-e",
        f"TF_PLUGIN_CACHE_DIR={RUNNER_TOFU_CACHE_PATH}",
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
    # 이미지 태그가 이전 코드로 만들어졌더라도 ENTRYPOINT에 저장된 Python 모듈명은
    # 사용하지 않는다. bind mount한 현재 저장소의 고정 진입점을 항상 명시한다.
    command.extend(
        [
            "--entrypoint",
            "python",
            image,
            "-B",
            "-m",
            "app.implementation.runtime.member_linux_runner",
            operation,
            *arguments,
        ]
    )
    return command
