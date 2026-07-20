"""query / agent_api 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from capacitykb import agent_api
from capacitykb.model import CapacitySet, Constraint, Quota
from capacitykb.query import (
    check_value,
    find_quota,
    immutable_properties,
    limits_for,
    resolve_type,
)

VOLUME = "aws::AWS::EC2::Volume"
SUBNET = "aws::AWS::EC2::Subnet"


def constraint(**overrides) -> Constraint:
    base = {
        "type_id": VOLUME,
        "property": "Size",
        "kind": "max",
        "value": 65536,
        "evidence": "cfn-description",
        "value_type": "integer",
        "unit": "GiB",
        "conditional": True,
    }
    base.update(overrides)
    return Constraint(**base)


@pytest.fixture()
def capacity() -> CapacitySet:
    result = CapacitySet()
    # 산문 유래(0.6) — 판정에는 쓰지 않고 참고로만
    result.add_constraint(constraint())
    # 스키마 유래(1.0) — 판정에 사용
    result.add_constraint(
        constraint(
            property="Iops", kind="max", value=64000, evidence="cfn-schema",
            unit="IOPS", conditional=False,
        )
    )
    result.add_constraint(
        constraint(
            property="Iops", kind="min", value=100, evidence="cfn-schema",
            unit="IOPS", conditional=False,
        )
    )
    result.add_constraint(
        constraint(
            property="VolumeType", kind="enum", value=["gp2", "gp3", "io1"],
            evidence="cfn-schema", unit=None, conditional=False,
            value_type="string",
        )
    )
    result.add_constraint(
        constraint(
            type_id=SUBNET, property="VpcId", kind="mutability", value="create_only",
            evidence="cfn-schema", unit=None, conditional=False,
            value_type="string",
        )
    )
    result.add_constraint(
        constraint(
            type_id=SUBNET, property="Ipv6CidrBlock", kind="mutability",
            value="conditional_create_only", evidence="cfn-schema",
            unit=None, conditional=False, value_type="string",
        )
    )
    result.add_constraint(
        constraint(
            type_id=SUBNET, property="SubnetId", kind="mutability", value="read_only",
            evidence="cfn-schema", unit=None, conditional=False,
            value_type="string",
        )
    )
    result.add_quota(
        Quota(
            provider="azure",
            name="Subnets per virtual network",
            source_doc="azure-virtual-network-limits.md",
            evidence="azure-limits-doc",
            scope="virtual network",
            default=3000,
            type_id="azure::Microsoft.Network/virtualNetworks/subnets",
        )
    )
    return result


# --- resolve_type ---


def test_resolve_exact_and_short_name(capacity: CapacitySet) -> None:
    assert resolve_type(capacity, VOLUME) == VOLUME
    assert resolve_type(capacity, "AWS::EC2::Volume") == VOLUME
    assert resolve_type(capacity, "aws::aws::ec2::volume") == VOLUME


def test_resolve_unknown_raises(capacity: CapacitySet) -> None:
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        resolve_type(capacity, "nope")


# --- check_value ---


def test_check_within_schema_range(capacity: CapacitySet) -> None:
    result = check_value(capacity, VOLUME, "Iops", 3000)
    assert result.ok
    assert result.checked == 2


def test_check_above_schema_max(capacity: CapacitySet) -> None:
    result = check_value(capacity, VOLUME, "Iops", 100000)
    assert not result.ok
    assert "최대 64000" in result.violations[0]
    assert "cfn-schema" in result.violations[0]


def test_guess_is_advisory_not_verdict(capacity: CapacitySet) -> None:
    """산문 유래(0.6) 제약은 값을 거부하지 않고 참고로만 알린다 — fail-open."""
    result = check_value(capacity, VOLUME, "Size", 100000)
    assert result.violations == []
    assert len(result.advisories) == 1
    assert "참고" in result.advisories[0]


def test_advisory_breach_is_not_reported_as_ok(capacity: CapacitySet) -> None:
    """참고 정보상 벗어났는데 '가능'이라고 하면 거짓 긍정이 된다 → 보류 상태."""
    result = check_value(capacity, VOLUME, "Size", 100000)
    assert result.verdict == "advisory"
    assert result.checked == 0


def test_verdict_states(capacity: CapacitySet) -> None:
    assert check_value(capacity, VOLUME, "Iops", 3000).verdict == "ok"
    assert check_value(capacity, VOLUME, "Iops", 100000).verdict == "violation"
    assert check_value(capacity, VOLUME, "Nonexistent", 1).verdict == "unknown"


def test_value_within_advisory_range_is_ok(capacity: CapacitySet) -> None:
    """참고 제약도 만족하면 참고 메시지가 없다."""
    result = check_value(capacity, VOLUME, "Size", 100)
    assert result.advisories == []
    assert result.verdict in ("ok", "unknown")


def test_guess_can_be_enforced_explicitly(capacity: CapacitySet) -> None:
    result = check_value(capacity, VOLUME, "Size", 100000, facts_only=False)
    assert not result.ok
    assert "최대 65536 GiB" in result.violations[0]


def test_check_enum(capacity: CapacitySet) -> None:
    assert check_value(capacity, VOLUME, "VolumeType", "gp3").ok
    result = check_value(capacity, VOLUME, "VolumeType", "st1")
    assert not result.ok
    assert "허용값" in result.violations[0]


def test_check_read_only_property(capacity: CapacitySet) -> None:
    result = check_value(capacity, SUBNET, "SubnetId", "subnet-123")
    assert not result.ok
    assert "읽기 전용" in result.violations[0]


def test_check_unknown_property_is_not_a_verdict(capacity: CapacitySet) -> None:
    result = check_value(capacity, VOLUME, "Nonexistent", 1)
    assert result.known is False


# --- limits / immutable / quota ---


def test_limits_for_can_show_facts_only(capacity: CapacitySet) -> None:
    assert len(limits_for(capacity, VOLUME)) == 4
    assert len(limits_for(capacity, VOLUME, facts_only=True)) == 3
    assert len(limits_for(capacity, VOLUME, prop="Iops")) == 2


def test_immutable_properties_excludes_read_only(capacity: CapacitySet) -> None:
    found = {c.property for c in immutable_properties(capacity, SUBNET)}
    assert found == {"VpcId", "Ipv6CidrBlock"}  # SubnetId(read_only)는 제외


def test_find_quota(capacity: CapacitySet) -> None:
    assert len(find_quota(capacity, "subnet")) == 1
    assert find_quota(capacity, "virtual network")[0].default == 3000
    assert find_quota(capacity, "없는키워드") == []


# --- agent_api (텍스트 반환) ---


@pytest.fixture()
def output_dir(tmp_path, capacity: CapacitySet) -> Path:
    capacity.save(tmp_path / "aws-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    return tmp_path


def test_agent_api_check_rejects_with_evidence(output_dir: Path) -> None:
    text = agent_api.check("AWS::EC2::Volume", "Iops", 100000, output_dir=output_dir)
    assert text.startswith("불가")
    assert "cfn-schema" in text


def test_agent_api_check_advisory_is_held_not_allowed(output_dir: Path) -> None:
    """확정 제약이 없고 참고 정보상 벗어나면 '가능'이 아니라 '판정 보류'여야 한다."""
    text = agent_api.check("AWS::EC2::Volume", "Size", 100000, output_dir=output_dir)
    assert text.startswith("판정 보류")
    assert not text.startswith("가능")
    assert "65536" in text
    assert "신뢰도" in text


def test_agent_api_immutable(output_dir: Path) -> None:
    text = agent_api.immutable("AWS::EC2::Subnet", output_dir=output_dir)
    assert "VpcId" in text
    assert "재생성" in text
    assert "SubnetId" not in text


def test_agent_api_property_limits(output_dir: Path) -> None:
    text = agent_api.property_limits("AWS::EC2::Volume", "Iops", output_dir=output_dir)
    assert "최대 64000 IOPS" in text
    assert "최소 100 IOPS" in text


def test_agent_api_allowed_values(output_dir: Path) -> None:
    text = agent_api.allowed_values("AWS::EC2::Volume", "VolumeType", output_dir=output_dir)
    assert "gp3" in text


def test_agent_api_unknown_type_message(output_dir: Path) -> None:
    assert "찾을 수 없습니다" in agent_api.check("nope", "x", 1, output_dir=output_dir)


def test_agent_api_missing_output(tmp_path) -> None:
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.check("AWS::EC2::Volume", "Size", 1, output_dir=tmp_path / "empty")
    assert "capacitykb build" in text


def test_agent_api_quota(tmp_path, capacity: CapacitySet) -> None:
    capacity.save(tmp_path / "azure-quota.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.service_quota("subnet", output_dir=tmp_path)
    assert "3000" in text
    assert "azure-virtual-network-limits.md" in text


def test_out_of_scope_answer_does_not_claim_absence() -> None:
    """**핵심 회귀**: 안 본 타입에 "제약 없음"이라고 답하면 거짓말이다.

    실측: graphkb가 아는 벤더 타입 5,547종 중 3,634종에 제약 레코드가 없고,
    GCP 527종은 capacitykb가 아예 안 읽어서 없는 것이다.
    """
    from capacitykb.agent_api import _nothing_found

    capacity = CapacitySet()
    capacity.coverage = [{"provider": "aws"}]

    covered = _nothing_found(capacity, "aws::AWS::EC2::Volume", "aws::AWS::EC2::Volume")
    assert "없습니다" in covered

    unknown = _nothing_found(capacity, "gcp::ComputeInstance", "gcp::ComputeInstance")
    assert "수집 범위 밖" in unknown
    assert "제약이 없다는 뜻이 아닙니다" in unknown
