"""One type contract must drive design, OpenAPI, implementation, SQL, and Testing."""
from __future__ import annotations

import httpx
import pytest

from app.design.contracts.api_spec import ApiSpecProposal
from app.design.contracts.type_system import (
    api_type_for_design,
    canonical_design_type,
    java_type_for_design,
    openapi_schema_for_type,
    sql_type_for_design,
)
from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.normalization import (
    interaction_contracts,
    normalize_api_spec_model,
)
from app.design.services.api_spec.projection import build_openapi_from_model
from app.design.services.class_diagram.operations import operation_prompt
from app.design.services.class_diagram.type_system import (
    type_is_resolved,
    types_compatible,
)
from app.design.services.erd.projection import project_logical_model
from app.testing.schemas.functional_plan import FunctionalTestCase
from app.testing.utils.functional_executor import execute_functional_plan


@pytest.mark.parametrize(
    ("source", "canonical", "java", "api", "openapi", "sql"),
    [
        ("object", "Object", "Object", "object", {"type": "object"}, None),
        ("biginteger", "BigInteger", "BigInteger", "BigInteger", {"type": "integer"}, "DECIMAL(38,0)"),
        ("str", "String", "String", "string", {"type": "string"}, "VARCHAR(255)"),
        ("bool", "boolean", "Boolean", "boolean", {"type": "boolean"}, "BOOLEAN"),
        ("byte", "byte", "Byte", "byte", {"type": "integer", "format": "int32"}, "TINYINT"),
        ("char", "char", "Character", "string", {"type": "string"}, "CHAR(1)"),
        ("integer", "int", "Integer", "integer", {"type": "integer", "format": "int32"}, "INT"),
        ("short", "short", "Short", "short", {"type": "integer", "format": "int32"}, "SMALLINT"),
        ("long", "long", "Long", "long", {"type": "integer", "format": "int64"}, "BIGINT"),
        ("decimal", "BigDecimal", "BigDecimal", "number", {"type": "number"}, "DECIMAL(19,4)"),
        ("float", "float", "Float", "float", {"type": "number", "format": "float"}, "FLOAT"),
        ("double", "double", "Double", "double", {"type": "number", "format": "double"}, "DOUBLE"),
        ("uuid", "UUID", "UUID", "uuid", {"type": "string", "format": "uuid"}, "UUID"),
        ("date", "LocalDate", "LocalDate", "date", {"type": "string", "format": "date"}, "DATE"),
        ("datetime", "LocalDateTime", "LocalDateTime", "date-time", {"type": "string", "format": "date-time"}, "TIMESTAMP"),
        ("timestamp", "Instant", "Instant", "date-time", {"type": "string", "format": "date-time"}, "TIMESTAMP WITH TIME ZONE"),
        ("offsetdatetime", "OffsetDateTime", "OffsetDateTime", "date-time", {"type": "string", "format": "date-time"}, "TIMESTAMP WITH TIME ZONE"),
        ("zoneddatetime", "ZonedDateTime", "ZonedDateTime", "date-time", {"type": "string", "format": "date-time"}, "TIMESTAMP WITH TIME ZONE"),
        ("time", "LocalTime", "LocalTime", "string", {"type": "string"}, "TIME"),
        ("byte[]", "byte[]", "byte[]", "binary", {"type": "string", "format": "byte"}, "BLOB"),
    ],
)
def test_scalar_projection_matrix_is_derived_from_one_contract(
    source: str,
    canonical: str,
    java: str,
    api: str,
    openapi: dict[str, object],
    sql: str | None,
) -> None:
    assert canonical_design_type(source) == canonical
    assert java_type_for_design(source, declared_types=set()) == java
    assert api_type_for_design(source) == api
    assert openapi_schema_for_type(api, declared_types=set()) == openapi
    if sql is None:
        with pytest.raises(ValueError, match="cannot be stored in one SQL column"):
            sql_type_for_design(source)
    else:
        assert sql_type_for_design(source) == sql


def test_aliases_and_nested_containers_have_canonical_semantics() -> None:
    assert types_compatible("List<Decimal>", "array<BigDecimal>")
    assert canonical_design_type("Optional[Set<str>]") == "Optional<Set<String>>"
    assert java_type_for_design(
        "Optional<Set<decimal>>", declared_types=set()
    ) == "Optional<Set<BigDecimal>>"
    assert api_type_for_design("Optional<Set<decimal>>") == "number[]"
    assert openapi_schema_for_type("number[]", declared_types=set()) == {
        "type": "array",
        "items": {"type": "number"},
    }


def test_unsupported_or_unresolved_types_fail_before_code_generation() -> None:
    assert canonical_design_type("void") == "void"
    assert java_type_for_design("void", declared_types=set()) == "void"
    with pytest.raises(ValueError, match="void cannot be projected"):
        openapi_schema_for_type("void", declared_types=set())
    assert not type_is_resolved("Map<String, String>", set(), allow_void=False)
    assert not type_is_resolved("MissingType", set(), allow_void=False)
    with pytest.raises(ValueError, match="not a declared Class or DataType"):
        java_type_for_design("MissingType", declared_types=set())
    with pytest.raises(ValueError, match="has no declared schema"):
        openapi_schema_for_type("MissingType", declared_types=set())
    with pytest.raises(ValueError, match="must name a declared Class or DataType"):
        BCEModel.model_validate(
            {
                "Classes": [
                    {
                        "className": "BrokenEntity",
                        "stereotype": "Entity",
                        "fields": ["value : MissingType"],
                    }
                ]
            }
        )


def test_declared_enum_and_value_object_have_deterministic_sql_storage() -> None:
    model = BCEModel.model_validate(
        {
            "Classes": [
                {
                    "className": "Order",
                    "stereotype": "Entity",
                    "fields": [
                        "id : UUID",
                        "status : OrderStatus",
                        "details : OrderDetails",
                        "history : List<OrderStatus>",
                    ],
                    "identifier": ["id"],
                }
            ],
            "DataTypes": [
                {
                    "name": "OrderStatus",
                    "kind": "enumeration",
                    "values": ["CREATED"],
                },
                {
                    "name": "OrderDetails",
                    "kind": "valueObject",
                    "fields": ["amount : decimal"],
                },
            ],
        }
    )

    logical = project_logical_model(model)
    order = next(table for table in logical["Tables"] if table["name"] == "Order")
    columns = {column["name"]: column["type"] for column in order["columns"]}
    history = next(
        table for table in logical["Tables"] if table["name"] == "OrderHistory"
    )

    assert columns == {
        "id": "UUID",
        "status": "VARCHAR(255)",
        "details": "JSON",
    }
    assert next(
        column["type"]
        for column in history["columns"]
        if column["name"] == "history_value"
    ) == "VARCHAR(255)"


def _calculator_bce() -> BCEModel:
    return BCEModel.model_validate(
        {
            "Classes": [
                {
                    "className": "CalculationBoundary",
                    "stereotype": "Boundary",
                    "use_case_ids": ["UC1"],
                    "operations": [
                        {
                            "operationId": "proposal",
                            "name": "performCalculation",
                            "parameters": [
                                {"name": "firstOperand", "type": "decimal"},
                                {"name": "secondOperand", "type": "decimal"},
                            ],
                            "returnType": "decimal",
                            "stepRefs": ["UC1:main:1"],
                        }
                    ],
                },
                {
                    "className": "CalculationControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC1"],
                    "operations": [
                        {
                            "operationId": "proposal",
                            "name": "performCalculation",
                            "parameters": [
                                {"name": "firstOperand", "type": "decimal"},
                                {"name": "secondOperand", "type": "decimal"},
                            ],
                            "returnType": "decimal",
                            "stepRefs": ["UC1:main:1"],
                        }
                    ],
                },
            ],
            "DataTypes": [],
            "Relationships": [],
            "Collaborations": [
                {
                    "collaborationId": "UC1",
                    "useCaseIds": ["UC1"],
                    "entryActor": "User",
                    "calls": [
                        {
                            "callId": "proposal",
                            "receiverOperationId": (
                                "CalculationBoundary::performCalculation("
                                "firstOperand:decimal,secondOperand:decimal)"
                            ),
                            "stepRefs": ["UC1:main:1"],
                        },
                        {
                            "callId": "proposal",
                            "parentCallId": "UC1::call:1",
                            "receiverOperationId": (
                                "CalculationControl::performCalculation("
                                "firstOperand:decimal,secondOperand:decimal)"
                            ),
                            "stepRefs": ["UC1:main:1"],
                        },
                    ],
                }
            ],
        }
    )


def test_decimal_request_stays_numeric_through_testing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bce = _calculator_bce()
    assert [
        parameter.type for parameter in bce.Classes[0].operations[0].parameters
    ] == ["BigDecimal", "BigDecimal"]
    assert "firstOperand:BigDecimal" in bce.Collaborations[0].calls[0].receiver_operation_id
    interaction = interaction_contracts(bce)[0].interaction_id
    api_model = normalize_api_spec_model(
        ApiSpecProposal.model_validate(
            {
                "Endpoints": [
                    {
                        "interaction_id": interaction,
                        "path": "/calculations",
                        "method": "post",
                        "summary": "Perform a calculation.",
                        "responses": [{"status": 200, "description": "Result"}],
                    }
                ]
            }
        ),
        bce,
    )
    openapi = build_openapi_from_model(api_model)
    request_name = api_model.Endpoints[0].request_schema
    request_schema = openapi["components"]["schemas"][request_name]
    assert request_schema["properties"]["firstOperand"] == {"type": "number"}
    assert request_schema["properties"]["secondOperand"] == {"type": "number"}

    sent: list[object] = []

    def request(_method: str, _url: str, **kwargs: object) -> httpx.Response:
        sent.append(kwargs["json"])
        return httpx.Response(200, json=4.0)

    monkeypatch.setattr(httpx, "request", request)
    proposed = iter([1.5, 2.5])
    result = execute_functional_plan(
        FunctionalTestCase.model_validate(
            {
                "case_id": "calculator",
                "requirement_ids": ["FR1"],
                "use_case_id": "UC1",
                "steps": [
                    {"step_id": "calculate", "operation_id": "performCalculation"}
                ],
            }
        ),
        openapi=openapi,
        target_url="http://app.test",
        propose_input=lambda _request: next(proposed),
    )

    assert result["gateStatus"] == "PASS"
    assert sent == [{"firstOperand": 1.5, "secondOperand": 2.5}]
    assert all(value != {} for value in sent[0].values())


def test_optional_response_fields_accept_explicit_json_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _calculator_bce().model_dump(by_alias=True)
    for accepted_class in payload["Classes"]:
        accepted_class["operations"][0]["returnType"] = "CalculationResponse"
    payload["DataTypes"] = [
        {
            "name": "CalculationResponse",
            "kind": "valueObject",
            "fields": [
                "success : boolean",
                "result : Optional<double>",
                "errorMessage : Optional<String>",
            ],
            "values": [],
        }
    ]
    bce = BCEModel.model_validate(payload)
    interaction = interaction_contracts(bce)[0].interaction_id
    api_model = normalize_api_spec_model(
        ApiSpecProposal.model_validate(
            {
                "Endpoints": [
                    {
                        "interaction_id": interaction,
                        "path": "/calculations",
                        "method": "post",
                        "summary": "Perform a calculation.",
                        "responses": [{"status": 200, "description": "Result"}],
                    }
                ]
            }
        ),
        bce,
    )
    openapi = build_openapi_from_model(api_model)
    response_schema = openapi["components"]["schemas"]["CalculationResponse"]
    assert response_schema["required"] == ["success"]
    assert response_schema["properties"]["errorMessage"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }

    monkeypatch.setattr(
        httpx,
        "request",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={"success": True, "result": 84.0, "errorMessage": None},
        ),
    )
    proposed = iter([6, 14])
    result = execute_functional_plan(
        FunctionalTestCase.model_validate(
            {
                "case_id": "calculator-nullable-response",
                "requirement_ids": ["FR1"],
                "use_case_id": "UC1",
                "steps": [
                    {"step_id": "calculate", "operation_id": "performCalculation"}
                ],
            }
        ),
        openapi=openapi,
        target_url="http://app.test",
        propose_input=lambda _request: next(proposed),
    )

    assert result["gateStatus"] == "PASS"
    assert "absent in any outcome as Optional<T>" in operation_prompt()
