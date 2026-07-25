"""AWS CloudFormation Registry 스키마 파서: AWS 벤더 레이어 그래프 추출.

세 가지 근거 소스를 병합한다 (같은 엣지는 **사실인 쪽** 유지):

1. relationshipRef — 스키마에 명시된 참조 메타데이터 (원본 명시).
   단, 실측상 전체 ~1,600개 스키마 중 ~26개에만 존재해 커버리지가 매우 좁다.
2. CDK out-of-band 관계 데이터 (cdk-oob, 원본 명시) — AWS CDK 팀이
   별도 관리하는 relationships.json (~350타입/~970항목).
3. 속성명 휴리스틱 (heuristic, 짐작) — `*Id`/`*Arn` 속성명을
   타입명과 매칭. 유일 매칭일 때만 엣지 생성해 오탐을 억제한다.

**담김(`contained_in`) 관계는 여기서 안 나온다 — 지어내지 않기로 한 결과다.**
CloudFormation 스키마에는 담김을 말하는 어휘가 아예 없다(`parentResource` 같은 키가
없고, 전수 확인함). Azure는 타입 이름이 계층적이라(`.../virtualNetworks/subnets`)
공짜로 나오고, GCP는 `projectRef` 같은 계층 참조가 있어 그걸 쓴다. AWS는 둘 다 없다.

"필수 참조를 담김으로 치면 되지 않나" 싶지만 그건 **짐작이다.** 실측상 aws 엣지
2,391건 중 744건이 필수인데, `Certificate → CertificateAuthority`처럼 담김이 맞는 것과
`Instance → Subnet`처럼 애매한 것이 섞여 있다. 대충 뭉뚱그리면 정확히 이 프로젝트가
막으려는 "확신에 찬 오답"이 된다. 그래서 **비워 두고 이 사실을 여기 적는다** —
비어 있는 게 우리가 빠뜨려서가 아니라 원본에 없어서임을 다음 사람이 알 수 있게.

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

from app.deployment.graphkb.fetch import fetch_cached
from app.deployment.graphkb.model import Edge, Graph, Node
from app.deployment.graphkb.parsers.review import apply_review, check_freshness
from app.deployment.kbcommon.fetch import describe_source
from app.deployment.kbcommon.invariants import announce
from app.deployment.kbcommon.sources import SOURCES

# ⚠️ AWS는 이 zip을 계속 덮어쓴다 — 고정할 ref가 없다. 받은 바이트의 sha256을
# 프로버넌스에 남기는 것이 최선이다 (kbcommon/sources.py 참조).
DEFAULT_ZIP_URL = SOURCES["cfn-schema"].url
# Git LFS 파일이므로 raw.githubusercontent.com이 아닌 media 호스트를 써야 한다.
# 태그 고정됨 — main을 쓰면 재현이 안 된다.
DEFAULT_OOB_URL = SOURCES["cdk-oob"].url

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
    """`/properties/Name` **정확히 그 깊이**의 포인터에서만 이름을 뽑는다.

    이 집합은 "이 최상위 속성 전체를 건너뛴다"에 쓰이므로, 더 깊은 포인터를 넣으면
    안 된다. 실측 사고:

        AWS::Batch::ComputeEnvironment
          readOnlyProperties: ["/properties/ComputeResources/Ec2Configuration/*/BatchImageStatus"]

    `BatchImageStatus` 하나가 읽기 전용인데 `ComputeResources` **서브트리 전체**가
    배제돼, 그 아래 `LaunchTemplate/LaunchTemplateId`(→ EC2::LaunchTemplate) 같은
    실제 참조가 통째로 사라졌다. 예전에는 definitions를 따로 순회해서 우회로가
    있었기 때문에 이 버그가 드러나지 않았다.
    """
    names = set()
    for pointer in pointer_list or []:
        parts = pointer.split("/")
        if len(parts) == 3 and parts[1] == "properties":
            names.add(parts[2])
    return names


def _target_property(pointer: str | None) -> str:
    """`/properties/GroupId` → `GroupId`.

    AWS는 대상의 어느 값을 쓰는지를 JSON 포인터로 준다. 중첩이면
    (`/properties/A/B`) 슬래시로 이어 둔다 — 우리 `via_property`와 같은 표기다.
    """
    if not isinstance(pointer, str):
        return ""
    parts = pointer.split("/")
    if len(parts) >= 3 and parts[1] == "properties":
        return "/".join(parts[2:])
    return pointer.strip("/")


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
) -> str | None:
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
    return target


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
                target_property=_target_property(ref.get("propertyPath")),
            )
        )

    definitions = schema.get("definitions") or {}

    def visit(
        node: dict,
        path: tuple[str, ...],
        in_array: bool,
        required: bool,
        depth: int,
        seen: frozenset[str],
    ) -> None:
        if depth > _MAX_DEPTH or not isinstance(node, dict):
            return
        ref = node.get("relationshipRef")
        if isinstance(ref, dict):
            emit_ref(ref, path, in_array, required)

        # `$ref`를 **경로를 유지한 채** 따라간다. 예전에는 definitions를 빈 경로로
        # 따로 순회해서, 중첩 속성이 마치 루트 속성인 것처럼 기록됐다
        # (`Settings/MongoDbSettings/CertificateArn` → `CertificateArn`).
        # 실측 결과 heuristic 486/1,102·relationshipRef 18/59의 via_property가
        # 실재하지 않는 경로였다. via_property는 "이 의존을 만들려면 어느 속성을
        # 채워야 하는가"를 답하는 필드라, 틀리면 그 값으로 템플릿을 만드는 쪽이 전부 깨진다.
        target_ref = node.get("$ref")
        if isinstance(target_ref, str):
            name = target_ref.rsplit("/", 1)[-1]
            if name not in seen and isinstance(definitions.get(name), dict):
                visit(
                    definitions[name], path, in_array, required, depth + 1, seen | {name}
                )

        for comb in _COMBINATORS:
            subs = node.get(comb)
            if isinstance(subs, list):
                for sub in subs:
                    if isinstance(sub, dict):
                        visit(sub, path, in_array, required, depth + 1, seen)
        items = node.get("items")
        if isinstance(items, dict):
            visit(items, path, True, required, depth + 1, seen)

        # 맵 타입(`{"patternProperties": {"^.+$": {"$ref": ...}}}`)도 따라간다.
        # 실측 253개 스키마가 이 모양이고, 안 따라가면 AppConfig::Extension의
        # `Actions` 아래 RoleArn처럼 실재하는 참조를 통째로 놓친다.
        # 키 이름은 정규식이라 경로에 넣을 수 없어 부모 경로를 그대로 물려준다.
        for maps in ("patternProperties", "additionalProperties"):
            sub = node.get(maps)
            if isinstance(sub, dict):
                values = sub.values() if maps == "patternProperties" else [sub]
                for value in values:
                    if isinstance(value, dict):
                        visit(value, path, True, required, depth + 1, seen)

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
                visit(prop, prop_path, in_array, prop_required, depth + 1, seen)
                if heuristics and not _has_relationship_ref(prop):
                    resolved = _resolve_heuristic(prop_name, service, type_index)
                    if resolved is not None:
                        target = resolved
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
                            )
                        )

    # 루트에서만 출발한다. definitions는 `$ref`를 통해서만 도달하므로 경로가 보존되고,
    # 어디서도 참조되지 않는 definition은 실제 속성이 아니므로 자연히 빠진다.
    visit(schema, (), False, True, 0, frozenset())


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
                        target_property=_target_property(
                            target_entry.get("propertyPath")
                        ),
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
    graph.provenance = [describe_source(zip_path, "cfn-schema")]
    if oob_path is not None:
        graph.provenance.append(describe_source(oob_path, "cdk-oob"))

    # 사람 검수를 마지막에 적용한다 — 파서가 못 거르는 것을 눈으로 보고 지운다.
    stale = check_freshness("aws", graph.provenance)
    if stale:
        print(f"⚠ {stale}", file=sys.stderr)
    stats = apply_review(graph, "aws")
    if any(stats.values()):
        print(
            f"검수 적용: 제거 {stats['dropped']}, 확인 표시 {stats['confirmed']}, "
            f"추가 {stats['added']}"
        )

    announce(graph.save(output), "graphkb/cfn")
    by_evidence: dict[str, int] = {}
    for edge in graph.edges:
        by_evidence[edge.evidence] = by_evidence.get(edge.evidence, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(
        f"cfn: 노드 {len(graph.nodes)}개, 엣지 {len(graph.edges)}개 "
        f"({summary}) → {output}"
    )
    return graph
