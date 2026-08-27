import pytest

from app.cloudkb.depkb import schema_evidence
from app.cloudkb.depkb.fetch_vendors import is_cached


def test_every_unique_schema_locator_resolves_against_its_pinned_source():
    if not is_cached("gcp-compute"):
        pytest.skip("고정 GCP 원천 스냅샷이 없는 환경에서는 전체 원천 대조를 실행하지 않는다")
    resolutions = schema_evidence.verify_claims()
    assert len(resolutions) == 35
    assert all(item.exists for item in resolutions)
    assert {item.source for item in resolutions} == {
        "aws-cfn",
        "gcp-compute",
        "compute-ComputeRP.json",
        "network-loadBalancer.json",
        "network-virtualNetwork.json",
    }


def test_legacy_azure_path_locator_with_embedded_slashes_resolves():
    value = schema_evidence.resolve(
        "network-virtualNetwork.json#/paths//subscriptions/{subscriptionId}/"
        "resourceGroups/{resourceGroupName}/providers/Microsoft.Network/"
        "virtualNetworks/{virtualNetworkName}/subnets/{subnetName}"
    )
    assert isinstance(value, dict)
