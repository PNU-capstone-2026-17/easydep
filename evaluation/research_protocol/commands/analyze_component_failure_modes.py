"""고정 입력 구성요소 실험의 실패 모드를 분리해 집계한다."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _check_id(check: dict[str, Any]) -> str:
    name = check.get("name") or check.get("componentId")
    if name:
        return str(name)
    source = check.get("from")
    target = check.get("to")
    if source or target:
        return f"{source or '?'}->{target or '?'}"
    return "(unnamed)"


def _provider(checks: list[dict[str, Any]]) -> str:
    for check in checks:
        if check.get("kind") == "providerBoundary":
            return str(check.get("expected") or "unknown")
    return "unknown"


def _diagnostic_codes(cell: dict[str, Any]) -> list[str]:
    codes = {
        str(item.get("code") or "unspecified")
        for item in cell.get("diagnostics", [])
        if isinstance(item, dict)
    }
    return sorted(codes)


def _delivery_failure_phases(cell: dict[str, Any]) -> list[str]:
    phases: set[str] = set()
    for item in cell.get("diagnostics", []):
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "")
        if "providerSchema validation" in message:
            phases.add("provider-schema-validation")
        else:
            phases.add("unclassified-delivery")
    return sorted(phases)


def _cell_record(
    cell: dict[str, Any], repetition: int, source_file: str
) -> dict[str, Any]:
    evaluation = cell.get("evaluation") or {}
    score = evaluation.get("score") or {}
    checks = score.get("checks") or []
    failed = [
        {"kind": str(check.get("kind") or "unknown"), "id": _check_id(check)}
        for check in checks
        if check.get("status") == "failed"
    ]
    unknown = [
        {"kind": str(check.get("kind") or "unknown"), "id": _check_id(check)}
        for check in checks
        if check.get("status") == "unknown"
    ]
    reference_failures = [
        item for item in failed if item["kind"] == "componentDependencyReference"
    ]
    delivery_completed = cell.get("stepStatus") == "completed"
    dependency_complete = delivery_completed and not reference_failures
    if not delivery_completed:
        outcome = "delivery-failed"
    elif not dependency_complete:
        outcome = "delivered-dependency-incomplete"
    else:
        outcome = "delivered-dependency-complete"
    return {
        "repetition": repetition,
        "sourceFile": source_file,
        "capability": cell.get("pairId"),
        "provider": _provider(checks),
        "arm": cell.get("arm"),
        "caseId": cell.get("caseId"),
        "outcome": outcome,
        "diagnosticCodes": _diagnostic_codes(cell),
        "deliveryFailurePhases": _delivery_failure_phases(cell),
        "checksObservable": delivery_completed,
        "failedChecks": failed,
        "unknownChecks": unknown,
    }


def analyze(paths: list[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    config_hashes: set[str] = set()
    for repetition, path in enumerate(paths, start=1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        config_hashes.add(str(payload.get("configSha256")))
        records.extend(
            _cell_record(cell, repetition, path.as_posix())
            for cell in payload.get("cells", [])
        )
    if len(config_hashes) != 1:
        raise ValueError("입력 실험의 configSha256가 서로 다릅니다.")

    outcome_counts = Counter(record["outcome"] for record in records)
    strata: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    failed_checks: Counter[tuple[str, str, str, str, str]] = Counter()
    diagnostic_codes: Counter[tuple[str, str, str, str]] = Counter()
    for record in records:
        key = (record["capability"], record["provider"], record["arm"])
        strata[key][record["outcome"]] += 1
        if record["checksObservable"]:
            for check in record["failedChecks"]:
                failed_checks[(*key, check["kind"], check["id"])] += 1
        for code in record["diagnosticCodes"]:
            diagnostic_codes[(*key, code)] += 1

    pairs: dict[tuple[int, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        pairs[(record["repetition"], record["capability"], record["provider"])][
            record["arm"]
        ] = record
    paired_candidates = []
    for key, arms in sorted(pairs.items()):
        if set(arms) != {"full", "no-depkb"}:
            continue
        full = arms["full"]
        no_depkb = arms["no-depkb"]
        full_complete = full["outcome"] == "delivered-dependency-complete"
        no_depkb_complete = no_depkb["outcome"] == "delivered-dependency-complete"
        both_delivered = full["checksObservable"] and no_depkb["checksObservable"]
        if not both_delivered:
            classification = "delivery-not-comparable"
        elif full_complete != no_depkb_complete:
            classification = "paired-structural-contrast"
        elif full_complete:
            classification = "paired-no-structural-contrast"
        else:
            classification = "paired-both-structurally-incomplete"
        paired_candidates.append(
            {
                "repetition": key[0],
                "capability": key[1],
                "provider": key[2],
                "classification": classification,
                "fullCaseId": full["caseId"],
                "noDepkbCaseId": no_depkb["caseId"],
                "sourceFile": full["sourceFile"],
                "cloudExecutionPreflightRequired": True,
                "fullRequirementGateFailures": [
                    item
                    for item in full["failedChecks"]
                    if item["kind"] in {"capability", "forbidden"}
                ],
                "noDepkbRequirementGateFailures": [
                    item
                    for item in no_depkb["failedChecks"]
                    if item["kind"] in {"capability", "forbidden"}
                ],
            }
        )

    return {
        "schemaVersion": "easydep-component-failure-analysis/v1",
        "configSha256": next(iter(config_hashes)),
        "sourceFiles": [path.as_posix() for path in paths],
        "cellCount": len(records),
        "outcomes": dict(sorted(outcome_counts.items())),
        "strata": [
            {
                "capability": key[0],
                "provider": key[1],
                "arm": key[2],
                "outcomes": dict(sorted(counts.items())),
            }
            for key, counts in sorted(strata.items())
        ],
        "recurrentFailedChecks": [
            {
                "capability": key[0],
                "provider": key[1],
                "arm": key[2],
                "kind": key[3],
                "id": key[4],
                "failedRepetitions": count,
                "systematicAcrossThreeRepetitions": count == 3,
            }
            for key, count in sorted(failed_checks.items())
        ],
        "deliveryDiagnosticCodes": [
            {
                "capability": key[0],
                "provider": key[1],
                "arm": key[2],
                "code": key[3],
                "occurrences": count,
            }
            for key, count in sorted(diagnostic_codes.items())
        ],
        "pairedCloudCandidates": paired_candidates,
        "cells": records,
        "interpretationBoundary": {
            "diagnosticCodeIsRootCause": False,
            "failedCheckIsFunctionalFailure": False,
            "structuralContrastIsCloudReady": False,
            "checksAfterDeliveryFailureExcludedFromRecurrence": True,
            "systematicMeans": "동일한 고정 입력·계층에서 3회 모두 같은 check가 실패함",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = analyze(args.inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
