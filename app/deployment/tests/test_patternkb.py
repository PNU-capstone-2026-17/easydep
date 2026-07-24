"""patternkb — 설계 지침 산문 검색 (advisory 전용).

여기 테스트가 고정하는 것은 검색 자체보다 **advisory 규율**이다.

    고지는 어디서도 안 빠진다     검색·무결과·coverage 전부에 "지침이지 사실 아님"
    라벨은 영원히 inferred        pattern-advisory는 검수해도 사실이 되지 않는다
    인용은 출처와 다닌다          문서 경로·라이선스·저작자 표시가 답에 실린다
    검색은 결정론이다             같은 코퍼스 같은 질의 → 같은 결과 (FTS5 + 동점 고정)
"""

from __future__ import annotations

import json
from pathlib import Path

from kbcommon.basis import INFERRED, basis_of, is_fact, needs_hedge
from kbcommon.display import evidence_name
from patternkb import dataset, query
from patternkb.agent_api import _MISSING, coverage_text, search_patterns
from patternkb.model import ADVISORY_NOTICE, EVIDENCE_ADVISORY
from patternkb.parsers.corpus import (
    SECTION_MINIMUMS,
    _fallback_title,
    _invariants,
    _parse_markdown,
)


# --- 근거 규율 -----------------------------------------------------------------

def test_advisory_label_is_inferred_and_never_a_fact() -> None:
    """**검수해도 사실이 되지 않는다.** 산문 지침은 클라우드 사실의 후보 자체가
    아니다 — reviewed를 붙이지 않는 것이 규약이고, 안 붙인 상태로 사실이 아니다."""
    basis = basis_of(EVIDENCE_ADVISORY)
    assert basis == INFERRED
    assert needs_hedge(basis)
    assert not is_fact(basis)


def test_advisory_label_display_says_not_a_fact() -> None:
    """표시 이름부터 "지침·사실 아님"이어야 한다 — "문서"라고만 하면 사실 소스처럼
    읽힌다."""
    label = evidence_name(EVIDENCE_ADVISORY)
    assert label != EVIDENCE_ADVISORY
    assert "지침" in label and "사실 아님" in label


# --- 픽스처 코퍼스 -------------------------------------------------------------

def _doc(doc_id: str, title: str, text: str, **overrides) -> dict:
    record = {
        "id": doc_id,
        "title": title,
        "path": f"docs/{doc_id}.md",
        "source": "ms-architecture-center",
        "section": "patterns",
        "license": "CC-BY-4.0",
        "attribution": "Microsoft Corporation — MicrosoftDocs/architecture-center, CC BY 4.0",
        "url": f"https://example.test/{doc_id}",
        "text": text,
    }
    record.update(overrides)
    return record


def _write_corpus(tmp_path: Path, docs: list[dict]) -> Path:
    out = tmp_path / "kbout"
    out.mkdir()
    (out / dataset.CORPUS_FILE).write_text(
        json.dumps({"docs": docs}, ensure_ascii=False), encoding="utf-8"
    )
    dataset.clear_caches()
    return out


_RETRY_TEXT = (
    "An application should handle transient faults by retrying the failed "
    "operation with an exponential backoff strategy so that the remote service "
    "is not overwhelmed while it recovers from the fault."
)
_QUEUE_TEXT = (
    "A message queue decouples the producer from the consumer so that bursts of "
    "load are leveled and the worker can process messages at its own pace "
    "without dropping requests during peak demand."
)


def test_search_returns_quote_with_source_and_notice(tmp_path) -> None:
    out = _write_corpus(tmp_path, [
        _doc("patterns/retry", "Retry", _RETRY_TEXT),
        _doc("patterns/queue-load", "Queue-Based Load Leveling", _QUEUE_TEXT),
    ])
    try:
        text = search_patterns("retry backoff", output_dir=out)
        assert "Retry" in text
        assert "«retrying»" in text or "«retry»" in text.lower()
        assert "docs/patterns/retry.md" in text          # 문서 경로
        assert "CC-BY-4.0" in text                       # 라이선스
        assert "Microsoft Corporation" in text           # 저작자 표시
        assert ADVISORY_NOTICE in text                   # 상시 고지
    finally:
        dataset.clear_caches()


def test_no_hit_and_coverage_still_carry_the_notice(tmp_path) -> None:
    """**고지는 어디서도 안 빠진다.** 무결과 답과 coverage도 이 축의 출력이다."""
    out = _write_corpus(tmp_path, [_doc("patterns/retry", "Retry", _RETRY_TEXT)])
    try:
        assert ADVISORY_NOTICE in search_patterns("zzzzunfindable", output_dir=out)
        assert ADVISORY_NOTICE in coverage_text(output_dir=out)
    finally:
        dataset.clear_caches()


def test_search_is_deterministic(tmp_path) -> None:
    out = _write_corpus(tmp_path, [
        _doc("patterns/retry", "Retry", _RETRY_TEXT),
        _doc("patterns/queue-load", "Queue-Based Load Leveling", _QUEUE_TEXT),
    ])
    try:
        first = query.search("message queue load", output_dir=out)
        second = query.search("message queue load", output_dir=out)
        assert first == second and first
    finally:
        dataset.clear_caches()


def test_missing_corpus_says_build_not_empty_result(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    dataset.clear_caches()
    try:
        assert search_patterns("retry", output_dir=empty) == _MISSING
        assert query.search("retry", output_dir=empty) == ()
    finally:
        dataset.clear_caches()


def test_fts_operator_characters_do_not_crash(tmp_path) -> None:
    """따옴표로 감싼 토큰만 MATCH에 넣는다 — `-`, `.` 같은 FTS5 연산자가 질의에
    섞여도 예외가 아니라 결과(또는 무결과)여야 한다."""
    out = _write_corpus(tmp_path, [_doc("patterns/retry", "Retry", _RETRY_TEXT)])
    try:
        query.search('retry- "unbalanced (NOT', output_dir=out)
        query.search("한글 질의도 죽지 않는다", output_dir=out)
        assert query.search("!!! ...", output_dir=out) == ()  # 토큰 없음
    finally:
        dataset.clear_caches()


# --- 파서 ----------------------------------------------------------------------

def test_frontmatter_title_wins_and_is_stripped() -> None:
    raw = "---\ntitle: Circuit Breaker Pattern\nms.author: x\n---\n\nBody text here.\n"
    title, body = _parse_markdown(raw, "fallback")
    assert title == "Circuit Breaker Pattern"
    assert "ms.author" not in body and body == "Body text here."


def test_content_fragments_fall_back_to_filename_not_section_heading() -> None:
    """`-content.md`는 h1이 없고 첫 `##`가 절 제목("Context and problem")이다 —
    h2를 제목으로 받으면 모든 조각 문서가 같은 제목이 된다(실측 결함)."""
    raw = "## Context and problem\n\nSome prose.\n"
    title, _ = _parse_markdown(raw, _fallback_title("patterns/retry-content.md"))
    assert title == "Retry"


def test_invariants_catch_section_collapse_and_empty_docs() -> None:
    """문서 재편(하위 증발)과 빈 문서를 빌드가 잡는다 — svcmap 행수 검사 계보."""
    from kbcommon.invariants import run

    good_docs = []
    for section, minimum in SECTION_MINIMUMS.items():
        good_docs.extend(
            _doc(f"{section}/d{i}", f"D{i}", _RETRY_TEXT, section=section)
            for i in range(minimum)
        )
    assert run({"docs": good_docs}, _invariants()).ok

    collapsed = [d for d in good_docs if d["section"] != "patterns"]
    assert not run({"docs": collapsed}, _invariants()).ok

    short = good_docs + [_doc("patterns/empty", "Empty", "too short")]
    assert not run({"docs": short}, _invariants()).ok

    duplicated = good_docs + [good_docs[0]]
    assert not run({"docs": duplicated}, _invariants()).ok


# --- GCP 프레임워크 (첫 HTML 소스 — 승인된 예외) --------------------------------

_HTML = """
<html><head><title>x</title></head><body>
<nav>사이드바 쓰레기</nav>
<article>
<devsite-toc>목차 쓰레기</devsite-toc>
<h1>Optimize continuously</h1>
<p>First paragraph of guidance.</p>
<script>evil()</script>
<h2>Recommendations</h2>
<li>Do the thing.</li>
</article>
<footer>푸터 쓰레기</footer>
</body></html>
"""


def test_html_extraction_takes_article_body_only() -> None:
    """구조 파싱이 아니라 본문 추출이다 — nav·script·toc·푸터는 버리고,
    article 밖은 아예 안 본다(HTML 변주에 파서가 깨지지 않게)."""
    from patternkb.parsers.gcp_framework import extract_text

    title, body = extract_text(_HTML)
    assert title == "Optimize continuously"
    assert "First paragraph" in body and "Do the thing." in body
    assert "쓰레기" not in body and "evil" not in body


def test_html_without_article_yields_nothing() -> None:
    """article 영역이 없으면 담지 않는다 — 빈 문서보다 부재가 낫고,
    급감은 최소 편수 불변식이 잡는다."""
    from patternkb.parsers.gcp_framework import extract_text

    assert extract_text("<html><body><p>x</p></body></html>") == ("", "")


def test_page_enumeration_excludes_printable_duplicates() -> None:
    from patternkb.parsers.gcp_framework import enumerate_pages

    index = (
        '<a href="/architecture/framework/cost-optimization">a</a>'
        '<a href="/architecture/framework/cost-optimization/printable">b</a>'
        '<a href="/architecture/framework/security/optimize-ai#frag">c</a>'
        '<a href="/other/page">d</a>'
    )
    assert enumerate_pages(index) == [
        "/architecture/framework/cost-optimization",
        "/architecture/framework/security/optimize-ai",
    ]


# --- 커밋된 코퍼스 (data/pattern-corpus.json.gz) --------------------------------

def test_bundled_corpus_docs_all_carry_license_and_attribution() -> None:
    """CC-BY의 저작자 표시는 데이터에서 떼면 안 된다 — NOTICE에만 있으면
    파일이 저장소를 떠날 때 사라진다."""
    docs = dataset.all_docs()
    assert docs, "코퍼스가 output/에도 data/에도 없다"
    for doc in docs:
        assert doc.license in ("CC-BY-4.0", "MIT"), doc.id
        assert doc.attribution, doc.id
        assert doc.path, doc.id


def test_bundled_corpus_finds_circuit_breaker() -> None:
    """FTS5 1단계가 최소한 제목 그대로의 질의는 맞혀야 한다 — 이게 깨지면
    임베딩 이전에 색인 자체가 잘못된 것이다."""
    hits = query.search("circuit breaker", limit=3)
    assert hits and hits[0].id == "patterns/circuit-breaker"


def test_bundled_corpus_sections_meet_minimums() -> None:
    counts = dataset.sections()
    for section, minimum in SECTION_MINIMUMS.items():
        assert counts.get(section, 0) >= minimum, section
