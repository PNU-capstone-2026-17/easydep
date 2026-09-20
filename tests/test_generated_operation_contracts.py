from app.design.contracts.api_spec import ApiSpecModel
from app.design.schemas.class_model import BCEModel
from app.design.schemas.sequence_model import SequenceCollection
from app.implementation.generation.operation_contracts import (
    build_generated_operation_contracts,
    write_generated_operation_contracts,
)
from app.implementation.planning.method_projection import (
    CallProjection,
    MethodProjection,
    MethodProjectionResult,
    MethodRef,
    MethodSlice,
)


def test_operation_contracts_keep_typed_source_and_endpoint_facts(tmp_path):
    bce = BCEModel.model_validate(
        {
            "Classes": [
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "operations": [
                        {
                            "operationId": "ignored",
                            "stableId": "order-place-v1",
                            "name": "place",
                            "parameters": [{"name": "request", "type": "OrderRequest"}],
                            "returnType": "OrderReceipt",
                        }
                    ],
                }
            ],
            "DataTypes": [
                {"name": "OrderRequest", "kind": "valueObject", "fields": ["id : string"]},
                {"name": "OrderReceipt", "kind": "valueObject", "fields": ["id : string"]},
            ],
            "Relationships": [],
            "Collaborations": [],
        }
    )
    api = ApiSpecModel.model_validate(
        {
            "Endpoints": [
                {
                    "operation_id": "placeOrder",
                    "path": "/orders",
                    "method": "post",
                    "request_schema": "OrderRequest",
                    "responses": [{"status": 200, "schema_name": "OrderReceipt"}],
                    "control_binding": {
                        "control": "OrderControl",
                        "method": "place",
                        "arguments": [{"name": "request", "source": "body"}],
                        "outcomes": [{"status": 200, "outcome": "success"}],
                    },
                }
            ],
        }
    )
    result = build_generated_operation_contracts(
        bce_model=bce,
        sequence_model=SequenceCollection.model_validate({"Diagrams": []}),
        api_model=api,
        base_package="com.example.orders",
    )
    path = write_generated_operation_contracts(tmp_path, result)
    item = result.contracts[0]
    assert item.stable_id == "order-place-v1"
    assert item.source.endswith("bce/OrderControl.java")
    assert item.writable_source.endswith("application/impl/OrderControlService.java")
    assert item.parameters[0].type == "com.example.orders.bce.OrderRequest"
    assert item.return_type == "com.example.orders.bce.OrderReceipt"
    assert item.endpoints[0].method == "POST"
    assert item.endpoints[0].request_type == "com.example.orders.api.model.OrderRequest"
    assert item.endpoints[0].response_types == ["com.example.orders.api.model.OrderReceipt"]
    assert item.endpoints[0].input_bindings[0].source == "body"
    assert item.completion_marker == "EASYDEP-IMPLEMENT: complete order-place-v1"
    assert path == tmp_path / "reports" / "generated-operation-contracts.json"


def test_interface_and_entity_writable_contract_facts_are_honest():
    bce = BCEModel.model_validate(
        {
            "Classes": [
                {
                    "className": "OrderBoundary",
                    "stereotype": "Boundary",
                    "operations": [{"operationId": "ignored", "name": "submit"}],
                },
                {
                    "className": "Order",
                    "stereotype": "Entity",
                    "operations": [{"operationId": "ignored", "name": "rename"}],
                },
            ],
            "DataTypes": [],
            "Relationships": [],
            "Collaborations": [],
        }
    )
    result = build_generated_operation_contracts(
        bce_model=bce,
        sequence_model=SequenceCollection.model_validate({"Diagrams": []}),
        api_model=ApiSpecModel.model_validate({"Endpoints": []}),
        base_package="com.example.orders",
    )
    boundary = next(item for item in result.contracts if item.owner == "OrderBoundary")
    assert boundary.writable_source is None
    assert boundary.completion_marker is None
    entity = next(item for item in result.contracts if item.owner == "Order")
    assert entity.constructor_dependencies == []


def test_void_control_with_projected_call_has_no_completion_marker(monkeypatch):
    bce = BCEModel.model_validate(
        {
            "Classes": [
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "operations": [
                        {"operationId": "ignored", "name": "run", "returnType": "void"},
                    ],
                }
            ],
            "DataTypes": [],
            "Relationships": [],
            "Collaborations": [],
        }
    )
    method = bce.Classes[0].operations[0]
    ref = MethodRef("OrderControl", "Control", method.operation_id, "run-v1", "run", (), "void")
    target = MethodRef("Order", "Entity", "Order::touch()", "touch-v1", "touch", (), "void")
    call = CallProjection("call-1", target, (), None, (), "code", ())
    slice_ = MethodSlice(("UC-1",), "incoming", "Actor", ref, (call,), "void", (), ())
    projection = MethodProjectionResult((MethodProjection(ref, (slice_,), "code", ()),), ())
    monkeypatch.setattr(
        "app.implementation.generation.operation_contracts.project_method_calls",
        lambda **_: projection,
    )
    result = build_generated_operation_contracts(
        bce_model=bce,
        sequence_model=SequenceCollection.model_validate({"Diagrams": []}),
        api_model=ApiSpecModel.model_validate({"Endpoints": []}),
        base_package="com.example.orders",
    )
    assert result.contracts[0].completion_marker is None
