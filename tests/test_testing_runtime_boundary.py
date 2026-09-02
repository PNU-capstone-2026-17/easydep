from __future__ import annotations

from pathlib import Path

from app.testing.runtime.container_runner import (
    CONTAINER_RUN_ROOT,
    DEFAULT_RUNNER_IMAGE,
    GRADLE_CACHE_VOLUME,
    TOFU_CACHE_PATH,
    TOFU_CACHE_VOLUME,
    configured_runner_image,
    runner_command,
)
from app.testing.utils.test_runner import DEFAULT_TOOLCHAIN_IMAGE, configured_toolchain_image


def test_testing_image_selection_uses_env_then_settings_without_remote_default(monkeypatch):
    monkeypatch.setattr(
        "app.testing.runtime.container_runner.settings.easydep_toolchain_image",
        "settings:image",
    )

    assert configured_runner_image({}) == "settings:image"
    assert configured_runner_image({"EASYDEP_TOOLCHAIN_IMAGE": "env:image"}) == "env:image"
    assert configured_toolchain_image({"EASYDEP_TESTING_TOOLCHAIN_IMAGE": "testing:image"}) == "testing:image"
    assert configured_toolchain_image({}) == "easydep-testing-toolchain:local"
    assert DEFAULT_RUNNER_IMAGE == "easydep-toolchain:local"
    assert DEFAULT_TOOLCHAIN_IMAGE == "easydep-testing-toolchain:local"


def test_testing_runner_uses_its_own_container_entrypoint(tmp_path: Path):
    run_root = tmp_path.parent / "restored-testing-run"
    command = runner_command(
        image="easydep/member-runner:test",
        repository_root=tmp_path,
        operation="test",
        arguments=["/easydep-workspace/.easydep/request.json"],
        environment={"EASYDEP_EXPERIMENT_SESSION": "experiment-1"},
        run_root=run_root,
    )

    assert "easydep.owner=testing-runner" in command
    assert "easydep.experiment-session=experiment-1" in command
    assert command[command.index("--entrypoint") + 1] == "python"
    assert "app.testing.runtime.member_linux_runner" in command
    assert all("runtime_hooks" not in value for value in command)
    assert f"{GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache" in command
    assert "GRADLE_USER_HOME=/tmp/easydep-gradle-cache" in command
    assert TOFU_CACHE_VOLUME == "easydep-tofu-provider-cache"
    assert f"{TOFU_CACHE_VOLUME}:{TOFU_CACHE_PATH}" in command
    assert f"EASYDEP_TOFU_PLUGIN_CACHE={TOFU_CACHE_PATH}" in command
    assert f"TF_PLUGIN_CACHE_DIR={TOFU_CACHE_PATH}" in command
    assert f"{tmp_path.resolve()}:/easydep-workspace:ro" in command
    assert f"{run_root.resolve()}:{CONTAINER_RUN_ROOT.as_posix()}:rw" in command
