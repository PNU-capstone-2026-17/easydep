"""Design-readiness checks shared by design hand-off and implementation entry."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from app.design.knowledge.detectors import (
    class_diagram_findings,
    erd_findings,
    sequence_diagram_findings,
    api_spec_findings,
)

DESIGN_READINESS_SCHEMA = "easydep-design-readiness/v1alpha1"


_CHECKED_STAGES: tuple[tuple[str, str, str, Callable[[dict, dict], list]], ...] = (
    ("class_diagram", "extracted_bce_classes", "class_diagram_check", class_diagram_findings),
    ("sequence_diagram", "sequence_diagram_model", "sequence_diagram_check", sequence_diagram_findings),
    ("api_spec", "api_spec_model", "api_spec_check", api_spec_findings),
    ("erd", "erd_bce_classes", "erd_check", erd_findings),
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
        findings = check(model, state)
        reports.append(
            {
                "stage": stage,
                "findings": [finding.as_issue() for finding in findings],
                "status": "READY" if not findings else "NEEDS_INPUT",
            }
        )
    unresolved = [
        {"stage": report["stage"], "finding": finding}
        for report in reports
        for finding in report["findings"]
    ]
    return {
        "schemaVersion": DESIGN_READINESS_SCHEMA,
        "status": "READY" if not unresolved else "NEEDS_INPUT",
        "stages": reports,
        "findings": unresolved,
    }


def rehydrated_check_state(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Rebuild visible check state from stored models without claiming a repair."""
    report = design_readiness_report(state)
    by_stage = {str(item["stage"]): item for item in report["stages"]}
    result: dict[str, dict[str, Any]] = {}
    for stage, _, check_key, _ in _CHECKED_STAGES:
        item = by_stage.get(stage)
        if item is None:
            continue
        findings = list(item["findings"])
        result[check_key] = {
            "findings": findings,
            "repair_iters": 0,
            "stopped": "clean" if not findings else "checked_only",
        }
    return result
