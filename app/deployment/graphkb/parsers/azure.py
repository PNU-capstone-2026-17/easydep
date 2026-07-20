"""Azure 파서: bicep-types-az 타입 인덱스에서 벤더 레이어 그래프 추출.

소스는 github.com/Azure/bicep-types-az의 generated/index.json(전체 타입 목록)과
프로바이더별 types.json(타입 상세)이다.

핵심 사실 (실측):
- ARM 타입명은 계층적이다: "Microsoft.Network/virtualNetworks/subnets"는
  virtualNetworks의 자식 → 이름만으로 contained_in 엣지를 얻는다
  (evidence=arm-hierarchy, 정의상 확실하므로 confidence 1.0).
- swagger의 arm-id 참조 메타데이터는 bicep 타입 생성 과정에서 소실된다.
  대신 프로퍼티 타입이 다른 리소스의 인라인 객체(예: "CommonSubnet")로
  $ref 연결되는 구조가 남으므로, ObjectType 이름을 정규화(Common 접두사 제거,
  단수/복수 보정)해 리소스 타입과 유일 매칭되면 참조 엣지로 본다
  (evidence=bicep-ref, confidence 0.8).
- 그 외 `*Id` 문자열 프로퍼티는 CFN과 같은 속성명 휴리스틱으로 보강한다
  (evidence=heuristic, confidence 0.6).

전체 노드/계층은 index.json 한 파일로 커버하고, 참조 엣지는 용량 문제로
선택된 프로바이더(기본: network/compute/containerservice)의 types.json만
내려받아 추출한다. --providers로 확장 가능.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from graphkb.fetch import fetch_cached
from graphkb.model import Edge, Graph, Node
from kbcommon.fetch import describe_source_set
from kbcommon.sources import SOURCES

# 고정 ref는 kbcommon/sources.py 한 곳에서 관리한다 — 같은 소스를 capacitykb도 쓰므로
# 양쪽이 따로 들고 있으면 조용히 다른 커밋을 보게 된다.
DEFAULT_BASE_URL = SOURCES["bicep-types-az"].url
DEFAULT_PROVIDERS = ("microsoft.network", "microsoft.compute", "microsoft.containerservice")

SOURCE = "bicep-types-az"

_ID_PROP = re.compile(r"^(\w+?)(Id|Ids)$", re.IGNORECASE)
_MAX_DEPTH = 24

_FLAG_REQUIRED = 1
_FLAG_READONLY = 2


def _node(type_name: str) -> Node:
    return Node(
        id=f"azure::{type_name}",
        layer="vendor",
        provider="azure",
        display_name=type_name,
        source=SOURCE,
    )


def _is_preview(version: str) -> bool:
    return "preview" in version.lower()


def _version_better(candidate: str, current: str) -> bool:
    """비-preview 우선, 같은 등급이면 사전순(날짜형이라 사전순=시간순) 최신."""
    if _is_preview(candidate) != _is_preview(current):
        return _is_preview(current)
    return candidate > current


def _singular_candidates(segment: str) -> set[str]:
    """복수형 타입 세그먼트("subnets")의 단수 후보들("subnet")을 생성한다."""
    lowered = segment.lower()
    candidates = {lowered}
    if lowered.endswith("ies"):
        candidates.add(lowered[:-3] + "y")
    if lowered.endswith("es"):
        candidates.add(lowered[:-2])
    if lowered.endswith("s"):
        candidates.add(lowered[:-1])
    return candidates


def parse_index(index: dict) -> tuple[Graph, dict[str, tuple[str, str]]]:
    """index.json에서 노드/계층 엣지를 만들고, 타입별 최신 안정 버전을 고른다.

    Returns:
        (그래프, {타입명: (버전, types.json 상대경로)}) 튜플.
    """
    # index.json에는 같은 타입이 대소문자 변형으로 중복 등재돼 있다
    # (예: virtualNetworks/subnets vs virtualnetworks/subnets) —
    # 소문자 키로 dedupe하고 최신 버전의 표기를 대표로 쓴다.
    by_lower: dict[str, tuple[str, str, str]] = {}  # lower → (version, rel_path, 표기)
    for key, ref in index.get("resources", {}).items():
        type_name, _, version = key.partition("@")
        rel_path = ref.get("$ref", "").split("#")[0]
        if not type_name or not version or not rel_path:
            continue
        lowered = type_name.lower()
        current = by_lower.get(lowered)
        if current is None or _version_better(version, current[0]):
            by_lower[lowered] = (version, rel_path, type_name)

    latest: dict[str, tuple[str, str]] = {
        name: (version, rel_path) for version, rel_path, name in by_lower.values()
    }
    lower_to_name = {name.lower(): name for name in latest}

    graph = Graph()
    for type_name in latest:
        graph.add_node(_node(type_name))
    for type_name in latest:
        parent, _, _child = type_name.rpartition("/")
        parent = lower_to_name.get(parent.lower(), parent)
        if "/" in parent and parent in latest:
            graph.add_edge(
                Edge(
                    from_id=f"azure::{type_name}",
                    to_id=f"azure::{parent}",
                    type="contained_in",
                    via_property="",
                    required=True,
                    cardinality="one",
                    evidence="arm-hierarchy",
                    confidence=1.0,
                )
            )
    return graph, latest


def _build_target_index(type_names: list[str]) -> dict[str, str]:
    """정규화된 이름 → 타입명. 여러 타입과 충돌하는 이름은 제외한다."""
    index: dict[str, str | None] = {}
    for type_name in type_names:
        last = type_name.rsplit("/", 1)[-1]
        for candidate in _singular_candidates(last):
            index[candidate] = None if candidate in index else type_name
    return {k: v for k, v in index.items() if v is not None}


def extract_references(
    graph: Graph, types_arr: list[dict], *, heuristics: bool = True
) -> None:
    """types.json 한 파일에서 참조 엣지를 추출해 그래프에 더한다."""

    def deref(ref: dict) -> tuple[int, dict]:
        idx = int(ref["$ref"].rsplit("/", 1)[-1])
        return idx, types_arr[idx]

    # 그래프에 이미 있는 노드 표기를 대표로 사용 (index와 파일 간 대소문자 차이 흡수)
    canonical = {
        node_id[len("azure::"):].lower(): node_id[len("azure::"):]
        for node_id in graph.nodes
        if node_id.startswith("azure::")
    }

    def _canon(type_name: str) -> str:
        return canonical.get(type_name.lower(), type_name)

    resource_types: list[tuple[str, dict]] = [
        (_canon(entry["name"].split("@")[0]), entry)
        for entry in types_arr
        if isinstance(entry, dict) and entry.get("$type") == "ResourceType"
    ]
    type_names = [name for name, _ in resource_types]
    exact_names = set(type_names)
    target_index = _build_target_index(type_names)

    def resolve_target(obj: dict) -> str | None:
        name = _canon(obj.get("name") or "")
        if name in exact_names:
            return name
        normalized = name.lower()
        normalized = normalized.removeprefix("common")
        return target_index.get(normalized)

    def emit(from_type: str, to_type: str, via: str, *, required: bool, many: bool, evidence: str, confidence: float) -> None:
        if to_type == from_type or to_type.startswith(from_type + "/"):
            # 자기 자신 / 인라인 자식 목록은 계층(contained_in)으로 이미 표현됨
            return
        graph.add_node(_node(to_type))
        graph.add_edge(
            Edge(
                from_id=f"azure::{from_type}",
                to_id=f"azure::{to_type}",
                type="references",
                via_property=via,
                required=required,
                cardinality="many" if many else "one",
                evidence=evidence,
                confidence=confidence,
            )
        )

    def walk(from_type: str, entry: dict, path: str, *, in_array: bool, required: bool, depth: int, visited: frozenset[int]) -> None:
        if depth > _MAX_DEPTH or not isinstance(entry, dict):
            return
        kind = entry.get("$type")
        if kind == "ArrayType":
            item_ref = entry.get("itemType")
            if isinstance(item_ref, dict) and "$ref" in item_ref:
                idx, item = deref(item_ref)
                if idx not in visited:
                    walk(from_type, item, path, in_array=True, required=required, depth=depth + 1, visited=visited | {idx})
            return
        if kind in ("UnionType", "DiscriminatedObjectType"):
            elements = entry.get("elements")
            if isinstance(elements, dict):  # DiscriminatedObjectType: {이름: $ref}
                elements = list(elements.values())
            for sub_ref in elements or []:
                if isinstance(sub_ref, dict) and "$ref" in sub_ref:
                    idx, sub = deref(sub_ref)
                    if idx not in visited:
                        walk(from_type, sub, path, in_array=in_array, required=required, depth=depth + 1, visited=visited | {idx})
            if kind == "DiscriminatedObjectType":
                _walk_properties(from_type, entry.get("baseProperties") or {}, path, in_array=in_array, depth=depth, visited=visited)
            return
        if kind == "ObjectType":
            _walk_properties(from_type, entry.get("properties") or {}, path, in_array=in_array, depth=depth, visited=visited)

    def _walk_properties(from_type: str, properties: dict, path: str, *, in_array: bool, depth: int, visited: frozenset[int]) -> None:
        for prop_name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            flags = prop.get("flags", 0)
            if flags & _FLAG_READONLY:
                continue
            prop_required = bool(flags & _FLAG_REQUIRED)
            via = f"{path}.{prop_name}" if path else prop_name
            type_ref = prop.get("type")
            if not (isinstance(type_ref, dict) and "$ref" in type_ref):
                continue
            idx, target_entry = deref(type_ref)

            many = in_array
            entry = target_entry
            if entry.get("$type") == "ArrayType":
                many = True

            resolved_entry = entry
            if entry.get("$type") == "ArrayType":
                item_ref = entry.get("itemType")
                if isinstance(item_ref, dict) and "$ref" in item_ref:
                    _, resolved_entry = deref(item_ref)

            if resolved_entry.get("$type") == "ObjectType":
                target = resolve_target(resolved_entry)
                if target is not None:
                    emit(from_type, target, via, required=prop_required, many=many, evidence="bicep-ref", confidence=0.8)
                    continue  # 참조 경계에서 멈춤 — 대상 내부는 대상 자신의 것

            if heuristics:
                match = _ID_PROP.match(prop_name)
                if match and resolved_entry.get("$type") in ("StringType", "StringLiteralType"):
                    target = target_index.get(match.group(1).lower())
                    if target is not None:
                        emit(from_type, target, via, required=prop_required, many=many, evidence="heuristic", confidence=0.6)
                        continue

            if idx not in visited:
                walk(from_type, target_entry, via, in_array=many, required=prop_required, depth=depth + 1, visited=visited | {idx})

    for type_name, resource in resource_types:
        body_ref = resource.get("body")
        if isinstance(body_ref, dict) and "$ref" in body_ref:
            idx, body = deref(body_ref)
            walk(type_name, body, "", in_array=False, required=False, depth=0, visited=frozenset({idx}))


def build(
    output: Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
    heuristics: bool = True,
    refresh: bool = False,
) -> Graph:
    """인덱스/타입 파일을 받아 파싱하고 output에 저장한 뒤 그래프를 반환한다."""
    index_path = _fetch_relative(base_url, "index.json", refresh=refresh)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    graph, latest = parse_index(index)

    wanted = {p.lower() for p in providers}
    rel_paths: list[str] = sorted(
        {
            rel_path
            for type_name, (_version, rel_path) in latest.items()
            if type_name.split("/", 1)[0].lower() in wanted
        }
    )
    read_paths = [index_path]
    for rel_path in rel_paths:
        try:
            types_path = _fetch_relative(base_url, rel_path, refresh=refresh)
            types_arr = json.loads(types_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — 한 파일 실패가 전체를 막지 않게
            print(f"경고: types.json 처리 실패, 건너뜀 — {rel_path}: {exc}", file=sys.stderr)
            continue
        read_paths.append(types_path)
        extract_references(graph, types_arr, heuristics=heuristics)

    graph.provenance = [describe_source_set(read_paths, "bicep-types-az")]
    graph.save(output)
    by_evidence: dict[str, int] = {}
    for edge in graph.edges:
        by_evidence[edge.evidence] = by_evidence.get(edge.evidence, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(
        f"azure: 노드 {len(graph.nodes)}개, 엣지 {len(graph.edges)}개 "
        f"({summary}; types.json {len(rel_paths)}개) → {output}"
    )
    return graph


def _fetch_relative(base: str, rel_path: str, *, refresh: bool) -> Path:
    """base가 로컬 디렉터리면 그 안에서 찾고, 아니면 URL로 조립해 받는다."""
    local = Path(base) / rel_path
    if local.exists():
        return local
    url = f"{base.rstrip('/')}/{rel_path}"
    cache_name = "azure-" + rel_path.replace("/", "_")
    return fetch_cached(url, cache_name, refresh=refresh)
