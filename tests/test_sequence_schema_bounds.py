from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.design.services.sequence_diagram.extractor import SequenceMessage, SequenceModel


def test_sequence_traceability_ids_are_unique_set_like_references():
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        SequenceMessage(
            source="A",
            target="B",
            label="call()",
            type="sync",
            fragments=[],
            use_case_ids=["UC1", "UC1"],
            step_ids=["UC1:main:1"],
        )


def test_sequence_traceability_schema_stays_within_endpoint_grammar_subset():
    schema = SequenceMessage.model_json_schema()

    assert "uniqueItems" not in schema["properties"]["use_case_ids"]


def test_sequence_schema_requires_every_field_and_forbids_extras():
    schema = SequenceMessage.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_self_message_requires_same_source_and_target():
    with pytest.raises(ValidationError, match="source == target"):
        SequenceMessage(
            source="A",
            target="B",
            label="recalculate()",
            type="self",
            fragments=[],
            use_case_ids=["UC1"],
            step_ids=["UC1:main:1"],
        )


def test_participant_aliases_must_be_unique():
    participants = [
        {
            "name": name,
            "alias": "SameAlias",
            "kind": kind,
            "description": "",
            "source_class": source_class,
        }
        for name, kind, source_class in (
            ("User", "actor", ""),
            ("OrderBoundary", "boundary", "OrderBoundary"),
        )
    ]
    with pytest.raises(ValidationError, match="aliases must be unique"):
        SequenceModel(Participants=participants, Messages=[])
