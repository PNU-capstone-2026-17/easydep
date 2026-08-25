from __future__ import annotations

import pytest

from app.core.validation import ValidationReport
from app.design import validation
from app.design.knowledge import detectors, rules


def _catalog_rule_ids(stage: str) -> tuple[str, ...]:
    return tuple(rule.id for rule in rules.judged_by(stage, rules.JUDGED_DETECTOR))


def test_design_check_registries_follow_the_rule_catalog_order() -> None:
    assert tuple(check.rule_id for check in detectors.CLASS_DIAGRAM_CHECKS) == _catalog_rule_ids(
        rules.CLASS_DIAGRAM
    )
    assert tuple(check.rule_id for check in detectors.SEQUENCE_CHECKS) == _catalog_rule_ids(
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
