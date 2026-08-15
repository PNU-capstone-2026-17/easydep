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


def test_reconcile_declares_return_type_for_a_required_result():
    state = {
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "methods": ["findOrder()"]}]
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
        "findOrder(): Order"
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

    with pytest.raises(ValueError, match="call/return contracts remain invalid"):
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


def test_uncovered_flow_causes_class_method_augmentation_and_sequence_reextraction():
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
    revised_bce = {
        "Classes": [
            {
                "className": "OrderControl",
                "methods": ["createOrder()", "reserveOrder()"],
            }
        ],
        "Relationships": [],
    }
    revised_sequence = {
        "Participants": state["sequence_diagram_model"]["Participants"],
        "Messages": [
            state["sequence_diagram_model"]["Messages"][0],
            {
                "source": "Control",
                "target": "Control",
                "label": "reserveOrder()",
                "type": "self",
                "step_ids": ["UC1:main:2"],
            },
        ],
    }

    with (
        patch(
            "app.design.services.sequence_diagram.reconcile.revise_bce_classes",
            return_value=revised_bce,
        ) as revise,
        patch(
            "app.design.services.sequence_diagram.reconcile.extract_sequence_model",
            return_value=revised_sequence,
        ) as extract,
    ):
        result = reconcile_class_methods(state)

    revise.assert_called_once()
    extract.assert_called_once()
    assert result["extracted_bce_classes"] == revised_bce
    assert result["sequence_diagram_model"] == revised_sequence
