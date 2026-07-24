"""patternkb 도구 — 설계 지침 산문 검색 (advisory 전용)."""

from __future__ import annotations

from agents import function_tool

from patternkb.agent_api import search_patterns


@function_tool
def pattern_search(query: str, top: int = 3) -> str:
    """클라우드 설계 패턴·원칙 **문서(산문)**를 검색해 인용문을 돌려준다 — 자문 전용.

    코퍼스: Azure Architecture Center의 설계 패턴·아키텍처 스타일·설계 원칙·실무
    지침(CC-BY-4.0)과 12factor 배포 원칙(MIT), 90편(영어).

    **결과는 설계 지침이지 클라우드 사실이 아닙니다** — 값·한도·가격·판정은 이
    도구가 아니라 kb_*/cap_*/cost_* 도구로 확인하세요. 결과에 붙는 고지와 출처
    (문서 경로·라이선스·저작자)를 답변에 그대로 옮기세요 — 인용은 출처와 함께
    다녀야 합니다.

    "이 설계 상황에 알려진 패턴이 있나"(재시도·큐·CQRS·마이크로서비스 분리 등)에
    쓰고, "얼마인가 / 되는가 / 한도는" 질문에는 쓰지 마세요.

    Args:
        query: 검색어. 코퍼스가 영어 문서라 영어 키워드가 잘 잡힙니다.
        top: 가져올 문서 수(기본 3, 최대 5).
    """
    print(f"\n[지침검색] {query!r} (top={top})")
    return search_patterns(query, top=top)


PATTERN_TOOLS = [pattern_search]
