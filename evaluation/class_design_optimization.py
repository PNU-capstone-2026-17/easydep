"""Offline, bounded E1 class-design optimization runner.

The runner consumes already-produced candidate JSON files. It never invokes a
provider or a generation service, so a protocol run cannot silently spend an
extra LLM call while evaluating a candidate. The companion evaluator performs
the schema/reference/call/sequence checks; this module owns the frozen cell
schedule, run isolation, stop-on-gate-failure behavior, and cache observations.
"""
from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evaluation.class_design_evaluation import (
    CASE_ID,
    evaluate_candidate,
)

SCHEMA_VERSION = "easydep-class-design-optimization/v1"
MAX_E1_RUNS = 9
RETRY_BUDGET = 0


@dataclass(frozen=True)
class E1Cell:
    """One frozen experiment cell and its candidate artifact key."""

    key: str
    treatment: str
    ordinal: int


E1_CELLS: tuple[E1Cell, ...] = (
    E1Cell("baseline-1", "baseline", 1),
    E1Cell("baseline-2", "baseline", 2),
    E1Cell("baseline-3", "baseline", 3),
    E1Cell("compact", "compact", 1),
    E1Cell("call-plan-low", "call-plan-low", 1),
    E1Cell("operation-low", "operation-low", 1),
    E1Cell("synthetic-1", "synthetic", 1),
    E1Cell("synthetic-2", "synthetic", 2),
    E1Cell("synthetic-3", "synthetic", 3),
)

if len(E1_CELLS) != MAX_E1_RUNS:  # pragma: no cover - frozen protocol guard
    raise AssertionError("the frozen E1 protocol must contain exactly nine cells")


class OptimizationGateFailure(RuntimeError):
    """Raised only when a caller explicitly requests fail-fast exception mode."""

    def __init__(self, cell: str, findings: list[dict[str, Any]]) -> None:
        super().__init__(f"E1 gate failed for {cell}")
        self.cell = cell
        self.findings = findings


def frozen_e1_schedule() -> tuple[E1Cell, ...]:
    """Return the immutable nine-cell schedule."""

    return E1_CELLS


def _run_id(cell: E1Cell, run_id_factory: Callable[[], str] | None) -> str:
    suffix = run_id_factory() if run_id_factory else uuid.uuid4().hex
    return f"{CASE_ID}:e1:{cell.key}:{suffix}"


def _candidate_parts(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Accept a raw BCE model or an envelope with optional observations."""

    if "classModel" in candidate:
        class_model = candidate.get("classModel")
        if not isinstance(class_model, dict):
            raise TypeError("candidate classModel must be an object")
        sequence = candidate.get("sequenceModel")
        metrics = candidate.get("metrics")
        cache = candidate.get("cache")
    else:
        class_model = dict(candidate)
        sequence = None
        metrics = None
        cache = None
    if sequence is not None and not isinstance(sequence, dict):
        raise TypeError("candidate sequenceModel must be an object")
    if metrics is not None and not isinstance(metrics, dict):
        raise TypeError("candidate metrics must be an object")
    if cache is not None and not isinstance(cache, dict):
        raise TypeError("candidate cache must be an object")
    return class_model, sequence, metrics, cache


def evaluate_cache_observations(
    cache: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Check optional cold/warm observations without making a provider call.

    A raw class artifact has no runtime telemetry and therefore receives a
    ``not_assessed`` cache gate. When an envelope supplies cold/warm metrics,
    warm physical calls must be zero; logical cache events may remain present.
    """

    if cache is None:
        return {"status": "not_assessed", "findings": [], "findingCount": 0}
    cold = cache.get("cold")
    warm = cache.get("warm")
    findings: list[dict[str, Any]] = []
    if not isinstance(cold, dict) or not isinstance(warm, dict):
        findings.append({
            "ruleId": "optimization.cache-cold-warm",
            "message": "cache observations must provide cold and warm objects",
            "location": None,
        })
    else:
        warm_calls = warm.get("physicalLlmCalls", warm.get("llm_calls"))
        if warm_calls != 0:
            findings.append({
                "ruleId": "optimization.cache-warm-physical-calls",
                "message": "warm accepted-unit cache must have zero physical provider calls",
                "location": "warm",
            })
        if warm.get("logicalCacheEvents") is not None and not isinstance(
            warm.get("logicalCacheEvents"), int
        ):
            findings.append({
                "ruleId": "optimization.cache-logical-events",
                "message": "logicalCacheEvents must be an integer",
                "location": "warm",
            })
    return {
        "status": "passed" if not findings else "failed",
        "findings": findings,
        "findingCount": len(findings),
    }


def _findings(evaluation: Mapping[str, Any], cache_gate: Mapping[str, Any]) -> list[dict[str, Any]]:
    gates = evaluation.get("machineGates") or {}
    result: list[dict[str, Any]] = []
    for gate_name, gate in gates.items():
        if isinstance(gate, Mapping) and gate.get("status") == "failed":
            result.extend({"gate": gate_name, **dict(item)} for item in gate.get("findings") or [])
    if cache_gate.get("status") == "failed":
        result.extend({"gate": "cache", **dict(item)} for item in cache_gate.get("findings") or [])
    return result


def run_e1(
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    run_id_factory: Callable[[], str] | None = None,
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    """Evaluate the frozen nine cells and stop on the first failed gate.

    ``candidates`` maps the schedule keys (for example ``baseline-1`` or
    ``compact``) to raw BCE models or the documented envelope. Every required
    key must be supplied before the first cell starts; this avoids converting a
    missing artifact into an accidental retry. ``stop_on_failure=False`` is
    available for offline diagnostics but still records the failure and never
    retries it.
    """

    expected = {cell.key for cell in E1_CELLS}
    missing = sorted(expected - set(candidates))
    extra = sorted(set(candidates) - expected)
    if missing:
        raise ValueError("missing frozen E1 candidate(s): " + ", ".join(missing))
    if extra:
        raise ValueError("unknown frozen E1 candidate(s): " + ", ".join(extra))

    runs: list[dict[str, Any]] = []
    stopped_at: str | None = None
    for cell in E1_CELLS:
        candidate = candidates[cell.key]
        class_model, sequence_model, metrics, cache = _candidate_parts(candidate)
        evaluation = evaluate_candidate(class_model, sequence_model=sequence_model)
        cache_gate = evaluate_cache_observations(cache)
        findings = _findings(evaluation, cache_gate)
        run = {
            "runId": _run_id(cell, run_id_factory),
            "cell": cell.key,
            "treatment": cell.treatment,
            "ordinal": cell.ordinal,
            "retryBudget": RETRY_BUDGET,
            "metrics": dict(metrics or {}),
            "machineGates": {
                **dict(evaluation.get("machineGates") or {}),
                "cacheColdWarm": cache_gate,
            },
            "status": "passed" if not findings else "failed",
            "findings": findings,
        }
        runs.append(run)
        if findings and stop_on_failure:
            stopped_at = cell.key
            break

    return {
        "schemaVersion": SCHEMA_VERSION,
        "caseId": CASE_ID,
        "maxRuns": MAX_E1_RUNS,
        "retryBudget": RETRY_BUDGET,
        "schedule": [asdict(cell) for cell in E1_CELLS],
        "status": "stopped" if stopped_at else "completed",
        "stoppedAt": stopped_at,
        "runCount": len(runs),
        "runs": runs,
    }


def _load_candidates(directory: Path) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for cell in E1_CELLS:
        path = directory / f"{cell.key}.json"
        if not path.is_file():
            raise FileNotFoundError(f"missing frozen E1 candidate: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError(f"candidate must be a JSON object: {path}")
        candidates[cell.key] = value
    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-dir", required=True, type=Path,
        help="directory containing the nine frozen <cell>.json artifacts",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--continue-after-failure", action="store_true",
        help="collect diagnostic cells after a failed gate (never retries a cell)",
    )
    args = parser.parse_args(argv)
    report = run_e1(
        _load_candidates(args.candidate_dir),
        stop_on_failure=not args.continue_after_failure,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "completed" and all(
        run["status"] == "passed" for run in report["runs"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
