"""Azure(bicep-types) 제약 파서 테스트.

fixture는 실측한 types.json 포맷($ref-by-index, flags 비트)을 그대로 따른 축소본.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capacitykb.model import CapacitySet, Constraint
from capacitykb.parsers.azure import extract_constraints, select_latest

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
