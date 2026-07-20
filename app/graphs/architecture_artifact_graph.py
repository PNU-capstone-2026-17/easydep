from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.nodes.artifact_generation import (
    generate_api_spec,
    generate_class_diagram,
    generate_deployment_diagram,
    generate_erd,
    generate_sequence_diagram,
)
from app.schemas.architecture_state import ArchitectureState


def build_architecture_artifact_graph():
    builder = StateGraph(ArchitectureState)

    builder.add_node("generate_class_diagram", generate_class_diagram)
    builder.add_node("generate_sequence_diagram", generate_sequence_diagram)
    builder.add_node("generate_api_spec", generate_api_spec)
    builder.add_node("generate_erd", generate_erd)
    builder.add_node("generate_deployment_diagram", generate_deployment_diagram)

    builder.add_edge(START, "generate_class_diagram")
    builder.add_edge("generate_class_diagram", "generate_sequence_diagram")
    builder.add_edge("generate_sequence_diagram", "generate_api_spec")
    builder.add_edge("generate_api_spec", "generate_erd")
    builder.add_edge("generate_erd", "generate_deployment_diagram")
    builder.add_edge("generate_deployment_diagram", END)

    return builder.compile()


architecture_artifact_graph = build_architecture_artifact_graph()
