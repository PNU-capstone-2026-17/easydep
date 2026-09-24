"""Accepted class-model parameter and DataType-field identity sidecars."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.identity import reconcile_stable_ids
from tests.class_design_fixtures import typed_class_model_payload


def _accepted(payload: dict) -> BCEModel:
    return reconcile_stable_ids(None, BCEModel.model_validate(payload))[0]


def _two_slot_payload() -> dict:
    payload = typed_class_model_payload()
    operation = payload["Classes"][0]["operations"][0]
    operation["parameters"].append({"name": "note", "type": "String"})
    payload["Collaborations"][0]["calls"][0]["receiverOperationId"] = (
        "OrderBoundary::submit(request:OrderRequest,note:String)"
    )
    payload["DataTypes"][0]["fields"].append("quantity : Integer")
    return payload


def test_acceptance_issues_parameter_and_aligned_data_type_field_refs():
    accepted = _accepted(_two_slot_payload())
    operation = accepted.Classes[0].operations[0]
    data_type = accepted.DataTypes[0]

    assert all(parameter.stable_ref for parameter in operation.parameters)
    assert len({parameter.stable_ref for parameter in operation.parameters}) == 2
    assert data_type.stable_id
    assert len(data_type.field_refs) == len(data_type.fields) == 2
    assert len(set(data_type.field_refs)) == 2
    assert accepted.Collaborations[0].calls[0].argument_bindings[0].source_ref == "UC1:main:1#request"


def test_single_display_rename_preserves_parent_positional_refs():
    previous = _accepted(_two_slot_payload())
    revised_payload = _two_slot_payload()
    revised_payload["Classes"][0]["operations"][0]["parameters"][0]["name"] = "input"
    revised_payload["Collaborations"][0]["calls"][0]["receiverOperationId"] = (
        "OrderBoundary::submit(input:OrderRequest,note:String)"
    )
    revised_payload["DataTypes"][0]["fields"][0] = "productCode : String"

    revised, _metadata = reconcile_stable_ids(previous, BCEModel.model_validate(revised_payload))
    assert revised.Classes[0].operations[0].parameters[0].stable_ref == (
        previous.Classes[0].operations[0].parameters[0].stable_ref
    )
    assert revised.DataTypes[0].stable_id == previous.DataTypes[0].stable_id
    assert revised.DataTypes[0].field_refs[0] == previous.DataTypes[0].field_refs[0]


def test_duplicate_parameter_or_field_refs_are_rejected():
    payload = _accepted(_two_slot_payload()).model_dump(mode="json", by_alias=True)
    operation = payload["Classes"][0]["operations"][0]
    operation["parameters"][1]["stableRef"] = operation["parameters"][0]["stableRef"]
    with pytest.raises(ValueError, match="parameter stableRef values must be unique"):
        BCEModel.model_validate(payload)

    payload = _accepted(_two_slot_payload()).model_dump(mode="json", by_alias=True)
    fields = payload["DataTypes"][0]["fieldRefs"]
    fields[1] = fields[0]
    with pytest.raises(ValueError, match="DataType fieldRefs must be unique"):
        BCEModel.model_validate(payload)


def test_reordered_slots_are_ambiguous_and_receive_fresh_refs():
    previous = _accepted(_two_slot_payload())
    revised_payload = _two_slot_payload()
    operation = revised_payload["Classes"][0]["operations"][0]
    operation["parameters"] = list(reversed(operation["parameters"]))
    revised_payload["Collaborations"][0]["calls"][0]["receiverOperationId"] = (
        "OrderBoundary::submit(note:String,request:OrderRequest)"
    )
    revised_payload["DataTypes"][0]["fields"] = list(reversed(
        revised_payload["DataTypes"][0]["fields"]
    ))

    revised, metadata = reconcile_stable_ids(previous, BCEModel.model_validate(revised_payload))
    assert {
        parameter.stable_ref for parameter in revised.Classes[0].operations[0].parameters
    }.isdisjoint({
        parameter.stable_ref for parameter in previous.Classes[0].operations[0].parameters
    })
    assert set(revised.DataTypes[0].field_refs).isdisjoint(set(previous.DataTypes[0].field_refs))
    assert metadata["ambiguousParameters"]
    assert metadata["ambiguousFields"]
