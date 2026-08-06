"""VM 카탈로그가 연구 범위를 벗어나지 않는지 검사한다."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from app.core.cloudkb.costkb.parsers.tumblebug import project_row


ROOT = Path(__file__).parents[1] / "app" / "core" / "cloudkb"
SUPPORTED = {"aws", "azure", "gcp"}


def _dataset(name: str) -> dict:
    with gzip.open(ROOT / "data" / name, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def test_bundled_vm_catalogs_only_contain_supported_csps() -> None:
    cost = _dataset("tumblebug-cost.json.gz")
    perf = _dataset("tumblebug-perf.json.gz")
    images = _dataset("basic-images.json.gz")
    regions = _dataset("cloud-regions.json.gz")

    assert {row["provider"] for row in cost["specs"]} == SUPPORTED
    assert {row["provider"] for row in perf["specs"]} == SUPPORTED
    assert {row["provider"] for row in images["images"]} == SUPPORTED
    assert set(regions["providers"]) == SUPPORTED


def test_cost_builder_drops_unsupported_csp_rows() -> None:
    row = {
        "provider_name": "ibm",
        "region_name": "us-south",
        "csp_spec_name": "bx2-2x8",
        "v_cpu": 2,
        "memory_gi_b": 8,
        "cost_per_hour": 0.1,
    }
    assert project_row(row) is None


def test_removed_legacy_kbs_do_not_reappear() -> None:
    assert not (ROOT / "capacitykb").exists()
    assert not (ROOT / "sizingkb").exists()
    assert not (ROOT / "costkb" / "parsers" / "aws_managed.py").exists()
    assert not (ROOT / "perfkb" / "parsers" / "ibm_catalog.py").exists()
