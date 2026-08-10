from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.dependency_audit.experiment_plan import validate_plan

PLAN = Path("evaluation/research_protocol/definitions/dependency-experiment-plan.json")


def test_dependency_plan_separates_provisioning_runtime_and_function():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    validate_plan(plan)
    assert {phase["layer"] for phase in plan["phases"]} == {
        "controlPlane", "runtime", "application",
    }


def test_plan_without_function_oracle_is_rejected():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["functionalOracle"] = {}
    with pytest.raises(ValueError, match="functional oracle"):
        validate_plan(plan)
