"""nim_agent 용량 질의 도구 등록 테스트.

도구 로직 자체는 capacitykb.agent_api 테스트가 담당하고,
여기서는 @function_tool 래핑과 LOCAL_TOOLS 등록만 검증한다.
"""

from __future__ import annotations

from agents import FunctionTool

from app.core.cloudkb.nim_agent.agent import INSTRUCTIONS
from app.core.cloudkb.nim_agent.capacity_tools import CAPACITY_TOOLS, _coerce
from app.core.cloudkb.nim_agent.tools import LOCAL_TOOLS


def test_capacity_tools_registered() -> None:
    """이름 목록을 고정한다.

    이름에 개수를 박지 않는다 — 도구를 하나 늘릴 때마다 테스트 **이름**이
    거짓이 되고, 그러면 이름을 믿고 읽는 사람이 속는다.
    """
    assert {tool.name for tool in CAPACITY_TOOLS} == {
        "cap_check_value",
        # 불변 속성·비밀값·작업 소요를 하나로 접었다 — 셋 다 인자가
        # `resource_type` 하나뿐이라 도구를 가르는 것이 모호한 결정 지점만 만들었다.
        "cap_resource_constraints",
        "cap_service_quota",
        "cap_resolve_region",
        "cap_service_regions",
        "cap_region_carbon",
        "cap_region_latency",
        "cap_basic_image",
        "cap_service_lifecycle",
        "cap_csp_supports",
    }


def test_tools_are_function_tools_with_docs() -> None:
    for tool in CAPACITY_TOOLS:
        assert isinstance(tool, FunctionTool)
        assert tool.description


def test_registered_in_local_tools() -> None:
    names = {getattr(t, "name", None) for t in LOCAL_TOOLS}
    for tool in CAPACITY_TOOLS:
        assert tool.name in names


def test_all_knowledge_axes_are_registered() -> None:
    """세 지식베이스 도구가 접두사로 구분돼 함께 등록된다."""
    names = {getattr(t, "name", None) for t in LOCAL_TOOLS}
    assert "kb_creation_order" in names  # 관계 축 (graphkb)
    assert "cap_check_value" in names  # 용량 축 (capacitykb)
    assert "cost_recommend_specs" in names  # 스펙·가격 축 (costkb)


def test_instructions_route_capacity_questions() -> None:
    assert "cap_check_value" in INSTRUCTIONS
    assert "cap_service_quota" in INSTRUCTIONS
    assert "kb_creation_order" in INSTRUCTIONS


def test_coerce_numeric_strings() -> None:
    assert _coerce("100000") == 100000
    assert _coerce("1.5") == 1.5
    assert _coerce("gp3") == "gp3"
