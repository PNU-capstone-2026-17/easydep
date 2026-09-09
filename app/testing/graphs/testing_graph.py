from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.testing.nodes.dynamic_functional import dynamic_functional_node
from app.testing.nodes.static_verification import static_verification_node
from app.testing.progress import emit_testing_progress
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.gates import gate_status

def _after_dynamic(state: TestingState) -> str:
    """Record the dynamic result and continue with independent verification gates."""

    scope = state.get("gate_scope")
    selected = (
        set(scope)
        if scope is not None
        else {"static", "package", "iac", "dynamicFunctional"}
    )
    # static-only repair는 이전 dynamic FAIL을 고치는 작업이 아니다. 이전 report를
    # 재사용했더라도 요청된 정적 gate는 실제로 실행한다.
    if "dynamicFunctional" not in selected:
        return "static_verification"
    dynamic = state.get("dynamic_functional_report") or {}
    dynamic_status = gate_status(dynamic)
    emit_testing_progress(
        phase="dynamic",
        scope="gate",
        status=(
            "REUSED"
            if dynamic.get("reused") is True
            else dynamic_status
            if dynamic_status in {"PASS", "FAIL", "INCONCLUSIVE"}
            else "SKIPPED"
        ),
        label="Completed dynamic API verification",
        gate="dynamicFunctional",
    )
    return "static_verification"


def create_testing_graph():
    """Run dynamic verification first, then complete the independent static gates."""
    workflow = StateGraph(TestingState)

    workflow.add_node("dynamic_functional", dynamic_functional_node)
    workflow.add_node("static_verification", static_verification_node)

    workflow.add_edge(START, "dynamic_functional")
    workflow.add_conditional_edges(
        "dynamic_functional",
        _after_dynamic,
        {
            "static_verification": "static_verification",
            END: END,
        },
    )
    workflow.add_edge("static_verification", END)
    return workflow.compile()


def initial_state(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    application_dir: str = "",
    repair_history: dict | None = None,
    fixed_arazzo_document: dict | None = None,
    fixed_workflow_inputs: dict[str, dict[str, Any]] | None = None,
    fixed_input_values: dict[str, list[dict[str, Any]]] | None = None,
    preserved_workflow_results: list[dict] | None = None,
    priority_workflow_id: str = "",
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
        "fixed_arazzo_document": fixed_arazzo_document,
        "fixed_workflow_inputs": fixed_workflow_inputs or {},
        "fixed_input_values": fixed_input_values or {},
        "preserved_workflow_results": preserved_workflow_results or [],
        "priority_workflow_id": priority_workflow_id,
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
