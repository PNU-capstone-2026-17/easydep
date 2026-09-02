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
from app.testing.utils.gates import aggregate_gate_report, gate_status


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
    fixed_test_code: str | None = None,
    implementation_job_id: str | None = None,
    run_dynamic: bool = True,
    testing_input: dict[str, Any] | None = None,
    iac_expected: bool | None = None,
    deployment_package_expected: bool | None = None,
    application_network: str | None = None,
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
            fixed_test_code=fixed_test_code,
            implementation_job_id=implementation_job_id,
            run_dynamic=run_dynamic,
            testing_input=testing_input,
            iac_expected=iac_expected,
            deployment_package_expected=deployment_package_expected,
            application_network=application_network,
        )


def _run_verification_graph(
    *,
    run_id: str,
    app_id: str,
    target_url: str = "",
    application_dir: str = "",
    repair_history: dict[str, Any] | None = None,
    fixed_test_code: str | None = None,
    implementation_job_id: str | None = None,
    run_dynamic: bool = True,
    testing_input: dict[str, Any] | None = None,
    iac_expected: bool | None = None,
    deployment_package_expected: bool | None = None,
    application_network: str | None = None,
) -> dict[str, Any]:
    """저장된 애플리케이션을 실행한 뒤 testing graph를 호출한다.

    애플리케이션 실행에 실패해도 deployment와 IaC 정적 검사는 실행할 수 있다. 따라서
    예외를 곧바로 밖으로 던지지 않고 정적 보고서를 만든 뒤, 실행 실패를 전체 작업의
    차단 원인과 진단 정보에 명확히 기록한다.
    """
    graph = create_testing_graph()
    application: dict[str, Any] = {}
    launch_error: str | None = None
    launch_defect_class = "SUT_DEFECT"

    if not run_dynamic:
        # 전체 test나 frontend build가 이미 실패했다면 Docker image까지 만들 이유가 없다.
        # 정적 설정 검사는 계속 실행하고 동적 노드는 target URL이 없으므로 SKIPPED가 된다.
        result = graph.invoke(
            initial_state(
                run_id=run_id,
                app_id=app_id,
                application_dir=application_dir,
                repair_history=repair_history,
                fixed_test_code=fixed_test_code,
                testing_input=testing_input,
                iac_expected=iac_expected,
                deployment_package_expected=deployment_package_expected,
                application_network=None,
            )
        )
    else:
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
                        fixed_test_code=fixed_test_code,
                        testing_input=testing_input,
                        iac_expected=iac_expected,
                        deployment_package_expected=deployment_package_expected,
                        application_network=application.get("network") or application_network,
                    )
                )
        except ApplicationLaunchError as error:
            launch_error = str(error)
            launch_defect_class = error.defect_class
            result = graph.invoke(
                initial_state(
                    run_id=run_id,
                    app_id=app_id,
                    application_dir=application_dir,
                    repair_history=repair_history,
                    fixed_test_code=fixed_test_code,
                    testing_input=testing_input,
                    iac_expected=iac_expected,
                    deployment_package_expected=deployment_package_expected,
                    application_network=None,
                )
            )

            # 앱을 띄우지 못했는데 동적 검사를 NOT_APPLICABLE로 두면 최종 finding에서
            # 시작 실패가 사라진다. Docker 환경 문제는 재실행 대기, 생성 앱 문제는 구현
            # 수리로 보낼 수 있도록 같은 dynamic gate에 명시적인 실패를 남긴다.
            environment_failure = launch_defect_class == "ENVIRONMENT_DEFECT"
            result["dynamic_functional_report"] = {
                "status": "UNAVAILABLE" if environment_failure else "FAILED",
                "gateStatus": "INCONCLUSIVE" if environment_failure else "FAIL",
                "reason": launch_error,
                "defectClass": launch_defect_class,
                "defect": {
                    "class": launch_defect_class,
                    "defectClass": launch_defect_class,
                    "route": "environment" if environment_failure else "implementation",
                    "preserveTests": True,
                },
            }

    reports = {
        "static": result.get("static_report"),
        "iac": result.get("iac_report"),
        "dynamicFunctional": result.get("dynamic_functional_report"),
    }
    required = {
        "static": True,
        # An unspecified IaC contract is a legacy/no-IaC application. The service
        # supplies True when the fixed implementation snapshot contains IaC.
        "iac": iac_expected is True,
        "dynamicFunctional": run_dynamic,
    }
    aggregate = aggregate_gate_report(reports, required=required)
    blocking = (
        f"애플리케이션을 실행하지 못해 동적 테스트를 수행할 수 없습니다: {launch_error}"
        if launch_error
        else blocking_reason(reports)
    )
    if blocking is None and aggregate["status"] == "INCONCLUSIVE":
        blocking = "필수 Testing 검사를 실행하지 못해 결과를 확정할 수 없습니다."
    if blocking is None and aggregate["status"] == "FAIL":
        blocking = "필수 Testing 검사에서 실패가 확인되었습니다."
    diagnostics = misconfiguration_diagnostics(reports)
    if launch_error:
        diagnostics.insert(
            0,
            {
                "code": "APPLICATION_LAUNCH_FAILED",
                "message": launch_error,
                "defectClass": launch_defect_class,
            },
        )
    return {
        "reports": reports,
        "application": application,
        "applicationLaunchError": launch_error,
        "errors": result.get("errors") or [],
        "passed": blocking is None,
        "status": aggregate["status"],
        "gateStatus": aggregate["status"],
        "gates": aggregate["gates"],
        "gateCounts": aggregate["counts"],
        "blockingReason": blocking,
        "diagnostics": diagnostics,
    }


def blocking_reason(reports: dict[str, Any]) -> str | None:
    """정적 또는 동적 필수 검사가 실패한 첫 번째 이유를 반환한다."""
    for label, key in (("배포 설정", "static"), ("IaC", "iac")):
        report = reports.get(key) or {}
        if gate_status(report) == "FAIL":
            issues = report.get("issues") or []
            detail = str(issues[0]) if issues else str(report.get("message") or "")
            return f"{label} 정적 검사에 실패했습니다: {detail}".rstrip()

    report = reports.get("dynamicFunctional") or {}
    if gate_status(report) == "FAIL":
        return str(
            report.get("reason")
            or report.get("stderr")
            or "동적 기능 테스트에 실패했습니다."
        )[-2000:]
    return None


def misconfiguration_diagnostics(reports: dict[str, Any]) -> list[dict[str, str]]:
    """정적 검사 미실행과 발견된 설정 문제를 사용자가 읽을 수 있는 진단으로 바꾼다."""
    diagnostics = []
    for subject, key in (("DEPLOYMENT", "static"), ("IAC", "iac")):
        report = reports.get(key) or {}
        issues = report.get("issues") or []
        if gate_status(report) == "INCONCLUSIVE":
            # 검사를 실행하지 못한 산출물을 문제가 없는 산출물처럼 표시하면 안 된다.
            diagnostics.append(
                {
                    "code": f"{subject}_NOT_SCANNED",
                    "message": str(report.get("message") or "Nothing was scanned."),
                }
            )
        elif gate_status(report) == "FAIL" and issues:
            diagnostics.append(
                {
                    "code": f"{subject}_MISCONFIGURATION",
                    "message": f"정적 검사에서 {len(issues)}개 문제를 찾았습니다: "
                    + "; ".join(issues[:5]),
                }
            )
    return diagnostics
