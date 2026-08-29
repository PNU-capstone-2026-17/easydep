"""정적 검사와 동적 검사를 한 번의 공통 검증 절차로 실행한다.

Testing HTTP API와 전체 파이프라인은 모두 생성된 앱을 실행하고 같은 순서로 검사해야 한다.
이 모듈에 공통 순서를 두어 두 진입점의 성공·실패 판정이 달라지지 않게 한다.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from app.metrics import langsmith as langsmith_metrics
from app.testing.graphs.testing_graph import create_testing_graph, initial_state
from app.testing.runtime.app_container import (
    ApplicationLaunchError,
    running_application,
)


def _launch(
    app_id: str,
    target_url: str,
    *,
    launch_id: str,
    application_dir: str,
):
    """호출자가 URL을 주면 재사용하고, 아니면 복원된 폴더를 실행한다."""
    if target_url:
        return nullcontext((target_url, {"source": "caller"}))
    if not application_dir:
        raise ApplicationLaunchError("실행할 애플리케이션 폴더가 없습니다.")
    return running_application(
        app_id,
        application_dir,
        launch_id=launch_id,
    )


def run_verification_graph(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    application_dir: str = "",
    repair_history: dict[str, Any] | None = None,
    implementation_job_id: str | None = None,
) -> dict[str, Any]:
    with langsmith_metrics.trace_scope(
        "easydep.testing.verification",
        metadata={
            "agent": "testing",
            "operation": "verification",
            "run_id": run_id,
            "app_id": app_id,
            "implementation_job_id": implementation_job_id,
        },
    ):
        return _run_verification_graph(
            run_id=run_id,
            app_id=app_id,
            target_url=target_url,
            application_dir=application_dir,
            repair_history=repair_history,
            implementation_job_id=implementation_job_id,
        )


def _run_verification_graph(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    application_dir: str = "",
    repair_history: dict[str, Any] | None = None,
    implementation_job_id: str | None = None,
) -> dict[str, Any]:
    """저장된 애플리케이션을 실행한 뒤 testing graph를 호출한다.

    애플리케이션 실행에 실패해도 deployment와 IaC 정적 검사는 실행할 수 있다. 따라서
    예외를 곧바로 밖으로 던지지 않고 정적 보고서를 만든 뒤, 실행 실패를 전체 작업의
    차단 원인과 진단 정보에 명확히 기록한다.
    """
    graph = create_testing_graph()
    application: dict[str, Any] = {}
    launch_error: str | None = None

    try:
        with _launch(
            app_id,
            target_url,
            launch_id=run_id,
            application_dir=application_dir,
        ) as (url, application):
            result = graph.invoke(
                initial_state(
                    run_id=run_id,
                    app_id=app_id,
                    target_url=url,
                    application_dir=application_dir,
                    repair_history=repair_history,
                )
            )
    except ApplicationLaunchError as error:
        launch_error = str(error)
        result = graph.invoke(
            initial_state(
                run_id=run_id,
                app_id=app_id,
                application_dir=application_dir,
                repair_history=repair_history,
            )
        )

    reports = {
        "static": result.get("static_report"),
        "iac": result.get("iac_report"),
        "dynamicFunctional": result.get("dynamic_functional_report"),
        "dynamicNfr": result.get("dynamic_nfr_report"),
    }
    blocking = (
        f"애플리케이션을 실행하지 못해 동적 테스트를 수행할 수 없습니다: {launch_error}"
        if launch_error
        else blocking_reason(reports)
    )
    diagnostics = misconfiguration_diagnostics(reports)
    if launch_error:
        diagnostics.insert(
            0,
            {
                "code": "APPLICATION_LAUNCH_FAILED",
                "message": launch_error,
            },
        )
    return {
        "reports": reports,
        "application": application,
        "applicationLaunchError": launch_error,
        "errors": result.get("errors") or [],
        "passed": blocking is None,
        "blockingReason": blocking,
        "diagnostics": diagnostics,
    }


def blocking_reason(reports: dict[str, Any]) -> str | None:
    """전체 검증을 실패로 처리할 이유를 반환하며, 문제가 없으면 ``None``을 반환한다.

    정적 설정 문제는 진단에는 남기지만, 동적 기능 검사 실패만 전체 결과를 차단한다.
    """
    report = reports.get("dynamicFunctional") or {}
    if str(report.get("status", "")).upper() == "FAILED":
        return str(report.get("reason") or "Dynamic functional tests failed.")
    return None


def misconfiguration_diagnostics(reports: dict[str, Any]) -> list[dict[str, str]]:
    """정적 검사 미실행과 발견된 설정 문제를 사용자가 읽을 수 있는 진단으로 바꾼다."""
    diagnostics = []
    for subject, key in (("DEPLOYMENT", "static"), ("IAC", "iac")):
        report = reports.get(key) or {}
        issues = report.get("issues") or []
        if report.get("status") == "UNAVAILABLE":
            # 검사를 실행하지 못한 산출물을 문제가 없는 산출물처럼 표시하면 안 된다.
            diagnostics.append(
                {
                    "code": f"{subject}_NOT_SCANNED",
                    "message": str(report.get("message") or "Nothing was scanned."),
                }
            )
        elif issues:
            diagnostics.append(
                {
                    "code": f"{subject}_MISCONFIGURATION",
                    "message": f"Trivy found {len(issues)} issue(s): "
                    + "; ".join(issues[:5]),
                }
            )
    return diagnostics
