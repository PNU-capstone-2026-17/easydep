"""Stable IDs are issued by accepted class-model boundaries."""
from __future__ import annotations

from copy import deepcopy

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.identity import reconcile_stable_ids
from tests.class_design_fixtures import typed_class_model_payload


def _accepted(payload: dict) -> BCEModel:
    return reconcile_stable_ids(None, BCEModel.model_validate(payload))[0]


def test_legacy_payload_hydrates_and_next_acceptance_serializes_stable_ids():
    legacy = BCEModel.model_validate(typed_class_model_payload())
    accepted, _metadata = reconcile_stable_ids(None, legacy)
    payload = accepted.model_dump(mode="json", by_alias=True)

    assert payload["Classes"][0]["operations"][0]["stableId"]
    assert payload["Collaborations"][0]["calls"][0]["stableId"]
    assert BCEModel.model_validate(payload) == accepted


def test_operation_rename_and_parameter_change_use_unique_step_provenance():
    previous = _accepted(typed_class_model_payload())
    revised_payload = deepcopy(typed_class_model_payload())
    operation = revised_payload["Classes"][0]["operations"][0]
    operation["name"] = "send"
    operation["parameters"][0]["type"] = "UUID"
    revised_payload["Collaborations"][0]["calls"][0]["receiverOperationId"] = (
        "OrderBoundary::send(request:UUID)"
    )
    revised = BCEModel.model_validate(revised_payload)

    reconciled, _metadata = reconcile_stable_ids(previous, revised)
    assert reconciled.Classes[0].operations[0].stable_id == (
        previous.Classes[0].operations[0].stable_id
    )


def test_targeted_operation_rename_without_step_refs_preserves_identity():
    previous_payload = typed_class_model_payload()
    previous_payload["Classes"][0]["operations"][0]["stepRefs"] = []
    previous = _accepted(previous_payload)
    old_operation = previous.Classes[0].operations[0]
    revised_payload = deepcopy(previous_payload)
    revised_payload["Classes"][0]["operations"][0]["name"] = "send"

    reconciled, _metadata = reconcile_stable_ids(
        previous,
        BCEModel.model_validate(revised_payload),
        targeted_refs=[f"operation:{old_operation.operation_id}"],
    )

    assert reconciled.Classes[0].operations[0].stable_id == old_operation.stable_id


def test_call_insertion_does_not_renumber_existing_stable_ids():
    previous = _accepted(typed_class_model_payload())
    revised_payload = deepcopy(typed_class_model_payload())
    calls = revised_payload["Collaborations"][0]["calls"]
    inserted = deepcopy(calls[0])
    inserted["callId"] = "new-call"
    inserted["stepRefs"] = ["UC1:main:new"]
    calls.insert(0, inserted)
    calls[2]["parentCallId"] = "place-order::call:2"
    reconciled, _metadata = reconcile_stable_ids(
        previous, BCEModel.model_validate(revised_payload),
    )

    prior_by_steps = {
        tuple(call.step_refs): call.stable_id
        for call in previous.Collaborations[0].calls
    }
    revised_by_steps = {
        tuple(call.step_refs): call.stable_id
        for call in reconciled.Collaborations[0].calls
    }
    assert revised_by_steps[("UC1:main:1",)] == prior_by_steps[("UC1:main:1",)]
    assert revised_by_steps[("UC1:main:2",)] == prior_by_steps[("UC1:main:2",)]
    revised_ids = list(revised_by_steps.values())
    assert len(revised_ids) == len(set(revised_ids))


def test_ambiguous_duplicate_call_does_not_steal_the_previous_identity():
    previous = _accepted(typed_class_model_payload())
    revised_payload = deepcopy(typed_class_model_payload())
    calls = revised_payload["Collaborations"][0]["calls"]
    calls.insert(0, deepcopy(calls[0]))

    reconciled, _metadata = reconcile_stable_ids(
        previous, BCEModel.model_validate(revised_payload),
    )

    duplicate_ids = [
        call.stable_id
        for call in reconciled.Collaborations[0].calls
        if call.step_refs == ["UC1:main:1"]
    ]
    assert previous.Collaborations[0].calls[0].stable_id not in duplicate_ids


def test_targeted_call_change_preserves_identity_with_changed_provenance():
    previous = _accepted(typed_class_model_payload())
    old_call = previous.Collaborations[0].calls[0]
    revised_payload = typed_class_model_payload()
    revised_payload["Collaborations"][0]["calls"][0]["stepRefs"] = [
        "UC1:alternate:1"
    ]

    reconciled, _metadata = reconcile_stable_ids(
        previous,
        BCEModel.model_validate(revised_payload),
        targeted_refs=[f"call:{old_call.call_id}"],
    )

    assert reconciled.Collaborations[0].calls[0].stable_id == old_call.stable_id


def test_noop_reconciliation_preserves_ambiguous_calls_by_existing_stable_id():
    payload = typed_class_model_payload()
    payload["Collaborations"][0]["calls"].insert(
        0,
        deepcopy(payload["Collaborations"][0]["calls"][0]),
    )
    previous = _accepted(payload)
    prior_ids = [call.stable_id for call in previous.Collaborations[0].calls]

    reconciled, _metadata = reconcile_stable_ids(
        previous,
        previous.model_copy(deep=True),
    )

    assert [call.stable_id for call in reconciled.Collaborations[0].calls] == prior_ids


def test_new_operations_and_calls_get_distinct_deterministic_ids():
    first = _accepted(typed_class_model_payload())
    second = _accepted(typed_class_model_payload())
    operation_ids = [operation.stable_id for item in first.Classes for operation in item.operations]
    call_ids = [call.stable_id for item in first.Collaborations for call in item.calls]

    assert len(operation_ids) == len(set(operation_ids))
    assert len(call_ids) == len(set(call_ids))
    assert first == second


def test_legacy_snapshot_and_initial_acceptance_share_the_same_fallback_ids():
    payload = typed_class_model_payload()
    legacy = BCEModel.model_validate(payload)
    accepted = _accepted(payload)

    reconciled, _metadata = reconcile_stable_ids(
        legacy,
        BCEModel.model_validate(payload),
    )

    assert [
        operation.stable_id
        for item in reconciled.Classes
        for operation in item.operations
    ] == [
        operation.stable_id
        for item in accepted.Classes
        for operation in item.operations
    ]
    assert [
        call.stable_id
        for item in reconciled.Collaborations
        for call in item.calls
    ] == [
        call.stable_id
        for item in accepted.Collaborations
        for call in item.calls
    ]


def test_duplicate_persisted_stable_ids_are_rejected():
    payload = _accepted(typed_class_model_payload()).model_dump(mode="json", by_alias=True)
    operations = [
        operation
        for item in payload["Classes"]
        for operation in item["operations"]
    ]
    operations[1]["stableId"] = operations[0]["stableId"]

    try:
        BCEModel.model_validate(payload)
    except ValueError as error:
        assert "operation stableId values must be unique" in str(error)
    else:  # pragma: no cover - documents the acceptance invariant
        raise AssertionError("duplicate stable IDs were accepted")
