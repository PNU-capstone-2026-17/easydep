"""GCP 파서: Config Connector(KCC) CRD → 속성 제약 추출.

소스와 핀은 `graphkb/parsers/gcp.py`와 **같다**(`kcc-crd`, v1.153.0). 거기서는
관계를 뽑고 여기서는 제약을 뽑는다. 캐시도 공유하므로 그래프를 한 번 빌드했으면
네트워크를 다시 타지 않는다.

**먼저 알아야 할 것 — 여기서 수치 한도는 나오지 않는다.**
CRD 510개의 `spec` 서브트리를 전수로 세어본 결과다:

    required            2,631건 / 474종
    Immutable. 접두사   2,187건 / 363종
    enum                   17건 /  12종
    pattern                 6건 /   5종
    default                 7건 /   2종
    maxLength               1건 /   1종
    minimum · maximum       0건 /   0종   ← 하나도 없다

그러니 이 파서가 메우는 것은 **커버리지**(GCP는 지금 아무것도 못 답한다)이지
"얼마까지 되나"가 아니다. 그건 `document/kb-design-2026-07-21.md`의 D3 몫이다.
기대치를 여기 적어 두는 이유는, 나중에 "GCP 제약이 왜 이것뿐이냐"는 질문에
**안 뽑아서가 아니라 원본에 없어서**라고 답할 수 있어야 하기 때문이다.

**불변성은 두 소스를 다 읽는다.**
KCC는 불변 필드를 두 가지로 표시하는데 둘이 일치하지 않는다(실측):

    둘 다                55건
    CEL만 (접두사 없음)  19건   ← 접두사만 읽으면 놓친다
    접두사만          2,132건

접두사가 있는데 CEL이 변경을 허용하는 모순은 **0건**이다. 즉 접두사는 과다 보고를
하지 않고 **누락만** 한다. 그래서 둘을 합집합으로 읽고, 어느 쪽이 근거인지는
evidence로 남긴다. CEL 규칙은 98건 전부 `self == oldSelf` 한 가지 모양이다.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml

from capacitykb.model import CapacitySet, Constraint
from kbcommon.fetch import describe_source_set
from kbcommon.invariants import announce
from kbcommon.sources import SOURCES
from kbcommon.type_ids import make_type_id

DEFAULT_TAG = SOURCES["kcc-crd"].pin

#: 설명문이 `Immutable.`로 시작 — KCC의 표기 규약. 원본이 명시한 것이지 우리 짐작이 아니다.
EVIDENCE_PREFIX = "kcc-immutable-prefix"
#: `x-kubernetes-validations`의 `self == oldSelf` — 기계가 강제하는 불변성.
EVIDENCE_CEL = "kcc-cel-immutable"
#: OpenAPI 스키마 키워드 그대로 (required / enum / pattern / default / maxLength ...).
EVIDENCE_SCHEMA = "kcc-crd-schema"

#: OpenAPI 키워드 → 우리 제약 종류. `spec` 안에 실재하는 것만 적는다.
_KEYWORDS = {
    "enum": "enum",
    "pattern": "pattern",
    "default": "default",
    "maxLength": "max_length",
    "minLength": "min_length",
    "maximum": "max",
    "minimum": "min",
    "maxItems": "max_items",
    "minItems": "min_items",
}

_MAX_DEPTH = 24

#: 접두사와 CEL이 엇갈린 필드. 빌드 끝에 보고한다.
DISAGREEMENTS: list[tuple[str, str, str]] = []


def _is_ref_shape(schema: dict) -> bool:
    """KCC 참조 객체(`external`/`name`/`namespace`)인지.

    참조는 graphkb가 **관계**로 다루는 것이라 여기서 제약으로 또 뽑지 않는다.
    안 걸러내면 `networkRef.external`의 필수 여부 같은 게 제약으로 쌓여
    "GCP 제약 대부분이 참조 껍데기"인 상태가 된다.
    """
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


def _immutable_by_cel(schema: dict) -> bool:
    for rule in schema.get("x-kubernetes-validations") or []:
        if "oldSelf" in (rule.get("rule") or ""):
            return True
    return False


def parse_crds(crds: list[dict]) -> CapacitySet:
    """CRD 목록에서 제약을 뽑는다."""
    capacity = CapacitySet()
    DISAGREEMENTS.clear()
    seen_kinds: set[str] = set()

    def add(type_id: str, prop: str, kind: str, value, evidence: str) -> None:
        capacity.add_constraint(
            Constraint(
                type_id=type_id, property=prop, kind=kind,
                value=value, evidence=evidence,
            )
        )

    def walk(type_id: str, schema: dict, path: str, depth: int) -> None:
        if depth > _MAX_DEPTH or not isinstance(schema, dict):
            return
        if path and _is_ref_shape(schema):
            return

        if path:
            description = schema.get("description")
            by_prefix = isinstance(description, str) and description.startswith("Immutable.")
            by_cel = _immutable_by_cel(schema)
            if by_prefix or by_cel:
                # 둘 다면 기계가 강제하는 쪽(CEL)을 근거로 적는다 — 더 강한 증거다.
                add(type_id, path, "mutability", "create_only",
                    EVIDENCE_CEL if by_cel else EVIDENCE_PREFIX)
                if by_cel and not by_prefix:
                    DISAGREEMENTS.append((type_id, path, "CEL만 — 설명문에 표기 없음"))

            for keyword, our_kind in _KEYWORDS.items():
                if keyword in schema:
                    add(type_id, path, our_kind, schema[keyword], EVIDENCE_SCHEMA)

        for name in schema.get("required") or []:
            child = f"{path}.{name}" if path else name
            add(type_id, child, "required", True, EVIDENCE_SCHEMA)

        props = schema.get("properties")
        if isinstance(props, dict):
            for name, child in props.items():
                walk(type_id, child, f"{path}.{name}" if path else name, depth + 1)
        for key in ("items", "additionalProperties"):
            child = schema.get(key)
            if isinstance(child, dict):
                walk(type_id, child, path, depth + 1)

    for crd in crds:
        if crd.get("kind") != "CustomResourceDefinition":
            continue
        kind = ((crd.get("spec") or {}).get("names") or {}).get("kind")
        if not kind:
            continue
        version = _storage_version(crd)
        if not version:
            continue
        root = ((version.get("schema") or {}).get("openAPIV3Schema") or {})
        spec = (root.get("properties") or {}).get("spec")
        if not isinstance(spec, dict):
            continue
        seen_kinds.add(kind)
        walk(make_type_id("gcp", kind), spec, "", depth=0)

    capacity.coverage.append({
        "provider": "gcp",
        "types": len(seen_kinds),
        # 제약이 하나도 안 나온 타입(실측 39종)도 이름으로 찾히게 하려면 목록이 필요하다.
        "type_ids": sorted(make_type_id("gcp", k) for k in seen_kinds),
        "note": (
            "KCC CRD 전체를 읽는다. **수치 한도(min/max)는 원본에 0건**이므로 "
            "'GCP 한도를 모른다'는 안 뽑아서가 아니라 원본에 없어서다."
        ),
    })
    return capacity


def build(
    output: Path,
    *,
    tag: str = DEFAULT_TAG,
    refresh: bool = False,
    crd_dir: str | None = None,
) -> CapacitySet:
    """CRD를 받아 파싱하고 output에 저장한 뒤 결과를 반환한다."""
    from graphkb.parsers.gcp import _list_config_files, _load_yaml
    from kbcommon.fetch import fetch_cached

    crds: list[dict] = []
    read_paths: list[Path] = []

    if crd_dir is not None:
        for path in sorted(Path(crd_dir).glob("*.yaml")):
            doc = _load_yaml(path)
            if isinstance(doc, dict) and doc.get("kind") == "CustomResourceDefinition":
                read_paths.append(path)
                crds.append(doc)
    else:
        # graphkb와 **같은 캐시 파일명**을 쓴다. 그래프를 이미 빌드했으면 네트워크를 안 탄다.
        for rel in _list_config_files(tag, refresh=refresh):
            if not rel.startswith("crds/resources/"):
                continue
            path = fetch_cached(
                f"{SOURCES['kcc-crd'].url.rsplit('/', 1)[0]}/{tag}/config/{rel}",
                f"kcc-{tag}-{Path(rel).name}",
                refresh=refresh,
            )
            doc = _load_yaml(path)
            if isinstance(doc, dict):
                read_paths.append(path)
                crds.append(doc)
        print(f"gcp: CRD {len(crds)}개 로드")

    capacity = parse_crds(crds)
    capacity.provenance = [describe_source_set(read_paths, "kcc-crd")]

    if DISAGREEMENTS:
        print(
            f"gcp: 불변성 표기가 엇갈린 필드 {len(DISAGREEMENTS)}건 "
            "(CEL은 불변이라는데 설명문에 표기 없음) — 합집합으로 담았습니다.",
            file=sys.stderr,
        )
        for type_id, prop, why in DISAGREEMENTS[:5]:
            print(f"  - {type_id}.{prop}: {why}", file=sys.stderr)

    kinds = Counter(c.evidence for c in capacity.constraints)
    print(f"gcp: 제약 {len(capacity.constraints):,}건 — 근거별 {dict(kinds)}")
    announce(capacity.save(output), "capacitykb/gcp")
    return capacity
