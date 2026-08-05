from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.design.nodes.api_spec import (
    convert_to_api_spec_code,
    extract_api_elements,
    revise_api_elements,
    validate_api_spec_syntax,
)
from app.design.schemas.architecture_state import ArchitectureState


# 생성과 피드백이 공유하는 꼬리: 구조화 요소 → 결정론적 변환 → 검증 → END.
def _add_convert_and_validate(builder: StateGraph, entry_node: str) -> None:
    builder.add_node("convert_to_api_spec_code", convert_to_api_spec_code)
    builder.add_node("validate_api_spec_syntax", validate_api_spec_syntax)
    builder.add_edge(entry_node, "convert_to_api_spec_code")
    builder.add_edge("convert_to_api_spec_code", "validate_api_spec_syntax")
    builder.add_edge("validate_api_spec_syntax", END)


def build_api_spec_graph():
    """생성: 다이어그램 정보 → 요소 추출 → 변환 → 검증."""
    builder = StateGraph(ArchitectureState)
    builder.add_node("extract_api_elements", extract_api_elements)
    builder.add_edge(START, "extract_api_elements")
    _add_convert_and_validate(builder, "extract_api_elements")
    return builder.compile()


def build_api_spec_feedback_graph():
    """피드백: 사용자 피드백을 요소 모델에 적용 → 같은 변환 → 검증."""
    builder = StateGraph(ArchitectureState)
    builder.add_node("revise_api_elements", revise_api_elements)
    builder.add_edge(START, "revise_api_elements")
    _add_convert_and_validate(builder, "revise_api_elements")
    return builder.compile()


api_spec_graph = build_api_spec_graph()
api_spec_feedback_graph = build_api_spec_feedback_graph()
