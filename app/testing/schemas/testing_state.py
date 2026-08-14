import operator
from typing import TypedDict, Annotated, Optional, Any

class TestingState(TypedDict):
    """
    LangGraph state for the Testing Agent.
    """
    # Inputs
    run_id: str
    manifests_dir: str # e.g. "application/k8s"
    iac_dir: str # e.g. "application/terraform"
    target_url: str # Target URL for dynamic testing, defaults to localhost:8080

    # State
    current_node: str
    errors: Annotated[list[str], operator.add]

    # Reports from each verification node
    static_report: Optional[dict[str, Any]]
    dynamic_functional_report: Optional[dict[str, Any]]
    dynamic_nfr_report: Optional[dict[str, Any]]
    iac_report: Optional[dict[str, Any]]


# 이름이 ``Test``로 시작하지만 pytest 수집 대상 클래스가 아니다.
TestingState.__test__ = False
