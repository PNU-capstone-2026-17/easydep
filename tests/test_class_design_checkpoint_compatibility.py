"""Checkpoint source/derived artifact compatibility for class design."""
from __future__ import annotations

import json

from app.db.models import FORMAT_JSON
from app.design.schemas.architecture_state import ArchitectureState
from app.design.schemas.class_model import BCEModel
from app.design.services.sequence_diagram.projection import (
    SequenceCollection,
    normalize_sequence_model,
)
from app.repositories.artifact_repository import STAGE_ARTIFACTS
from tests.class_design_fixtures import (
    typed_class_model_payload,
    typed_sequence_model_payload,
)


def test_class_and_sequence_checkpoints_store_typed_sources_and_derive_renderings():
    class_model = BCEModel.model_validate(typed_class_model_payload())
    sequence_model = SequenceCollection.model_validate(typed_sequence_model_payload())
    class_source = class_model.model_dump(mode="json", by_alias=True)
    sequence_source = sequence_model.model_dump(mode="json")

    class_config = STAGE_ARTIFACTS["class_diagram"]
    sequence_config = STAGE_ARTIFACTS["sequence_diagram"]
    assert class_config["source_format"] == sequence_config["source_format"] == FORMAT_JSON
    assert class_config["source_key"] == "extracted_bce_classes"
    assert sequence_config["source_key"] == "sequence_diagram_model"

    class_puml = class_config["derive"](class_source)
    sequence_puml = sequence_config["derive"](sequence_source)
    assert "class OrderBoundary" in class_puml
    assert "submit(request)" in sequence_puml


def test_json_checkpoint_round_trip_revalidates_sequence_source_before_rendering():
    source = SequenceCollection.model_validate(
        typed_sequence_model_payload()
    ).model_dump(mode="json")
    checkpoint_payload = json.loads(json.dumps(source, ensure_ascii=False))

    normalized = normalize_sequence_model(checkpoint_payload)
    restored = SequenceCollection.model_validate(normalized)

    assert restored.model_dump(mode="json") == source
    assert STAGE_ARTIFACTS["sequence_diagram"]["derive"](normalized).startswith(
        "@startuml"
    )


def test_architecture_state_keeps_structured_sources_as_checkpoint_values():
    class_source = BCEModel.model_validate(
        typed_class_model_payload()
    ).model_dump(mode="json", by_alias=True)
    sequence_source = SequenceCollection.model_validate(
        typed_sequence_model_payload()
    ).model_dump(mode="json")
    state: ArchitectureState = {
        "extracted_bce_classes": class_source,
        "sequence_diagram_model": sequence_source,
    }

    assert state["extracted_bce_classes"]["Collaborations"][0]["calls"]
    assert state["sequence_diagram_model"]["Diagrams"][0]["Messages"]
