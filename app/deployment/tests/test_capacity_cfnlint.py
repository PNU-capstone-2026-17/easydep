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
    assert got.constraints[0].condition["value"] == "us-east-1"
    assert report.skipped_all == 1, "건너뛴 사실을 세어야 한다"
    assert not [c for c in got.constraints if c.value == []]


def test_empty_region_list_is_not_stored(tmp_path: Path) -> None:
    """빈 목록은 '아무것도 못 쓴다'가 아니라 '데이터가 없다'로 본다."""
    got, _ = parse_wheel(
        _wheel(tmp_path, {"us-east-1": {"enum": []},
                          "us-west-2": {"enum": ["t3.micro"]}}),
        cfn_schemas=SCHEMAS,
    )
    assert [c.condition["value"] for c in got.constraints] == ["us-west-2"]


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
    assert ok.startswith("가능") and no.startswith("불가")


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
    assert "1가지에서 가능" in text
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
    assert "50개 중 하나" in text
    assert text.count("t4.micro") == 0 or len(text) < 400, "목록을 통째로 찍으면 안 된다"
