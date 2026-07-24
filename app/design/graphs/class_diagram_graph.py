from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.design.nodes.class_diagram import (
    convert_to_class_diagram_code,
    extract_class_elements,
    revise_class_elements,
    validate_class_diagram_syntax,
)
from app.design.schemas.architecture_state import ArchitectureState


# 생성과 피드백이 공유하는 꼬리: BCE → 결정론적 변환 → (트립와이어) 검증 → END.
# 변환이 유효성을 보장하므로 문법 수리 루프는 없다.
def _add_convert_and_validate(builder: StateGraph, entry_node: str) -> None:
    builder.add_node("convert_to_class_diagram_code", convert_to_class_diagram_code)
    builder.add_node("validate_class_diagram_syntax", validate_class_diagram_syntax)
    builder.add_edge(entry_node, "convert_to_class_diagram_code")
    builder.add_edge("convert_to_class_diagram_code", "validate_class_diagram_syntax")
    builder.add_edge("validate_class_diagram_syntax", END)


def build_class_diagram_graph():
    """생성: 유스케이스 명세 → BCE 추출 → 변환 → 검증."""
    builder = StateGraph(ArchitectureState)
    builder.add_node("extract_class_elements", extract_class_elements)
    builder.add_edge(START, "extract_class_elements")
    _add_convert_and_validate(builder, "extract_class_elements")
    return builder.compile()


def build_class_diagram_feedback_graph():
    """피드백: 사용자 피드백을 BCE에 적용 → 같은 변환 → 검증.

    LLM은 구조화된 BCE만 편집하고 PlantUML 텍스트는 만지지 않으므로, BCE와 다이어그램이
    어긋나지 않는다. 생성 그래프와 convert/validate 노드를 공유한다.
    """
    builder = StateGraph(ArchitectureState)
    builder.add_node("revise_class_elements", revise_class_elements)
    builder.add_edge(START, "revise_class_elements")
    _add_convert_and_validate(builder, "revise_class_elements")
    return builder.compile()


class_diagram_graph = build_class_diagram_graph()
class_diagram_feedback_graph = build_class_diagram_feedback_graph()
