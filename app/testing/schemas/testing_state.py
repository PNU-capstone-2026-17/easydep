import operator
from typing import TypedDict, Annotated, Optional, Any

class TestingState(TypedDict):
    """
    LangGraph state for the Testing Agent.
    """
    # Inputs
    run_id: str
    # 검사 대상 앱. 정적분석이 읽을 배포/IaC 스냅샷과 동적 검사가 읽을 기능
    # 요구사항이 모두 이 id로 DB에서 조회된다. LangGraph는 스키마에 없는 키를
    # 조용히 버리므로, 이 칸이 없으면 호출자가 넘겨도 노드에는 닿지 않는다.
    app_id: str
    # DB에 저장된 스냅샷이 없을 때만 쓰는 작업공간 대체 경로.
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
