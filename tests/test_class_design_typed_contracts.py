"""Typed class-design boundaries and JSON contract regressions."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    build_scenario_index,
)
from app.design.services.sequence_diagram.projection import (
    SequenceCollection,
    SequenceMessage,
)
from tests.class_design_fixtures import (
    single_use_case,
    typed_class_model_payload,
    typed_sequence_model_payload,
)


def test_scenario_index_is_the_immutable_typed_boundary_for_raw_specs():
    raw = single_use_case()
    index = build_scenario_index(raw)

    assert isinstance(index, ScenarioIndex)
    assert isinstance(index.use_cases[0], UseCase)
    assert isinstance(index.groups[0], ExecutionGroup)
    assert isinstance(index.use_cases, tuple)
    assert isinstance(index.groups, tuple)
    assert index.step_ids == frozenset({"UC1:main:1", "UC1:main:2"})
    assert raw["use_case_specs"][0]["main_scenario"][0]["step_number"] == 1


def test_scenario_index_rejects_duplicate_structured_use_case_specs():
    raw = single_use_case()
    raw["use_case_specs"].append(raw["use_case_specs"][0])

    with pytest.raises(ValueError, match="duplicate use-case specification"):
        build_scenario_index(raw)


def test_bce_model_canonicalizes_generated_ids_and_round_trips_json():
    model = BCEModel.model_validate(typed_class_model_payload())
    encoded = model.model_dump(mode="json", by_alias=True)
    decoded = json.loads(json.dumps(encoded, ensure_ascii=False))
    restored = BCEModel.model_validate(decoded)

    assert model == restored
    assert encoded["Classes"][0]["operations"][0]["operationId"] == (
        "OrderBoundary::submit(request:OrderRequest)"
    )
    assert encoded["Collaborations"][0]["calls"][0]["callId"] == (
        "place-order::call:1"
    )
    assert encoded["Collaborations"][0]["calls"][1]["parentCallId"] == (
        "place-order::call:1"
    )


def test_bce_model_rejects_unknown_nested_contract_fields():
    payload = typed_class_model_payload()
    payload["Classes"][0]["operations"][0]["unexpected"] = True

    with pytest.raises(ValidationError):
        BCEModel.model_validate(payload)


def test_bce_model_rejects_class_and_data_type_name_collisions():
    payload = typed_class_model_payload()
    payload["DataTypes"].append({
        "name": "OrderBoundary",
        "kind": "valueObject",
        "fields": ["value : String"],
    })

    with pytest.raises(ValidationError, match="must not overlap"):
        BCEModel.model_validate(payload)


def test_sequence_collection_is_strict_and_json_round_trippable():
    collection = SequenceCollection.model_validate(typed_sequence_model_payload())
    encoded = collection.model_dump(mode="json")
    restored = SequenceCollection.model_validate(
        json.loads(json.dumps(encoded, ensure_ascii=False))
    )

    assert collection == restored
    assert encoded["Diagrams"][0]["Messages"][0]["call_id"] == (
        "place-order::call:1"
    )


def test_sequence_message_requires_a_complete_call_or_matching_return():
    with pytest.raises(ValidationError, match="complete method signature"):
        SequenceMessage(
            source="Buyer",
            target="OrderBoundary",
            label="submit",
            type="sync",
            use_case_ids=["UC1"],
            call_id="place-order::call:1",
        )

    with pytest.raises(ValidationError, match="return requires reply_to"):
        SequenceMessage(
            source="OrderBoundary",
            target="Buyer",
            label="Receipt",
            type="return",
            use_case_ids=["UC1"],
        )
