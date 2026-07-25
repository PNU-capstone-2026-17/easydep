"""capacitykb 공통 데이터 모델 단위 테스트."""

from __future__ import annotations

import jsonschema
import pytest

from app.deployment.capacitykb.model import CapacitySet, Constraint, Quota
from app.deployment.kbcommon.artifact import ArtifactInvalid


def make_constraint(**overrides) -> Constraint:
    base = {
        "type_id": "aws::AWS::EC2::Volume",
        "property": "Size",
        "kind": "max",
        "value": 65536,
        "evidence": "cfn-description",
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


def test_validate_rejects_unknown_basis() -> None:
    bad = CapacitySet()
    bad.add_constraint(make_constraint(basis="probably"))
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


def test_dedup_keeps_the_fact_over_the_guess() -> None:
    """같은 키면 원본이 명시한 것이 짐작을 이긴다 — 순서와 무관하게."""
    result = CapacitySet()
    result.add_constraint(make_constraint(evidence="cfn-description"))
    result.add_constraint(
        make_constraint(evidence="cfn-schema", conditional=False)
    )
    assert len(result.constraints) == 1
    assert result.constraints[0].evidence == "cfn-schema"
    # 짐작이 뒤에 와도 덮어쓰지 않는다
    result.add_constraint(make_constraint(evidence="cfn-description"))
    assert result.constraints[0].evidence == "cfn-schema"


def test_different_kinds_coexist() -> None:
    """같은 프로퍼티라도 kind가 다르면 근거가 따로 유지된다 (narrow 모델의 핵심)."""
    result = CapacitySet()
    result.add_constraint(
        make_constraint(kind="min", value=1, evidence="cfn-schema")
    )
    result.add_constraint(
        make_constraint(kind="max", value=900, evidence="cfn-description")
    )
    assert len(result.constraints) == 2
    kinds = {c.kind: c for c in result.constraints}
    assert kinds["min"].basis == "stated"      # 스키마 필드
    assert kinds["max"].basis == "inferred"    # 설명문에서 뽑음


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


def test_quota_dedup_prefers_the_stated_number() -> None:
    """비수치 값은 파서가 `azure-limits-note`(짐작)로 라벨을 달아 보낸다.

    그래서 표에서 읽은 숫자(azure-limits-doc, 사실)가 이긴다 — 도착 순서와 무관하게.
    예전엔 신뢰도 0.9 vs 0.7로 갈랐는데, 그 숫자에는 정의가 없었다.
    """
    result = CapacitySet()
    result.add_quota(make_quota(default="Contact support", evidence="azure-limits-note"))
    result.add_quota(make_quota(default=3000))
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
    """save는 이제 kbcommon의 쓰기 관문을 탄다 — costkb·perfkb와 같은 경로다.

    그래서 예외도 `ArtifactInvalid`로 통일됐다. 예전엔 여기서 바로 write_text를 해서
    쓰다 끊기면 잘린 JSON이 남았다.
    """
    bad = CapacitySet()
    bad.add_constraint(make_constraint(kind="vibes"))
    path = tmp_path / "bad.json"
    with pytest.raises(ArtifactInvalid, match="kind"):
        bad.save(path)
    assert not path.exists()


def test_save_refuses_contradictory_records(tmp_path) -> None:
    """스키마는 통과하지만 레코드 **사이**가 모순이면 쓰지 않는다.

    두 레코드 다 형태는 멀쩡하다 — 나란히 놓아야 모순이 보인다.
    """
    bad = CapacitySet()
    bad.add_constraint(make_constraint(kind="default", value=0))
    bad.add_constraint(make_constraint(kind="min", value=10))
    path = tmp_path / "bad.json"
    with pytest.raises(ArtifactInvalid, match="default=0인데 min=10"):
        bad.save(path)
    assert not path.exists()


# --- 커버리지: "없다"와 "안 봤다"를 가른다 (2026-07-21, 감사 §5-5) ---


def test_coverage_absent_means_no_claim() -> None:
    """기록이 없는 옛 산출물은 판단하지 않는다 — 없는 정보로 단정할 수 없다."""
    assert CapacitySet().covers("gcp::ComputeInstance")


def test_scope_limits_what_we_claim_to_know() -> None:
    """범위를 적었으면 그 밖은 '모른다'다.

    실측: capacitykb는 Azure 3,382종 중 3개 네임스페이스만 읽는다. 범위 기록이
    없으면 나머지 3,104종에 대해 "제약 없음"이라고 답하게 된다.
    """
    result = CapacitySet()
    result.coverage = [{"provider": "azure", "scope": ["microsoft.network"]}]
    assert result.covers("azure::Microsoft.Network/virtualNetworks")
    assert not result.covers("azure::Microsoft.Storage/storageAccounts")
    assert not result.covers("gcp::ComputeInstance")  # 프로바이더 자체가 미수집


def test_scope_omitted_means_whole_provider() -> None:
    """AWS는 스키마 zip 전체를 읽으므로 범위 제한이 없다."""
    result = CapacitySet()
    result.coverage = [{"provider": "aws", "types": 1635}]
    assert result.covers("aws::AWS::EC2::Volume")
    assert result.covers("aws::AWS::Anything::AtAll")


def test_coverage_survives_merge_and_roundtrip() -> None:
    left, right = CapacitySet(), CapacitySet()
    left.coverage = [{"provider": "aws"}]
    right.coverage = [{"provider": "azure", "scope": ["microsoft.network"]}]
    left.merge(right)
    assert len(left.coverage) == 2
    assert CapacitySet.from_dict(left.to_dict()).coverage == left.coverage
