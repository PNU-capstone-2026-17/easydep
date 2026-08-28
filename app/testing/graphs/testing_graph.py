from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, START, StateGraph

from app.testing.nodes.dynamic_functional import dynamic_functional_node
from app.testing.nodes.iac_verification import iac_verification_node
from app.testing.nodes.placeholders import dynamic_nfr_node
from app.testing.nodes.static_verification import static_verification_node
from app.testing.schemas.testing_state import TestingState


def parallel_static_verification_node(state: TestingState) -> dict:
    """Run the independent deployment and IaC scans with bounded concurrency.

    Each scan materializes a different immutable artifact snapshot into its own
    temporary directory and opens its own database session. Collect futures in
    declaration order so the combined error list remains identical to the former
    deployment-then-IaC graph order even when the IaC scan finishes first.
    """
    with ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="easydep-static-verification",
    ) as pool:
        deployment_future = pool.submit(static_verification_node, state)
        iac_future = pool.submit(iac_verification_node, state)
        deployment = deployment_future.result()
        iac = iac_future.result()

    return {
        **deployment,
        **iac,
        "current_node": iac.get(
            "current_node", deployment.get("current_node", "static_verification")
        ),
        "errors": [*(deployment.get("errors") or []), *(iac.get("errors") or [])],
    }

def create_testing_graph():
    """
    Creates and compiles the Testing Agent LangGraph.
    """
    workflow = StateGraph(TestingState)

    # Add nodes
    workflow.add_node("static_verification", parallel_static_verification_node)
    workflow.add_node("dynamic_functional", dynamic_functional_node)
    workflow.add_node("dynamic_nfr", dynamic_nfr_node)

    # Add edges
    # The two static scans overlap inside one bounded node. Dynamic checks remain
    # outside that pool so Trivy cannot contend with the application under test.
    workflow.add_edge(START, "static_verification")

    workflow.add_edge("static_verification", "dynamic_functional")
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
    repair_history: dict | None = None,
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
        "repair_history": repair_history or {},
        "current_node": "",
        "errors": [],
        "static_report": None,
        "dynamic_functional_report": None,
        "dynamic_nfr_report": None,
        "iac_report": None,
    }
