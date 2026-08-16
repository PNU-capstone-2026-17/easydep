from __future__ import annotations

import json
from pathlib import Path

from evaluation.dependency_audit.aws_sample_app_postgres_e2 import (
    FUNCTIONAL_RECOVERY_BUDGET_SECONDS,
    AwsE2Recorder,
    _app_user_data,
    _continuity_summary,
)


def test_aws_e2_recorder_limits_the_availability_claim(tmp_path: Path) -> None:
    recorder = AwsE2Recorder("test-run", tmp_path / "result.json")

    assert recorder.document["provider"] == "aws"
    assert "Auto Scaling" in recorder.document["pathUnderTest"]
    assert "state tier remains singleton" in recorder.document["scope"]


def test_aws_e2_app_user_data_is_cloud_init_executable() -> None:
    script = _app_user_data("10.0.1.7")

    assert script.startswith("#!/bin/bash\n")
    assert "10.0.1.7:5432" in script
    assert "course" not in script.lower()


def test_continuity_gate_uses_health_derived_bounded_recovery() -> None:
    summary = _continuity_summary([
        {"atSeconds": 0.0, "status": 200, "valueKept": True},
        {"atSeconds": 1.0, "status": None, "valueKept": False},
        {"atSeconds": 5.0, "status": 502, "valueKept": False},
        {"atSeconds": 10.0, "status": 200, "valueKept": True},
    ])

    assert FUNCTIONAL_RECOVERY_BUDGET_SECONDS == 30
    assert summary == {
        "probeCount": 4,
        "successCount": 2,
        "failureCount": 2,
        "successRatio": 0.5,
        "maxConsecutiveFailureSeconds": 9.0,
    }


def test_aws_e2_confirmation_records_replacement_and_bounded_recovery() -> None:
    result = json.loads(
        Path(
            "evaluation/dependency_audit/aws-sample-app-postgres-e2-result-20260815-3.json"
        ).read_text(encoding="utf-8")
    )
    steps = {step["name"]: step for step in result["steps"]}
    replacement = steps["fault.health-based-managed-replacement"]["detail"]

    assert result["outcome"] == "passed"
    assert result["cleanup"] == {"passed": True, "residual": []}
    assert replacement["victimInstance"] != replacement["replacementInstance"]
    assert replacement["businessContinuity"]["maxConsecutiveFailureSeconds"] <= 30
    assert replacement["successesDuringReplacement"] > 0
