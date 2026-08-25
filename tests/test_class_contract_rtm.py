from app.design.rtm import build_design_rtm


def test_class_operation_and_input_binding_rows_are_traceable():
    operation_id = "OrderController::placeOrder(orderRequest:OrderRequest)"
    state = {
        "usecase_spec": {"use_cases": [{"id": "UC1"}]},
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderController",
                    "stereotype": "Control",
                    "use_case_ids": ["UC1"],
                    "operations": [
                        {
                            "operationId": operation_id,
                            "name": "placeOrder",
                            "parameters": [
                                {"name": "orderRequest", "type": "OrderRequest"}
                            ],
                            "returnType": "void",
                            "stepRefs": ["UC1:main:2"],
                            "actorEntry": False,
                            "inputBindings": [
                                {
                                    "useCaseId": "UC1",
                                    "parameter": "orderRequest",
                                    "sourceRef": "UC1:main:1#orderRequest",
                                }
                            ],
                        }
                    ],
                }
            ],
            "Relationships": [],
        },
    }

    rtm = build_design_rtm(state)
    operation = next(row for row in rtm["rows"] if row["element"] == operation_id)
    binding = next(
        row
        for row in rtm["rows"]
        if row["element"] == f"{operation_id}#orderRequest"
    )

    assert operation["sources"] == {
        "class": ["OrderController"],
        "flow_step": ["UC1:main:2"],
        "use_case": ["UC1"],
    }
    assert binding["sources"] == {
        "class": ["OrderController"],
        "class_operation": [operation_id],
        "flow_step": ["UC1:main:2"],
        "use_case": ["UC1"],
        "value_source": ["UC1:main:1#orderRequest"],
    }
