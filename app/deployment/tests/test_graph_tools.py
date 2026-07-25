"""nim_agent 그래프 질의 도구 등록 테스트.

도구 로직 자체는 graphkb.agent_api 테스트가 담당하고,
여기서는 @function_tool 래핑과 LOCAL_TOOLS 등록만 검증한다.
"""

from __future__ import annotations

from agents import FunctionTool

from app.deployment.nim_agent.graph_tools import GRAPHKB_TOOLS
from app.deployment.nim_agent.tools import LOCAL_TOOLS


def test_graphkb_tools_defined() -> None:
    assert len(GRAPHKB_TOOLS) == 6
    names = {tool.name for tool in GRAPHKB_TOOLS}
    assert names == {
        "kb_creation_order",
        "kb_deletion_impact",
        "kb_equivalent_types",
        "kb_describe_type",
        "kb_search_types",
        "kb_rank_types",
    }


def test_aggregate_tool_exists_for_whole_graph_questions() -> None:
    """전체 대상 순위 질문을 타입별 도구로 풀면 턴이 소진된다 → 집계 도구 필요."""
    from app.deployment.nim_agent.agent import INSTRUCTIONS

    assert "kb_rank_types" in INSTRUCTIONS
    assert "aggregate" in INSTRUCTIONS


def test_tools_are_function_tools_with_docs() -> None:
    for tool in GRAPHKB_TOOLS:
        assert isinstance(tool, FunctionTool)
        assert tool.description  # docstring에서 생성된 설명이 있어야 함


def test_registered_in_local_tools() -> None:
    local_names = {getattr(t, "name", None) for t in LOCAL_TOOLS}
    for tool in GRAPHKB_TOOLS:
        assert tool.name in local_names
