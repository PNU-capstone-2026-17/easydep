"""Public contracts for the small functional-plan testing flow."""

from __future__ import annotations

import importlib

import httpx
import pytest
from pydantic import ValidationError

from app.testing.schemas.functional_plan import (
    FunctionalTestCase,
    FunctionalTestPlan,
)
from app.testing.utils.functional_executor import (
    UpstreamAmbiguity,
    execute_functional_plan,
    operation_for_id,
)


def _case(*, step_id: str = "create", operation_id: str = "createOrder") -> dict:
    return {
        "case_id": "case-order",
        "requirement_ids": ["FR-1"],
        "use_case_id": "UC-1",
        "steps": [{"step_id": step_id, "operation_id": operation_id}],
    }


def test_functional_plan_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FunctionalTestCase.model_validate({**_case(), "path": "/orders"})

    with pytest.raises(ValidationError):
        FunctionalTestPlan.model_validate({"cases": [_case()], "target_url": "http://app"})


def test_functional_plan_rejects_duplicate_step_ids() -> None:
    value = _case()
    value["steps"] = [
        {"step_id": "create", "operation_id": "createOrder"},
        {"step_id": "create", "operation_id": "getOrder"},
    ]

    with pytest.raises(ValidationError):
        FunctionalTestCase.model_validate(value)


def test_operation_id_resolves_to_exact_path_and_method() -> None:
    document = {
        "paths": {
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "responses": {
                        "201": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                },
                "get": {
                    "operationId": "createOrderSummary",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                },
            }
        }
    }

    operation = operation_for_id(document, "createOrder", use_case_id="UC-1")

    assert operation.path == "/orders"
    assert operation.method == "POST"


def test_executor_builds_schema_requests_and_passes_only_unique_previous_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "components": {
            "schemas": {
                "CreateOrderRequest": {
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                    "required": ["description"],
                },
                "CreatedOrder": {
                    "type": "object",
                    "properties": {
                        "orderId": {"type": "string"},
                        "status": {"type": "string"},
                    },
                    "required": ["orderId", "status"],
                },
                "ConfirmOrderRequest": {
                    "type": "object",
                    "properties": {
                        "orderId": {"type": "string"},
                        "confirmationCode": {"type": "string"},
                    },
                    "required": ["orderId", "confirmationCode"],
                },
                "Confirmation": {
                    "type": "object",
                    "properties": {"confirmed": {"type": "boolean"}},
                    "required": ["confirmed"],
                },
            }
        },
        "paths": {
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateOrderRequest"}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CreatedOrder"}
                                }
                            }
                        }
                    },
                }
            },
            "/orders/{orderId}/confirm": {
                "post": {
                    "operationId": "confirmOrder",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ConfirmOrderRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Confirmation"}
                                }
                            }
                        }
                    },
                }
            },
        },
    }
    plan = FunctionalTestCase(
        case_id="case-order",
        requirement_ids=["FR-1"],
        use_case_id="UC-1",
        steps=[
            {"step_id": "create", "operation_id": "createOrder"},
            {"step_id": "confirm", "operation_id": "confirmOrder"},
        ],
    )
    responses = iter(
        [
            httpx.Response(201, json={"orderId": "order-42", "status": "created"}),
            httpx.Response(200, json={"confirmed": True}),
        ]
    )
    requests: list[tuple[str, str, dict]] = []

    def fake_transport(method: str, url: str, **kwargs: object) -> httpx.Response:
        requests.append((method, url, kwargs))
        return next(responses)

    monkeypatch.setattr(httpx, "request", fake_transport)

    result = execute_functional_plan(plan, openapi=document, target_url="http://app.test")

    assert result["gateStatus"] == "PASS"
    assert requests[0][0:2] == ("POST", "http://app.test/orders")
    assert set(requests[0][2]["json"]) == {"description"}
    assert requests[1][1] == "http://app.test/orders/order-42/confirm"
    assert requests[1][2]["json"]["orderId"] == "order-42"
    assert set(requests[1][2]["json"]) == {"orderId", "confirmationCode"}
    assert result["steps"][1]["inputSources"]["path.orderId"] == "previous-response"
    assert result["steps"][1]["inputSources"]["body.orderId"] == "previous-response"


def test_one_failed_case_is_reported_without_rerunning_another_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dynamic_module = importlib.import_module("app.testing.nodes.dynamic_functional")
    document = {
        "paths": {
            "/one": {
                "get": {
                    "operationId": "runOne",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "boolean"}}}}
                    },
                }
            },
            "/two": {
                "get": {
                    "operationId": "runTwo",
                    "x-easydep-use-case-ids": ["UC-2"],
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "boolean"}}}}
                    },
                }
            },
        }
    }
    plan = {
        "cases": [
            {
                "case_id": "UC-1",
                "requirement_ids": ["FR-1"],
                "use_case_id": "UC-1",
                "steps": [{"step_id": "one", "operation_id": "runOne"}],
            },
            {
                "case_id": "UC-2",
                "requirement_ids": ["FR-1"],
                "use_case_id": "UC-2",
                "steps": [{"step_id": "two", "operation_id": "runTwo"}],
            },
        ]
    }
    calls: list[str] = []

    def fake_execute(case: FunctionalTestCase, **_: object) -> dict[str, str]:
        calls.append(case.case_id)
        if case.case_id == "UC-1":
            return {
                "status": "failed",
                "gateStatus": "FAIL",
                "reason": "broken app",
                "defectClass": "SUT_DEFECT",
            }
        return {"status": "passed", "gateStatus": "PASS"}

    monkeypatch.setattr(dynamic_module, "execute_functional_plan", fake_execute)
    result = dynamic_module.dynamic_functional_node(
        {
            "run_id": "run-1",
            "app_id": "app-1",
            "target_url": "http://app.test",
            "testing_input": {
                "contract_artifacts": {
                    "requirements": {"content": [{"id": "FR-1", "type": "functional"}]},
                    "use_cases": {
                        "content": {
                            "use_case_specs": [
                                {"use_case_id": "UC-1", "requirement_ids": ["FR-1"]},
                                {"use_case_id": "UC-2", "requirement_ids": ["FR-1"]},
                            ]
                        }
                    },
                    "openapi": {"content": document},
                }
            },
            "fixed_test_plan": plan,
        }
    )

    report = result["dynamic_functional_report"]
    assert calls == ["UC-1", "UC-2"]
    assert [item["caseId"] for item in report["cases"]] == ["UC-1", "UC-2"]
    assert report["gateStatus"] == "FAIL"
    assert report["cases"][0]["result"]["reason"] == "broken app"


def test_missing_or_ambiguous_operation_is_upstream_ambiguity() -> None:
    missing = {"paths": {}}
    ambiguous = {
        "paths": {
            "/one": {
                "get": {
                    "operationId": "sameOperation",
                    "x-easydep-use-case-ids": ["UC-1"],
                }
            },
            "/two": {
                "post": {
                    "operationId": "sameOperation",
                    "x-easydep-use-case-ids": ["UC-1"],
                }
            },
        }
    }

    with pytest.raises(UpstreamAmbiguity):
        operation_for_id(missing, "missingOperation", use_case_id="UC-1")
    with pytest.raises(UpstreamAmbiguity):
        operation_for_id(ambiguous, "sameOperation", use_case_id="UC-1")
