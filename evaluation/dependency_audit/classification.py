"""Validate multi-label evidence-card classifications without forcing one root cause."""
from __future__ import annotations

from typing import Any

LABELS = frozenset({
    "confirmed-model-error",
    "confirmed-oracle-error",
    "confirmed-analyzer-error",
    "invalid-iac",
    "acceptable-alternative",
    "insufficient-evidence",
})


def validate_card(card: dict[str, Any]) -> None:
    if card.get("schemaVersion") != "easydep-dependency-audit-card/v2":
        raise ValueError("unsupported dependency audit card")
    labels = card.get("classifications")
    if not isinstance(labels, list) or not labels:
        raise ValueError("audit card requires one or more classifications")
    unknown = set(labels) - LABELS
    if unknown:
        raise ValueError("unknown audit classifications: " + ", ".join(sorted(unknown)))
    if len(labels) != len(set(labels)):
        raise ValueError("duplicate audit classifications")
    evidence = card.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("audit card requires evidence")
    if any(not item.get("source") or not item.get("finding") for item in evidence):
        raise ValueError("every evidence item requires source and finding")
    reviewers = card.get("reviewers") or {}
    if not reviewers.get("reviewerA") or not reviewers.get("reviewerB"):
        raise ValueError("audit card requires two reviewer decisions")
    if reviewers.get("reviewerA") != reviewers.get("reviewerB") and not reviewers.get("adjudication"):
        raise ValueError("reviewer disagreement requires adjudication")
