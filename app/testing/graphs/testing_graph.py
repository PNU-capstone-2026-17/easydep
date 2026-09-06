from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.testing.nodes.dynamic_functional import dynamic_functional_node
from app.testing.nodes.static_verification import static_verification_node
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.gates import gate_status


def _deferred_report(gate: str, reason: str) -> dict[str, Any]:
    return {
        "status": "DEFERRED",
        "gateStatus": "NOT_APPLICABLE",
        "deferred": True,
        "deferredGate": gate,
        "reason": reason,
    }


def _defer_static_verification(_state: TestingState) -> dict[str, Any]:
    """동적 차단 원인을 먼저 수리할 때 아직 실행하지 않은 gate를 명시한다."""

    reason = "Deferred until the blocking dynamic functional failure is repaired."
    trivy = _deferred_report("static", reason)
    package = _deferred_report("package", reason)
    dynamic = dict(_state.get("dynamic_functional_report") or {})
    dynamic["deferredGates"] = ["static", "package", "iac"]
    return {
        "current_node": "static_verification_deferred",
        "dynamic_functional_report": dynamic,
        "static_report": {
            **_deferred_report("static", reason),
            "issues": [],
            "trivyScan": trivy,
            "deploymentPackage": package,
        },
        "iac_report": _deferred_report("iac", reason),
    }


def _after_dynamic(state: TestingState) -> str:
    """동적 gate가 이번 실행의 차단 원인이면 정적 도구를 뒤로 미룬다."""

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
    if gate_status(dynamic) in {"FAIL", "INCONCLUSIVE"}:
        return "defer_static_verification"
    return "static_verification"


def create_testing_graph():
    """동적 runtime 검사를 우선하고, 차단 실패면 후속 정적 gate를 미룬다."""
    workflow = StateGraph(TestingState)

    workflow.add_node("dynamic_functional", dynamic_functional_node)
    workflow.add_node("static_verification", static_verification_node)
    workflow.add_node("defer_static_verification", _defer_static_verification)

    workflow.add_edge(START, "dynamic_functional")
    workflow.add_conditional_edges(
        "dynamic_functional",
        _after_dynamic,
        {
            "static_verification": "static_verification",
            "defer_static_verification": "defer_static_verification",
            END: END,
        },
    )
    workflow.add_edge("static_verification", END)
    workflow.add_edge("defer_static_verification", END)

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
    priority_case_id: str = "",
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
        "priority_case_id": priority_case_id,
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
