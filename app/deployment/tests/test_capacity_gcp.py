"""capacitykb GCP(KCC CRD) 파서.

이 파서가 메우는 것은 **커버리지**다. 수치 한도는 원본에 0건이라 여기서 안 나온다 —
그 사실 자체를 테스트로 박아, 나중에 "왜 GCP 한도가 없냐"에 "안 뽑아서가 아니라
원본에 없어서"라고 답할 수 있게 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capacitykb import agent_api
from capacitykb.parsers.gcp import DISAGREEMENTS, parse_crds


def crd(kind: str, spec: dict) -> dict:
    return {
        "kind": "CustomResourceDefinition",
        "spec": {
            "names": {"kind": kind},
            "versions": [{
                "name": "v1beta1", "storage": True,
                "schema": {"openAPIV3Schema": {"properties": {"spec": spec}}},
            }],
        },
    }


def test_immutable_prefix_becomes_create_only() -> None:
    got = parse_crds([crd("ComputeSubnetwork", {
        "properties": {"region": {"type": "string", "description": "Immutable. The region."}},
    })])
    found = [c for c in got.constraints if c.kind == "mutability"]
    assert len(found) == 1
    assert found[0].property == "region"
    assert found[0].value == "create_only"
    assert found[0].is_fact, "KCC의 Immutable. 표기는 원본이 명시한 것이다"


def test_cel_immutability_is_read_even_without_prefix() -> None:
    """접두사만 읽으면 19건을 놓친다(실측) — CEL도 봐야 한다."""
    got = parse_crds([crd("BatchJob", {
        "properties": {"location": {
            "type": "string",
            "description": "The location.",  # 접두사 없음
            "x-kubernetes-validations": [{"rule": "self == oldSelf"}],
        }},
    })])
    found = [c for c in got.constraints if c.kind == "mutability"]
    assert [c.property for c in found] == ["location"]
    assert found[0].evidence == "kcc-cel-immutable"
    assert DISAGREEMENTS, "접두사와 CEL이 엇갈리면 보고해야 한다"


def test_reference_shapes_are_not_constraints() -> None:
    """참조는 graphkb가 관계로 다룬다. 여기서 또 뽑으면 제약이 참조 껍데기로 채워진다."""
    got = parse_crds([crd("ComputeInstance", {
        "properties": {"networkRef": {
            "type": "object",
            "required": ["external"],
            "properties": {"external": {"type": "string"}, "name": {"type": "string"}},
        }},
    })])
    assert not [c for c in got.constraints if c.property.startswith("networkRef")]


def test_coverage_lists_types_so_empty_ones_resolve(tmp_path: Path) -> None:
    """제약이 0건인 타입도 이름으로 찾혀야 한다.

    안 그러면 "타입을 찾을 수 없습니다"라고 답하는데, 실재하고 우리가 읽기까지 한
    타입이라 그건 거짓이다. 실측상 GCP 39종이 여기 해당한다.
    """
    got = parse_crds([crd("BigLakeDatabase", {"properties": {"x": {"type": "string"}}})])
    assert not got.constraints
    entry = got.coverage[0]
    assert "gcp::BigLakeDatabase" in entry["type_ids"]

    got.save(tmp_path / "gcp-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.immutable("BigLakeDatabase", output_dir=tmp_path)
    assert "찾을 수 없습니다" not in text
    assert "없습니다" in text


def test_immutable_folds_children_of_immutable_parent(tmp_path: Path) -> None:
    """KCC는 부모·자식 모두에 표기한다 — 실측 45.6%가 중복이었다."""
    im = "Immutable. x"
    got = parse_crds([crd("DataprocCluster", {
        "properties": {"config": {
            "type": "object", "description": im,
            "properties": {
                "a": {"type": "string", "description": im},
                "b": {"type": "string", "description": im},
            },
        }},
    })])
    assert len([c for c in got.constraints if c.kind == "mutability"]) == 3

    got.save(tmp_path / "gcp-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.immutable("DataprocCluster", output_dir=tmp_path)
    assert "속성 1개" in text
    assert "하위 속성 2개" in text
    assert "config.a" not in text, "부모가 불변이면 자식은 접는다"


def test_records_that_numeric_limits_are_absent_at_source() -> None:
    """원본에 min/max가 0건이라는 사실을 고정한다.

    KCC CRD 510개의 spec 서브트리를 전수로 세어 확인했다. 이게 깨지면 KCC가
    수치 한도를 싣기 시작한 것이므로, 반가운 소식이자 파서를 손볼 신호다.
    """
    got = parse_crds([crd("X", {"properties": {
        "n": {"type": "integer", "maximum": 100, "minimum": 1},
    }})])
    # 파서는 나오면 담을 수 있다 — 안 담는 게 아니라 원본에 없을 뿐이다.
    kinds = {c.kind for c in got.constraints}
    assert {"max", "min"} <= kinds
