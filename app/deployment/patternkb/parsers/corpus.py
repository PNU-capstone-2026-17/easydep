"""패턴 산문 코퍼스 빌드 — architecture-center 선별 하위 + 12factor.

## 선별의 기준

architecture-center `docs/`는 502편이다. 전부 담지 않는다 — 이 축이 답할 질문은
"이 설계 상황에 알려진 지침이 있나"이고, 거기 맞는 하위만 4곳 담는다(실측 2026-07-24):

    patterns/                   44편  클라우드 설계 패턴 (Retry·CQRS·Circuit Breaker …)
    guide/architecture-styles/   7편  아키텍처 스타일 (microservices·web-queue-worker …)
    guide/design-principles/    11편  설계 원칙 (self-healing·scale-out …)
    best-practices/             13편  실무 지침 (caching·message encoding …)

여기에 12factor(content/en, toc 제외 15편)를 더한다. Azure 참조 아키텍처류
(`docs/reference-architectures` 등)는 **안 담는다** — 특정 벤더 제품 조합의 안내라
svcmap·guideline 축과 역할이 겹치고, 산문으로 담으면 그 축들의 근거 규율(타입 id
대조)을 우회하게 된다.

## 문서 재편 감지

MS 문서는 재편된다(aws-professional/services.md 404 선례). 하위별 **최소 편수**를
불변식으로 박아, 트리에서 급감하면 빌드가 죽는다 — svcmap의 행수 검사와 같은 규율.

## 라이선스가 데이터에 실린다

CC-BY-4.0의 저작자 표시는 NOTICE에만 두면 파일이 저장소를 떠날 때 사라진다.
문서마다 `license`·`attribution` 칸을 넣어 **인용이 출처와 함께 다니게** 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.deployment.kbcommon.fetch import describe_source_set, fetch_cached, list_github_tree
from app.deployment.kbcommon.invariants import Invariant, Violation, announce
from app.deployment.kbcommon.sources import SOURCES
from app.deployment.patternkb.dataset import schema

_ARCH = "ms-architecture-center"
_TWELVE = "twelve-factor"
_WAF = "azure-well-architected"

_ARCH_API = "https://api.github.com/repos/MicrosoftDocs/architecture-center"
_TWELVE_API = "https://api.github.com/repos/heroku/12factor"
_WAF_API = "https://api.github.com/repos/MicrosoftDocs/well-architected"

_ARCH_ATTRIBUTION = (
    "Microsoft Corporation — MicrosoftDocs/architecture-center, CC BY 4.0"
)
_TWELVE_ATTRIBUTION = "Adam Wiggins — heroku/12factor, MIT"
_WAF_ATTRIBUTION = "Microsoft Corporation — MicrosoftDocs/well-architected, CC BY 4.0"

#: (경로 접두, section, 최소 편수). 최소치는 실측 편수보다 조금 낮게 잡는다 —
#: 한두 편의 이동은 흡수하고 하위 전체의 증발만 잡는 것이 목적이다.
_ARCH_SECTIONS = (
    ("patterns/", "patterns", 35),
    ("guide/architecture-styles/", "architecture-styles", 5),
    ("guide/design-principles/", "design-principles", 8),
    ("best-practices/", "best-practices", 10),
)
_TWELVE_MIN = 12  # 12요소보다 적으면 뭔가 잘못됐다
_WAF_MIN = 150  # 실측 199편 — 급감하면 저장소 재편

_GCP_FW_MIN = 40  # 실측 60± — 급감하면 사이트 재편(첫 HTML 소스라 특히 지켜본다)

#: 섹션별 최소 편수 — 불변식과 테스트가 공유한다(두 벌이면 드리프트한다).
SECTION_MINIMUMS: dict[str, int] = {
    **{section: minimum for _, section, minimum in _ARCH_SECTIONS},
    "twelve-factor": _TWELVE_MIN,
    "well-architected": _WAF_MIN,
    "gcp-framework": _GCP_FW_MIN,
}

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_TITLE_LINE = re.compile(r"^title:\s*(.+?)\s*$", re.M)
#: h1만 제목이다. `-content.md`(본문 조각)는 h1이 없고 첫 `##`가 "Context and
#: problem" 같은 절 제목이라, h2까지 받으면 제목이 절 이름이 된다 — 실측.
_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.M)


def _parse_markdown(raw: str, fallback_title: str) -> tuple[str, str]:
    """(제목, frontmatter 뗀 본문). 제목은 frontmatter → 첫 헤딩 → 파일명 순."""
    title = ""
    body = raw
    if match := _FRONTMATTER.match(raw):
        body = raw[match.end():]
        if title_match := _TITLE_LINE.search(match.group(1)):
            title = title_match.group(1).strip().strip("\"'")
    if not title and (heading := _HEADING.search(body)):
        title = heading.group(1).strip()
    return title or fallback_title, body.strip()


def _fallback_title(path: str) -> str:
    stem = Path(path).stem.removesuffix("-content")
    return stem.replace("-", " ").capitalize()


def _fetch_doc(source_key: str, rel_path: str, *, refresh: bool) -> tuple[str, Path]:
    source = SOURCES[source_key]
    cache_name = f"{source_key}-{rel_path.replace('/', '-')}"
    path = fetch_cached(f"{source.url}/{rel_path}", cache_name, refresh=refresh)
    return path.read_text(encoding="utf-8"), path


def _arch_docs(refresh: bool) -> tuple[list[dict], list[Path]]:
    pin = SOURCES[_ARCH].pin
    paths = list_github_tree(
        _ARCH_API, pin, "docs", cache_prefix="msarch-tree", refresh=refresh
    )
    docs: list[dict] = []
    fetched: list[Path] = []
    for prefix, section, _minimum in _ARCH_SECTIONS:
        selected = sorted(
            p for p in paths
            if p.startswith(prefix) and p.endswith(".md")
            and "/" not in p[len(prefix):]  # 하위 디렉터리(포함 조각 등)는 안 담는다
        )
        for path in selected:
            rel = f"docs/{path}"
            raw, cached = _fetch_doc(_ARCH, rel, refresh=refresh)
            fetched.append(cached)
            title, body = _parse_markdown(raw, _fallback_title(path))
            doc_id = path.removesuffix(".md").removesuffix("-content")
            docs.append({
                "id": doc_id,
                "title": title,
                "path": rel,
                "source": _ARCH,
                "section": section,
                "license": "CC-BY-4.0",
                "attribution": _ARCH_ATTRIBUTION,
                "url": (
                    "https://github.com/MicrosoftDocs/architecture-center/"
                    f"blob/{pin}/{rel}"
                ),
                "text": body,
            })
    return docs, fetched


def _twelve_docs(refresh: bool) -> tuple[list[dict], list[Path]]:
    pin = SOURCES[_TWELVE].pin
    paths = list_github_tree(
        _TWELVE_API, pin, "content", cache_prefix="twelvefactor-tree", refresh=refresh
    )
    docs: list[dict] = []
    fetched: list[Path] = []
    for path in sorted(paths):
        if not (path.startswith("en/") and path.endswith(".md")):
            continue
        if path == "en/toc.md":  # 목차 — 지침이 아니다
            continue
        rel = f"content/{path}"
        raw, cached = _fetch_doc(_TWELVE, rel, refresh=refresh)
        fetched.append(cached)
        slug = Path(path).stem
        title, body = _parse_markdown(raw, _fallback_title(path))
        docs.append({
            "id": f"twelve-factor/{slug}",
            "title": title,
            "path": rel,
            "source": _TWELVE,
            "section": "twelve-factor",
            "license": "MIT",
            "attribution": _TWELVE_ATTRIBUTION,
            "url": f"https://github.com/heroku/12factor/blob/{pin}/{rel}",
            "text": body,
        })
    return docs, fetched


def _waf_docs(refresh: bool) -> tuple[list[dict], list[Path]]:
    """Azure Well-Architected 지침 199편 (조사 2026-07-24 채택 3)."""
    pin = SOURCES[_WAF].pin
    paths = list_github_tree(
        _WAF_API, pin, "well-architected", cache_prefix="waf-tree", refresh=refresh
    )
    docs: list[dict] = []
    fetched: list[Path] = []
    for path in sorted(p for p in paths if p.endswith(".md")):
        rel = f"well-architected/{path}"
        raw, cached = _fetch_doc(_WAF, rel, refresh=refresh)
        fetched.append(cached)
        title, body = _parse_markdown(raw, _fallback_title(path))
        docs.append({
            "id": f"well-architected/{path.removesuffix('.md')}",
            "title": title,
            "path": rel,
            "source": _WAF,
            "section": "well-architected",
            "license": "CC-BY-4.0",
            "attribution": _WAF_ATTRIBUTION,
            "url": (
                "https://github.com/MicrosoftDocs/well-architected/"
                f"blob/{pin}/{rel}"
            ),
            "text": body,
        })
    return docs, fetched


def _invariants() -> list[Invariant]:
    minimums = SECTION_MINIMUMS

    def section_counts(dataset: dict):
        counts: dict[str, int] = {}
        for doc in dataset.get("docs") or []:
            counts[doc.get("section", "?")] = counts.get(doc.get("section", "?"), 0) + 1
        for section, minimum in minimums.items():
            if counts.get(section, 0) < minimum:
                yield Violation(
                    where=section,
                    detail=(
                        f"{counts.get(section, 0)}편 < 최소 {minimum}편 — "
                        "원본 문서가 재편됐는지 확인할 것(경로 목록이 낡았을 수 있다)"
                    ),
                )

    def docs_complete(dataset: dict):
        seen: set[str] = set()
        for doc in dataset.get("docs") or []:
            doc_id = doc.get("id", "?")
            if doc_id in seen:
                yield Violation(where=doc_id, detail="id가 중복된다")
            seen.add(doc_id)
            if len(doc.get("text", "")) < 100:
                yield Violation(
                    where=doc_id,
                    detail=f"본문이 {len(doc.get('text', ''))}자 — 빈 문서를 담으면 검색이 헛돈다",
                )

    return [
        Invariant(
            name="section-minimums",
            question="선별한 하위마다 문서가 최소 편수 이상 있는가 (문서 재편 감지)",
            severity="error",
            check=section_counts,
        ),
        Invariant(
            name="docs-complete",
            question="문서 id가 유일하고 본문이 비어 있지 않은가",
            severity="error",
            check=docs_complete,
        ),
    ]


def build(output: Path, *, refresh: bool = False) -> dict:
    from app.deployment.kbcommon.artifact import write_dataset

    from app.deployment.patternkb.parsers import gcp_framework

    arch_docs, arch_paths = _arch_docs(refresh)
    twelve_docs, twelve_paths = _twelve_docs(refresh)
    waf_docs, waf_paths = _waf_docs(refresh)
    gcpfw_docs, gcpfw_paths = gcp_framework.fetch_docs(refresh)
    docs = arch_docs + twelve_docs + waf_docs + gcpfw_docs

    coverage: dict[str, int] = {}
    for doc in docs:
        coverage[doc["section"]] = coverage.get(doc["section"], 0) + 1

    dataset = {
        "docs": docs,
        "_coverage": coverage,
        "_source": [
            describe_source_set(arch_paths, _ARCH),
            describe_source_set(twelve_paths, _TWELVE),
            describe_source_set(waf_paths, _WAF),
            describe_source_set(gcpfw_paths, "gcp-architecture-framework"),
        ],
    }
    result = write_dataset(output, dataset, schema(), _invariants())
    announce(result, "patternkb/corpus")
    parts = " · ".join(f"{k} {v}편" for k, v in sorted(coverage.items()))
    print(f"patternkb: 문서 {len(docs)}편 ({parts}) → {output}")
    return dataset
