"""인용 좌표를 로컬 도서 사본과 대조한다 — 사본이 있을 때만 돈다.

책은 저작물이라 저장소에 없다(`d1a7ec5`). 그래서 CI에서는 **건너뛴다.** 대신 사본을
`materials/Usecase_Knowledge/`(gitignore됨)에 둔 사람의 로컬에서는 반드시 돈다.

이 배치가 맞는 이유: 인용은 손으로 옮겨 적는 것이라 틀린다. 실제로 두 건이 틀려 있었고
(`p.64`·`p.207`, `verify_citations.py` docstring 참고), 사본을 가진 사람이 검사를 한 번
돌리는 것으로 잡혔다. 검사가 로컬 전용이라도 **있는 것이 없는 것보다 낫다** — 없으면
아무도 안 돌린다.
"""
from __future__ import annotations

import pytest

from app.requirements.knowledge import verify_citations

pytestmark = pytest.mark.skipif(
    not verify_citations.DEFAULT_BOOK.exists(),
    reason=f"로컬 도서 사본이 없다({verify_citations.DEFAULT_BOOK}) — 저작물이라 저장소에 없다",
)


@pytest.fixture(scope="module")
def pages():
    """301쪽 파싱은 10초쯤 걸린다 — 모듈에서 한 번만 한다."""
    return verify_citations.load_pages()


def test_every_book_citation_points_at_the_right_page(pages):
    verdicts = verify_citations.verify_pages(pages)
    assert verdicts, "대조한 인용이 하나도 없다 — 규칙에 좌표가 빠졌다"

    failures = {v.rule_id: (v.citation, v.missing) for v in verdicts if not v.ok}
    assert not failures, f"인용이 그 페이지를 가리키지 않는다: {failures}"


def test_printed_page_offset_is_measured_not_assumed(pages):
    """오프셋은 사본마다 다를 수 있으므로 측정한다. 측정이 되는지만 본다."""
    assert 0 <= verify_citations.measure_offset(pages) < len(pages)
