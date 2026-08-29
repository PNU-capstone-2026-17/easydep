from __future__ import annotations

import json
from pathlib import Path

from evaluation.dependency_audit.gcp_sample_app_postgres_e2 import (
    GcpE2Recorder,
    _app_controller,
    _state_ready_from_serial,
)


def test_gcp_e2_recorder_limits_the_availability_claim(tmp_path: Path) -> None:
    recorder = GcpE2Recorder("test-run", tmp_path / "result.json")

    assert recorder.document["provider"] == "gcp"
    assert "managed app VM group (2)" in recorder.document["pathUnderTest"]
    assert "state tier remains singleton" in recorder.document["scope"]


def test_gcp_e2_app_controller_is_domain_neutral_and_faultable() -> None:
    script = _app_controller("10.82.1.2", "test-token")

    assert script.startswith("#!/bin/bash\n")
    assert "10.82.1.2:5432" in script
    assert "EASYDEP_TEST_FAULT_TOKEN='test-token'" in script
    assert "--restart" not in script
    assert "course" not in script.lower()


def test_gcp_e2_state_readiness_accepts_explicit_or_equivalent_guest_evidence() -> None:
    assert _state_ready_from_serial("EASYDEP_E1 state-ready passed")
    assert _state_ready_from_serial(
        "postgres: accepting connections\nFinished running startup scripts"
    )
    assert not _state_ready_from_serial(
        "postgres: accepting connections\nFailed to run startup scripts"
    )


def test_gcp_e2_confirmation_records_managed_restart_and_cleanup() -> None:
    result_path = Path(
        "evaluation/dependency_audit/gcp-sample-app-postgres-e2-result-20260815-6.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    steps = {step["name"]: step for step in result["steps"]}
    recovery = steps["fault.health-based-managed-recovery"]["detail"]
    assert result["outcome"] == "passed"
    assert result["cleanup"] == {"passed": True, "residual": []}
    assert recovery["managedRecovery"]["action"] == "restart-in-place"
    assert recovery["businessContinuity"]["maxConsecutiveFailureSeconds"] == 0
    assert recovery["successfulRequestsDuringManagedRecovery"] > 0
