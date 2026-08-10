from __future__ import annotations

import json

import pytest

from app.core.cloudkb.depkb.evidence_model import validate_frozen_model
from evaluation.research_protocol.commands.build_evidence_models import build


def test_all_provider_boundary_models_are_grounded_and_frozen():
    for provider in ("aws", "azure", "gcp"):
        model = build(provider)
        validate_frozen_model(model)
        boundaries = [claim for claim in model["claims"]
                      if claim["claimType"] == "resourceBoundary"]
        assert boundaries and all(claim["decision"] == "confirmed" for claim in boundaries)
        assert any(claim["claimType"] == "dependencyExistence" for claim in model["claims"])


def test_normative_azure_manual_documents_vm_nic_necessity():
    model = build("azure")
    nic = {claim["claimId"]: claim for claim in model["claims"]}
    assert nic["azure.vm-network-interface.existence"]["decision"] == "confirmed"
    assert nic["azure.vm-network-interface.necessity"]["decision"] == "documented"


def test_replicated_gcp_function_intervention_confirms_necessity():
    model = build("gcp")
    claims = {claim["claimId"]: claim for claim in model["claims"]}
    backend = claims["gcp.backend-service-backend-group.necessity"]

    assert backend["decision"] == "confirmed"
    assert any(
        item["sourceRole"] == "runtimeIntervention" and item["replications"] == 3
        for item in backend["observations"]
    )


def test_missing_official_operation_blocks_boundary_confirmation(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "neutralSource": "app/core/cloudkb/depkb/neutral_candidates/crosswalk.json",
        "providers": {"aws": [{
            "id": "vm", "conceptId": "neutral.compute", "serviceFamily": "ec2",
            "operations": {"create": "DoesNotExist", "read": "DescribeInstances",
                           "delete": "TerminateInstances"},
            "capabilityIds": ["linux-vm-runtime"],
        }]},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="absent"):
        build("aws", config_path=config)
