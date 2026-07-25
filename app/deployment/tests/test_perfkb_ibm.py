"""IBM Global Catalog → 성능 신호. perfkb의 일곱 공백 중 유일하게 메울 수 있던 하나.

여기 테스트가 지키는 것은 **담은 것이 아니라 담지 않기로 한 것**이다.

    freqency 310건 전부 2000  → 담으면 모든 IBM 스펙에 "2.0 GHz"라는 확신에 찬 오답
    sustainedCpu 원본에 없음  → dedicated를 보고 "상시 CPU 보장"이라 적으면 우리 추론
                                 그러면 침묵이 "확인됨"으로 읽힌다(결함 C4와 같은 모양)
"""

from __future__ import annotations

import json

import pytest

from perfkb import agent_api, dataset
from perfkb.parsers.ibm_catalog import DROPPED_CONSTANT_FIELDS, to_record

PROFILE = {
    "name": "bx2-16x64",
    "kind": "instance.profile",
    "metadata": {"other": {"profile": {
        "family": "balanced",
        "default_config": {
            "cpu": 16, "ram": 64,
            "freqency": 2000,                 # 원본 철자. 전부 같은 값
            "status": "current",              # 전부 같은 값
            "vcpu_architecture": "amd64",     # 전부 같은 값
            "bandwidth": 32000,
            "port_speed": 25000,
            "max_nics": 5,
            "numa_count": 2,
            "provisioning_timeout_seconds": 600,
            "vcpu_manufacturer": "Intel",
            "vcpu_tenancy": {"default": "dedicated", "values": ["dedicated"]},
        },
    }}},
}


def _one(profile=PROFILE, regions=("us-south",)) -> dict:
    return to_record(profile, list(regions))[0]


# --- 담지 않기로 한 것 ---------------------------------------------------------

def test_constant_clock_is_not_carried() -> None:
    """**310건 전부 2000이다.** 담았으면 모든 IBM 스펙이 "2.0 GHz"로 나왔을 것이고,
    실제 클럭은 세대마다 다른데 우리는 그걸 모른다.
    """
    rec = _one()
    assert "clockGHz" not in rec and "cpuClockMHz" not in rec
    assert "freqency" in DROPPED_CONSTANT_FIELDS


def test_other_single_valued_fields_are_not_carried() -> None:
    rec = _one()
    for key in ("status", "vcpu_architecture", "os_architecture"):
        assert key in DROPPED_CONSTANT_FIELDS
    assert "currentGeneration" not in rec  # status=current를 세대로 옮기지 않는다


def test_tenancy_is_kept_raw_and_not_turned_into_sustained_cpu() -> None:
    """`dedicated`를 보고 "상시 CPU 보장"이라 적는 것은 **우리 추론**이지
    원본이 한 말이 아니다. 원본 표기 그대로 담는다.
    """
    rec = _one()
    assert rec["vcpuTenancy"] == "dedicated"
    assert "sustainedCpu" not in rec


# --- 담은 것 -------------------------------------------------------------------

def test_discriminating_signals_are_carried() -> None:
    rec = _one()
    assert rec["networkBandwidthMbps"] == 32000
    assert rec["portSpeedMbps"] == 25000
    assert rec["maxNics"] == 5
    assert rec["numaCount"] == 2
    assert rec["cpuVendor"] == "Intel"
    assert rec["provisioningTimeoutSeconds"] == 600


def test_id_matches_the_costkb_join_key() -> None:
    """조인의 진입점이다 — 형식이 어긋나면 성능 경고가 통째로 사라진다(결함 C3)."""
    assert _one()["id"] == "ibm+us-south+bx2-16x64"


def test_one_record_per_region() -> None:
    assert len(to_record(PROFILE, ["us-south", "jp-tok"])) == 2


# --- 침묵을 "확인됨"으로 읽게 하지 않는다 --------------------------------------

@pytest.fixture()
def built(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    source = [{"source": "t", "pin_kind": "digest", "pin": "(고정 불가)",
               "sha256": "0" * 64}]
    (out / dataset.BUILT_FILENAME).write_text(json.dumps({
        "_note": "미러",
        "specs": [{"id": "aws+us-east-1+m5.large", "provider": "aws",
                   "specName": "m5.large", "currentGeneration": True}],
        "_source": source,
    }, ensure_ascii=False), encoding="utf-8")
    (out / dataset.EXTRA_FILENAMES[0]).write_text(json.dumps({
        "_note": "IBM 보강",
        "specs": [_one()],
        "_source": source,
    }, ensure_ascii=False), encoding="utf-8")
    dataset.clear_caches()
    yield out
    dataset.clear_caches()


def test_extra_file_is_merged_into_the_index(built) -> None:
    assert dataset.get_by_id("ibm+us-south+bx2-16x64", built) is not None
    assert "ibm" in dataset.tracked_providers(built)


def test_missing_extra_file_does_not_break_the_rest(tmp_path) -> None:
    """**없는 것이 기본이다**(재배포 허가 미확인). 없다고 나머지가 못 돌면 안 된다."""
    out = tmp_path / "output"
    out.mkdir()
    (out / dataset.BUILT_FILENAME).write_text(json.dumps({
        "_note": "미러",
        "specs": [{"id": "aws+us-east-1+m5.large", "provider": "aws",
                   "specName": "m5.large"}],
        "_source": [{"source": "t", "pin_kind": "digest", "pin": "x",
                     "sha256": "0" * 64}],
    }, ensure_ascii=False), encoding="utf-8")
    dataset.clear_caches()
    try:
        assert dataset.is_built(out)
        assert dataset.tracked_providers(out) == frozenset({"aws"})
    finally:
        dataset.clear_caches()


def test_record_without_burst_signals_is_partial_not_ok(built) -> None:
    """**가장 중요한 계약.** `ok`로 돌려주면 비용 도구의 꼬리말
    "경고가 없는 후보는 성능 확인됨(상시 CPU 보장·최신 세대)"이 거짓말이 된다.
    """
    note = agent_api.recommend_note("ibm", "bx2-16x64", output_dir=built)
    assert note.status == agent_api.NOTE_PARTIAL
    assert "Cannot judge burst or generation" in " ".join((note.text or "").split())


def test_record_with_signals_is_still_ok(built) -> None:
    note = agent_api.recommend_note("aws", "m5.large", output_dir=built)
    assert note.status == agent_api.NOTE_OK


def test_committed_artifact_discloses_its_redistribution_status() -> None:
    """저장소에 넣기로 한 이상 **파일 하나만 떼어 봐도** 출처와 허가 상태가 보여야 한다."""
    from kbcommon import artifact

    packed = artifact.BUNDLED_DIR / f"{dataset.EXTRA_FILENAMES[0]}.gz"
    if not packed.exists():
        pytest.skip("아직 빌드·포장하지 않은 환경")
    note = artifact.load_json(packed).get("_note") or ""
    assert "재배포" in note and "찾지 못했습니다" in note
    assert "허가를 받았다는 뜻이 아닙니다" in note
