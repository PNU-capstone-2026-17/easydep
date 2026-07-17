"""costkb 데이터셋 테스트.

이 로직(filter_specs / _SORT_KEYS)은 `nim_agent/cloud/dataset.py`였을 때
**직접 테스트가 0건**이었다. 손으로 편집하는 데이터라 스키마 검증과 함께 여기서 고정한다.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from costkb import dataset
from costkb.dataset import coverage, dataset_note, filter_specs, load_specs

# 번들 36건을 보도록 output_dir을 고정하는 픽스처는 `tests/conftest.py`에 있다(autouse).

# --- 번들 데이터 ---


def test_bundled_dataset_is_valid_and_loads() -> None:
    specs = load_specs()
    assert len(specs) == 36
    assert all(s["provider"] in ("aws", "azure", "gcp") for s in specs)


def test_note_is_preserved_as_file_level_evidence() -> None:
    """레코드별 evidence 대신 _note가 출처·한계를 담는다."""
    note = dataset_note()
    assert "청구서가 아니" in note
    assert "cb-tumblebug" in note


# --- 스키마 검증 (예전에 없던 안전망) ---


def _validate(payload: dict) -> None:
    jsonschema.validate(payload, dataset._schema())


def _sample(**overrides) -> dict:
    spec = {
        "provider": "aws",
        "region": "us-east-1",
        "specName": "t3.medium",
        "vCPU": 2,
        "memGiB": 4,
        "hourlyUSD": 0.0416,
        "family": "burstable",
    }
    spec.update(overrides)
    return {"_note": "테스트", "specs": [spec]}


def test_schema_accepts_good_record() -> None:
    _validate(_sample())


def test_schema_rejects_field_typo() -> None:
    """`memGib` 오타는 예전엔 filter_specs 안에서 KeyError로 터졌다."""
    bad = _sample()
    bad["specs"][0]["memGib"] = bad["specs"][0].pop("memGiB")
    with pytest.raises(jsonschema.ValidationError):
        _validate(bad)


def test_schema_rejects_missing_field() -> None:
    bad = _sample()
    del bad["specs"][0]["hourlyUSD"]
    with pytest.raises(jsonschema.ValidationError):
        _validate(bad)


def test_schema_rejects_unknown_family() -> None:
    with pytest.raises(jsonschema.ValidationError):
        _validate(_sample(family="quantum"))


def test_schema_rejects_unknown_provider() -> None:
    with pytest.raises(jsonschema.ValidationError):
        _validate(_sample(provider="oracle"))


def test_schema_rejects_nonpositive_numbers() -> None:
    for field, value in (("vCPU", 0), ("memGiB", 0), ("hourlyUSD", 0)):
        with pytest.raises(jsonschema.ValidationError):
            _validate(_sample(**{field: value}))


def test_schema_requires_note() -> None:
    bad = _sample()
    del bad["_note"]
    with pytest.raises(jsonschema.ValidationError):
        _validate(bad)


def test_load_raises_on_invalid_data(tmp_path, monkeypatch) -> None:
    """검증은 로드 시점에 한 번 — 필터 안에서 KeyError로 터지지 않는다."""
    broken = tmp_path / "specs.json"
    broken.write_text(json.dumps(_sample(family="quantum")), encoding="utf-8")
    monkeypatch.setattr(dataset, "_SPECS_PATH", broken)
    dataset._load_cached.cache_clear()
    with pytest.raises(jsonschema.ValidationError):
        load_specs()


# --- filter_specs 경계값 ---


def test_filter_by_vcpu_and_memory() -> None:
    found = filter_specs(vcpu_min=8, mem_min_gib=32, limit=99)
    assert found
    assert all(s["vCPU"] >= 8 and s["memGiB"] >= 32 for s in found)


def test_filter_by_provider() -> None:
    found = filter_specs(provider="gcp", limit=99)
    assert found
    assert all(s["provider"] == "gcp" for s in found)


def test_provider_filter_is_case_insensitive() -> None:
    assert filter_specs(provider="AWS", limit=99) == filter_specs(provider="aws", limit=99)


def test_region_filter_is_substring_match() -> None:
    """'us-east'가 'us-east-1'을 잡아야 한다 (부분 일치)."""
    found = filter_specs(region="us-east", limit=99)
    assert found
    assert all("us-east" in s["region"] for s in found)


def test_impossible_requirement_returns_empty() -> None:
    """데이터셋 경계를 넘으면 빈 리스트 — 예외가 아니다."""
    assert filter_specs(vcpu_min=1024, limit=99) == []


def test_unknown_sort_falls_back_to_cost() -> None:
    assert filter_specs(sort_by="vibes", limit=5) == filter_specs(sort_by="cost", limit=5)


def test_sort_directions() -> None:
    by_cost = filter_specs(sort_by="cost", limit=99)
    assert by_cost == sorted(by_cost, key=lambda s: s["hourlyUSD"])  # 저렴한 순

    by_vcpu = filter_specs(sort_by="vcpu", limit=99)
    assert [s["vCPU"] for s in by_vcpu] == sorted(
        (s["vCPU"] for s in by_vcpu), reverse=True
    )  # 큰 순

    by_mem = filter_specs(sort_by="memory", limit=99)
    assert [s["memGiB"] for s in by_mem] == sorted(
        (s["memGiB"] for s in by_mem), reverse=True
    )


def test_limit_is_applied() -> None:
    assert len(filter_specs(limit=3)) == 3


@pytest.mark.parametrize("limit", [0, -5])
def test_nonpositive_limit_returns_one(limit: int) -> None:
    """limit<=0 이면 max(1, limit)으로 최소 1건."""
    assert len(filter_specs(limit=limit)) == 1


# --- coverage ---


def test_coverage_reports_dataset_boundaries() -> None:
    """'스펙 없음'이 나올 때 경계를 확인하는 용도."""
    rows = coverage()
    assert len(rows) == 4  # aws/us-east-1, aws/ap-northeast-2, azure/eastus, gcp/us-central1
    assert sum(r["count"] for r in rows) == 36
    seoul = next(r for r in rows if r["region"] == "ap-northeast-2")
    assert seoul["vcpu_max"] == 4  # 서울은 커버리지가 좁다
