"""에이전트용 사전 정의 질의 API (patternkb) — **advisory 전용**.

**이 KB의 답은 사실이 아니라 인용이다.** 다른 축은 "원본이 이렇게 말했다"를 값으로
옮기지만, 여기는 산문을 발췌해 보여줄 뿐이다. 그래서 모든 답에:

- 인용문 (FTS5 snippet — «…» 표시)
- 문서 경로 · 라이선스 · 저작자 표시 (인용이 출처와 함께 다닌다)
- **"설계 지침이지 클라우드 사실이 아닙니다"** (한 번도 빠지면 안 된다)
"""

from __future__ import annotations

from pathlib import Path

from kbcommon.display import evidence_name
from patternkb.dataset import is_built, load_warnings, sections
from patternkb.model import ADVISORY_NOTICE, EVIDENCE_ADVISORY
from patternkb.query import search

_MISSING = (
    "설계 패턴 코퍼스가 없습니다. `python -m patternkb build` 로 빌드하세요."
)

_SECTION_KOREAN = {
    "patterns": "클라우드 설계 패턴",
    "architecture-styles": "아키텍처 스타일",
    "design-principles": "설계 원칙",
    "best-practices": "실무 지침",
    "twelve-factor": "12factor 배포 원칙",
    "well-architected": "Well-Architected 지침",
}


def search_patterns(
    query: str, top: int = 3, *, output_dir: Path | str | None = None
) -> str:
    """설계 지침 산문 검색. 인용문 + 출처 + 상시 고지."""
    if not is_built(output_dir):
        warnings = load_warnings(output_dir)
        return f"{_MISSING}\n⚠ {warnings[0]}" if warnings else _MISSING

    hits = search(query, limit=max(1, min(top, 5)), output_dir=output_dir)
    if not hits:
        known = " · ".join(
            f"{_SECTION_KOREAN.get(k, k)} {v}편"
            for k, v in sorted(sections(output_dir).items())
        )
        return (
            f"'{query}'에 맞는 지침 문서가 없습니다. 코퍼스: {known}.\n"
            "영어 키워드로 다시 시도해 보세요 — 코퍼스가 영어 문서입니다.\n"
            + ADVISORY_NOTICE
        )

    lines = [
        f"설계 지침 검색 {len(hits)}건 — 근거: {evidence_name(EVIDENCE_ADVISORY)}"
    ]
    for i, hit in enumerate(hits, 1):
        lines.append(
            f"{i}. {hit.title} [{_SECTION_KOREAN.get(hit.section, hit.section)}]"
        )
        lines.append(f"   {hit.quote}")
        lines.append(f"   문서: {hit.path} · {hit.attribution} ({hit.license})")
    lines.append(ADVISORY_NOTICE)
    return "\n".join(lines)


def coverage_text(output_dir: Path | str | None = None) -> str:
    if not is_built(output_dir):
        return _MISSING
    counts = sections(output_dir)
    parts = " · ".join(
        f"{_SECTION_KOREAN.get(k, k)} {v}편" for k, v in sorted(counts.items())
    )
    return (
        f"설계 지침 문서 {sum(counts.values())}편 ({parts}). "
        "전부 산문이라 값·한도는 담지 않습니다.\n" + ADVISORY_NOTICE
    )
