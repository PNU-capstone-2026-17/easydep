from langgraph.graph import END, START, StateGraph

from app.testing.nodes.dynamic_functional import dynamic_functional_node
from app.testing.nodes.static_verification import static_verification_node
from app.testing.schemas.testing_state import TestingState


def create_testing_graph():
    """Testing 에이전트의 검사 순서를 만들고 실행 가능한 graph로 변환한다."""
    workflow = StateGraph(TestingState)

    # 정적 분석 두 종류를 한 node 안에서 병렬 실행한 뒤 동적 검사를 이어서 수행한다.
    workflow.add_node("static_verification", static_verification_node)
    workflow.add_node("dynamic_functional", dynamic_functional_node)

    # 동적 검사는 실행 중인 애플리케이션을 사용하므로 Trivy 병렬 구간 밖에 둔다.
    workflow.add_edge(START, "static_verification")

    workflow.add_edge("static_verification", "dynamic_functional")
    workflow.add_edge("dynamic_functional", END)

    return workflow.compile()


def initial_state(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    application_dir: str = "",
    repair_history: dict | None = None,
    fixed_test_plan: dict | None = None,
    preserved_case_results: list[dict] | None = None,
    testing_input: dict | None = None,
    iac_expected: bool | None = None,
    deployment_package_expected: bool | None = None,
    gate_scope: list[str] | None = None,
    previous_reports: dict | None = None,
    previous_job_id: str = "",
) -> dict:
    """호출 인자를 빠짐없이 채운 graph 시작 상태를 만든다."""
    return {
        "run_id": run_id,
        "app_id": app_id,
        "testing_input": testing_input or {},
        "application_dir": application_dir,
        "target_url": target_url,
        "repair_history": repair_history or {},
        "fixed_test_plan": fixed_test_plan,
        "preserved_case_results": preserved_case_results or [],
        "iac_expected": iac_expected,
        "deployment_package_expected": deployment_package_expected,
        "gate_scope": gate_scope,
        "previous_reports": previous_reports or {},
        "previous_job_id": previous_job_id,
        "current_node": "",
        "errors": [],
        "static_report": None,
        "dynamic_functional_report": None,
        "iac_report": None,
    }
