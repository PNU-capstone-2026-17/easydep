"""patternkb 도구 등록 + compose의 pattern-advisory 자문.

여기서는 @function_tool 래핑·LOCAL_TOOLS 등록과, 아키타입 분류가 애매할 때
자문 노트가 **미결 판정을 지우지 않고** 얹히는지만 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from nim_agent.design_tools import _pattern_advisory, compose
from nim_agent.pattern_tools import PATTERN_TOOLS, pattern_search
from nim_agent.tools import LOCAL_TOOLS


def test_pattern_search_is_registered() -> None:
    assert len(PATTERN_TOOLS) == 1
    names = {getattr(t, "name", None) for t in LOCAL_TOOLS}
    assert "pattern_search" in names


def test_tool_description_scopes_it_to_advisory() -> None:
    """도구 설명이 LLM에게 가장 가깝게 작동한다(web_search 선례) — 값·한도
    질문에 쓰지 말라는 규칙이 설명에 있어야 한다."""
    # 설명이 영어가 되면서 줄바꿈이 구절 중간에 걸린다 — 공백을 눌러 대조한다.
    description = " ".join((pattern_search.description or "").split())
    assert "design guidance, not a cloud fact" in description
    assert "advisory only" in description
    assert "do not use it for" in description
    # 코퍼스가 영어라 질의어도 영어여야 한다는 규칙이 설명에 있어야 한다.
    assert "English keywords" in description


def _order_demo_with_engine(engine: str) -> dict:
    path = Path(__file__).resolve().parent.parent / "appkb" / "examples" / "order-demo.json"
    design = json.loads(path.read_text(encoding="utf-8"))
    for artifact in design["artifacts"]:
        if artifact["kind"] == "er":
            artifact["engineHint"] = engine
    return design


def test_unknown_engine_gets_advisory_note_but_stays_unresolved() -> None:
    """**자문은 판정을 바꾸지 않는다.** engineHint를 못 옮긴 미결은 그대로 남고,
    그 자리에 pattern-advisory 노트(지침 고지 포함)만 얹힌다."""
    plan = compose(_order_demo_with_engine("kafka"))

    assert any("kafka" in item for item in plan.unresolved), "미결이 사라졌다"
    advisories = [
        note
        for node in plan.nodes
        for note in node.notes
        if note.source == "pattern-advisory"
    ]
    assert advisories, "자문 노트가 없다 (코퍼스가 kafka를 못 찾았나?)"
    assert all("설계 지침이지 클라우드 사실이 아닙니다" in n.text for n in advisories)


def test_known_engine_gets_no_advisory() -> None:
    """분류가 애매하지 않으면 자문을 달지 않는다 — 모든 노드에 산문이 붙으면
    진짜 경고가 안 보인다."""
    plan = compose(_order_demo_with_engine("postgresql"))
    assert not [
        note
        for node in plan.nodes
        for note in node.notes
        if note.source == "pattern-advisory"
    ]


def test_advisory_helper_returns_none_when_nothing_matches() -> None:
    """토큰이 통째로 낯설면 자문이 없어야 한다 — 억지 top-1을 달면 모든 미상
    엔진에 엉뚱한 지침이 붙는다. (질의를 하이픈 구문으로 쓰면 "not"·"any" 같은
    실제 영단어로 토큰화되어 매칭되는 것이 정상 동작임을 실측으로 확인했다.)"""
    assert _pattern_advisory("zzzunfindableenginezzz") is None
