"""cfn-lint 리전별 허용값 — CloudFormation이 표현 못 하는 것."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from capacitykb import agent_api
from capacitykb.parsers.cfnlint import parse_wheel, property_of

EXT = "cfnlint/data/schemas/extensions/"
SCHEMAS = {
    "aws::AWS::EC2::Instance": {
        "typeName": "AWS::EC2::Instance",
        "properties": {"InstanceType": {"type": "string"}},
    }
}


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


def _wheel(tmp_path: Path, payload: dict, name: str = "instancetype_enum.json") -> Path:
    path = tmp_path / "w.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(EXT + "aws_ec2_instance/" + name,
                    json.dumps(payload, ensure_ascii=False))
    return path


def test_all_key_is_not_a_region(tmp_path: Path) -> None:
    """**핵심 함정.** `all`은 리전이 아니고, EC2에서는 그 값이 빈 목록이다.

    리전으로 읽으면 "허용값 0개"인 제약이 생겨 **모든 인스턴스 타입이 거부된다** —
    잘못 막는 게 침묵보다 나쁘다는 원칙에 정면으로 어긋난다.
    """
    got, report = parse_wheel(
        _wheel(tmp_path, {
            "all": {"enum": []},
            "us-east-1": {"enum": ["t3.micro", "m5.large"]},
        }),
        cfn_schemas=SCHEMAS,
    )
    assert len(got.constraints) == 1
    assert got.constraints[0].conditions[0]["value"] == "us-east-1"
    assert report.skipped_all == 1, "건너뛴 사실을 세어야 한다"
    assert not [c for c in got.constraints if c.value == []]


def test_empty_region_list_is_not_stored(tmp_path: Path) -> None:
    """빈 목록은 '아무것도 못 쓴다'가 아니라 '데이터가 없다'로 본다."""
    got, _ = parse_wheel(
        _wheel(tmp_path, {"us-east-1": {"enum": []},
                          "us-west-2": {"enum": ["t3.micro"]}}),
        cfn_schemas=SCHEMAS,
    )
    assert [c.conditions[0]["value"] for c in got.constraints] == ["us-west-2"]


def test_property_name_is_verified_not_guessed() -> None:
    """스키마에 없는 이름은 만들지 않는다 — 그러면 그 제약은 영원히 안 걸린다."""
    assert property_of("instancetype_enum.json", {"InstanceType"}) == "InstanceType"
    # 중첩 경로를 담은 파일명은 뒤에서부터 잘라 실재하는 이름을 찾는다
    assert property_of("clusterconfig_instancetype_enum.json", {"InstanceType"}) == "InstanceType"
    assert property_of("nope_enum.json", {"InstanceType"}) is None


def test_unknown_property_is_counted_not_stored(tmp_path: Path) -> None:
    got, report = parse_wheel(
        _wheel(tmp_path, {"us-east-1": {"enum": ["x"]}}, name="nope_enum.json"),
        cfn_schemas=SCHEMAS,
    )
    assert not got.constraints
    assert report.unmapped_props


def test_region_decides_the_answer(tmp_path: Path) -> None:
    got, _ = parse_wheel(
        _wheel(tmp_path, {"us-east-1": {"enum": ["p5.48xlarge", "t3.micro"]},
                          "af-south-1": {"enum": ["t3.micro"]}}),
        cfn_schemas=SCHEMAS,
    )
    got.coverage = [{"provider": "aws", "types": 1,
                     "type_ids": ["aws::AWS::EC2::Instance"]}]
    got.save(tmp_path / "aws-regions.json")
    agent_api._load_merged_cached.cache_clear()

    ok = agent_api.check("AWS::EC2::Instance", "InstanceType", "p5.48xlarge",
                         context={"Region": "us-east-1"}, output_dir=tmp_path)
    no = agent_api.check("AWS::EC2::Instance", "InstanceType", "p5.48xlarge",
                         context={"Region": "af-south-1"}, output_dir=tmp_path)
    assert ok.startswith("allowed:") and no.startswith("not allowed:")


def test_without_region_it_counts_where_it_works(tmp_path: Path) -> None:
    """조건이 많으면 나열이 아니라 **세어 주는 것**이 답에 가깝다.

    실제 리전은 38개다. 반대로 볼륨 종류처럼 몇 개 안 되면 값을 그대로 보여준다 —
    "2가지에서 가능"만 말하면 정작 한도 숫자가 사라진다.
    """
    payload = {"us-east-1": {"enum": ["p5.48xlarge"]}}
    payload.update({f"eu-west-{i}": {"enum": ["t3.micro"]} for i in range(1, 10)})
    got, _ = parse_wheel(_wheel(tmp_path, payload), cfn_schemas=SCHEMAS)
    got.coverage = [{"provider": "aws", "types": 1,
                     "type_ids": ["aws::AWS::EC2::Instance"]}]
    got.save(tmp_path / "aws-regions.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.check("AWS::EC2::Instance", "InstanceType", "p5.48xlarge",
                           output_dir=tmp_path)
    assert "**1 allow it**" in flat(text)
    assert "us-east-1" in text and "eu-west-1" in text


def test_long_value_lists_are_summarized(tmp_path: Path) -> None:
    """허용값 600개를 통째로 찍으면 '되나 안 되나'가 안 보인다."""
    values = [f"t{i}.micro" for i in range(50)]
    got, _ = parse_wheel(
        _wheel(tmp_path, {"us-east-1": {"enum": values}}), cfn_schemas=SCHEMAS
    )
    got.coverage = [{"provider": "aws", "types": 1,
                     "type_ids": ["aws::AWS::EC2::Instance"]}]
    got.save(tmp_path / "aws-regions.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.check("AWS::EC2::Instance", "InstanceType", "nope",
                           context={"Region": "us-east-1"}, output_dir=tmp_path)
    assert "one of 50" in flat(text)
    assert text.count("t4.micro") == 0 or len(text) < 400, "목록을 통째로 찍으면 안 된다"


# --- if/then 조건 블록 (다중 조건) ---


def test_conditions_of_ignores_target_markers() -> None:
    """`if.properties`의 값 없는 항목은 **조건이 아니라 대상 표시**다.

    `{"DBInstanceClass": {"type": "string"}}`은 if 스키마가 그 속성의 존재를
    요구하려고 넣은 것이라, 조건으로 읽으면 "DBInstanceClass가 아무 값일 때"라는
    뜻 없는 조건이 생긴다.
    """
    from capacitykb.parsers.cfnlint import _conditions_of

    got = _conditions_of({
        "properties": {
            "DBInstanceClass": {"type": "string"},
            "Engine": {"const": "aurora-mysql", "type": "string"},
            "EngineVersion": {"pattern": r"^(10\.11)$", "type": ["string", "number"]},
        },
        "required": ["Engine", "EngineVersion", "DBInstanceClass"],
    })
    assert [c["property"] for c in got] == ["Engine", "EngineVersion"]
    assert got[0]["op"] == "eq" and got[1]["op"] == "matches"


def test_a_definite_mismatch_beats_an_unknown() -> None:
    """조건 하나가 **확실히 안 맞으면** 나머지를 몰라도 해당 없음이다.

    RDS는 Engine은 알고 EngineVersion은 모르는 경우가 흔하다. 모름으로 접으면
    938블록이 전부 미결로 남아 아무것도 못 답한다. 실측: Engine만 줘도
    후보가 67개로 좁혀지고 "67가지 전부 불가"라는 확정 답이 나온다.
    """
    from capacitykb.model import Constraint
    from capacitykb.query import _condition_holds

    c = Constraint(
        type_id="aws::AWS::RDS::DBInstance", property="DBInstanceClass",
        kind="enum", value=["db.t3.medium"], evidence="cfn-lint-conditional",
        conditions=(
            {"property": "Engine", "op": "eq", "value": "aurora-mysql"},
            {"property": "EngineVersion", "op": "matches", "value": r"^10\."},
        ),
    )
    # 엔진이 다르다 → 버전을 몰라도 해당 없음
    assert _condition_holds(c, {"Engine": "postgres"}) is False
    # 엔진은 맞고 버전은 모른다 → 모름
    assert _condition_holds(c, {"Engine": "aurora-mysql"}) is None
    # 둘 다 맞다 → 성립
    assert _condition_holds(c, {"Engine": "aurora-mysql", "EngineVersion": "10.11.2"}) is True
    # 버전이 안 맞는다 → 해당 없음
    assert _condition_holds(c, {"Engine": "aurora-mysql", "EngineVersion": "8.0"}) is False


def test_broken_regex_is_unknown_not_false() -> None:
    """정규식이 파이썬에서 안 돌면 **모른다**로 둔다.

    False로 치면 아는 제약을 통째로 버리고, True로 치면 엉뚱한 조건의 한도를
    적용한다. 실측상 이 데이터셋에 컴파일 안 되는 정규식이 205건 있다.
    """
    from capacitykb.query import _one_condition

    cond = {"property": "V", "op": "matches", "value": "*{1,2}"}
    assert _one_condition(cond, {"V": "x"}) is None
