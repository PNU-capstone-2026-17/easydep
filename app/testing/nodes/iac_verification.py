from pathlib import Path

from app.testing.schemas.testing_state import TestingState
from app.testing.utils.opentofu import run_opentofu_checks
from app.testing.utils.static_analysis import scan_stage


def iac_verification_node(state: TestingState) -> dict:
    """Terraform 폴더를 Trivy와 OpenTofu로 함께 검사한다."""
    terraform_dir = Path(state.get("application_dir", "")) / "terraform"
    expected = state.get("iac_expected")
    if expected is False and not terraform_dir.is_dir():
        return {
            "current_node": "iac_verification",
            "errors": [],
            "iac_report": {
                "status": "SKIPPED",
                "gateStatus": "NOT_APPLICABLE",
                "issues": [],
                "source": {"source": "none", "directory": str(terraform_dir)},
                "message": "No IaC is required for this application.",
            },
        }
    scanned = scan_stage(
        node="iac_verification",
        directory=str(terraform_dir),
        subject="IaC",
        report_key="iac_report",
    )
    report = scanned["iac_report"]
    if report["status"] == "UNAVAILABLE":
        return scanned

    tofu = run_opentofu_checks(terraform_dir)
    trivy_issues = list(report.get("issues") or [])
    tofu_issues = list(tofu.get("issues") or [])
    issues = [*trivy_issues, *tofu_issues]
    if trivy_issues or tofu["status"] == "FAILED":
        status = "FAILED"
        gate_status = "FAIL"
    elif tofu["status"] == "UNAVAILABLE":
        status = "UNAVAILABLE"
        gate_status = "INCONCLUSIVE"
    else:
        status = "PASSED"
        gate_status = "PASS"
    report.update(
        {
            "status": status,
            "gateStatus": gate_status,
            "issues": issues,
            "openTofu": tofu,
            "message": (
                "IaC가 Trivy와 OpenTofu 검사를 통과했습니다."
                if status == "PASSED"
                else tofu.get("message") or report.get("message")
            ),
        }
    )
    scanned["errors"] = issues
    return scanned
