"""리전별 탄소 (GCP 발표 + Cloud Carbon Footprint 추정).

여기서 지켜야 하는 것:
- **두 방법론을 섞지 않는다.** 같은 도시에서 값이 다르고 순서까지 뒤집히므로
  (실측: 서울은 gcp 최저, 도쿄는 aws 최저) 프로바이더 간 비교를 막는다.
- **참조를 못 풀면 담지 않고 센다.** CCF 계수 표가 NERC 상수를 참조한다.
- **단위를 맞춘다.** CCF는 메트릭톤/kWh, GCP는 g/kWh다.
"""

from __future__ import annotations

import json

import pytest

from envkb import carbon


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


CORE_TS = """
export const US_NERC_REGIONS_EMISSIONS_FACTORS: { [k: string]: number } = {
  RFC: 0.0003761283186,
  SERC: 0.0003651277965,
}
"""

AWS_REGIONS_TS = """
export enum AWS_REGIONS {
  US_EAST_1 = 'us-east-1',
  AP_NORTHEAST_2 = 'ap-northeast-2',
}
"""

AWS_FACTORS_TS = """
export const AWS_EMISSIONS_FACTORS_METRIC_TON_PER_KWH = {
  [AWS_REGIONS.US_EAST_1]: US_NERC_REGIONS_EMISSIONS_FACTORS.SERC,
  [AWS_REGIONS.AP_NORTHEAST_2]: 0.00047739,
  [AWS_REGIONS.UNKNOWN_9]: 0.001,
  [AWS_REGIONS.US_EAST_1_ALT]: US_NERC_REGIONS_EMISSIONS_FACTORS.MISSING,
}
"""

AZURE_REGIONS_TS = """
export const AZURE_REGIONS = {
  AP_KOREA_CENTRAL: { name: 'koreacentral', options: ['koreacentral'] },
  US_CENTRAL: { name: 'centralus', options: ['centralus'] },
}
"""

AZURE_FACTORS_TS = """
export const AZURE_EMISSIONS_FACTORS_METRIC_TON_PER_KWH = {
  [AZURE_REGIONS.AP_KOREA_CENTRAL.name]: 0.0004156,
  [AZURE_REGIONS.US_CENTRAL.name]: US_NERC_REGIONS_EMISSIONS_FACTORS.RFC,
}
"""

FILES = {
    "core": CORE_TS,
    "aws_regions": AWS_REGIONS_TS,
    "aws_factors": AWS_FACTORS_TS,
    "azure_regions": AZURE_REGIONS_TS,
    "azure_factors": AZURE_FACTORS_TS,
}

GCP_ROWS = [
    {
        "Google Cloud Region": "asia-northeast3", "Location": "Seoul",
        "Google CFE": "0.37", "Grid carbon intensity (gCO2eq / kWh)": "356.57",
    },
    {
        "Google Cloud Region": "europe-north1", "Location": "Finland",
        "Google CFE": "0.98", "Grid carbon intensity (gCO2eq / kWh)": "39.28",
    },
]


def test_nerc_reference_is_resolved() -> None:
    """`US_NERC_REGIONS_EMISSIONS_FACTORS.SERC`를 숫자로 풀어야 한다."""
    records, _ = carbon.parse_ccf(FILES)
    us = next(r for r in records if r["region"] == "us-east-1")
    assert us["gramsPerKWh"] == pytest.approx(365.1278, abs=0.01)


def test_unit_is_converted_to_grams() -> None:
    """CCF는 메트릭톤/kWh다. 0.00047739 t → 477.39 g."""
    records, _ = carbon.parse_ccf(FILES)
    seoul = next(r for r in records if r["region"] == "ap-northeast-2")
    assert seoul["gramsPerKWh"] == pytest.approx(477.39, abs=0.01)


def test_unresolvable_entries_are_counted_not_kept() -> None:
    """리전 이름을 못 찾거나 참조가 없으면 담지 않고 센다."""
    records, report = carbon.parse_ccf(FILES)
    codes = {r["region"] for r in records}
    assert "us-east-1" in codes and "ap-northeast-2" in codes
    assert len(report.unresolved) == 2  # UNKNOWN_9, US_EAST_1_ALT


def test_azure_name_form_is_parsed() -> None:
    """Azure는 `{ name: 'koreacentral' }` 꼴이라 형태가 다르다."""
    records, _ = carbon.parse_ccf(FILES)
    azure = {r["region"] for r in records if r["provider"] == "azure"}
    assert azure == {"koreacentral", "centralus"}


def test_gcp_carries_carbon_free_energy() -> None:
    """CFE는 GCP만 주는 축이다. CCF 레코드엔 없어야 한다."""
    gcp = carbon.parse_gcp(GCP_ROWS)
    assert gcp[0]["carbonFreeEnergy"] == 0.37
    assert gcp[0]["method"] == "provider-published"
    ccf, _ = carbon.parse_ccf(FILES)
    assert all(r["carbonFreeEnergy"] is None for r in ccf)
    assert all(r["method"] == "grid-estimate" for r in ccf)


@pytest.fixture
def built(tmp_path):
    records = carbon.parse_gcp(GCP_ROWS) + carbon.parse_ccf(FILES)[0]
    (tmp_path / "region-carbon.json").write_text(
        json.dumps({"regions": records, "_source": []}), encoding="utf-8"
    )
    carbon._load.cache_clear()
    yield tmp_path
    carbon._load.cache_clear()


def test_cross_provider_comparison_is_blocked(built) -> None:
    """방법론이 다른 값을 비교로 읽지 않도록 고지가 반드시 붙는다."""
    for text in (
        carbon.describe("gcp", output_dir=str(built)),
        carbon.describe("aws", "ap-northeast-2", output_dir=str(built)),
    ):
        assert "**Do not compare providers against each other.**" in flat(text)


def test_method_is_named_in_the_answer(built) -> None:
    gcp = carbon.describe("gcp", "asia-northeast3", output_dir=str(built))
    aws = carbon.describe("aws", "ap-northeast-2", output_dir=str(built))
    # 방법론 이름은 고지문에도 비슷한 말이 나오므로 `Basis:` 줄로 잡는다.
    assert "Basis: published by the provider" in flat(gcp)
    assert "Basis: estimated from public grid data" in flat(aws)


def test_cleanest_is_sorted(built) -> None:
    rows = carbon.cleanest("gcp", output_dir=str(built))
    assert [r["region"] for r in rows] == ["europe-north1", "asia-northeast3"]


def test_untracked_provider_is_distinguished(built) -> None:
    """'탄소 없음'이 아니라 '이 축을 추적 안 함'이다."""
    text = carbon.describe("tencent", output_dir=str(built))
    assert "**we do not track this axis**" in flat(text)


def test_missing_region_is_not_called_clean(built) -> None:
    text = carbon.describe("aws", "nonexistent-1", output_dir=str(built))
    assert "does not mean the region is clean" in flat(text)


def test_missing_artifact_guides_to_build(tmp_path) -> None:
    carbon._load.cache_clear()
    assert "build-carbon" in carbon.describe("gcp", output_dir=str(tmp_path))
    carbon._load.cache_clear()
