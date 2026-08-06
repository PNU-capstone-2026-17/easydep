import json
from pathlib import Path

import pytest

from app.core.cloudkb.depkb.closure import closure
from app.core.cloudkb.depkb.scope import VM_ANCHOR_TYPES, is_vm_claim


def test_generated_claims_are_strictly_vm_scoped():
    artifact = Path("app/core/cloudkb/depkb/claims.json")
    claims = json.loads(artifact.read_text(encoding="utf-8"))["claims"]

    assert claims
    assert all(is_vm_claim(claim) for claim in claims)
    assert {claim["csp"] for claim in claims} == {"aws", "azure", "gcp"}


@pytest.mark.parametrize("csp", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("anchor", ["vm", "disk", "loadBalancer"])
def test_product_anchors_have_a_plan_for_each_csp(csp, anchor):
    result = closure(anchor, csp)

    assert result.startResource == anchor
    assert result.csp == csp
    assert anchor in result.createOrder


def test_claims_use_only_relation_specific_findings():
    claims = json.loads(
        Path("app/core/cloudkb/depkb/claims.json").read_text(encoding="utf-8")
    )["claims"]
    forbidden_fields = {"question", "verdict", "predicate", "oracle", "evidence"}
    assert all(not (forbidden_fields & set(claim)) for claim in claims)
    assert all(claim["replicationStatus"] in {"pending", "replicated", "failed"} for claim in claims)


@pytest.mark.parametrize(
    "resource", ["k8sCluster", "k8sService", "k8sPvc", "vpn", "fileSystem"]
)
def test_out_of_scope_resources_are_rejected(resource):
    assert resource not in VM_ANCHOR_TYPES
    with pytest.raises(KeyError, match="Docker-on-VM"):
        closure(resource, "aws")


def test_retained_experiment_evidence_contains_no_private_keys():
    experiments = Path("app/core/cloudkb/depkb/experiments")
    marker = "PRIVATE KEY-----"

    contaminated = [
        str(path)
        for path in experiments.rglob("*.json")
        if marker in path.read_text(encoding="utf-8", errors="replace")
    ]

    assert contaminated == []
