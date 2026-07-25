"""GCP Architecture Framework — 렌더링 HTML에서 산문 추출 (조사 2026-07-24 재상정 채택).

## 왜 HTML인가 — 그리고 왜 여기만 허용되나

이 프레임워크는 git 저장소가 없다(사이트 렌더링뿐). 처음엔 그 이유로 기각했지만
재조사에서 게이트가 전부 열렸다: 푸터가 CC-BY-4.0을 명시하고, robots.txt 차단이
없고, 색인 페이지에서 하위 링크가 기계 열거되며, digest 핀 선례(AWS zip 등)가
이미 있다. 남는 것은 "사람이 읽는 문서를 긁지 않는다"는 원칙의 형식뿐이었고 —
**용도가 산문 검색이라 구조 파싱이 필요 없다**(본문 텍스트만)는 근거로 사용자
승인을 받아 예외를 열었다(2026-07-24). **사실 축에는 이 방식을 쓰면 안 된다.**

## 추출은 보수적으로

devsite HTML의 `<article>` 영역만 취하고 script·style·nav류는 버린다. 표·코드
블록의 구조는 보존하지 않는다 — FTS 코퍼스라 본문 텍스트면 충분하고, 구조에
기대는 순간 HTML 변주가 파서를 깨뜨린다(데이터셋 > 파서). printable 변형은
필러 전체의 중복이라 뺀다.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from app.deployment.kbcommon.fetch import fetch_cached
from app.deployment.kbcommon.sources import SOURCES

_BASE = "https://cloud.google.com"
_INDEX = "/architecture/framework"

#: 프래그먼트(#)·쿼리(?)는 캡처에서 끊되 링크 자체는 버리지 않는다 — `#frag`가
#: 붙은 링크를 통째로 거르면 페이지가 조용히 빠진다(테스트가 잡은 결함).
_LINK = re.compile(r'href="(/architecture/framework/[^"#?]+)[^"]*"')

_ATTRIBUTION = "Google LLC — cloud.google.com/architecture/framework, CC BY 4.0"

#: 본문이 아닌 태그 — 이 안의 텍스트는 버린다.
_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "nav", "footer", "header", "button",
    "devsite-toc", "devsite-feedback", "devsite-header", "devsite-book-nav",
})
_BLOCK_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "li", "tr", "pre", "div"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.h1: str | None = None
        self._skip_depth = 0
        self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "h1" and self._skip_depth == 0:
            self._in_h1 = True

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._in_h1 and data.strip() and self.h1 is None:
            self.h1 = data.strip()


def extract_text(html: str) -> tuple[str, str]:
    """`(제목, 본문)`. `<article>` 영역만 — 없으면 빈 본문(담지 않게 된다)."""
    start = html.find("<article")
    end = html.rfind("</article>")
    if start == -1 or end == -1 or end <= start:
        return "", ""
    extractor = _TextExtractor()
    extractor.feed(html[start:end])
    text = unescape("".join(extractor.parts))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    body = "\n".join(ln for ln in lines if ln)
    return extractor.h1 or "", body


def enumerate_pages(index_html: str) -> list[str]:
    """색인에서 하위 경로 열거. printable(필러 전체 중복)은 뺀다."""
    seen: set[str] = set()
    for match in _LINK.finditer(index_html):
        path = match.group(1).rstrip("/")
        if path.endswith("/printable"):
            continue
        seen.add(path)
    return sorted(seen)


def fetch_docs(refresh: bool = False) -> tuple[list[dict], list[Path]]:
    """색인 → 페이지 전부 → patternkb 문서 목록."""
    source = SOURCES["gcp-architecture-framework"]
    index_path = fetch_cached(source.url, "gcpfw-index.html", refresh=refresh)
    fetched: list[Path] = [index_path]
    pages = enumerate_pages(index_path.read_text(encoding="utf-8"))
    if len(pages) < 40:
        raise RuntimeError(
            f"프레임워크 하위 페이지가 {len(pages)}개뿐이다(실측 기준 60±) — "
            "사이트가 재편됐는지 확인할 것."
        )
    docs: list[dict] = []
    for path in pages:
        slug = path.removeprefix("/architecture/framework/").replace("/", "-")
        cached = fetch_cached(f"{_BASE}{path}", f"gcpfw-{slug}.html", refresh=refresh)
        fetched.append(cached)
        title, body = extract_text(cached.read_text(encoding="utf-8"))
        if not body:
            continue  # article 영역이 없으면 담지 않는다 — 불변식이 급감을 잡는다
        docs.append({
            "id": f"gcp-framework/{path.removeprefix('/architecture/framework/')}",
            "title": title or slug.replace("-", " ").capitalize(),
            "path": path.lstrip("/"),
            "source": "gcp-architecture-framework",
            "section": "gcp-framework",
            "license": "CC-BY-4.0",
            "attribution": _ATTRIBUTION,
            "url": f"{_BASE}{path}",
            "text": body,
        })
    return docs, fetched
