"""AWS 실제 한도 — Price List × botocore 교차 검증 (D3).

여기서 `condition`이 처음 실제로 쓰인다. 요지는 **봉투 붕괴를 없애는 것**이다:
볼륨 크기 한도는 종류마다 달라서 min/max 한 쌍으로는 못 담고, 뭉개면
`standard` 볼륨 5,000 GiB처럼 불가능한 값이 통과한다.
"""

from __future__ import annotations

from pathlib import Path

from app.deployment.capacitykb import agent_api
from app.deployment.capacitykb.model import CapacitySet, Constraint
from app.deployment.capacitykb.parsers.aws_limits import VOLUME, cross_check, read_botocore, read_price_list

PRICE = {
    "products": {
        "A": {"productFamily": "Storage", "attributes": {
            "volumeApiName": "gp2", "maxVolumeSize": "16 TiB", "maxIopsvolume": "16000"}},
        "B": {"productFamily": "Storage", "attributes": {
            "volumeApiName": "gp3", "maxVolumeSize": "64 TiB", "maxIopsvolume": "80000"}},
        "C": {"productFamily": "Storage", "attributes": {
            "volumeApiName": "st1", "maxVolumeSize": "16 TiB",
            "maxIopsvolume": "500 - based on 1 MiB I/O size"}},
        "D": {"productFamily": "Compute Instance", "attributes": {"instanceType": "m5.large"}},
    }
}
BOTO = {"shapes": {"CreateVolumeRequest": {"members": {
    "Size": {"documentation": "<p>Valid sizes:</p><ul>"
             "<li>gp2: 1 - 16,384 GiB</li><li>gp3: 1 - 65,536 GiB</li>"
             "<li>st1 and sc1: 125 - 16,384 GiB</li></ul>"},
    "Iops": {"documentation": "<p>Valid ranges:</p><ul>"
             "<li>gp3: 3,000 ( default ) - 80,000 IOPS</li></ul>"},
}}}}


def test_units_are_converted() -> None:
    assert read_price_list(PRICE)["gp2"]["maxVolumeSize"] == "16 TiB"
    got, _ = cross_check(read_price_list(PRICE), read_botocore(BOTO))
    sizes = {(c.conditions[0]["value"], c.kind): c.value
             for c in got.constraints if c.property == "Size"}
    assert sizes[("gp2", "max")] == 16384, "16 TiB → 16,384 GiB"
    assert sizes[("gp3", "max")] == 65536


def test_shared_range_line_expands_to_each_type() -> None:
    """`st1 and sc1: 125 - 16,384` 한 줄이 두 종류를 가리킨다."""
    boto = read_botocore(BOTO)
    assert boto["Size"]["st1"] == (125, 16384)
    assert boto["Size"]["sc1"] == (125, 16384)


def test_parenthetical_default_does_not_break_range() -> None:
    """`3,000 ( default ) - 80,000`의 삽입구를 건너뛴다."""
    assert read_botocore(BOTO)["Iops"]["gp3"] == (3000, 80000)


def test_only_agreeing_values_are_stored() -> None:
    """두 소스가 다르면 담지 않는다 — 단일 미검증 소스가 '확신에 찬 오답'의 원인이었다."""
    bad = {"products": {"A": {"productFamily": "Storage", "attributes": {
        "volumeApiName": "gp2", "maxVolumeSize": "99 TiB"}}}}
    got, report = cross_check(read_price_list(bad), read_botocore(BOTO))
    assert not [c for c in got.constraints if c.conditions[0]["value"] == "gp2"]
    assert report.disagreed and report.disagreed[0][1] == "gp2"


def test_non_numeric_price_value_is_not_guessed() -> None:
    """`500 - based on 1 MiB I/O size`는 숫자가 아니다. 억지로 파싱하지 않는다."""
    got, _ = cross_check(read_price_list(PRICE), read_botocore(BOTO))
    assert not [c for c in got.constraints
                if c.property == "Iops" and c.conditions[0]["value"] == "st1"]


def test_condition_is_part_of_the_dedup_key() -> None:
    """조건이 키에 없으면 종류별 레코드가 하나로 접혀 도로 봉투가 된다."""
    capacity = CapacitySet()
    for vol, limit in (("gp2", 16384), ("gp3", 65536)):
        capacity.add_constraint(Constraint(
            type_id=VOLUME, property="Size", kind="max", value=limit,
            evidence="aws-cross-checked",
            conditions=({"property": "VolumeType", "op": "eq", "value": vol},)))
    assert len(capacity.constraints) == 2


def _out(tmp_path: Path) -> Path:
    got, _ = cross_check(read_price_list(PRICE), read_botocore(BOTO))
    got.coverage = [{"provider": "aws", "types": 1, "type_ids": [VOLUME]}]
    got.save(tmp_path / "aws-limits.json")
    agent_api._load_merged_cached.cache_clear()
    return tmp_path


def test_context_decides_which_limit_applies(tmp_path: Path) -> None:
    out = _out(tmp_path)
    assert agent_api.check(VOLUME, "Size", 30000, context={"VolumeType": "gp2"},
                           output_dir=out).startswith("not allowed:")
    assert agent_api.check(VOLUME, "Size", 30000, context={"VolumeType": "gp3"},
                           output_dir=out).startswith("allowed:")


def test_without_context_it_says_what_it_needs(tmp_path: Path) -> None:
    """조건을 모르면 '모른다'가 아니라 '무엇을 알려주면 되는지'를 말한다."""
    text = " ".join(agent_api.check(VOLUME, "Size", 30000, output_dir=_out(tmp_path)).split())
    assert "depends on the condition:" in text
    assert "VolumeType" in text
    assert "16384" in text and "65536" in text, "어느 조건에서 얼마인지 다 보여준다"


def test_unknown_volume_type_does_not_silently_pass(tmp_path: Path) -> None:
    """모르는 종류를 주면 어떤 조건도 성립하지 않는다 — 통과시키면 안 된다."""
    text = agent_api.check(VOLUME, "Size", 99999,
                           context={"VolumeType": "nope"}, output_dir=_out(tmp_path))
    assert not text.startswith("allowed:")
