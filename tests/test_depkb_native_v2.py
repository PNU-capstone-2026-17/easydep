from __future__ import annotations

import copy

import pytest

from app.core.cloudkb.depkb.native_v2 import (
    boundary_sample,
    freeze,
    make_review,
    review_scope,
    validate_frozen,
    validate_inventory,
)


def _inventory(count: int = 6) -> dict:
    return {
        "schemaVersion": "easydep-native-observations/v2",
        "provider": "aws",
        "source": {"identity": "botocore", "version": "1", "sha256": "a" * 64},
        "decisionAnchors": [{
            "capabilityId": "compute-runtime",
            "sourceLocator": "ec2#RunInstances",
            "requirementIds": ["P1-aws:R5"],
            "evidenceSpans": ["Docker"],
        }],
        "observations": [{
            "provider": "aws",
            "nativeId": f"ec2.Shape{i}",
            "sourceIdentity": "botocore",
            "sourceVersion": "1",
            "sourceLocator": f"ec2#/shapes/{i}",
            "serviceFamily": "ec2",
            "observationChannel": "schema",
        } for i in range(count)],
    }


def _complete(review: dict, *, included: bool = True) -> dict:
    for item in review["decisions"]:
        item.update(
            included=included,
            derivedTypes=["inductively-derived-type"] if included else [],
            reason="Pinned API evidence supports this decision.",
        )
    return review


def test_raw_v2_inventory_forbids_predefined_resource_form():
    inventory = _inventory()
    validate_inventory(inventory)
    inventory["observations"][0]["nativeForm"] = "standaloneResource"
    with pytest.raises(ValueError, match="premature classification"):
        validate_inventory(inventory)


def test_boundary_sampling_is_stratified_reproducible_and_bounded():
    inventory = _inventory(40)
    first = boundary_sample(inventory["observations"])
    second = boundary_sample(inventory["observations"])
    assert first == second
    assert len(first["selectedNativeIds"]) == 8


def test_review_scope_unions_anchor_traversal_and_boundary_sample():
    inventory = _inventory(6)
    inventory["observations"][0]["anchorCapabilityIds"] = ["compute-runtime"]
    sample = {"selectedNativeIds": [inventory["observations"][1]["nativeId"]]}

    scope = review_scope(inventory, sample)
    review = make_review(inventory, "reviewer", native_ids=scope["selectedNativeIds"])

    assert scope["selectedCount"] == 2
    assert len(review["decisions"]) == 2


def test_freeze_requires_reliable_two_reviewer_completion():
    inventory = _inventory()
    first = _complete(make_review(inventory, "reviewer-a"))
    second = copy.deepcopy(_complete(make_review(inventory, "reviewer-b")))

    model = freeze(inventory, first, second, {})

    assert model["schemaVersion"] == "easydep-native-model/v2"
    assert model["reliability"]["cohenKappaInclusion"] == 1
    assert len(model["freeze"]["sha256"]) == 64
    validate_frozen(model)

    model["decisions"][0]["included"] = False
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_frozen(model)


def test_low_reliability_blocks_freeze_even_with_adjudication():
    inventory = _inventory()
    first = _complete(make_review(inventory, "reviewer-a"))
    second = _complete(make_review(inventory, "reviewer-b"), included=False)
    adjudications = {
        item["nativeId"]: {
            "included": True,
            "derivedTypes": ["type"],
            "reason": "Adjudicated from API evidence.",
        }
        for item in inventory["observations"]
    }
    with pytest.raises(ValueError, match="reliability"):
        freeze(inventory, first, second, adjudications)


def test_freeze_rejects_review_that_differs_from_preregistered_scope():
    inventory = _inventory()
    first = _complete(make_review(inventory, "reviewer-a"))
    second = _complete(make_review(inventory, "reviewer-b"))

    with pytest.raises(ValueError, match="preregistered review scope"):
        freeze(inventory, first, second, {}, expected_native_ids=["ec2.Shape0"])
