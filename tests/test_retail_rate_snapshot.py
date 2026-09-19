from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.cloudkb.costkb.retail_rates import (
    RetailRateSnapshot,
    SnapshotRateResolver,
    VmDatasetRateResolver,
    build_retail_rate_resolver,
    load_retail_rate_snapshot,
)


def _snapshot(*, rate: str = "0.25", stale_after: str | None = None) -> RetailRateSnapshot:
    body = {
        "schemaVersion": "easydep-retail-rate-snapshot/v1",
        "sourceRef": "reviewed-price-export-2026-09",
        "effectiveAt": "2026-09-01T00:00:00Z",
        "rates": [
            {
                "provider": "aws",
                "region": "us-east-1",
                "ruleId": "aws.compute.ec2-on-demand",
                "rateKey": "instance_runtime_rate",
                "dimensions": {
                    "instance_type": "t3.small",
                    "region": "us-east-1",
                    "operating_system": "Linux",
                    "tenancy": "shared",
                    "capacity_status": "used",
                    "unit": "Hrs",
                },
                "unitRateUsd": rate,
            }
        ],
    }
    if stale_after:
        body["staleAfter"] = stale_after
    return load_retail_rate_snapshot(body)


def _aws_dimensions(region: str = "us-east-1") -> dict[str, str]:
    return {
        "instance_type": "t3.small",
        "region": region,
        "operating_system": "Linux",
        "tenancy": "shared",
        "capacity_status": "used",
        "unit": "Hrs",
    }


def test_snapshot_resolves_only_exact_region_and_dimensions_with_provenance() -> None:
    snapshot = _snapshot(stale_after="2026-09-02T00:00:00Z")
    resolver = SnapshotRateResolver(
        snapshot, now=datetime(2026, 9, 3, tzinfo=UTC)
    )

    quote = resolver.resolve_rate(
        provider="aws",
        rule_id="aws.compute.ec2-on-demand",
        rate_key="instance_runtime_rate",
        dimensions=_aws_dimensions(),
    )

    assert quote is not None
    assert quote.unit_rate_usd == Decimal("0.25")
    assert quote.currency == "USD"
    assert quote.source_ref == "reviewed-price-export-2026-09"
    assert quote.effective_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert quote.snapshot_digest == snapshot.snapshot_digest
    assert quote.stale is True  # metadata only; it is still a usable reviewed quote.
    assert (
        resolver.resolve_rate(
            provider="aws",
            rule_id="aws.compute.ec2-on-demand",
            rate_key="instance_runtime_rate",
            dimensions=_aws_dimensions("ap-northeast-2"),
        )
        is None
    )
    assert (
        resolver.resolve_rate(
            provider="aws",
            rule_id="aws.compute.ec2-on-demand",
            rate_key="instance_runtime_rate",
            dimensions={**_aws_dimensions(), "unit": None},
        )
        is None
    )


def test_snapshot_digest_is_stable_when_object_key_or_rate_order_changes() -> None:
    snapshot = _snapshot()
    reordered = load_retail_rate_snapshot(
        {
            "rates": [
                {
                    "unitRateUsd": "0.25",
                    "dimensions": dict(reversed(list(_aws_dimensions().items()))),
                    "rateKey": "instance_runtime_rate",
                    "ruleId": "aws.compute.ec2-on-demand",
                    "region": "us-east-1",
                    "provider": "aws",
                }
            ],
            "effectiveAt": "2026-09-01T00:00:00Z",
            "sourceRef": "reviewed-price-export-2026-09",
            "schemaVersion": "easydep-retail-rate-snapshot/v1",
        }
    )

    assert snapshot.snapshot_digest == reordered.snapshot_digest


def test_malformed_or_duplicate_snapshot_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_retail_rate_snapshot({"schemaVersion": "v0", "rates": []})
    with pytest.raises(ValidationError, match="duplicate exact retail-rate keys"):
        load_retail_rate_snapshot(
            {
                "schemaVersion": "easydep-retail-rate-snapshot/v1",
                "sourceRef": "reviewed",
                "effectiveAt": "2026-09-01T00:00:00Z",
                "rates": [_snapshot().rates[0].model_dump(by_alias=True)] * 2,
            }
        )


def test_vm_dataset_resolver_requires_single_exact_priced_sku() -> None:
    resolver = VmDatasetRateResolver(
        [
            {
                "provider": "aws",
                "region": "us-east-1",
                "specName": "t3.small",
                "hourlyUSD": "0.0208",
            },
            {
                "provider": "aws",
                "region": "ap-northeast-2",
                "specName": "t3.small",
                "hourlyUSD": None,
            },
        ]
    )
    exact = resolver.resolve_rate(
        provider="aws",
        rule_id="aws.compute.ec2-on-demand",
        rate_key="instance_runtime_rate",
        dimensions=_aws_dimensions(),
    )

    assert exact is not None
    assert exact.unit_rate_usd == Decimal("0.0208")
    assert exact.source_ref == "costkb-vm-dataset"
    assert (
        resolver.resolve_rate(
            provider="aws",
            rule_id="aws.compute.ec2-on-demand",
            rate_key="instance_runtime_rate",
            dimensions=_aws_dimensions("ap-northeast-2"),
        )
        is None
    )
    assert (
        resolver.resolve_rate(
            provider="aws",
            rule_id="aws.storage.ebs-gp3",
            rate_key="storage_gib_month_rate",
            dimensions={"region": "us-east-1"},
        )
        is None
    )
    ambiguous = VmDatasetRateResolver(
        [
            {
                "provider": "aws",
                "region": "us-east-1",
                "specName": "t3.small",
                "hourlyUSD": "0.0208",
            },
            {
                "provider": "aws",
                "region": "us-east-1",
                "specName": "t3.small",
                "hourlyUSD": "0.021",
            },
        ]
    )
    assert (
        ambiguous.resolve_rate(
            provider="aws",
            rule_id="aws.compute.ec2-on-demand",
            rate_key="instance_runtime_rate",
            dimensions=_aws_dimensions(),
        )
        is None
    )


def test_snapshot_precedes_vm_dataset_in_composite_resolver() -> None:
    resolver = build_retail_rate_resolver(
        _snapshot(rate="0.25"),
        vm_specs=[
            {
                "provider": "aws",
                "region": "us-east-1",
                "specName": "t3.small",
                "hourlyUSD": "0.0208",
            }
        ],
    )

    quote = resolver.resolve_rate(
        provider="aws",
        rule_id="aws.compute.ec2-on-demand",
        rate_key="instance_runtime_rate",
        dimensions=_aws_dimensions(),
    )

    assert quote is not None
    assert quote.unit_rate_usd == Decimal("0.25")
    assert quote.snapshot_digest is not None
