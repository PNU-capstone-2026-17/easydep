"""CFN 제약 파서 테스트.

골든 케이스를 셋으로 **분리**한다:
1. machine-readable — 산문 정규식을 어떻게 고쳐도 절대 깨지면 안 되는 것
2. 산문 — 추출기가 잡아야 하는 것
3. negative — **절대 레코드가 생기면 안 되는 것** (실은 이쪽이 가장 중요:
   잘못된 제약은 유효한 배포를 거부하게 만들어 침묵보다 나쁘다)
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app.core.cloudkb.capacitykb import prose
from app.core.cloudkb.capacitykb.model import CapacitySet, Constraint
from app.core.cloudkb.capacitykb.parsers.cfn import parse_schemas

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "capacity" / "cfn"

VOLUME = "aws::AWS::EC2::Volume"
LAMBDA = "aws::AWS::Lambda::Function"
RDS = "aws::AWS::RDS::DBInstance"
SUBNET = "aws::AWS::EC2::Subnet"
SQS = "aws::AWS::SQS::Queue"


def load_schemas() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURE_DIR.glob("*.json"))
    ]


@pytest.fixture(scope="module")
def capacity() -> CapacitySet:
    return parse_schemas(load_schemas(), prose=True)


def find(capacity: CapacitySet, type_id: str, prop: str, kind: str) -> Constraint | None:
    return capacity.get_constraint(type_id, prop, kind)


# ---------------------------------------------------------------- 1. machine-readable


def test_golden_lambda_timeout_min_from_schema(capacity: CapacitySet) -> None:
    found = find(capacity, LAMBDA, "Timeout", "min")
    assert found.value == 1
    assert found.evidence == "cfn-schema"
    assert found.basis == "stated"


def test_golden_ephemeral_storage_size_from_definition(capacity: CapacitySet) -> None:
    """산문은 $ref 래퍼에 있지만 실제 제약은 definitions 아래에 이미 있다."""
    assert find(capacity, LAMBDA, "EphemeralStorage/Size", "min").value == 512
    assert find(capacity, LAMBDA, "EphemeralStorage/Size", "max").value == 10240
    assert find(capacity, LAMBDA, "EphemeralStorage/Size", "max").evidence == "cfn-schema"


def test_golden_subnet_required_and_immutable(capacity: CapacitySet) -> None:
    """변경 제약: VpcId는 필수이며 바꾸면 리소스가 재생성된다."""
    assert find(capacity, SUBNET, "VpcId", "required").value is True
    mutability = find(capacity, SUBNET, "VpcId", "mutability")
    assert mutability.value == "create_only"
    assert mutability.evidence == "cfn-schema"
    assert mutability.basis == "stated"
    assert find(capacity, SUBNET, "CidrBlock", "mutability").value == "create_only"


def test_golden_subnet_conditional_create_only(capacity: CapacitySet) -> None:
    assert (
        find(capacity, SUBNET, "Ipv6CidrBlock", "mutability").value
        == "conditional_create_only"
    )


def test_golden_subnet_read_only(capacity: CapacitySet) -> None:
    assert find(capacity, SUBNET, "SubnetId", "mutability").value == "read_only"


def test_golden_rds_promotion_tier_schema_min(capacity: CapacitySet) -> None:
    found = find(capacity, RDS, "PromotionTier", "min")
    assert found.value == 0
    assert found.evidence == "cfn-schema"


def test_pattern_and_enum_from_schema(capacity: CapacitySet) -> None:
    assert find(capacity, RDS, "Port", "pattern").value == "^\\d*$"


# ---------------------------------------------------------------- 2. 산문


def test_prose_lambda_timeout_max(capacity: CapacitySet) -> None:
    """min은 스키마(1.0), max는 산문(0.8) — narrow 모델이라 근거가 따로 유지된다."""
    found = find(capacity, LAMBDA, "Timeout", "max")
    assert found.value == 900
    assert found.evidence == "cfn-description"
    assert found.basis == "inferred"
    assert found.unit == "seconds"


def test_prose_volume_throughput_range(capacity: CapacitySet) -> None:
    assert find(capacity, VOLUME, "Throughput", "min").value == 125
    assert find(capacity, VOLUME, "Throughput", "max").value == 2000
    assert find(capacity, VOLUME, "Throughput", "max").basis == "inferred"


def test_prose_volume_size_conditional_envelope(capacity: CapacitySet) -> None:
    found = find(capacity, VOLUME, "Size", "max")
    assert found.value == 65536
    assert found.conditional is True
    assert found.basis == "inferred"
    assert found.unit == "GiB"
    assert "gp3" in found.note


def test_prose_volume_iops_splice(capacity: CapacitySet) -> None:
    """``3,000``(*default*)``- 80,000`` 봉합 + 'up to 256,000'(인스턴스 한도)은 무시."""
    assert find(capacity, VOLUME, "Iops", "min").value == 100
    assert find(capacity, VOLUME, "Iops", "max").value == 256000


def test_prose_defaults(capacity: CapacitySet) -> None:
    assert find(capacity, LAMBDA, "MemorySize", "default").value == 128
    assert find(capacity, VOLUME, "VolumeType", "default").value == "gp2"


def test_prose_rds_storage_type_enum(capacity: CapacitySet) -> None:
    found = find(capacity, RDS, "StorageType", "enum")
    assert found.value == ["gp2", "gp3", "io1", "io2", "standard"]
    assert found.evidence == "cfn-description"


def test_prose_rds_iops_min_only(capacity: CapacitySet) -> None:
    assert find(capacity, RDS, "Iops", "min").value == 1000


# ---------------------------------------------------------------- 3. negative


def test_negative_rds_iops_has_no_max(capacity: CapacitySet) -> None:
    """'Must be a multiple between 1 and 50'은 비율이지 범위가 아니다 → max=50 금지."""
    assert find(capacity, RDS, "Iops", "max") is None


def test_negative_volume_type_has_no_enum(capacity: CapacitySet) -> None:
    """불릿 합집합은 st1/sc1/standard를 놓쳐 유효한 값을 거부하게 된다 → enum 금지."""
    assert find(capacity, VOLUME, "VolumeType", "enum") is None


def test_negative_ephemeral_storage_wrapper_has_no_range(capacity: CapacitySet) -> None:
    """$ref 래퍼($tmp 설명이 붙은 곳)에는 범위 레코드를 만들면 안 된다."""
    assert find(capacity, LAMBDA, "EphemeralStorage", "min") is None
    assert find(capacity, LAMBDA, "EphemeralStorage", "max") is None


def test_negative_throughput_ignores_example_and_ratio(capacity: CapacitySet) -> None:
    assert find(capacity, VOLUME, "Throughput", "max").value not in (750, 0.25)


def test_negative_r1_schema_wins_over_prose(capacity: CapacitySet) -> None:
    """Timeout 설명에 'The default is 3 seconds'가 있어도 min은 스키마 값(1)이어야 한다."""
    assert find(capacity, LAMBDA, "Timeout", "min").evidence == "cfn-schema"


def test_negative_string_typed_numeric_property_skipped(capacity: CapacitySet) -> None:
    """RDS.Port는 type=string이라 Phase 1에서 범위를 뽑지 않는다."""
    assert find(capacity, RDS, "Port", "min") is None
    assert find(capacity, RDS, "Port", "max") is None


def test_negative_sqs_lower_bound_dropped_for_sentinel(capacity: CapacitySet) -> None:
    """'integer from 1 to 20'이지만 0도 유효 → 하한을 기록하면 fail-closed."""
    assert find(capacity, SQS, "ReceiveMessageWaitTimeSeconds", "min") is None
    assert find(capacity, SQS, "ReceiveMessageWaitTimeSeconds", "max").value == 20


def test_negative_readonly_property_no_prose(capacity: CapacitySet) -> None:
    assert not [
        c
        for c in capacity.for_property(SUBNET, "SubnetId")
        if c.evidence == "cfn-description"
    ]


# ---------------------------------------------------------------- 방어 이중화 / 플래그


def test_r3_survives_without_veto(monkeypatch) -> None:
    """veto를 꺼도 자기모순 검사가 RDS.Iops의 비율 오탐을 막아야 한다."""
    monkeypatch.setattr(prose, "_VETO", re.compile(r"(?!x)x"))
    capacity = parse_schemas(load_schemas(), prose=True)
    assert find(capacity, RDS, "Iops", "max") is None


def test_no_prose_flag() -> None:
    capacity = parse_schemas(load_schemas(), prose=False)
    assert all(c.evidence == "cfn-schema" for c in capacity.constraints)
    # 스키마 제약은 그대로 남는다
    assert find(capacity, LAMBDA, "Timeout", "min").value == 1
    assert find(capacity, SUBNET, "VpcId", "mutability").value == "create_only"
    assert find(capacity, LAMBDA, "Timeout", "max") is None


def test_rule_count_snapshot() -> None:
    """산문 규칙이 조용히 넓어지면 즉시 실패하게 고정한다."""
    stats: Counter = Counter()
    parse_schemas(load_schemas(), prose=True, stats=stats)
    # backtick_range 5 = Volume.Size(min+max) + Volume.Iops(min+max)
    #                    + PromotionTier(max만 — min은 스키마에 있어 R1이 차단)
    assert dict(stats) == {
        "backtick_range": 5,
        "default_num": 4,
        "default_str": 1,
        "enum_valid_values": 1,
        "from_to": 1,
        "max_allowed": 1,
        "min_at_least": 1,
        "valid_range": 2,
    }


def test_graph_validates(capacity: CapacitySet) -> None:
    capacity.validate()


def test_type_id_convention_matches_graphkb(capacity: CapacitySet) -> None:
    """graphkb 노드 id와 같은 규약이어야 두 지식베이스를 조인할 수 있다."""
    assert all(c.type_id.startswith("aws::AWS::") for c in capacity.constraints)


# ---------------------------------------------------------------- 전수 감사 스냅샷

CACHED_ZIP = (
    Path(__file__).resolve().parent.parent / ".cache" / "cloudkb" / "CloudformationSchema.zip"
)
AUDIT_FILE = Path(__file__).parent / "fixtures" / "capacity" / "prose-audit.json"


@pytest.mark.skipif(
    not CACHED_ZIP.exists(),
    reason="전체 스키마 zip 캐시가 있을 때만 실행 (`capacitykb build --source cfn`으로 생성)",
)
def test_prose_only_ranges_match_reviewed_snapshot() -> None:
    """산문으로만 얻은 범위 전수가 **사람이 검토·승인한 목록**과 일치해야 한다.

    대상이 31개뿐이라 샘플링 없이 전부 검토했다. 정규식을 고쳐 새 항목이 생기거나
    값이 바뀌면 여기서 실패하므로 사람이 다시 검토하게 된다.
    """
    from app.core.cloudkb.capacitykb.parsers.cfn import parse_zip

    approved = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))["approved"]
    built = parse_zip(CACHED_ZIP)

    actual: dict[str, dict] = {}
    for item in built.constraints:
        if item.evidence != "cfn-description" or item.kind not in ("min", "max"):
            continue
        entry = actual.setdefault(f"{item.type_id}.{item.property}", {"conditional": False})
        entry[item.kind] = item.value
        entry["conditional"] = entry["conditional"] or item.conditional

    assert actual == approved
