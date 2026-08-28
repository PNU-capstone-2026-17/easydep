"""ERD typed 수정 서비스와 결정론 투영의 공개 경계를 검증한다."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.design.graphs import subgraphs as design_subgraphs
from app.design.schemas.architecture_state import ArchitectureState
from app.design.schemas.class_model import BCEModel
from app.design.services.erd.mapping import build_logical_model
from app.design.services.erd.plantuml import (
    generate_erd_from_bce_json,
    render_logical_model,
)
from app.design.services.erd.projection import project_logical_model
from app.design.services.erd.relationship_mapping import map_relationship
from app.design.services.erd.service import revise_erd_model
from app.design.services.erd.table_mapping import build_entity_tables

_FIXTURES = Path(__file__).with_name("fixtures")
_BASELINE_COMMIT = "bd17fce"
_BASELINE_INPUT_SHA256 = (
    "5e3fbf20c0034a4da72c5a4ef9cad9ca055ee283465dde9a14440481bed9fe21"
)
_BASELINE_LOGICAL_SHA256 = (
    "c37ffe3469f93d5ee2c9c5839faa3e1d7e1dc74d6754dce330694a4c9afc9b3b"
)
_BASELINE_PUML_SHA256 = (
    "0732c352bdd8b0d64e8c753f65f84853368613755bc839556cd0989047c3c6a2"
)


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bce_model() -> BCEModel:
    return BCEModel.model_validate(
        {
            "Classes": [
                {
                    "className": "OrderBoundary",
                    "stereotype": "Boundary",
                    "use_case_ids": ["UC1"],
                },
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC1"],
                },
                {
                    "className": "Customer",
                    "stereotype": "Entity",
                    "fields": ["customerId : UUID", "name : String"],
                    "identifier": ["customerId"],
                    "use_case_ids": ["UC1"],
                },
                {
                    "className": "Order",
                    "stereotype": "Entity",
                    "fields": ["placedAt : LocalDateTime", "tags : List<String>"],
                    "use_case_ids": ["UC1"],
                },
                {
                    "className": "Product",
                    "stereotype": "Entity",
                    "fields": ["sku : String", "title : String"],
                    "identifier": ["sku"],
                    "use_case_ids": ["UC1"],
                },
            ],
            "DataTypes": [
                {
                    "name": "Address",
                    "kind": "valueObject",
                    "fields": ["line1 : String"],
                }
            ],
            "Relationships": [
                {
                    "source": "Customer",
                    "target": "Order",
                    "type": "Association",
                    "sourceMultiplicity": "1",
                    "targetMultiplicity": "*",
                },
                {
                    "source": "Order",
                    "target": "Product",
                    "type": "Association",
                    "sourceMultiplicity": "*",
                    "targetMultiplicity": "*",
                },
                {
                    "source": "Customer",
                    "target": "Product",
                    "type": "Association",
                },
            ],
            "Collaborations": [],
        }
    )


def test_empty_feedback_preserves_typed_bce_without_llm_call() -> None:
    current = _bce_model()

    def unexpected_call(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("빈 ERD 피드백은 structured LLM을 호출하면 안 된다")

    revised = revise_erd_model(current, "", proposal_call=unexpected_call)

    assert revised is current


def test_revision_calls_one_typed_proposal_and_normalizes_fields() -> None:
    current = _bce_model()
    before = current.model_dump(by_alias=True)
    calls: list[type[BCEModel]] = []

    def propose(
        messages: list[dict[str, str]],
        schema: type[BCEModel],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert messages
        calls.append(schema)
        payload = copy.deepcopy(before)
        order = next(
            item for item in payload["Classes"] if item["className"] == "Order"
        )
        order["fields"] = ["placedAt:LocalDateTime", "status:String"]
        payload["DataTypes"][0]["fields"] = ["line1:String"]
        return payload

    revised = revise_erd_model(
        current,
        "Order에 상태를 추가한다.",
        "UC1: 고객이 주문한다.",
        {"Order"},
        proposal_call=propose,
    )

    assert calls == [BCEModel]
    assert isinstance(revised, BCEModel)
    order = next(item for item in revised.Classes if item.class_name == "Order")
    assert order.fields == ["placedAt : LocalDateTime", "status : String"]
    assert revised.DataTypes[0].fields == ["line1 : String"]
    assert current.model_dump(by_alias=True) == before


def test_table_field_mapping_precedes_relationship_decisions() -> None:
    payload = _bce_model().model_dump(by_alias=True)

    made, tables, pending_children = build_entity_tables(payload["Classes"])

    assert [table["name"] for table in made] == ["Customer", "Order", "Product"]
    assert [column["name"] for column in tables["Order"]["columns"]] == [
        "order_id",
        "placedAt",
    ]
    assert [
        (item["table"]["name"], item["field"], item["inner"])
        for item in pending_children
    ] == [("Order", "tags", "String")]
    assert not any(
        column["references"]
        for table in made
        for column in table["columns"]
    )


def test_relationship_mapping_owns_foreign_keys_and_junctions() -> None:
    payload = _bce_model().model_dump(by_alias=True)
    _made, tables, _pending_children = build_entity_tables(payload["Classes"])

    extra, relations, unmapped = map_relationship(
        payload["Relationships"][0], tables, set()
    )

    assert extra == []
    assert unmapped == []
    customer_fk = tables["Order"]["columns"][-1]
    assert customer_fk == {
        "name": "customerId",
        "type": "UUID",
        "role": "fk",
        "references": "Customer",
        "referencesColumn": "customerId",
        "unique": False,
        "mandatory": True,
    }
    assert relations == [
        {
            "source": "Customer",
            "target": "Order",
            "symbol": "||..o{",
            "kind": "one-to-many",
            "identifying": False,
        }
    ]

    junctions: set[str] = set()
    extra, relations, unmapped = map_relationship(
        payload["Relationships"][1], tables, junctions
    )

    assert [table["name"] for table in extra] == ["OrderProduct"]
    assert extra[0]["primaryKey"] == ["order_id", "product_sku"]
    assert [column["references"] for column in extra[0]["columns"]] == [
        "Order",
        "Product",
    ]
    assert [(item["source"], item["target"]) for item in relations] == [
        ("Order", "OrderProduct"),
        ("Product", "OrderProduct"),
    ]
    assert unmapped == []


def test_typed_projection_matches_json_and_puml_golden_byte_for_byte() -> None:
    """분리 전 ``bd17fce`` 출력과 typed·호환 투영을 바이트 단위로 비교한다."""
    bce_model = _bce_model()
    payload = bce_model.model_dump(by_alias=True)
    expected_logical = json.loads(
        (_FIXTURES / "erd_projection_golden.json").read_text(encoding="utf-8")
    )
    expected_puml = (
        (_FIXTURES / "erd_projection_golden.puml")
        .read_text(encoding="utf-8")
        .removesuffix("\n")
    )

    projected = project_logical_model(bce_model)
    rendered = render_logical_model(projected)

    # 이 세 해시는 _BASELINE_COMMIT의 기존 mapping.py와 plantuml.py를 직접
    # 실행해 고정했다. 현재 구현에서 golden을 다시 생성한 값이 아니다.
    assert _BASELINE_COMMIT == "bd17fce"
    assert _json_sha256(payload) == _BASELINE_INPUT_SHA256
    assert _json_sha256(expected_logical) == _BASELINE_LOGICAL_SHA256
    assert hashlib.sha256(expected_puml.encode()).hexdigest() == _BASELINE_PUML_SHA256
    assert projected == expected_logical
    assert projected == build_logical_model(payload)
    assert rendered == expected_puml
    assert rendered == generate_erd_from_bce_json(payload)


def test_erd_bce_storage_shape_round_trips_without_projection_fields() -> None:
    stored = _bce_model().model_dump(by_alias=True)
    restored = BCEModel.model_validate(json.loads(json.dumps(stored)))

    assert restored.model_dump(by_alias=True) == stored
    assert set(stored) == {
        "Classes",
        "DataTypes",
        "Relationships",
        "Collaborations",
    }
    assert "Tables" not in stored
    assert "Relations" not in stored
    assert "Unmapped" not in stored


def test_graph_spec_validates_raw_bce_and_preserves_stored_alias_shape() -> None:
    stored = _bce_model().model_dump(by_alias=True)
    state: ArchitectureState = {
        "extracted_bce_classes": stored,
        "erd_bce_classes": {},
    }

    extracted = design_subgraphs.ERD_SPEC.extract(state)
    rendered = design_subgraphs.ERD_SPEC.render(extracted)
    expected_puml = (
        (_FIXTURES / "erd_projection_golden.puml")
        .read_text(encoding="utf-8")
        .removesuffix("\n")
    )

    assert extracted == stored
    assert extracted is not stored
    assert set(extracted) == {
        "Classes",
        "DataTypes",
        "Relationships",
        "Collaborations",
    }
    assert rendered == expected_puml
    assert rendered == generate_erd_from_bce_json(stored)

    invalid_state: ArchitectureState = {
        **state,
        "extracted_bce_classes": {"Classes": "invalid"},
    }
    with pytest.raises(ValidationError):
        design_subgraphs.ERD_SPEC.extract(invalid_state)
    with pytest.raises(ValidationError):
        design_subgraphs.ERD_SPEC.render({"Classes": "invalid"})


def test_graph_revision_passes_typed_bce_and_dumps_alias_json(monkeypatch) -> None:
    current = _bce_model()
    stored = current.model_dump(by_alias=True)
    captured: list[tuple[BCEModel, str, set[str]]] = []

    def revise(
        bce_model: BCEModel,
        feedback: str,
        _scenario_text: str,
        targets: set[str],
    ) -> BCEModel:
        captured.append((bce_model, feedback, targets))
        return bce_model

    monkeypatch.setattr(design_subgraphs, "revise_erd_classes", revise)

    state: ArchitectureState = {
        "usecase_spec": {"use_cases": [{"id": "UC1"}]},
    }
    revised = design_subgraphs.ERD_SPEC.revise(
        stored,
        "Order table을 유지한다.",
        state,
        {"Order"},
    )

    assert len(captured) == 1
    assert isinstance(captured[0][0], BCEModel)
    assert captured[0][1:] == ("Order table을 유지한다.", {"Order"})
    assert revised == stored
