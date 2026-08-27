import json
from pathlib import Path

import pytest

from app.cloudkb.depkb.closure import closure
from app.cloudkb.depkb.scope import VM_ANCHOR_TYPES, is_vm_claim


def test_generated_claims_are_strictly_vm_scoped():
    artifact = Path("app/cloudkb/depkb/claims.json")
    claims = json.loads(artifact.read_text(encoding="utf-8"))["claims"]

    assert claims
    assert all(is_vm_claim(claim) for claim in claims)
    assert {claim["csp"] for claim in claims} == {"aws", "azure", "gcp"}


def test_every_dynamic_observation_has_a_valid_local_evidence_coordinate():
    claims = json.loads(
        Path("app/cloudkb/depkb/claims.json").read_text(encoding="utf-8")
    )["claims"]
    assert len(claims) == 44
    for claim in claims:
        for observation in claim["observations"]:
            experiment = observation.get("experiment")
            result_file = observation.get("resultFile")
            if experiment:
                assert result_file == f"experiments/{experiment}/results.json"
                assert (Path("app/cloudkb/depkb") / result_file).is_file()


def test_product_kb_contains_only_creation_and_runtime_dependencies():
    claims = json.loads(
        Path("app/cloudkb/depkb/claims.json").read_text(encoding="utf-8")
    )["claims"]

    assert {claim["relationFamily"] for claim in claims} == {
        "provisioning",
        "runtime",
    }


def test_exclusive_choice_uses_cardinality_not_borrowed_idl_labels():
    claims = json.loads(
        Path("app/cloudkb/depkb/claims.json").read_text(encoding="utf-8")
    )["claims"]
    choice = next(c for c in claims if c["condition"]["kind"] == "exclusiveChoice")
    assert choice["condition"]["machine"] == {"cardinality": {"min": 1, "max": 1}}


@pytest.mark.parametrize("csp", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("anchor", ["vm", "disk", "loadBalancer"])
def test_product_anchors_have_a_plan_for_each_csp(csp, anchor):
    result = closure(anchor, csp)

    assert result.startResource == anchor
    assert result.csp == csp
    assert anchor in result.createOrder


def test_claims_use_only_relation_specific_findings():
    artifact = json.loads(
        Path("app/cloudkb/depkb/claims.json").read_text(encoding="utf-8")
    )
    claims = artifact["claims"]
    forbidden_fields = {"question", "verdict", "predicate", "oracle", "evidence"}
    assert all(not (forbidden_fields & set(claim)) for claim in claims)
    assert all(claim["replicationStatus"] in {"pending", "replicated", "failed"} for claim in claims)
    assert artifact["methodology"]["originalProtocolTiming"] == "retrospective"
    assert "not preregistered" in artifact["methodology"]["expectedOutcomeOrigin"]


def test_unreplicated_findings_do_not_drive_closure_but_remain_visible():
    result = closure("vm", "aws")

    unavailable = {
        (item.subject, item.object, item.relationFamily, item.replicationStatus)
        for item in result.unavailableFindings
    }
    assert ("vm", "workloadIdentity", "runtime", "failed") in unavailable
    assert all(
        not (subject == "vm" and object_ == "workloadIdentity")
        for subject, object_, _signal in result.runtimeRequiredForSignal
    )


@pytest.mark.parametrize(
    "resource", ["k8sCluster", "k8sService", "k8sPvc", "vpn", "fileSystem"]
)
def test_out_of_scope_resources_are_rejected(resource):
    assert resource not in VM_ANCHOR_TYPES
    with pytest.raises(KeyError, match="Docker-on-VM"):
        closure(resource, "aws")


def test_retained_experiment_evidence_contains_no_private_keys():
    experiments = Path("app/cloudkb/depkb/experiments")
    marker = "PRIVATE KEY-----"

    contaminated = [
        str(path)
        for path in experiments.rglob("*.json")
        if marker in path.read_text(encoding="utf-8", errors="replace")
    ]

    assert contaminated == []
