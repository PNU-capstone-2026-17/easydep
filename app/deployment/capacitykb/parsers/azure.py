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

**그 공백의 원인을 찾았다(2026-07-21).** 원본에는 있고 **생성기가 버린다.**
`azure-rest-api-specs`의 `x-ms-mutability: ["read","create"]`가 생성 불변성인데,
bicep 생성기는 이를 writable&readable로 접어 `flags: None`으로 만든다 —
`ObjectTypePropertyFlags`에 `Immutable` 멤버 자체가 없다. 우리 캐시에서 `x-ms-mutability`
출현은 **0건**이다.

> 다만 "bicep이 제약을 잃는다"는 일반화는 **틀렸다.** `pattern` 920 · `maxLength` 827 ·
> `minValue` 446 · `maxValue` 337은 그대로 있고 이 파서가 전부 소비한다. 잃는 건
> **불변성 하나**다. 상류를 볼 이유가 있다면 그 필드 때문이지 제약 일반 때문이 아니다.
> — `document/source-survey-2026-07-21.md`

실측상 제약이 붙은 스칼라로 resolve되는 프로퍼티는 2.39%뿐이고, `diskSizeGB` 같은
간판 필드에는 제약이 없다. 그래도 ContainerService/Network 쪽은 값이 잘 붙어 있다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from capacitykb.model import CapacitySet, Constraint
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.invariants import announce
from kbcommon.sources import SOURCES
from kbcommon.type_ids import AzureTypeIndex, make_type_id, read_azure_index

# graphkb/parsers/azure.py와 **같은 커밋**을 봐야 한다 (kbcommon/sources.py에서 관리).
DEFAULT_BASE_URL = SOURCES["bicep-types-az"].url
#: 기본은 **전체**다. 예전에는 네임스페이스 3개만 읽어 3,382종 중 279종(8.5%)만
#: 커버했는데, 그 목록은 우리가 손으로 고른 것이라 "왜 이 셋인가"에 답이 없었고
#: 나머지 3,103종은 물어보면 "안 봤음"이 나왔다. 전체라야 576개 파일이라
#: (일회성 100MB, 캐시됨) 굳이 고를 이유가 없다. 좁히려면 --providers.
DEFAULT_PROVIDERS: tuple[str, ...] = ()

EVIDENCE_TYPE = "bicep-type"
EVIDENCE_FLAGS = "bicep-flags"

_FLAG_REQUIRED = 1
_FLAG_READONLY = 2
_MAX_DEPTH = 24

# bicep 스칼라 타입의 제약 필드 → 우리 모델의 제약 종류
_STRING_KEYWORDS = {"minLength": "min_length", "maxLength": "max_length", "pattern": "pattern"}
_INTEGER_KEYWORDS = {"minValue": "min", "maxValue": "max"}
_ARRAY_KEYWORDS = {"minLength": "min_items", "maxLength": "max_items"}


def select_latest(index: dict) -> dict[str, tuple[str, str]]:
    """index.json에서 타입별 최신 안정 버전을 고른다.

    Returns:
        {타입명: (버전, types.json 상대경로)}
    """
    return read_azure_index(index).latest


def extract_constraints(
    capacity: CapacitySet,
    types_arr: list[dict],
    *,
    stats: Counter | None = None,
    type_index: AzureTypeIndex | None = None,
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

            # 읽기 전용이면 required 비트가 켜져 있어도 싣지 않는다. 둘이 같이 켜진
            # 것은 "응답에 늘 들어 있다"는 뜻이지 "네가 채워야 한다"가 아니다 —
            # 그대로 옮기면 사용자에게 채울 수 없는 칸을 채우라고 하게 된다.
            # CFN의 readOnlyProperties ∩ definitions.required와 같은 모양이다.
            if flags & _FLAG_REQUIRED and not flags & _FLAG_READONLY:
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
        # **대표 표기로 정규화한 뒤** id를 만든다. types.json은 API 버전마다
        # 표기가 달라서(Microsoft.Compute vs microsoft.Compute) 그대로 쓰면
        # 같은 타입이 두 개의 id로 갈리고 graphkb와 조인이 깨진다.
        bare, _, version = name.partition("@")

        # **최신 안정 버전만 읽는다.** 한 types.json에는 같은 타입의 여러 API 버전이
        # 들어 있고, 버전마다 flags가 다르다. 전부 읽으면 옛 버전의 `required`와
        # 새 버전의 `read_only`가 한 레코드 집합에 섞여, 사용자에게 **못 채우는 칸을
        # 채우라고** 하게 된다. 실측: workbooks.properties.userId가 2015-05-01에선
        # required, 2023-06-01에선 read_only다. 불변식이 이걸 잡아 쓰기를 거부했다.
        if type_index is not None:
            chosen = type_index.latest.get(type_index.canonical(bare))
            if chosen is not None and version and chosen[0] != version:
                counters["skipped:old-version"] += 1
                continue
        type_id = type_index.type_id(bare) if type_index else make_type_id("azure", bare)
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
    type_index = read_azure_index(index)
    latest = type_index.latest

    wanted = {p.lower() for p in providers}  # 비어 있으면 전체
    rel_paths = sorted(
        {
            rel_path
            for type_name, (_version, rel_path) in latest.items()
            if not wanted or type_name.split("/", 1)[0].lower() in wanted
        }
    )
    print(f"azure: types.json {len(rel_paths):,}개 읽는 중"
          f"{'' if not wanted else f' (네임스페이스 {len(wanted)}개로 한정)'}")

    capacity = CapacitySet()
    stats: Counter = Counter()
    read_paths = [index_path]
    for rel_path in rel_paths:
        try:
            types_path = _fetch_relative(base_url, rel_path, refresh=refresh)
            types_arr = json.loads(types_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — 한 파일 실패가 전체를 막지 않게
            print(f"경고: types.json 처리 실패, 건너뜀 — {rel_path}: {exc}", file=sys.stderr)
            continue
        read_paths.append(types_path)
        extract_constraints(capacity, types_arr, stats=stats, type_index=type_index)

    capacity.provenance = [describe_source_set(read_paths, "bicep-types-az")]
    # **무엇을 훑었는지 남긴다.** 이게 없으면 안 훑은 타입의 제약을 물었을 때
    # "제약 없음"이라고 답하게 된다 — Azure 3,382종 중 훑는 건 이 3개 네임스페이스뿐이다.
    entry: dict = {
        "provider": "azure",
        "types": len({c.type_id for c in capacity.constraints}),
    }
    if wanted:
        # scope가 있으면 covers()가 "목록 밖은 안 봤음"으로 답한다. 전체를 읽었을 때
        # 이걸 남기면 훑은 타입까지 '안 봤음'이 되므로, 좁혔을 때만 적는다.
        entry["scope"] = sorted(providers)
        entry["note"] = (
            "bicep-types의 이 네임스페이스만 읽었다. 목록 밖 타입은 "
            "'제약 없음'이 아니라 '안 봤음'이다."
        )
    else:
        entry["note"] = "bicep-types 전체를 읽는다 (최신 안정 버전 기준)."
    capacity.coverage = [entry]
    announce(capacity.save(output), "capacitykb/azure")
    by_evidence: Counter = Counter(c.evidence for c in capacity.constraints)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(
        f"azure: 제약 {len(capacity.constraints)}개 ({summary}; "
        f"types.json {len(rel_paths)}개) → {output}"
    )
    return capacity
