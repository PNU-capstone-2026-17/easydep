"""계획 게이트 테스트 — 프롬프트가 아니라 코드가 순서를 강제하는지.

배경(실측): 같은 프롬프트를 3회 돌리면 gpt-oss-120b / gpt-oss-20b /
nemotron-3-super-120b 모두 도구 순서가 제각각이었고, NIM은
`ModelSettings(tool_choice="record_plan")`도 무시했다(3회 중 2회가 다른 도구부터).
그래서 순서 보장은 모델이 아니라 도구 자신이 한다.
"""

from __future__ import annotations

import asyncio

from agents.tool_context import ToolContext

from app.core.cloudkb.nim_agent.cost_tools import cost_estimate_monthly, cost_recommend_specs
from app.core.cloudkb.nim_agent.session import SessionState
from app.core.cloudkb.nim_agent.tools import record_plan


def invoke(tool, context, args: str) -> str:
    """@function_tool로 감싼 도구를 SDK의 실제 호출 경로로 부른다.

    SDK는 도구에 ToolContext(RunContextWrapper의 하위 클래스)를 넘긴다.
    """
    tool_context = ToolContext(
        context=context, tool_name=tool.name, tool_call_id="test", tool_arguments=args
    )
    return asyncio.run(tool.on_invoke_tool(tool_context, args))


# --- SessionState ---


def test_state_starts_without_plan() -> None:
    assert SessionState().has_plan is False


def test_record_plan_opens_the_gate() -> None:
    state = SessionState()
    invoke(record_plan, state, '{"steps": ["사이징", "스펙 추천", "비용 합산"]}')
    assert state.has_plan is True
    assert state.plan_steps == ["사이징", "스펙 추천", "비용 합산"]


# --- 게이트 동작 ---


def test_recommend_specs_refuses_without_plan() -> None:
    state = SessionState()
    out = invoke(cost_recommend_specs, state, '{"vcpu_min": 2, "mem_min_gib": 4}')
    assert "record_plan" in out
    assert "record a plan with record_plan first" in " ".join(out.split()).lower()


def test_estimate_cost_refuses_without_plan() -> None:
    state = SessionState()
    out = invoke(cost_estimate_monthly, state, '{"hourly_usd": 0.05, "count": 2}')
    assert "record_plan" in out


def test_tools_work_after_plan_recorded() -> None:
    state = SessionState()
    invoke(record_plan, state, '{"steps": ["스펙 추천", "비용 계산"]}')

    specs = invoke(
        cost_recommend_specs, state, '{"vcpu_min": 2, "mem_min_gib": 4, "provider": "aws"}'
    )
    assert "record_plan" not in specs
    assert "recommended candidates" in " ".join(specs.split()).lower()

    cost = invoke(cost_estimate_monthly, state, '{"hourly_usd": 0.05, "count": 2}')
    assert "record_plan" not in cost
    assert "estimated monthly cost" in " ".join(cost.split()).lower()


def test_gate_is_per_request() -> None:
    """앞 요청의 계획이 다음 요청의 게이트를 열면 안 된다."""
    first = SessionState()
    invoke(record_plan, first, '{"steps": ["a"]}')
    assert "record_plan" not in invoke(cost_recommend_specs, first, '{"vcpu_min": 2}')

    second = SessionState()  # main.py는 요청마다 새로 만든다
    assert "record_plan" in invoke(cost_recommend_specs, second, '{"vcpu_min": 2}')


def test_no_session_state_means_no_gate() -> None:
    """세션 상태 없이 도구만 쓰는 경우(테스트·직접 호출)엔 게이트를 적용하지 않는다."""
    out = invoke(cost_recommend_specs, None, '{"vcpu_min": 2, "mem_min_gib": 4}')
    assert "record_plan" not in out


# --- 스키마: ctx는 LLM에게 노출되지 않아야 한다 ---


def test_context_param_is_not_in_tool_schema() -> None:
    for tool in (record_plan, cost_recommend_specs, cost_estimate_monthly):
        assert "ctx" not in tool.params_json_schema.get("properties", {})


def test_kb_tools_are_not_gated() -> None:
    """지식베이스 질의는 단순 조회라 계획을 요구하지 않는다."""
    from app.core.cloudkb.nim_agent.graph_tools import kb_search_types

    assert "ctx" not in kb_search_types.params_json_schema.get("properties", {})
    out = invoke(kb_search_types, SessionState(), '{"keyword": "subnet"}')
    assert "record_plan" not in out
