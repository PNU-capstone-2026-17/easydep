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
"얼마까지 되나"가 아니다. 그건 `git 히스토리의 kb-design-2026-07-21.md`의 D3 몫이다.
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

from app.core.cloudkb.capacitykb.model import CapacitySet, Constraint
from app.core.cloudkb.kbcommon.fetch import describe_source_set
from app.core.cloudkb.kbcommon.invariants import announce
from app.core.cloudkb.kbcommon.sources import SOURCES
from app.core.cloudkb.kbcommon.type_ids import make_type_id

DEFAULT_TAG = SOURCES["kcc-crd"].pin
#: graphkb의 KCC 파서와 같은 저장소를 본다 — 캐시 접두(kcc-tree)도 같아서
#: 한쪽이 이미 빌드했으면 트리 목록에 네트워크를 안 탄다.
_API_BASE = "https://api.github.com/repos/GoogleCloudPlatform/k8s-config-connector"

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

#: kind → KCC가 아는 속성 경로. `parse_crds`가 채운다.
KCC_PATHS: dict[str, set[str]] = {}


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


#: CRD가 스스로 밝히는 백엔드 라벨. 셋의 신선도가 다르다 —
#: `tf2crd`는 2023-09-26에 나온 terraform-provider-google-beta 4.84.0을 벤더링한 것에서
#: 스키마를 뽑는다. 실측으로 허용값이 낡은 리소스 5/5가 전부 tf2crd였다.
_BACKEND_LABELS = {
    "cnrm.cloud.google.com/tf2crd": "tf2crd",
    "cnrm.cloud.google.com/dcl2crd": "dcl2crd",
}


def _backend(crd: dict) -> str:
    """이 CRD를 무엇이 만들었나. 라벨이 없으면 direct(손으로 쓴 Go 타입)."""
    labels = (crd.get("metadata") or {}).get("labels") or {}
    for label, name in _BACKEND_LABELS.items():
        if labels.get(label) == "true":
            return name
    return "direct"


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
    backends: Counter[str] = Counter()
    #: kind → KCC가 아는 속성 경로 전체. 프로바이더 값을 어디에 붙일지 판정하는 데 쓴다.
    KCC_PATHS.clear()

    def add(type_id: str, prop: str, kind: str, value, evidence: str,
            backend: str) -> None:
        capacity.add_constraint(
            Constraint(
                type_id=type_id, property=prop, kind=kind,
                value=value, evidence=evidence, backend=backend,
            )
        )

    def walk(type_id: str, schema: dict, path: str, depth: int, backend: str) -> None:
        if depth > _MAX_DEPTH or not isinstance(schema, dict):
            return
        if path and _is_ref_shape(schema):
            return

        if path:
            KCC_PATHS.setdefault(type_id.split("::", 1)[1], set()).add(path)
            description = schema.get("description")
            by_prefix = isinstance(description, str) and description.startswith("Immutable.")
            by_cel = _immutable_by_cel(schema)
            if by_prefix or by_cel:
                # 둘 다면 기계가 강제하는 쪽(CEL)을 근거로 적는다 — 더 강한 증거다.
                add(type_id, path, "mutability", "create_only",
                    EVIDENCE_CEL if by_cel else EVIDENCE_PREFIX, backend)
                if by_cel and not by_prefix:
                    DISAGREEMENTS.append((type_id, path, "CEL만 — 설명문에 표기 없음"))

            for keyword, our_kind in _KEYWORDS.items():
                if keyword in schema:
                    add(type_id, path, our_kind, schema[keyword],
                        EVIDENCE_SCHEMA, backend)

        for name in schema.get("required") or []:
            child = f"{path}.{name}" if path else name
            add(type_id, child, "required", True, EVIDENCE_SCHEMA, backend)

        props = schema.get("properties")
        if isinstance(props, dict):
            for name, child in props.items():
                walk(type_id, child, f"{path}.{name}" if path else name,
                     depth + 1, backend)
        for key in ("items", "additionalProperties"):
            child = schema.get(key)
            if isinstance(child, dict):
                walk(type_id, child, path, depth + 1, backend)

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
        backends[_backend(crd)] += 1
        walk(make_type_id("gcp", kind), spec, "", depth=0, backend=_backend(crd))

    capacity.coverage.append({
        "provider": "gcp",
        "types": len(seen_kinds),
        # 제약이 하나도 안 나온 타입(실측 39종)도 이름으로 찾히게 하려면 목록이 필요하다.
        "type_ids": sorted(make_type_id("gcp", k) for k in seen_kinds),
        "backends": dict(backends),
        "note": (
            "reads all KCC CRDs. **the source carries 0 numeric limits (min/max)**, "
            "so 'we do not know GCP limits' is because the source has none, not "
            "because we failed to extract them."
        ),
    })
    return capacity


def merge_provider(
    kcc: CapacitySet, tpg: CapacitySet, report=None
) -> tuple[CapacitySet, dict]:
    """KCC와 프로바이더를 **출처 계보에 따른 우선순위**로 합친다.

    규칙은 출처 분석에서 그대로 나온다:

    - `backend == "tf2crd"`인 레코드는 **프로바이더가 이긴다.** tf2crd는 애초에
      2023-09-26판 프로바이더를 벤더링한 것이므로, 같은 파이프라인의 3개 메이저
      최신판이 엄격히 대체한다.
    - `direct`·`dcl2crd`는 **KCC가 이긴다.** direct는 GCP proto에 주석으로 붙어 있는
      독립 소스이고, 그쪽에서 보면 프로바이더가 오히려 파생물이다.

    구현은 `add_constraint`가 이미 first-wins라는 성질을 쓴다 — 이길 것을 먼저 넣는다.
    새 비교 로직을 모델에 넣지 않아도 되고, 순서만 보면 규칙이 읽힌다.
    """
    stale = {c.type_id for c in kcc.constraints if c.backend == "tf2crd"}
    old = {(c.type_id, c.property, c.kind): c.value for c in kcc.constraints}

    def superseded(c: Constraint) -> bool:
        """낡은 KCC의 '불변' 표시를, 프로바이더가 **갱신 가능하다고 아는** 경우 버린다.

        프로바이더의 침묵이 아니라 **적극적 근거**일 때만이다 — 그 속성을 알면서
        ForceNew를 안 달았을 때. 이게 없으면 낡은 값을 지울 방법이 없어서
        `ComputeSubnetwork.purpose`가 옛 `Immutable.` 표기 그대로 남는다.
        """
        if report is None or c.backend != "tf2crd" or c.kind != "mutability":
            return False
        if c.value != "create_only":
            return False
        kind = c.type_id.split("::", 1)[1]
        return (
            c.property in report.seen.get(kind, ())
            and c.property not in report.forcenew.get(kind, ())
        )

    dropped = [c for c in kcc.constraints if superseded(c)]
    kept = [c for c in kcc.constraints if not superseded(c)]

    merged = CapacitySet()
    first = [c for c in tpg.constraints if c.type_id in stale]
    for constraint in first:            # 낡은 타입은 프로바이더가 먼저
        merged.add_constraint(constraint)
    for constraint in kept:             # 그다음 KCC (이미 찬 자리는 못 들어간다)
        merged.add_constraint(constraint)
    for constraint in tpg.constraints:  # 나머지 프로바이더 값은 빈자리에만
        merged.add_constraint(constraint)
    merged.coverage = list(kcc.coverage)

    changed = sum(
        1 for c in first
        if (c.type_id, c.property, c.kind) in old
        and old[(c.type_id, c.property, c.kind)] != c.value
    )
    return merged, {
        "dropped_stale_immutable": len(dropped),
        "dropped_examples": [(c.type_id[5:], c.property) for c in dropped[:6]],
        "refreshed_types": len({c.type_id for c in first}),
        "overwritten": sum(1 for c in first if (c.type_id, c.property, c.kind) in old),
        "value_changed": changed,
        "added": len(merged.constraints) - len(kcc.constraints),
    }


def build(
    output: Path,
    *,
    tag: str = DEFAULT_TAG,
    refresh: bool = False,
    crd_dir: str | None = None,
    provider: bool = True,
) -> CapacitySet:
    """CRD를 받아 파싱하고 output에 저장한 뒤 결과를 반환한다."""
    from app.core.cloudkb.kbcommon.fetch import fetch_cached, list_github_tree, load_yaml_lenient

    crds: list[dict] = []
    read_paths: list[Path] = []

    if crd_dir is not None:
        for path in sorted(Path(crd_dir).glob("*.yaml")):
            doc = load_yaml_lenient(path)
            if isinstance(doc, dict) and doc.get("kind") == "CustomResourceDefinition":
                read_paths.append(path)
                crds.append(doc)
    else:
        # graphkb와 **같은 캐시 파일명**(kcc-tree-…)을 쓴다. 그래프를 이미
        # 빌드했으면 네트워크를 안 탄다.
        for rel in list_github_tree(
            _API_BASE, tag, "config", cache_prefix="kcc-tree", refresh=refresh
        ):
            if not rel.startswith("crds/resources/"):
                continue
            path = fetch_cached(
                f"{SOURCES['kcc-crd'].url.rsplit('/', 1)[0]}/{tag}/config/{rel}",
                f"kcc-{tag}-{Path(rel).name}",
                refresh=refresh,
            )
            doc = load_yaml_lenient(path)
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

    if provider:
        capacity = _add_provider(capacity, refresh=refresh)

    kinds = Counter(c.evidence for c in capacity.constraints)
    print(f"gcp: 제약 {len(capacity.constraints):,}건 — 근거별 {dict(kinds)}")
    announce(capacity.save(output), "capacitykb/gcp")
    return capacity


def _add_provider(capacity: CapacitySet, *, refresh: bool) -> CapacitySet:
    """프로바이더 릴리스에서 값을 보강한다 (실패해도 빌드를 죽이지 않는다)."""
    from app.core.cloudkb.capacitykb.parsers import tpg
    from app.core.cloudkb.kbcommon.fetch import fetch_cached

    source = SOURCES["tpg-provider"]
    try:
        tar_path = fetch_cached(
            source.url, f"tpg-{source.pin}.tar.gz", refresh=refresh
        )
    except Exception as exc:  # 네트워크가 없다고 GCP 빌드 전체가 죽을 이유는 없다
        print(f"gcp: 프로바이더를 못 받아 보강을 건너뜁니다 — {exc}", file=sys.stderr)
        return capacity

    kinds = {t.split("::", 1)[1] for t in {c.type_id for c in capacity.constraints}}
    extra, report = tpg.parse_provider(
        tar_path, kcc_kinds=kinds, kcc_paths=KCC_PATHS
    )
    merged, stat = merge_provider(capacity, extra, report)
    merged.provenance = list(capacity.provenance) + [
        describe_source_set([tar_path], source.key)
    ]

    if stat["dropped_stale_immutable"]:
        print(
            f"  낡은 '불변' 표시 {stat['dropped_stale_immutable']}건을 **지웠습니다** — "
            "프로바이더가 그 속성을 알면서 재생성 표시를 안 했습니다(=갱신 가능)."
        )
        for kind, prop in stat["dropped_examples"]:
            print(f"    - {kind}.{prop}")
    print(
        f"gcp: 프로바이더 {source.pin} 보강 — 제약 {len(extra.constraints):,}건에서 "
        f"낡은 타입 {stat['refreshed_types']}종을 갱신"
        f"(덮어씀 {stat['overwritten']:,}, 그중 값이 실제로 바뀐 것 {stat['value_changed']:,}), "
        f"새로 추가 {stat['added']:,}건"
    )
    # 조용히 자르지 않는다 — 못 붙인 것을 세어 밝힌다.
    # "못 붙임"이라 뭉뚱그리면 우리 실패처럼 읽힌다. 대부분은 우리 실패가 아니라
    # 프로바이더가 KCC보다 새 필드를 안다는 뜻이다(실측 확인: KCC v1.153.0의
    # AccessContextManagerAccessLevel에 vpcNetworkSources가 0건, 프로바이더엔 있다).
    print(
        f"  안 담은 것: KCC에 없는 리소스 {len(report.unmapped_kinds)}종 · "
        f"프로바이더엔 있고 KCC v1.153.0엔 없는 속성 {report.unmapped_paths:,}건 · "
        f"Terraform 전용 필드 {report.tf_only_paths:,}건 · "
        f"서버가 채우는 출력 필드 {report.output_only:,}건",
        file=sys.stderr,
    )
    if report.empty_groups:
        print(
            f"  교차 조건 {report.empty_groups}건은 프로바이더에서 **빈 목록**이라 "
            "담지 않았습니다 (Magic Modules가 선언했으나 생성 과정에서 증발한 것).",
            file=sys.stderr,
        )
    if report.force_new_if:
        print(f"  조건부 불변 {len(report.force_new_if)}건:", file=sys.stderr)
        for kind, prop, pred in report.force_new_if[:8]:
            print(f"    - {kind}.{prop} ({pred})", file=sys.stderr)
    return merged
