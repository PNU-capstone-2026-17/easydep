from __future__ import annotations

from app.cloudkb.depkb.official_dependency_model import (
    dependencies_for,
    load_official_dependencies,
)
from evaluation.research_protocol.commands.build_runtime_dependencies import build


def test_runtime_dependency_artifact_is_reproducible_from_frozen_models():
    assert load_official_dependencies() == build()


def test_gcp_replicated_necessity_is_promoted_by_runtime_evidence():
    rows = dependencies_for("gcp", ["vm", "loadBalancer"])
    backend = next(item for item in rows if item["id"].endswith("backend-service-backend-group"))

    assert backend["existenceDecision"] == "confirmed"
    assert backend["necessityDecision"] == "confirmed"


def test_plain_vm_does_not_receive_unrequested_load_balancer_dependencies():
    rows = dependencies_for("aws", ["vm"])

    assert {item["id"] for item in rows} == {"aws.vm-subnet", "aws.vm-security-group"}
