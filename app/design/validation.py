"""Design-readiness checks shared by design hand-off and implementation entry."""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from app.design.knowledge.detectors import (
    Finding,
    class_diagram_findings,
    erd_findings,
    sequence_diagram_findings,
    api_spec_findings,
)

DESIGN_READINESS_SCHEMA = "easydep-design-readiness/v1alpha1"
_EXPLICIT_JAVA_TYPES = frozenset({
    "String", "Object", "boolean", "Boolean", "byte", "Byte", "char", "Character",
    "short", "Short", "int", "Integer", "long", "Long", "float", "Float", "double",
    "Double", "void", "Void", "List", "Set", "Map", "Collection", "Iterable",
    "Optional", "Page", "UUID", "Date", "LocalDate", "LocalDateTime", "LocalTime",
    "OffsetDateTime", "Instant", "BigDecimal",
})


def _contract_type_tokens(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", str(value or ""))
        if token not in _EXPLICIT_JAVA_TYPES
    }


def _class_contract_type_findings(model: dict[str, Any]) -> list[str]:
    """Return BCE signature types that have no declaration or explicit Java type."""
    classes = [item for item in model.get("Classes", []) if isinstance(item, dict)]
    declared = {
        str(item.get("className"))
        for item in classes
        if str(item.get("className") or "").strip()
    }
    missing: set[str] = set()
    for item in classes:
        for raw in [*item.get("fields", []), *item.get("methods", [])]:
            text = str(raw or "")
            if ":" not in text:
                continue
            # Inspect every colon-separated type segment so method parameters,
            # return types, and fields are all checked.
            for segment in text.split(":")[1:]:
                missing.update(_contract_type_tokens(segment) - declared)
    return sorted(missing)


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
        if stage == "class_diagram":
            missing_types = _class_contract_type_findings(model)
            findings.extend(
                Finding(
                    "class.contract-types-exist",
                    "BCE method/field signatures reference undeclared type "
                    f"'{name}' — declare it in the class diagram",
                    name,
                )
                for name in missing_types
            )
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
        if stage == "sequence_diagram":
            model = state.get(model_key) or {}
            proposals = model.get("MethodProposals") if isinstance(model, dict) else []
            if isinstance(proposals, list) and proposals:
                check["method_proposals"] = proposals
                check["stopped"] = "needs_input"
        result[check_key] = check
    return result
