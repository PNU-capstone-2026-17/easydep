"""CB-Tumblebug swagger 파서: 코어 레이어(벤더 중립) 타입 의존성 그래프 추출.

생성 요청 스키마(model.Tb*Req)만 사용한다 — required 배열이 생성 시점
제약을 표현하고, 응답 스키마는 서버 생성 필드 노이즈가 많기 때문.

v0.11.8 태그를 기본 소스로 고정한다(main 브랜치는 MCI→Infra 개명 +
Tb 접두사 제거로 스키마 이름이 다름). 스펙 포맷은 Swagger 2.0
(definitions)과 OpenAPI 3(components.schemas)을 모두 지원한다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from graphkb.fetch import fetch_cached
from graphkb.model import Edge, Graph, Node
from kbcommon.invariants import announce
from kbcommon.fetch import describe_source

DEFAULT_SWAGGER_URL = (
    "https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/v0.11.8/"
    "src/interface/rest/docs/swagger.json"
)

SOURCE = "cb-tumblebug-swagger"

# 생성 요청 스키마 정의명 → 코어 리소스 타입 (v0.11.8 기준, 없는 항목은 skip)
DEFINITION_TYPES: dict[str, str] = {
    "model.TbVNetReq": "vNet",
    "model.TbSubnetReq": "subnet",
    "model.TbSecurityGroupReq": "securityGroup",
    "model.TbSshKeyReq": "sshKey",
    "model.TbCreateSubGroupReq": "vm",  # v0.11.8의 VM 생성 요청 (subGroup 단위)
    "model.TbVmReq": "vm",  # 구버전 명명 호환
    "model.TbMciReq": "mci",
    "model.TbNLBReq": "nlb",
    "model.TbK8sClusterReq": "k8sCluster",
    "model.TbK8sNodeGroupReq": "k8sNodeGroup",
    "model.TbDataDiskReq": "dataDisk",
    "model.TbCustomImageReq": "customImage",
    "model.TbSpecReq": "spec",
    "model.TbImageReq": "image",
}

# 참조 필드명 → 대상 코어 타입 (명시 매핑: evidence=swagger-field)
FIELD_TARGETS: dict[str, str] = {
    "vNetId": "vNet",
    "subnetId": "subnet",
    "subnetIds": "subnet",
    "securityGroupId": "securityGroup",
    "securityGroupIds": "securityGroup",
    "sshKeyId": "sshKey",
    "specId": "spec",
    "imageId": "image",
    "dataDiskIds": "dataDisk",
    "customImageId": "customImage",
    "sourceVmId": "vm",
    "subGroupId": "vm",
}

_ID_FIELD = re.compile(r"^(\w+?)Ids?$")
_MAX_DEPTH = 4


def _schemas(spec: dict) -> dict:
    """Swagger 2.0(definitions)과 OpenAPI 3(components.schemas) 모두 지원."""
    return spec.get("components", {}).get("schemas") or spec.get("definitions") or {}


def _ref_name(prop: dict) -> str | None:
    """프로퍼티가 가리키는 정의명을 추출한다.

    직접 $ref, allOf 래핑({"allOf": [{"$ref": ...}]}) 두 형태를 처리하고,
    배열은 호출 쪽에서 items를 넘긴다.
    """
    ref = prop.get("$ref")
    if ref is None:
        all_of = prop.get("allOf")
        if isinstance(all_of, list) and len(all_of) == 1:
            ref = all_of[0].get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    return None


def _core_node(core_type: str) -> Node:
    return Node(
        id=f"core::{core_type}",
        layer="core",
        provider="common",
        display_name=core_type,
        source=SOURCE,
    )


def parse_swagger(spec: dict, *, heuristics: bool = False) -> Graph:
    """swagger 스펙에서 코어 레이어 타입 그래프를 추출한다.

    Args:
        spec: 파싱된 swagger/OpenAPI 문서.
        heuristics: True면 명시 매핑에 없는 *Id(s) 필드도 suffix 규칙으로
            추정한다 (evidence=heuristic).
    """
    schemas = _schemas(spec)
    graph = Graph()
    # 코어 타입 집합 (휴리스틱 대상 판정용, 소문자 키)
    known_types = {t.lower(): t for t in DEFINITION_TYPES.values()}
    known_types.update({t.lower(): t for t in FIELD_TARGETS.values()})

    missing = [name for name in DEFINITION_TYPES if name not in schemas]
    for name in missing:
        print(f"경고: 정의 없음, 건너뜀 — {name}", file=sys.stderr)

    for def_name, core_type in DEFINITION_TYPES.items():
        definition = schemas.get(def_name)
        if definition is None:
            continue
        graph.add_node(_core_node(core_type))
        _walk_definition(
            graph,
            core_type,
            definition,
            schemas=schemas,
            known_types=known_types,
            heuristics=heuristics,
            path_prefix="",
            in_array=False,
            path_required=True,
            visited=frozenset({def_name}),
            depth=0,
        )
    return graph


def _walk_definition(
    graph: Graph,
    resource_type: str,
    definition: dict,
    *,
    schemas: dict,
    known_types: dict[str, str],
    heuristics: bool,
    path_prefix: str,
    in_array: bool,
    path_required: bool,
    visited: frozenset[str],
    depth: int,
) -> None:
    """정의의 프로퍼티를 순회하며 참조/포함 엣지를 수집한다.

    allow-list 밖의 정의로 인라인 $ref가 이어지면 (NLB의 targetGroup 등)
    깊이 제한 안에서 재귀하여, 발견한 참조 필드를 현재 리소스의 엣지로
    귀속시킨다 (via_property는 "targetGroup.subGroupId"처럼 점 표기).
    """
    required_fields = set(definition.get("required", []))
    for prop_name, prop in definition.get("properties", {}).items():
        if not isinstance(prop, dict):
            continue
        via = f"{path_prefix}{prop_name}"
        is_array = prop.get("type") == "array"
        items = prop.get("items", {}) if is_array else {}
        required = path_required and prop_name in required_fields
        many = in_array or is_array

        if prop_name in FIELD_TARGETS:
            target = FIELD_TARGETS[prop_name]
            graph.add_node(_core_node(target))
            graph.add_edge(
                Edge(
                    from_id=f"core::{resource_type}",
                    to_id=f"core::{target}",
                    type="references",
                    via_property=via,
                    required=required,
                    cardinality="many" if many else "one",
                    evidence="swagger-field",
                    # FIELD_TARGETS는 사람이 손으로 채운 표다(`vNetId` → `vNet`).
                    # 대상을 사람이 정했으므로 검수된 것으로 본다 — Azure의
                    # azure-references.json과 같은 성격이다.
                    reviewed=True,
                )
            )
            continue

        ref_name = _ref_name(prop) or (_ref_name(items) if is_array else None)
        if ref_name is not None:
            if ref_name in DEFINITION_TYPES:
                # 부모가 자식을 인라인 포함 → 자식 →contained_in→ 부모
                child = DEFINITION_TYPES[ref_name]
                graph.add_node(_core_node(child))
                graph.add_edge(
                    Edge(
                        from_id=f"core::{child}",
                        to_id=f"core::{resource_type}",
                        type="contained_in",
                        via_property=via,
                        required=True,
                        cardinality="one",
                        evidence="swagger-field",
                        # DEFINITION_TYPES도 손으로 채운 표다.
                        reviewed=True,
                    )
                )
            elif ref_name in schemas and ref_name not in visited and depth < _MAX_DEPTH:
                _walk_definition(
                    graph,
                    resource_type,
                    schemas[ref_name],
                    schemas=schemas,
                    known_types=known_types,
                    heuristics=heuristics,
                    path_prefix=f"{via}.",
                    in_array=many,
                    path_required=required,
                    visited=visited | {ref_name},
                    depth=depth + 1,
                )
            continue

        if heuristics:
            match = _ID_FIELD.match(prop_name)
            if match:
                target = known_types.get(match.group(1).lower())
                if target is not None:
                    graph.add_node(_core_node(target))
                    graph.add_edge(
                        Edge(
                            from_id=f"core::{resource_type}",
                            to_id=f"core::{target}",
                            type="references",
                            via_property=via,
                            required=required,
                            cardinality="many" if many else "one",
                            evidence="heuristic",
                        )
                    )


def build(
    output: Path,
    *,
    swagger_url: str = DEFAULT_SWAGGER_URL,
    heuristics: bool = False,
    refresh: bool = False,
) -> Graph:
    """swagger를 받아 파싱하고 output에 저장한 뒤 그래프를 반환한다."""
    path = fetch_cached(swagger_url, "tumblebug-swagger.json", refresh=refresh)
    spec = json.loads(path.read_text(encoding="utf-8"))
    graph = parse_swagger(spec, heuristics=heuristics)
    graph.provenance = [describe_source(path, "tumblebug-swagger")]
    announce(graph.save(output), "graphkb/tumblebug")
    print(
        f"tumblebug: 노드 {len(graph.nodes)}개, 엣지 {len(graph.edges)}개 → {output}"
    )
    return graph
