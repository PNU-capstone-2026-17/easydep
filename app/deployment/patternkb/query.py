"""SQLite FTS5 검색 — patternkb의 값 표면.

## 왜 FTS5인가 (벡터가 아니라)

계획(app-layer-plan §④)이 정한 단계다: 1단계는 **표준 라이브러리·결정론·의존성 0**인
FTS5로 시작하고, 재현율 부족이 실측되면 그때 임베딩으로 올린다. 코퍼스가 ~90편이라
bm25로 충분한지부터 재는 것이 순서다.

## 결정론

색인은 커밋된 코퍼스에서 **메모리 안에** 만든다 — 디스크에 바이너리 DB를 커밋하면
재현 검증(바이트 대조)이 안 되고, JSON 코퍼스가 이미 진실의 원본이다. 같은 코퍼스
같은 질의는 같은 결과다: bm25 동점은 문서 id로 다시 정렬해 순서까지 고정한다.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from patternkb.dataset import _resolve, all_docs

#: 질의에서 살리는 토큰. 한글도 살린다 — 코퍼스가 영어라 매칭은 안 되지만,
#: 토큰이 0개가 되어 "결과 없음"의 이유가 사라지는 것보다 낫다.
_TOKEN = re.compile(r"[A-Za-z0-9가-힣]+")

_QUOTE_OPEN, _QUOTE_CLOSE, _ELLIPSIS = "«", "»", " … "


@dataclass(frozen=True)
class Hit:
    """검색 결과 하나 — 인용에 필요한 것 전부."""

    id: str
    title: str
    path: str
    section: str
    license: str
    attribution: str
    url: str
    quote: str
    """FTS5 snippet — 매칭 주변의 원문 발췌. 인용은 이걸 쓴다."""


@lru_cache(maxsize=4)
def _index(output_dir: str) -> sqlite3.Connection:
    """코퍼스의 인메모리 FTS5 색인. 코퍼스가 없으면 빈 색인."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        "CREATE VIRTUAL TABLE docs USING fts5("
        "doc_id UNINDEXED, title, section, body, tokenize='porter unicode61')"
    )
    conn.executemany(
        "INSERT INTO docs (doc_id, title, section, body) VALUES (?, ?, ?, ?)",
        [(d.id, d.title, d.section, d.text) for d in all_docs(output_dir)],
    )
    conn.commit()
    return conn


def clear_caches() -> None:
    _index.cache_clear()


def match_expression(query: str) -> str | None:
    """사용자 질의 → FTS5 MATCH 식. 토큰이 없으면 None.

    토큰을 **따옴표로 감싸 OR로 잇는다.** 따옴표는 FTS5 연산자 오작동(`-`, `.` 등)을
    막고, OR는 재현율을 위한 것이다 — 여러 단어 질의에서 전부 등장하는 문서만 남기면
    자문이 자주 빈다. 정밀도는 bm25 순위가 맡는다(많이 맞을수록 위로 온다).
    """
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def search(
    query: str, *, limit: int = 3, output_dir: Path | str | None = None
) -> tuple[Hit, ...]:
    """코퍼스 검색. 코퍼스가 없거나 토큰이 없으면 빈 튜플 — 예외를 던지지 않는다."""
    expression = match_expression(query)
    if expression is None:
        return ()
    resolved = _resolve(output_dir)
    docs = {d.id: d for d in all_docs(resolved)}
    if not docs:
        return ()
    rows = _index(resolved).execute(
        # bm25 가중치: title 5 > section 2 > body 1. snippet은 본문(3번 열)에서
        # 매칭 주변 32토큰 — 인용문이 문장 반쪽이면 지침이 왜곡된다.
        "SELECT doc_id, snippet(docs, 3, ?, ?, ?, 32), bm25(docs, 0.0, 5.0, 2.0, 1.0) AS rank "
        "FROM docs WHERE docs MATCH ? ORDER BY rank, doc_id LIMIT ?",
        (_QUOTE_OPEN, _QUOTE_CLOSE, _ELLIPSIS, expression, max(1, limit)),
    ).fetchall()
    hits = []
    for doc_id, quote, _rank in rows:
        doc = docs.get(doc_id)
        if doc is None:
            continue
        hits.append(Hit(
            id=doc.id, title=doc.title, path=doc.path, section=doc.section,
            license=doc.license, attribution=doc.attribution, url=doc.url,
            quote=" ".join(quote.split()),
        ))
    return tuple(hits)
