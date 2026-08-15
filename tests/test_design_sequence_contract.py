from unittest.mock import patch

from app.design.graphs.subgraphs import SEQUENCE_DIAGRAM_SPEC
from app.design.knowledge.detectors import (
    sequence_message_methods,
    sequence_usecase_coverage,
)
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.reconcile import reconcile_class_methods


def _participant(name: str, kind: str, source_class: str = "") -> dict:
    return {
        "name": name,
        "alias": name,
        "kind": kind,
        "description": "",
        "source_class": source_class,
    }


def _message(source: str, target: str, label: str, **overrides) -> dict:
    message = {
        "source": source,
        "target": target,
        "label": label,
        "type": "sync",
        "fragments": [],
        "use_case_ids": ["UC1"],
        "step_ids": ["UC1:main:1"],
    }
    message.update(overrides)
    return message


def test_sequence_stage_never_mutates_class_diagram_to_accept_a_message():
    assert SEQUENCE_DIAGRAM_SPEC.reconcile is None
    state = {
        "app_id": "test-app-id",
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [_participant("OrderControl", "control", "OrderControl")],
            "Messages": [_message("OrderControl", "OrderControl", "inventedMethod()")],
        },
    }
    with patch("app.repositories.artifact_repository.save_stage") as save_stage:
        assert reconcile_class_methods(state) == {}
    save_stage.assert_not_called()
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["createOrder()"]


def test_receiver_must_already_own_the_called_method():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["submitOrder()"]},
                {"className": "OrderControl", "methods": ["createOrder()"]},
            ]
        }
    }
    model = {
        "Participants": [
            _participant("OrderBoundary", "boundary", "OrderBoundary"),
            _participant("OrderControl", "control", "OrderControl"),
        ],
        "Messages": [_message("OrderBoundary", "OrderControl", "inventedMethod()")],
    }
    findings = sequence_message_methods(model, state)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.message-labels-match-methods"


def test_flow_coverage_checks_each_main_and_extension_step():
    state = {
        "usecase_spec": {
            "use_case_specs": [
                {
                    "use_case_id": "UC1",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "submit"},
                        {"step_number": 2, "sentence": "save"},
                    ],
                    "extensions": [
                        {
                            "label": "2a",
                            "handling_steps": [{"sub_step": "2a1", "sentence": "reject"}],
                        }
                    ],
                }
            ]
        }
    }
    model = {"Messages": [_message("A", "B", "submitOrder()") ]}
    findings = sequence_usecase_coverage(model, state)
    missing = {finding.location for finding in findings}
    assert missing == {"UC1:main:2", "UC1:extension:2a:2a1"}


def test_renderer_preserves_alt_else_nested_fragments_and_lifecycle_events():
    outer_main = {"id": "payment", "type": "alt", "branch": "main", "condition": "approved"}
    outer_else = {"id": "payment", "type": "alt", "branch": "else", "condition": "declined"}
    inner = {"id": "items", "type": "loop", "branch": "main", "condition": "for each item"}
    model = {
        "Participants": [
            _participant("OrderBoundary", "boundary", "OrderBoundary"),
            _participant("OrderControl", "control", "OrderControl"),
        ],
        "Messages": [
            _message("OrderBoundary", "OrderControl", "createOrder()", fragments=[outer_main]),
            _message("OrderControl", "OrderControl", "reserveItem()", type="self", fragments=[outer_main, inner]),
            _message("OrderControl", "OrderControl", "", type="activate", fragments=[outer_main]),
            _message("OrderControl", "OrderControl", "", type="deactivate", fragments=[outer_main]),
            _message("OrderControl", "OrderBoundary", "showFailure()", fragments=[outer_else]),
        ],
    }
    rendered = generate_sequence_from_model(model)
    assert "alt approved" in rendered
    assert "loop for each item" in rendered
    assert "else declined" in rendered
    assert rendered.count("alt ") == 1
    assert "activate OrderControl" in rendered
    assert "deactivate OrderControl" in rendered
