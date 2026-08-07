import os
from typing import Any

from app.testing.schemas.testing_state import TestingState
from app.testing.utils.docker_trivy import run_trivy_scan

def static_verification_node(state: TestingState) -> dict:
    """
    Performs static verification on K8s deployment manifests using Trivy via Docker.
    """
    manifests_dir = state.get("manifests_dir", "")
    
    if not manifests_dir or not os.path.exists(manifests_dir):
        return {
            "current_node": "static_verification",
            "errors": [f"Manifests directory not found: {manifests_dir}"],
            "static_report": {"status": "FAILED", "message": "Directory missing"}
        }

    issues = run_trivy_scan(manifests_dir)

    status = "FAILED" if issues else "PASSED"
    report = {
        "status": status,
        "issues": issues,
        "message": f"Found {len(issues)} K8s misconfigurations via Trivy."
    }
    
    return {
        "current_node": "static_verification",
        "errors": issues,
        "static_report": report
    }
