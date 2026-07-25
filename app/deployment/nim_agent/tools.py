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
from .cost_tools import COST_TOOLS
from .design_tools import DESIGN_TOOLS
from .graph_tools import GRAPHKB_TOOLS
from .guideline_tools import GUIDELINE_TOOLS
from .pattern_tools import PATTERN_TOOLS
from .perf_tools import PERF_TOOLS
from .session import SessionState
from .sizing_tools import SIZING_TOOLS


# `list_tasks`·`get_task_detail`은 뺐다(2026-07-25). 카탈로그는 **이미
# INSTRUCTIONS에 통째로 주입**돼 있어(`catalog_as_text()`) 같은 정보를 도구로 다시
# 주는 것이었고, 프로브 58건 중 어느 것도 이 둘을 기대하지 않았다. 상시 노출되는
# 도구 하나하나가 라우팅 결정 지점을 늘린다.
@function_tool
def record_plan(ctx: RunContextWrapper[SessionState], steps: list[str]) -> str:
    """Record a plan **before you start executing**, for work that coordinates
    several steps.

    **Do not call this** for simple lookups that finish in one or two tool calls
    (knowledge-base queries, search, file listings). Writing up work you have
    already done as if it were a plan misleads the user about the order things
    happened in. This tool is only for coordinating work still ahead.

    Cloud resource sizing (recommend_specs / estimate_monthly_cost) cannot run
    until this plan has been recorded first.

    Args:
        steps: the plan steps, in the order you will execute them.
    """
    print("\n[plan recorded]")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    print()
    if isinstance(ctx.context, SessionState):
        ctx.context.plan_steps = list(steps)
    return f"Recorded a {len(steps)}-step plan. Now execute it as planned."


@function_tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo and return top results (title / URL / snippet).
    No API key needed.

    **Do not use this tool to find cloud facts. Dedicated tools exist:**
    - instance specs and hourly rates -> cost_recommend_specs, monthly cost ->
      cost_estimate_monthly
    - resource type dependencies, ordering, equivalents -> kb_* tools
    - capacity limits, allowed values, mutability -> cap_* tools

    Mixing in a price pulled from search breaks the basis of the total, and it leads
    to searching over and over until the desired number appears. If a dedicated tool
    says "no data", tell the user exactly that -- do not try to fill the gap with
    search.

    **Only search when the user asked you to go find it** ("look it up", "check the
    official docs", "find it anyway"). Otherwise report what the dedicated tool said
    and offer to look it up.

    **Two calls at most per question.** If two do not find it, say it was not found;
    rewording the query again does not add a fact.

    Args:
        query: the search terms.
        max_results: how many results to fetch (default 5, max 10).
    """
    print(f"\n[web search] {query!r} (max_results={max_results})")
    count = max(1, min(max_results, 10))
    try:
        results = DDGS().text(query, max_results=count)
    except Exception as exc:  # noqa: BLE001 - 검색 실패는 에이전트가 읽을 메시지로 되돌린다.
        return f"Web search failed: {exc}"

    if not results:
        return f"No search results for '{query}'."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '(no title)')}\n   {r.get('href', '')}\n   {r.get('body', '')}")
    return "\n\n".join(lines)


# 에이전트에 등록할 로컬 도구 목록
# (의존성 그래프 + 용량·제약 + 스펙·가격 + 성능 특성 지식베이스 질의 도구 포함).
LOCAL_TOOLS: list[Tool] = [
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
