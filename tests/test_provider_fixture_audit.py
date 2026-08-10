from pathlib import Path

import pytest

from evaluation.research_protocol.commands.provider_fixture_audit import validate_fixture


def test_missing_provider_fixture_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_fixture("aws", tmp_path)


def test_fixture_audit_isolated_copy_and_reports_subtask_times(tmp_path, monkeypatch):
    fixture = tmp_path / "fixtures/aws"
    fixture.mkdir(parents=True)
    (fixture / "main.tf").write_text("terraform {}", encoding="utf-8")
    calls = []

    def command(arguments, cwd, timeout=600, **_kwargs):
        calls.append((arguments, Path(cwd), timeout))
        payload = {
            "status": "passed",
            "stdout": '{"valid":true}' if "validate" in arguments else "",
            "elapsedSeconds": 0.1,
        }
        return payload

    monkeypatch.setattr(
        "evaluation.research_protocol.commands.provider_fixture_audit.shutil.which",
        lambda _name: "tofu",
    )
    monkeypatch.setattr(
        "evaluation.research_protocol.commands.provider_fixture_audit.run_provider_command",
        command,
    )

    result = validate_fixture("aws", tmp_path / "fixtures")

    assert result["status"] == "passed"
    assert result["elapsedSeconds"] >= 0
    assert [item[0][1] for item in calls] == ["fmt", "init", "validate"]
    assert all(item[1] != fixture for item in calls)
