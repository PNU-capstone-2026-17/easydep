from pathlib import Path

import pytest

from evaluation.baselines.metagpt import MIN_QA_ROUNDS, _command, _materialize_repository


def test_command_enables_native_qa_and_uses_minimum_rounds():
    command = _command(Path("metagpt"), "build it", 3.0, MIN_QA_ROUNDS)

    assert command[-1] == "--run-tests"
    assert command[command.index("--n-round") + 1] == "8"


def test_command_rejects_too_few_rounds_for_qa():
    with pytest.raises(ValueError, match="requires at least 8 rounds"):
        _command(Path("metagpt"), "build it", 3.0, 7)


def _write(root: Path, name: str, content: str = "") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_materialize_repository_selects_complete_generated_project(tmp_path):
    workspace = tmp_path / "workspace"
    _write(workspace / "incomplete", "Dockerfile", "FROM scratch")
    project = workspace / "workspace" / "app" / "app"
    _write(project, "Dockerfile", "FROM eclipse-temurin:21")
    _write(project, "build.gradle", "plugins {}")
    _write(project, "src/main/java/App.java", "class App {}")
    _write(project, "terraform/main.tf", 'resource "aws_instance" "app" {}')
    _write(project, ".git/config", "must not be copied")

    selected = _materialize_repository(workspace, tmp_path / "repo")

    assert selected == project
    assert (tmp_path / "repo" / "Dockerfile").is_file()
    assert (tmp_path / "repo" / "terraform" / "main.tf").is_file()
    assert not (tmp_path / "repo" / ".git").exists()


def test_materialize_repository_reports_missing_project(tmp_path):
    assert _materialize_repository(tmp_path / "workspace", tmp_path / "repo") is None
    assert not (tmp_path / "repo").exists()
