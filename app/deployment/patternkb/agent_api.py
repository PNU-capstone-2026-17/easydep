"""에이전트용 사전 정의 질의 API (patternkb) — **advisory 전용**.

**이 KB의 답은 사실이 아니라 인용이다.** 다른 축은 "원본이 이렇게 말했다"를 값으로
옮기지만, 여기는 산문을 발췌해 보여줄 뿐이다. 그래서 모든 답에:

- 인용문 (FTS5 snippet — «…» 표시)
- 문서 경로 · 라이선스 · 저작자 표시 (인용이 출처와 함께 다닌다)
- **"설계 지침이지 클라우드 사실이 아닙니다"** (한 번도 빠지면 안 된다)
"""

from __future__ import annotations

from pathlib import Path

from app.deployment.kbcommon.display import evidence_name
from app.deployment.patternkb.dataset import aws_built, is_built, load_warnings, sections
from app.deployment.patternkb.model import ADVISORY_NOTICE, EVIDENCE_ADVISORY
from app.deployment.patternkb.query import search

_MISSING = (
    "No design-pattern corpus found. Build it with `python -m patternkb build`."
)

#: aws 코퍼스 부재는 "없다"가 아니라 "이 환경에 안 실렸다"다 — 그 구분을 문장으로.
_AWS_UNBUILT = (
    "The AWS Well-Architected guidance corpus is not in this environment — "
    "build it with `python -m patternkb build-aws-waf` to include it in search."
)

_SECTION_KOREAN = {
    "patterns": "cloud design patterns",
    "architecture-styles": "architecture styles",
    "design-principles": "design principles",
    "best-practices": "best practices",
    "twelve-factor": "12factor deployment principles",
    "well-architected": "Well-Architected guidance",
    "gcp-framework": "GCP Architecture Framework",
    "aws-well-architected": "AWS Well-Architected guidance",
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
            f"{_SECTION_KOREAN.get(k, k)} {v}"
            for k, v in sorted(sections(output_dir).items())
        )
        return (
            f"No guidance document matches '{query}'. Corpus: {known}.\n"
            "Try again with English keywords — the corpus is English documents.\n"
            + ADVISORY_NOTICE
        )

    lines = [
        f"Design guidance search ({len(hits)}) — "
        f"evidence: {evidence_name(EVIDENCE_ADVISORY)}"
    ]
    for i, hit in enumerate(hits, 1):
        lines.append(
            f"{i}. {hit.title} [{_SECTION_KOREAN.get(hit.section, hit.section)}]"
        )
        lines.append(f"   {hit.quote}")
        lines.append(f"   document: {hit.path} · {hit.attribution} ({hit.license})")
    lines.append(ADVISORY_NOTICE)
    return "\n".join(lines)


def coverage_text(output_dir: Path | str | None = None) -> str:
    if not is_built(output_dir):
        return _MISSING
    counts = sections(output_dir)
    parts = " · ".join(
        f"{_SECTION_KOREAN.get(k, k)} {v}" for k, v in sorted(counts.items())
    )
    lines = [
        f"{sum(counts.values())} design guidance documents ({parts}). "
        "All prose, so no values or limits are included."
    ]
    if not aws_built(output_dir):
        lines.append(_AWS_UNBUILT)
    lines.append(ADVISORY_NOTICE)
    return "\n".join(lines)
