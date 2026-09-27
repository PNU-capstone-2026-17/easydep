import json
import os
import subprocess
from pathlib import Path

import pytest

from app.demo_validation import DEMO_SKIP_VALIDATION_ENV
from app.implementation.agents.verification.build import verification_timeout_seconds
from app.implementation.runtime.linux_runner_transport import (
    OWNER_CONTROL_ROOT_ENV,
    OWNER_NPM_CACHE,
    OWNER_TERMINAL_SHELL,
    OWNER_TERMINAL_SHELL_ENV,
    OWNER_TERMINAL_USER,
    OWNER_TERMINAL_USER_ENV,
    OWNER_WORKSPACE_VOLUME_PREFIX,
    RUNNER_GRADLE_CACHE_VOLUME,
    RUNNER_NPM_CACHE_VOLUME,
    RUNNER_TOFU_CACHE_PATH,
    RUNNER_TOFU_CACHE_VOLUME,
    configured_runner_image,
    reconcile_orphaned_runner_containers,
    runner_command,
    to_container_path,
    to_host_path,
)
from app.implementation.runtime.member_linux_runner import (
    _clear_llm_credentials_from_environment,
    _cli,
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
            DEMO_SKIP_VALIDATION_ENV: "true",
        },
        llm_environment={},
    )

    assert "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS" in command
    assert "IMPLEMENTATION_MAX_TASK_ATTEMPTS" in command
    assert "EASYDEP_MEMBER_CHECKPOINT_RUN" in command
    assert DEMO_SKIP_VALIDATION_ENV in command


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


def test_runner_command_labels_run_workflow_root(tmp_path: Path):
    _, container_job = _runner_job(tmp_path)
    run_root = "/easydep-workspace/.easydep/implementation-runs/job-1/generated/runs/run_abc"
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="cli",
        arguments=["run-workflow", run_root, container_job, "--retry-failed"],
        environment={},
        llm_environment={},
    )

    assert "easydep.job-id=job-1" in command
    assert "easydep.run-id=run_abc" in command


def test_runner_command_labels_run_owner_root(tmp_path: Path):
    _, container_job = _runner_job(tmp_path)
    container_job_root = container_job.rsplit("/", 1)[0]
    run_root = "/easydep-workspace/.easydep/implementation-runs/job-1/generated/runs/run_abc"
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="cli",
        arguments=["run-owner", run_root, container_job, "owner-1"],
        environment={},
        llm_environment={},
    )

    assert "easydep.job-id=job-1" in command
    assert "easydep.run-id=run_abc" in command
    volume_mounts = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "-v" and command[index + 1].startswith(OWNER_WORKSPACE_VOLUME_PREFIX)
    ]
    assert len(volume_mounts) == 1
    assert volume_mounts[0].endswith(f":{container_job_root}/w:nocopy")
    resumed = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="cli",
        arguments=["run-owner", run_root, container_job, "owner-2"],
        environment={},
        llm_environment={},
    )
    other_run = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="cli",
        arguments=[
            "run-owner",
            run_root.replace("run_abc", "run_def"),
            container_job,
            "owner-1",
        ],
        environment={},
        llm_environment={},
    )
    resumed_volume = next(
        resumed[index + 1]
        for index, value in enumerate(resumed[:-1])
        if value == "-v" and resumed[index + 1].startswith(OWNER_WORKSPACE_VOLUME_PREFIX)
    )
    workflow = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="cli",
        arguments=["run-workflow", run_root, container_job, "--retry-failed"],
        environment={},
        llm_environment={},
    )
    workflow_volume = next(
        workflow[index + 1]
        for index, value in enumerate(workflow[:-1])
        if value == "-v" and workflow[index + 1].startswith(OWNER_WORKSPACE_VOLUME_PREFIX)
    )
    other_run_volume = next(
        other_run[index + 1]
        for index, value in enumerate(other_run[:-1])
        if value == "-v" and other_run[index + 1].startswith(OWNER_WORKSPACE_VOLUME_PREFIX)
    )
    assert resumed_volume == volume_mounts[0]
    assert workflow_volume == volume_mounts[0]
    assert other_run_volume != volume_mounts[0]


def test_member_runner_translates_run_owner_job_path(monkeypatch: pytest.MonkeyPatch):
    observed: list[object] = []
    monkeypatch.setattr(
        "app.implementation.runtime.member_linux_runner._configure_runner_tools", lambda: None
    )
    monkeypatch.setattr(
        "app.implementation.runtime.member_linux_runner._runner_job",
        lambda path: observed.append(path) or Path("/runner-job.json"),
    )
    monkeypatch.setattr(
        "app.implementation.interfaces.cli.main",
        lambda arguments: observed.append(arguments) or 0,
    )

    assert _cli(["run-owner", "/run", "/job.json", "owner-1"]) == 0
    assert observed == [
        Path("/job.json"),
        ["run-owner", "/run", str(Path("/runner-job.json")), "owner-1"],
    ]


def test_reconciliation_preserves_owner_only_legacy_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_docker(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[:2] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(arguments, 0, "legacy-container\n", "")
        if arguments[0] == "inspect":
            return subprocess.CompletedProcess(arguments, 0, "\n", "")
        raise AssertionError(f"legacy owner-only container was cleaned: {arguments}")

    monkeypatch.setattr(
        "app.implementation.runtime.linux_runner_transport._docker_run",
        fake_docker,
    )
    reconcile_orphaned_runner_containers(set())

    assert len(calls) == 2


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
