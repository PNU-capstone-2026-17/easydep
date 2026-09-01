from __future__ import annotations

import subprocess
from pathlib import Path

from app.implementation.generation.warmup import warmup_implementation_runtime
from app.implementation.runtime.linux_runner_transport import RUNNER_GRADLE_CACHE_VOLUME


def test_startup_warmup_uses_isolated_gradle_home(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def completed(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "app.implementation.generation.warmup.subprocess.run", completed
    )
    monkeypatch.setattr(
        "app.implementation.generation.warmup.configured_runner_image",
        lambda: "easydep-toolchain:test",
    )

    report = warmup_implementation_runtime(tmp_path, 60)

    assert report["status"] == "SUCCEEDED"
    # `gradle dependencies` only resolves POM metadata, which leaves the
    # artifact jars unfetched. Compiling a throwaway source validates the image
    # classpath without touching a Windows-host Gradle journal.
    gradle = next(command for command in commands if "compileJava" in command)
    assert "dependencies" not in gradle
    assert "--build-cache" in gradle
    warmup_source = (
        tmp_path
        / ".easydep"
        / "implementation-warmup"
        / "gradle"
        / "src"
        / "main"
        / "java"
        / "easydep"
        / "Warmup.java"
    )
    assert warmup_source.is_file()
    assert gradle[gradle.index("-e") + 1] == "GRADLE_USER_HOME=/tmp/easydep-gradle-home"
    assert f"{RUNNER_GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-home" in gradle
    assert gradle[gradle.index("--entrypoint") + 1] == "gradle"
    assert "easydep-toolchain:test" in gradle
    assert "-Dorg.gradle.vfs.watch=false" in gradle
    assert any("install" in command and "--package-lock-only" in command for command in commands)
    assert not any("puml2code" in argument for command in commands for argument in command)
    assert (tmp_path / ".easydep" / "implementation-warmup" / "report.json").is_file()
