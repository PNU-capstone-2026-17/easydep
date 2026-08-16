import json
from pathlib import Path

import evaluation.dependency_audit.inter_vm_postgres_intervention as intervention
from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    POSTGRES_PASSWORD,
    _baseline_script,
    _blocked_script,
    _gcp_probe_controller,
    _safe_text,
)

RESULT_ROOT = Path("evaluation/dependency_audit")


def test_sql_oracles_use_the_supplied_private_endpoint() -> None:
    private_ip = "10.99.3.17"

    baseline = _baseline_script(private_ip)
    blocked = _blocked_script(private_ip)

    assert f"@{private_ip}:5432/" in baseline
    assert "INSERT INTO easydep_evidence" in baseline
    assert 'test "$value" = "inter-vm"' in baseline
    assert f"@{private_ip}:5432/" in blocked
    assert "unexpected PostgreSQL success" in blocked
    assert "timeout 20" in blocked


def test_gcp_guest_controller_uses_markers_instead_of_ssh() -> None:
    script = _gcp_probe_controller("10.88.1.9")

    assert "EASYDEP_RESULT baseline passed" in script
    assert "EASYDEP_RESULT blocked passed" in script
    assert "EASYDEP_RESULT restored passed" in script
    assert "Metadata-Flavor: Google" in script
    assert "easydep-phase" in script
    assert "compute ssh" not in script


def test_log_sanitizer_removes_transient_credentials() -> None:
    raw = (
        f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
        "arn:aws:iam::123456789012:role/test\n"
        "projects/example-project/zones/example"
    )

    safe = _safe_text(raw)

    assert POSTGRES_PASSWORD not in safe
    assert "secret" not in safe
    assert "123456789012" not in safe
    assert "projects/example-project" not in safe


def test_azure_guest_script_uses_encoded_transport_and_exit_sentinel(monkeypatch) -> None:
    observed: dict[str, str] = {}

    def fake_json(command: list[str], *, timeout: int) -> dict[str, list[object]]:
        assert timeout == 1200
        argument = command[command.index("--scripts") + 1]
        observed["script"] = argument
        return {"value": [{"message": "EASYDEP_EXIT_CODE=0"}]}

    monkeypatch.setattr(intervention, "_json", fake_json)
    script = "echo evidence\n" * 700

    assert intervention._az_run("group", "vm", script) == "guest command passed"
    assert script not in observed["script"]
    assert "base64 -d" in observed["script"]
    assert "EASYDEP_EXIT_CODE=$easydep_exit_code" in observed["script"]
    assert "mktemp /tmp/easydep-run-command" in observed["script"]


def test_azure_guest_script_rejects_nonzero_guest_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        intervention,
        "_json",
        lambda _command, *, timeout: {
            "value": [{"message": "Enable succeeded\nEASYDEP_EXIT_CODE=7"}]
        },
    )

    try:
        intervention._az_run("group", "vm", "exit 7")
    except intervention.ExperimentFailure as error:
        assert "EASYDEP_EXIT_CODE=7" in str(error)
    else:
        raise AssertionError("non-zero Azure guest exit must fail the harness")


def test_three_provider_results_preserve_the_same_functional_intervention() -> None:
    for provider in ("aws", "azure", "gcp"):
        path = RESULT_ROOT / f"inter-vm-postgres-{provider}-result-20260814.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        steps = {step["name"]: step["status"] for step in result["steps"]}

        assert result["provider"] == provider
        assert result["outcome"] == "passed"
        assert result["cleanup"] == {"passed": True, "residual": []}
        assert steps["baseline.private-postgres-write-read"] == "passed"
        assert steps["intervention.connection-blocked"] == "passed"
        assert steps["restore.private-postgres-read"] == "passed"
        assert result["networkObservation"]["stateAddressUsedByProbe"]
