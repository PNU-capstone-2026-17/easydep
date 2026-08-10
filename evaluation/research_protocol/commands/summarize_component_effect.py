"""고정 snapshot component 반복을 사전 정의한 분리 지표로 집계한다."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

ARMS = ("full", "no-depkb")


def _checks(cell: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        item
        for item in ((cell.get("evaluation") or {}).get("score") or {}).get(
            "checks", []
        )
        if item.get("kind") == kind
    ]


def _statuses(cell: dict[str, Any], kind: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in _checks(cell, kind):
        counts[str(item.get("status") or "unknown")] += 1
    return dict(counts)


def _reference_recall(cell: dict[str, Any]) -> float | None:
    statuses = _statuses(cell, "componentDependencyReference")
    expected = statuses.get("passed", 0) + statuses.get("failed", 0)
    return statuses.get("passed", 0) / expected if expected else None


def _dependency_complete(cell: dict[str, Any]) -> bool:
    recall = _reference_recall(cell)
    return cell.get("stepStatus") == "completed" and recall == 1.0


def _exact_sign_pvalue(wins: int, losses: int) -> float | None:
    discordant = wins + losses
    if discordant == 0:
        return None
    tail = sum(math.comb(discordant, index) for index in range(min(wins, losses) + 1))
    return min(1.0, 2 * tail / (2**discordant))


def _arm_summary(cells: list[dict[str, Any]]) -> dict[str, Any]:
    status_kinds = (
        "providerBoundary",
        "capability",
        "forbidden",
        "componentProjection",
        "componentDependencyReference",
    )
    status_summary: dict[str, dict[str, int]] = {}
    for kind in status_kinds:
        combined: dict[str, int] = defaultdict(int)
        for cell in cells:
            for status, count in _statuses(cell, kind).items():
                combined[status] += count
        status_summary[kind] = dict(combined)
    recalls = [value for cell in cells if (value := _reference_recall(cell)) is not None]
    return {
        "cellCount": len(cells),
        "deliveryCompleted": sum(cell.get("stepStatus") == "completed" for cell in cells),
        "dependencyComplete": sum(_dependency_complete(cell) for cell in cells),
        "modeledOutcomeCount": sum(len(cell.get("modeledOutcomes") or []) for cell in cells),
        "realizationCount": sum(len(cell.get("realizationIds") or []) for cell in cells),
        "elapsedSeconds": round(sum(float(cell.get("elapsedSeconds") or 0) for cell in cells), 6),
        "referenceRecallMacro": round(sum(recalls) / len(recalls), 6) if recalls else None,
        "referenceRecallMedian": round(median(recalls), 6) if recalls else None,
        "checks": status_summary,
    }


def summarize(paths: list[Path]) -> dict[str, Any]:
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    hashes = {document.get("configSha256") for document in documents}
    if len(hashes) != 1:
        raise ValueError("반복의 config hash가 일치하지 않는다")
    cells: list[dict[str, Any]] = []
    validity = []
    for repetition, (path, document) in enumerate(zip(paths, documents, strict=True), 1):
        snapshots_passed = all(
            bool(item.get("applicationTests", {}).get("passed"))
            for item in document.get("snapshots", {}).values()
        )
        same_inputs = all(
            item.get("inputApplicationSha256") == item.get("sourceApplicationSha256")
            for item in document.get("cells", [])
        )
        validity.append({
            "repetition": repetition,
            "path": path.as_posix(),
            "cellCount": len(document.get("cells", [])),
            "snapshotsPassed": snapshots_passed,
            "sameInputs": same_inputs,
        })
        if not snapshots_passed or not same_inputs:
            raise ValueError(f"유효하지 않은 반복 입력: {path}")
        for cell in document.get("cells", []):
            cells.append({**cell, "repetition": repetition})

    by_arm = {
        arm: _arm_summary([cell for cell in cells if cell.get("arm") == arm])
        for arm in ARMS
    }
    pairs: dict[tuple[int, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for cell in cells:
        pairs[(cell["repetition"], cell["caseId"])][cell["arm"]] = cell
    delivery = {"fullWins": 0, "noDepkbWins": 0, "ties": 0}
    dependency = {"fullWins": 0, "noDepkbWins": 0, "ties": 0}
    recall_differences = []
    pair_rows = []
    for (repetition, case_id), arms in sorted(pairs.items()):
        if set(arms) != set(ARMS):
            raise ValueError(f"paired arm 누락: r{repetition} {case_id}")
        full = arms["full"]
        baseline = arms["no-depkb"]
        full_delivery = full.get("stepStatus") == "completed"
        baseline_delivery = baseline.get("stepStatus") == "completed"
        _paired_count(delivery, full_delivery, baseline_delivery)
        full_dependency = _dependency_complete(full)
        baseline_dependency = _dependency_complete(baseline)
        _paired_count(dependency, full_dependency, baseline_dependency)
        full_recall = _reference_recall(full)
        baseline_recall = _reference_recall(baseline)
        difference = (
            full_recall - baseline_recall
            if full_recall is not None and baseline_recall is not None
            else None
        )
        if difference is not None:
            recall_differences.append(difference)
        pair_rows.append({
            "repetition": repetition,
            "caseId": case_id,
            "pairId": full.get("pairId"),
            "provider": case_id.rsplit("-", 1)[-1],
            "fullDelivery": full_delivery,
            "noDepkbDelivery": baseline_delivery,
            "fullDependencyComplete": full_dependency,
            "noDepkbDependencyComplete": baseline_dependency,
            "fullReferenceRecall": full_recall,
            "noDepkbReferenceRecall": baseline_recall,
            "referenceRecallDifference": difference,
        })
    for outcome in (delivery, dependency):
        outcome["discordantExactSignPValue"] = _exact_sign_pvalue(
            outcome["fullWins"], outcome["noDepkbWins"]
        )
    return {
        "schemaVersion": "easydep-component-effect-summary/v1",
        "configSha256": next(iter(hashes)),
        "repetitions": validity,
        "measurementBoundary": {
            "requirementsDesignApplicationGenerationCalls": 0,
            "cloudApply": False,
            "cardinalityScored": False,
            "runtimeConstraintsScored": False,
            "primarySeparatedMetrics": [
                "deliveryCompleted",
                "dependencyComplete",
                "componentDependencyReference",
            ],
        },
        "armSummary": by_arm,
        "pairedDelivery": delivery,
        "pairedDependencyComplete": dependency,
        "pairedReferenceRecall": {
            "pairCount": len(recall_differences),
            "meanDifference": round(sum(recall_differences) / len(recall_differences), 6)
            if recall_differences else None,
            "medianDifference": round(median(recall_differences), 6)
            if recall_differences else None,
        },
        "pairs": pair_rows,
        "interpretationLimits": [
            "세 개발 반복이며 CNA 모집단의 무작위 표본이 아니다.",
            "provider validate와 정적 참조는 실제 cloud 기능 성공을 대신하지 않는다.",
            "sign test는 case·CSP 군집을 모델링하지 않은 기술적 보조값이다.",
        ],
    }


def _paired_count(outcome: dict[str, Any], full: bool, baseline: bool) -> None:
    if full and not baseline:
        outcome["fullWins"] += 1
    elif baseline and not full:
        outcome["noDepkbWins"] += 1
    else:
        outcome["ties"] += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = summarize(args.inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
