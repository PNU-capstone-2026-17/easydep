"""query / agent_api 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.deployment.capacitykb import agent_api
from app.deployment.capacitykb.model import CapacitySet, Constraint, Quota
from app.deployment.capacitykb.query import (
    check_value,
    find_quota,
    immutable_properties,
    limits_for,
    resolve_type,
)
from app.deployment.tests._helpers import flat

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
    with pytest.raises(ValueError, match="type not found"):
        resolve_type(capacity, "nope")


# --- check_value ---


def test_check_within_schema_range(capacity: CapacitySet) -> None:
    result = check_value(capacity, VOLUME, "Iops", 3000)
    assert result.ok
    assert result.checked == 2


def test_check_above_schema_max(capacity: CapacitySet) -> None:
    result = check_value(capacity, VOLUME, "Iops", 100000)
    assert not result.ok
    assert "is outside max 64000 IOPS" in flat(result.violations[0])
    # 근거는 계속 밝히되 내부 라벨(cfn-schema)이 아니라 사람 말로 — kbcommon.display
    assert "CloudFormation schema" in result.violations[0]
    assert "cfn-schema" not in result.violations[0]


def test_guess_is_advisory_not_verdict(capacity: CapacitySet) -> None:
    """산문 유래(0.6) 제약은 값을 거부하지 않고 참고로만 알린다 — fail-open."""
    result = check_value(capacity, VOLUME, "Size", 100000)
    assert result.violations == []
    assert len(result.advisories) == 1
    assert "[for reference]" in flat(result.advisories[0])


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
    assert "is outside max 65536 GiB" in flat(result.violations[0])


def test_check_enum(capacity: CapacitySet) -> None:
    assert check_value(capacity, VOLUME, "VolumeType", "gp3").ok
    result = check_value(capacity, VOLUME, "VolumeType", "st1")
    assert not result.ok
    assert "is outside allowed values" in flat(result.violations[0])


def test_check_read_only_property(capacity: CapacitySet) -> None:
    result = check_value(capacity, SUBNET, "SubnetId", "subnet-123")
    assert not result.ok
    assert "read-only, cannot be set" in flat(result.violations[0])


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
    assert text.startswith("not allowed:")
    assert "CloudFormation schema" in text
    assert "cfn-schema" not in text


def test_agent_api_check_advisory_is_held_not_allowed(output_dir: Path) -> None:
    """확정 제약이 없고 참고 정보상 벗어나면 '가능'이 아니라 '판정 보류'여야 한다."""
    text = agent_api.check("AWS::EC2::Volume", "Size", 100000, output_dir=output_dir)
    assert text.startswith("verdict withheld:")
    assert not text.startswith("allowed:")
    assert "65536" in text
    # **유보하는 이유가 답에 있어야 한다.** 예전엔 "신뢰도가 낮아"라고 적었는데,
    # 신뢰도라는 숫자는 폐기됐다(척도의 정의가 없어 임계값을 0.01 옮기면 판정이
    # 뒤집혔다). 지금 가르는 것은 `basis` 하나이므로 말도 그렇게 해야 한다.
    assert "prose that the source never stated" in flat(text)
    assert "confidence" not in flat(text).lower()


def test_agent_api_immutable(output_dir: Path) -> None:
    text = agent_api.immutable("AWS::EC2::Subnet", output_dir=output_dir)
    assert "VpcId" in text
    assert "properties that recreate the resource when changed" in flat(text)
    assert "SubnetId" not in text


def test_agent_api_property_limits(output_dir: Path) -> None:
    text = agent_api.property_limits("AWS::EC2::Volume", "Iops", output_dir=output_dir)
    assert "max 64000 IOPS" in flat(text)
    assert "min 100 IOPS" in flat(text)


def test_agent_api_allowed_values(output_dir: Path) -> None:
    text = agent_api.allowed_values("AWS::EC2::Volume", "VolumeType", output_dir=output_dir)
    assert "gp3" in text


def test_agent_api_unknown_type_message(output_dir: Path) -> None:
    assert "type not found" in agent_api.check("nope", "x", 1, output_dir=output_dir)


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
    from app.deployment.capacitykb.agent_api import _nothing_found

    capacity = CapacitySet()
    capacity.coverage = [{"provider": "aws"}]

    covered = _nothing_found(capacity, "aws::AWS::EC2::Volume", "aws::AWS::EC2::Volume")
    assert "no known constraint" in flat(covered)

    unknown = _nothing_found(capacity, "gcp::ComputeInstance", "gcp::ComputeInstance")
    assert "outside the collected scope" in flat(unknown)
    assert "that does not mean there are none" in flat(unknown)


def test_weak_reference_survives_when_in_range(capacity: CapacitySet) -> None:
    """범위 **안**인 약한 근거도 버리지 않는다 (결함 ①).

    예전에는 약한 근거를 '벗어났을 때만' 기록해서, 범위 안이면 아무것도 안 남고
    판정이 unknown으로 떨어졌다. 그 결과 "gp2 30,000 GiB 되나?"에 "알려진 제약이
    없습니다"라고 답했는데 데이터셋 안에는 볼륨 종류별 상한표가 들어 있었다.
    """
    result = check_value(capacity, VOLUME, "Size", 30000)
    assert result.verdict == "unknown"  # 확정 근거는 여전히 없다
    assert result.references, "범위 안인 약한 근거가 버려졌다"
    assert "65536" in result.references[0]
    assert result.known, "참고 정보가 있으면 '아무것도 모른다'가 아니다"


def test_agent_api_unknown_still_reports_what_it_holds(output_dir: Path) -> None:
    """확정 판정을 못 해도 쥐고 있는 참고 정보는 내놓아야 한다."""
    text = agent_api.check("AWS::EC2::Volume", "Size", 30000, output_dir=output_dir)
    assert "no known constraint" not in flat(text), "답을 쥐고도 모른다고 말하고 있다"
    assert "no definite verdict" in flat(text)
    assert "65536" in text
    assert "for reference (not stated by the source; not used in the verdict)" in flat(text)


def test_agent_api_hides_internal_ids(output_dir: Path) -> None:
    """내부 id 접두사와 근거 라벨은 사용자에게 보이지 않는다 (결함 ⑥)."""
    text = agent_api.immutable("AWS::EC2::Volume", output_dir=output_dir)
    assert "aws::" not in text
    for label in ("cfn-schema", "cfn-description", "cdk-oob", "heuristic"):
        assert label not in text


# --- 표시 계약: 조건을 밝히고, 긴 목록은 요약한다 ---


@pytest.fixture()
def wide(tmp_path) -> Path:
    """리전별 허용값을 흉내낸 산출물 — 조건부이고 값이 아주 많다."""
    result = CapacitySet()
    for region, size in (("us-east-1", 500), ("af-south-1", 400)):
        result.add_constraint(
            constraint(
                property="InstanceType", kind="enum",
                value=[f"m{n}.large" for n in range(size)],
                evidence="cfn-lint-region", unit=None, conditional=False,
                value_type="string",
                conditions=({"property": "Region", "op": "eq", "value": region},),
            )
        )
    result.add_constraint(
        constraint(
            property="Size", kind="max", value=16384, evidence="cfn-schema",
            conditional=False,
            conditions=({"property": "VolumeType", "op": "eq", "value": "gp2"},),
        )
    )
    result.save(tmp_path / "aws-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    return tmp_path


def test_violation_names_the_condition(wide: Path) -> None:
    """위반 문구는 **어느 조건에서의** 한도인지 밝혀야 한다.

    안 밝히면 "최대 16,384 GiB"가 보편 상한처럼 읽힌다. 정작 사용자가 필요한
    사실은 gp2에서의 한도라는 것 — 즉 gp3로 바꾸면 된다는 것이다.
    """
    text = agent_api.check(
        "AWS::EC2::Volume", "Size", 30000,
        context={"VolumeType": "gp2"}, output_dir=wide,
    )
    assert text.startswith("not allowed:")
    assert "when VolumeType='gp2'," in flat(text), "한도의 조건이 빠져 보편 상한처럼 읽힌다"


def test_property_limits_summarizes_and_scopes(wide: Path) -> None:
    """긴 허용값을 통째로 찍지 않고, 줄마다 조건을 밝힌다.

    실측 결함: `property_limits('AWS::EC2::Instance','InstanceType')`이
    **377,439자**를 반환해 도구 응답 하나가 모델 컨텍스트를 통째로 먹었다.
    요약을 `check` 경로에만 넣고 이쪽에 안 넣은 게 원인이었다.

    줄인 뒤엔 리전별 줄이 전부 똑같아 보이는 두 번째 결함이 드러났다 —
    조건이 그 줄의 뜻 전부인 경우가 있다.
    """
    text = agent_api.property_limits("AWS::EC2::Volume", "InstanceType", output_dir=wide)
    assert len(text) < 2000, f"응답이 {len(text):,}자 — 목록을 통째로 찍고 있다"
    assert "m0.large" not in text.split("…")[-1], "요약 뒤에도 전체 목록이 남아 있다"
    assert "one of 500" in flat(text) and "one of 400" in flat(text)
    for region in ("us-east-1", "af-south-1"):
        assert f"when Region={region!r}" in flat(text), f"{region} 줄이 어느 조건인지 모른다"


# --- 축 간 교차 참조 (2차 실측) ---


def test_type_summary_points_at_the_data(wide: Path) -> None:
    """다른 축의 입구에서 "여기 데이터가 있다"를 알 수 있어야 한다.

    실측 결함: `kb_describe_type('AWS::EC2::Instance')`이 그래프 엣지만 1,484자
    반환하고 제약을 한 글자도 말하지 않아서, 에이전트가 근거 없음으로 판단하고
    웹검색 13회를 14분간 돌린 뒤 "지식베이스에 없습니다"라고 답했다. 그 순간
    KB는 리전별 허용값 39건을 쥐고 있었다.
    """
    text = agent_api.type_summary("AWS::EC2::Volume", output_dir=wide)
    assert text and "InstanceType" in text, "제약이 걸린 속성 이름이 안 보인다"
    # **축으로 가리킨다 — 도구 이름을 적지 않는다.** 예전엔 여기서 `cap_check_value`를
    # 요구했는데, 그 줄은 사용자에게 옮겨지는 텍스트라 지시문이 금지한 내부 이름
    # 노출을 우리가 유발했다(2026-07-25). 게다가 같이 적혀 있던 이름 둘은 도구
    # 통합으로 사라져 **없는 도구를 가리키고 있었다.**
    assert "limits & constraints axis" in text, "다음에 볼 축을 안 가리킨다"


def test_type_summary_is_silent_on_unknown_types(wide: Path) -> None:
    """모르는 타입엔 아무 말도 하지 않는다 — 그래프 질의를 어지럽히면 안 된다."""
    assert agent_api.type_summary("AWS::Lambda::Function", output_dir=wide) is None


def test_capacity_pointer_survives_broken_output(monkeypatch) -> None:
    """용량 산출물이 깨져도 그래프 질의는 답해야 한다. 이 줄은 덤이다."""
    from app.deployment.nim_agent import graph_tools

    def boom(*args, **kwargs):
        raise RuntimeError("산출물 손상")

    monkeypatch.setattr(graph_tools.capacity_api, "type_summary", boom)
    assert graph_tools._capacity_pointer("AWS::EC2::Instance") == ""


def test_value_lookup_turns_a_dead_end_into_an_answer(wide: Path) -> None:
    """값을 타입 이름으로 물어도 어디를 봐야 하는지 알려줘야 한다.

    실측 결함: 에이전트가 `p5.48xlarge`를 **타입 이름으로** 물었고(사람도 그렇게
    묻는다) 우리는 "노드를 찾을 수 없습니다"만 내놨다. 값에서 속성으로 가는
    길이 없어서, 올바른 질문을 하고도 막다른 길을 만나 웹으로 나갔다.
    """
    agent_api._value_index.cache_clear()
    text = agent_api.value_lookup("m3.large", output_dir=wide)
    assert text, "허용값인데 못 찾았다"
    assert "InstanceType" in text, "어느 속성의 값인지 안 알려준다"
    assert "allowed under 2 of 2 conditions" in flat(text), "몇 개 조건에서 되는지 안 센다"
    # **축으로 가리킨다 — 도구 이름을 적지 않는다.** 예전엔 여기서 `cap_check_value`를
    # 요구했는데, 그 줄은 사용자에게 옮겨지는 텍스트라 지시문이 금지한 내부 이름
    # 노출을 우리가 유발했다(2026-07-25). 게다가 같이 적혀 있던 이름 둘은 도구
    # 통합으로 사라져 **없는 도구를 가리키고 있었다.**
    assert "limits & constraints axis" in text, "다음에 볼 축을 안 가리킨다"


def test_value_lookup_is_silent_on_unknown_names(wide: Path) -> None:
    """모르는 이름엔 아무 말도 하지 않는다 — 지어내면 없느니만 못하다."""
    agent_api._value_index.cache_clear()
    assert agent_api.value_lookup("m9999.enormous", output_dir=wide) is None


def test_search_does_not_contradict_its_own_results(monkeypatch, wide: Path) -> None:
    """타입을 찾았으면 "이건 타입이 아니라 값입니다"라고 말하면 안 된다.

    값 안내를 무조건 붙였더니 'subnet' 검색이 타입 12개를 찾아 놓고 바로 밑에
    모순된 말을 했다(실측). 막다른 길일 때만 다른 길을 가리킨다.
    """
    from app.deployment.graphkb import agent_api as graph_api
    from app.deployment.nim_agent import graph_tools

    agent_api._value_index.cache_clear()
    monkeypatch.setattr(agent_api, "DEFAULT_OUTPUT_DIR", wide)

    def compose(keyword: str) -> str:
        found = graph_api.search_types(keyword)
        if graph_api.count_types(keyword):
            return found
        return found + graph_tools._capacity_pointer(keyword)

    hit = compose("Volume")  # 타입으로 실재하는 이름
    assert graph_api.count_types("Volume"), "전제가 깨졌다 — 타입이 있어야 한다"
    assert "is not a type but a" not in flat(hit), "찾아 놓고 아니라고 말한다"

    miss = compose("m3.large")  # 값이지 타입이 아닌 이름
    assert "is not a type but a" in flat(miss), "막다른 길인데 다른 축을 안 가리킨다"


# --- 정규식 방언 ---


def test_vendor_dialect_patterns_are_evaluated(tmp_path) -> None:
    r"""`\p{L}` 계열은 원본이 틀린 게 아니라 .NET/PCRE 문법이다.

    파이썬 `re`로는 205건이 안 돌았는데 그중 194건이 이것이었다. 예전에는
    `re.error`를 잡아 조용히 건너뛰어서, 우리가 패턴을 쥐고도 판정하지 않는다는
    사실이 답변에서 통째로 사라졌다.
    """
    result = CapacitySet()
    result.add_constraint(
        constraint(
            property="Name", kind="pattern", value=r"^([\p{L}\p{N}_]*)$",
            evidence="cfn-schema", unit=None, conditional=False, value_type="string",
        )
    )
    result.save(tmp_path / "aws-capacity.json")
    agent_api._load_merged_cached.cache_clear()

    ok = agent_api.check("AWS::EC2::Volume", "Name", "abc123", output_dir=tmp_path)
    assert ok.startswith("allowed:"), "방언 패턴을 여전히 못 읽는다"
    bad = agent_api.check("AWS::EC2::Volume", "Name", "a b!", output_dir=tmp_path)
    assert bad.startswith("not allowed:")


def test_unreadable_pattern_is_reported_not_swallowed(tmp_path) -> None:
    """어느 엔진에서도 안 도는 정규식(상류 오류 11건)은 **밝힌다.**

    조용히 넘기면 "패턴 제약이 없다"로 읽힌다 — 우리가 쥐고 있는데도.
    """
    result = CapacitySet()
    result.add_constraint(
        constraint(
            property="KmsKeyId", kind="pattern", value=r"^[a-z]*{1,1000}$",
            evidence="cfn-schema", unit=None, conditional=False, value_type="string",
        )
    )
    result.save(tmp_path / "aws-capacity.json")
    agent_api._load_merged_cached.cache_clear()

    text = agent_api.check("AWS::EC2::Volume", "KmsKeyId", "abc", output_dir=tmp_path)
    assert "no known constraint" not in flat(text), "쥐고 있는 제약을 없다고 말한다"
    assert "we cannot read its regex" in flat(text)


def test_wrong_property_name_points_at_the_real_ones(wide: Path) -> None:
    """살짝 틀린 속성 이름에 막다른 길을 주지 않는다.

    실측: 에이전트가 RDS에 `InstanceType`을 물었다(EC2가 그 이름을 쓰니 자연스러운
    시도인데 RDS는 `DBInstanceClass`다). 우리는 938건을 쥐고도 "알려진 제약이
    없어 판정할 수 없습니다"라고만 답했다 — `p5.48xlarge`를 타입 이름으로 물었을
    때와 같은 실패다.
    """
    text = agent_api.check("AWS::EC2::Volume", "InstanceTyp", "x", output_dir=wide)
    assert "this type has no such property" in flat(text), "막다른 길을 그대로 준다"
    assert "InstanceType" in text, "우리가 아는 이름을 안 가리킨다"


def test_property_hint_is_silent_when_the_type_is_unknown(tmp_path) -> None:
    """아는 게 없으면 후보를 지어내지 않는다."""
    CapacitySet().save(tmp_path / "aws-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.check("AWS::EC2::Volume", "Size", 1, output_dir=tmp_path)
    assert "did you mean" not in flat(text)
