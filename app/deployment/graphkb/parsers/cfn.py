"""AWS CloudFormation Registry 스키마 파서: AWS 벤더 레이어 그래프 추출.

세 가지 근거 소스를 병합한다 (같은 엣지는 confidence 높은 쪽 유지):

1. relationshipRef (confidence 1.0) — 스키마에 명시된 참조 메타데이터.
   단, 실측상 전체 ~1,600개 스키마 중 ~26개에만 존재해 커버리지가 매우 좁다.
2. CDK out-of-band 관계 데이터 (cdk-oob, confidence 0.9) — AWS CDK 팀이
   별도 관리하는 relationships.json (~350타입/~970항목).
3. 속성명 휴리스틱 (heuristic, confidence 0.6/0.5) — `*Id`/`*Arn` 속성명을
   타입명과 매칭. 유일 매칭일 때만 엣지 생성해 오탐을 억제한다.

readOnly 속성(생성 출력)은 생성 순서와 무관하므로 모든 소스에서 제외한다.
특히 CDK OoB에는 readOnly 출력 속성 기준의 역방향 항목이 섞여 있어
이 필터가 없으면 방향이 뒤집힌 엣지가 생긴다.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

from graphkb.fetch import fetch_cached
from graphkb.model import Edge, Graph, Node

DEFAULT_ZIP_URL = (
    "https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip"
)
# Git LFS 파일이므로 raw.githubusercontent.com이 아닌 media 호스트를 써야 한다.
DEFAULT_OOB_URL = (
    "https://media.githubusercontent.com/media/cdklabs/awscdk-service-spec/main/"
    "sources/OobRelationships/relationships.json"
)

SOURCE = "cloudformation-registry"

_ID_PROP = re.compile(r"^(\w+?)(Id|Ids|Arn|Arns)$")
_MAX_DEPTH = 32
_COMBINATORS = ("anyOf", "oneOf", "allOf")


def _node(type_name: str, source: str = SOURCE) -> Node:
    return Node(
        id=f"aws::{type_name}",
        layer="vendor",
        provider="aws",
        display_name=type_name,
        source=source,
    )


def _top_property_names(pointer_list: list[str] | None) -> set[str]:
    """readOnlyProperties 같은 "/properties/Name" 포인터에서 최상위 이름 추출."""
    names = set()
    for pointer in pointer_list or []:
        parts = pointer.split("/")
        if len(parts) >= 3 and parts[1] == "properties":
            names.add(parts[2])
    return names


def _service(type_name: str) -> str:
    """"AWS::EC2::VPC" → "ec2"."""
    parts = type_name.split("::")
    return parts[1].lower() if len(parts) >= 2 else ""


def _has_relationship_ref(obj: object, depth: int = 0) -> bool:
    if depth > _MAX_DEPTH:
        return False
    if isinstance(obj, dict):
        if "relationshipRef" in obj:
            return True
        return any(_has_relationship_ref(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_relationship_ref(v, depth + 1) for v in obj)
    return False


def _build_type_index(type_names: set[str]) -> dict[str, list[str]]:
    """타입명 마지막 세그먼트(소문자) → 타입명 목록 (휴리스틱 해석용)."""
    index: dict[str, list[str]] = {}
    for type_name in type_names:
        index.setdefault(type_name.split("::")[-1].lower(), []).append(type_name)
    return index


def _resolve_heuristic(
    prop_name: str, service: str, type_index: dict[str, list[str]]
) -> tuple[str, float] | None:
    """속성명에서 대상 타입을 추정한다. 유일 매칭일 때만 반환."""
    match = _ID_PROP.match(prop_name)
    if match is None:
        return None
    candidates = type_index.get(match.group(1).lower(), [])
    if len(candidates) == 1:
        target = candidates[0]
    else:
        same_service = [t for t in candidates if _service(t) == service]
        if len(same_service) != 1:
            return None
        target = same_service[0]
    confidence = 0.6 if _service(target) == service else 0.5
    return target, confidence


def _extract_schema_edges(
    graph: Graph,
    schema: dict,
    type_index: dict[str, list[str]],
    *,
    heuristics: bool,
) -> None:
    """스키마 하나에서 relationshipRef(+휴리스틱) 엣지를 추출한다."""
    type_name = schema.get("typeName")
    if not isinstance(type_name, str) or not type_name:
        return
    from_id = f"aws::{type_name}"
    service = _service(type_name)
    readonly = _top_property_names(schema.get("readOnlyProperties"))

    def emit_ref(ref: dict, path: tuple[str, ...], in_array: bool, required: bool) -> None:
        target = ref.get("typeName")
        if not isinstance(target, str) or not target:
            return
        graph.add_node(_node(target))
        graph.add_edge(
            Edge(
                from_id=from_id,
                to_id=f"aws::{target}",
                type="references",
                via_property="/".join(path),
                required=required,
                cardinality="many" if in_array else "one",
                evidence="relationshipRef",
                confidence=1.0,
            )
        )

    def visit(node: dict, path: tuple[str, ...], in_array: bool, required: bool, depth: int) -> None:
        if depth > _MAX_DEPTH or not isinstance(node, dict):
            return
        ref = node.get("relationshipRef")
        if isinstance(ref, dict):
            emit_ref(ref, path, in_array, required)
        for comb in _COMBINATORS:
            subs = node.get(comb)
            if isinstance(subs, list):
                for sub in subs:
                    if isinstance(sub, dict):
                        visit(sub, path, in_array, required, depth + 1)
        items = node.get("items")
        if isinstance(items, dict):
            visit(items, path, True, required, depth + 1)
        required_here = node.get("required")
        required_set = set(required_here) if isinstance(required_here, list) else set()
        properties = node.get("properties")
        if isinstance(properties, dict):
            for prop_name, prop in properties.items():
                if not isinstance(prop, dict):
                    continue
                if not path and prop_name in readonly:
                    continue  # 생성 출력 속성은 순서 제약이 아님
                prop_path = path + (prop_name,)
                prop_required = required and prop_name in required_set
                visit(prop, prop_path, in_array, prop_required, depth + 1)
                if heuristics and not _has_relationship_ref(prop):
                    resolved = _resolve_heuristic(prop_name, service, type_index)
                    if resolved is not None:
                        target, confidence = resolved
                        many = in_array or prop.get("type") == "array"
                        graph.add_edge(
                            Edge(
                                from_id=from_id,
                                to_id=f"aws::{target}",
                                type="references",
                                via_property="/".join(prop_path),
                                required=prop_required,
                                cardinality="many" if many else "one",
                                evidence="heuristic",
                                confidence=confidence,
                            )
                        )

    # 루트(properties)와 definitions를 각각 순회. $ref는 해석하지 않는다 —
    # relationshipRef는 구체 프로퍼티 객체에 직접 붙으므로 두 트리를 훑으면 충분.
    visit(schema, (), False, True, 0)
    for definition in (schema.get("definitions") or {}).values():
        if isinstance(definition, dict):
            visit(definition, (), False, False, 0)


def _apply_oob(graph: Graph, oob: dict, schemas_by_type: dict[str, dict]) -> None:
    """CDK out-of-band relationships.json을 엣지로 변환해 병합한다."""
    for type_name, entry in oob.items():
        if not isinstance(entry, dict):
            continue
        schema = schemas_by_type.get(type_name)
        readonly = _top_property_names(
            schema.get("readOnlyProperties") if schema else None
        )
        required_set = (
            set(schema.get("required") or []) if schema else set()
        )
        properties = (schema.get("properties") or {}) if schema else {}
        graph.add_node(_node(type_name, source="cdk-oob" if schema is None else SOURCE))
        for prop_path, targets in (entry.get("relationships") or {}).items():
            top = prop_path.split("/", 1)[0]
            if top in readonly:
                continue  # readOnly 출력 기준의 역방향 항목 제거
            top_schema = properties.get(top, {})
            many = top_schema.get("type") == "array" or "items" in top_schema
            required = "/" not in prop_path and top in required_set
            for target_entry in targets or []:
                target = target_entry.get("cloudformationType")
                if not isinstance(target, str) or not target:
                    continue
                graph.add_node(
                    _node(target, source="cdk-oob" if target not in schemas_by_type else SOURCE)
                )
                graph.add_edge(
                    Edge(
                        from_id=f"aws::{type_name}",
                        to_id=f"aws::{target}",
                        type="references",
                        via_property=prop_path,
                        required=required,
                        cardinality="many" if many else "one",
                        evidence="cdk-oob",
                        confidence=0.9,
                    )
                )


def parse_schemas(
    schemas: list[dict], *, oob: dict | None = None, heuristics: bool = True
) -> Graph:
    """파싱된 스키마 목록(+선택적 OoB 데이터)에서 AWS 그래프를 만든다."""
    graph = Graph()
    schemas_by_type: dict[str, dict] = {}
    for schema in schemas:
        type_name = schema.get("typeName")
        if isinstance(type_name, str) and type_name:
            schemas_by_type[type_name] = schema
            graph.add_node(_node(type_name))

    type_index = _build_type_index(set(schemas_by_type))
    for schema in schemas_by_type.values():
        _extract_schema_edges(graph, schema, type_index, heuristics=heuristics)
    if oob is not None:
        _apply_oob(graph, oob, schemas_by_type)
    return graph


def parse_zip(
    zip_path: Path, *, oob_path: Path | None = None, heuristics: bool = True
) -> Graph:
    """스키마 zip(및 선택적 OoB 파일)을 읽어 그래프를 만든다."""
    schemas: list[dict] = []
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not name.endswith(".json"):
                continue
            try:
                schemas.append(json.loads(archive.read(name)))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"경고: 스키마 파싱 실패, 건너뜀 — {name}: {exc}", file=sys.stderr)
    oob = None
    if oob_path is not None:
        oob = json.loads(oob_path.read_text(encoding="utf-8"))
    return parse_schemas(schemas, oob=oob, heuristics=heuristics)


def build(
    output: Path,
    *,
    zip_url: str = DEFAULT_ZIP_URL,
    oob_url: str = DEFAULT_OOB_URL,
    heuristics: bool = True,
    cdk_oob: bool = True,
    refresh: bool = False,
) -> Graph:
    """스키마를 받아 파싱하고 output에 저장한 뒤 그래프를 반환한다."""
    zip_path = fetch_cached(zip_url, "CloudformationSchema.zip", refresh=refresh)
    oob_path = (
        fetch_cached(oob_url, "cdk-relationships.json", refresh=refresh)
        if cdk_oob
        else None
    )
    graph = parse_zip(zip_path, oob_path=oob_path, heuristics=heuristics)
    graph.save(output)
    by_evidence: dict[str, int] = {}
    for edge in graph.edges:
        by_evidence[edge.evidence] = by_evidence.get(edge.evidence, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(
        f"cfn: 노드 {len(graph.nodes)}개, 엣지 {len(graph.edges)}개 "
        f"({summary}) → {output}"
    )
    return graph
