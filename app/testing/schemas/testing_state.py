import operator
from typing import TypedDict, Annotated, Optional, Any

class TestingState(TypedDict):
    """
    LangGraph state for the Testing Agent.
    """
    # Inputs
    run_id: str
    manifests_dir: str # e.g. "application/k8s"

    # State
    current_node: str
    errors: Annotated[list[str], operator.add]

    # Reports from each verification node
    static_report: Optional[dict[str, Any]]
    dynamic_functional_report: Optional[dict[str, Any]]
    dynamic_nfr_report: Optional[dict[str, Any]]
    iac_report: Optional[dict[str, Any]]
