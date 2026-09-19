"""Regression coverage for the ResourcePlan-to-pricing adapter boundary."""

from __future__ import annotations

import pytest

from app.cloudkb.costkb.resource_pricing import (
    no_separate_meter_primitive_kinds,
    resource_pricing_rule_for_primitive,
)
from app.design.services.deployment_diagram.bundle import build_deployment_diagram_bundle
from app.design.services.deployment_diagram.costing import (
    build_pricing_inventory,
    explicit_pricing_exclusion_reason,
)
from scripts.generate_deployment_diagram_examples import (
    DEPLOYMENT_CASES,
    deployment_case_graph,
    deployment_resource_spec,
)


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("case", DEPLOYMENT_CASES)
def test_generated_primitives_are_priced_or_explicitly_structural(
    provider: str, case: str
) -> None:
    """Do not let a newly generated primitive silently disappear from costing.

    A primitive with a pricing rule can share one root meter with another node
    (for example a listener with its load balancer).  It must therefore have a
    scenario for that rule, not necessarily a scenario with the same node id.
    Structural primitives are the only permitted no-scenario classification.
    """

    bundle = build_deployment_diagram_bundle(
        deployment_case_graph(case), deployment_resource_spec(provider)
    )
    projection = bundle["projections"][0]
    resource_plan = projection["resourcePlan"]
    inventory = build_pricing_inventory(
        resource_plan,
        projection["deploymentPlan"],
        bundle["workloadGraph"],
    )
    scenario_rules = {
        rule["id"]
        for scenario in inventory.scenarios
        if (rule := resource_pricing_rule_for_primitive(provider, scenario.primitive_kind))
        is not None
    }
    structural = no_separate_meter_primitive_kinds(provider)
    unclassified: list[str] = []
    for node in resource_plan["nodes"]:
        if node.get("handling") != "create":
            continue
        primitive = str(node.get("providerPrimitiveKind") or "")
        if primitive in structural:
            continue
        if explicit_pricing_exclusion_reason(provider, node) is not None:
            continue
        rule = resource_pricing_rule_for_primitive(provider, primitive)
        if rule is not None and rule["id"] in scenario_rules:
            continue
        unclassified.append(f"{node.get('id')}:{primitive}")
    assert not unclassified, (
        "Every generated primitive must be represented by a priced root meter "
        "or explicitly classified as structural: " + ", ".join(unclassified)
    )
