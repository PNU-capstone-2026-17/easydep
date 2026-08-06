import os
import yaml
from pathlib import Path
from typing import Any

from app.testing.schemas.testing_state import TestingState

def check_resources(container: dict[str, Any], workload_name: str, container_name: str) -> list[str]:
    errors = []
    resources = container.get("resources", {})
    if not resources:
        errors.append(f"[{workload_name}/{container_name}] missing 'resources' block")
        return errors
        
    limits = resources.get("limits", {})
    if "cpu" not in limits:
        errors.append(f"[{workload_name}/{container_name}] missing 'resources.limits.cpu'")
    if "memory" not in limits:
        errors.append(f"[{workload_name}/{container_name}] missing 'resources.limits.memory'")
        
    return errors

def check_probes(container: dict[str, Any], workload_name: str, container_name: str) -> list[str]:
    errors = []
    if "livenessProbe" not in container:
        errors.append(f"[{workload_name}/{container_name}] missing 'livenessProbe'")
    if "readinessProbe" not in container:
        errors.append(f"[{workload_name}/{container_name}] missing 'readinessProbe'")
    return errors

def static_verification_node(state: TestingState) -> dict:
    """
    Performs static verification on K8s manifests.
    Checks for:
    - Resource limits (CPU/Memory)
    - Probes (Liveness/Readiness)
    - Required Labels
    """
    manifests_dir = state.get("manifests_dir", "")
    if not manifests_dir or not os.path.exists(manifests_dir):
        return {
            "current_node": "static_verification",
            "errors": [f"Manifests directory not found: {manifests_dir}"],
            "static_report": {"status": "FAILED", "message": "Manifests directory missing"}
        }

    issues = []
    
    # Iterate through all yaml files in the directory
    for root, _, files in os.walk(manifests_dir):
        for file in files:
            if not file.endswith((".yaml", ".yml")):
                continue
                
            file_path = Path(root) / file
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    docs = yaml.safe_load_all(f)
                    for doc in docs:
                        if not doc:
                            continue
                            
                        kind = doc.get("kind", "")
                        metadata = doc.get("metadata", {})
                        name = metadata.get("name", "unknown")
                        
                        # 1. Check Labels
                        labels = metadata.get("labels", {})
                        if "app.kubernetes.io/name" not in labels:
                            issues.append(f"[{kind}/{name}] missing recommended label 'app.kubernetes.io/name'")
                            
                        # 2. Check Pod Specs in Workloads
                        if kind in ("Deployment", "StatefulSet", "Job", "DaemonSet"):
                            pod_spec = doc.get("spec", {}).get("template", {}).get("spec", {})
                            containers = pod_spec.get("containers", [])
                            
                            for container in containers:
                                container_name = container.get("name", "unknown")
                                issues.extend(check_resources(container, f"{kind}/{name}", container_name))
                                # Only check probes for long-running workloads, not Jobs
                                if kind != "Job":
                                    issues.extend(check_probes(container, f"{kind}/{name}", container_name))
                                    
            except Exception as e:
                issues.append(f"Error parsing {file_path}: {str(e)}")

    status = "FAILED" if issues else "PASSED"
    report = {
        "status": status,
        "issues": issues,
        "message": f"Found {len(issues)} static verification issues."
    }
    
    return {
        "current_node": "static_verification",
        "errors": issues,
        "static_report": report
    }
