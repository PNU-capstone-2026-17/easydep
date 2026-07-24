"""에이전트 로컬 도구 조립 — 각 축의 *_TOOLS를 한 목록으로 모은다.

@function_tool 데코레이터는 함수 시그니처와 docstring으로부터 도구 스키마를 자동 생성한다.
gpt-oss-120b는 tool calling을 지원하므로 NIM Chat Completions 경로에서도 아래 도구들이 호출된다.

record_plan은 **여러 단계를 조율하는 작업에서 실행 전에** 계획을 남기는 도구다.
단순 조회에까지 쓰면 모델이 이미 실행한 일을 사후에 계획서로 적어(post-hoc rationalization)
실행 순서를 오해하게 만든다 — 계획이 실행을 이끌 때만 의미가 있다.
에이전트가 실제로 어떤 도구를 어떤 인자로 부르는지 관찰하려면 `main.py --verbose`를 쓴다.
"""

from __future__ import annotations

from agents import RunContextWrapper, Tool, function_tool
from ddgs import DDGS

from .bundle_tools import BUNDLE_TOOLS
from .capacity_tools import CAPACITY_TOOLS
from .catalog import catalog_as_text, get_task
from .cost_tools import COST_TOOLS
from .design_tools import DESIGN_TOOLS
from .graph_tools import GRAPHKB_TOOLS
from .guideline_tools import GUIDELINE_TOOLS
from .pattern_tools import PATTERN_TOOLS
from .perf_tools import PERF_TOOLS
from .session import SessionState
from .sizing_tools import SIZING_TOOLS


@function_tool
def list_tasks() -> str:
    """수행 가능한 작업 카탈로그(작업 id/제목/설명)를 반환한다."""
    return catalog_as_text()


@function_tool
def get_task_detail(task_id: str) -> str:
    """주어진 작업 id의 상세 설명을 반환한다. 없으면 안내 메시지를 반환한다."""
    task = get_task(task_id)
    if task is None:
        return f"'{task_id}'는 카탈로그에 없는 작업입니다. list_tasks로 가능한 작업을 확인하세요."
    return f"[{task.id}] {task.title}\n{task.description}"


@function_tool
def record_plan(ctx: RunContextWrapper[SessionState], steps: list[str]) -> str:
    """여러 단계를 조율해야 하는 작업에서, **실행을 시작하기 전에** 계획을 기록한다.

    도구를 한두 번 부르면 끝나는 단순 조회(지식베이스 질의, 검색, 파일 목록 등)에는
    **호출하지 마세요.** 이미 실행한 일을 나중에 계획서처럼 적으면 사용자가 실행 순서를
    오해합니다. 앞으로 할 일을 조율할 때만 쓰는 도구입니다.

    클라우드 리소스 산정(recommend_specs / estimate_monthly_cost)은 이 계획을 먼저
    기록해야 실행할 수 있습니다.

    Args:
        steps: 앞으로 순서대로 실행할 계획 단계들.
    """
    print("\n[계획 수립됨]")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    print()
    if isinstance(ctx.context, SessionState):
        ctx.context.plan_steps = list(steps)
    return f"{len(steps)}단계 계획을 기록했습니다. 이제 계획대로 실행하세요."


@function_tool
def web_search(query: str, max_results: int = 5) -> str:
    """DuckDuckGo로 웹을 검색해 상위 결과(제목/URL/요약)를 반환한다. API 키 불필요.

    **클라우드 관련 사실은 이 도구로 찾지 마세요. 전용 도구가 따로 있습니다:**
    - 인스턴스 스펙·시간당 단가 → cost_recommend_specs, 월 비용 → cost_estimate_monthly
    - 리소스 타입 의존성·순서·동치 → kb_* 도구
    - 용량 한도·허용값·변경 가능성 → cap_* 도구

    검색으로 가져온 가격을 섞으면 합계 기준이 어긋나고, 원하는 숫자가 나올 때까지
    검색을 반복하게 됩니다. 전용 도구가 "데이터가 없다"고 하면 그대로 사용자에게
    알리세요 — 검색으로 메우려 하지 마세요.

    Args:
        query: 검색어.
        max_results: 가져올 결과 수(기본 5, 최대 10).
    """
    print(f"\n[웹검색] {query!r} (max_results={max_results})")
    count = max(1, min(max_results, 10))
    try:
        results = DDGS().text(query, max_results=count)
    except Exception as exc:  # noqa: BLE001 - 검색 실패는 에이전트가 읽을 메시지로 되돌린다.
        return f"웹검색에 실패했습니다: {exc}"

    if not results:
        return f"'{query}'에 대한 검색 결과가 없습니다."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '(제목 없음)')}\n   {r.get('href', '')}\n   {r.get('body', '')}")
    return "\n\n".join(lines)


# 에이전트에 등록할 로컬 도구 목록
# (의존성 그래프 + 용량·제약 + 스펙·가격 + 성능 특성 지식베이스 질의 도구 포함).
LOCAL_TOOLS: list[Tool] = [
    list_tasks,
    get_task_detail,
    record_plan,
    web_search,
    *COST_TOOLS,
    *GRAPHKB_TOOLS,
    *CAPACITY_TOOLS,
    *PERF_TOOLS,
    *BUNDLE_TOOLS,
    *SIZING_TOOLS,
    *GUIDELINE_TOOLS,
    *DESIGN_TOOLS,
    *PATTERN_TOOLS,
]
