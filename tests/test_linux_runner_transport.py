from pathlib import Path

from app.implementation.agents.verification.build import verification_timeout_seconds
from app.implementation.runtime.linux_runner_transport import (
    RUNNER_GRADLE_CACHE_VOLUME,
    configured_runner_image,
    runner_command,
    to_container_path,
    to_host_path,
)


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
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="worker",
        arguments=["/easydep-workspace/job.json"],
        environment={"LLM_API_KEY": "secret", "UNRELATED_SECRET": "do-not-pass"},
    )

    assert "LLM_API_KEY" in command
    assert "UNRELATED_SECRET" not in command
    assert "secret" not in command
    assert command[-2:] == ["worker", "/easydep-workspace/job.json"]
    assert "GRADLE_USER_HOME=/tmp/easydep-gradle-cache" in command
    assert f"{RUNNER_GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache" in command
    assert command[command.index("--entrypoint") + 1] == "python"
    assert "app.implementation.runtime.member_linux_runner" in command


def test_runner_command_transmits_verification_timeout(tmp_path: Path):
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="worker",
        arguments=["/easydep-workspace/job.json"],
        environment={
            "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS": "1200",
            "IMPLEMENTATION_MAX_TASK_ATTEMPTS": "5",
            "EASYDEP_MEMBER_CHECKPOINT_RUN": "run_abc123",
        },
    )

    assert "IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS" in command
    assert "IMPLEMENTATION_MAX_TASK_ATTEMPTS" in command
    assert "EASYDEP_MEMBER_CHECKPOINT_RUN" in command


def test_verification_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS", "1200")

    assert verification_timeout_seconds() == 1200


def test_runner_command_labels_the_experiment_session(tmp_path: Path):
    command = runner_command(
        image="runner:test",
        repository_root=tmp_path,
        operation="worker",
        arguments=["/easydep-workspace/job.json"],
        environment={"EASYDEP_EXPERIMENT_SESSION": "session-123"},
    )

    assert "easydep.owner=member-runner" in command
    assert "easydep.experiment-session=session-123" in command
