from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.design.services.sequence_diagram.extractor import SequenceMessage


def test_sequence_traceability_ids_are_unique_set_like_references():
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        SequenceMessage(source="A", target="B", use_case_ids=["UC1", "UC1"])


def test_sequence_traceability_schema_stays_within_endpoint_grammar_subset():
    schema = SequenceMessage.model_json_schema()

    assert "uniqueItems" not in schema["properties"]["use_case_ids"]
