"""Bounded live class-optimization orchestration without network calls."""

from __future__ import annotations

from typing import Any

from evaluation.class_design_optimization_run import (
    apply_qualitative_review,
    execute_live_e1,
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
