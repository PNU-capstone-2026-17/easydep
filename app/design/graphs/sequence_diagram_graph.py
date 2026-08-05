from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.design.nodes.sequence_diagram import (
    convert_to_sequence_diagram_code,
    extract_sequence_elements,
    revise_sequence_elements,
    validate_sequence_diagram_syntax,
)
from app.design.schemas.architecture_state import ArchitectureState


# 생성과 피드백이 공유하는 꼬리: 구조화 요소 → 결정론적 변환 → (트립와이어) 검증 → END.
# 변환이 유효성을 보장하므로 문법 수리 루프는 없다.
def _add_convert_and_validate(builder: StateGraph, entry_node: str) -> None:
    builder.add_node("convert_to_sequence_diagram_code", convert_to_sequence_diagram_code)
    builder.add_node("validate_sequence_diagram_syntax", validate_sequence_diagram_syntax)
    builder.add_edge(entry_node, "convert_to_sequence_diagram_code")
    builder.add_edge("convert_to_sequence_diagram_code", "validate_sequence_diagram_syntax")
    builder.add_edge("validate_sequence_diagram_syntax", END)


def build_sequence_diagram_graph():
    """생성: 유스케이스 명세 + 클래스 다이어그램 → 요소 추출 → 변환 → 검증."""
    builder = StateGraph(ArchitectureState)
    builder.add_node("extract_sequence_elements", extract_sequence_elements)
    builder.add_edge(START, "extract_sequence_elements")
    _add_convert_and_validate(builder, "extract_sequence_elements")
    return builder.compile()


def build_sequence_diagram_feedback_graph():
    """피드백: 사용자 피드백을 요소 모델에 적용 → 같은 변환 → 검증."""
    builder = StateGraph(ArchitectureState)
    builder.add_node("revise_sequence_elements", revise_sequence_elements)
    builder.add_edge(START, "revise_sequence_elements")
    _add_convert_and_validate(builder, "revise_sequence_elements")
    return builder.compile()


sequence_diagram_graph = build_sequence_diagram_graph()
sequence_diagram_feedback_graph = build_sequence_diagram_feedback_graph()
