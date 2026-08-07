import os
from typing import Any

from app.testing.schemas.testing_state import TestingState
from app.testing.utils.docker_trivy import run_trivy_scan

def iac_verification_node(state: TestingState) -> dict:
    """
    Performs static verification on IaC (Terraform) files using Trivy via Docker.
    """
    iac_dir = state.get("iac_dir", "")
    
    if not iac_dir or not os.path.exists(iac_dir):
        return {
            "current_node": "iac_verification",
            "errors": [f"IaC directory not found: {iac_dir}"],
            "iac_report": {"status": "FAILED", "message": "IaC directory missing"}
        }

    issues = run_trivy_scan(iac_dir)

    status = "FAILED" if issues else "PASSED"
    report = {
        "status": status,
        "issues": issues,
        "message": f"Found {len(issues)} IaC misconfigurations via Trivy."
    }
    
    return {
        "current_node": "iac_verification",
        "errors": issues,
        "iac_report": report
    }
