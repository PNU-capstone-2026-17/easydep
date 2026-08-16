from unittest.mock import patch

from app.design.graphs.subgraphs import SEQUENCE_DIAGRAM_SPEC
import pytest

from app.design.services.sequence_diagram.reconcile import (
    ensure_sequence_class_methods,
    reconcile_class_methods,
)


def test_sequence_reconcile_uses_llm_to_add_grounded_receiver_method():
    state = {
        "app_id": "test-app-id",
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                }
            ],
            "Messages": [
                {
                    "source": "Control",
                    "target": "Control",
                    "label": "reserveOrder()",
                    "type": "self",
                }
            ],
        },
    }

    assert SEQUENCE_DIAGRAM_SPEC.reconcile is reconcile_class_methods
    assert SEQUENCE_DIAGRAM_SPEC.finalize is ensure_sequence_class_methods
    revised_bce = {
        "Classes": [
            {
                "className": "OrderControl",
                # LLM이 기존 메서드를 누락해도 병합 단계가 보존해야 한다.
                "methods": ["reserveOrder()"],
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
    assert "decide whether the use case genuinely requires it" in revise.call_args.kwargs["feedback"]
    save_stage.assert_called_once()
    assert result["extracted_bce_classes"]["Classes"][0]["methods"] == [
        "createOrder()",
        "reserveOrder()",
    ]


def test_sequence_finalizer_rejects_call_without_a_receiver_class():
    state = {
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "User",
                    "alias": "User",
                    "kind": "actor",
                    "source_class": "",
                }
            ],
            "Messages": [
                {
                    "source": "User",
                    "target": "User",
                    "label": "createOrder()",
                    "type": "self",
                }
            ],
        },
    }

    with pytest.raises(ValueError, match="must target a class-diagram class"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_requires_one_diagram_per_use_case():
    state = {
        "usecase_spec": {
            "use_cases": [{"id": "UC1"}, {"id": "UC2"}],
        },
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": []}]
        },
        "sequence_diagram_model": {
            "Diagrams": [
                {
                    "use_case_id": "UC1",
                    "use_case_name": "Create order",
                    "Participants": [],
                    "Messages": [],
                }
            ]
        },
    }

    with pytest.raises(ValueError, match="exactly one diagram per use case"):
        ensure_sequence_class_methods(state)


def test_reconcile_declares_return_type_for_a_required_result():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderControl",
                    "methods": ["findOrder()", "cancelOrder()"],
                }
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                },
                {
                    "name": "OrderBoundary",
                    "alias": "Boundary",
                    "kind": "boundary",
                    "source_class": "OrderBoundary",
                },
            ],
            "Messages": [
                {"source": "Boundary", "target": "Control", "label": "findOrder()", "type": "sync"},
                {"source": "Control", "target": "Boundary", "label": "Order", "type": "return"},
            ],
        },
    }

    revised_bce = {
        "Classes": [{"className": "OrderControl", "methods": ["findOrder(): Order"]}]
    }
    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
        return_value=revised_bce,
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_called_once()
    assert result["extracted_bce_classes"]["Classes"][0]["methods"] == [
        "findOrder(): Order",
        "cancelOrder()",
    ]


def test_finalizer_rejects_return_label_different_from_declared_type():
    state = {
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["findOrder(): Order"]}]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                },
                {
                    "name": "OrderBoundary",
                    "alias": "Boundary",
                    "kind": "boundary",
                    "source_class": "OrderBoundary",
                },
            ],
            "Messages": [
                {"source": "Boundary", "target": "Control", "label": "findOrder()", "type": "sync"},
                {"source": "Control", "target": "Boundary", "label": "Customer", "type": "return"},
            ],
        },
    }

    with pytest.raises(ValueError, match="sequence interaction contracts remain invalid"):
        ensure_sequence_class_methods(state)


def test_finalizer_rejects_multiple_returns_for_one_call():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderControl", "methods": ["findOrder(): Order"]}
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                },
                {
                    "name": "OrderBoundary",
                    "alias": "Boundary",
                    "kind": "boundary",
                    "source_class": "OrderBoundary",
                },
            ],
            "Messages": [
                {
                    "source": "Boundary",
                    "target": "Control",
                    "label": "findOrder()",
                    "type": "sync",
                },
                {
                    "source": "Control",
                    "target": "Boundary",
                    "label": "Order",
                    "type": "return",
                },
                {
                    "source": "Control",
                    "target": "Boundary",
                    "label": "Customer",
                    "type": "return",
                },
            ],
        },
    }

    with pytest.raises(ValueError, match="고립된 return"):
        ensure_sequence_class_methods(state)


def test_uncovered_flow_is_left_for_sequence_repair_not_class_augmentation():
    state = {
        "usecase_spec": {
            "use_case_specs": [
                {
                    "use_case_id": "UC1",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "submit order"},
                        {"step_number": 2, "sentence": "reserve order"},
                    ],
                    "extensions": [],
                }
            ]
        },
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["createOrder()"]}],
            "Relationships": [],
        },
        "sequence_diagram_model": {
            "Participants": [
                {
                    "name": "OrderControl",
                    "alias": "Control",
                    "kind": "control",
                    "source_class": "OrderControl",
                }
            ],
            "Messages": [
                {
                    "source": "Control",
                    "target": "Control",
                    "label": "createOrder()",
                    "type": "self",
                    "step_ids": ["UC1:main:1"],
                }
            ],
        },
    }
    with patch(
        "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
    ) as revise:
        result = reconcile_class_methods(state)

    revise.assert_not_called()
    assert result == {}


def _finalizer_contract_state(messages: list[dict]) -> dict:
    return {
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "methods": ["start(): View"]},
                {"className": "OrderControl", "methods": ["process(): Result", "validate(): void"]},
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {"name": "User", "alias": "User", "kind": "actor"},
                {"name": "OrderBoundary", "alias": "Boundary", "kind": "boundary", "source_class": "OrderBoundary"},
                {"name": "OrderControl", "alias": "Control", "kind": "control", "source_class": "OrderControl"},
            ],
            "Messages": messages,
        },
    }


def test_sequence_finalizer_rejects_missing_nonvoid_return():
    state = _finalizer_contract_state([
        {"source": "User", "target": "Boundary", "label": "start()", "type": "sync"},
        {"source": "Boundary", "target": "User", "label": "View", "type": "return"},
        {"source": "Boundary", "target": "Control", "label": "process()", "type": "sync"},
    ])

    with pytest.raises(ValueError, match="return 메시지가 없음"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_rejects_disconnected_call_source():
    state = _finalizer_contract_state([
        {"source": "User", "target": "Boundary", "label": "start()", "type": "sync"},
        {"source": "Boundary", "target": "User", "label": "View", "type": "return"},
        {"source": "Control", "target": "Control", "label": "validate()", "type": "self"},
    ])

    with pytest.raises(ValueError, match="활성화되기 전에"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_rejects_one_sided_alt():
    state = _finalizer_contract_state([
        {"source": "User", "target": "Boundary", "label": "start()", "type": "sync"},
        {"source": "Boundary", "target": "User", "label": "View", "type": "return"},
        {
            "source": "Boundary",
            "target": "Control",
            "label": "validate()",
            "type": "sync",
            "fragments": [{"id": "choice", "type": "alt", "branch": "main", "condition": "valid"}],
        },
    ])

    with pytest.raises(ValueError, match="main과 else"):
        ensure_sequence_class_methods(state)


def test_new_sequence_contract_finalizer_runs_all_registered_detectors():
    state = _finalizer_contract_state([
        {
            "source": "User", "target": "Boundary", "label": "start()", "type": "sync",
            "call_id": "call-1", "reply_to": "", "arguments": [],
        },
        {
            "source": "Boundary", "target": "User", "label": "View", "type": "return",
            "call_id": "", "reply_to": "call-1", "arguments": [],
        },
        {
            "source": "Boundary", "target": "Control", "label": "validate()", "type": "sync",
            "call_id": "call-2", "reply_to": "", "arguments": [],
        },
        {
            "source": "Boundary", "target": "Control", "label": "validate()", "type": "sync",
            "call_id": "call-3", "reply_to": "", "arguments": [],
        },
    ])

    with pytest.raises(ValueError, match="연달아 중복"):
        ensure_sequence_class_methods(state)


def test_sequence_finalizer_rejects_stale_class_diagram_version():
    state = {
        "class_diagram_puml": "class Current",
        "extracted_bce_classes": {"Classes": []},
        "sequence_diagram_model": {
            "class_diagram_hash": "stale",
            "Diagrams": [],
        },
    }

    with pytest.raises(ValueError, match="different class diagram version"):
        ensure_sequence_class_methods(state)
