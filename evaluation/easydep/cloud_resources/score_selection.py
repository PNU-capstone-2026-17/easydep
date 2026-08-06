"""Score VM selection without requiring an internal cloud-plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def score(run_dir: Path) -> dict:
    manifest = _load(run_dir / "manifest.json")
    case = _load(run_dir / "input.json")
    actual = _load(run_dir / "vm-selection.json")
    expected = _load(ROOT / "selection_oracle.json")[manifest["caseId"]]
    status_correct = actual.get("status") == expected["status"]
    reason_correct = (
        actual.get("reason") == expected["reason"] if expected.get("reason") else None
    )
    recommendation = actual.get("recommended") or {}
    recommendation_correct = (
        recommendation.get("specName") == expected["recommendedSpec"]
        if expected.get("recommendedSpec")
        else None
    )
    spec = case["resourceSpec"]
    capacity_satisfied = None
    budget_satisfied = None
    performance_satisfied = None
    if actual.get("status") == "selected":
        capacity_satisfied = (
            recommendation.get("vCPU", 0) >= spec.get("minVCpu", 0)
            and recommendation.get("memoryGiB", 0) >= spec.get("minMemoryGiB", 0)
        )
        budget_satisfied = recommendation.get("monthlyComputeListPriceUsd", float("inf")) <= spec.get(
            "monthlyBudgetUSD", float("inf")
        )
        performance_satisfied = (
            recommendation.get("performanceEvidence", {}).get("status") == "ok"
            if expected.get("requireNoPerformanceWarning")
            else True
        )
    checks = [status_correct]
    if reason_correct is not None:
        checks.append(reason_correct)
    if recommendation_correct is not None:
        checks.append(recommendation_correct)
    checks.extend(
        value for value in (capacity_satisfied, budget_satisfied, performance_satisfied)
        if value is not None
    )
    result = {
        "runId": manifest["runId"],
        "variant": manifest["variant"],
        "caseId": manifest["caseId"],
        "passed": all(checks),
        "score": sum(bool(value) for value in checks) / len(checks),
        "checks": {
            "statusCorrect": status_correct,
            "reasonCorrect": reason_correct,
            "recommendationCorrect": recommendation_correct,
            "capacitySatisfied": capacity_satisfied,
            "computeBudgetSatisfied": budget_satisfied,
            "steadyPerformanceSuitable": performance_satisfied,
        },
    }
    (run_dir / "evaluation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for run_dir in args.run_dirs:
        print(json.dumps(score(run_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
