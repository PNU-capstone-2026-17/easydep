import json

from evaluation.research_protocol.commands.evaluate_app_cloud_ablation import (
    DEFAULT_CASES,
    evaluate,
)


def test_fixed_input_ablation_covers_general_contract_boundaries():
    cases = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))

    result = evaluate(cases)

    assert {item["group"] for item in result["cases"]} == {
        "build-runtime-dependency",
        "runtime-integration",
        "port-binding",
        "storage-binding",
    }
    elapsed = result["summary"].pop("evaluationElapsedSeconds")
    assert elapsed >= 0
    assert result["summary"] == {
        "mismatchCaseCount": 4,
        "controlCaseCount": 4,
        "fullTruePositiveCount": 4,
        "fullFalsePositiveCount": 0,
        "noValidatorEarlyDetectionCount": 0,
        "repairOwnerCorrectCount": 4,
        "sameInputAcrossArmsCount": 8,
        "functionalSuccessMeasured": False,
        "repairExecutionMeasured": False,
    }


def test_control_and_mismatch_inputs_differ_only_inside_each_contract_group():
    cases = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))["cases"]
    grouped = {}
    for case in cases:
        grouped.setdefault(case["group"], []).append(case)

    assert all(len(items) == 2 for items in grouped.values())
    assert all(
        {item["expectedDiagnostic"] is None for item in items} == {True, False}
        for items in grouped.values()
    )
