from __future__ import annotations

import pytest

from app.cloudkb.depkb.provider_realizations import capability_realizations
from app.cloudkb.infra_planning import plan_for_anchors


@pytest.mark.parametrize("provider,count,composition", [
    ("aws", 4, "multi-resource"),
    ("azure", 6, "multi-resource"),
    ("gcp", 4, "multi-resource"),
])
def test_load_balancer_plan_carries_provider_specific_realization(provider, count, composition):
    plan = plan_for_anchors(["vm", "loadBalancer"], provider, "test-region")
    realization = plan.intent.capabilityRealizations[0]
    assert realization["composition"] == composition
    assert len(realization["components"]) == count
    assert all(item["boundaryStatus"] == "confirmed" for item in realization["components"])
    assert plan.design["capabilityRealizations"] == list(plan.intent.capabilityRealizations)
    assert plan.provision["capabilityRealizations"] == list(plan.intent.capabilityRealizations)
    assert plan.intent.officialDependencies
    assert plan.design["officialDependencies"] == list(plan.intent.officialDependencies)


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_https_load_balancer_is_out_of_scope(provider):
    plan = plan_for_anchors(
        ["vm", "loadBalancer"],
        provider,
        "test-region",
        capability_ids=("https-load-balanced-ingress",),
    )

    assert plan.intent.capabilityRealizations == ()


def test_plain_vm_plan_does_not_receive_unrequested_load_balancer_components():
    plan = plan_for_anchors(["vm"], "aws", "test-region")
    assert plan.intent.capabilityRealizations == ()
    assert all(item["from"] == "vm" for item in plan.intent.officialDependencies)


def test_unknown_capability_does_not_invent_a_realization():
    assert capability_realizations("aws", "unknown-capability") == ()
