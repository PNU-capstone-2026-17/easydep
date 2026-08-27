from __future__ import annotations

import pytest

from app.cloudkb.depkb.evidence_model import freeze_model, validate_frozen_model


def _claim():
    return {
        "claimId": "aws.vm.boundary",
        "claimType": "resourceBoundary",
        "observations": [{
            "sourceRole": "vendorLifecycleSchema",
            "sourceLocator": "official#vm",
            "sourceSha256": "a" * 64,
            "supports": True,
            "independentIdentity": True,
            "lifecycleOperations": ["create", "read", "delete"],
        }],
    }


def test_official_evidence_model_freezes_without_blank_expert_ballots():
    model = freeze_model("aws", [_claim()])

    validate_frozen_model(model)
    assert model["claims"][0]["decision"] == "confirmed"


def test_evidence_decision_tampering_breaks_validation():
    model = freeze_model("aws", [_claim()])
    model["claims"][0]["decision"] = "candidate"

    with pytest.raises(ValueError, match="differs"):
        validate_frozen_model(model)
