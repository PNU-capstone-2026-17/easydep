"""Azure 파서: bicep-types-az 타입 인덱스에서 벤더 레이어 그래프 추출.

소스는 github.com/Azure/bicep-types-az의 generated/index.json(전체 타입 목록)과
프로바이더별 types.json(타입 상세)이다.

핵심 사실 (실측):
- ARM 타입명은 계층적이다: "Microsoft.Network/virtualNetworks/subnets"는
  virtualNetworks의 자식 → 이름만으로 contained_in 엣지를 얻는다
  (evidence=arm-hierarchy — 이름의 구조라 원본이 명시한 것과 같다).
- swagger의 arm-id 참조 메타데이터는 bicep 타입 생성 과정에서 소실된다.
  대신 프로퍼티 타입이 다른 리소스의 인라인 객체(예: "CommonSubnet")로
  $ref 연결되는 구조가 남으므로, ObjectType 이름을 정규화(Common 접두사 제거,
  단수/복수 보정)해 리소스 타입과 유일 매칭되면 참조 엣지로 본다
  (evidence=bicep-ref — 짐작이며 검수표로 확정한다).
- 그 외 `*Id` 문자열 프로퍼티는 CFN과 같은 속성명 휴리스틱으로 보강한다
  (evidence=heuristic — 짐작).

전체 노드/계층은 index.json 한 파일로 커버하고, 참조 엣지는 용량 문제로
선택된 프로바이더(기본: network/compute/containerservice)의 types.json만
내려받아 추출한다. --providers로 확장 가능.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from graphkb.fetch import fetch_cached
from graphkb.model import Edge, Graph, Node
from graphkb.parsers.review import apply_review, check_freshness, load_reference_map
from kbcommon.invariants import announce
from kbcommon.fetch import describe_source_set
from kbcommon.sources import SOURCES
from kbcommon.type_ids import read_azure_index

# 고정 ref는 kbcommon/sources.py 한 곳에서 관리한다 — 같은 소스를 capacitykb도 쓰므로
# 양쪽이 따로 들고 있으면 조용히 다른 커밋을 보게 된다.
DEFAULT_BASE_URL = SOURCES["bicep-types-az"].url
DEFAULT_PROVIDERS = ("microsoft.network", "microsoft.compute", "microsoft.containerservice")

SOURCE = "bicep-types-az"

# 대상을 못 정한 참조 껍데기. 파일마다 extract_references가 불리므로 모듈에 모아 두고
# build가 한 번에 보고한다. **침묵시키지 않는다** — 미결을 안 알리면 "관계가 없는 것"과
# "아직 안 본 것"이 겉보기에 같아진다.
UNRESOLVED_REFS: Counter[str] = Counter()

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
    # 대표 표기를 고르는 규칙은 **capacitykb와 같아야 한다** — 아니면 같은 타입이
    # 두 id로 갈려 조인이 깨진다. 그래서 규칙은 kbcommon에 한 벌만 둔다.
    type_index = read_azure_index(index)
    latest = type_index.latest
    lower_to_name = type_index.by_lower

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
                )
            )
    return graph, latest


def _build_target_index(type_names: list[str]) -> dict[str, str]:
    """정규화된 이름 → 타입명.

    같은 이름으로 끝나는 타입이 여럿이면 **경로가 가장 얕은 것**을 고른다.
    이름만으로 가리킬 때는 독립 리소스를 뜻하지, 중첩 하위 리소스가 아니기 때문이다
    (하위 리소스는 부모 경로를 포함해 참조한다).

        networkInterfaces로 끝나는 타입 3종:
          Microsoft.Network/networkInterfaces                        ← 이걸 고른다
          Microsoft.Compute/virtualMachineScaleSets/networkInterfaces
          Microsoft.Compute/.../virtualMachines/networkInterfaces

    예전에는 충돌하면 통째로 뺐다. 안전해 보이지만 가장 중요한 참조가 그렇게 사라졌다 —
    가상머신이 네트워크 인터페이스를 가리키지 못한 이유 중 하나다.
    같은 깊이에서 갈리면 그때는 제외한다 — 고를 근거가 없다.
    """
    by_key: dict[str, list[str]] = {}
    for type_name in type_names:
        last = type_name.rsplit("/", 1)[-1]
        for candidate in _singular_candidates(last):
            by_key.setdefault(candidate, []).append(type_name)

    index: dict[str, str] = {}
    for key, names in by_key.items():
        best = min(names, key=lambda n: (n.count("/"), n))
        if sum(1 for n in names if n.count("/") == best.count("/")) == 1:
            index[key] = best
    return index


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

    # 대상 후보는 **Azure 전체 타입**이다. 예전에는 이 파일 안의 ResourceType만 썼는데,
    # 그러면 파일을 넘는 참조가 원리적으로 불가능하다 — Compute 파일에는
    # Microsoft.Network/networkInterfaces가 없으니 가상머신이 네트워크 인터페이스를
    # 가리킬 방법이 없었다. 실제로 Azure 관계 2,294개 중 참조가 71개뿐이었고
    # 가상머신은 나가는 관계가 0개였다.
    #
    # graph.nodes는 index.json에서 만든 것이라 이 시점에 전체 타입이 들어 있다.
    all_type_names = [canonical[k] for k in canonical]
    exact_names = set(all_type_names)
    target_index = _build_target_index(all_type_names)

    reference_map = load_reference_map("azure")

    def resolve_target(obj: dict, prop_name: str) -> tuple[str | None, bool]:
        """참조 껍데기가 가리키는 타입과, 그게 **사람이 정한 것인지**.

        판단 순서는 **확실한 것부터**다. 이름이 그대로 타입이면 그것, 사람이 채운
        표에 있으면 그것, 이름 규칙으로 후보가 하나뿐이면 그것. 그 외에는 짐작하지
        않는다 — `networkInterface`로 끝나는 타입이 5개인데 그중 하나를 고르는 규칙은
        전부 근거 없는 취향이다(경로 깊이로 골라 봤더니 AzureStackHCI와 동점이었다).
        """
        raw = obj.get("name") or ""
        name = _canon(raw)
        if name in exact_names:
            return name, False
        # 표를 찾을 때는 Common 접두사를 벗긴다 — bicep-types가 공용 정의 파일에
        # 내보내는 같은 모양의 사본이라 뜻이 같다.
        bare = raw[len("Common"):] if raw.startswith("Common") and len(raw) > 6 else raw
        for key in (f"{bare}@{prop_name}", bare):
            if key in reference_map:
                # 표에서 나온 대상은 **사람이 정한 것**이다. 검수 이력은
                # azure-references.json이 갖고 있으므로 엣지에 그대로 표시한다 —
                # 같은 판단을 azure-edges.json에 한 번 더 적으면 둘이 어긋난다.
                return reference_map[key], True  # None이면 "관계 없음"이라고 적은 것
        normalized = name.lower().removeprefix("common")
        # `NetworkInterfaceReference`처럼 참조 껍데기에 붙는 꼬리표를 떼고 다시 본다.
        for suffix in ("reference", "ref"):
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                stripped = normalized[: -len(suffix)]
                hit = target_index.get(stripped) or (
                    stripped if stripped in exact_names else None
                )
                if hit:
                    return hit, False
        hit = target_index.get(normalized)
        if hit is None:
            UNRESOLVED_REFS[f"{bare}@{prop_name}"] += 1
        return hit, False

    def emit(from_type: str, to_type: str, via: str, *, required: bool, many: bool, evidence: str, target_property: str = "", reviewed: bool = False) -> None:
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
                target_property=target_property,
                reviewed=reviewed,
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
                # **`id`가 있어야 참조다.** ARM에서 다른 리소스를 가리키는 객체는 그
                # 리소스의 id를 담는 껍데기이고, id가 없으면 그 자리에 값을 직접 적는
                # 인라인 설정이다. 이름만 보면 둘이 구분되지 않는다 —
                # 가상머신의 `networkProfile`은 NetworkProfile이라는 이름 때문에
                # Microsoft.Network/networkProfiles로 오인되지만 실제 속성은
                # networkInterfaces·networkApiVersion뿐이다.
                #
                # 이 판별은 검수에서 먼저 쓴 것이다(오탐 12건을 이 기준으로 걸렀다).
                # 파서에 넣으면 애초에 안 생기고, 무엇보다 **인라인 객체 안으로 계속
                # 내려가게 된다** — networkProfile에서 멈추지 않아야 그 아래
                # networkInterfaces(진짜 참조)에 닿는다.
                if "id" in (resolved_entry.get("properties") or {}):
                    target, decided = resolve_target(resolved_entry, prop_name)
                    if target is not None:
                        emit(from_type, target, via, required=prop_required, many=many,
                             evidence="bicep-ref", target_property="id",
                             reviewed=decided)
                        continue  # 참조 경계에서 멈춤 — 대상 내부는 대상 자신의 것

            if heuristics:
                match = _ID_PROP.match(prop_name)
                if match and resolved_entry.get("$type") in ("StringType", "StringLiteralType"):
                    target = target_index.get(match.group(1).lower())
                    if target is not None:
                        emit(from_type, target, via, required=prop_required, many=many, evidence="heuristic")
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

    # 검수표의 대상이 실재하는 타입인지 먼저 본다. 오타가 나면 그 껍데기의 엣지가
    # 통째로 안 생기는데, 미결로도 안 잡혀서(표에는 있으므로) 조용히 사라진다.
    known = {nid.split("::", 1)[1] for nid in graph.nodes}
    missing = sorted({t for t in load_reference_map("azure").values() if t and t not in known})
    if missing:
        print(
            f"⚠ 검수표(azure-references.json)의 대상 {len(missing)}개가 없는 타입입니다: "
            + ", ".join(missing),
            file=sys.stderr,
        )

    wanted = {p.lower() for p in providers}
    rel_paths: list[str] = sorted(
        {
            rel_path
            for type_name, (_version, rel_path) in latest.items()
            if type_name.split("/", 1)[0].lower() in wanted
        }
    )
    read_paths = [index_path]
    UNRESOLVED_REFS.clear()
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

    stale = check_freshness("azure", graph.provenance)
    if stale:
        print(f"⚠ {stale}", file=sys.stderr)
    review_stats = apply_review(graph, "azure")
    if any(review_stats.values()):
        print(
            f"검수 적용: 제거 {review_stats['dropped']}, "
            f"확인 표시 {review_stats['confirmed']}, 추가 {review_stats['added']}"
        )

    pending = output.parent / "azure-unresolved-refs.json"
    if not UNRESOLVED_REFS:
        # 다 풀렸으면 지운다. 남겨 두면 **낡은 목록이 사실처럼 읽힌다** —
        # 파일이 없는 것이 "미결 없음"이고, 있으면 항상 이번 빌드의 결과다.
        pending.unlink(missing_ok=True)
    else:
        pending.write_text(
            json.dumps(
                {"resolved": {k: None for k, _ in UNRESOLVED_REFS.most_common()}},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(
            f"⚠ 대상을 못 정한 참조 껍데기 {len(UNRESOLVED_REFS)}종 "
            f"({sum(UNRESOLVED_REFS.values())}곳) — 관계를 만들지 않았습니다. "
            f"검수 대상 목록: {pending}",
            file=sys.stderr,
        )

    announce(graph.save(output), "graphkb/azure")
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
