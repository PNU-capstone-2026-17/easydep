"""GCP 파서: Config Connector(KCC) CRD에서 벤더 레이어 그래프 추출.

소스는 github.com/GoogleCloudPlatform/k8s-config-connector의
config/crds/resources/ 아래 리소스별 CRD YAML (v1.153.0 태그 고정 —
파일 수가 800개 이상이라 기본은 선택 서비스만 내려받는다).

참조는 spec 스키마의 `*Ref` 필드(external/name/namespace 구조)로 표현되며,
대상 kind는 CRD 안에 구조화 메타데이터가 없어 3단계로 해석한다:

1. config/servicemappings/<service>.yaml 의 (kind, key) → gvk.kind
   (구조화된 명시 메타데이터: evidence=kcc-ref → 원본이 명시)
2. description 텍스트의 생성 패턴 정규식
   ("Allowed value: The `selfLink` field of a `ComputeNetwork` resource." /
   "externally managed ComputeNetwork resource" / "The name of a X resource")
   (evidence=kcc-description → 짐작)
3. 필드명 휴리스틱 (networkRef → *Network kind 유일 매칭)
   (evidence=heuristic → 짐작)

DCL 기반 CRD는 description이 generic이라 1번 없이는 대상을 알 수 없다.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

import yaml

from graphkb.fetch import fetch_cached
from graphkb.model import Edge, Graph, Node
from graphkb.parsers.review import apply_review, check_freshness
from kbcommon.invariants import announce
from kbcommon.fetch import describe_source_set
from kbcommon.sources import SOURCES

# 고정 태그는 kbcommon/sources.py에서 관리한다 (--tag로 덮어쓸 수 있다).
DEFAULT_TAG = SOURCES["kcc-crd"].pin
RAW_BASE = "https://raw.githubusercontent.com/GoogleCloudPlatform/k8s-config-connector"
API_BASE = "https://api.github.com/repos/GoogleCloudPlatform/k8s-config-connector"
# 기본은 **전체 서비스**다. compute·container만 받던 시절에는 부품 95개 중 14개가
# 빈 껍데기였다 — 엣지가 가리켜서 노드만 생기고 CRD를 안 읽어 아무것도 모르는 상태.
# "디스크가 KMS 키를 필요로 한다"는 알지만 "KMS 키는 무엇을 필요로 하나"에서 끊겼다.
# 전체를 받으면 부품 538개·관계 844개가 되고 잃는 관계는 없다(실측).
# 특정 서비스만 원하면 --services로 좁힌다.
DEFAULT_SERVICES: tuple[str, ...] = ()  # 빈 튜플 = 전체

SOURCE = "kcc-crd"

_REF_FIELD = re.compile(r"^(\w+?)Refs?$")
# KCC 종류 이름의 모양: 대문자로 시작하는 PascalCase (ComputeNetwork, IAMServiceAccount).
# 소문자로 시작하면 산문 속 영어 단어이지 종류가 아니다.
_KIND_NAME = re.compile(r"[A-Z][A-Za-z0-9]{2,}")
# 대상 필드까지 잡는 패턴 — 앞 백틱이 "대상의 어느 값을 쓰나"다.
_DESC_WITH_FIELD = re.compile(r"Allowed value: The `(\w+)` field of an? `(\w+)` resource")
_DESC_PATTERNS = (
    re.compile(r"Allowed value: The `\w+` field of an? `(\w+)` resource"),
    re.compile(r"externally managed (\w+) resource"),
    re.compile(r"The name of an? (\w+) resource"),
    re.compile(r"reference to an? (?:GCP )?(\w+)\b"),
)
_MAX_DEPTH = 24

# 설명문에서 대상 이름을 뽑았지만 실재하는 KCC 종류가 아니어서 버린 것들.
# **침묵시키지 않는다** — 정규식이 "externally" 같은 부사를 종류로 읽은 것(오탐)과
# KCC가 아직 안 만든 진짜 리소스(수집 공백)가 섞여 있고, 둘은 대응이 다르다.
UNKNOWN_DESC_TARGETS: collections.Counter = collections.Counter()


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


def build_sm_index(servicemappings: list[dict]) -> dict[tuple[str, str], tuple[str, str]]:
    """ServiceMapping에서 (kind, ref필드명) → (대상 kind, 대상 필드) 인덱스.

    `targetField`가 "대상의 어느 값을 가져다 쓰나"다 — AWS의 propertyPath에 해당한다.
    없으면 빈 문자열이고, 그건 KCC 기본값(selfLink 또는 name)을 쓴다는 뜻이지만
    무엇인지 단정할 수 없으므로 지어내지 않는다.
    """
    index: dict[tuple[str, str], tuple[str, str]] = {}
    for sm in servicemappings:
        for resource in sm.get("spec", {}).get("resources") or []:
            kind = resource.get("kind")
            if not kind:
                continue
            for ref in resource.get("resourceReferences") or []:
                key = ref.get("key")
                target = (ref.get("gvk") or {}).get("kind")
                if key and target:
                    index[(kind, key)] = (target, ref.get("targetField") or "")
    return index


def _resolve_target(
    kind: str,
    field_name: str,
    ref_schema: dict,
    *,
    sm_index: dict[tuple[str, str], str],
    known_kinds: set[str],
    heuristics: bool,
) -> tuple[str, str, str] | None:
    """참조 필드의 대상을 3단계로 해석한다. (kind, evidence, 대상필드).

    세 단계는 성격이 다르므로 **라벨도 다르다.** 1번은 ServiceMapping의 구조화
    필드라 원본이 명시한 것이고(`kcc-ref`), 2·3번은 산문과 이름에서 짐작한
    것이다(`kcc-description`, `heuristic`). 예전에는 1·2번이 같은 `kcc-ref`
    라벨을 쓰면서 신뢰도만 1.0/0.9로 갈렸는데, 그 탓에 라벨 단위 검수가
    **짐작까지 싸잡아 승인**해 버렸다(gcp-edges.json의 통짜 confirmed).
    """
    mapped = sm_index.get((kind, field_name))
    if mapped:
        return mapped[0], "kcc-ref", mapped[1]

    # 설명문에서 뽑은 이름은 **실재하는 종류인지 확인한다.** 정규식이 잡아낸 단어가
    # 곧 KCC 종류라는 보장이 없다 — 소문자 "service"를 종류로 읽어 `gcp::service`라는
    # 없는 노드를 만든 적이 있다(진짜 대상은 IAMServiceAccount였고, 같은 이름의 필드
    # 46개 중 45개는 이미 그리로 갔다). sm_index 경로는 출처가 확실해서 그대로 믿고,
    # 짐작 경로는 이미 known_kinds를 확인한다 — 이 경로만 빠져 있었다.
    def as_kind(name: str) -> str | None:
        """설명문에서 뽑은 낱말이 KCC 종류 이름인가.

        정규식이 잡아낸 단어가 곧 종류라는 보장이 없다. 실제로 `externally`(67곳),
        `parent`, `private`, `service`가 종류로 읽혀 있었고, 그중 소문자 `service`는
        `gcp::service`라는 없는 부품까지 만들었다(진짜 대상은 IAMServiceAccount).

        판별 기준은 **KCC의 작명 규칙**이다 — 종류 이름은 예외 없이 PascalCase다.
        산문 속 소문자 낱말은 종류가 아니라 그냥 영어 단어다. 이건 취향으로 고른
        금지어 목록이 아니라 소스 자체의 성질이다.

        CRD를 안 받은 종류라도 이름 모양이 맞으면 관계는 남긴다. `ComputeInstanceTemplate`
        처럼 스키마가 없어도 "이게 있어야 한다"는 사실 자체가 답이 되기 때문이다.
        """
        if not name or not _KIND_NAME.fullmatch(name):
            if name:
                UNKNOWN_DESC_TARGETS[name] += 1
            return None
        return name

    props = ref_schema.get("properties") or {}
    texts = [
        ref_schema.get("description") or "",
        (props.get("external") or {}).get("description") or "",
        (props.get("name") or {}).get("description") or "",
    ]
    for text in texts:
        # 첫 패턴만 대상 필드를 함께 준다 ("The `selfLink` field of a `X` resource").
        field_match = _DESC_WITH_FIELD.search(text)
        if field_match and (found := as_kind(field_match.group(2))):
            return found, "kcc-description", field_match.group(1)
        for pattern in _DESC_PATTERNS:
            match = pattern.search(text)
            if match and (found := as_kind(match.group(1))):
                return found, "kcc-description", ""

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
                return candidates[0], "heuristic", ""
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
    known_kinds = {target for target, _ in sm_index.values()}

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
                    target, evidence, target_field = resolved
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
                            target_property=target_field,
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
    read_paths: list[Path] = []

    if crd_dir is not None:
        for path in sorted(Path(crd_dir).glob("*.yaml")):
            read_paths.append(path)
            doc = _load_yaml(path)
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "CustomResourceDefinition":
                crds.append(doc)
            elif doc.get("kind") == "ServiceMapping":
                servicemappings.append(doc)
    else:
        wanted = {s.lower() for s in services}  # 비어 있으면 아래에서 전체 통과
        files = _list_config_files(tag, refresh=refresh)
        crd_files = [
            f
            for f in files
            if f.startswith("crds/resources/")
            and (not wanted or (_crd_service(f) or "") in wanted)
        ]
        sm_files = [
            f
            for f in files
            if f.startswith("servicemappings/")
            and (not wanted or Path(f).stem.lower() in wanted)
        ]
        for rel in crd_files:
            path = fetch_cached(
                f"{RAW_BASE}/{tag}/config/{rel}",
                f"kcc-{tag}-{Path(rel).name}",
                refresh=refresh,
            )
            read_paths.append(path)
            doc = _load_yaml(path)
            if isinstance(doc, dict):
                crds.append(doc)
        for rel in sm_files:
            path = fetch_cached(
                f"{RAW_BASE}/{tag}/config/{rel}",
                f"kcc-{tag}-sm-{Path(rel).name}",
                refresh=refresh,
            )
            read_paths.append(path)
            doc = _load_yaml(path)
            if isinstance(doc, dict):
                servicemappings.append(doc)
        print(
            f"gcp: CRD {len(crds)}개, servicemapping {len(servicemappings)}개 로드"
        )

    UNKNOWN_DESC_TARGETS.clear()
    graph = parse_crds(crds, servicemappings=servicemappings, heuristics=heuristics)
    graph.provenance = [describe_source_set(read_paths, "kcc-crd")]

    if UNKNOWN_DESC_TARGETS:
        top = ", ".join(
            f"{name}×{n}" for name, n in UNKNOWN_DESC_TARGETS.most_common(6)
        )
        print(
            f"· 설명문이 가리킨 이름 {len(UNKNOWN_DESC_TARGETS)}종"
            f"({sum(UNKNOWN_DESC_TARGETS.values())}곳)이 실재하는 KCC 종류가 아니라"
            f" 관계를 만들지 않았습니다: {top}",
            file=sys.stderr,
        )

    stale = check_freshness("gcp", graph.provenance)
    if stale:
        print(f"⚠ {stale}", file=sys.stderr)
    review_stats = apply_review(graph, "gcp")
    if any(review_stats.values()):
        print(
            f"검수 적용: 제거 {review_stats['dropped']}, "
            f"확인 표시 {review_stats['confirmed']}, 추가 {review_stats['added']}"
        )

    announce(graph.save(output), "graphkb/gcp")
    by_evidence: dict[str, int] = {}
    for edge in graph.edges:
        by_evidence[edge.evidence] = by_evidence.get(edge.evidence, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(
        f"gcp: 노드 {len(graph.nodes)}개, 엣지 {len(graph.edges)}개 ({summary}) → {output}"
    )
    return graph
