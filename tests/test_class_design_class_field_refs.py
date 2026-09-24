"""Accepted BCE class field identity sidecars."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.identity import (
    materialize_pre_collaboration_refs,
    reconcile_stable_ids,
)
from tests.class_design_fixtures import typed_class_model_payload


def _payload_with_fields() -> dict:
    payload = typed_class_model_payload()
    payload["Classes"][0]["fields"] = ["request : OrderRequest", "status : String"]
    return payload


def _accepted(payload: dict) -> BCEModel:
    return reconcile_stable_ids(None, BCEModel.model_validate(payload))[0]


def test_acceptance_issues_class_and_aligned_field_refs_before_collaboration():
    payload = _payload_with_fields()
    payload["Collaborations"] = []

    accepted = materialize_pre_collaboration_refs(None, BCEModel.model_validate(payload))
    item = accepted.Classes[0]

    assert item.stable_id
    assert len(item.field_refs) == len(item.fields) == 2
    assert len(set(item.field_refs)) == 2


def test_one_display_rename_preserves_class_field_ref_at_same_position():
    previous = _accepted(_payload_with_fields())
    revised_payload = _payload_with_fields()
    revised_payload["Classes"][0]["fields"][0] = "input : OrderRequest"

    revised, _metadata = reconcile_stable_ids(previous, BCEModel.model_validate(revised_payload))

    assert revised.Classes[0].stable_id == previous.Classes[0].stable_id
    assert revised.Classes[0].field_refs[0] == previous.Classes[0].field_refs[0]


def test_reordered_class_fields_are_ambiguous_and_receive_fresh_refs():
    previous = _accepted(_payload_with_fields())
    revised_payload = _payload_with_fields()
    revised_payload["Classes"][0]["fields"].reverse()

    revised, metadata = reconcile_stable_ids(previous, BCEModel.model_validate(revised_payload))

    assert set(revised.Classes[0].field_refs).isdisjoint(previous.Classes[0].field_refs)
    assert metadata["ambiguousClassFields"] == ["OrderBoundary#1", "OrderBoundary#2"]


def test_duplicate_or_misaligned_class_field_refs_are_rejected():
    payload = _accepted(_payload_with_fields()).model_dump(mode="json", by_alias=True)
    payload["Classes"][0]["fieldRefs"][1] = payload["Classes"][0]["fieldRefs"][0]
    with pytest.raises(ValueError, match="Class fieldRefs must be unique"):
        BCEModel.model_validate(payload)

    payload = deepcopy(_payload_with_fields())
    payload["Classes"][0]["fieldRefs"] = ["classfield_one"]
    with pytest.raises(ValueError, match="fieldRefs must align with Class fields"):
        BCEModel.model_validate(payload)
