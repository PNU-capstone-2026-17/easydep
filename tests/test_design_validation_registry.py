from __future__ import annotations

import pytest

from app.core.validation import ValidationReport
from app.design import validation
from app.design.knowledge import detectors, rules
from app.design.services.class_diagram.validation import diagram as class_validation
from app.design.services.sequence_diagram import validation as sequence_validation


def _catalog_rule_ids(stage: str) -> tuple[str, ...]:
    return tuple(rule.id for rule in rules.judged_by(stage, rules.JUDGED_DETECTOR))


def test_design_check_registries_follow_the_rule_catalog_order() -> None:
    assert tuple(check.rule_id for check in class_validation.CLASS_DIAGRAM_CHECKS) == _catalog_rule_ids(
        rules.CLASS_DIAGRAM
    )
    assert tuple(check.rule_id for check in sequence_validation.SEQUENCE_CHECKS) == _catalog_rule_ids(
        rules.SEQUENCE_DIAGRAM
    )
    assert tuple(check.rule_id for check in detectors.API_SPEC_CHECKS) == _catalog_rule_ids(
        rules.API_SPEC
    )
    assert tuple(check.rule_id for check in detectors.ERD_CHECKS) == _catalog_rule_ids(rules.ERD)


@pytest.mark.parametrize(
    ("checked", "expected"),
    (
        (ValidationReport(status="clean"), "READY"),
        (
            ValidationReport(
                status="findings",
                findings=(detectors.Finding("rule.fix", "needs a repair"),),
            ),
            "BLOCKED",
        ),
        (
            ValidationReport(
                status="needs_input",
                findings=(
                    detectors.Finding(
                        "rule.choice",
                        "needs a product decision",
                        requires_user_input=True,
                    ),
                ),
            ),
            "NEEDS_INPUT",
        ),
        (
            ValidationReport(
                status="findings",
                findings=(
                    detectors.Finding(
                        "rule.choice",
                        "needs a product decision",
                        requires_user_input=True,
                    ),
                    detectors.Finding("rule.fix", "also needs a repair"),
                ),
            ),
            "BLOCKED",
        ),
        (ValidationReport(status="disabled"), "BLOCKED"),
        (ValidationReport(status="error", errors=("check failed",)), "BLOCKED"),
    ),
)
def test_readiness_uses_typed_status_without_treating_incomplete_checks_as_clean(
    monkeypatch: pytest.MonkeyPatch, checked: ValidationReport, expected: str
) -> None:
    def check(_model: dict, _state: dict) -> ValidationReport:
        return checked

    monkeypatch.setattr(
        validation,
        "_CHECKED_STAGES",
        (("test", "model", "test_check", check),),
    )

    report = validation.design_readiness_report({"model": {"present": True}})

    assert report["status"] == expected
    assert report["stages"][0]["status"] == expected
    assert report["stages"][0]["findings"] == [
        finding.as_issue() for finding in checked.findings
    ]
    assert report["findingRecords"] == [
        {"stage": "test", **record} for record in report["stages"][0]["findingRecords"]
    ]


def test_class_readiness_keeps_semantic_relationship_checks() -> None:
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderBoundary",
                    "stereotype": "Boundary",
                    "fields": [],
                    "operations": [],
                    "use_case_ids": [],
                },
                {
                    "className": "OrderEntity",
                    "stereotype": "Entity",
                    "fields": [],
                    "operations": [],
                    "use_case_ids": [],
                },
            ],
            "Relationships": [
                {
                    "source": "OrderBoundary",
                    "target": "OrderEntity",
                    "type": "Association",
                    "sourceMultiplicity": "1",
                    "targetMultiplicity": "1",
                }
            ],
        }
    }

    report = validation.design_readiness_report(state, stages=("class_diagram",))

    assert report["status"] == "BLOCKED"
    assert any(
        finding["ruleId"] == "class.no-boundary-entity-link"
        for finding in report["findingRecords"]
    )
