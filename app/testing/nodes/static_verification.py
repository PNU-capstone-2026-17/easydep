import os

from app.testing.schemas.testing_state import TestingState
from app.testing.utils.static_analysis import scan_stage
from app.implementation.delivery.verification import check_deployment_package


def _resource_plan(state: TestingState) -> dict:
    """Read the selected projection's final ResourcePlan from frozen deployment input."""
    raw = (state.get("testing_input") or {}).get("contract_artifacts") or {}
    deployment = raw.get("deployment") if isinstance(raw, dict) else None
    content = deployment.get("content") if isinstance(deployment, dict) else None
    if not isinstance(content, dict):
        return {}
    for key in ("resourcePlan", "resource_plan"):
        value = content.get(key)
        if isinstance(value, dict):
            return value
    projections = content.get("projections") or []
    selected = content.get("selectedTarget") or content.get("selected_target")
    if isinstance(selected, dict):
        selected = selected.get("id") or selected.get("provider") or selected.get("target")
    for projection in projections:
        if not isinstance(projection, dict):
            continue
        target = projection.get("target") or projection.get("provider") or projection.get("id")
        if selected and target != selected:
            continue
        for key in ("resourcePlan", "resource_plan"):
            value = projection.get(key)
            if isinstance(value, dict):
                return value
    return {}


def static_verification_node(state: TestingState) -> dict:
    """복원한 애플리케이션 전체에서 배포 설정 문제를 찾는다."""
    scanned = scan_stage(
        node="static_verification",
        directory=state.get("application_dir", ""),
        subject="deployment file",
        report_key="static_report",
    )
    report = scanned["static_report"]
    resource_plan = _resource_plan(state)
    expected = state.get("deployment_package_expected")
    # Dockerfile도 DEPLOYMENT_FILE snapshot에 저장되지만 사용자 배포 패키지는 아니다.
    # 확정 ResourcePlan이 있거나 호출자가 명시적으로 요구한 경우에만 누락을 차단한다.
    package = check_deployment_package(
        state.get("application_dir", ""),
        expected=bool(resource_plan) if expected is None else expected,
        resource_plan=resource_plan,
        include_plan=str(os.getenv("TESTING_IAC_PLAN") or "").lower()
        in {"1", "true", "yes", "on"},
    )
    # A package is part of the deployment gate only when it exists/was expected;
    # absent packages are represented as NOT_APPLICABLE by the package checker.
    report["deploymentPackage"] = package
    package_gate = str(package.get("gateStatus") or "").upper()
    report_gate = str(report.get("gateStatus") or "").upper()
    if package_gate == "INCONCLUSIVE" and report_gate in {"", "PASS"}:
        report["gateStatus"] = "INCONCLUSIVE"
        report["status"] = "UNAVAILABLE"
    elif package_gate == "FAIL":
        report["gateStatus"] = "FAIL"
        report["status"] = "FAILED"
    if package.get("issues"):
        report["issues"] = [*(report.get("issues") or []), *package["issues"]]
    scanned["errors"] = report.get("issues") or []
    scanned["iac_report"] = package.get("openTofu") or {
        "status": "SKIPPED",
        "gateStatus": "NOT_APPLICABLE",
        "issues": [],
        "source": {"source": "none", "directory": state.get("application_dir", "")},
    }
    return scanned
