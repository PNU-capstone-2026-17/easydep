"""GCP 관리형 과금 축 (보강 3) — objectStorage 하나뿐이고, 그것이 소스의 전부다.

여기 테스트가 고정하는 것:

    수록 한 종의 정직성    objectStorage 외 아키타입을 지어내지 않는다
    두 축이 함께 간다      콜드 클래스의 검색 요금을 떼면 저장 단가가 거짓말이 된다
    리전 체계 순수성       멀티/듀얼 리전(asia1 등)이 단일 리전 축에 섞이지 않는다
    미빌드/없음 구분       None(안 봤다)과 [](봤는데 없다)은 뜻이 반대다
"""

from __future__ import annotations

from costkb.dataset import DEFAULT_OUTPUT_DIR, managed_axes


def _gcs_axes() -> list[dict]:
    axes = managed_axes("objectStorage", "asia-northeast3", output_dir=DEFAULT_OUTPUT_DIR)
    assert axes, "gcp-managed-pricing이 output/에도 data/에도 없다"
    return axes


def test_storage_is_capacity_rate_not_hourly() -> None:
    """GB/월 단가는 용량-비례형이다 — 시간당 단가로 읽히면 곱할 수량(용량)이
    사이징 결과라는 사실이 사라진다."""
    storage = [a for a in _gcs_axes() if a["meter"] == "storage"]
    assert storage
    assert all(a["axis"] == "capacityRate" and a["unit"] == "GB/월" for a in storage)


def test_cold_classes_carry_their_retrieval_fee() -> None:
    """archive($0.0025/GB/월)가 standard($0.023)보다 싸 보이는 것으로 답이 끝나면
    안 된다 — 검색 요금(사용량형)이 함께 실려야 한다."""
    axes = _gcs_axes()
    by_sku = {}
    for a in axes:
        by_sku.setdefault(a["sku"], set()).add(a["meter"])
    for cold in ("nearline", "coldline", "archiv"):
        assert by_sku.get(cold) == {"storage", "retrieval"}, cold
    retrievals = [a for a in axes if a["meter"] == "retrieval"]
    assert all(a["axis"] == "usage" for a in retrievals)


def test_standard_has_no_retrieval_axis() -> None:
    """standard에 검색 축이 없는 것은 원본에 없어서다 — 지어내지 않는다."""
    assert not [
        a for a in _gcs_axes() if a["sku"] == "standard" and a["meter"] == "retrieval"
    ]


def test_no_multi_region_location_leaks_into_the_region_axis() -> None:
    """멀티/듀얼 리전(asia1·asia-multi)은 리전 체계가 다르다 — 섞이면 리전 칸이
    두 체계의 잡탕이 된다."""
    from kbcommon import artifact

    path = artifact.resolve("output", "gcp-managed-pricing.json")
    data = artifact.load_json(path)
    regions = {r["region"] for r in data["records"]}
    assert not [r for r in regions if r.endswith(("-multi", "1")) and "-" not in r]
    assert "asia-multi" not in regions and "asia1" not in regions


def test_only_object_storage_is_claimed() -> None:
    """소스에 있는 것이 objectStorage뿐이다 — 다른 아키타입이 나타나면 그건
    지어낸 것이다."""
    from kbcommon import artifact

    path = artifact.resolve("output", "gcp-managed-pricing.json")
    data = artifact.load_json(path)
    assert {r["archetype"] for r in data["records"]} == {"objectStorage"}


def test_azure_and_gcp_regions_do_not_collide() -> None:
    """한 맵으로 합치는 전제 — 두 리전 체계가 겹치지 않는다."""
    azure = managed_axes("keyValueCache", "koreasouth", output_dir=DEFAULT_OUTPUT_DIR)
    gcp = _gcs_axes()
    assert azure is not None
    assert {a["region"] for a in gcp} & {r["region"] for r in (azure or [])} == set()


def test_unbuilt_is_none_not_empty(tmp_path) -> None:
    from costkb import dataset

    dataset.clear_caches()
    try:
        assert managed_axes("objectStorage", "asia-northeast3", output_dir=tmp_path) is None
    finally:
        dataset.clear_caches()
