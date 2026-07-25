"""patternkb 도구 — 설계 지침 산문 검색 (advisory 전용)."""

from __future__ import annotations

from agents import function_tool

from patternkb.agent_api import search_patterns


@function_tool
def pattern_search(query: str, top: int = 3) -> str:
    """Search **prose documents** on cloud design patterns and principles and
    return quotations — advisory only.

    **The corpus is English documents, so `query` must be English keywords.**
    Translate the user's wording into English search terms before calling; a
    non-English query matches poorly or not at all.

    Corpus: Azure Architecture Center design patterns, architecture styles,
    design principles, and practice guidance, plus Well-Architected guidance and
    the GCP Architecture Framework (all CC-BY-4.0), and the 12factor deployment
    principles (MIT).

    **The result is design guidance, not a cloud fact** — check values, limits,
    prices, and verdicts with the kb_*/cap_*/cost_* tools, not this one. Carry
    the notice and the source (document path, license, author) attached to the
    result into your answer as-is — a quotation must travel with its source.

    Use it for "is there a known pattern for this design situation?" (retry,
    queue, CQRS, microservice decomposition, …) **and for design guidance and
    trade-offs between competing goals** — "guidance on trading cost against
    reliability", "what does optimizing X cost me elsewhere", "principles for
    Y". The corpus carries a Well-Architected trade-off document per pillar,
    so these questions are answerable here even when the word "pattern" never
    appears in them.

    Do not use it for "how much / is it allowed / what is the limit" questions.

    Args:
        query: Search terms. **English only** — the corpus is English documents.
        top: How many documents to fetch (default 3, max 5).
    """
    print(f"\n[guidance search] {query!r} (top={top})")
    return search_patterns(query, top=top)


PATTERN_TOOLS = [pattern_search]
