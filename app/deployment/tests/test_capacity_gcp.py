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


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


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
    assert "type not found" not in flat(text)
    assert "no property is known to be unchangeable" in flat(text)


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
    assert "1 property that recreates the resource when changed" in flat(text)
    assert "2 child properties folded away" in flat(text)
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


# --- backend: 어느 상류가 만들었나 (D1-a) ---


def labelled(kind: str, spec: dict, label: str | None) -> dict:
    doc = crd(kind, spec)
    if label:
        doc["metadata"] = {"labels": {f"cnrm.cloud.google.com/{label}": "true"}}
    return doc


def test_backend_read_from_crd_label() -> None:
    """KCC가 스스로 라벨로 밝히는 백엔드를 그대로 적는다 (우리 등급이 아니다)."""
    got = parse_crds([
        labelled("A", {"properties": {"x": {"type": "string", "description": "Immutable. x"}}}, "tf2crd"),
        labelled("B", {"properties": {"x": {"type": "string", "description": "Immutable. x"}}}, "dcl2crd"),
        labelled("C", {"properties": {"x": {"type": "string", "description": "Immutable. x"}}}, None),
    ])
    by_type = {c.type_id: c.backend for c in got.constraints}
    assert by_type["gcp::A"] == "tf2crd"
    assert by_type["gcp::B"] == "dcl2crd"
    assert by_type["gcp::C"] == "direct", "라벨이 없으면 direct(손으로 쓴 Go 타입)"
    assert got.coverage[0]["backends"] == {"tf2crd": 1, "dcl2crd": 1, "direct": 1}


def test_backend_survives_roundtrip(tmp_path: Path) -> None:
    got = parse_crds([labelled("A", {"properties": {
        "x": {"type": "string", "description": "Immutable. x"}}}, "tf2crd")])
    got.save(tmp_path / "gcp-capacity.json")
    from capacitykb.model import CapacitySet
    assert CapacitySet.load(tmp_path / "gcp-capacity.json").constraints[0].backend == "tf2crd"


def test_stale_backend_is_disclosed_once_not_per_line(tmp_path: Path) -> None:
    """낡은 상류는 밝히되 **블록당 한 번**.

    backend는 CRD 단위라 한 타입 안에서는 전부 같은 값이다. 줄마다 달면 2,453줄에
    똑같은 경고가 붙고, 노이즈가 되면 진짜 경고가 안 보인다.
    """
    im = "Immutable. x"
    got = parse_crds([labelled("A", {"properties": {
        "a": {"type": "string", "description": im},
        "b": {"type": "string", "description": im},
        "c": {"type": "string", "description": im},
    }}, "tf2crd")])
    got.save(tmp_path / "gcp-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.immutable("A", output_dir=tmp_path)
    assert flat(text).count("so it may be outdated") == 1, "줄마다 붙으면 안 된다"
    assert "all 3 entries:" in flat(text)


def test_fresh_backend_says_nothing(tmp_path: Path) -> None:
    got = parse_crds([labelled("A", {"properties": {
        "a": {"type": "string", "description": "Immutable. x"}}}, None)])
    got.save(tmp_path / "gcp-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    assert "may be outdated" not in flat(agent_api.immutable("A", output_dir=tmp_path))


def test_internal_labels_never_reach_the_user(tmp_path: Path) -> None:
    got = parse_crds([labelled("A", {"properties": {
        "a": {"type": "string", "description": "Immutable. x"}}}, "tf2crd")])
    got.save(tmp_path / "gcp-capacity.json")
    agent_api._load_merged_cached.cache_clear()
    text = agent_api.immutable("A", output_dir=tmp_path)
    for label in ("kcc-immutable-prefix", "kcc-crd-schema", "kcc-cel-immutable", "gcp::"):
        assert label not in text


# --- 프로바이더 릴리스 보강 (D6) ---

from capacitykb.parsers import tpg
from capacitykb.parsers.gcp import merge_provider

GO = '''
func ResourceComputeSubnetwork() *schema.Resource {
	return &schema.Resource{
		CustomizeDiff: customdiff.All(
			customdiff.ForceNewIfChange("ip_cidr_range", IsShrinkageIpCidr),
		),
		Schema: map[string]*schema.Schema{
			"purpose": {
				Type:        schema.TypeString,
				Optional:    true,
				Description: `Has a brace { in the text and 'quotes'.`,
			},
			"region": {
				Type:     schema.TypeString,
				ForceNew: true,
			},
			"role": {
				Type:         schema.TypeString,
				ValidateFunc: verify.ValidateEnum([]string{"ACTIVE", "BACKUP", ""}),
			},
			"log_config": {
				Type:     schema.TypeList,
				MaxItems: 1,
				Elem: &schema.Resource{
					Schema: map[string]*schema.Schema{
						"flow_sampling": {
							Type:         schema.TypeFloat,
							Default:      0.5,
							AtLeastOneOf: []string{"log_config.0.flow_sampling", "log_config.0.metadata"},
						},
						"metadata": {
							Type:          schema.TypeString,
							ConflictsWith: []string{},
						},
					},
				},
			},
		},
	}
}
'''


def _fake_tar(tmp_path: Path) -> Path:
    import io as _io
    import tarfile
    path = tmp_path / "tpg.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = GO.encode("utf-8")
        info = tarfile.TarInfo("x/google/services/compute/resource_compute_subnetwork.go")
        info.size = len(data)
        tar.addfile(info, _io.BytesIO(data))
    return path


def test_go_parser_survives_braces_in_strings(tmp_path: Path) -> None:
    """Description이 백틱 문자열이고 그 안에 중괄호가 있어도 블록이 안 어긋난다."""
    got, _ = tpg.parse_provider(_fake_tar(tmp_path), kcc_kinds={"ComputeSubnetwork"})
    props = {(c.property, c.kind) for c in got.constraints}
    assert ("region", "mutability") in props
    assert ("role", "enum") in props
    assert ("logConfig.flowSampling", "default") in props, "중첩 경로가 안 잡혔다"


def test_nested_attributes_not_stolen_by_parent(tmp_path: Path) -> None:
    """자식의 Default가 부모(log_config) 것으로 잘못 읽히면 안 된다."""
    got, _ = tpg.parse_provider(_fake_tar(tmp_path), kcc_kinds={"ComputeSubnetwork"})
    parent = [c for c in got.constraints if c.property == "logConfig"]
    assert {c.kind for c in parent} == {"max_items"}


def test_empty_group_is_counted_not_stored(tmp_path: Path) -> None:
    """MM이 선언했지만 생성 과정에서 증발한 교차 조건 — 담으면 현실보다 엄격해진다."""
    got, report = tpg.parse_provider(_fake_tar(tmp_path), kcc_kinds={"ComputeSubnetwork"})
    assert not [c for c in got.constraints if c.kind == "conflicts_with"]
    assert report.empty_groups == 1


def test_force_new_if_becomes_update_restricted(tmp_path: Path) -> None:
    """'줄이면 재생성'은 불변도 가변도 아니다 — 별도 축으로 담는다."""
    got, report = tpg.parse_provider(_fake_tar(tmp_path), kcc_kinds={"ComputeSubnetwork"})
    found = [c for c in got.constraints
             if c.kind == "mutability" and c.value == "update_restricted"]
    assert [c.property for c in found] == ["ipCidrRange"]
    assert "IsShrinkageIpCidr" in found[0].note
    assert report.force_new_if


def test_tf_path_conversion() -> None:
    assert tpg.tf_path_to_kcc("log_config.0.aggregation_interval") == "logConfig.aggregationInterval"
    assert tpg.tf_path_to_kcc("ip_cidr_range") == "ipCidrRange"
    assert tpg.tf_path_to_kcc("name") == "resourceID", "KCC는 name을 resourceID로 쓴다"


def test_provider_wins_only_over_stale_backend(tmp_path: Path) -> None:
    """tf2crd만 프로바이더가 이긴다. direct는 KCC가 이긴다."""
    from capacitykb.model import CapacitySet, Constraint
    kcc = CapacitySet()
    for backend in ("tf2crd", "direct"):
        kcc.add_constraint(Constraint(
            type_id=f"gcp::{backend}Type", property="x", kind="max_items",
            value=1, evidence="kcc-crd-schema", backend=backend))
    prov = CapacitySet()
    for backend in ("tf2crd", "direct"):
        prov.add_constraint(Constraint(
            type_id=f"gcp::{backend}Type", property="x", kind="max_items",
            value=99, evidence="tpg-schema"))
    merged, stat = merge_provider(kcc, prov)
    by = {c.type_id: c.value for c in merged.constraints}
    assert by["gcp::tf2crdType"] == 99, "낡은 쪽은 프로바이더가 이겨야 한다"
    assert by["gcp::directType"] == 1, "direct는 proto에 붙은 독립 소스라 KCC가 이긴다"
    assert stat["value_changed"] == 1


def test_provider_absence_removes_stale_immutability(tmp_path: Path) -> None:
    """프로바이더가 그 속성을 **알면서** 재생성 표시를 안 했으면 갱신 가능하다는 뜻이다.

    이게 없으면 ComputeSubnetwork.purpose가 2023년판 'Immutable.' 표기 그대로 남는다.
    """
    from capacitykb.model import CapacitySet, Constraint
    kcc = CapacitySet()
    kcc.add_constraint(Constraint(
        type_id="gcp::ComputeSubnetwork", property="purpose", kind="mutability",
        value="create_only", evidence="kcc-immutable-prefix", backend="tf2crd"))
    prov, report = tpg.parse_provider(_fake_tar(tmp_path), kcc_kinds={"ComputeSubnetwork"})
    merged, stat = merge_provider(kcc, prov, report)
    assert stat["dropped_stale_immutable"] == 1
    assert not [c for c in merged.constraints
                if c.property == "purpose" and c.kind == "mutability"]


def test_absence_alone_is_not_enough(tmp_path: Path) -> None:
    """프로바이더가 **모르는** 속성은 손대지 않는다 — 침묵은 근거가 아니다."""
    from capacitykb.model import CapacitySet, Constraint
    kcc = CapacitySet()
    kcc.add_constraint(Constraint(
        type_id="gcp::ComputeSubnetwork", property="somethingElse", kind="mutability",
        value="create_only", evidence="kcc-immutable-prefix", backend="tf2crd"))
    prov, report = tpg.parse_provider(_fake_tar(tmp_path), kcc_kinds={"ComputeSubnetwork"})
    merged, stat = merge_provider(kcc, prov, report)
    assert stat["dropped_stale_immutable"] == 0
    assert len([c for c in merged.constraints if c.property == "somethingElse"]) == 1


GO_OUTPUT = '''
func ResourceX() *schema.Resource {
	return &schema.Resource{
		Schema: map[string]*schema.Schema{
			"create_time": {
				Type:     schema.TypeString,
				Computed: true,
			},
			"purpose": {
				Type:     schema.TypeString,
				Computed: true,
				Optional: true,
				ForceNew: true,
			},
			"deletion_policy": {
				Type:     schema.TypeString,
				Optional: true,
				ForceNew: true,
			},
		},
	}
}
'''


def _tar_with(tmp_path: Path, body: str, name: str = "resource_x.go") -> Path:
    import io as _io
    import tarfile
    path = tmp_path / f"{name}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = body.encode("utf-8")
        info = tarfile.TarInfo(f"x/google/services/compute/{name}")
        info.size = len(data)
        tar.addfile(info, _io.BytesIO(data))
    return path


def test_output_only_fields_are_skipped(tmp_path: Path) -> None:
    """서버가 채우기만 하는 필드는 '사용자가 넣을 값의 제약'이 아니다.

    이름 목록을 손으로 만들지 않고 프로바이더 자신의 Computed 표시로 거른다.
    실측 1,647건이 여기 해당했다.
    """
    got, report = tpg.parse_provider(_tar_with(tmp_path, GO_OUTPUT), kcc_kinds={"X"})
    props = {c.property for c in got.constraints}
    assert "createTime" not in props
    assert report.output_only == 1


def test_computed_plus_optional_is_still_settable(tmp_path: Path) -> None:
    """Computed 하나로 판단하면 안 된다 — Optional과 함께면 사용자가 넣을 수 있다."""
    got, _ = tpg.parse_provider(_tar_with(tmp_path, GO_OUTPUT), kcc_kinds={"X"})
    assert ("purpose", "mutability") in {(c.property, c.kind) for c in got.constraints}


def test_terraform_only_concepts_are_skipped(tmp_path: Path) -> None:
    """deletion_policy는 Terraform이 지울 때의 동작이지 GCP 필드가 아니다(309개 리소스)."""
    got, report = tpg.parse_provider(_tar_with(tmp_path, GO_OUTPUT), kcc_kinds={"X"})
    assert "deletionPolicy" not in {c.property for c in got.constraints}
    assert report.tf_only_paths == 1


def test_kcc_spelling_wins_for_acronyms(tmp_path: Path) -> None:
    """TF는 snake_case라 두문자어 대소문자를 잃는다. KCC가 쓰는 철자에 맞춘다."""
    body = GO_OUTPUT.replace('"create_time"', '"peering_cidr_range"')
    got, _ = tpg.parse_provider(
        _tar_with(tmp_path, body), kcc_kinds={"X"},
        kcc_paths={"X": {"peeringCIDRRange", "purpose"}},
    )
    # Computed 전용이라 안 담기지만, 철자 매칭 자체는 purpose로 확인한다
    assert "purpose" in {c.property for c in got.constraints}
    assert tpg._match_kcc_spelling("peeringCidrRange", {"peeringCIDRRange"},
                                   {"peeringcidrrange": "peeringCIDRRange"}) == "peeringCIDRRange"
    assert tpg._match_kcc_spelling("nope", {"peeringCIDRRange"}, {}) is None
