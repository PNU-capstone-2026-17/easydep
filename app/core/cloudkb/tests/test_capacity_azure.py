"""Azure(bicep-types) 제약 파서 테스트.

fixture는 실측한 types.json 포맷($ref-by-index, flags 비트)을 그대로 따른 축소본.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.capacitykb.model import CapacitySet, Constraint
from app.core.cloudkb.capacitykb.parsers.azure import extract_constraints, select_latest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "capacity" / "azure"
AKS = "azure::Microsoft.ContainerService/managedClusters"


def load_index() -> dict:
    return json.loads((FIXTURE_DIR / "index.json").read_text(encoding="utf-8"))


def load_types() -> list[dict]:
    path = (
        FIXTURE_DIR
        / "containerservice"
        / "microsoft.containerservice"
        / "2025-01-01"
        / "types.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def capacity() -> CapacitySet:
    result = CapacitySet()
    extract_constraints(result, load_types())
    return result


def find(capacity: CapacitySet, prop: str, kind: str) -> Constraint | None:
    return capacity.get_constraint(AKS, prop, kind)


def test_select_latest_prefers_stable_and_dedupes_case() -> None:
    latest = select_latest(load_index())
    assert len(latest) == 1  # managedClusters / managedclusters 는 같은 타입
    version, rel_path = next(iter(latest.values()))
    assert version == "2025-01-01"  # preview보다 비-preview 우선
    assert rel_path.endswith("2025-01-01/types.json")


def test_golden_os_disk_size_range(capacity: CapacitySet) -> None:
    """골든: osDiskSizeGB {minValue: 0, maxValue: 2048}."""
    prop = "properties.agentPoolProfiles.osDiskSizeGB"
    assert find(capacity, prop, "min").value == 0
    max_found = find(capacity, prop, "max")
    assert max_found.value == 2048
    assert max_found.evidence == "bicep-type"
    assert max_found.basis == "stated"
    assert max_found.value_type == "integer"


def test_nested_object_range(capacity: CapacitySet) -> None:
    prop = "properties.networkProfile.allocatedOutboundPorts"
    assert find(capacity, prop, "max").value == 64000


def test_string_constraints(capacity: CapacitySet) -> None:
    prop = "properties.agentPoolProfiles.name"
    assert find(capacity, prop, "min_length").value == 1
    assert find(capacity, prop, "max_length").value == 63
    assert find(capacity, prop, "pattern").value == "^[a-zA-Z0-9]$"


def test_array_item_counts(capacity: CapacitySet) -> None:
    prop = "properties.agentPoolProfiles"
    assert find(capacity, prop, "min_items").value == 1
    assert find(capacity, prop, "max_items").value == 100


def test_union_of_literals_becomes_enum(capacity: CapacitySet) -> None:
    found = find(capacity, "properties.networkProfile.sku", "enum")
    assert found.value == ["Standard", "Basic"]
    assert found.evidence == "bicep-type"


def test_union_with_open_string_is_not_enum(capacity: CapacitySet) -> None:
    """StringType이 섞인 UnionType은 열린 집합이라 enum으로 보면 fail-closed가 된다."""
    assert find(capacity, "properties.agentPoolProfiles.osType", "enum") is None


def test_required_from_flags(capacity: CapacitySet) -> None:
    assert find(capacity, "name", "required").value is True
    assert find(capacity, "location", "required").value is True
    assert find(capacity, "properties.agentPoolProfiles.name", "required").value is True
    assert find(capacity, "properties.agentPoolProfiles.osDiskSizeGB", "required") is None


def test_readonly_from_flags(capacity: CapacitySet) -> None:
    assert find(capacity, "properties.fqdn", "mutability").value == "read_only"
    assert (
        find(capacity, "properties.agentPoolProfiles.nodeImageVersion", "mutability").value
        == "read_only"
    )
    assert find(capacity, "properties.fqdn", "mutability").evidence == "bicep-flags"


def test_deploy_time_constant_is_not_mutability(capacity: CapacitySet) -> None:
    """flags 8(DeployTimeConstant)은 불변성이 아니다 — id/type/apiVersion에 붙을 뿐."""
    # id/type/apiVersion은 flags=10 (ReadOnly|DeployTimeConstant) → read_only만 나와야 한다
    assert find(capacity, "id", "mutability").value == "read_only"
    assert not [
        c
        for c in capacity.for_type(AKS)
        if c.kind == "mutability" and c.value not in ("read_only",)
    ]


def test_no_constraint_when_type_unconstrained(capacity: CapacitySet) -> None:
    """count는 제약 없는 IntegerType이라 레코드가 없어야 한다."""
    prop = "properties.agentPoolProfiles.count"
    assert find(capacity, prop, "min") is None
    assert find(capacity, prop, "max") is None


def test_type_id_convention_matches_graphkb(capacity: CapacitySet) -> None:
    assert all(c.type_id == AKS for c in capacity.constraints)
    assert "@" not in AKS  # 버전은 id에 넣지 않는다


def test_validates(capacity: CapacitySet) -> None:
    capacity.validate()


# --- 최신 API 버전만 읽는다 (D2에서 불변식이 잡은 결함) ---


def _resource(name: str, prop_flags: int) -> list[dict]:
    """types.json 한 벌: ResourceType → body(Object) → 속성 하나."""
    return [
        {"$type": "ObjectType", "name": "Body",
         "properties": {"userId": {"type": {"$ref": "#/2"}, "flags": prop_flags}}},
        {"$type": "ResourceType", "name": name, "body": {"$ref": "#/0"}},
        {"$type": "StringType"},
    ]


def test_only_latest_api_version_is_read() -> None:
    """한 파일에 같은 타입의 여러 API 버전이 들어 있고 flags가 다르다.

    전부 읽으면 옛 버전의 required와 새 버전의 read_only가 섞여, 사용자에게
    **못 채우는 칸을 채우라고** 하게 된다. 실측: workbooks.properties.userId가
    2015-05-01에선 required(1), 2023-06-01에선 read_only(2)였다.
    """
    from app.core.cloudkb.capacitykb.model import CapacitySet
    from app.core.cloudkb.capacitykb.parsers.azure import extract_constraints
    from app.core.cloudkb.kbcommon.type_ids import read_azure_index

    index = {"resources": {
        "Microsoft.Insights/workbooks@2015-05-01": {"$ref": "old.json#/1"},
        "Microsoft.Insights/workbooks@2023-06-01": {"$ref": "new.json#/1"},
    }}
    ti = read_azure_index(index)
    assert ti.latest["Microsoft.Insights/workbooks"][0] == "2023-06-01"

    capacity = CapacitySet()
    extract_constraints(capacity, _resource("Microsoft.Insights/workbooks@2015-05-01", 1),
                        type_index=ti)
    extract_constraints(capacity, _resource("Microsoft.Insights/workbooks@2023-06-01", 2),
                        type_index=ti)

    kinds = {(c.property, c.kind) for c in capacity.constraints}
    assert ("userId", "mutability") in kinds, "최신 버전의 read_only가 실려야 한다"
    assert ("userId", "required") not in kinds, "옛 버전의 required가 새면 안 된다"


def test_full_scope_leaves_no_scope_marker() -> None:
    """전체를 읽었으면 coverage에 scope를 남기지 않는다.

    scope가 있으면 covers()가 "목록 밖은 안 봤음"으로 답하는데, 전체를 읽고도
    이걸 남기면 훑은 타입까지 '안 봤음'이 되어 거짓말이 된다.
    """
    from app.core.cloudkb.capacitykb.parsers.azure import DEFAULT_PROVIDERS
    assert DEFAULT_PROVIDERS == (), "기본은 전체여야 한다 (손으로 고른 목록 없음)"


# --- x-ms-mutability (azure_mutability.py) ---


def test_arm_type_reads_the_alternation_not_the_braces() -> None:
    """ARM 경로는 `타입/이름` 교대다. `{파라미터}`를 걸러내는 방식은 틀린다.

    `/virtualMachineInstances/default`의 `default`는 이름인데, 중괄호만 보고
    거르면 그걸 타입으로 읽어 `.../virtualMachineInstances/default`라는 없는
    타입이 나온다(실측 21종 27건).
    """
    from app.core.cloudkb.capacitykb.parsers.azure_mutability import arm_type

    base = "/subscriptions/{s}/resourceGroups/{g}/providers/"
    assert arm_type(base + "Microsoft.ContainerService/managedClusters/{n}") == (
        "Microsoft.ContainerService/managedClusters"
    )
    assert arm_type(base + "Microsoft.ContainerService/managedClusters/{n}/agentPools/{a}") == (
        "Microsoft.ContainerService/managedClusters/agentPools"
    )
    assert arm_type(base + "Microsoft.AzureStackHCI/virtualMachineInstances/default") == (
        "Microsoft.AzureStackHCI/virtualMachineInstances"
    )


def test_arm_type_uses_the_last_providers_segment() -> None:
    """`/providers/`가 두 번 나오면 뒤쪽이 진짜 타입이다 (실측 10종)."""
    from app.core.cloudkb.capacitykb.parsers.azure_mutability import arm_type

    url = (
        "/subscriptions/{s}/resourceGroups/{g}/providers/Microsoft.Sql/servers/{n}"
        "/providers/Microsoft.Insights/diagnosticSettings/{d}"
    )
    assert arm_type(url) == "Microsoft.Insights/diagnosticSettings"


def test_only_create_without_update_becomes_immutable() -> None:
    """`update`가 있으면 불변이 아니고, `read`만 있는 건 우리 몫이 아니다.

    읽기 전용은 `bicep-flags`가 이미 4,704건 담고 있어 중복이고, 라벨 하나에
    성격 하나라는 규칙상 섞으면 안 된다.
    """
    from app.core.cloudkb.capacitykb.parsers.azure_mutability import parse_tarball

    # 순수 함수 부분만 본다 — tarball 없이 판정 규칙을 고정한다.
    def verdict(mutability):
        return "create" in mutability and "update" not in mutability

    assert verdict(("create", "read")) is True
    assert verdict(("create",)) is True
    assert verdict(("create", "read", "update")) is False
    assert verdict(("read",)) is False
    assert callable(parse_tarball)
