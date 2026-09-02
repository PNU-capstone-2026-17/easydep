from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import END, START, StateGraph

from app.metrics import langsmith as langsmith_metrics
from app.testing.nodes.dynamic_functional import dynamic_functional_node
from app.testing.nodes.iac_verification import iac_verification_node
from app.testing.nodes.static_verification import static_verification_node
from app.testing.schemas.testing_state import TestingState


def parallel_static_verification_node(state: TestingState) -> dict:
    """Run the independent deployment and IaC scans with bounded concurrency.

    두 검사는 Testing 작업이 한 번 복원한 같은 애플리케이션 폴더를 읽는다. 결과는 선언
    순서로 합쳐 병렬 실행 완료 순서가 달라도 외부 보고서 순서는 일정하게 유지한다.
    """
    with ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="easydep-static-verification",
    ) as pool:
        deployment_future = pool.submit(
            langsmith_metrics.bind_context(static_verification_node), state
        )
        iac_future = pool.submit(langsmith_metrics.bind_context(iac_verification_node), state)
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
    """Testing 에이전트의 검사 순서를 만들고 실행 가능한 graph로 변환한다."""
    workflow = StateGraph(TestingState)

    # 정적 분석 두 종류를 한 node 안에서 병렬 실행한 뒤 동적 검사를 이어서 수행한다.
    workflow.add_node("static_verification", parallel_static_verification_node)
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
    application_network: str | None = None,
    application_dir: str = "",
    repair_history: dict | None = None,
    fixed_test_plan: dict | None = None,
    preserved_case_results: list[dict] | None = None,
    testing_input: dict | None = None,
    iac_expected: bool | None = None,
    deployment_package_expected: bool | None = None,
) -> dict:
    """호출 인자를 빠짐없이 채운 graph 시작 상태를 만든다."""
    return {
        "run_id": run_id,
        "app_id": app_id,
        "testing_input": testing_input or {},
        "application_dir": application_dir,
        "target_url": target_url,
        "application_network": application_network,
        "repair_history": repair_history or {},
        "fixed_test_plan": fixed_test_plan,
        "preserved_case_results": preserved_case_results or [],
        "iac_expected": iac_expected,
        "deployment_package_expected": deployment_package_expected,
        "current_node": "",
        "errors": [],
        "static_report": None,
        "dynamic_functional_report": None,
        "iac_report": None,
    }
