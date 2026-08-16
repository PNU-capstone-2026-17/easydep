from __future__ import annotations

import subprocess
from pathlib import Path

from app.implementation.generation.warmup import warmup_implementation_runtime


def test_startup_warmup_populates_the_shared_gradle_cache(
    monkeypatch, tmp_path: Path
) -> None:
    tool_root = tmp_path / "app" / "implementation" / "tools" / "puml2code-bce"
    tool_root.mkdir(parents=True)
    (tool_root / "Dockerfile").write_text("FROM node:20\n", encoding="utf-8")
    commands: list[list[str]] = []

    def completed(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "app.implementation.generation.warmup.subprocess.run", completed
    )

    report = warmup_implementation_runtime(tmp_path, 60)

    assert report["status"] == "SUCCEEDED"
    assert (tmp_path / ".easydep" / "gradle-cache").is_dir()
    # `gradle dependencies` only resolves POM metadata, which leaves the
    # artifact jars unfetched.  Compiling a throwaway source is what actually
    # materialises the compile classpath into the shared cache.
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
    assert any(
        str(tmp_path / ".easydep" / "gradle-cache") in argument
        for argument in gradle
    )
    assert (tmp_path / ".easydep" / "implementation-warmup" / "report.json").is_file()
