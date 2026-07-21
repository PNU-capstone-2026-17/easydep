from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.design.nodes.class_diagram import (
    convert_to_class_diagram_code,
    extract_class_elements,
    repair_class_diagram_syntax,
    validate_class_diagram_syntax,
)
from app.design.schemas.architecture_state import ArchitectureState


def route_after_class_diagram_validation(
    state: ArchitectureState,
) -> Literal["repair", "end"]:
    # Syntax errors are retried until the diagram compiles. LangGraph's own
    # recursion limit is the only bound: a diagram that never validates is not
    # worth returning.
    if not state.get("class_diagram_syntax_valid", False):
        return "repair"
    return "end"


def build_class_diagram_graph():
    builder = StateGraph(ArchitectureState)

    builder.add_node("extract_class_elements", extract_class_elements)
    builder.add_node("convert_to_class_diagram_code", convert_to_class_diagram_code)
    builder.add_node("repair_class_diagram_syntax", repair_class_diagram_syntax)
    builder.add_node("validate_class_diagram_syntax", validate_class_diagram_syntax)

    builder.add_edge(START, "extract_class_elements")
    builder.add_edge("extract_class_elements", "convert_to_class_diagram_code")
    builder.add_edge("convert_to_class_diagram_code", "validate_class_diagram_syntax")
    builder.add_conditional_edges(
        "validate_class_diagram_syntax",
        route_after_class_diagram_validation,
        {
            "repair": "repair_class_diagram_syntax",
            "end": END,
        },
    )
    builder.add_edge("repair_class_diagram_syntax", "validate_class_diagram_syntax")

    return builder.compile()


class_diagram_graph = build_class_diagram_graph()
