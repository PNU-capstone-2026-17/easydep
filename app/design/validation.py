"""Design-readiness checks shared by design hand-off and implementation entry."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.core.validation import ValidationReport
from app.design.knowledge.detectors import (
    Finding,
    api_spec_validation_report,
    class_diagram_validation_report,
    erd_validation_report,
    sequence_diagram_findings,
)

DESIGN_READINESS_SCHEMA = "easydep-design-readiness/v1alpha1"


def _finding_payload(finding: Finding) -> dict[str, Any]:
    """Keep display text while exposing typed validation evidence."""
    return {
        "ruleId": finding.rule_id,
        "finding": finding.as_issue(),
        "message": finding.message,
        "location": finding.location,
        "requiresUserInput": finding.requires_user_input,
        "origin": finding.origin,
    }


def _readiness_status(
    findings: list[Finding], validation_status: str | None = None
) -> str:
    """Map typed validation evidence to the design hand-off vocabulary."""
    if validation_status in {"disabled", "error"}:
        return "BLOCKED"
    if not findings:
        return "READY"
    if all(finding.requires_user_input for finding in findings):
        return "NEEDS_INPUT"
    return "BLOCKED"


_CHECKED_STAGES: tuple[
    tuple[str, str, str, Callable[[dict, dict], ValidationReport | list[Finding]]], ...
] = (
    ("class_diagram", "extracted_bce_classes", "class_diagram_check", class_diagram_validation_report),
    ("sequence_diagram", "sequence_diagram_model", "sequence_diagram_check", sequence_diagram_findings),
    ("api_spec", "api_spec_model", "api_spec_check", api_spec_validation_report),
    ("erd", "erd_bce_classes", "erd_check", erd_validation_report),
)


def design_readiness_report(
    state: dict[str, Any], stages: Iterable[str] | None = None
) -> dict[str, Any]:
    """Return unresolved deterministic findings in a transport-safe form."""
    selected = set(stages) if stages is not None else None
    reports: list[dict[str, Any]] = []
    for stage, model_key, _, check in _CHECKED_STAGES:
        if selected is not None and stage not in selected:
            continue
        model = state.get(model_key)
        if not isinstance(model, dict) or not model:
            continue
        checked = check(model, state)
        validation_status: str | None = None
        if isinstance(checked, ValidationReport):
            validation_status = checked.status
            findings = [Finding.model_validate(finding) for finding in checked.findings]
        else:
            # Sequence collections include cross-diagram checks in their
            # established public list-returning adapter.
            findings = checked
        reports.append(
            {
                "stage": stage,
                "findings": [finding.as_issue() for finding in findings],
                "findingRecords": [_finding_payload(finding) for finding in findings],
                "status": _readiness_status(findings, validation_status),
            }
        )
    unresolved = [
        {"stage": report["stage"], "finding": finding}
        for report in reports
        for finding in report["findings"]
    ]
    status = "READY"
    if any(report["status"] == "BLOCKED" for report in reports):
        status = "BLOCKED"
    elif any(report["status"] == "NEEDS_INPUT" for report in reports):
        status = "NEEDS_INPUT"
    return {
        "schemaVersion": DESIGN_READINESS_SCHEMA,
        "status": status,
        "stages": reports,
        "findings": unresolved,
        "findingRecords": [
            {"stage": report["stage"], **finding}
            for report in reports
            for finding in report["findingRecords"]
        ],
    }


def rehydrated_check_state(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Rebuild visible check state from stored models without claiming a repair."""
    report = design_readiness_report(state)
    by_stage = {str(item["stage"]): item for item in report["stages"]}
    result: dict[str, dict[str, Any]] = {}
    for stage, model_key, check_key, _ in _CHECKED_STAGES:
        item = by_stage.get(stage)
        if item is None:
            continue
        findings = list(item["findings"])
        check = {
            "findings": findings,
            "repair_iters": 0,
            "stopped": "clean" if not findings else "checked_only",
        }
        result[check_key] = check
    return result

