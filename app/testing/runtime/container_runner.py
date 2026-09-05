"""Testing이 생성 앱과 배포 파일을 검사할 때 쓰는 공용 툴체인."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.implementation.runtime.process import run_process_tree

RUNNER_IMAGE_ENV = "EASYDEP_TOOLCHAIN_IMAGE"
DEFAULT_RUNNER_IMAGE = "easydep-toolchain:local"
GRADLE_CACHE_VOLUME = "easydep-member-gradle-cache"
TOFU_CACHE_VOLUME = "easydep-tofu-provider-cache"
TOFU_CACHE_PATH = "/app/.cache/opentofu"
CONTAINER_CHECK_ROOT = "/easydep-check"


@dataclass(frozen=True)
class ToolchainExecution:
    """공용 툴체인에서 한 명령을 실행한 결과.

    ``command``는 사용자 산출물에 적용한 짧은 명령이고, ``toolchain``은
    그 명령을 실제로 실행한 환경이다. Docker가 컨테이너를 시작하지
    못한 경우만 ``environment_error``를 참으로 두어, 파일 검사 실패와
    실행 환경 실패를 혼동하지 않게 한다.
    """

    completed: subprocess.CompletedProcess[str]
    command: tuple[str, ...]
    toolchain: str
    environment_error: bool


def configured_runner_image(environment: dict[str, str] | None = None) -> str:
    """환경변수와 공용 설정에서 로컬 toolchain image 이름을 고른다."""

    source = os.environ if environment is None else environment
    value = source.get(RUNNER_IMAGE_ENV, "").strip()
    return value or (settings.easydep_toolchain_image or "").strip() or DEFAULT_RUNNER_IMAGE


def run_toolchain_command(
    command: list[str],
    *,
    cwd: str | Path,
    timeout: int,
    environment: dict[str, str] | None = None,
) -> ToolchainExecution:
    """정적 검사 명령을 현재 소스와 같은 공용 Linux 툴체인에서 실행한다.

    백엔드가 이미 툴체인 컨테이너 안에 있으면 명령을 바로 실행한다.
    Windows 개발 환경에서는 검사 대상 폴더 하나만 mount하여 host의
    우연한 PATH, Git Bash, WSL 설정에 결과가 달라지지 않게 한다.

    이 함수는 외부 network를 끊은 채 실행하며, 호출자도 ``apply`` 명령을
    넘기지 않는다. 따라서 검사 중 실제 cloud resource가 바뀐 일은 없다.
    """

    if not command:
        raise ValueError("toolchain command must not be empty")

    working_directory = Path(cwd).resolve()
    process_environment = {**os.environ, **(environment or {})}
    if os.environ.get("EASYDEP_FIXED_LINUX_RUNNER") == "1":
        completed = run_process_tree(
            command,
            cwd=working_directory,
            env=process_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
        return ToolchainExecution(
            completed=completed,
            command=tuple(command),
            toolchain="fixed-linux-runner",
            environment_error=False,
        )

    image = configured_runner_image()
    docker_command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--network",
        "none",
        "--label",
        "easydep.owner=testing-tool",
        "-v",
        f"{working_directory}:{CONTAINER_CHECK_ROOT}",
        "-v",
        f"{TOFU_CACHE_VOLUME}:{TOFU_CACHE_PATH}",
        "-w",
        CONTAINER_CHECK_ROOT,
        "-e",
        "EASYDEP_FIXED_LINUX_RUNNER=1",
        "-e",
        f"TF_PLUGIN_CACHE_DIR={TOFU_CACHE_PATH}",
    ]
    for name, value in (environment or {}).items():
        docker_command.extend(["-e", f"{name}={value}"])
    docker_command.extend(["--entrypoint", command[0], image, *command[1:]])
    completed = run_process_tree(
        docker_command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    return ToolchainExecution(
        completed=completed,
        command=tuple(command),
        toolchain=image,
        # Docker run의 125~127은 image/entrypoint/container 시작 실패이다.
        # 툴이 실행된 뒤 산출물을 거부한 반환 코드와 구분한다.
        environment_error=completed.returncode in {125, 126, 127},
    )
