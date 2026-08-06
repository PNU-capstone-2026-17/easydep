from unittest.mock import MagicMock, patch

from app.design.services.sequence_diagram import reconcile


# ---------------------------------------------------------------------------
# Fixture State
# ---------------------------------------------------------------------------
def _base_state() -> dict:
    return {
        "app_id": "test-app-id",
        "usecase_spec": {"use_cases": [{"id": "UC1", "name": "Create order"}]},
        "class_diagram_puml": "class OrderBoundary <<Boundary>>\nclass OrderControl <<Control>>\n",
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderBoundary",
                    "stereotype": "Boundary",
                    "methods": ["displayForm()"],
                },
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "methods": ["+ createOrder()"],
                },
            ],
            "Relationships": [],
        },
        "sequence_diagram_model": {
            "Participants": [
                {"name": "User", "kind": "actor"},
                {"name": "OrderBoundary", "kind": "boundary", "source_class": "OrderBoundary"},
                {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
            ],
            "Messages": [
                {"source": "User", "target": "OrderBoundary", "label": "displayForm()", "type": "sync"},
                {"source": "OrderBoundary", "target": "OrderControl", "label": "createOrder()", "type": "sync"},
            ],
        },
    }


# ---------------------------------------------------------------------------
# Case 1: All methods exist → No-op
# ---------------------------------------------------------------------------
def test_reconcile_no_op_when_all_methods_exist():
    state = _base_state()
    res = reconcile.reconcile_class_methods(state)
    assert res == {}


# ---------------------------------------------------------------------------
# Case 2: Missing method → Deterministically added to class BCE
# ---------------------------------------------------------------------------
def test_reconcile_adds_missing_methods_deterministically():
    state = _base_state()
    # Add a message to OrderControl calling new method "processPayment(amount)"
    state["sequence_diagram_model"]["Messages"].append(
        {"source": "OrderBoundary", "target": "OrderControl", "label": "processPayment(amount)", "type": "sync"}
    )

    with patch("app.repositories.artifact_repository.save_stage") as mock_save:
        res = reconcile.reconcile_class_methods(state)

    assert "extracted_bce_classes" in res
    assert "class_diagram_puml" in res

    # Verify processPayment(amount) was added to OrderControl
    classes = res["extracted_bce_classes"]["Classes"]
    control_class = next(c for c in classes if c["className"] == "OrderControl")
    assert "processPayment(amount)" in control_class["methods"]

    # Verify PlantUML was re-rendered and saved to DB
    assert "processPayment" in res["class_diagram_puml"]
    mock_save.assert_called_once()
    assert mock_save.call_args[0][1] == "class_diagram"
    assert mock_save.call_args[1]["origin"] == "AUTO_FIXED"


# ---------------------------------------------------------------------------
# Case 3: Return messages and empty labels are ignored
# ---------------------------------------------------------------------------
def test_reconcile_skips_return_messages_and_empty_labels():
    state = _base_state()
    state["sequence_diagram_model"]["Messages"].extend([
        {"source": "OrderControl", "target": "OrderBoundary", "label": "ghostMethod()", "type": "return"},
        {"source": "OrderBoundary", "target": "OrderControl", "label": "", "type": "sync"},
    ])

    res = reconcile.reconcile_class_methods(state)
    assert res == {}


# ---------------------------------------------------------------------------
# Case 4: Empty sequence messages + classes lack methods → Triggers LLM enrichment
# ---------------------------------------------------------------------------
def test_reconcile_empty_messages_and_no_methods_triggers_llm():
    state = _base_state()
    # Remove methods from classes
    for c in state["extracted_bce_classes"]["Classes"]:
        c["methods"] = []
    # Empty messages
    state["sequence_diagram_model"]["Messages"] = []

    mock_revised_bce = {
        "Classes": [
            {
                "className": "OrderBoundary",
                "stereotype": "Boundary",
                "methods": ["showForm()"],
            },
            {
                "className": "OrderControl",
                "stereotype": "Control",
                "methods": ["executeOrder()"],
            },
        ],
        "Relationships": [],
    }

    mock_new_seq = {
        "Participants": [
            {"name": "OrderBoundary", "kind": "boundary", "source_class": "OrderBoundary"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {"source": "OrderBoundary", "target": "OrderControl", "label": "executeOrder()", "type": "sync"},
        ],
    }

    with patch("app.design.services.sequence_diagram.reconcile.revise_bce_classes", return_value=mock_revised_bce) as mock_revise, \
         patch("app.design.services.sequence_diagram.reconcile.extract_sequence_model", return_value=mock_new_seq) as mock_extract, \
         patch("app.repositories.artifact_repository.save_stage") as mock_save:

        res = reconcile.reconcile_class_methods(state)

    assert res["extracted_bce_classes"] == mock_revised_bce
    assert res["sequence_diagram_model"] == mock_new_seq
    assert "executeOrder" in res["class_diagram_puml"]
    mock_revise.assert_called_once()
    mock_extract.assert_called_once()
    mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# Case 5: When app_id is absent, skip persistence call
# ---------------------------------------------------------------------------
def test_reconcile_skips_persistence_when_no_appid():
    state = _base_state()
    state["app_id"] = None
    state["sequence_diagram_model"]["Messages"].append(
        {"source": "OrderBoundary", "target": "OrderControl", "label": "newAction()", "type": "sync"}
    )

    with patch("app.repositories.artifact_repository.save_stage") as mock_save:
        res = reconcile.reconcile_class_methods(state)

    assert "extracted_bce_classes" in res
    mock_save.assert_not_called()
