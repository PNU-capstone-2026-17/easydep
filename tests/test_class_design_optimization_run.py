"""Bounded live class-optimization orchestration without network calls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.design.schemas.class_model import BCEModel
from evaluation.class_design_optimization_run import (
    apply_qualitative_review,
    execute_live_e1,
    record_failed_inflight,
)


def _metrics(*, input_tokens: int, total_tokens: int, wall: float, physical: int = 3):
    return {
        "physicalLlmCalls": physical,
        "logicalCacheEvents": 3,
        "inputTokens": input_tokens,
        "outputTokens": total_tokens - input_tokens,
        "totalTokens": total_tokens,
        "wallSeconds": wall,
        "repairs": 1,
        "handoffs": 1,
        "stageOutputMax": {"inventory": 1000, "operation": 800, "callPlan": 500},
        "stageLengthOrSchemaFailure": {
            "inventory": False,
            "operation": False,
            "callPlan": False,
        },
    }


def test_live_protocol_bounds_cold_cells_and_reviews_without_rerunning_generation():
    calls: list[tuple[str, dict[str, Any]]] = []

    def cell_runner(key, treatment, overrides, **_kwargs):
        calls.append((key, dict(overrides)))
        if key == "compact":
            metrics = _metrics(input_tokens=800, total_tokens=900, wall=9)
        elif key.startswith("candidate-") and key != "candidate-warm-verification":
            metrics = _metrics(input_tokens=750, total_tokens=850, wall=8)
        elif key == "candidate-warm-verification":
            metrics = _metrics(
                input_tokens=0, total_tokens=0, wall=0.1, physical=0
            )
        else:
            metrics = _metrics(input_tokens=1000, total_tokens=1000, wall=10)
        return {
            "runId": f"run:{key}",
            "cell": key,
            "treatment": treatment,
            "settings": dict(overrides),
            "metrics": metrics,
            "timingEvents": [],
            "machineGates": {"status": "passed"},
            "artifacts": {},
            "status": "passed",
        }

    report = execute_live_e1(cell_runner=cell_runner)

    assert report["coldGenerationCount"] == 9
    assert len([key for key, _settings in calls if "warm" not in key]) == 9
    assert report["warmVerification"]["metrics"]["physicalLlmCalls"] == 0
    assert report["decision"]["candidateCaps"] == {
        "inventory": 2048,
        "operation": 2048,
        "callPlan": 2048,
    }
    candidate_settings = next(
        values for key, values in calls if key == "candidate-1"
    )
    assert candidate_settings["design_class_compact_operation_payload"] is True
    assert candidate_settings["design_class_call_plan_reasoning_effort"] == "low"
    assert candidate_settings["design_class_operation_reasoning_effort"] == "low"
    assert report["decision"]["adopted"] is False

    reviewed = apply_qualitative_review(
        report, baseline_issues=0, candidate_issues=0
    )
    assert reviewed["decision"]["adopted"] is True
    assert len(calls) == 10


def test_live_protocol_stops_candidate_repetitions_after_a_failed_gate():
    called: list[str] = []

    def cell_runner(key, treatment, overrides, **_kwargs):
        called.append(key)
        status = "failed" if key == "candidate-2" else "passed"
        return {
            "runId": f"run:{key}",
            "cell": key,
            "treatment": treatment,
            "settings": dict(overrides),
            "metrics": _metrics(input_tokens=800, total_tokens=900, wall=9),
            "timingEvents": [],
            "machineGates": {"status": status},
            "artifacts": {},
            "status": status,
        }

    report = execute_live_e1(cell_runner=cell_runner)

    assert report["status"] == "stopped"
    assert report["stoppedAt"] == "candidate-2"
    assert "candidate-3" not in called
    assert "candidate-warm-verification" not in called


def test_live_protocol_resumes_only_after_a_completed_prefix():
    calls: list[str] = []
    saved: dict[str, Any] | None = None

    def cell_runner(key, treatment, overrides, **_kwargs):
        calls.append(key)
        physical = 0 if key == "candidate-warm-verification" else 3
        return {
            "runId": f"run:{key}",
            "cell": key,
            "treatment": treatment,
            "settings": dict(overrides),
            "metrics": _metrics(
                input_tokens=0 if physical == 0 else (800 if key == "compact" else 750),
                total_tokens=0 if physical == 0 else (900 if key == "compact" else 850),
                wall=0.1 if physical == 0 else 8,
                physical=physical,
            ),
            "timingEvents": [],
            "machineGates": {"status": "passed"},
            "artifacts": {},
            "status": "passed",
        }

    class StopAfterCompact(RuntimeError):
        pass

    def progress(report):
        nonlocal saved
        saved = deepcopy(report)
        if [run["cell"] for run in report["runs"]][-1:] == ["compact"]:
            raise StopAfterCompact

    with pytest.raises(StopAfterCompact):
        execute_live_e1(cell_runner=cell_runner, progress=progress)

    assert saved is not None
    assert saved["status"] == "in_progress"
    assert saved["inFlight"] is None
    assert saved["coldGenerationCount"] == 4
    calls.clear()

    report = execute_live_e1(cell_runner=cell_runner, resume_report=saved)

    assert calls[:2] == ["call-plan-low", "operation-low"]
    assert not any(key.startswith("baseline-") or key == "compact" for key in calls)
    assert report["coldGenerationCount"] == 9
    assert report["warmVerification"]["metrics"]["physicalLlmCalls"] == 0


def test_live_protocol_refuses_to_repeat_an_ambiguous_in_flight_cell():
    report = {
        "schemaVersion": "easydep-class-design-live-optimization/v1",
        "caseId": "e1-aws",
        "maxColdGenerations": 9,
        "coldGenerationCount": 0,
        "retryBudget": 0,
        "status": "in_progress",
        "stoppedAt": None,
        "inFlight": "baseline-1",
        "runs": [],
        "warmVerification": None,
        "decision": {"adopted": False, "status": "in_progress"},
    }

    with pytest.raises(RuntimeError, match="ambiguous inFlight"):
        execute_live_e1(resume_report=report)


def test_warm_cache_miss_fails_before_any_provider_computation():
    provider_computations = 0

    def cell_runner(key, treatment, overrides, **kwargs):
        nonlocal provider_computations
        if key == "candidate-warm-verification":
            def unexpected_provider_call():
                nonlocal provider_computations
                provider_computations += 1
                return {"unexpected": True}

            kwargs["cache"].get_or_compute("not-accepted-during-cold", unexpected_provider_call)
            raise AssertionError("sealed cache miss must stop before this line")
        metrics = (
            _metrics(input_tokens=800, total_tokens=900, wall=9)
            if key == "compact"
            else _metrics(input_tokens=750, total_tokens=850, wall=8)
        )
        return {
            "runId": f"run:{key}",
            "cell": key,
            "treatment": treatment,
            "settings": dict(overrides),
            "metrics": metrics,
            "timingEvents": [],
            "machineGates": {"status": "passed"},
            "artifacts": {},
            "status": "passed",
        }

    report = execute_live_e1(cell_runner=cell_runner)

    assert provider_computations == 0
    assert report["status"] == "stopped"
    assert report["stoppedAt"] == "candidate-warm-verification"
    warm = report["warmVerification"]
    assert warm["metrics"]["physicalLlmCalls"] == 0
    assert warm["machineGates"]["cacheWarm"]["reason"] == "sealed-cache-miss"


def test_candidate_cap_uses_the_next_tier_even_when_it_exceeds_the_old_cap():
    def cell_runner(key, treatment, overrides, **_kwargs):
        physical = 0 if key == "candidate-warm-verification" else 3
        if physical == 0:
            metrics = _metrics(input_tokens=0, total_tokens=0, wall=0.1, physical=0)
        elif key == "compact":
            metrics = _metrics(input_tokens=800, total_tokens=7500, wall=9)
        elif key.startswith("candidate-"):
            metrics = _metrics(input_tokens=750, total_tokens=7000, wall=8)
        else:
            metrics = _metrics(input_tokens=1000, total_tokens=8000, wall=10)
        metrics["stageOutputMax"]["operation"] = 7000
        return {
            "runId": f"run:{key}",
            "cell": key,
            "treatment": treatment,
            "settings": dict(overrides),
            "metrics": metrics,
            "timingEvents": [],
            "machineGates": {"status": "passed"},
            "artifacts": {},
            "status": "passed",
        }

    report = execute_live_e1(cell_runner=cell_runner)

    assert report["decision"]["candidateCaps"]["operation"] == 16384


def test_generation_value_error_is_a_failed_cell_without_a_provider_retry():
    calls = 0

    def generator(_index, *, cache):
        nonlocal calls
        calls += 1
        raise ValueError("accepted fragment remains invalid")

    report = execute_live_e1(generator=generator)

    assert calls == 7
    assert report["status"] == "stopped"
    assert report["stoppedAt"] == "candidate-1"
    assert report["coldGenerationCount"] == 7
    assert all(run["status"] == "failed" for run in report["runs"])
    assert all(
        run["machineGates"]["generation"]["errorType"] == "ValueError"
        for run in report["runs"]
    )


def test_projection_value_error_is_a_failed_cell_with_the_class_artifact_retained():
    calls = 0

    def generator(_index, *, cache):
        nonlocal calls
        calls += 1
        return BCEModel()

    report = execute_live_e1(generator=generator)

    assert calls == 7
    first = report["runs"][0]
    assert first["status"] == "failed"
    assert first["machineGates"]["projection"]["errorType"] == "ValueError"
    assert first["artifacts"]["classModel"] == {
        "Classes": [],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }
    assert isinstance(first["artifacts"]["classPuml"], str)


def test_recorded_failed_baseline_resumes_at_the_next_cell_without_repeating_it():
    saved: dict[str, Any] | None = None

    class StopAfterBaselineOne(RuntimeError):
        pass

    def successful_cell(key, treatment, overrides, **_kwargs):
        metrics = (
            _metrics(input_tokens=0, total_tokens=0, wall=0.1, physical=0)
            if key == "candidate-warm-verification"
            else _metrics(input_tokens=1000, total_tokens=1200, wall=10)
        )
        return {
            "runId": f"run:{key}",
            "cell": key,
            "treatment": treatment,
            "settings": dict(overrides),
            "metrics": metrics,
            "timingEvents": [],
            "machineGates": {"status": "passed"},
            "artifacts": {},
            "status": "passed",
        }

    def progress(report):
        nonlocal saved
        if report["coldGenerationCount"] == 1 and report["inFlight"] is None:
            saved = deepcopy(report)
            raise StopAfterBaselineOne

    with pytest.raises(StopAfterBaselineOne):
        execute_live_e1(cell_runner=successful_cell, progress=progress)
    assert saved is not None
    saved["inFlight"] = "baseline-2"

    recovered = record_failed_inflight(
        saved,
        error_type="ValueError",
        error_message="operation fragment remains invalid",
    )
    calls: list[str] = []

    def remaining_cell(key, treatment, overrides, **kwargs):
        calls.append(key)
        return successful_cell(key, treatment, overrides, **kwargs)

    report = execute_live_e1(
        resume_report=recovered,
        cell_runner=remaining_cell,
    )

    assert calls[0] == "baseline-3"
    assert "baseline-1" not in calls
    assert "baseline-2" not in calls
    assert report["coldGenerationCount"] == 9
    assert report["status"] == "completed"
    assert report["runs"][1]["machineGates"]["generation"] == {
        "status": "failed",
        "errorType": "ValueError",
        "message": "operation fragment remains invalid",
        "recoveredFromTerminatedProcess": True,
    }
