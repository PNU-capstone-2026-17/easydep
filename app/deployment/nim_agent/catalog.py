"""작업 카탈로그 — 에이전트에게 '무엇을 할 수 있는지' 전달하는 구조화 데이터.

CATALOG를 텍스트로 직렬화해 에이전트 instructions에 주입하면, 에이전트는 이를 근거로
사용자 요청을 어떤 작업으로 처리할지 스스로 계획(plan)할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    description: str


CATALOG: list[Task] = [
    Task("summarize", "문서 요약", "긴 텍스트를 핵심만 간결하게 요약한다."),
    Task("translate", "번역", "한국어 ↔ 영어 번역을 수행한다."),
    Task("brainstorm", "아이디어 발상", "주제에 대한 아이디어와 대안을 제시한다."),
    Task("web_search", "웹 검색", "web_search 도구(DuckDuckGo)로 최신 정보를 검색한다."),
    Task(
        "cloud_sizing",
        "클라우드 리소스 산정",
        "앱 요구사항으로부터 VM 스펙을 추천(cost_recommend_specs)하고 "
        "월 비용을 추정(cost_estimate_monthly)한다.",
    ),
]

_BY_ID = {t.id: t for t in CATALOG}


def catalog_as_text() -> str:
    """instructions에 넣기 좋은 사람이 읽는 카탈로그 텍스트."""
    return "\n".join(f"- [{t.id}] {t.title}: {t.description}" for t in CATALOG)


def get_task(task_id: str) -> Task | None:
    return _BY_ID.get(task_id)
