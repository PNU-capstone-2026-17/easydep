"""Azure bicep-types → 속성 제약 추출.

소스는 graphkb와 같은 `bicep-types-az`이고, 여기서는 관계가 아니라 **제약**을 뽑는다:
- `IntegerType`의 `minValue`/`maxValue` → min/max
- `StringType`의 `minLength`/`maxLength`/`pattern` → 길이/패턴
- `ArrayType`의 `minLength`/`maxLength` → min_items/max_items
- `UnionType`(전부 StringLiteralType) → enum
- 프로퍼티 `flags` 비트 → required(1) / mutability=read_only(2)

**주의 — flags 8(DeployTimeConstant)은 불변성이 아니다.** name/type/apiVersion에만
붙는 배포 시점 상수 표시이므로 mutability로 쓰면 안 된다. 실측상 bicep에는 CFN의
createOnlyProperties에 해당하는 불변 정보가 없다.

실측상 제약이 붙은 스칼라로 resolve되는 프로퍼티는 2.39%뿐이고, `diskSizeGB` 같은
간판 필드에는 제약이 없다. 그래도 ContainerService/Network 쪽은 값이 잘 붙어 있다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from capacitykb.model import CapacitySet, Constraint
from kbcommon.fetch import fetch_cached

DEFAULT_BASE_URL = "https://raw.githubusercontent.com/Azure/bicep-types-az/main/generated"
DEFAULT_PROVIDERS = (
    "microsoft.network",
    "microsoft.compute",
    "microsoft.containerservice",
)

EVIDENCE_TYPE = "bicep-type"
EVIDENCE_FLAGS = "bicep-flags"

_FLAG_REQUIRED = 1
_FLAG_READONLY = 2
_MAX_DEPTH = 24

# bicep 스칼라 타입의 제약 필드 → 우리 모델의 제약 종류
_STRING_KEYWORDS = {"minLength": "min_length", "maxLength": "max_length", "pattern": "pattern"}
_INTEGER_KEYWORDS = {"minValue": "min", "maxValue": "max"}
_ARRAY_KEYWORDS = {"minLength": "min_items", "maxLength": "max_items"}


def _is_preview(version: str) -> bool:
    return "preview" in version.lower()


def _version_better(candidate: str, current: str) -> bool:
    """비-preview 우선, 같은 등급이면 사전순(날짜형이라 사전순=시간순) 최신."""
    if _is_preview(candidate) != _is_preview(current):
        return _is_preview(current)
    return candidate > current


def select_latest(index: dict) -> dict[str, tuple[str, str]]:
    """index.json에서 타입별 최신 안정 버전을 고른다.

    index.json에는 같은 타입이 대소문자 변형으로 중복 등재돼 있어
    (virtualNetworks vs virtualnetworks) 소문자 키로 합친다.

    Returns:
        {타입명: (버전, types.json 상대경로)}
    """
    by_lower: dict[str, tuple[str, str, str]] = {}
    for key, ref in index.get("resources", {}).items():
        type_name, _, version = key.partition("@")
        rel_path = ref.get("$ref", "").split("#")[0]
        if not type_name or not version or not rel_path:
            continue
        lowered = type_name.lower()
        current = by_lower.get(lowered)
        if current is None or _version_better(version, current[0]):
            by_lower[lowered] = (version, rel_path, type_name)
    return {name: (version, rel_path) for version, rel_path, name in by_lower.values()}


def extract_constraints(
    capacity: CapacitySet, types_arr: list[dict], *, stats: Counter | None = None
) -> None:
    """types.json 한 파일에서 제약 레코드를 뽑아 capacity에 더한다."""
    counters = stats if stats is not None else Counter()

    def deref(ref: dict) -> tuple[int, dict]:
        index = int(ref["$ref"].rsplit("/", 1)[-1])
        return index, types_arr[index]

    def enum_values(entry: dict) -> list[str] | None:
        """UnionType이 전부 StringLiteralType이면 enum으로 본다."""
        elements = entry.get("elements")
        if not isinstance(elements, list) or not elements:
            return None
        values: list[str] = []
        for ref in elements:
            if not (isinstance(ref, dict) and "$ref" in ref):
                return None
            _, member = deref(ref)
            if member.get("$type") != "StringLiteralType":
                return None
            values.append(member.get("value"))
        return values if len(values) >= 2 else None

    def add(type_id: str, prop: str, kind: str, value, evidence: str, vtype: str | None) -> None:
        capacity.add_constraint(
            Constraint(
                type_id=type_id,
                property=prop,
                kind=kind,
                value=value,
                evidence=evidence,
                confidence=1.0,
                value_type=vtype,
            )
        )
        counters[f"{evidence}:{kind}"] += 1

    def describe_scalar(type_id: str, prop: str, entry: dict) -> None:
        kind_of = entry.get("$type")
        if kind_of == "IntegerType":
            for keyword, kind in _INTEGER_KEYWORDS.items():
                if keyword in entry:
                    add(type_id, prop, kind, entry[keyword], EVIDENCE_TYPE, "integer")
        elif kind_of == "StringType":
            for keyword, kind in _STRING_KEYWORDS.items():
                if keyword in entry:
                    add(type_id, prop, kind, entry[keyword], EVIDENCE_TYPE, "string")
        elif kind_of == "ArrayType":
            for keyword, kind in _ARRAY_KEYWORDS.items():
                if keyword in entry:
                    add(type_id, prop, kind, entry[keyword], EVIDENCE_TYPE, "array")
        elif kind_of == "UnionType":
            values = enum_values(entry)
            if values is not None:
                add(type_id, prop, "enum", values, EVIDENCE_TYPE, "string")

    def walk(type_id: str, entry: dict, path: str, *, depth: int, visited: frozenset[int]) -> None:
        if depth > _MAX_DEPTH or not isinstance(entry, dict):
            return
        properties = entry.get("properties")
        if entry.get("$type") == "DiscriminatedObjectType":
            properties = entry.get("baseProperties")
        if not isinstance(properties, dict):
            return

        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            prop_path = f"{path}.{name}" if path else name
            flags = prop.get("flags", 0)

            if flags & _FLAG_REQUIRED:
                add(type_id, prop_path, "required", True, EVIDENCE_FLAGS, None)
            if flags & _FLAG_READONLY:
                # 읽기 전용은 설정 대상이 아니므로 기록만 하고 내부로 내려가지 않는다.
                # (flags 8 = DeployTimeConstant는 불변성이 아니므로 여기서 다루지 않는다)
                add(type_id, prop_path, "mutability", "read_only", EVIDENCE_FLAGS, None)
                continue

            type_ref = prop.get("type")
            if not (isinstance(type_ref, dict) and "$ref" in type_ref):
                continue
            index, target = deref(type_ref)
            describe_scalar(type_id, prop_path, target)

            nested = target
            if target.get("$type") == "ArrayType":
                item_ref = target.get("itemType")
                if isinstance(item_ref, dict) and "$ref" in item_ref:
                    index, nested = deref(item_ref)
            if nested.get("$type") in ("ObjectType", "DiscriminatedObjectType") and index not in visited:
                walk(type_id, nested, prop_path, depth=depth + 1, visited=visited | {index})

    for entry in types_arr:
        if not isinstance(entry, dict) or entry.get("$type") != "ResourceType":
            continue
        name = entry.get("name")
        body_ref = entry.get("body")
        if not isinstance(name, str) or not isinstance(body_ref, dict):
            continue
        type_id = f"azure::{name.split('@')[0]}"
        index, body = deref(body_ref)
        walk(type_id, body, "", depth=0, visited=frozenset({index}))


def _fetch_relative(base: str, rel_path: str, *, refresh: bool) -> Path:
    """base가 로컬 디렉터리면 그 안에서 찾고, 아니면 URL로 조립해 받는다."""
    local = Path(base) / rel_path
    if local.exists():
        return local
    url = f"{base.rstrip('/')}/{rel_path}"
    return fetch_cached(url, "azure-" + rel_path.replace("/", "_"), refresh=refresh)


def build(
    output: Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
    refresh: bool = False,
) -> CapacitySet:
    """인덱스/타입 파일을 받아 파싱하고 output에 저장한 뒤 결과를 반환한다."""
    index_path = _fetch_relative(base_url, "index.json", refresh=refresh)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    latest = select_latest(index)

    wanted = {p.lower() for p in providers}
    rel_paths = sorted(
        {
            rel_path
            for type_name, (_version, rel_path) in latest.items()
            if type_name.split("/", 1)[0].lower() in wanted
        }
    )

    capacity = CapacitySet()
    stats: Counter = Counter()
    for rel_path in rel_paths:
        try:
            types_path = _fetch_relative(base_url, rel_path, refresh=refresh)
            types_arr = json.loads(types_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — 한 파일 실패가 전체를 막지 않게
            print(f"경고: types.json 처리 실패, 건너뜀 — {rel_path}: {exc}", file=sys.stderr)
            continue
        extract_constraints(capacity, types_arr, stats=stats)

    capacity.save(output)
    by_evidence: Counter = Counter(c.evidence for c in capacity.constraints)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(
        f"azure: 제약 {len(capacity.constraints)}개 ({summary}; "
        f"types.json {len(rel_paths)}개) → {output}"
    )
    return capacity
