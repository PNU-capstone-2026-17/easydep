"""관리형 서비스의 버전별 지원 종료일 (endoflife.date).

**무엇을 답하나.** "EKS 1.28을 언제까지 쓸 수 있나?" "RDS MySQL 5.7 아직 지원되나?"
버전을 고르는 일은 앱을 만들 때 늘 하는 결정인데, 지금까지 이 축은 **0건**이었다
(`document/gap-map-2026-07-22.md`의 "아예 없는 축").

리소스 타입에 딸린 사실이지만 `Constraint`(속성 하나에 걸린 제약) 모델에 안 맞는다 —
"버전 1.28의 종료일 2026-11-26"은 속성 제약이 아니다. 그래서 탄소와 같이 별도
산출물로 두고, `type_id`로 다른 축과 조인한다.

## 라이선스 — frontmatter만 취한다

저장소는 MIT이지만 README가 본문 설명을 *"adapted from Wikipedia, CC BY-SA 3.0"*
이라고 밝힌다. 그래서 **`---` 사이의 YAML frontmatter에서 `releases:`만 읽고 그
뒤의 산문은 버린다.** 우리가 담는 것은 날짜와 버전 문자열뿐이라 MIT 범위 안에 남는다.
이건 우연이 아니라 **의도적인 경계**다 — 파서가 본문을 아예 읽지 않는다.

## 날짜 필드가 여럿이다

    eoas  active support 종료   (버그 수정이 끝나는 날)
    eol   지원 종료             (보안 패치도 끝나는 날 — 이게 기준선이다)
    eoes  extended support 종료 (돈을 더 내면 연장되는 날)

`eol`이 우리가 쓰는 기준이다. `eoes`가 있으면 "연장 지원은 여기까지"로 함께 밝힌다 —
연장이 있는데 없다고 하면 아직 쓸 수 있는 버전을 못 쓴다고 답하게 된다.

**`false`가 올 수 있다.** endoflife.date는 "종료일이 아직 안 정해졌다"를 `false`로
적는다. 그걸 날짜로 읽으면 안 되고, **"종료 예정일 미정"과 "이미 종료"는 다른 답**이다.

## 타입 매핑은 손으로 한다

제품 이름(`amazon-eks`)과 우리 타입 id(`aws::AWS::EKS::Cluster`)는 규칙으로 못 잇는다.
15개 안팎이라 손으로 적고, **그 타입이 실재하는지는 빌드가 확인한다**(graphkb 노드와
대조). 손매핑은 틀리면 엉뚱한 타입의 수명을 답하게 되므로 검증 없이 두지 않는다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from kbcommon.artifact import load_json, resolve, write_dataset
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

try:
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _Loader = yaml.SafeLoader

#: 담을 제품과 우리 타입 id. **손매핑이다** — 규칙으로 못 잇는다.
#: `None`은 "우리 타입에 대응물이 없지만 제품 이름으로는 물어볼 수 있다"는 뜻.
PRODUCTS: dict[str, str | None] = {
    "amazon-eks": "aws::AWS::EKS::Cluster",
    "amazon-rds-mysql": "aws::AWS::RDS::DBInstance",
    "amazon-rds-postgresql": "aws::AWS::RDS::DBInstance",
    "amazon-rds-mariadb": "aws::AWS::RDS::DBInstance",
    "amazon-aurora-postgresql": "aws::AWS::RDS::DBCluster",
    "amazon-documentdb": "aws::AWS::DocDB::DBCluster",
    "amazon-elasticache-redis": "aws::AWS::ElastiCache::CacheCluster",
    "amazon-msk": "aws::AWS::MSK::Cluster",
    "amazon-neptune": "aws::AWS::Neptune::DBCluster",
    "amazon-opensearch": "aws::AWS::OpenSearchService::Domain",
    "aws-lambda": "aws::AWS::Lambda::Function",
    "azure-kubernetes-service": "azure::Microsoft.ContainerService/managedClusters",
    "azure-database-for-mysql": "azure::Microsoft.DBforMySQL/flexibleServers",
    "azure-database-for-postgresql": "azure::Microsoft.DBforPostgreSQL/flexibleServers",
    "google-kubernetes-engine": None,  # KCC 타입 이름이 우리 인덱스와 다르다
    "alibaba-ack": None,
    "amazon-linux": None,  # OS라 리소스 타입이 없다
}

#: frontmatter는 `---` 두 줄 사이에 있다. 그 뒤(본문)는 **읽지 않는다** — 라이선스가 다르다.
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

SCHEMA = {
    "type": "object",
    "required": ["products", "_source"],
    "properties": {
        "_note": {"type": "string"},
        "_source": {"type": "array"},
        "unmapped_types": {"type": "array", "items": {"type": "string"}},
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["product", "releases"],
                "additionalProperties": False,
                "properties": {
                    "product": {"type": "string", "minLength": 1},
                    "title": {"type": ["string", "null"]},
                    "type_id": {"type": ["string", "null"]},
                    "releases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["cycle"],
                            "additionalProperties": False,
                            "properties": {
                                "cycle": {"type": "string", "minLength": 1},
                                "released": {"type": ["string", "null"]},
                                # 날짜 문자열이거나 null(=원본이 false, 미정)
                                "eol": {"type": ["string", "null"]},
                                "eoas": {"type": ["string", "null"]},
                                "eoes": {"type": ["string", "null"]},
                                "latest": {"type": ["string", "null"]},
                            },
                        },
                    },
                },
            },
        },
    },
}


def _date(value) -> str | None:
    """날짜를 문자열로. `False`(미정)와 `True`는 **날짜가 아니므로** None이다.

    endoflife.date는 "종료일이 안 정해졌다"를 `false`로 적는다. 이걸 날짜처럼
    다루면 "미정"이 "종료됨"으로 읽힌다 — 정반대 답이다.
    """
    if value is None or isinstance(value, bool):
        return None
    return str(value)


def parse_product(name: str, text: str, type_id: str | None) -> dict | None:
    """제품 markdown → 릴리스 레코드. **frontmatter만 읽는다.**"""
    found = _FRONTMATTER.match(text)
    if not found:
        return None
    try:
        meta = yaml.load(found.group(1), Loader=_Loader) or {}
    except yaml.YAMLError:
        return None

    releases = []
    for entry in meta.get("releases") or []:
        if not isinstance(entry, dict):
            continue
        cycle = entry.get("releaseCycle")
        if cycle is None:
            continue
        releases.append({
            "cycle": str(cycle),
            "released": _date(entry.get("releaseDate")),
            "eol": _date(entry.get("eol")),
            "eoas": _date(entry.get("eoas")),
            "eoes": _date(entry.get("eoes")),
            "latest": str(entry["latest"]) if entry.get("latest") else None,
        })
    if not releases:
        return None
    return {
        "product": name,
        "title": str(meta.get("title") or name),
        "type_id": type_id,
        "releases": releases,
    }


def build(output: Path, *, refresh: bool = False, graph_dir: Path | None = None) -> dict:
    source = SOURCES["endoflife-date"]
    products: list[dict] = []
    paths = []
    for name, type_id in PRODUCTS.items():
        path = fetch_cached(
            f"{source.url}{name}.md", f"eol-{name}-{source.pin}.md", refresh=refresh
        )
        paths.append(path)
        record = parse_product(name, path.read_text(encoding="utf-8"), type_id)
        if record:
            products.append(record)

    # **손매핑을 검증한다.** 틀린 타입을 적으면 엉뚱한 리소스의 수명을 답하게 된다.
    unmapped = _check_types(products, graph_dir or Path("output"))

    dataset = {
        "_note": (
            "관리형 서비스의 버전별 지원 종료일(endoflife.date). YAML frontmatter의 "
            "releases만 담는다 — 본문 산문은 라이선스가 달라(CC BY-SA) 읽지 않는다. "
            "eol이 null이면 '이미 종료'가 아니라 **종료일 미정**이다."
        ),
        "_source": [describe_source_set(paths, source.key)],
        "unmapped_types": unmapped,
        "products": products,
    }
    write_dataset(output, dataset, SCHEMA)

    cycles = sum(len(p["releases"]) for p in products)
    mapped = sum(1 for p in products if p["type_id"])
    print(
        f"service-lifecycle: 제품 {len(products)}종 · 버전 {cycles:,}개 "
        f"(타입 매핑 {mapped}종)"
    )
    if unmapped:
        print(
            f"  ⚠ graphkb에 없는 타입 {len(unmapped)}개 — 손매핑을 고쳐야 합니다: "
            + ", ".join(unmapped)
        )
    return dataset


def _check_types(products: list[dict], graph_dir: Path) -> list[str]:
    """손매핑한 type_id가 graphkb에 실재하는지 대조한다."""
    nodes: set[str] = set()
    for path in sorted(graph_dir.glob("*-graph.json")):
        try:
            data = load_json(path)
        except Exception:
            continue
        found = data.get("nodes")
        nodes |= set(found) if isinstance(found, dict) else {
            n["id"] for n in found or [] if isinstance(n, dict) and "id" in n
        }
    if not nodes:
        return []  # 그래프가 없으면 검사할 수 없다 — 없다고 단정하지 않는다
    return sorted(
        {p["type_id"] for p in products if p["type_id"] and p["type_id"] not in nodes}
    )


# --------------------------------------------------------------------------
# 조회 — 탄소(`carbon.py`)와 같은 자리에서 문장까지 만든다.
# --------------------------------------------------------------------------

ARTIFACT = "service-lifecycle.json"

_MISSING = (
    "수명주기 산출물이 없습니다. `python -m kbcommon build-lifecycle` 로 생성하세요."
)


@lru_cache(maxsize=4)
def _load(output_dir: str) -> tuple[dict, ...]:
    path = resolve(Path(output_dir), ARTIFACT)
    if path is None:
        return ()
    try:
        data = load_json(path)
    except Exception:
        return ()
    return tuple(data.get("products") or ())


def find(query: str, *, output_dir: str = "output") -> list[dict]:
    """제품 이름·타입 id·부분 문자열로 제품을 찾는다."""
    text = query.strip().lower()
    rows = _load(output_dir)
    exact = [p for p in rows if p["product"] == text or (p.get("type_id") or "").lower() == text]
    if exact:
        return exact
    # 타입 이름은 접두사 없이 물어올 수 있다(`AWS::EKS::Cluster`).
    tail = [
        p for p in rows
        if p.get("type_id") and (p["type_id"].split("::", 1)[-1].lower() == text)
    ]
    if tail:
        return tail
    return [
        p for p in rows
        if text in p["product"] or text in (p.get("title") or "").lower()
    ]


def _status(release: dict, today: str) -> str:
    """오늘 기준 한 버전의 상태. **미정과 종료를 구분한다.**"""
    eol = release.get("eol")
    if eol is None:
        return "종료일 미정"
    if eol <= today:
        extended = release.get("eoes")
        if extended and extended > today:
            return f"지원 종료({eol}) · 연장 지원은 {extended}까지"
        return f"지원 종료됨 ({eol})"
    return f"{eol}까지 지원"


def describe(
    query: str,
    cycle: str | None = None,
    *,
    today: str | None = None,
    output_dir: str = "output",
) -> str:
    """관리형 서비스 버전의 지원 종료일. 사람이 읽는 답."""
    from datetime import date

    if not _load(output_dir):
        return _MISSING
    stamp = today or date.today().isoformat()
    found = find(query, output_dir=output_dir)
    if not found:
        known = ", ".join(p["product"] for p in _load(output_dir)[:8])
        return (
            f"'{query}'의 수명주기 정보가 없습니다 — 그 서비스에 종료일이 없다는 뜻이 "
            f"아니라 **이 소스에 수록되지 않았다**는 뜻입니다.\n"
            f"  수록된 제품 예: {known}"
        )

    product = found[0]
    releases = product["releases"]
    if cycle is not None:
        want = cycle.strip()
        match = [r for r in releases if r["cycle"] == want]
        if not match:
            near = ", ".join(r["cycle"] for r in releases[:8])
            return (
                f"{product['title']}: '{want}' 버전을 찾지 못했습니다.\n"
                f"  수록된 버전: {near}"
            )
        releases = match

    lines = [f"{product['title']} 지원 종료일 (오늘 {stamp} 기준):"]
    for release in releases[:10]:
        line = f"  - {release['cycle']}: {_status(release, stamp)}"
        if release.get("latest"):
            line += f" · 최신 {release['latest']}"
        lines.append(line)
    if len(releases) > 10:
        lines.append(f"  … 외 {len(releases) - 10}개 버전")
    if len(found) > 1:
        others = ", ".join(p["product"] for p in found[1:5])
        lines.append(f"  ※ 같은 이름으로 걸린 다른 제품: {others}")
    lines.append(
        "  ※ eol이 '종료일 미정'인 것은 아직 안 정해진 것이지 종료된 게 아닙니다."
    )
    return "\n".join(lines)
