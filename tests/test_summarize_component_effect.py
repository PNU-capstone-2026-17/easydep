import json

from evaluation.research_protocol.commands.summarize_component_effect import summarize


def _cell(case_id, arm, *, completed, reference_status):
    return {
        "caseId": case_id,
        "pairId": "pair",
        "arm": arm,
        "stepStatus": "completed" if completed else "failed",
        "inputApplicationSha256": "same",
        "sourceApplicationSha256": "same",
        "modeledOutcomes": [{}] if arm == "full" else [],
        "realizationIds": [],
        "elapsedSeconds": 1,
        "evaluation": {"score": {"checks": [
            {"kind": "providerBoundary", "status": "passed" if completed else "unknown"},
            {"kind": "componentDependencyReference", "status": reference_status},
        ]}},
    }


def test_summary_keeps_delivery_and_dependency_outcomes_separate(tmp_path):
    path = tmp_path / "r1.json"
    path.write_text(json.dumps({
        "configSha256": "frozen",
        "snapshots": {"pair": {"applicationTests": {"passed": True}}},
        "cells": [
            _cell("CASE-aws", "full", completed=True, reference_status="passed"),
            _cell("CASE-aws", "no-depkb", completed=False, reference_status="failed"),
        ],
    }), encoding="utf-8")

    result = summarize([path])

    assert result["armSummary"]["full"]["deliveryCompleted"] == 1
    assert result["armSummary"]["full"]["dependencyComplete"] == 1
    assert result["pairedDelivery"]["fullWins"] == 1
    assert result["pairedDependencyComplete"]["fullWins"] == 1
    assert result["pairedReferenceRecall"]["meanDifference"] == 1.0


def test_summary_rejects_nonidentical_application_inputs(tmp_path):
    path = tmp_path / "invalid.json"
    cell = _cell("CASE-aws", "full", completed=True, reference_status="passed")
    cell["sourceApplicationSha256"] = "different"
    path.write_text(json.dumps({
        "configSha256": "frozen",
        "snapshots": {"pair": {"applicationTests": {"passed": True}}},
        "cells": [cell],
    }), encoding="utf-8")

    try:
        summarize([path])
    except ValueError as error:
        assert "유효하지 않은 반복 입력" in str(error)
    else:
        raise AssertionError("input hash mismatch must be rejected")
