from langgraph.graph import StateGraph, START, END

from app.testing.schemas.testing_state import TestingState
from app.testing.nodes.static_verification import static_verification_node
from app.testing.nodes.iac_verification import iac_verification_node
from app.testing.nodes.dynamic_functional import dynamic_functional_node
from app.testing.nodes.placeholders import dynamic_nfr_node

def create_testing_graph():
    """
    Creates and compiles the Testing Agent LangGraph.
    """
    workflow = StateGraph(TestingState)

    # Add nodes
    workflow.add_node("static_verification", static_verification_node)
    workflow.add_node("iac_verification", iac_verification_node)
    workflow.add_node("dynamic_functional", dynamic_functional_node)
    workflow.add_node("dynamic_nfr", dynamic_nfr_node)

    # Add edges
    # Standard flow: Static K8s -> Static IaC -> Dynamic Functional -> Dynamic NFR
    workflow.add_edge(START, "static_verification")

    # Simple linear flow for now.
    # In the future, we could add conditional edges to halt if a stage fails.
    workflow.add_edge("static_verification", "iac_verification")
    workflow.add_edge("iac_verification", "dynamic_functional")
    workflow.add_edge("dynamic_functional", "dynamic_nfr")
    workflow.add_edge("dynamic_nfr", END)

    # Compile the graph
    return workflow.compile()


def initial_state(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    manifests_dir: str = "",
    iac_dir: str = "",
) -> dict:
    """A fully populated input for :func:`create_testing_graph`.

    ``TestingState`` is a TypedDict, so LangGraph silently drops any key it does
    not declare — an input assembled by hand loses ``app_id`` the moment the two
    drift apart, and every database lookup in the graph is keyed on it.  Callers
    build their input here so that cannot happen quietly.
    """
    return {
        "run_id": run_id,
        "app_id": app_id,
        "manifests_dir": manifests_dir,
        "iac_dir": iac_dir,
        "target_url": target_url,
        "current_node": "",
        "errors": [],
        "static_report": None,
        "dynamic_functional_report": None,
        "dynamic_nfr_report": None,
        "iac_report": None,
    }
