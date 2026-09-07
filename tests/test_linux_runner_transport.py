import json
import os
from pathlib import Path

import pytest

from app.implementation.agents.verification.build import verification_timeout_seconds
from app.implementation.runtime.linux_runner_transport import (
    OWNER_CONTROL_ROOT_ENV,
    OWNER_NPM_CACHE,
    OWNER_TERMINAL_SHELL,
    OWNER_TERMINAL_SHELL_ENV,
    OWNER_TERMINAL_USER,
    OWNER_TERMINAL_USER_ENV,
    RUNNER_GRADLE_CACHE_VOLUME,
    RUNNER_NPM_CACHE_VOLUME,
    RUNNER_TOFU_CACHE_PATH,
    RUNNER_TOFU_CACHE_VOLUME,
    configured_runner_image,
    runner_command,
    to_container_path,
    to_host_path,
)
from app.implementation.runtime.member_linux_runner import (
    _clear_llm_credentials_from_environment,
)


def _runner_job(tmp_path: Path) -> tuple[Path, str]:
    application_source = tmp_path / "app"
    application_source.mkdir()
    (application_source / "__init__.py").write_text("", encoding="utf-8")
    job_root = tmp_path / ".easydep" / "implementation-runs" / "job-1"
    job_root.mkdir(parents=True)
    job = job_root / "job.json"
    job.write_text(
        json.dumps(
            {
                "inputs": {
                    "openapi": (
                        ".easydep/implementation-runs/job-1/design-context/openapi.json"
                    )
                },
                "outputRoot": ".easydep/implementation-runs/job-1/generated/runs",
                "progressPath": ".easydep/implementation-runs/job-1/progress.json",
            }
        ),
        encoding="utf-8",
    )
    return job, to_container_path(job, tmp_path).as_posix()


def test_configured_runner_image_uses_explicit_environment_only():
    assert configured_runner_image({"EASYDEP_TOOLCHAIN_IMAGE": "runner:test"}) == "runner:test"
    assert configured_runner_image({}) is None


def test_runner_transport_round_trips_workspace_path(tmp_path: Path):
    path = tmp_path / ".easydep" / "run" / "job.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")

    container = to_container_path(path, tmp_path)

    assert container.as_posix() == "/easydep-workspace/.easydep/run/job.json"
    assert Path(to_host_path(str(container), tmp_path)) == path


def test_runner_command_transmits_only_named_environment(tmp_path: Path):
    job, container_job = _runner_job(tmp_path)
    container_job_root = to_container_path(job.parent, tmp_path).as_posix()
    llm_environment = {
        "LLM_PROVIDER": "openrouter",
        "API_KEY": "secret",
        "BASE_URL": "https://llm.test.invalid/v1",
        "MODEL": "test/provider-model",
    }
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="worker",
        arguments=[container_job],
        environment={**llm_environment, "UNRELATED_SECRET": "do-not-pass"},
        llm_environment=llm_environment,
    )

    assert all(name in command for name in llm_environment)
    assert "LLM_PROVIDER" in command
    assert "UNRELATED_SECRET" not in command
    assert "secret" not in command
    assert "https://llm.test.invalid/v1" not in command
    assert "test/provider-model" not in command
    assert command[-2:] == ["worker", container_job]
    assert f"{tmp_path / 'app'}:/easydep-workspace/app:ro" in command
    assert f"{job.parent}:{container_job_root}" in command
    assert f"{tmp_path}:/easydep-workspace" not in command
    assert all(".env" not in value and ".git" not in value for value in command)
    assert "GRADLE_USER_HOME=/tmp/easydep-gradle-cache" in command
    assert f"{RUNNER_GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache" in command
    assert RUNNER_TOFU_CACHE_VOLUME == "easydep-tofu-provider-cache"
    assert f"{RUNNER_TOFU_CACHE_VOLUME}:{RUNNER_TOFU_CACHE_PATH}" in command
    assert f"EASYDEP_TOFU_PLUGIN_CACHE={RUNNER_TOFU_CACHE_PATH}" in command
    assert f"TF_PLUGIN_CACHE_DIR={RUNNER_TOFU_CACHE_PATH}" in command
    assert command[command.index("--user") + 1] == "root"
    assert "no-new-privileges:true" in command
    assert f"{RUNNER_NPM_CACHE_VOLUME}:{OWNER_NPM_CACHE}" in command
    assert f"{OWNER_TERMINAL_USER_ENV}={OWNER_TERMINAL_USER}" in command
    assert f"{OWNER_TERMINAL_SHELL_ENV}={OWNER_TERMINAL_SHELL}" in command
    assert f"{OWNER_CONTROL_ROOT_ENV}={container_job_root}" in command
    assert command[command.index("--entrypoint") + 1] == "python"
    assert "app.implementation.runtime.member_linux_runner" in command


def test_runner_command_transmits_verification_timeout(tmp_path: Path):
    _, container_job = _runner_job(tmp_path)
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="worker",
        arguments=[container_job],
        environment={
            "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS": "1200",
            "IMPLEMENTATION_MAX_TASK_ATTEMPTS": "5",
            "EASYDEP_MEMBER_CHECKPOINT_RUN": "run_abc123",
        },
        llm_environment={},
    )

    assert "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS" in command
    assert "IMPLEMENTATION_MAX_TASK_ATTEMPTS" in command
    assert "EASYDEP_MEMBER_CHECKPOINT_RUN" in command


def test_verification_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS", "1200")

    assert verification_timeout_seconds() == 1200


def test_runner_command_labels_the_experiment_session(tmp_path: Path):
    _, container_job = _runner_job(tmp_path)
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="worker",
        arguments=[container_job],
        environment={"EASYDEP_EXPERIMENT_SESSION": "session-123"},
        llm_environment={},
    )

    assert "easydep.owner=member-runner" in command
    assert "easydep.experiment-session=session-123" in command


def test_runner_command_rejects_job_references_outside_job_directory(tmp_path: Path):
    job, container_job = _runner_job(tmp_path)
    job.write_text(
        json.dumps(
            {
                "inputs": {"secret": ".env"},
                "outputRoot": ".easydep/implementation-runs/job-1/generated/runs",
                "progressPath": ".easydep/implementation-runs/job-1/progress.json",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside its job directory"):
        runner_command(
            image="runner:test",
            repository_root=tmp_path,
            operation="worker",
            arguments=[container_job],
            environment={},
            llm_environment={},
        )


def test_member_runner_scrubs_llm_credentials(monkeypatch):
    monkeypatch.setenv("API_KEY", "member-runner-secret")

    _clear_llm_credentials_from_environment()

    assert "API_KEY" not in os.environ
