import dataclasses
from unittest.mock import patch

from app.design.graphs.subgraphs import SEQUENCE_DIAGRAM_SPEC
from app.design.knowledge.detectors import (
    sequence_message_methods,
    sequence_usecase_coverage,
)
from app.design.services.sequence_diagram.extractor import extract_sequence_diagrams
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.reconcile import reconcile_class_methods
from app.design.nodes.artifact import CLEAN, NO_IMPROVEMENT, check_node, merge_model


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


def test_extracts_one_sequence_diagram_for_each_use_case():
    specification = {
        "use_cases": [
            {"id": "UC1", "name": "Create order"},
            {"id": "UC2", "name": "Cancel order"},
        ],
        "use_case_specs": [
            {"use_case_id": "UC1", "main_scenario": []},
            {"use_case_id": "UC2", "main_scenario": []},
        ],
    }

    def extracted(scenario_text, class_diagram_puml):
        use_case_id = "UC1" if '"UC1"' in scenario_text else "UC2"
        return {
            "Participants": [],
            "Messages": [],
        }

    with patch(
        "app.design.services.sequence_diagram.extractor.extract_sequence_model",
        side_effect=extracted,
    ) as extract:
        result = extract_sequence_diagrams(specification, "class Order")

    assert extract.call_count == 2
    assert [item["use_case_id"] for item in result["Diagrams"]] == ["UC1", "UC2"]
    assert [item["use_case_name"] for item in result["Diagrams"]] == [
        "Create order",
        "Cancel order",
    ]


def test_targeted_sequence_revision_preserves_other_use_case_diagrams():
    original = {
        "Diagrams": [
            {"use_case_id": "UC1", "Messages": [{"label": "before()"}]},
            {"use_case_id": "UC2", "Messages": [{"label": "keep()"}]},
        ]
    }
    revised = {
        "Diagrams": [
            {"use_case_id": "UC1", "Messages": [{"label": "after()"}]},
            {"use_case_id": "UC2", "Messages": [{"label": "changedByLlm()"}]},
        ]
    }

    merged = merge_model(SEQUENCE_DIAGRAM_SPEC, original, revised, {"UC1"})

    assert set(merged) == {"Diagrams"}
    assert merged["Diagrams"][0]["Messages"][0]["label"] == "after()"
    assert merged["Diagrams"][1]["Messages"][0]["label"] == "keep()"


def test_sequence_stage_asks_llm_before_adding_receiver_method():
    assert SEQUENCE_DIAGRAM_SPEC.reconcile is reconcile_class_methods
    state = {
        "app_id": "test-app-id",
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [_participant("OrderControl", "control", "OrderControl")],
            "Messages": [_message("OrderControl", "OrderControl", "reserveOrder()")],
        },
    }
    revised_bce = {
        "Classes": [
            {
                "className": "OrderControl",
                "methods": ["createOrder()", "reserveOrder()"],
            }
        ]
    }
    with (
        patch(
            "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
            return_value=revised_bce,
        ) as revise,
        patch("app.repositories.artifact_repository.save_stage") as save_stage,
    ):
        result = reconcile_class_methods(state)
    revise.assert_called_once()
    save_stage.assert_called_once()
    assert result["extracted_bce_classes"]["Classes"][0]["methods"] == [
        "createOrder()",
        "reserveOrder()",
    ]


def test_sequence_stage_does_not_add_method_when_llm_declines():
    bce = {"Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]}
    state = {
        "extracted_bce_classes": bce,
        "sequence_diagram_model": {
            "Participants": [_participant("OrderControl", "control", "OrderControl")],
            "Messages": [_message("OrderControl", "OrderControl", "reserveOrder()")],
        },
    }

    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=bce,
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_called_once()
    assert result == {}
    assert bce["Classes"][0]["methods"] == ["createOrder()"]


def test_sequence_check_repairs_a_return_attached_to_an_async_call(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "2")
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    async_call = {
        "source": "User",
        "target": "OrderBoundary",
        "label": "requestOrder()",
        "type": "async",
        "fragments": [],
        "use_case_ids": [],
        "step_ids": [],
    }
    returned = {
        "source": "OrderBoundary",
        "target": "User",
        "label": "Order",
        "type": "return",
        "fragments": [],
        "use_case_ids": [],
        "step_ids": [],
    }
    dirty = {"Participants": participants, "Messages": [async_call, returned]}
    repaired = {
        "Participants": participants,
        "Messages": [{**async_call, "type": "sync"}, returned],
    }
    state = {
        "class_diagram_puml": "class OrderBoundary <<Boundary>>",
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["requestOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": dirty,
    }
    feedback_seen: list[str] = []

    def repair(current, feedback, current_state, targets):
        feedback_seen.append(feedback)
        return repaired

    spec = dataclasses.replace(SEQUENCE_DIAGRAM_SPEC, revise=repair)
    result = check_node(spec)(state)

    assert "sequence.async-call-has-no-return" in feedback_seen[0]
    assert result["sequence_diagram_model"] == repaired
    assert result["sequence_diagram_check"] == {
        "findings": [],
        "repair_iters": 1,
        "stopped": CLEAN,
    }


def test_sequence_check_rejects_repair_that_drops_existing_step_trace(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "1")
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    async_call = _message(
        "User",
        "OrderBoundary",
        "requestOrder()",
        type="async",
    )
    returned = _message(
        "OrderBoundary",
        "User",
        "Order",
        type="return",
    )
    dirty = {"Participants": participants, "Messages": [async_call, returned]}
    lossy_repair = {
        "Participants": participants,
        "Messages": [{**async_call, "step_ids": []}],
    }
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["requestOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": dirty,
    }
    spec = dataclasses.replace(
        SEQUENCE_DIAGRAM_SPEC,
        revise=lambda current, feedback, current_state, targets: lossy_repair,
    )

    result = check_node(spec)(state)

    assert result["sequence_diagram_model"] == dirty
    assert result["sequence_diagram_check"]["stopped"] == NO_IMPROVEMENT
    assert result["sequence_diagram_check"]["findings"]


def test_sequence_check_allows_removing_hallucinated_trace_references(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "1")
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    async_call = _message(
        "User",
        "OrderBoundary",
        "requestOrder()",
        type="async",
        use_case_ids=["UC404"],
        step_ids=["UC404:main:1"],
    )
    returned = _message(
        "OrderBoundary",
        "User",
        "Order",
        type="return",
        use_case_ids=["UC404"],
        step_ids=["UC404:main:1"],
    )
    repaired_call = {
        **async_call,
        "use_case_ids": ["UC1"],
        "step_ids": ["UC1:main:1"],
    }
    dirty = {"Participants": participants, "Messages": [async_call, returned]}
    repaired = {"Participants": participants, "Messages": [repaired_call]}
    state = {
        "class_diagram_puml": "class OrderBoundary <<Boundary>>",
        "usecase_spec": {
            "use_cases": [{"id": "UC1"}],
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{"step_number": 1, "description": "request"}],
            }],
        },
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["requestOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": dirty,
    }
    spec = dataclasses.replace(
        SEQUENCE_DIAGRAM_SPEC,
        revise=lambda current, feedback, current_state, targets: repaired,
    )

    result = check_node(spec)(state)

    assert result["sequence_diagram_model"] == repaired
    assert result["sequence_diagram_check"]["stopped"] == CLEAN


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
    assert "autonumber" not in rendered
    assert "alt approved" in rendered
    assert "loop for each item" in rendered
    assert "else declined" in rendered
    assert rendered.count("alt ") == 1
    assert "activate OrderControl" in rendered
    assert "deactivate OrderControl" in rendered


def test_renderer_emits_an_independent_plantuml_document_per_use_case():
    participants = [
        _participant("User", "actor"),
        _participant("OrderBoundary", "boundary", "OrderBoundary"),
    ]
    model = {
        "Diagrams": [
            {
                "use_case_id": "UC1",
                "use_case_name": "Create order",
                "Participants": participants,
                "Messages": [_message("User", "OrderBoundary", "createOrder()")],
            },
            {
                "use_case_id": "UC2",
                "use_case_name": "Cancel order",
                "Participants": participants,
                "Messages": [_message("User", "OrderBoundary", "cancelOrder()")],
            },
        ]
    }

    rendered = generate_sequence_from_model(model)

    assert rendered.count("@startuml") == 2
    assert "@startuml UC1" in rendered
    assert "@startuml UC2" in rendered
    assert "title UC1 - Create order" in rendered
    assert "title UC2 - Cancel order" in rendered
    assert "createOrder()" in rendered
    assert "cancelOrder()" in rendered
