from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from app.cloudkb.costkb.dataset import load_specs
from app.cloudkb.costkb.deployment_cost import estimate_deployment_cost
from app.cloudkb.costkb.retail_rates import load_retail_rate_snapshot
from app.design.services.deployment_diagram.costing import build_pricing_inventory
from app.design.services.deployment_diagram.sizing import (
    _BUNDLED_RETAIL_RATE_SNAPSHOT_PATH,
    _configured_rate_resolver,
)

_PUBLIC_IP_DIMENSIONS = {
    "region": "ap-northeast-2",
    "address_state": "in-use",
    "unit": "Hrs",
}


def _fixed_meter_dimensions(region: str) -> list[tuple[str, str, dict[str, str]]]:
    return [
        (
            "aws.storage.ebs-gp3",
            "storage_gib_month_rate",
            {
                "region": region,
                "volume_type": "gp3",
                "usage_type": "EBS:VolumeUsage.gp3",
                "unit": "GB-Mo",
            },
        ),
        (
            "aws.network.public-ipv4",
            "public_ipv4_hour_rate",
            {"region": region, "address_state": "in-use", "unit": "Hrs"},
        ),
        (
            "aws.network.nat-gateway",
            "gateway_hour_rate",
            {
                "region": region,
                "operation": "NatGateway",
                "usage_type": "NatGateway-Hours",
                "unit": "Hrs",
            },
        ),
        (
            "aws.network.nlb",
            "load_balancer_hour_rate",
            {"region": region, "load_balancer_type": "network", "unit": "Hrs"},
        ),
    ]


def test_bundled_snapshot_has_strict_digest_and_all_provider_coverage() -> None:
    snapshot = load_retail_rate_snapshot(_BUNDLED_RETAIL_RATE_SNAPSHOT_PATH.read_bytes())
    review_path = Path(str(_BUNDLED_RETAIL_RATE_SNAPSHOT_PATH) + ".review.json")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    expected_regions = {
        provider: {row["region"] for row in load_specs() if row.get("provider") == provider}
        for provider in ("aws", "azure", "gcp")
    }
    snapshot_regions = {
        provider: {rate.region for rate in snapshot.rates if rate.provider == provider}
        for provider in expected_regions
    }

    assert snapshot.snapshot_digest == review["snapshotDigest"]
    assert {rate.provider for rate in snapshot.rates} == set(expected_regions)
    assert snapshot_regions == expected_regions
    assert len(snapshot.rates) == 2159
    assert Counter(rate.provider for rate in snapshot.rates) == {
        "aws": 112,
        "azure": 624,
        "gcp": 1423,
    }
    expected_counts = {
        ("aws.storage.ebs-gp3", "storage_gib_month_rate"): 28,
        ("aws.network.public-ipv4", "public_ipv4_hour_rate"): 28,
        ("aws.network.nat-gateway", "gateway_hour_rate"): 28,
        ("aws.network.nlb", "load_balancer_hour_rate"): 28,
        **{
            ("gcp.network.regional-external-passthrough-nlb", key): 43
            for key in (
                "first_five_forwarding_rules_hour_rate",
                "additional_forwarding_rule_hour_rate",
            )
        },
        **{
            ("gcp.network.cloud-nat", key): 43
            for key in (
                "per_vm_gateway_hour_rate",
                "capped_gateway_hour_rate",
                "processed_gib_rate",
                "nat_external_ip_hour_rate",
            )
        },
        ("gcp.network.external-ipv4", "external_ipv4_hour_rate"): 43,
    }
    counts = Counter((rate.rule_id, rate.rate_key) for rate in snapshot.rates)
    assert {key: counts[key] for key in expected_counts} == expected_counts
    assert {item["provider"] for item in review["components"]} == set(expected_regions)
    assert review["documentedAdditions"]["rateCount"] == 43 * 7


def test_default_resolver_resolves_all_fixed_meters_in_spot_regions(monkeypatch) -> None:
    monkeypatch.delenv("EASYDEP_RETAIL_RATE_SNAPSHOT_PATH", raising=False)

    resolver, digest = _configured_rate_resolver()
    assert digest is not None
    expected = {
        "ap-northeast-2": ("0.0912000000", "0.0050000000", "0.0590000000", "0.0225000000"),
        "us-east-1": ("0.0800000000", "0.0050000000", "0.0450000000", "0.0225000000"),
        "eu-west-1": ("0.0880000000", "0.0050000000", "0.0480000000", "0.0252000000"),
    }
    for region, amounts in expected.items():
        for (rule_id, rate_key, dimensions), amount in zip(
            _fixed_meter_dimensions(region), amounts, strict=True
        ):
            quote = resolver.resolve_rate(
                provider="aws",
                rule_id=rule_id,
                rate_key=rate_key,
                dimensions=dimensions,
            )
            assert quote is not None
            assert quote.unit_rate_usd == Decimal(amount)
            assert quote.snapshot_digest == digest


def test_unified_default_resolver_keeps_azure_and_gcp_fixed_terms_exact(monkeypatch) -> None:
    monkeypatch.delenv("EASYDEP_RETAIL_RATE_SNAPSHOT_PATH", raising=False)
    resolver, digest = _configured_rate_resolver()

    azure_disk = resolver.resolve_rate(
        provider="azure",
        rule_id="azure.storage.standard-hdd-managed-disk",
        rate_key="disk_tier_month_rate",
        dimensions={"region": "eastus", "disk_sku": "S4"},
    )
    azure_nat = resolver.resolve_rate(
        provider="azure",
        rule_id="azure.network.nat-gateway",
        rate_key="gateway_hour_rate",
        dimensions={"region": "eastus", "sku": "Standard"},
    )
    gcp_quote = resolver.resolve_rate(
        provider="gcp",
        rule_id="gcp.network.cloud-nat",
        rate_key="per_vm_gateway_hour_rate",
        dimensions={
            "region": "us-central1",
            "nat_allocation_mode": "auto-only",
            "unit": "vm-hour",
        },
    )
    gcp_cap = resolver.resolve_rate(
        provider="gcp",
        rule_id="gcp.network.cloud-nat",
        rate_key="capped_gateway_hour_rate",
        dimensions={
            "region": "us-central1",
            "nat_allocation_mode": "auto-only",
            "unit": "gateway-hour",
        },
    )

    assert azure_disk is not None and azure_disk.unit_rate_usd > 0
    assert azure_nat is not None and azure_nat.unit_rate_usd > 0
    assert azure_disk.snapshot_digest == azure_nat.snapshot_digest == digest
    assert gcp_quote is not None and gcp_quote.unit_rate_usd == Decimal("0.0014")
    assert gcp_cap is not None and gcp_cap.unit_rate_usd == Decimal("0.044")
    assert gcp_quote.snapshot_digest == digest


def test_explicit_snapshot_path_overrides_bundled_snapshot(monkeypatch, tmp_path) -> None:
    override = tmp_path / "override.json"
    override.write_text(
        json.dumps(
            {
                "schemaVersion": "easydep-retail-rate-snapshot/v1",
                "sourceRef": "test override",
                "effectiveAt": "2026-09-15T00:00:00Z",
                "rates": [
                    {
                        "provider": "aws",
                        "region": "ap-northeast-2",
                        "ruleId": "aws.network.public-ipv4",
                        "rateKey": "public_ipv4_hour_rate",
                        "dimensions": _PUBLIC_IP_DIMENSIONS,
                        "unitRateUsd": "0.01",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EASYDEP_RETAIL_RATE_SNAPSHOT_PATH", str(override))

    resolver, _digest = _configured_rate_resolver()
    quote = resolver.resolve_rate(
        provider="aws",
        rule_id="aws.network.public-ipv4",
        rate_key="public_ipv4_hour_rate",
        dimensions=_PUBLIC_IP_DIMENSIONS,
    )

    assert quote is not None and quote.unit_rate_usd == Decimal("0.01")


def test_canonical_aws_seoul_plan_known_floor_includes_gp3_and_public_ip(monkeypatch) -> None:
    monkeypatch.delenv("EASYDEP_RETAIL_RATE_SNAPSHOT_PATH", raising=False)
    resource = {
        "provider": "aws",
        "region": "ap-northeast-2",
        "nodes": [
            {
                "id": "web-vm",
                "providerPrimitiveKind": "compute-instance",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {},
            },
            {
                "id": "web-data",
                "providerPrimitiveKind": "disk",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {"capacityGiB": 20},
            },
            {
                "id": "web-ip",
                "providerPrimitiveKind": "public-ip",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {},
            },
        ],
        "embeddedBlocks": [],
    }
    deployment = {
        "computeUnits": [
            {
                "id": "web",
                "selectedVmSku": "t3.medium",
                "selectedReplicaCount": 1,
                "resourceRequirements": {"minVCpu": 2, "minMemoryGiB": 4},
            }
        ],
        "networkPaths": [],
    }
    resolver, _digest = _configured_rate_resolver()

    quote = estimate_deployment_cost(
        build_pricing_inventory(resource, deployment, {}), resolver
    )
    amounts = {component.term: component.amount_usd for component in quote.components}

    assert amounts["capacity"] == Decimal("1.8240000000")
    assert amounts["address_runtime"] == Decimal("3.6500000000")
    assert quote.known_floor_usd >= Decimal("5.4740000000")


def test_aws_managed_group_fixed_nat_and_nlb_costs_are_known_without_variable_usage(
    monkeypatch,
) -> None:
    monkeypatch.delenv("EASYDEP_RETAIL_RATE_SNAPSHOT_PATH", raising=False)
    resource = {
        "provider": "aws",
        "region": "ap-northeast-2",
        "nodes": [
            {
                "id": "web-group",
                "providerPrimitiveKind": "compute-group",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {},
            },
            {
                "id": "nat-gateway",
                "providerPrimitiveKind": "nat-gateway",
                "handling": "create",
                "attributes": {},
                "sourceRefs": ["project-policy:aws.nat-gateway"],
            },
            {
                "id": "load-balancer-web",
                "providerPrimitiveKind": "load-balancer",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {"scheme": "public"},
                "sourceRefs": ["project-policy:aws.public-l4-load-balancer"],
            },
        ],
        "embeddedBlocks": [],
    }
    deployment = {
        "computeUnits": [
            {
                "id": "web",
                "kind": "managedVmGroup",
                "selectedVmSku": "t3.medium",
                "selectedReplicaCount": 2,
                "resourceRequirements": {"minVCpu": 2, "minMemoryGiB": 4},
            }
        ],
        "networkPaths": [],
    }
    resolver, _digest = _configured_rate_resolver()

    quote = estimate_deployment_cost(
        build_pricing_inventory(resource, deployment, {}), resolver
    )
    components = {(item.rule_id, item.term): item for item in quote.components}

    assert components[("aws.network.nlb", "load_balancer_runtime")].amount_usd == Decimal(
        "16.4250000000"
    )
    assert components[("aws.network.nat-gateway", "gateway_runtime")].amount_usd == Decimal(
        "43.0700000000"
    )
    assert components[("aws.network.nlb", "capacity")].known is False
    assert components[("aws.network.nat-gateway", "data_processing")].known is False
    assert quote.complete is False


def test_us_east_managed_group_prices_fixed_meters_but_not_variable_usage(monkeypatch) -> None:
    monkeypatch.delenv("EASYDEP_RETAIL_RATE_SNAPSHOT_PATH", raising=False)
    resource = {
        "provider": "aws",
        "region": "us-east-1",
        "nodes": [
            {
                "id": "web-group",
                "providerPrimitiveKind": "compute-group",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {},
            },
            {
                "id": "web-data",
                "providerPrimitiveKind": "disk",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {"capacityGiB": 20},
            },
            {
                "id": "web-ip",
                "providerPrimitiveKind": "public-ip",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {},
            },
            {
                "id": "nat-gateway",
                "providerPrimitiveKind": "nat-gateway",
                "handling": "create",
                "attributes": {},
                "sourceRefs": ["project-policy:aws.nat-gateway"],
            },
            {
                "id": "load-balancer-web",
                "providerPrimitiveKind": "load-balancer",
                "logicalRef": "web",
                "handling": "create",
                "attributes": {"scheme": "public"},
                "sourceRefs": ["project-policy:aws.public-l4-load-balancer"],
            },
        ],
        "embeddedBlocks": [],
    }
    deployment = {
        "computeUnits": [
            {
                "id": "web",
                "kind": "managedVmGroup",
                "selectedVmSku": "t3.medium",
                "selectedReplicaCount": 2,
                "resourceRequirements": {"minVCpu": 2, "minMemoryGiB": 4},
            }
        ],
        "networkPaths": [],
    }
    resolver, _digest = _configured_rate_resolver()

    quote = estimate_deployment_cost(
        build_pricing_inventory(resource, deployment, {}), resolver
    )
    components = {(item.rule_id, item.term): item for item in quote.components}

    assert components[("aws.storage.ebs-gp3", "capacity")].amount_usd == Decimal(
        "1.6000000000"
    )
    assert components[("aws.network.public-ipv4", "address_runtime")].amount_usd == Decimal(
        "3.6500000000"
    )
    assert components[("aws.network.nat-gateway", "gateway_runtime")].amount_usd == Decimal(
        "32.8500000000"
    )
    assert components[("aws.network.nlb", "load_balancer_runtime")].amount_usd == Decimal(
        "16.4250000000"
    )
    assert components[("aws.network.nat-gateway", "data_processing")].known is False
    assert components[("aws.network.nlb", "capacity")].known is False
    assert quote.complete is False
