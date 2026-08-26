"""Read-only sequence/class contract checks.

Class operations and collaborations are accepted upstream as one contract. This
module deliberately has no sequence-to-class reconciliation, proposal, or
method-repair path: a downstream sequence consumer can only reject an invalid
or stale input, never alter the class artifact to make a diagram render.
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.design.knowledge.detectors import sequence_diagram_findings
from app.design.schemas.architecture_state import ArchitectureState
from app.design.services.sequence_diagram.methods import method_call_signature


_CALL_TYPES = {"sync", "async", "self"}


def _sequence_diagrams(sequence: dict[str, Any]) -> list[dict[str, Any]]:
    diagrams = sequence.get("Diagrams") if isinstance(sequence, dict) else None
    if isinstance(diagrams, list):
        return [diagram for diagram in diagrams if isinstance(diagram, dict)]
    return [sequence] if isinstance(sequence, dict) else []


def _operation_signatures(bce: dict[str, Any]) -> dict[str, set[str]]:
    """Return current typed operation signatures, with legacy read fallback."""

    by_class: dict[str, set[str]] = {}
    for class_item in bce.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        class_name = str(class_item.get("className") or "").strip()
        if not class_name:
            continue
        signatures: set[str] = set()
        operations = class_item.get("operations")
        if isinstance(operations, list):
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                name = str(operation.get("name") or "").strip()
                parameters = operation.get("parameters") or []
                rendered = [
                    f"{str(parameter.get('name') or '').strip()}:"
                    f"{str(parameter.get('type') or '').strip()}"
                    for parameter in parameters
                    if isinstance(parameter, dict)
                    and str(parameter.get("name") or "").strip()
                    and str(parameter.get("type") or "").strip()
                ]
                if name and len(rendered) == len(parameters):
                    signatures.add(f"{name}({','.join(rendered)})")
        else:
            signatures.update(
                signature
                for method in class_item.get("methods") or []
                if (signature := method_call_signature(str(method)))
            )
        by_class[class_name] = signatures
    return by_class


def _participant_classes(diagram: dict[str, Any]) -> dict[str, str]:
    return {
        str(participant.get("alias") or participant.get("name") or "").strip(): str(
            participant.get("source_class") or participant.get("name") or ""
        ).strip()
        for participant in diagram.get("Participants") or []
        if isinstance(participant, dict)
        and str(participant.get("kind") or "").casefold() != "actor"
    }


def _is_current_but_stale(bce: dict[str, Any]) -> bool:
    """An empty persisted collaboration list blocks regeneration.

    Models without the field predate this contract. They remain readable for
    artifact history, while current models explicitly carrying an empty list
    are reported stale rather than having calls guessed from signatures.
    """

    return isinstance(bce, dict) and "Collaborations" in bce and not bce.get("Collaborations")


def reconcile_class_methods(state: ArchitectureState) -> dict[str, Any]:
    """Compatibility no-op for the removed reverse-repair workflow."""

    return {}


def ensure_sequence_class_methods(state: ArchitectureState) -> dict[str, Any]:
    """Assert that a sequence only invokes accepted receiver operations."""

    sequence = state.get("sequence_diagram_model") or {}
    bce = state.get("extracted_bce_classes") or {}
    if _is_current_but_stale(bce):
        raise ValueError(
            "class model is stale: Collaborations are required for sequence regeneration"
        )

    expected_hash = str(sequence.get("class_diagram_hash") or "").strip()
    if expected_hash:
        actual_hash = hashlib.sha256(
            str(state.get("class_diagram_puml") or "").encode("utf-8")
        ).hexdigest()
        if expected_hash != actual_hash:
            raise ValueError("sequence diagram was generated from a different class diagram version")

    signatures = _operation_signatures(bce)
    for diagram in _sequence_diagrams(sequence):
        participants = _participant_classes(diagram)
        for message in diagram.get("Messages") or []:
            if not isinstance(message, dict) or str(message.get("type") or "").casefold() not in _CALL_TYPES:
                continue
            target = str(message.get("target") or "").strip()
            class_name = participants.get(target)
            signature = method_call_signature(str(message.get("label") or ""))
            if not class_name or not signature or signature not in signatures.get(class_name, set()):
                raise ValueError(
                    "call messages must exactly match receiver class operations: "
                    f"{target}: {message.get('label') or '<empty>'}"
                )

    findings = sequence_diagram_findings(sequence, state)
    if findings:
        raise ValueError(
            "sequence interaction contracts remain invalid: "
            + "; ".join(finding.message for finding in findings)
        )
    return {}


def finalize_sequence_class_methods(state: ArchitectureState) -> dict[str, Any]:
    """Turn the read-only assertion into the sequence renderer gate."""

    report = dict(state.get("sequence_diagram_check") or {})
    findings = list(report.get("findings") or [])
    try:
        ensure_sequence_class_methods(state)
    except ValueError as exc:
        if not findings:
            findings.append(f"{exc} [sequence.final-contract]")
            report.update({
                "findings": findings,
                "repair_iters": int(report.get("repair_iters") or 0),
                "stopped": report.get("stopped") or "checked_only",
            })
        return {"sequence_diagram_renderable": False, "sequence_diagram_check": report}
    return {
        "sequence_diagram_renderable": not findings,
        "sequence_diagram_check": report,
    }
