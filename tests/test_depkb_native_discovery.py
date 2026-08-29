from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cloudkb.depkb.fetch_vendors import is_cached
from app.cloudkb.depkb.native.discovery import (
    PROTOCOL,
    discover_aws,
    discover_azure,
    discover_gcp,
)
from app.cloudkb.depkb.native.model import validate_inventory

_CACHE_KEYS = {
    discover_aws: "aws-cfn",
    discover_gcp: "gcp-compute",
}


def _skip_without_pinned_source(discover) -> None:
    key = _CACHE_KEYS.get(discover)
    if key and not is_cached(key):
        pytest.skip(f"고정 {key} 원천 스냅샷이 없는 환경에서는 원천 재추출을 실행하지 않는다")


def test_protocol_forbids_prior_model_and_benchmark_discovery_inputs():
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))

    forbidden = " ".join(protocol["biasControls"]["forbiddenDiscoveryInputs"])
    assert "vocabulary.py" in forbidden
    assert protocol["downstreamOnlyEvaluationInputs"] == ["P1", "P2", "P3"]


@pytest.mark.parametrize("discover", [discover_aws, discover_azure, discover_gcp])
def test_native_discovery_is_pinned_and_contains_no_cross_provider_projection(discover):
    _skip_without_pinned_source(discover)
    inventory = discover()
    validate_inventory(inventory)

    assert inventory["source"]["version"]
    assert inventory["elements"]
    assert all("crossProviderId" not in item for item in inventory["elements"])
    assert all("crossProviderSubject" not in item for item in inventory["candidates"])


@pytest.mark.parametrize("discover", [discover_azure, discover_gcp])
def test_request_body_schema_is_a_traversal_root_not_a_dependency(discover):
    _skip_without_pinned_source(discover)
    inventory = discover()

    assert inventory["candidates"]
    assert all(item["form"] != "requestSchema" for item in inventory["candidates"])
    assert all(item.get("referenceToken") for item in inventory["candidates"])
    assert len(
        {
            (
                item["subjectNativeId"],
                item["referenceToken"],
                item.get("objectNativeId"),
                item["sourceLocator"],
            )
            for item in inventory["candidates"]
        }
    ) == len(inventory["candidates"])


@pytest.mark.parametrize("provider,discover", [
    ("aws", discover_aws),
    ("azure", discover_azure),
    ("gcp", discover_gcp),
])
def test_committed_native_inventory_is_reproducible(provider, discover):
    _skip_without_pinned_source(discover)
    committed = json.loads(
        Path(f"app/cloudkb/depkb/native/{provider}-inventory.json").read_text(
            encoding="utf-8"
        )
    )

    assert committed == discover()
