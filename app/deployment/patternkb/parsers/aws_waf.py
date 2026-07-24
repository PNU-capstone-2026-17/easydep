"""AWS Well-Architected 화이트페이퍼 PDF → 코퍼스 (공정이용 수록).

## 왜 PDF인가 — 그리고 수록의 법적 근거

HTML 문서는 사이트 약관이 자동 수집을 금지해 막혔다. 이 PDF는 docs.aws.amazon.com이
**내려받으라고 배포하는 정식 산출물**이라 수집이 정당하다(사용자 제안, 재조사
2026-07-25). 법적 고지는 "All rights reserved"뿐이고(실측, 2쪽) 라이선스 부여
조항이 없다 — 산문 저작물이라 기본은 재배포 불가로 읽히지만, **졸업과제의 교육
목적 공정이용 판단으로 수록한다**(사용자 결정 2026-07-25). 그 판단은 숨기지 않고
세 곳에 밝힌다: NOTICE · 산출물 `_note` · 문서별 attribution. 권리자가 요청하면
제거한다. 판단 체계는 `kbcommon/sources.py`의 `redistribution="fair-use"`다.

## 왜 산문 휴리스틱이 아니라 책갈피인가

실측(1,002쪽): PDF 책갈피가 1,334개이고 계층·쪽 번호가 붙어 있다. 목차 구조가
기계로 주어지는데 본문을 정규식으로 자르는 것은 함정을 자초하는 일이다(산문 추출
함정 — kb-book 19장). 깊이 3까지의 책갈피를 문서 경계로 쓴다: 깊이 4부터는
베스트 프랙티스 낱개(647개)라 문서가 너무 잘게 쪼개진다 — 검색 단위는 "질문/절"
수준이 알맞다(azure WAF 199편과 같은 입도).

## pypdf는 지연 import

이 파서를 쓰는 명령(`build-aws-waf`)에서만 필요하다. 코퍼스를 읽기만 하는
환경(커밋된 산출물 사용)에 PDF 의존성을 강요하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.invariants import Invariant, Violation, announce
from kbcommon.sources import SOURCES
from patternkb.dataset import schema

_SOURCE = "aws-well-architected"
SECTION = "aws-well-architected"

_ATTRIBUTION = (
    "Amazon Web Services, Inc. — AWS Well-Architected Framework whitepaper, "
    "All rights reserved (교육 목적 공정이용 수록 — 요청 시 제거)"
)

#: 산출물 파일 자체에 실리는 재배포 상태 — 파일이 저장소를 떠나도 판단이 따라간다.
_NOTE = (
    "AWS 공식 화이트페이퍼 PDF에서 유도. 원본은 All rights reserved로 재배포 "
    "라이선스가 없으며, 졸업과제의 교육 목적 공정이용 판단으로 수록했다"
    "(사용자 결정 2026-07-25). 권리자가 요청하면 제거한다 — NOTICE 참조."
)

#: 문서 경계로 삼는 책갈피 깊이 (0 = 최상위). 실측 분포:
#: 0:12 · 1:16 · 2:55 · 3:103 · 4:307 · 5:647 — 3까지가 절 단위다.
_MAX_DEPTH = 3

#: 지침이 아닌 앞뒤 장치들 — 문서로 담지 않는다.
_SKIP_TITLES = frozenset({
    "AWS Well-Architected Framework",
    "Table of Contents",
    "Abstract and introduction",  # 자식(Introduction 등)이 본문을 갖는다
    "Notices",
    "Contributors",
    "Further reading",
    "Document revisions",
    "Appendix: Questions and best practices",  # 컨테이너 — 자식이 본문을 갖는다
})

#: 모든 쪽에 찍히는 머리글 — 본문에서 뗀다.
_RUNNING_HEADER = re.compile(
    r"^AWS Well-Architected Framework\s*(Framework)?\s*$", re.M
)
_PAGE_NUMBER_LINE = re.compile(r"^\s*\d{1,4}\s*$", re.M)

_MIN_TEXT = 200          # 이보다 짧으면 컨테이너 절이다 — 담지 않고 센다
_MIN_DOCS = 100          # 실측 ~150편 — 급감하면 PDF 재편


@dataclass(frozen=True)
class OutlineEntry:
    """책갈피 하나 — 파싱 로직을 pypdf 없이 테스트하기 위한 최소 표현."""

    depth: int
    title: str
    page: int


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _clean_page_text(raw: str) -> str:
    text = _RUNNING_HEADER.sub("", raw)
    text = _PAGE_NUMBER_LINE.sub("", text)
    return text


def split_sections(
    entries: list[OutlineEntry], page_text, total_pages: int
) -> tuple[list[dict], int]:
    """책갈피 목록 → 문서 레코드. (문서들, 건너뛴 짧은 절 수)를 돌려준다.

    경계 규칙: 깊이 ≤ `_MAX_DEPTH`인 책갈피가 문서를 연다. 문서의 본문은 그
    시작 쪽부터 **다음 경계 책갈피의 시작 쪽 전까지**다(더 깊은 책갈피는 본문에
    포함된다). 제목이 겹치므로("Design principles"가 기둥마다 있다) id·표시
    제목에 조상 사슬을 싣는다.
    """
    bounds = [e for e in entries if e.depth <= _MAX_DEPTH]
    docs: list[dict] = []
    skipped = 0
    chain: dict[int, str] = {}
    for i, entry in enumerate(bounds):
        chain[entry.depth] = entry.title
        for deeper in [d for d in chain if d > entry.depth]:
            del chain[deeper]
        if entry.title in _SKIP_TITLES:
            continue
        end_page = bounds[i + 1].page if i + 1 < len(bounds) else total_pages
        end_page = max(end_page, entry.page + 1)
        body = "\n".join(
            _clean_page_text(page_text(p)) for p in range(entry.page, end_page)
        ).strip()
        if len(body) < _MIN_TEXT:
            skipped += 1
            continue
        ancestors = [chain[d] for d in sorted(chain) if d < entry.depth]
        title = " › ".join([*ancestors[-1:], entry.title]) if ancestors else entry.title
        doc_id = "/".join([SECTION, *(_slug(t) for t in [*ancestors, entry.title])])
        docs.append({
            "id": f"{doc_id}@p{entry.page + 1}",  # 제목 사슬이 같아도 쪽으로 유일
            "title": title,
            "path": f"wellarchitected-framework.pdf#page={entry.page + 1}",
            "source": _SOURCE,
            "section": SECTION,
            "license": "All-rights-reserved",
            "attribution": _ATTRIBUTION,
            "url": SOURCES[_SOURCE].url + f"#page={entry.page + 1}",
            "text": body,
        })
    return docs, skipped


def _read_outline(reader) -> list[OutlineEntry]:
    entries: list[OutlineEntry] = []

    def walk(items, depth: int) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(item)
            except Exception:  # noqa: BLE001 — 깨진 목적지는 담지 않고 센다
                continue
            entries.append(OutlineEntry(depth=depth, title=str(item.title), page=page))

    walk(reader.outline, 0)
    return entries


def fetch_docs(refresh: bool = False) -> tuple[list[dict], list[Path], int]:
    """(문서들, 프로버넌스 경로, 건너뛴 절 수). pypdf가 없으면 안내하고 실패한다."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise SystemExit(
            "pypdf가 필요합니다 (로컬 빌드 전용 명령) — "
            "`uv pip install pypdf` 후 다시 실행하세요."
        ) from exc

    pdf_path = fetch_cached(
        SOURCES[_SOURCE].url, "aws-wellarchitected-framework.pdf", refresh=refresh
    )
    reader = PdfReader(str(pdf_path))
    entries = _read_outline(reader)
    if not entries:
        raise SystemExit(
            "PDF에 책갈피가 없습니다 — 원본이 재편된 것이니 파서 전제를 다시 재세요."
        )
    docs, skipped = split_sections(
        entries,
        lambda p: reader.pages[p].extract_text() or "",
        len(reader.pages),
    )
    return docs, [pdf_path], skipped


def _invariants() -> list[Invariant]:
    def enough_docs(dataset: dict):
        count = len(dataset.get("docs") or [])
        if count < _MIN_DOCS:
            yield Violation(
                where=SECTION,
                detail=f"{count}편 < 최소 {_MIN_DOCS}편 — PDF 구조가 재편됐는지 확인",
            )

    def docs_complete(dataset: dict):
        seen: set[str] = set()
        for doc in dataset.get("docs") or []:
            doc_id = doc.get("id", "?")
            if doc_id in seen:
                yield Violation(where=doc_id, detail="id가 중복된다")
            seen.add(doc_id)

    return [
        Invariant(
            name="aws-waf-minimum",
            question="PDF에서 지침 절이 최소 편수 이상 나왔는가 (재편 감지)",
            severity="error",
            check=enough_docs,
        ),
        Invariant(
            name="aws-waf-unique-ids",
            question="문서 id가 유일한가",
            severity="error",
            check=docs_complete,
        ),
    ]


def build(output: Path, *, refresh: bool = False) -> dict:
    from kbcommon.artifact import write_dataset

    docs, paths, skipped = fetch_docs(refresh)
    dataset = {
        "docs": docs,
        "_note": _NOTE,
        "_coverage": {SECTION: len(docs)},
        "_source": [describe_source_set(paths, _SOURCE)],
    }
    result = write_dataset(output, dataset, schema(), _invariants())
    announce(result, "patternkb/aws-waf")
    print(
        f"patternkb: AWS WAF 문서 {len(docs)}편 (짧은 컨테이너 절 {skipped}개 제외) "
        f"→ {output}\n"
        "※ 원본은 All rights reserved — 교육 목적 공정이용 수록(NOTICE 참조)."
    )
    return dataset
