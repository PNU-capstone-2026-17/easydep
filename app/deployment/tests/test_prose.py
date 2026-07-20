"""산문 추출기 단위 테스트 (파서 없이 문자열 → 기대 출력).

모든 POSITIVE 입력은 실제 CloudFormation 스키마의 description을 **verbatim 복사**한
것이다. AWS가 문서를 바꾸면 fixture 갱신 시 이 테스트가 알려준다.
"""

from __future__ import annotations

import re

import pytest

from capacitykb import prose
from capacitykb.prose import extract_default, extract_enum, extract_ranges

# --- 실제 스키마에서 그대로 가져온 description ---

VOLUME_SIZE = (
    "The size of the volume, in GiBs.\n  +  Required for new empty volumes.\n  +  Optional"
    " for volumes created from snapshots and volume copies. In this case, the size defaults"
    " to the size of the snapshot or source volume. You can optionally specify a size that"
    " is equal to or larger than the size of the source snapshot or volume.\n  \n Supported"
    " volume sizes:\n  +  gp2: ``1 - 16,384`` GiB\n  +  gp3: ``1 - 65,536`` GiB\n  +  io1:"
    " ``4 - 16,384`` GiB\n  +  io2: ``4 - 65,536`` GiB\n  +  st1 and sc1: ``125 - 16,384``"
    " GiB\n  +  standard: ``1 - 1024`` GiB"
)

VOLUME_IOPS = (
    "The number of I/O operations per second (IOPS) to provision for the volume. Required"
    " for ``io1`` and ``io2`` volumes. Optional for ``gp3`` volumes. Omit for all other"
    " volume types. \n Valid ranges:\n  +  gp3: ``3,000``(*default*)``- 80,000`` IOPS\n  + "
    " io1: ``100 - 64,000`` IOPS\n  +  io2: ``100 - 256,000`` IOPS\n  \n  [Instances built"
    " on the Nitro System](https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-nitro-"
    "instances.html) can support up to 256,000 IOPS. Other instances can support up to"
    " 32,000 IOPS."
)

VOLUME_THROUGHPUT = (
    "The throughput to provision for a volume, with a maximum of 2,000 MiB/s.\n This"
    " parameter is valid only for ``gp3`` volumes. The default value is 125.\n Valid Range:"
    " Minimum value of 125. Maximum value of 2000.\n The maximum ratio of throughput to IOPS"
    " is 0.25 MiB/s per IOPS. For example, a volume with 3,000 IOPS can have a maximum"
    " throughput of 750 MiB/s (3,000 x 0.25)."
)

VOLUME_TYPE = (
    "The volume type. This parameter can be one of the following values:\n  +  General"
    " Purpose SSD: ``gp2`` | ``gp3``\n  +  Provisioned IOPS SSD: ``io1`` | ``io2``\n  + "
    " Throughput Optimized HDD: ``st1``\n  +  Cold HDD: ``sc1``\n  +  Magnetic:"
    " ``standard``\n  \n  Throughput Optimized HDD (``st1``) and Cold HDD (``sc1``) volumes"
    " can't be used as boot volumes.\n  For more information, see [Amazon EBS volume types]"
    "(https://docs.aws.amazon.com/ebs/latest/userguide/ebs-volume-types.html) in the *Amazon"
    " EBS User Guide*.\n Default: ``gp2``"
)

LAMBDA_TIMEOUT = (
    "The amount of time (in seconds) that Lambda allows a function to run before stopping"
    " it. The default is 3 seconds. The maximum allowed value is 900 seconds. For more"
    " information, see [Lambda execution environment](https://docs.aws.amazon.com/lambda/"
    "latest/dg/runtimes-context.html)."
)

LAMBDA_MEMORY = (
    "The amount of [memory available to the function](https://docs.aws.amazon.com/lambda/"
    "latest/dg/configuration-function-common.html#configuration-memory-console) at runtime."
    " Increasing the function memory also increases its CPU allocation. The default value is"
    " 128 MB. The value can be any multiple of 1 MB. Note that new AWS accounts have reduced"
    " concurrency and memory quotas. AWS raises these quotas automatically based on your"
    " usage. You can also request a quota increase."
)

EPHEMERAL_STORAGE = (
    "The size of the function's ``/tmp`` directory in MB. The default value is 512, but it"
    " can be any whole number between 512 and 10,240 MB."
)

RDS_IOPS = (
    "The number of I/O operations per second (IOPS) that the database provisions. The value"
    " must be equal to or greater than 1000. \n If you specify this property, you must"
    " follow the range of allowed ratios of your requested IOPS rate to the amount of"
    " storage that you allocate (IOPS to allocated storage). For example, you can provision"
    " an Oracle database instance with 1000 IOPS and 200 GiB of storage (a ratio of 5:1), or"
    " specify 2000 IOPS with 200 GiB of storage (a ratio of 10:1). For more information, see"
    " [Amazon RDS Provisioned IOPS Storage to Improve Performance](https://docs.aws.amazon."
    "com/AmazonRDS/latest/DeveloperGuide/CHAP_Storage.html#USER_PIOPS) in the *Amazon RDS"
    " User Guide*.\n  If you specify ``io1`` for the ``StorageType`` property, then you must"
    " also specify the ``Iops`` property.\n  Constraints:\n  +  For RDS for Db2, MariaDB,"
    " MySQL, Oracle, and PostgreSQL - Must be a multiple between .5 and 50 of the storage"
    " amount for the DB instance.\n  +  For RDS for SQL Server - Must be a multiple between"
    " 1 and 50 of the storage amount for the DB instance."
)

RDS_STORAGE_TYPE = (
    "The storage type to associate with the DB instance.\n If you specify ``io1``, ``io2``,"
    " or ``gp3``, you must also include a value for the ``Iops`` parameter.\n This setting"
    " doesn't apply to Amazon Aurora DB instances. Storage is managed by the DB cluster.\n"
    " Valid Values: ``gp2 | gp3 | io1 | io2 | standard``\n Default: ``io1``, if the ``Iops``"
    " parameter is specified. Otherwise, ``gp3``."
)

SQS_WAIT_TIME = (
    "Specifies the duration, in seconds, that the ReceiveMessage action call waits until a"
    " message is in the queue in order to include it in the response, rather than returning"
    " an empty response if a message isn't yet available. You can specify an integer from 1"
    " to 20. Short polling is used as the default or when you specify 0 for this property."
)

RDS_PROMOTION_TIER = (
    "The order of priority in which an Aurora Replica is promoted to the primary instance"
    " after a failure of the existing primary instance.\n This setting doesn't apply to RDS"
    " Custom DB instances.\n Default: ``1``\n Valid Values: ``0 - 15``"
)

REDSHIFT_MANUAL_SNAPSHOT = (
    "The number of days to retain newly copied snapshots in the destination AWS Region after"
    " they are copied from the source AWS Region. If the value is -1, the manual snapshot is"
    " retained indefinitely.\n\nThe value must be either -1 or an integer between 1 and"
    " 3,653."
)

S3_TIERING_DAYS = (
    "The number of consecutive days of no access after which an object will be eligible to"
    " be transitioned to the corresponding tier. The minimum number of days specified for"
    " Archive Access tier must be at least 90 days and Deep Archive Access tier must be at"
    " least 180 days. The maximum can be up to 2 years (730 days)."
)

APPSTREAM_MAX_LENGTH = (
    "Specifies the number of characters that can be copied by end users from the local"
    " device to the remote session, and to the local device from the remote session. This"
    " can be specified only for the CLIPBOARD_COPY_FROM_LOCAL_DEVICE and"
    " CLIPBOARD_COPY_TO_LOCAL_DEVICE actions. This defaults to 20,971,520 (20 MB) when"
    " unspecified and the permission is ENABLED. This can't be specified when the permission"
    " is DISABLED. The value can be between 1 and 20,971,520 (20 MB)."
)


def ranges_as_tuple(description: str) -> tuple:
    """(min, max, conditional) 로 축약 — 없으면 None."""
    found = {e.kind: e for e in extract_ranges(description)}
    lo = found["min"].value if "min" in found else None
    hi = found["max"].value if "max" in found else None
    conditional = any(e.conditional for e in found.values())
    return (lo, hi, conditional)


# --- POSITIVE: 잡아야 하는 것들 ---


@pytest.mark.parametrize(
    ("label", "description", "expected"),
    [
        # 조건부 6범위 → envelope
        ("volume_size", VOLUME_SIZE, (1, 65536, True)),
        # ``3,000``(*default*)``- 80,000`` 봉합 + "up to 256,000"은 무시
        ("volume_iops", VOLUME_IOPS, (100, 256000, True)),
        # 두 문장에 걸친 Valid Range (줄 단위 스코프여야 잡힘)
        ("volume_throughput", VOLUME_THROUGHPUT, (125, 2000, False)),
        # 단독 상한만
        ("lambda_timeout", LAMBDA_TIMEOUT, (None, 900, False)),
        # between + 콤마 10,240
        ("ephemeral_storage", EPHEMERAL_STORAGE, (512, 10240, False)),
        # from_to — 단, 0도 유효하다는 문구가 있어 하한은 기록하지 않는다 (아래 센티널 테스트)
        ("sqs_wait_time", SQS_WAIT_TIME, (None, 20, False)),
        # 백틱 범위 단일
        ("rds_promotion_tier", RDS_PROMOTION_TIER, (0, 15, False)),
        # 비율 오탐은 veto, 정상 하한만 남음
        ("rds_iops", RDS_IOPS, (1000, None, False)),
        # 티어별로 하한이 다름(90/180) → 가장 낮은 값으로 envelope + 조건부 표시
        ("s3_tiering_days", S3_TIERING_DAYS, (90, None, True)),
        # "DISABLED"라는 단어가 있어도 특수값 신호가 아니므로 하한을 유지해야 한다
        ("appstream_max_length", APPSTREAM_MAX_LENGTH, (1, 20971520, False)),
    ],
)
def test_extract_ranges_positive(label: str, description: str, expected: tuple) -> None:
    assert ranges_as_tuple(description) == expected


# --- fail-closed 방어: 범위 밖 특수값이 허용되는 경우 하한을 기록하지 않는다 ---


def test_sentinel_either_minus_one_drops_lower_bound() -> None:
    """'must be either -1 or an integer between 1 and 3,653' — -1도 유효하므로 min=1을 쓰면 안 된다."""
    found = {e.kind: e for e in extract_ranges(REDSHIFT_MANUAL_SNAPSHOT)}
    assert "min" not in found
    assert found["max"].value == 3653
    assert "특수값" in found["max"].note


def test_sentinel_specify_zero_drops_lower_bound() -> None:
    """'integer from 1 to 20' + 'when you specify 0' — 0도 유효하므로 min=1을 쓰면 안 된다."""
    found = {e.kind: e for e in extract_ranges(SQS_WAIT_TIME)}
    assert "min" not in found
    assert found["max"].value == 20


def test_conditional_envelope_has_a_note() -> None:
    found = {e.kind: e for e in extract_ranges(VOLUME_SIZE)}
    assert found["max"].conditional is True
    assert "gp3" in found["max"].note
    assert found["max"].unit == "GiB"


def test_single_range_keeps_the_rule() -> None:
    found = {e.kind: e for e in extract_ranges(VOLUME_THROUGHPUT)}
    assert found["min"].rule == "valid_range"


def test_unlabelled_rules_still_extract() -> None:
    found = {e.kind: e for e in extract_ranges(SQS_WAIT_TIME)}
    assert found["max"].rule == "from_to"


def test_max_allowed_rule() -> None:
    found = {e.kind: e for e in extract_ranges(LAMBDA_TIMEOUT)}
    assert found["max"].rule == "max_allowed"
    assert found["max"].unit == "seconds"


# --- NEGATIVE: 절대 잡히면 안 되는 것들 ---


@pytest.mark.parametrize(
    ("label", "description"),
    [
        ("ratio_multiple", "Must be a multiple between 1 and 50 of the storage amount."),
        ("ratio_maximum", "The maximum ratio of throughput to IOPS is 0.25 MiB/s per IOPS."),
        (
            "for_example",
            "For example, a volume with 3,000 IOPS can have a maximum throughput of 750 MiB/s.",
        ),
        (
            "instance_limit",
            "Instances can support up to 256,000 IOPS. Other instances up to 32,000 IOPS.",
        ),
        (
            "url_version",
            "See [docs](https://docs.aws.amazon.com/ec2/latest/v2/api-2016-11-15.html) for details.",
        ),
        ("increments", "If you increase the Iops value (in 1,000 IOPS increments), ..."),
        ("percent", "The sampling rate is a percentage between 1 and 100 percent."),
        ("no_numbers", "The name of the resource."),
    ],
)
def test_extract_ranges_negative(label: str, description: str) -> None:
    assert extract_ranges(description) == []


def test_throughput_does_not_pick_up_example_or_ratio_numbers() -> None:
    """750(예시)·0.25(비율)·2,000(문두 산문)이 아니라 Valid Range의 값이어야 한다."""
    found = {e.kind: e for e in extract_ranges(VOLUME_THROUGHPUT)}
    assert found["max"].value == 2000
    assert found["max"].value not in (750, 0.25)


def test_self_contradiction_discards_ranges_even_without_veto(monkeypatch) -> None:
    """R3는 veto와 독립 — veto를 꺼도 RDS.Iops의 비율 오탐이 걸러져야 한다."""
    monkeypatch.setattr(prose, "_VETO", re.compile(r"(?!x)x"))  # 아무것도 매칭 안 함
    assert extract_ranges(RDS_IOPS) == []


def test_disjoint_conditional_ranges_survive() -> None:
    """서로 떨어진 조건부 범위는 자기모순이 아니다 (R3가 과잉 차단하면 안 됨)."""
    text = "Sizes:\n  +  typeA: ``500 - 1,000`` GiB\n  +  typeB: ``2,000 - 5,000`` GiB"
    assert ranges_as_tuple(text) == (500, 5000, True)


# --- 숫자 토큰 회귀 ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Valid Range: Minimum value of 1. Maximum value of 1024.", 1024),
        ("Valid Range: Minimum value of 1. Maximum value of 65,536.", 65536),
        ("Valid Range: Minimum value of 1. Maximum value of 3,000.", 3000),
    ],
)
def test_num_token_comma_handling(text: str, expected: int) -> None:
    """콤마 그룹을 `*`로 쓰면 1024가 102로 잘린다 — 회귀 방어."""
    found = {e.kind: e for e in extract_ranges(text)}
    assert found["max"].value == expected


# --- default ---


def test_default_numeric() -> None:
    assert extract_default(LAMBDA_TIMEOUT).value == 3
    assert extract_default(LAMBDA_MEMORY).value == 128
    assert extract_default(VOLUME_THROUGHPUT).value == 125
    assert extract_default(RDS_PROMOTION_TIER).value == 1


def test_default_numeric_survives_veto_words() -> None:
    """veto는 범위에만 적용된다 — MemorySize의 'multiple' 때문에 기본값을 잃으면 안 된다."""
    assert extract_default(LAMBDA_MEMORY).value == 128
    assert extract_ranges(LAMBDA_MEMORY) == []


def test_default_string() -> None:
    assert extract_default(VOLUME_TYPE, numeric=False).value == "gp2"


def test_ambiguous_conditional_default_skipped() -> None:
    """'Default: ``io1``, if ... Otherwise, ``gp3``' 처럼 조건부면 뽑지 않는다."""
    assert extract_default(RDS_STORAGE_TYPE, numeric=False) is None


def test_no_default() -> None:
    assert extract_default("The name of the resource.") is None


# --- enum ---


def test_enum_from_explicit_valid_values() -> None:
    found = extract_enum(RDS_STORAGE_TYPE)
    assert found.value == ["gp2", "gp3", "io1", "io2", "standard"]


def test_enum_not_extracted_from_bullets() -> None:
    """Volume.VolumeType 불릿 합집합은 st1/sc1/standard를 놓쳐 fail-closed가 된다 → 금지."""
    assert extract_enum(VOLUME_TYPE) is None


def test_enum_ignores_range_valid_values() -> None:
    """'Valid Values: ``0 - 15``' 는 범위지 enum이 아니다."""
    assert extract_enum(RDS_PROMOTION_TIER) is None
