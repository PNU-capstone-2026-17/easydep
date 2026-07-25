"""성능 필드 목록이 **한 곳**이라는 것을 고정한다.

같은 목록이 세 군데(프로파일·비교·CLI)에 따로 있어서 갈라졌고, 그 결과가 실측으로
나왔다:

    perf_compare(aws, [m5.large, c6a.large])  → 세대 이야기 없음
    cost_recommend_specs                      → "구세대 인스턴스입니다"

**두 도구가 같은 사실에 다른 그림을 줬다.** 여기 테스트는 그 상충과, 목록이 AWS 위주로
자라서 azure·gcp의 100% 채워진 칸이 통째로 빠졌던 것을 함께 잡는다.
"""

from __future__ import annotations

import json
from pathlib import Path

#: 저장소 기준 경로. CWD 기준으로 열면 easydep 루트에서 돌 때 파일을 못 찾고,
#: exists() 가드가 있는 곳은 실패 대신 **조용히 스킵**된다(병합 때 실제로 그랬다).
_ROOT = Path(__file__).resolve().parent.parent

import pytest

from app.deployment.perfkb import dataset
from app.deployment.perfkb.agent_api import compare, instance_profile, recommend_note
from app.deployment.perfkb.fields import COMPARE_FIELDS, FIELDS

# 레코드를 식별하거나, 다른 칸의 주석으로 쓰이거나, 별도 블록에서 다루는 것들.
# **여기 없고 FIELDS에도 없으면 그 칸은 아무 도구에도 안 보인다** — 그게 결함이었다.
_NOT_DISPLAY_FIELDS = {
    "id", "provider", "specName",
    "sustainedCpu",       # 근거·note가 있어 전용 처리
    "networkIsBurst",     # networkPerformance 줄에 붙는 주석
    # 하드웨어 블록 — 소스가 달라 따로 묶는다
    "cpuVendor", "cpuModel", "cpuClockMHz", "cpuCacheKB", "cpuCores", "cpuThreads",
    "memorySpeedMHz", "gpuCount", "gpuModel", "gpuArchitecture",
    "hardwareCheckedAt", "hardwareEvidence",
    # 보강 소스별 근거 메타 칸 — hardwareEvidence와 같은 규약(값 칸이 아니다)
    "azureSizesEvidence", "gcpSeriesEvidence",
}

AWS_OLD = {
    "id": "aws+us-east-1+m5.large", "provider": "aws", "specName": "m5.large",
    "sustainedCpu": {"value": True, "evidence": "aws-non-burstable-inferred",
                     "basis": "inferred"},
    "currentGeneration": False, "clockGHz": 3.1, "threadsPerCore": 2,
    "bareMetal": False, "ebsBaselineMbps": 650.0, "ebsBaselineIops": 3600.0,
}
AWS_NEW = {**AWS_OLD, "id": "aws+us-east-1+c6a.large", "specName": "c6a.large",
           "currentGeneration": True, "clockGHz": 3.6}
GCP = {
    "id": "gcp+us-central1+n2-highmem-8", "provider": "gcp", "specName": "n2-highmem-8",
    "sustainedCpu": {"value": True, "evidence": "gcp-dedicated-cpu-inferred",
                     "basis": "inferred"},
    "maxPersistentDisks": 128.0, "maxPersistentDiskGB": 263168.0,
    "vendorDescription": "8 vCPUs 64 GB RAM",
}
GCP_SHARED = {
    "id": "gcp+us-central1+e2-medium", "provider": "gcp", "specName": "e2-medium",
    "sustainedCpu": {"value": False, "evidence": "gcp-shared-cpu-field", "basis": "stated",
                     "note": "공유 코어 — 성능이 일정하지 않습니다."},
    "maxPersistentDisks": 128.0, "maxPersistentDiskGB": 263168.0,
}
AZURE = {
    "id": "azure+koreacentral+Standard_D2s_v5", "provider": "azure",
    "specName": "Standard_D2s_v5",
    "sustainedCpu": {"value": True, "evidence": "azure-family-name", "basis": "inferred"},
    "threadsPerCore": 2.0, "diskIops": 3750.0, "cachedDiskIops": 9000.0,
    "acceleratedNetworking": True, "premiumIO": True, "family": "standardDSv5Family",
}


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-perf.json").write_text(
        json.dumps({"_note": "테스트",
                    "specs": [AWS_OLD, AWS_NEW, GCP, GCP_SHARED, AZURE],
                    "_source": []}),
        encoding="utf-8",
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


# --- 상충 회귀 -------------------------------------------------------------

def test_compare_says_old_generation(built) -> None:
    """**핵심 회귀.** 세대는 aws에서 100% 채워진 칸인데 비교 축 목록에만 빠져 있었다."""
    flat = " ".join(compare("aws", ["m5.large", "c6a.large"], output_dir=built).split())
    assert "previous generation" in flat and "current generation" in flat


def test_compare_and_recommend_agree_word_for_word(built) -> None:
    """판정 문구를 공유한다 — 두 도구가 다른 말을 하면 어느 쪽을 믿을지 알 수 없다."""
    note = recommend_note("aws", "m5.large", output_dir=built)
    assert note.text and note.text in compare("aws", ["m5.large", "c6a.large"], output_dir=built)


def test_compare_warns_only_about_the_spec_that_has_it(built) -> None:
    text = compare("gcp", ["n2-highmem-8", "e2-medium"], output_dir=built)
    assert "⚠ e2-medium:" in text
    assert "⚠ n2-highmem-8:" not in text


# --- 프로파일 비대칭 회귀 --------------------------------------------------

def test_gcp_profile_is_not_one_line(built) -> None:
    """gcp는 100% 채워진 칸 3개를 갖고도 프로파일이 상시 CPU 한 줄뿐이었다."""
    flat = " ".join(instance_profile("gcp", "n2-highmem-8", output_dir=built).split())
    assert "max persistent disks" in flat
    assert "vendor description" in flat  # CLI만 출력하고 에이전트는 못 보던 칸


def test_azure_profile_shows_its_own_axes(built) -> None:
    flat = " ".join(instance_profile("azure", "Standard_D2s_v5", output_dir=built).split())
    for label in ("cached disk IOPS", "accelerated networking", "premium IO", "family"):
        assert label in flat


# --- 표시 규약 -------------------------------------------------------------

def test_booleans_are_not_raw_python(built) -> None:
    """`최신 세대: False`는 파이썬을 읽는 사람에게만 뜻이 통한다."""
    flat = " ".join(instance_profile("aws", "m5.large", output_dir=built).split())
    assert "False" not in flat and "True" not in flat
    assert "generation: previous generation" in flat


def test_counts_have_no_decimal_point(built) -> None:
    """`128.0개 디스크`는 값 자체를 의심스럽게 만든다."""
    text = instance_profile("gcp", "n2-highmem-8", output_dir=built)
    assert "128" in text and "128.0" not in text


def test_burst_axis_is_labelled_as_burst() -> None:
    """최대 대역폭을 숨기면 데이터를 버리는 것이고, 그냥 '최대'면 지속으로 읽힌다."""
    for key in ("ebsMaxMbps", "ebsMaxIops"):
        field = next(f for f in FIELDS if f.key == key)
        assert "burst" in field.label


# --- 구조 불변식 -----------------------------------------------------------

def test_compare_fields_are_a_subset_of_fields() -> None:
    assert set(COMPARE_FIELDS) <= set(FIELDS)


def test_field_keys_are_unique() -> None:
    keys = [f.key for f in FIELDS]
    assert len(keys) == len(set(keys))


def test_every_schema_field_is_shown_or_deliberately_excluded() -> None:
    """**다음 누락을 여기서 막는다.**

    스키마에 칸을 더하고 표시 목록에 안 넣으면 그 데이터는 어느 도구에도 안 보인다.
    빠뜨리려면 `_NOT_DISPLAY_FIELDS`에 이유와 함께 적어야 한다.
    """
    schema = json.load(open(_ROOT / "perfkb/schema.json", encoding="utf-8"))
    declared = {f.key for f in FIELDS} | _NOT_DISPLAY_FIELDS
    missing = set(schema["$defs"]["spec"]["properties"]) - declared
    assert not missing, f"표시되지 않는 칸: {sorted(missing)}"


def test_excluded_fields_still_exist_in_schema() -> None:
    """제외 목록이 스키마 변경 뒤에 남아 유령이 되지 않도록."""
    schema = json.load(open(_ROOT / "perfkb/schema.json", encoding="utf-8"))
    props = set(schema["$defs"]["spec"]["properties"])
    assert props >= _NOT_DISPLAY_FIELDS
    assert {f.key for f in FIELDS} <= props
