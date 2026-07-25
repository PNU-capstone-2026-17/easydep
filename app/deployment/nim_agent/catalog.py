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
    Task(
        "web_search",
        "Web search",
        "Search for up-to-date information with the web_search tool (DuckDuckGo).",
    ),
    Task(
        "cloud_sizing",
        "Cloud resource sizing",
        "Recommend VM specs from the app requirements (cost_recommend_specs) and "
        "estimate the monthly cost (cost_estimate_monthly).",
    ),
    Task(
        "design_to_deployment",
        "Design → deployment configuration",
        "Build a deployment configuration and a PlantUML diagram from app design "
        "artifacts (JSON holding class/sequence/ER/OpenAPI). Every line carries "
        "its origin (design / designer / knowledge base / inference).",
    ),
]

_BY_ID = {t.id: t for t in CATALOG}


def catalog_as_text() -> str:
    """instructions에 넣기 좋은 사람이 읽는 카탈로그 텍스트."""
    return "\n".join(f"- [{t.id}] {t.title}: {t.description}" for t in CATALOG)


def get_task(task_id: str) -> Task | None:
    return _BY_ID.get(task_id)
