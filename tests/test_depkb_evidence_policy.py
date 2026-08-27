from __future__ import annotations

from app.cloudkb.depkb.evidence_policy import adjudicate


def _evidence(role, **values):
    return {
        "sourceRole": role, "sourceLocator": "official#location",
        "sourceSha256": "a" * 64, "supports": True, **values,
    }


def test_vendor_lifecycle_schema_can_confirm_resource_boundary_without_expert_vote():
    result = adjudicate({
        "claimType": "resourceBoundary",
        "observations": [_evidence(
            "vendorLifecycleSchema", independentIdentity=True,
            lifecycleOperations=["create", "read", "update", "delete"],
        )],
    })

    assert result["decision"] == "confirmed"
    assert result["humanReviewRequired"] is False


def test_reference_schema_does_not_overclaim_dependency_necessity():
    result = adjudicate({
        "claimType": "dependencyNecessity",
        "observations": [_evidence("vendorReferenceSchema")],
    })

    assert result["decision"] == "candidate"


def test_replicated_removal_recovery_confirms_necessity():
    result = adjudicate({
        "claimType": "dependencyNecessity",
        "observations": [_evidence(
            "runtimeIntervention", controlPassed=True, removalFailed=True,
            restorationPassed=True, replications=3,
        )],
    })

    assert result["decision"] == "confirmed"


def test_conflicting_pinned_sources_route_only_exception_to_human():
    contradiction = _evidence("vendorManual") | {"supports": False}
    result = adjudicate({
        "claimType": "dependencyExistence",
        "observations": [_evidence("vendorReferenceSchema"), contradiction],
    })

    assert result["decision"] == "exceptionReview"
    assert result["humanReviewRequired"] is True
