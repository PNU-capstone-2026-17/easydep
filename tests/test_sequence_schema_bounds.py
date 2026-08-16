from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.design.services.sequence_diagram.extractor import (
    SequenceDiagramCollection,
    SequenceMessage,
    SequenceModel,
)


def _link_fields(message_type: str) -> dict:
    if message_type == "return":
        return {"call_id": "", "reply_to": "call-1", "arguments": []}
    return {"call_id": "call-1", "reply_to": "", "arguments": []}


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
            **_link_fields("sync"),
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
            **_link_fields("self"),
        )


@pytest.mark.parametrize(
    "label", ["1", "2.", "2a", "create order", "createOrder", "Order.create()"]
)
def test_call_message_label_must_be_a_complete_method_call(label: str):
    with pytest.raises(ValidationError, match="must be complete method calls"):
        SequenceMessage(
            source="A",
            target="B",
            label=label,
            type="sync",
            fragments=[],
            use_case_ids=["UC1"],
            step_ids=["UC1:main:1"],
            **_link_fields("sync"),
        )


@pytest.mark.parametrize("label", ["createOrder()", "save_order(order: Order)"])
def test_call_message_label_accepts_complete_method_calls(label: str):
    message = SequenceMessage(
        source="A",
        target="B",
        label=label,
        type="sync",
        fragments=[],
        use_case_ids=["UC1"],
        step_ids=["UC1:main:1"],
        **_link_fields("sync"),
    )

    assert message.label == label


def test_return_message_requires_a_result_label():
    with pytest.raises(ValidationError, match="require a result label"):
        SequenceMessage(
            source="B",
            target="A",
            label="",
            type="return",
            fragments=[],
            use_case_ids=["UC1"],
            step_ids=["UC1:main:1"],
            **_link_fields("return"),
        )


def test_return_message_accepts_a_non_empty_result_label():
    message = SequenceMessage(
        source="B",
        target="A",
        label="Order",
        type="return",
        fragments=[],
        use_case_ids=["UC1"],
        step_ids=["UC1:main:1"],
        **_link_fields("return"),
    )

    assert message.label == "Order"


def test_call_and_return_require_explicit_link_fields():
    with pytest.raises(ValidationError, match="require call_id"):
        SequenceMessage(
            source="A",
            target="B",
            label="createOrder()",
            type="sync",
            fragments=[],
            use_case_ids=["UC1"],
            step_ids=["UC1:main:1"],
            call_id="",
            reply_to="",
            arguments=[],
        )
    with pytest.raises(ValidationError, match="require reply_to"):
        SequenceMessage(
            source="B",
            target="A",
            label="Order",
            type="return",
            fragments=[],
            use_case_ids=["UC1"],
            step_ids=["UC1:main:1"],
            call_id="",
            reply_to="",
            arguments=[],
        )


def test_fragment_path_rejects_duplicate_fragment_ids():
    fragment = {"id": "choice", "type": "opt", "branch": "main", "condition": "needed"}
    with pytest.raises(ValidationError, match="fragment ids must be unique"):
        SequenceMessage(
            source="A",
            target="B",
            label="createOrder()",
            type="sync",
            fragments=[fragment, fragment],
            use_case_ids=["UC1"],
            step_ids=["UC1:main:1"],
            **_link_fields("sync"),
        )


@pytest.mark.parametrize("label", ["1", "order creation result", "Order result"])
def test_return_message_rejects_non_type_result_labels(label: str):
    with pytest.raises(ValidationError, match="must be return type identifiers"):
        SequenceMessage(
            source="B",
            target="A",
            label=label,
            type="return",
            fragments=[],
            use_case_ids=["UC1"],
            step_ids=["UC1:main:1"],
            **_link_fields("return"),
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


def test_use_case_sequence_rejects_messages_from_another_use_case():
    with pytest.raises(ValidationError, match="reference only its use_case_id"):
        SequenceDiagramCollection(
            Diagrams=[
                {
                    "use_case_id": "UC1",
                    "use_case_name": "Create order",
                    "Participants": [],
                    "Messages": [
                        {
                            "source": "A",
                            "target": "B",
                            "label": "createOrder()",
                            "type": "sync",
                            "fragments": [],
                            "use_case_ids": ["UC2"],
                            "step_ids": ["UC2:main:1"],
                            "call_id": "call-1",
                            "reply_to": "",
                            "arguments": [],
                        }
                    ],
                }
            ]
        )


def test_sequence_collection_rejects_duplicate_use_case_diagrams():
    diagram = {
        "use_case_id": "UC1",
        "use_case_name": "Create order",
        "Participants": [],
        "Messages": [],
    }
    with pytest.raises(ValidationError, match="use_case_ids must be unique"):
        SequenceDiagramCollection(Diagrams=[diagram, diagram])
