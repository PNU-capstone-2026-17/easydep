from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.cloudkb.costkb.deployment_cost import (
    SUPPORTED_CALCULATION_KINDS,
    BudgetVerdict,
    PricingInventory,
    RateQuote,
    UsageScenario,
    estimate_deployment_cost,
)
from app.cloudkb.costkb.resource_pricing import resource_pricing_rules


class FixtureResolver:
    def __init__(self, rates: dict[str, Decimal]) -> None:
        self.rates = rates
        self.calls: list[dict[str, Any]] = []

    def resolve_rate(self, **kwargs: Any) -> RateQuote | None:
        self.calls.append(kwargs)
        rate = self.rates.get(kwargs["rate_key"])
        return RateQuote(rate_key=kwargs["rate_key"], unit_rate_usd=rate) if rate is not None else None


class ExactFixtureResolver:
    def __init__(self, rates: dict[tuple[str, tuple[tuple[str, str], ...]], Decimal]) -> None:
        self.rates = rates
        self.calls: list[dict[str, Any]] = []

    def resolve_rate(self, **kwargs: Any) -> RateQuote | None:
        self.calls.append(kwargs)
        dimensions = tuple(sorted((str(key), str(value)) for key, value in kwargs["dimensions"].items()))
        rate = self.rates.get((kwargs["rate_key"], dimensions))
        return RateQuote(rate_key=kwargs["rate_key"], unit_rate_usd=rate) if rate else None


def _exact_rate(
    rate_key: str, dimensions: dict[str, str], amount: str
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], Decimal]:
    return (rate_key, tuple(sorted(dimensions.items()))), Decimal(amount)


def _aws_vm(*, budget: str | None = "200") -> PricingInventory:
    return PricingInventory(
        provider="aws",
        monthly_budget_usd=Decimal(budget) if budget is not None else None,
        scenarios=[
            UsageScenario(
                primitive_kind="compute-instance",
                values={
                    "instance_count": 2,
                    "running_seconds": 730 * 3600,
                    "instance_type": "t3.small",
                    "region": "us-east-1",
                    "operating_system": "Linux",
                    "tenancy": "shared",
                    "capacity_status": "used",
                    "unit": "Hrs",
                },
            )
        ],
    )


def test_vm_quote_uses_named_runtime_evaluator_and_budget_verdict() -> None:
    quote = estimate_deployment_cost(
        _aws_vm(budget="100"), FixtureResolver({"instance_runtime_rate": Decimal("0.1")})
    )

    assert quote.known_floor_usd == Decimal("146.0")
    assert quote.complete is True
    assert quote.verdict == BudgetVerdict.EXCEEDS
    assert quote.components[0].term == "runtime"


def test_missing_rate_is_unknown_not_zero_and_budget_is_indeterminate() -> None:
    quote = estimate_deployment_cost(_aws_vm(), FixtureResolver({}))

    assert quote.known_floor_usd == Decimal(0)
    assert quote.complete is False
    assert quote.components[0].amount_usd is None
    assert quote.verdict == BudgetVerdict.INDETERMINATE


def test_aws_nat_keeps_known_hourly_floor_when_processed_data_is_unknown() -> None:
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="aws",
            monthly_budget_usd=Decimal(10),
            scenarios=[
                UsageScenario(
                    primitive_kind="nat-gateway",
                    values={
                        "gateway_hours": "2.4",
                        "region": "us-east-1",
                        "operation": "NatGateway",
                        "usage_type": "NatGateway-Hours",
                        "unit": "Hrs",
                    },
                )
            ],
        ),
        FixtureResolver({"gateway_hour_rate": Decimal("0.1")}),
    )

    assert quote.known_floor_usd == Decimal("0.3")
    assert quote.complete is False
    assert quote.verdict == BudgetVerdict.INDETERMINATE
    assert [(item.term, item.known) for item in quote.components] == [
        ("gateway_runtime", True),
        ("data_processing", False),
    ]


def test_aws_load_balancer_keeps_fixed_terms_when_capacity_usage_is_unknown() -> None:
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="aws",
            monthly_budget_usd=Decimal(10),
            scenarios=[
                UsageScenario(
                    primitive_kind="load-balancer",
                    values={
                        "load_balancer_hours": 10,
                        "public_ipv4_count": 2,
                        "region": "us-east-1",
                        "load_balancer_type": "network",
                        "address_state": "in-use",
                        "unit": "Hrs",
                    },
                )
            ],
        ),
        FixtureResolver(
            {
                "load_balancer_hour_rate": Decimal("0.1"),
                "public_ipv4_hour_rate": Decimal("0.01"),
            }
        ),
    )

    assert quote.known_floor_usd == Decimal("1.20")
    assert quote.complete is False
    assert [(item.term, item.known) for item in quote.components] == [
        ("load_balancer_runtime", True),
        ("capacity", False),
        ("implicit_public_ipv4", True),
    ]


def test_azure_load_balancer_keeps_hourly_rules_when_data_is_unknown() -> None:
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="azure",
            monthly_budget_usd=Decimal(10),
            scenarios=[
                UsageScenario(
                    primitive_kind="load-balancer",
                    values={
                        "load_balancer_hours": 10,
                        "billable_rule_count": 1,
                        "region": "eastus",
                        "tier": "Standard",
                    },
                )
            ],
        ),
        FixtureResolver({"first_five_rules_hour_rate": Decimal("0.02")}),
    )

    assert quote.known_floor_usd == Decimal("0.20")
    assert quote.complete is False
    assert [(item.term, item.known) for item in quote.components] == [
        ("first_five_rules", True),
        ("additional_rules", True),
        ("data_processing", False),
    ]


def test_azure_semantic_fixed_terms_remain_known_when_usage_is_missing() -> None:
    resolver = ExactFixtureResolver(
        dict(
            [
                _exact_rate(
                    "disk_tier_month_rate",
                    {"region": "eastus", "disk_sku": "Standard_LRS"},
                    "4",
                ),
                _exact_rate(
                    "registry_day_rate",
                    {"region": "eastus", "sku": "Basic"},
                    "0.1",
                ),
            ]
        )
    )
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="azure",
            scenarios=[
                UsageScenario(
                    primitive_kind="disk",
                    values={
                        "disk_count": 1,
                        "provisioned_seconds": 730 * 3600,
                        "disk_sku": "Standard_LRS",
                        "region": "eastus",
                    },
                ),
                UsageScenario(
                    primitive_kind="app-registry",
                    values={"registry_days": 30, "sku": "Basic", "region": "eastus"},
                ),
            ],
        ),
        resolver,
    )

    components = {(item.rule_id, item.term): item for item in quote.components}
    assert components[("azure.storage.standard-hdd-managed-disk", "capacity_tier")].amount_usd == Decimal(4)
    assert components[("azure.storage.standard-hdd-managed-disk", "transactions")].known is False
    assert components[("azure.registry.acr-basic", "tier_runtime")].amount_usd == Decimal(3)
    assert components[("azure.registry.acr-basic", "excess_storage")].known is False
    assert quote.complete is False


def test_semantic_dimension_mismatch_is_unknown_without_fuzzy_rate_lookup() -> None:
    resolver = ExactFixtureResolver(
        dict(
            [
                _exact_rate(
                    "gateway_hour_rate",
                    {"region": "eastus", "sku": "Standard"},
                    "0.05",
                )
            ]
        )
    )
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="azure",
            scenarios=[
                UsageScenario(
                    primitive_kind="nat-gateway",
                    values={"gateway_hours": 10, "region": "eastus", "sku": "Basic"},
                )
            ],
        ),
        resolver,
    )

    assert quote.components[0].known is False
    assert quote.components[0].reason == "Missing retail rate for gateway_hour_rate."


def test_gcp_semantic_rates_require_complete_dimensions_and_keep_variable_usage_unknown() -> None:
    missing = FixtureResolver({"disk_gib_time_rate": Decimal("0.1")})
    missing_quote = estimate_deployment_cost(
        PricingInventory(
            provider="gcp",
            scenarios=[
                UsageScenario(
                    primitive_kind="disk",
                    values={
                        "disk_count": 1,
                        "capacity_gib": 10,
                        "provisioned_seconds": 730 * 3600,
                        "region": "us-central1",
                        "disk_type": "pd-balanced",
                    },
                )
            ],
        ),
        missing,
    )
    assert missing_quote.components[0].known is False
    assert "disk_scope, unit" in str(missing_quote.components[0].reason)
    assert missing.calls == []

    resolver = ExactFixtureResolver(
        dict(
            [
                _exact_rate(
                    "per_vm_gateway_hour_rate",
                    {
                        "region": "us-central1",
                        "nat_allocation_mode": "auto-only",
                        "unit": "vm-hour",
                    },
                    "0.01",
                ),
                _exact_rate(
                    "shared_core_instance_runtime_rate",
                    {
                        "region": "us-central1",
                        "machine_type": "e2-micro",
                        "pricing_model": "shared-core-instance",
                        "operating_system": "linux",
                        "unit": "instance-hour",
                    },
                    "0.02",
                ),
                _exact_rate(
                    "first_five_forwarding_rules_hour_rate",
                    {
                        "region": "us-central1",
                        "load_balancing_scheme": "external",
                        "unit": "rule-hour",
                    },
                    "0.03",
                ),
            ]
        )
    )
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="gcp",
            scenarios=[
                UsageScenario(
                    primitive_kind="compute-instance",
                    values={
                        "instance_count": 2,
                        "running_seconds": 3600,
                        "machine_type": "e2-micro",
                        "pricing_model": "shared-core-instance",
                        "operating_system": "linux",
                        "region": "us-central1",
                        "unit": "instance-hour",
                    },
                ),
                UsageScenario(
                    primitive_kind="forwarding-rule",
                    values={
                        "forwarding_rule_count": 1,
                        "forwarding_rule_hours": 10,
                        "region": "us-central1",
                        "load_balancing_scheme": "external",
                        "unit": "rule-hour",
                    },
                ),
                UsageScenario(
                    primitive_kind="cloud-nat",
                    values={
                        "assigned_vm_count": 2,
                        "gateway_hours": 10,
                        "region": "us-central1",
                        "nat_allocation_mode": "auto-only",
                        "unit": "vm-hour",
                    },
                ),
            ],
        ),
        resolver,
    )

    components = {(item.rule_id, item.term): item for item in quote.components}
    assert components[("gcp.compute.vm-on-demand", "shared_core_runtime")].amount_usd == Decimal("0.04")
    assert components[("gcp.network.regional-external-passthrough-nlb", "first_five_forwarding_rules")].amount_usd == Decimal("0.3")
    assert components[("gcp.network.cloud-nat", "gateway_runtime_below_cap")].amount_usd == Decimal("0.2")
    assert components[("gcp.network.regional-external-passthrough-nlb", "inbound_processing")].known is False
    assert components[("gcp.network.cloud-nat", "data_processing")].known is False
    assert quote.complete is False


def test_gcp_resource_based_terms_support_per_rate_semantic_units() -> None:
    resolver = ExactFixtureResolver(
        dict(
            [
                _exact_rate(
                    "vcpu_runtime_rate",
                    {
                        "region": "us-central1",
                        "machine_family": "n2",
                        "pricing_model": "resource-based",
                        "operating_system": "linux",
                        "unit": "vcpu-hour",
                    },
                    "0.02",
                ),
                _exact_rate(
                    "memory_gib_runtime_rate",
                    {
                        "region": "us-central1",
                        "machine_family": "n2",
                        "pricing_model": "resource-based",
                        "operating_system": "linux",
                        "unit": "gib-hour",
                    },
                    "0.01",
                )
            ]
        )
    )
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="gcp",
            scenarios=[
                UsageScenario(
                    primitive_kind="compute-instance",
                    values={
                        "instance_count": 1,
                        "running_seconds": 3600,
                        "machine_type": "n2-standard-2",
                        "machine_family": "n2",
                        "pricing_model": "resource-based",
                        "vcpu_count": 2,
                        "memory_gib": 8,
                        "region": "us-central1",
                        "operating_system": "linux",
                        "rate_dimensions": {
                            "vcpu_runtime_rate": {"unit": "vcpu-hour"},
                            "memory_gib_runtime_rate": {"unit": "gib-hour"},
                        },
                    },
                )
            ],
        ),
        resolver,
    )

    components = {item.term: item for item in quote.components}
    assert components["vcpu_runtime"].amount_usd == Decimal("0.04")
    assert components["memory_runtime"].amount_usd == Decimal("0.08")


def test_gcp_direct_ipv4_uses_semantic_attachment_dimensions() -> None:
    dimensions = {
        "region": "us-central1",
        "attachment_class": "standard-vm",
        "network_tier": "premium",
        "unit": "address-hour",
    }
    resolver = ExactFixtureResolver(
        dict([_exact_rate("external_ipv4_hour_rate", dimensions, "0.005")])
    )
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="gcp",
            scenarios=[
                UsageScenario(
                    primitive_kind="public-ip",
                    values={**dimensions, "address_count": 1, "allocated_hours": 10},
                )
            ],
        ),
        resolver,
    )

    assert quote.components[0].amount_usd == Decimal("0.05")


def test_no_separate_meter_is_not_double_counted() -> None:
    resolver = FixtureResolver({})
    quote = estimate_deployment_cost(
        PricingInventory(
            provider="aws",
            monthly_budget_usd=Decimal(1),
            scenarios=[UsageScenario(primitive_kind="network")],
        ),
        resolver,
    )

    assert quote.components == []
    assert quote.verdict == BudgetVerdict.WITHIN
    assert resolver.calls == []


def test_all_observed_rule_calculation_kinds_have_named_evaluators() -> None:
    assert {rule["calculationKind"] for rule in resource_pricing_rules()} <= SUPPORTED_CALCULATION_KINDS
