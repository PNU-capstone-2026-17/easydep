"""costkb 데이터셋 테스트.

이 로직(filter_specs / _SORT_KEYS)은 `nim_agent/cloud/dataset.py`였을 때
**직접 테스트가 0건**이었다. 손으로 편집하는 데이터라 스키마 검증과 함께 여기서 고정한다.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from app.core.cloudkb.costkb import dataset
from app.core.cloudkb.costkb.dataset import coverage, dataset_note, filter_specs, load_specs

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
    dataset.clear_caches()
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


def test_region_filter_falls_back_to_substring() -> None:
    """'us-east'가 'us-east-1'을 잡아야 한다 — **정확 일치가 없을 때만**.

    번들 36건에는 `us-east`라는 리전이 없어서 부분 일치로 떨어진다. 미러 전체에는
    `us-east`가 실존 리전(190건)이므로 거기서는 정확 일치가 이긴다 —
    `test_costkb_region.py`가 그 갈림을 따로 고정한다.
    """
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


# --- 리전 접기 (결함 ④) ---


def _multi_region_specs(tmp_path):
    """같은 스펙이 리전만 달리해 여러 벌 있는 데이터셋을 만든다."""
    specs = [
        {"provider": "tencent", "region": "ap-chengdu", "specName": "S5.MEDIUM4",
         "vCPU": 2, "memGiB": 4, "hourlyUSD": 0.0300},
        {"provider": "tencent", "region": "ap-chongqing", "specName": "S5.MEDIUM4",
         "vCPU": 2, "memGiB": 4, "hourlyUSD": 0.0300},
        {"provider": "tencent", "region": "ap-guangzhou", "specName": "S5.MEDIUM4",
         "vCPU": 2, "memGiB": 4, "hourlyUSD": 0.0300},
        {"provider": "tencent", "region": "ap-chengdu", "specName": "BF1.MEDIUM4",
         "vCPU": 2, "memGiB": 4, "hourlyUSD": 0.0301},
    ]
    path = tmp_path / "tumblebug-cost.json"
    path.write_text(json.dumps({"_note": "테스트", "specs": specs}), encoding="utf-8")
    dataset.clear_caches()
    return tmp_path


def test_folds_same_spec_across_regions(tmp_path) -> None:
    """6칸 요청에 실제 스펙이 3종만 나오던 문제 — 같은 스펙은 한 칸만 먹는다."""
    out = _multi_region_specs(tmp_path)
    results = filter_specs(2, 4, limit=5, architecture=None, output_dir=out)
    names = [s["specName"] for s in results]
    assert names == ["S5.MEDIUM4", "BF1.MEDIUM4"], names
    assert len(names) == len(set(names)), "같은 스펙이 여러 칸을 먹었다"


def test_folding_records_what_it_hid(tmp_path) -> None:
    """접었다는 사실을 남긴다 — 숨기면 '이 리전에만 있다'는 거짓 인상을 준다."""
    out = _multi_region_specs(tmp_path)
    top = filter_specs(2, 4, limit=5, architecture=None, output_dir=out)[0]
    assert sorted(top["_foldedRegions"]) == ["ap-chongqing", "ap-guangzhou"]


def test_folding_keeps_the_best_row(tmp_path) -> None:
    """정렬 뒤에 접으므로 남는 레코드는 그 스펙의 최선(비용 정렬이면 최저가)이다."""
    specs = [
        {"provider": "aws", "region": "us-west-2", "specName": "m5.large",
         "vCPU": 2, "memGiB": 8, "hourlyUSD": 0.1120},
        {"provider": "aws", "region": "us-east-1", "specName": "m5.large",
         "vCPU": 2, "memGiB": 8, "hourlyUSD": 0.0960},
    ]
    path = tmp_path / "tumblebug-cost.json"
    path.write_text(json.dumps({"_note": "테스트", "specs": specs}), encoding="utf-8")
    dataset.clear_caches()
    top = filter_specs(2, 4, limit=5, architecture=None, output_dir=tmp_path)[0]
    assert top["region"] == "us-east-1"
    assert top["hourlyUSD"] == 0.0960


def test_folding_can_be_turned_off(tmp_path) -> None:
    out = _multi_region_specs(tmp_path)
    results = filter_specs(2, 4, limit=5, architecture=None,
                           fold_regions=False, output_dir=out)
    assert len(results) == 4
