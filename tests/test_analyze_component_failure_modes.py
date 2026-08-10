from pathlib import Path

import pytest

from evaluation.research_protocol.commands.analyze_component_failure_modes import analyze


def _payload(config_hash: str, repetition: int, cells: list[dict]) -> dict:
    return {
        "configSha256": config_hash,
        "configuration": {"repetition": repetition},
        "cells": cells,
    }


def _cell(status: str, failed_reference: bool, diagnostic: bool = False) -> dict:
    checks = [
        {"kind": "providerBoundary", "expected": "vendor-a", "status": "passed"}
    ]
    if failed_reference:
        checks.append(
            {
                "kind": "componentDependencyReference",
                "from": "source",
                "to": "target",
                "status": "failed",
            }
        )
    return {
        "pairId": "capability-a",
        "arm": "full",
        "caseId": "arbitrary-case",
        "stepStatus": status,
        "diagnostics": [{"code": "ProviderValidationError"}] if diagnostic else [],
        "evaluation": {"score": {"checks": checks}},
    }


def test_analyze_separates_delivery_and_dependency_failures(tmp_path: Path) -> None:
    paths = []
    for repetition, cell in enumerate(
        [
            _cell("failed", True, diagnostic=True),
            _cell("completed", True),
            _cell("completed", False),
        ],
        start=1,
    ):
        path = tmp_path / f"r{repetition}.json"
        path.write_text(
            __import__("json").dumps(_payload("same", repetition, [cell])),
            encoding="utf-8",
        )
        paths.append(path)

    result = analyze(paths)

    assert result["outcomes"] == {
        "delivered-dependency-complete": 1,
        "delivered-dependency-incomplete": 1,
        "delivery-failed": 1,
    }
    reference = next(
        item
        for item in result["recurrentFailedChecks"]
        if item["kind"] == "componentDependencyReference"
    )
    assert reference["failedRepetitions"] == 1
    assert reference["systematicAcrossThreeRepetitions"] is False
    assert result["deliveryDiagnosticCodes"][0]["code"] == "ProviderValidationError"
    assert [item["repetition"] for item in result["cells"]] == [1, 2, 3]


def test_analyze_rejects_mixed_experiment_configs(tmp_path: Path) -> None:
    paths = []
    for repetition, config_hash in enumerate(["a", "b"], start=1):
        path = tmp_path / f"r{repetition}.json"
        path.write_text(
            __import__("json").dumps(
                _payload(config_hash, repetition, [_cell("completed", False)])
            ),
            encoding="utf-8",
        )
        paths.append(path)

    with pytest.raises(ValueError, match="configSha256"):
        analyze(paths)


def test_analyze_selects_only_observable_structural_contrast(tmp_path: Path) -> None:
    full = _cell("completed", False)
    no_depkb = _cell("completed", True)
    no_depkb["arm"] = "no-depkb"
    path = tmp_path / "paired.json"
    path.write_text(
        __import__("json").dumps(_payload("same", 1, [full, no_depkb])),
        encoding="utf-8",
    )

    result = analyze([path])

    assert result["pairedCloudCandidates"] == [
        {
            "repetition": 1,
            "capability": "capability-a",
            "provider": "vendor-a",
            "classification": "paired-structural-contrast",
            "fullCaseId": "arbitrary-case",
            "noDepkbCaseId": "arbitrary-case",
            "sourceFile": path.as_posix(),
            "cloudExecutionPreflightRequired": True,
            "fullRequirementGateFailures": [],
            "noDepkbRequirementGateFailures": [],
        }
    ]
