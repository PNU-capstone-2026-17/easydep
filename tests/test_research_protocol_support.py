import subprocess
from pathlib import Path

import pytest

from evaluation.research_protocol.core.support import (
    copy_terraform_inputs,
    read_json,
    redact_tail,
    run_captured,
    write_json_atomic,
)


def test_atomic_json_redaction_and_terraform_copy(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_json_atomic(result, {"status": "running"})
    write_json_atomic(result, {"status": "completed"})
    assert result.read_text(encoding="utf-8") == '{\n  "status": "completed"\n}\n'
    assert read_json(result) == {"status": "completed"}
    assert not result.with_suffix(".json.tmp").exists()

    sensitive_value = "value-to-redact"
    redacted = redact_tail("x" * 13_000 + sensitive_value, [sensitive_value])
    assert sensitive_value not in redacted
    assert redacted.endswith("<redacted-input>")
    assert len(redacted) <= 12_000

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "main.tf").write_text("terraform {}", encoding="utf-8")
    (source / "startup.sh.tftpl").write_text("#!/bin/sh", encoding="utf-8")
    (source / "ignored.txt").write_text("ignore", encoding="utf-8")
    assert copy_terraform_inputs(source, destination) == [
        "main.tf",
        "startup.sh.tftpl",
    ]
    assert not (destination / "ignored.txt").exists()


def test_captured_timeout_requires_an_explicit_censor_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["tofu", "plan"], 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        run_captured(
            ["tofu", "plan"],
            cwd=tmp_path,
            environment={},
            redactions=[],
            timeout_seconds=1,
        )
    censored = run_captured(
        ["tofu", "apply"],
        cwd=tmp_path,
        environment={},
        redactions=[],
        timeout_seconds=1,
        timeout_reason="measurementWallClock",
        include_timestamps=True,
    )
    assert censored["status"] == "censored"
    assert censored["reason"] == "measurementWallClock"
    assert censored["startedAt"] <= censored["finishedAt"]
