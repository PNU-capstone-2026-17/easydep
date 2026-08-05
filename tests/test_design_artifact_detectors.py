from app.design.knowledge import detectors


STATE = {
    "class_diagram_puml": "class OrderBoundary <<Boundary>>\nclass OrderControl <<Control>>\n",
    "usecase_spec": {"use_cases": [{"id": "UC1", "name": "Create order"}]},
}


def test_sequence_detector_rejects_dangling_and_invalid_bce_messages():
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderControl", "type": "sync", "use_case_ids": ["UC404"]},
            {"source": "Missing", "target": "OrderControl", "type": "sync"},
        ],
    }
    found = {item.rule_id for item in detectors.sequence_diagram_findings(model, STATE)}
    assert {"sequence.message-participants-exist", "sequence.message-bce-flow", "sequence.references-exist"} <= found


def test_api_detector_rejects_invalid_references_and_path_parameters():
    model = {
        "Endpoints": [
            {
                "path": "/orders/{orderId}", "method": "get", "operation_id": "",
                "path_params": [{"name": "wrong"}], "request_schema": "Missing",
                "responses": [{"schema_name": "Missing"}],
                "source_classes": ["Ghost"], "use_case_ids": ["UC404"],
            }
        ],
        "Schemas": [],
    }
    found = {item.rule_id for item in detectors.api_spec_findings(model, STATE)}
    assert {"api.path-parameters-match", "api.schema-references-exist", "api.operation-ids-unique", "api.references-exist"} <= found
