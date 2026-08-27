from __future__ import annotations

import copy

import pytest

from app.cloudkb.depkb.native.consensus import reconcile_reviews
from app.cloudkb.depkb.native.freeze import freeze_native_graph
from app.cloudkb.depkb.native.review import make_review_packet


def _inventory() -> dict:
    return {
        "schemaVersion": "easydep-native-discovery/v1",
        "provider": "aws",
        "source": {"identity": "test", "version": "1"},
        "elements": [
            {
                "nativeId": "aws.native.a",
                "nativeForm": "standaloneResource",
                "sourceLocator": "source#/a",
            }
        ],
        "candidates": [],
    }


def _completed_review(inventory: dict) -> dict:
    packet = make_review_packet(inventory)
    packet["decisions"][0].update(
        status="included",
        criterion="provisioningOutcome",
        reason="Native schema exposes a separately provisioned resource.",
    )
    return packet


def test_independent_agreement_can_be_frozen_with_audit_provenance():
    inventory = _inventory()
    first = _completed_review(inventory)
    second = copy.deepcopy(first)
    second["decisions"][0]["reason"] = "Provisioning changes independently."

    consensus = reconcile_reviews(
        inventory, first, second, first_reviewer="reviewer-a", second_reviewer="reviewer-b"
    )

    assert consensus["consensus"]["humanReviewRequired"] is False
    assert consensus["consensus"]["reliability"] == {
        "percentAgreement": 1.0,
        "cohenKappaInclusion": 1.0,
        "krippendorffAlphaType": 1.0,
    }
    assert consensus["decisions"][0]["independentAgreement"] is True
    freeze_native_graph(inventory, consensus)


def test_disagreement_is_explicit_and_blocks_freeze():
    inventory = _inventory()
    first = _completed_review(inventory)
    second = copy.deepcopy(first)
    second["decisions"][0].update(
        status="excluded",
        criterion="outsideStudyBoundary",
        reason="Not connected to VM deployment.",
    )

    consensus = reconcile_reviews(
        inventory, first, second, first_reviewer="reviewer-a", second_reviewer="reviewer-b"
    )

    assert consensus["consensus"]["humanReviewRequired"] is True
    assert consensus["consensus"]["conflicts"][0]["humanReviewRequired"] is True
    with pytest.raises(ValueError, match="not complete"):
        freeze_native_graph(inventory, consensus)


def test_same_reviewer_cannot_supply_both_independent_reviews():
    inventory = _inventory()
    review = _completed_review(inventory)
    with pytest.raises(ValueError, match="distinct reviewer"):
        reconcile_reviews(
            inventory, review, copy.deepcopy(review),
            first_reviewer="same", second_reviewer="same"
        )
