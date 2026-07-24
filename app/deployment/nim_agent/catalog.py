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
    Task("web_search", "웹 검색", "web_search 도구(DuckDuckGo)로 최신 정보를 검색한다."),
    Task(
        "cloud_sizing",
        "클라우드 리소스 산정",
        "앱 요구사항으로부터 VM 스펙을 추천(cost_recommend_specs)하고 "
        "월 비용을 추정(cost_estimate_monthly)한다.",
    ),
    Task(
        "design_to_deployment",
        "설계도 → 배포 구성",
        "앱 설계 산출물(클래스·시퀀스·ER·OpenAPI를 담은 JSON)에서 배포 구성과 "
        "PlantUML 다이어그램을 만든다. 줄마다 근거(설계/설계자/지식베이스/추론)가 붙는다.",
    ),
]

_BY_ID = {t.id: t for t in CATALOG}


def catalog_as_text() -> str:
    """instructions에 넣기 좋은 사람이 읽는 카탈로그 텍스트."""
    return "\n".join(f"- [{t.id}] {t.title}: {t.description}" for t in CATALOG)


def get_task(task_id: str) -> Task | None:
    return _BY_ID.get(task_id)
