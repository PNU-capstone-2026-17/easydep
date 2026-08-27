from __future__ import annotations

import json
from pathlib import Path

from evaluation.class_design_optimization import (
    MAX_E1_RUNS,
    RETRY_BUDGET,
    evaluate_cache_observations,
    frozen_e1_schedule,
    run_e1,
)
from evaluation.class_design_evaluation import CASE_ID, compare, evaluate_candidate

ROOT = Path(__file__).parent.parent
GOLD = ROOT / "evaluation" / "baselines" / "course-registration-cases" / "goldset" / "e1-aws"
PROTOCOL = ROOT / "evaluation" / "class_design_optimization_protocol.md"


def _candidate() -> dict:
    return {
        "Classes": [{
            "className": "OrderForm", "stereotype": "Boundary", "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "OrderForm::submit(courseId:String)", "name": "submit",
                "parameters": [{"name": "courseId", "type": "String"}],
                "returnType": "void", "stepRefs": ["UC1:main:1"],
            }],
        }],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [{
            "collaborationId": "UC1-request", "useCaseIds": ["UC1"], "entryActor": "Student",
            "calls": [{
                "callId": "ignored-by-schema", "receiverOperationId": "OrderForm::submit(courseId:String)",
                "stepRefs": ["UC1:main:1"],
                "argumentBindings": [{"parameter": "courseId", "sourceRef": "UC1:main:1#courseId"}],
            }],
        }],
    }


def test_evaluation_uses_the_single_frozen_course_registration_checkpoints():
    report = evaluate_candidate(_candidate())

    assert report["caseId"] == "e1-aws"
    assert set(report["upstreamCheckpoints"]) == {"requirements", "specifications"}
    assert "classStructure" in report["machineGates"]
    assert report["machineGates"]["downstreamSequence"]["status"] == "not_assessed"


def test_comparison_does_not_encode_the_accepted_diagram_as_an_oracle():
    report = compare(_candidate(), _candidate())

    assert report["machineGateFindingDelta"] == dict.fromkeys(
        report["baseline"]["machineGates"], 0
    )
    assert "class names, counts, topology, or text" in report["comparisonNote"]
    assert "responsibility_cohesion" in report["qualitativeRubric"]


def test_schema_failure_is_reported_without_trying_to_run_downstream_checks():
    report = evaluate_candidate({"Classes": [{"className": "Bad"}]})

    assert report["status"] == "failed"
    assert report["machineGates"]["schema"]["status"] == "failed"
    assert report["machineGates"]["referencesAndTypes"]["status"] == "not_assessed"


def test_gold_files_remain_input_artifacts_not_a_generated_evaluation_fixture():
    manifest = json.loads((GOLD / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["caseId"] == CASE_ID
    assert not (ROOT / "evaluation" / "class_design" / "fixtures").exists()


def test_class_optimization_protocol_is_frozen_and_bounded():
    protocol = PROTOCOL.read_text(encoding="utf-8")

    for contract in (
        "최대 9회",
        "baseline 3회",
        "`compact` 1회",
        "`call-plan-low` 1회",
        "`operation-low` 1회",
        "합성 후보 3회",
        "retry 예산은 0",
        "독립 `run_id`",
        "즉시 중단",
        "cold와 warm",
        "physical `llm_calls`는 0",
    ):
        assert contract in protocol


def test_optimization_runner_has_exactly_nine_independent_frozen_cells():
    schedule = frozen_e1_schedule()

    assert len(schedule) == MAX_E1_RUNS == 9
    assert [cell.key for cell in schedule[:3]] == [
        "baseline-1", "baseline-2", "baseline-3",
    ]
    assert [cell.key for cell in schedule[3:6]] == [
        "compact", "call-plan-low", "operation-low",
    ]
    assert [cell.key for cell in schedule[6:]] == [
        "synthetic-1", "synthetic-2", "synthetic-3",
    ]
    assert RETRY_BUDGET == 0


def test_optimization_runner_stops_on_the_first_failed_gate_without_retrying():
    candidates = {cell.key: _candidate() for cell in frozen_e1_schedule()}
    candidates["baseline-2"] = {"Classes": [{"className": "invalid"}]}
    ids = iter(f"isolated-{index}" for index in range(MAX_E1_RUNS))

    report = run_e1(candidates, run_id_factory=lambda: next(ids))

    assert report["status"] == "stopped"
    assert report["stoppedAt"] == "baseline-2"
    assert report["runCount"] == 2
    assert [run["status"] for run in report["runs"]] == ["passed", "failed"]
    assert [run["retryBudget"] for run in report["runs"]] == [0, 0]
    assert len({run["runId"] for run in report["runs"]}) == 2


def test_cache_gate_requires_zero_warm_physical_calls_but_allows_logical_events():
    assert evaluate_cache_observations({
        "cold": {"physicalLlmCalls": 1},
        "warm": {"physicalLlmCalls": 0, "logicalCacheEvents": 3},
    })["status"] == "passed"
    failed = evaluate_cache_observations({
        "cold": {"physicalLlmCalls": 1},
        "warm": {"physicalLlmCalls": 1},
    })
    assert failed["status"] == "failed"
