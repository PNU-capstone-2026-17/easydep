from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.nodes.class_diagram import (
    feedback_class_diagram,
    convert_to_class_diagram_code,
    extract_class_elements,
    validate_class_diagram_syntax,
)
from app.schemas.architecture_state import ArchitectureState


def route_after_class_diagram_validation(
    state: ArchitectureState,
) -> Literal["feedback", "end"]:
    # Pending user feedback is applied first; the feedback node then clears the
    # request so it is not applied twice.
    if state.get("class_diagram_feedback_requested", False):
        return "feedback"

    # Syntax errors are retried until the diagram compiles. There is no attempt
    # cap: a diagram that never validates is not worth returning.
    if not state.get("class_diagram_syntax_valid", False):
        return "feedback"

    return "end"


def build_class_diagram_graph():
    builder = StateGraph(ArchitectureState)

    builder.add_node("extract_class_elements", extract_class_elements)
    builder.add_node("convert_to_class_diagram_code", convert_to_class_diagram_code)
    builder.add_node("feedback_class_diagram", feedback_class_diagram)
    builder.add_node("validate_class_diagram_syntax", validate_class_diagram_syntax)

    builder.add_edge(START, "extract_class_elements")
    builder.add_edge("extract_class_elements", "convert_to_class_diagram_code")
    builder.add_edge("convert_to_class_diagram_code", "validate_class_diagram_syntax")
    builder.add_conditional_edges(
        "validate_class_diagram_syntax",
        route_after_class_diagram_validation,
        {
            "feedback": "feedback_class_diagram",
            "end": END,
        },
    )
    builder.add_edge("feedback_class_diagram", "validate_class_diagram_syntax")

    return builder.compile()


class_diagram_graph = build_class_diagram_graph()
