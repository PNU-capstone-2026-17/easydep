from __future__ import annotations

from pathlib import Path

from app.testing.runtime.container_runner import runner_command


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
    assert "app.orchestration.member_linux_runner" not in command
    assert all("runtime_hooks" not in value for value in command)


def test_testing_package_has_no_core_imports():
    testing_root = Path(__file__).resolve().parents[1] / "app" / "testing"
    imports = [
        path
        for path in testing_root.rglob("*.py")
        if "app.core" in path.read_text(encoding="utf-8")
    ]

    assert imports == []
