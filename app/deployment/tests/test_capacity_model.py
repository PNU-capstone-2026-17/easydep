"""capacitykb 공통 데이터 모델 단위 테스트."""

from __future__ import annotations

import jsonschema
import pytest

from capacitykb.model import CapacitySet, Constraint, Quota


def make_constraint(**overrides) -> Constraint:
    base = {
        "type_id": "aws::AWS::EC2::Volume",
        "property": "Size",
        "kind": "max",
        "value": 65536,
        "evidence": "cfn-description",
        "confidence": 0.6,
        "value_type": "integer",
        "unit": "GiB",
        "conditional": True,
        "note": "VolumeType에 따라 범위가 다름",
    }
    base.update(overrides)
    return Constraint(**base)


def make_quota(**overrides) -> Quota:
    base = {
        "provider": "azure",
        "name": "Subnets per virtual network",
        "source_doc": "azure-virtual-network-limits.md",
        "evidence": "azure-limits-doc",
        "confidence": 0.9,
        "scope": "virtual network",
        "default": 3000,
        "type_id": "azure::Microsoft.Network/virtualNetworks/subnets",
    }
    base.update(overrides)
    return Quota(**base)


def make_set() -> CapacitySet:
    result = CapacitySet()
    result.add_constraint(make_constraint())
    result.add_quota(make_quota())
    return result


def test_roundtrip() -> None:
    original = make_set()
    restored = CapacitySet.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()


def test_validate_passes() -> None:
    make_set().validate()


def test_validate_rejects_bad_confidence() -> None:
    bad = CapacitySet()
    bad.add_constraint(make_constraint(confidence=1.5))
    with pytest.raises(jsonschema.ValidationError):
        bad.validate()


def test_validate_rejects_unknown_kind() -> None:
    bad = CapacitySet()
    bad.add_constraint(make_constraint(kind="vibes"))
    with pytest.raises(jsonschema.ValidationError):
        bad.validate()


def test_validate_rejects_unknown_evidence() -> None:
    bad = CapacitySet()
    bad.add_constraint(make_constraint(evidence="guesswork"))
    with pytest.raises(jsonschema.ValidationError):
        bad.validate()


def test_validate_rejects_bad_mutability_value() -> None:
    """mutability 레코드의 value는 세 가지 중 하나여야 한다."""
    bad = CapacitySet()
    bad.add_constraint(make_constraint(kind="mutability", value="sometimes"))
    with pytest.raises(jsonschema.ValidationError):
        bad.validate()
    good = CapacitySet()
    good.add_constraint(make_constraint(kind="mutability", value="create_only"))
    good.validate()


def test_dedup_keeps_higher_confidence() -> None:
    result = CapacitySet()
    result.add_constraint(make_constraint(evidence="cfn-description", confidence=0.6))
    result.add_constraint(
        make_constraint(evidence="cfn-schema", confidence=1.0, conditional=False)
    )
    assert len(result.constraints) == 1
    assert result.constraints[0].evidence == "cfn-schema"
    # 낮은 신뢰도가 뒤에 와도 덮어쓰지 않는다
    result.add_constraint(make_constraint(evidence="cfn-description", confidence=0.6))
    assert result.constraints[0].evidence == "cfn-schema"


def test_different_kinds_coexist() -> None:
    """같은 프로퍼티라도 kind가 다르면 근거·신뢰도가 따로 유지된다 (narrow 모델의 핵심)."""
    result = CapacitySet()
    result.add_constraint(
        make_constraint(kind="min", value=1, evidence="cfn-schema", confidence=1.0)
    )
    result.add_constraint(
        make_constraint(kind="max", value=900, evidence="cfn-description", confidence=0.8)
    )
    assert len(result.constraints) == 2
    kinds = {c.kind: c for c in result.constraints}
    assert kinds["min"].confidence == 1.0
    assert kinds["max"].confidence == 0.8


def test_has_and_get_constraint() -> None:
    result = make_set()
    assert result.has_constraint("aws::AWS::EC2::Volume", "Size", "max")
    assert not result.has_constraint("aws::AWS::EC2::Volume", "Size", "min")
    assert result.get_constraint("aws::AWS::EC2::Volume", "Size", "max").value == 65536
    assert result.get_constraint("aws::AWS::EC2::Volume", "Size", "min") is None


def test_for_type_and_for_property() -> None:
    result = CapacitySet()
    result.add_constraint(make_constraint(kind="min", value=1))
    result.add_constraint(make_constraint(kind="max", value=65536))
    result.add_constraint(make_constraint(property="Iops", kind="max", value=256000))
    result.add_constraint(make_constraint(type_id="aws::AWS::EC2::Subnet", kind="min"))
    assert len(result.for_type("aws::AWS::EC2::Volume")) == 3
    assert len(result.for_property("aws::AWS::EC2::Volume", "Size")) == 2


def test_quota_dedup_and_nonnumeric_default() -> None:
    result = CapacitySet()
    result.add_quota(make_quota(default="Contact support", confidence=0.7))
    result.add_quota(make_quota(default=3000, confidence=0.9))
    assert len(result.quotas) == 1
    assert result.quotas[0].default == 3000
    result.validate()


def test_merge() -> None:
    left = make_set()
    right = CapacitySet()
    right.add_constraint(make_constraint(property="Iops"))
    right.add_quota(make_quota(name="Virtual networks", scope="subscription"))
    left.merge(right)
    assert len(left.constraints) == 2
    assert len(left.quotas) == 2


def test_save_and_load(tmp_path) -> None:
    original = make_set()
    path = tmp_path / "out" / "capacity.json"
    original.save(path)
    restored = CapacitySet.load(path)
    assert restored.to_dict() == original.to_dict()


def test_save_validates_before_writing(tmp_path) -> None:
    bad = CapacitySet()
    bad.add_constraint(make_constraint(confidence=2.0))
    path = tmp_path / "bad.json"
    with pytest.raises(jsonschema.ValidationError):
        bad.save(path)
    assert not path.exists()
