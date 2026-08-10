from __future__ import annotations

import copy

import pytest

from app.core.cloudkb.depkb.native.adjudication import (
    apply_adjudication,
    make_adjudication_template,
)
from app.core.cloudkb.depkb.native.consensus import reconcile_reviews
from app.core.cloudkb.depkb.native.freeze import freeze_native_graph
from app.core.cloudkb.depkb.native.review import make_review_packet


def _inventory() -> dict:
    return {
        "schemaVersion": "easydep-native-discovery/v1",
        "provider": "aws",
        "source": {"identity": "test", "version": "1"},
        "elements": [
            {
                "nativeId": "aws.a",
                "nativeForm": "standaloneResource",
                "sourceLocator": "test#/a",
            }
        ],
        "candidates": [],
    }


def _conflicted() -> tuple[dict, dict]:
    inventory = _inventory()
    first = make_review_packet(inventory)
    first["decisions"][0].update(
        status="included",
        criterion="provisioningOutcome",
        reason="Separately provisioned.",
    )
    second = copy.deepcopy(first)
    second["decisions"][0].update(
        status="excluded",
        criterion="outsideStudyBoundary",
        reason="Outside VM-connected scope.",
    )
    return inventory, reconcile_reviews(
        inventory,
        first,
        second,
        first_reviewer="reviewer-a",
        second_reviewer="reviewer-b",
    )


def test_human_can_select_an_evidenced_review_and_then_freeze():
    inventory, consensus = _conflicted()
    adjudication = make_adjudication_template(consensus)
    adjudication["humanIdentity"] = "human-reviewer"
    adjudication["decisions"][0].update(
        resolution="first", rationale="The pinned source establishes separate provisioning."
    )

    resolved = apply_adjudication(inventory, consensus, adjudication)

    assert resolved["consensus"]["humanReviewRequired"] is False
    assert resolved["decisions"][0]["humanAdjudicated"] is True
    freeze_native_graph(inventory, resolved)


def test_unreviewed_template_and_missing_identity_cannot_clear_conflicts():
    inventory, consensus = _conflicted()
    adjudication = make_adjudication_template(consensus)

    with pytest.raises(ValueError, match="identity"):
        apply_adjudication(inventory, consensus, adjudication)

    adjudication["humanIdentity"] = "human-reviewer"
    with pytest.raises(ValueError, match="unresolved"):
        apply_adjudication(inventory, consensus, adjudication)
