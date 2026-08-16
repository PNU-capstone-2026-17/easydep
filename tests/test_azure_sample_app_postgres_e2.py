from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evaluation.dependency_audit import azure_sample_app_postgres_e2 as azure_e2
from evaluation.dependency_audit.azure_sample_app_postgres_e2 import (
    AUTO_REPAIR_GRACE_MINUTES,
    AzureE2Recorder,
    _app_cloud_init,
)


def test_azure_e2_recorder_limits_the_availability_claim(tmp_path: Path) -> None:
    recorder = AzureE2Recorder("test-run", tmp_path / "result.json")

    assert recorder.document["provider"] == "azure"
    assert "VMSS app VM group (2)" in recorder.document["pathUnderTest"]
    assert "state tier remains singleton" in recorder.document["scope"]


def test_azure_e2_uses_provider_minimum_repair_grace() -> None:
    assert AUTO_REPAIR_GRACE_MINUTES == 30


def test_azure_e2_cloud_init_is_domain_neutral_and_faultable() -> None:
    script = _app_cloud_init("10.83.1.4", "test-token")

    assert script.startswith("#!/bin/bash\n")
    assert "10.83.1.4:5432" in script
    assert "EASYDEP_TEST_FAULT_TOKEN='test-token'" in script
    assert "--restart" not in script
    assert "course" not in script.lower()


def test_azure_e2_guest_run_requires_managed_command_exit_evidence(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_run(command, **_kwargs) -> str:
        operation = command[3]
        if operation == "create":
            script_path = Path(command[command.index("--script") + 1][1:])
            captured["script"] = script_path.read_text(encoding="utf-8")
            return ""
        if operation == "show":
            marker = next(
                line for line in captured["script"].splitlines()
                if line.startswith("echo EASYDEP_AZ_GUEST_OK_")
            ).removeprefix("echo ")
            return json.dumps({
                "instanceView": {
                    "executionState": "Succeeded",
                    "exitCode": 0,
                    "output": marker,
                    "error": "",
                }
            })
        return ""

    monkeypatch.setattr(azure_e2, "_run", fake_run)

    marker = azure_e2._azure_guest_run("group", "vm", "true")

    assert marker.startswith("EASYDEP_AZ_GUEST_OK_")


def test_azure_e2_confirmation_records_replacement_and_cleanup() -> None:
    result_path = Path(
        "evaluation/dependency_audit/azure-sample-app-postgres-e2-result-20260815-4.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    steps = {step["name"]: step for step in result["steps"]}
    recovery = steps["fault.health-based-managed-recovery"]["detail"]
    adjudication = json.loads(
        Path(
            "evaluation/dependency_audit/"
            "azure-sample-app-postgres-e2-adjudication-20260815.json"
        ).read_text(encoding="utf-8")
    )
    selected = adjudication["attempts"][-1]

    assert result["outcome"] == "passed"
    assert result["cleanup"] == {"passed": True, "residual": []}
    assert recovery["victimVmId"] != recovery["managedRecovery"]["vmId"]
    assert recovery["automaticRepairGraceMinutes"] == 30
    assert recovery["businessContinuity"]["maxConsecutiveFailureSeconds"] == 0
    assert selected["sha256"] == hashlib.sha256(result_path.read_bytes()).hexdigest()
