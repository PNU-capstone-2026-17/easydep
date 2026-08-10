"""앱 기능까지 포함한 의존성 개입 결과를 검증하고 판정한다."""
from __future__ import annotations

from typing import Any

from .experiment_plan import REQUIRED_PHASES

OUTCOMES = {"provisionBlocked", "runtimeBlocked", "functionBlocked", "noEffect"}


def adjudicate_result(result: dict[str, Any], experiment_id: str) -> str:
    if result.get("schemaVersion") != "easydep-dependency-intervention-result/v1":
        raise ValueError("unsupported intervention result")
    if result.get("experimentId") != experiment_id:
        raise ValueError("result does not match registered experiment")
    if result.get("cleanupVerified") is not True or result.get("residualResources") != []:
        raise ValueError("confirmed intervention requires verified zero residual resources")
    replications = result.get("replications") or []
    if len(replications) != 3:
        raise ValueError("exactly three replications are required")
    eligible = []
    for replication in replications:
        phases = replication.get("phases") or []
        if [phase.get("id") for phase in phases] != list(REQUIRED_PHASES):
            raise ValueError("replication does not contain the registered phase sequence")
        for phase in phases:
            if phase.get("status") not in {"passed", "failed", "notReached"}:
                raise ValueError("every phase requires an observable status")
            if not phase.get("observedAt") or phase.get("evidence") is None:
                raise ValueError("every phase requires time and retained evidence")
        if replication.get("outcomeClass") not in OUTCOMES:
            raise ValueError("invalid intervention outcome class")
        if not replication.get("budgetCensored") and not replication.get("schedulerDelayed"):
            eligible.append(replication)
    if len(eligible) != 3:
        return "inconclusive"
    if all(
        _phase(replication, "controlFunction") == "passed"
        and _phase(replication, "interventionFunction") == "failed"
        and _phase(replication, "restorationFunction") == "passed"
        for replication in eligible
    ):
        return "confirmed"
    if all(replication["outcomeClass"] == "noEffect" for replication in eligible):
        return "rejected"
    return "inconclusive"


def _phase(replication: dict[str, Any], phase_id: str) -> str:
    return next(phase["status"] for phase in replication["phases"] if phase["id"] == phase_id)
