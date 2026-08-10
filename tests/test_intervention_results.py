from __future__ import annotations

from evaluation.dependency_audit.experiment_plan import REQUIRED_PHASES
from evaluation.dependency_audit.intervention_results import adjudicate_result

EXPERIMENT = "intervention.gcp.backend-service-backend-group.necessity"


def _replication(number: int, intervention_status: str = "failed"):
    phases = []
    for phase_id in REQUIRED_PHASES:
        status = "passed"
        if phase_id == "interventionFunction":
            status = intervention_status
        phases.append({"id": phase_id, "status": status,
                       "observedAt": f"2026-08-08T00:00:0{number}+09:00", "evidence": "artifact.json"})
    return {"replication": number, "budgetCensored": False, "schedulerDelayed": False,
            "outcomeClass": "functionBlocked" if intervention_status == "failed" else "noEffect",
            "phases": phases}


def _result(reps):
    return {"schemaVersion": "easydep-dependency-intervention-result/v1",
            "experimentId": EXPERIMENT, "provider": "gcp", "replications": reps,
            "cleanupVerified": True, "residualResources": []}


def test_three_function_failures_with_recovery_confirm_dependency():
    assert adjudicate_result(_result([_replication(i) for i in range(1, 4)]), EXPERIMENT) == "confirmed"


def test_scheduler_or_budget_censoring_is_not_dependency_failure():
    reps = [_replication(i) for i in range(1, 4)]
    reps[1]["schedulerDelayed"] = True
    assert adjudicate_result(_result(reps), EXPERIMENT) == "inconclusive"


def test_residual_resources_block_confirmation():
    result = _result([_replication(i) for i in range(1, 4)])
    result["cleanupVerified"] = False
    result["residualResources"] = ["edbgint-r1-net"]

    try:
        adjudicate_result(result, EXPERIMENT)
    except ValueError as error:
        assert "zero residual" in str(error)
    else:
        raise AssertionError("residual resources must block confirmation")
