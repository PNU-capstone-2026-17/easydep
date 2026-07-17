"""GCP 파서: Config Connector(KCC) CRD에서 벤더 레이어 그래프 추출.

소스는 github.com/GoogleCloudPlatform/k8s-config-connector의
config/crds/resources/ 아래 리소스별 CRD YAML (v1.153.0 태그 고정 —
파일 수가 800개 이상이라 기본은 선택 서비스만 내려받는다).

참조는 spec 스키마의 `*Ref` 필드(external/name/namespace 구조)로 표현되며,
대상 kind는 CRD 안에 구조화 메타데이터가 없어 3단계로 해석한다:

1. config/servicemappings/<service>.yaml 의 (kind, key) → gvk.kind
   (구조화된 명시 메타데이터: evidence=kcc-ref, confidence 1.0)
2. description 텍스트의 생성 패턴 정규식
   ("Allowed value: The `selfLink` field of a `ComputeNetwork` resource." /
   "externally managed ComputeNetwork resource" / "The name of a X resource")
   (evidence=kcc-ref, confidence 0.9)
3. 필드명 휴리스틱 (networkRef → *Network kind 유일 매칭)
   (evidence=heuristic, confidence 0.6/0.5)

DCL 기반 CRD는 description이 generic이라 1번 없이는 대상을 알 수 없다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

from graphkb.fetch import fetch_cached
from graphkb.model import Edge, Graph, Node

DEFAULT_TAG = "v1.153.0"
RAW_BASE = "https://raw.githubusercontent.com/GoogleCloudPlatform/k8s-config-connector"
API_BASE = "https://api.github.com/repos/GoogleCloudPlatform/k8s-config-connector"
DEFAULT_SERVICES = ("compute", "container")

SOURCE = "kcc-crd"

_REF_FIELD = re.compile(r"^(\w+?)Refs?$")
_DESC_PATTERNS = (
    re.compile(r"Allowed value: The `\w+` field of an? `(\w+)` resource"),
    re.compile(r"externally managed (\w+) resource"),
    re.compile(r"The name of an? (\w+) resource"),
    re.compile(r"reference to an? (?:GCP )?(\w+)\b"),
)
_MAX_DEPTH = 24


def _node(kind: str) -> Node:
    return Node(
        id=f"gcp::{kind}",
        layer="vendor",
        provider="gcp",
        display_name=kind,
        source=SOURCE,
    )


def _is_ref_shape(schema: dict) -> bool:
    """external/name/namespace 하위 프로퍼티를 가진 KCC 참조 객체인지 판별."""
    props = schema.get("properties")
    if not isinstance(props, dict):
        return False
    return "external" in props or {"name", "namespace"} <= set(props)


def _storage_version(crd: dict) -> dict | None:
    versions = crd.get("spec", {}).get("versions") or []
    for version in versions:
        if version.get("storage"):
            return version
    return versions[-1] if versions else None


def _service_of_kind(kind: str) -> str:
    """"ComputeNetwork" → "compute" (kind의 선행 대문자 단어)."""
    match = re.match(r"^([A-Z][a-z0-9]+)", kind)
    return match.group(1).lower() if match else ""


def build_sm_index(servicemappings: list[dict]) -> dict[tuple[str, str], str]:
    """ServiceMapping 문서들에서 (kind, ref필드명) → 대상 kind 인덱스 생성."""
    index: dict[tuple[str, str], str] = {}
    for sm in servicemappings:
        for resource in sm.get("spec", {}).get("resources") or []:
            kind = resource.get("kind")
            if not kind:
                continue
            for ref in resource.get("resourceReferences") or []:
                key = ref.get("key")
                target = (ref.get("gvk") or {}).get("kind")
                if key and target:
                    index[(kind, key)] = target
    return index


def _resolve_target(
    kind: str,
    field_name: str,
    ref_schema: dict,
    *,
    sm_index: dict[tuple[str, str], str],
    known_kinds: set[str],
    heuristics: bool,
) -> tuple[str, str, float] | None:
    """참조 필드의 대상 kind를 3단계로 해석한다. (kind, evidence, confidence)."""
    target = sm_index.get((kind, field_name))
    if target:
        return target, "kcc-ref", 1.0

    props = ref_schema.get("properties") or {}
    texts = [
        ref_schema.get("description") or "",
        (props.get("external") or {}).get("description") or "",
        (props.get("name") or {}).get("description") or "",
    ]
    for text in texts:
        for pattern in _DESC_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1), "kcc-ref", 0.9

    if heuristics:
        base = _REF_FIELD.match(field_name)
        if base:
            needle = base.group(1).lower()
            candidates = [k for k in known_kinds if k.lower().endswith(needle)]
            if len(candidates) != 1:
                service = _service_of_kind(kind)
                candidates = [
                    k for k in candidates if _service_of_kind(k) == service
                ]
            if len(candidates) == 1:
                target = candidates[0]
                same = _service_of_kind(target) == _service_of_kind(kind)
                return target, "heuristic", 0.6 if same else 0.5
    return None


def parse_crds(
    crds: list[dict],
    *,
    servicemappings: list[dict] | None = None,
    heuristics: bool = True,
) -> Graph:
    """CRD 문서 목록(+선택적 ServiceMapping)에서 GCP 그래프를 만든다."""
    graph = Graph()
    sm_index = build_sm_index(servicemappings or [])
    known_kinds = {k for _, k in sm_index.items()}

    specs: list[tuple[str, dict]] = []  # (kind, spec 스키마)
    for crd in crds:
        if not isinstance(crd, dict) or crd.get("kind") != "CustomResourceDefinition":
            continue
        kind = crd.get("spec", {}).get("names", {}).get("kind")
        version = _storage_version(crd)
        if not kind or version is None:
            continue
        schema = (
            version.get("schema", {})
            .get("openAPIV3Schema", {})
            .get("properties", {})
            .get("spec")
        )
        graph.add_node(_node(kind))
        known_kinds.add(kind)
        if isinstance(schema, dict):
            specs.append((kind, schema))

    def walk(kind: str, schema: dict, path: str, *, in_array: bool, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        required_set = set(schema.get("required") or [])
        for prop_name, prop in (schema.get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            via = f"{path}.{prop_name}" if path else prop_name
            target_schema = prop
            many = in_array
            if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
                target_schema = prop["items"]
                many = True

            match = _REF_FIELD.match(prop_name)
            if match and _is_ref_shape(target_schema):
                resolved = _resolve_target(
                    kind,
                    prop_name,
                    target_schema,
                    sm_index=sm_index,
                    known_kinds=known_kinds,
                    heuristics=heuristics,
                )
                if resolved is not None:
                    target, evidence, confidence = resolved
                    graph.add_node(_node(target))
                    graph.add_edge(
                        Edge(
                            from_id=f"gcp::{kind}",
                            to_id=f"gcp::{target}",
                            type="references",
                            via_property=via,
                            required=prop_name in required_set,
                            cardinality="many" if many else "one",
                            evidence=evidence,
                            confidence=confidence,
                        )
                    )
                continue  # Ref 필드 내부(external/name)는 더 내려가지 않음

            if isinstance(target_schema, dict):
                walk(kind, target_schema, via, in_array=many, depth=depth + 1)

    for kind, schema in specs:
        walk(kind, schema, "", in_array=False, depth=0)
    return graph


def _list_config_files(tag: str, *, refresh: bool) -> list[str]:
    """git trees API로 config/ 아래 파일 경로 목록을 얻는다 (캐시됨)."""
    root_path = fetch_cached(
        f"{API_BASE}/git/trees/{tag}", f"kcc-tree-root-{tag}.json", refresh=refresh
    )
    root = json.loads(root_path.read_text(encoding="utf-8"))
    config_sha = next(
        (e["sha"] for e in root.get("tree", []) if e.get("path") == "config"), None
    )
    if config_sha is None:
        raise FileNotFoundError("config/ 디렉터리를 저장소 트리에서 찾지 못했습니다.")
    sub_path = fetch_cached(
        f"{API_BASE}/git/trees/{config_sha}?recursive=1",
        f"kcc-tree-config-{tag}.json",
        refresh=refresh,
    )
    sub = json.loads(sub_path.read_text(encoding="utf-8"))
    if sub.get("truncated"):
        print("경고: config/ 트리 목록이 잘렸습니다 — 일부 CRD가 누락될 수 있음", file=sys.stderr)
    return [e["path"] for e in sub.get("tree", []) if e.get("type") == "blob"]


def _crd_service(filename: str) -> str | None:
    """CRD 파일명에서 서비스명 추출.

    예: apiextensions.k8s.io_v1_customresourcedefinition_
        computesubnetworks.compute.cnrm.cloud.google.com.yaml → "compute"
    """
    last = filename.rsplit("_", 1)[-1]
    parts = last.split(".")
    return parts[1] if len(parts) > 2 else None


def _load_yaml(path: Path) -> dict | None:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"경고: YAML 파싱 실패, 건너뜀 — {path.name}: {exc}", file=sys.stderr)
        return None


def build(
    output: Path,
    *,
    tag: str = DEFAULT_TAG,
    services: tuple[str, ...] = DEFAULT_SERVICES,
    heuristics: bool = True,
    refresh: bool = False,
    crd_dir: str | None = None,
) -> Graph:
    """CRD를 받아 파싱하고 output에 저장한 뒤 그래프를 반환한다.

    Args:
        crd_dir: 지정하면 네트워크 대신 로컬 디렉터리의 *.yaml을 사용
            (CRD와 ServiceMapping을 kind로 구분해 읽음).
    """
    crds: list[dict] = []
    servicemappings: list[dict] = []

    if crd_dir is not None:
        for path in sorted(Path(crd_dir).glob("*.yaml")):
            doc = _load_yaml(path)
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "CustomResourceDefinition":
                crds.append(doc)
            elif doc.get("kind") == "ServiceMapping":
                servicemappings.append(doc)
    else:
        wanted = {s.lower() for s in services}
        files = _list_config_files(tag, refresh=refresh)
        crd_files = [
            f
            for f in files
            if f.startswith("crds/resources/")
            and (_crd_service(f) or "") in wanted
        ]
        sm_files = [
            f
            for f in files
            if f.startswith("servicemappings/")
            and Path(f).stem.lower() in wanted
        ]
        for rel in crd_files:
            path = fetch_cached(
                f"{RAW_BASE}/{tag}/config/{rel}",
                f"kcc-{tag}-{Path(rel).name}",
                refresh=refresh,
            )
            doc = _load_yaml(path)
            if isinstance(doc, dict):
                crds.append(doc)
        for rel in sm_files:
            path = fetch_cached(
                f"{RAW_BASE}/{tag}/config/{rel}",
                f"kcc-{tag}-sm-{Path(rel).name}",
                refresh=refresh,
            )
            doc = _load_yaml(path)
            if isinstance(doc, dict):
                servicemappings.append(doc)
        print(
            f"gcp: CRD {len(crds)}개, servicemapping {len(servicemappings)}개 로드"
        )

    graph = parse_crds(crds, servicemappings=servicemappings, heuristics=heuristics)
    graph.save(output)
    by_evidence: dict[str, int] = {}
    for edge in graph.edges:
        by_evidence[edge.evidence] = by_evidence.get(edge.evidence, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(
        f"gcp: 노드 {len(graph.nodes)}개, 엣지 {len(graph.edges)}개 ({summary}) → {output}"
    )
    return graph
