from __future__ import annotations

import json
from pathlib import Path

from evaluation.class_design_evaluation import CASE_ID, compare, evaluate_candidate

ROOT = Path(__file__).parent.parent
GOLD = ROOT / "evaluation" / "baselines" / "course-registration-cases" / "goldset" / "e1-aws"


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
