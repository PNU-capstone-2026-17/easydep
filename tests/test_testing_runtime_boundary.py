from __future__ import annotations

from pathlib import Path

from app.testing.runtime.container_runner import GRADLE_CACHE_VOLUME, runner_command


def test_testing_runner_uses_its_own_container_entrypoint(tmp_path: Path):
    command = runner_command(
        image="easydep/member-runner:test",
        repository_root=tmp_path,
        operation="test",
        arguments=["/easydep-workspace/.easydep/request.json"],
        environment={"EASYDEP_EXPERIMENT_SESSION": "experiment-1"},
    )

    assert "easydep.owner=testing-runner" in command
    assert "easydep.experiment-session=experiment-1" in command
    assert command[command.index("--entrypoint") + 1] == "python"
    assert "app.testing.runtime.member_linux_runner" in command
    assert all("runtime_hooks" not in value for value in command)
    assert f"{GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache" in command
    assert "GRADLE_USER_HOME=/tmp/easydep-gradle-cache" in command
