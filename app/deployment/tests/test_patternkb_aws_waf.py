"""AWS WAF PDF 코퍼스 — 공정이용 수록 규율과 분할 로직.

지키는 것 둘:
1. **공정이용 판단이 판단으로 실려 다니는가** — 허가가 아니라는 사실이 산출물
   `_note`·문서별 attribution에 있고, NOTICE 강제는 test_redistribution_notice.py.
2. **책갈피 분할이 옳은가** — 겹치는 절 제목("Design principles"가 기둥마다
   있다)이 문맥으로 갈리고, 컨테이너·앞뒤 장치는 담지 않고 센다.
"""

from __future__ import annotations

import json
from pathlib import Path

from patternkb import agent_api, dataset
from patternkb.parsers.aws_waf import (
    OutlineEntry,
    _clean_page_text,
    split_sections,
)

BUNDLED_DIR = Path(__file__).resolve().parent.parent / "data"

LONG = "steady content line for a guidance section. " * 10  # > _MIN_TEXT


# --- 1. 공정이용 수록 규율 ------------------------------------------------------


def test_committed_aws_corpus_carries_the_fair_use_note() -> None:
    """수록은 허가가 아니라 판단이다 — 판단이 파일 안에 실려 함께 다녀야 한다."""
    from kbcommon import artifact

    packed = BUNDLED_DIR / "aws-pattern-corpus.json.gz"
    assert packed.exists(), (
        "aws 코퍼스가 커밋돼 있지 않다 — 수록 결정(2026-07-25)과 어긋난다. "
        "python -m patternkb build-aws-waf 후 kbcommon pack."
    )
    data = artifact.load_json(packed)
    # 산출물 `_note`는 영어로 옮겼다. 지키려는 것은 **공정이용 판단이 파일에
    # 실려 함께 다니는가**이지 그 문장의 언어가 아니다 — 두 표기를 다 받는다.
    note = " ".join((data.get("_note") or "").split()).lower()
    assert "공정이용" in note or "fair use" in note
    assert data.get("docs"), "커밋된 코퍼스가 비어 있다"


# --- 2. 책갈피 분할 ------------------------------------------------------------


def _entries() -> list[OutlineEntry]:
    return [
        OutlineEntry(0, "AWS Well-Architected Framework", 0),   # 표지 — 건너뜀
        OutlineEntry(0, "Table of Contents", 1),                # 목차 — 건너뜀
        OutlineEntry(0, "The pillars of the framework", 2),     # 컨테이너 — 본문 짧음
        OutlineEntry(1, "Operational excellence", 3),
        OutlineEntry(2, "Design principles", 4),
        OutlineEntry(1, "Security", 6),
        OutlineEntry(2, "Design principles", 7),
    ]


def _page_text(page: int) -> str:
    if page in (2,):
        return "short container"
    return f"AWS Well-Architected Framework Framework\n{LONG}\n{page}\n"


def test_repeated_section_titles_stay_distinct() -> None:
    """"Design principles"가 기둥마다 있어도 id·제목이 문맥으로 갈린다."""
    docs, skipped = split_sections(_entries(), _page_text, total_pages=9)
    principles = [d for d in docs if "Design principles" in d["title"]]
    assert len(principles) == 2
    titles = {d["title"] for d in principles}
    assert titles == {
        "Operational excellence › Design principles",
        "Security › Design principles",
    }
    ids = {d["id"] for d in principles}
    assert len(ids) == 2
    assert any("operational-excellence" in i for i in ids)
    assert any("security" in i for i in ids)


def test_front_matter_and_containers_are_skipped_not_included() -> None:
    docs, skipped = split_sections(_entries(), _page_text, total_pages=9)
    titles = {d["title"] for d in docs}
    assert "Table of Contents" not in titles
    assert "AWS Well-Architected Framework" not in titles
    assert skipped == 1  # 컨테이너("The pillars…")는 짧아서 담지 않고 센다


def test_running_header_and_page_numbers_are_stripped() -> None:
    cleaned = _clean_page_text(_page_text(4))
    assert "AWS Well-Architected Framework" not in cleaned
    assert "\n4\n" not in f"\n{cleaned}\n"
    docs, _ = split_sections(_entries(), _page_text, total_pages=9)
    assert all("AWS Well-Architected Framework Framework" not in d["text"] for d in docs)


def test_docs_carry_fair_use_license_facts() -> None:
    """인용이 나갈 때 라이선스 상태가 함께 다닌다 — CC-BY의 attribution 규율과 동형."""
    docs, _ = split_sections(_entries(), _page_text, total_pages=9)
    for doc in docs:
        assert doc["license"] == "All-rights-reserved"
        assert "공정이용" in doc["attribution"]
        assert doc["section"] == "aws-well-architected"


# --- 3. dataset 병합 — 미빌드와 수록 없음의 구분 --------------------------------


def _write_corpus(path: Path, doc_id: str, source: str, section: str,
                  license_: str) -> None:
    path.write_text(json.dumps({"docs": [{
        "id": doc_id, "title": doc_id, "path": "p", "source": source,
        "section": section, "license": license_, "attribution": "a",
        "url": "https://example.test", "text": LONG,
    }]}), encoding="utf-8")


def test_aws_corpus_merges_and_built_state_is_distinguished(tmp_path: Path) -> None:
    _write_corpus(tmp_path / dataset.CORPUS_FILE,
                  "twelve-factor/config", "twelve-factor", "twelve-factor", "MIT")
    dataset.clear_caches()
    try:
        assert not dataset.aws_built(tmp_path)
        assert len(dataset.all_docs(tmp_path)) == 1
        # 미빌드 → coverage가 "없는 게 아니라 이 환경에 안 실렸다"를 말한다
        text = agent_api.coverage_text(tmp_path)
        assert "build-aws-waf" in text

        _write_corpus(tmp_path / dataset.AWS_CORPUS_FILE,
                      "aws-well-architected/sec", "aws-well-architected",
                      "aws-well-architected", "All-rights-reserved")
        dataset.clear_caches()
        assert dataset.aws_built(tmp_path)
        docs = dataset.all_docs(tmp_path)
        assert len(docs) == 2
        assert dataset.sections(tmp_path)["aws-well-architected"] == 1
        text = " ".join(agent_api.coverage_text(tmp_path).split())
        assert "build-aws-waf" not in text
        assert "AWS Well-Architected guidance 1" in text
    finally:
        dataset.clear_caches()
