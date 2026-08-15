from unittest.mock import patch

from app.design.graphs.subgraphs import SEQUENCE_DIAGRAM_SPEC
from app.design.services.sequence_diagram.reconcile import reconcile_class_methods


def test_removed_reconcile_boundary_never_mutates_class_diagram():
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
                    "label": "inventedMethod()",
                    "type": "self",
                }
            ],
        },
    }

    assert SEQUENCE_DIAGRAM_SPEC.reconcile is None
    with patch("app.repositories.artifact_repository.save_stage") as save_stage:
        assert reconcile_class_methods(state) == {}

    save_stage.assert_not_called()
    assert state["extracted_bce_classes"]["Classes"][0]["methods"] == ["createOrder()"]
