"""`details` 파서 테스트 — 여기가 perfkb에서 유일하게 취약한 부분이다.

`details`의 값은 JSON이 아니라 Go `%v` 포맷이라 정규식으로 읽는다. 실제 덤프 문자열을
fixture로 박아 회귀를 고정한다(34MB 덤프 다운로드 불필요).

원칙: **못 읽으면 None**(fail-open). 단 중복 키의 값이 달라지는 건 상위 스키마가 바뀐
신호이므로 **크게 실패**시킨다 — 조용한 드리프트가 더 나쁘다.
"""

from __future__ import annotations

import json

import pytest

from app.core.cloudkb.perfkb.parsers.details import (
    DetailsMismatch,
    go_bool,
    go_field,
    go_number,
    is_burst_bandwidth,
    parse_details,
)

# 실제 덤프(v0.12.25)에서 그대로 가져온 문자열들.
EBS_INFO = (
    "{EbsOptimizedInfo:{BaselineBandwidthInMbps:347,BaselineIops:2000,"
    "BaselineThroughputInMBps:43.375,MaximumBandwidthInMbps:2085,MaximumIops:11800,"
    "MaximumThroughputInMBps:260.625},EbsOptimizedSupport:default,"
    "EncryptionSupport:supported,NvmeSupport:required}"
)
NETWORK_INFO = (
    "{DefaultNetworkCardIndex:0,EfaInfo:null,EfaSupported:false,EnaSupport:required,"
    "Ipv4AddressesPerInterface:6,Ipv6AddressesPerInterface:6,Ipv6Supported:true,"
    "MaximumNetworkCards:1,MaximumNetworkInterfaces:3,NetworkCards:[{MaximumNetworkInterfaces:3,"
    "NetworkCardIndex:0,NetworkPerformance:Up to 5 Gigabit}],NetworkPerformance:Up to 5 Gigabit}"
)
PROCESSOR_INFO = "{SupportedArchitectures:[x86_64],SustainedClockSpeedInGhz:2.5}"
VCPU_INFO = "{DefaultCores:1,DefaultThreadsPerCore:2,DefaultVCpus:2,ValidCores:[1],ValidThreadsPerCore:[1,2]}"


def _details(pairs: list[tuple[str, str]]) -> str:
    return json.dumps([{"key": k, "value": v} for k, v in pairs])


# --- parse_details ---


def test_parses_top_level_keys() -> None:
    raw = _details([("BurstablePerformanceSupported", "true"), ("InstanceType", "t3.medium")])
    assert parse_details(raw) == {
        "BurstablePerformanceSupported": "true",
        "InstanceType": "t3.medium",
    }


def test_duplicate_key_with_same_value_is_deduped() -> None:
    """Azure는 MaxDataDiskCount가 두 번 나온다 — 실측 34,846건 전부 값이 같다."""
    raw = _details([("MaxDataDiskCount", "8"), ("Name", "Standard_D2s_v3"), ("MaxDataDiskCount", "8")])
    assert parse_details(raw)["MaxDataDiskCount"] == "8"


def test_duplicate_key_with_different_value_fails_loudly() -> None:
    """값이 달라지면 상위 스키마가 바뀐 것 — 조용히 덮어쓰면 안 된다."""
    raw = _details([("MaxDataDiskCount", "8"), ("MaxDataDiskCount", "16")])
    with pytest.raises(DetailsMismatch, match="MaxDataDiskCount"):
        parse_details(raw)


@pytest.mark.parametrize("raw", [None, "", "not json", "{}", "[broken", '"a string"'])
def test_unreadable_details_yields_empty_not_crash(raw) -> None:
    """fail-open: 못 읽으면 성능 정보 없음으로 취급한다."""
    assert parse_details(raw) == {}


# --- go_field / go_number ---


def test_extracts_nested_number() -> None:
    assert go_number(EBS_INFO, "BaselineBandwidthInMbps") == 347
    assert go_number(EBS_INFO, "MaximumBandwidthInMbps") == 2085
    assert go_number(EBS_INFO, "BaselineIops") == 2000
    assert go_number(PROCESSOR_INFO, "SustainedClockSpeedInGhz") == 2.5
    assert go_number(VCPU_INFO, "DefaultThreadsPerCore") == 2


def test_extracts_value_containing_spaces() -> None:
    """'Up to 5 Gigabit' — 값에 공백이 있어 순진한 파서는 여기서 깨진다."""
    assert go_field(NETWORK_INFO, "NetworkPerformance") == "Up to 5 Gigabit"


def test_repeated_key_takes_the_last_top_level_one() -> None:
    """NetworkPerformance는 NetworkCards[] 안에 한 번, 최상위에 한 번 나온다."""
    blob = "{NetworkCards:[{NetworkPerformance:Up to 5 Gigabit}],NetworkPerformance:25 Gigabit}"
    assert go_field(blob, "NetworkPerformance") == "25 Gigabit"


def test_nested_object_value_is_not_extracted_as_scalar() -> None:
    """EbsOptimizedInfo의 값은 `{`로 시작하는 객체 — 스칼라로 뽑으면 안 된다."""
    assert go_field(EBS_INFO, "EbsOptimizedInfo") is None


def test_missing_key_and_non_numeric_yield_none() -> None:
    assert go_field(EBS_INFO, "NoSuchKey") is None
    assert go_number(EBS_INFO, "NoSuchKey") is None
    assert go_number(EBS_INFO, "EbsOptimizedSupport") is None  # 'default'는 숫자가 아니다
    assert go_field(None, "Anything") is None


def test_key_is_not_matched_as_suffix_of_another_key() -> None:
    """접미사 매치는 조용히 엉뚱한 값을 준다 — 'Iops'로 MaximumIops가 걸리면 안 된다."""
    assert go_number(EBS_INFO, "Iops") is None
    assert go_number(VCPU_INFO, "ThreadsPerCore") is None  # DefaultThreadsPerCore가 걸리면 안 됨
    # 정확한 키는 정상 동작해야 한다
    assert go_number(EBS_INFO, "BaselineIops") == 2000
    assert go_number(EBS_INFO, "MaximumIops") == 11800
    assert go_number(VCPU_INFO, "DefaultThreadsPerCore") == 2


# --- go_bool ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("false", False), ("True", True), ("FALSE", False),
     ("", None), (None, None), ("yes", None), ("1", None)],
)
def test_go_bool(raw, expected) -> None:
    assert go_bool(raw) is expected


# --- is_burst_bandwidth ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Up to 5 Gigabit", True),
        ("Up to 12.5 Gigabit", True),
        ("25 Gigabit", False),      # 실측상 이 두 형태뿐이다
        ("100 Gigabit", False),
        (None, None),
        ("", None),
    ],
)
def test_burst_bandwidth_detection(raw, expected) -> None:
    assert is_burst_bandwidth(raw) is expected


# --- 리전 접기 정책 (2026-07-21, 감사 §5-6 / P3) ---


def test_region_fold_is_conservative_not_first_wins() -> None:
    """리전마다 값이 갈리면 **가장 작은 값**을 쓰고 범위를 병기한다.

    예전엔 first-wins였는데 그건 정책이 아니라 **파일에 먼저 나온 리전을 쓰는
    우연**이었다 — 덤프 순서만 바뀌어도 같은 질문에 다른 답이 나온다. 실측상
    `aws c8gn.48xlarge`가 리전에 따라 IOPS 240,000 / 480,000이라, 순서에 따라
    **2배 과대 진술**이 가능했다.

    성능 KB에서는 과대 진술이 과소 진술보다 해롭다 — "이 정도는 난다"고 했다가
    안 나는 것이 "더 날 수도 있다"보다 나쁘다.
    """
    from app.core.cloudkb.perfkb.dataset import _fold_regions

    records = [
        {"provider": "aws", "specName": "c8gn.48xlarge", "region": "me-central-1",
         "ebsMaxIops": 480000.0, "currentGeneration": True},
        {"provider": "aws", "specName": "c8gn.48xlarge", "region": "us-east-1",
         "ebsMaxIops": 240000.0, "currentGeneration": True},
    ]
    folded = _fold_regions(records)
    assert folded["ebsMaxIops"] == 240000.0            # 보수적으로
    assert folded["ebsMaxIopsRange"] == [240000.0, 480000.0]  # 폭은 밝힌다

    # 순서를 뒤집어도 같은 답 — 이게 first-wins와의 차이다
    assert _fold_regions(list(reversed(records)))["ebsMaxIops"] == 240000.0


def test_region_fold_leaves_invariant_fields_alone() -> None:
    from app.core.cloudkb.perfkb.dataset import _fold_regions

    records = [
        {"provider": "aws", "specName": "t3.micro", "region": "us-east-1",
         "sustainedCpu": {"value": False}, "ebsMaxIops": 11800.0},
        {"provider": "aws", "specName": "t3.micro", "region": "eu-west-1",
         "sustainedCpu": {"value": False}, "ebsMaxIops": 11800.0},
    ]
    folded = _fold_regions(records)
    assert folded["sustainedCpu"] == {"value": False}
    assert "ebsMaxIopsRange" not in folded  # 값이 하나뿐이면 범위를 안 붙인다


def test_region_invariance_assumption_is_checked_not_assumed() -> None:
    """접기는 "경고 신호는 리전 불변"이라는 가정 위에 선다 — 매 빌드 확인한다.

    가정을 주석에 적어 두는 것과 검사하는 것은 다르다. 상류가 리전별로 다른 값을
    주기 시작하면 접기가 조용히 임의의 답을 낸다.
    """
    from app.core.cloudkb.kbcommon.invariants import run
    from app.core.cloudkb.perfkb.invariants import INVARIANTS

    ok = {"specs": [
        {"provider": "aws", "specName": "t3.micro", "sustainedCpu": {"value": False}},
        {"provider": "aws", "specName": "t3.micro", "sustainedCpu": {"value": False}},
    ]}
    assert run(ok, INVARIANTS).ok

    broken = {"specs": [
        {"provider": "aws", "specName": "t3.micro", "sustainedCpu": {"value": False}},
        {"provider": "aws", "specName": "t3.micro", "sustainedCpu": {"value": True}},
    ]}
    result = run(broken, INVARIANTS)
    assert not result.ok
    assert "리전마다" in result.summary()
