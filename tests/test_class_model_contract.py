import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import extractor


def _class_with_operation() -> dict:
    return {
        "className": "OrderController",
        "stereotype": "Control",
        "methods": ["outdated()"],
        "use_case_ids": ["UC1"],
        "operations": [
            {
                "operationId": "OrderController::placeOrder(orderRequest:OrderRequest)",
                "name": "placeOrder",
                "parameters": [{"name": "orderRequest", "type": "OrderRequest"}],
                "returnType": "Order",
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


def test_accepted_class_operations_mirror_legacy_methods():
    model = BCEModel.model_validate(
        {"Classes": [_class_with_operation()], "Relationships": []}
    )

    assert model.Classes[0].methods == [
        "placeOrder(orderRequest : OrderRequest): Order"
    ]
    dumped = model.model_dump(by_alias=True)
    assert dumped["Classes"][0]["operations"][0]["inputBindings"] == [
        {
            "useCaseId": "UC1",
            "parameter": "orderRequest",
            "sourceRef": "UC1:main:1#orderRequest",
        }
    ]


@pytest.mark.parametrize("name", ["UnknownClass", "Unknown Class", "UnknownClass12"])
def test_accepted_class_rejects_unknown_class_placeholders(name: str):
    with pytest.raises(ValidationError, match="concrete BCE class"):
        BCEModel.model_validate(
            {
                "Classes": [
                    {
                        "className": name,
                        "stereotype": "Control",
                        "use_case_ids": ["UC1"],
                    }
                ]
            }
        )


def test_extraction_boundary_persists_aliases_and_empty_operations(monkeypatch):
    captured: dict = {}

    def parsed(_messages, schema):
        captured["schema"] = schema
        return {
            "Classes": [
                {
                    "class_name": "Order",
                    "stereotype": "Entity",
                    "use_case_ids": ["UC1"],
                }
            ],
            "Relationships": [
                {
                    "source": "Order",
                    "target": "Order",
                    "source_multiplicity": "1",
                    "target_multiplicity": "*",
                }
            ],
        }

    monkeypatch.setattr(extractor, "parse_structured", parsed)

    result = extractor.run_bce_parse([])

    assert captured["schema"] is BCEModel
    assert result["Classes"][0]["className"] == "Order"
    assert result["Classes"][0]["operations"] == []
    assert result["Relationships"][0]["sourceMultiplicity"] == "1"


def test_extraction_boundary_rejects_non_contract_fields(monkeypatch):
    monkeypatch.setattr(
        extractor,
        "parse_structured",
        lambda _messages, _schema: {
            "Classes": [
                {
                    "className": "Order",
                    "stereotype": "Entity",
                    "use_case_ids": ["UC1"],
                    "unexpected": True,
                }
            ]
        },
    )

    with pytest.raises(ValidationError, match="unexpected"):
        extractor.run_bce_parse([])
