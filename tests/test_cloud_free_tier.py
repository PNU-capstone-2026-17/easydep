from __future__ import annotations

from app.cloudkb.costkb.free_tier import (
    load_free_tier_policy,
    preferred_vm_free_tier_skus,
    vm_free_tier_status,
)


def _status(provider: str, region: str, sku: str) -> dict:
    return vm_free_tier_status(provider=provider, region=region, sku=sku)


def test_policy_covers_three_providers_with_official_sources_and_limits() -> None:
    policy = load_free_tier_policy()

    assert set(policy["providers"]) == {"aws", "azure", "gcp"}
    for offer in policy["providers"].values():
        assert offer["conditions"]
        assert all(source["url"].startswith("https://") for source in offer["sources"])


def test_aws_status_distinguishes_shared_and_account_cohort_skus() -> None:
    assert _status("aws", "us-east-1", "t3.micro")["status"] == "eligible"
    assert _status("aws", "us-east-1", "t2.micro")["status"] == "conditional"
    assert _status("aws", "us-east-1", "t3.small")["status"] == "conditional"
    assert _status("aws", "us-east-1", "t3a.micro")["status"] == "notEligible"


def test_azure_and_gcp_rules_include_region_boundaries() -> None:
    assert _status("azure", "koreasouth", "Standard_B1s")["status"] == "eligible"
    assert _status("gcp", "us-central1", "e2-micro")["status"] == "eligible"
    assert _status("gcp", "asia-northeast3", "e2-micro")["status"] == "notEligible"
    assert preferred_vm_free_tier_skus(provider="gcp", region="us-central1") == ["e2-micro"]
    assert preferred_vm_free_tier_skus(provider="gcp", region="asia-northeast3") == []
