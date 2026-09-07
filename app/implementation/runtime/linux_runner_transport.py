"""호스트 오케스트레이터와 고정 Linux 멤버 runner 사이의 전송 경계."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from app.config import settings

CONTAINER_WORKSPACE = PurePosixPath("/easydep-workspace")
RUNNER_IMAGE_ENV = "EASYDEP_TOOLCHAIN_IMAGE"
RUNNER_GRADLE_CACHE_VOLUME = "easydep-member-gradle-cache"
RUNNER_NPM_CACHE_VOLUME = "easydep-member-npm-cache"
RUNNER_TOFU_CACHE_VOLUME = "easydep-tofu-provider-cache"
RUNNER_TOFU_CACHE_PATH = "/app/.cache/opentofu"
OWNER_TERMINAL_USER = "appuser"
OWNER_TERMINAL_SHELL = "/usr/local/bin/easydep-owner-shell"
OWNER_TERMINAL_HOME = "/var/lib/easydep-owner/home"
OWNER_NPM_CACHE = "/var/cache/easydep/npm"
OWNER_CONTROL_ROOT_ENV = "EASYDEP_OWNER_CONTROL_ROOT"
OWNER_TERMINAL_USER_ENV = "EASYDEP_OWNER_TERMINAL_USER"
OWNER_TERMINAL_SHELL_ENV = "EASYDEP_OWNER_TERMINAL_SHELL"
RUNTIME_ENVIRONMENT = (
    "OPENHANDS_MAX_OUTPUT_TOKENS",
    "OPENHANDS_REASONING_EFFORT",
    "OPENHANDS_PROVIDER_RETRY_BASE_SECONDS",
    "OPENHANDS_PROVIDER_RETRY_MAX_SECONDS",
    "IMPLEMENTATION_COMMAND_TIMEOUT_SECONDS",
    "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS",
    "IMPLEMENTATION_MAX_TASK_ATTEMPTS",
    "EASYDEP_MEMBER_CHECKPOINT_RUN",
)
# ``llm_subprocess_environment`` publishes the selected provider credential
# under this canonical name.  Keep the list next to the Docker transport so
# both the runner entrypoint and autonomous tool adapter use the same boundary.
LLM_CREDENTIAL_ENVIRONMENT = ("API_KEY",)


def _job_root_for_arguments(
    arguments: list[str], repository_root: Path
) -> tuple[Path, PurePosixPath] | None:
    """Return the one implementation-job directory needed by this runner.

    The member runner used to bind the whole EasyDep checkout read-write.  An
    autonomous terminal would then be able to read ``.env`` and edit EasyDep
    itself.  Job files are self-contained: all design inputs, generated runs,
    reports and progress files live below the directory containing ``job.json``.
    Mounting only that directory preserves the existing container paths without
    exposing the rest of the checkout.
    """

    root = repository_root.resolve()
    implementation_runs = (root / ".easydep" / "implementation-runs").resolve()
    for value in arguments:
        candidate = Path(to_host_path(value, root)).resolve()
        if candidate.name != "job.json" or not candidate.is_file():
            continue
        try:
            candidate.relative_to(implementation_runs)
        except ValueError as error:
            raise ValueError(
                f"Implementation runner job is outside the work root: {candidate}"
            ) from error
        job_root = candidate.parent
        try:
            job = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Implementation runner job is unreadable: {candidate}") from error
        raw_inputs = job.get("inputs")
        if raw_inputs is not None and not isinstance(raw_inputs, dict):
            raise ValueError(f"Implementation runner job inputs are invalid: {candidate}")
        referenced = [
            *(
                str(path)
                for path in (raw_inputs or {}).values()
                if isinstance(path, str)
            ),
            *(
                str(job[name])
                for name in ("outputRoot", "progressPath")
                if isinstance(job.get(name), str)
            ),
        ]
        for relative in referenced:
            referenced_path = (root / relative).resolve()
            if referenced_path != job_root and job_root not in referenced_path.parents:
                raise ValueError(
                    "Implementation runner input is outside its job directory: "
                    f"{relative}"
                )
        return job_root, to_container_path(job_root, root)
    return None


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
    llm_environment: dict[str, str],
) -> list[str]:
    root = repository_root.resolve()
    runner_arguments = [str(argument) for argument in arguments]
    job_mount = _job_root_for_arguments(runner_arguments, root)
    if operation in {"worker", "cli"} and job_mount is None:
        raise ValueError("Implementation runner requires a job.json below its work root")
    application_source = (root / "app").resolve()
    if not application_source.is_dir():
        raise ValueError(f"EasyDep application source is missing: {application_source}")
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--user",
        "root",
        "--security-opt",
        "no-new-privileges:true",
        "--label",
        "easydep.owner=member-runner",
        "-v",
        f"{application_source}:{CONTAINER_WORKSPACE.as_posix()}/app:ro",
        # 컨테이너가 끝나도 Gradle 배포본과 Maven dependency를 남긴다. 구현 Job마다
        # 130MB가 넘는 배포본을 다시 받거나 Windows bind mount에서 수천 파일을 읽지 않는다.
        "-v",
        f"{RUNNER_GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache",
        "-v",
        f"{RUNNER_NPM_CACHE_VOLUME}:{OWNER_NPM_CACHE}",
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
        "-e",
        f"{OWNER_TERMINAL_USER_ENV}={OWNER_TERMINAL_USER}",
        "-e",
        f"{OWNER_TERMINAL_SHELL_ENV}={OWNER_TERMINAL_SHELL}",
        "-e",
        f"npm_config_cache={OWNER_NPM_CACHE}",
    ]
    if job_mount is not None:
        job_root, container_job_root = job_mount
        cache_mount_index = command.index("-v", command.index("-v") + 1)
        command[cache_mount_index:cache_mount_index] = [
            "-v",
            f"{job_root}:{container_job_root.as_posix()}",
        ]
        command.extend(
            ["-e", f"{OWNER_CONTROL_ROOT_ENV}={container_job_root.as_posix()}"]
        )
    experiment_session = environment.get("EASYDEP_EXPERIMENT_SESSION", "").strip()
    if experiment_session:
        volume_index = command.index("-v")
        command[volume_index:volume_index] = [
            "--label",
            f"easydep.experiment-session={experiment_session}",
        ]
    # 일반 실행 설정은 이 모듈이 관리하지만 LLM 설정 이름은 app.llm_connection이 만든
    # 묶음을 그대로 사용한다. provider별 환경변수를 여기에 다시 나열하면 둘이 쉽게
    # 어긋나므로 별도 목록을 두지 않는다.
    transmitted_names = [*RUNTIME_ENVIRONMENT, *llm_environment]
    for name in dict.fromkeys(transmitted_names):
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
            *runner_arguments,
        ]
    )
    return command
