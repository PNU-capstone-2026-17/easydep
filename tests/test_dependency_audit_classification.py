from __future__ import annotations

import pytest

from evaluation.dependency_audit.classification import validate_card


def _card() -> dict:
    return {
        "schemaVersion": "easydep-dependency-audit-card/v2",
        "cellId": "cell",
        "classifications": ["invalid-iac", "confirmed-oracle-error"],
        "evidence": [{"source": "provider schema", "finding": "argument is invalid"}],
        "reviewers": {
            "reviewerA": ["invalid-iac"],
            "reviewerB": ["confirmed-oracle-error"],
            "adjudication": "both defects are independently present",
        },
    }


def test_audit_cards_allow_multiple_independent_mismatch_causes():
    validate_card(_card())


def test_audit_disagreement_requires_adjudication():
    card = _card()
    card["reviewers"]["adjudication"] = None
    with pytest.raises(ValueError, match="adjudication"):
        validate_card(card)
