from __future__ import annotations

import threading
import time

from app.requirements.agent.steps import step_cloud_inputs


def test_cloud_input_analysis_overlaps_independent_branches(monkeypatch) -> None:
    barrier = threading.Barrier(2)
    entered: list[str] = []
    lock = threading.Lock()

    def deployment(_state):
        with lock:
            entered.append("deployment")
        barrier.wait(timeout=1)
        time.sleep(0.02)
        return {
            "deployment_needs": {"ingress": {}},
            "capability_contract": {"capabilities": []},
            "phase": "deployment_needs",
        }

    def constraints(_state):
        with lock:
            entered.append("constraints")
        barrier.wait(timeout=1)
        return {
            "resource_constraint_extraction": {
                "status": "completed",
                "result": {},
            }
        }

    monkeypatch.setattr(step_cloud_inputs, "derive_deployment_needs", deployment)
    monkeypatch.setattr(step_cloud_inputs, "extract_resource_constraints", constraints)

    result = step_cloud_inputs.analyze_cloud_inputs(
        {"classified": [{"id": "FR1", "text": "x", "type": "FR"}]}
    )

    assert set(entered) == {"deployment", "constraints"}
    assert result["deployment_needs"] == {"ingress": {}}
    assert result["resource_constraint_extraction"]["status"] == "completed"
    assert result["phase"] == "cloud_inputs"
