from __future__ import annotations

from pathlib import Path

from evaluation.dependency_audit.gcp_backend_group_intervention import (
    ESTIMATED_CAMPAIGN_COST_USD,
    Names,
    cleanup_commands,
    creation_commands,
    openssl_path,
    outcome,
    sanitize_command,
    startup_script,
)


def test_plan_creates_functional_https_path_and_cleanup_reverses_ownership():
    names = Names("edbgint-r1")
    create = creation_commands(names, Path("startup.sh"), Path("cert.pem"), Path("key.pem"))
    cleanup = cleanup_commands(names)
    rendered = "\n".join(" ".join(item) for item in create)

    assert "target-https-proxies create" in rendered
    assert "forwarding-rules create" in rendered
    assert "backend-services add-backend" in rendered
    assert cleanup[0][2:4] == ("delete", names.forwarding_rule)
    assert cleanup[-1][2:4] == ("delete", names.network)
    assert ESTIMATED_CAMPAIGN_COST_USD < 10


def test_startup_oracle_has_readiness_and_business_contracts():
    script = startup_script()

    assert "'/readyz'" in script
    assert "'/business'" in script
    assert "easydep-intervention" in script


def test_local_certificate_generator_is_discoverable_before_cloud_creation():
    assert openssl_path()


def test_outcome_keeps_function_failure_separate_from_provisioning():
    phases = [
        {"id": "interventionProvision", "status": "passed"},
        {"id": "interventionStartup", "status": "passed"},
        {"id": "interventionFunction", "status": "failed"},
    ]

    assert outcome(phases) == "functionBlocked"


def test_evidence_command_hides_temporary_local_file_paths():
    command = [
        "gcloud",
        "compute",
        "ssl-certificates",
        "create",
        "cert",
        "--certificate=C:/Temp/cert.pem",
        "--private-key=C:/Temp/key.pem",
        "--metadata-from-file=startup-script=C:/Temp/startup.sh",
    ]

    sanitized = sanitize_command(command)

    assert "C:/Temp" not in " ".join(sanitized)
    assert "--private-key=<temporary-private-key>" in sanitized
