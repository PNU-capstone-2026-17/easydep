"""의존성 개입 실험이 생성 성공과 기능 성공을 분리하는지 검증한다."""
from __future__ import annotations

from typing import Any

REQUIRED_PHASES = (
    "controlProvision",
    "controlStartup",
    "controlFunction",
    "dependencyIntervention",
    "interventionProvision",
    "interventionStartup",
    "interventionFunction",
    "restorationFunction",
)


def validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("schemaVersion") != "easydep-dependency-experiment-plan/v1":
        raise ValueError("unsupported dependency experiment plan")
    oracle = plan.get("functionalOracle") or {}
    if not oracle.get("request") or not oracle.get("successPredicate"):
        raise ValueError("plan requires an application-level functional oracle")
    phases = plan.get("phases") or []
    names = [item.get("id") for item in phases]
    if names != list(REQUIRED_PHASES):
        raise ValueError("plan must preserve the control-intervention-restoration sequence")
    for phase in phases:
        if phase.get("layer") not in {"controlPlane", "runtime", "application"}:
            raise ValueError("every phase requires a valid observation layer")
        if not phase.get("measure"):
            raise ValueError("every phase requires a measure")
    outcomes = plan.get("outcomeClasses") or {}
    required = {"provisionBlocked", "runtimeBlocked", "functionBlocked", "noEffect"}
    if set(outcomes) != required:
        raise ValueError("plan must classify provisioning and functional failures separately")
