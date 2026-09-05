"""Public contracts for the small functional-plan testing flow."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import httpx
import pytest
from pydantic import ValidationError

from app.testing.nodes.dynamic_functional import build_functional_cases
from app.testing.schemas.functional_plan import (
    FunctionalInputValue,
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


def _post_input_openapi(schema: dict, *, summary: str = "") -> dict:
    """입력 생성 검사는 달라지는 schema만 읽히도록 공통 HTTP 계약을 줄인다."""

    operation = {
        "operationId": "createOrder",
        "x-easydep-use-case-ids": ["UC-1"],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": schema}},
        },
        "responses": {
            "201": {
                "content": {"application/json": {"schema": {"type": "boolean"}}}
            }
        },
    }
    if summary:
        operation["summary"] = summary
    return {"paths": {"/orders": {"post": operation}}}


def test_functional_plan_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FunctionalTestCase.model_validate({**_case(), "path": "/orders"})

    with pytest.raises(ValidationError):
        FunctionalTestPlan.model_validate({"cases": [_case()], "target_url": "http://app"})


def test_functional_plan_direct_sdk_schema_is_strict_and_english_only() -> None:
    dynamic_module = importlib.import_module("app.testing.nodes.dynamic_functional")
    schema = dynamic_module._response_format()["json_schema"]["schema"]

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert set(value.get("properties", ())) == set(value.get("required", ()))
                assert value["additionalProperties"] is False
            description = value.get("description")
            if isinstance(description, str):
                assert description.isascii()
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(schema)


def test_functional_plan_rejects_duplicate_step_ids() -> None:
    value = _case()
    value["steps"] = [
        {"step_id": "create", "operation_id": "createOrder"},
        {"step_id": "create", "operation_id": "getOrder"},
    ]

    with pytest.raises(ValidationError):
        FunctionalTestCase.model_validate(value)


def test_functional_cases_follow_realization_edges_and_skip_unscoped_policy() -> None:
    requirements = [
        {"id": "FR-1", "type": "FR"},
        {"id": "FR-2", "type": "FR"},
        {"id": "FR-POLICY", "type": "FR"},
    ]
    use_cases = {
        "use_case_specs": [
            {
                "use_case_id": "UC-1",
                "name": "List orders",
                "requirement_ids": ["FR-1"],
                "preconditions": ["The customer is authenticated."],
                "trigger": "The customer requests the order list.",
                "main_scenario": [
                    {"step_number": 1, "sentence": "System returns the customer's orders."}
                ],
            }
        ],
        "traceability": {
            "requirements": {
                "FR-1": {"use_cases": ["UC-1"], "modeled_as_constraint": False},
                "FR-2": {
                    "realized_by_use_cases": ["UC-1"],
                    "modeled_as_constraint": False,
                },
                "FR-POLICY": {"modeled_as_constraint": True},
            }
        },
    }
    openapi = {
        "paths": {
            "/orders": {
                "get": {
                    "operationId": "listOrders",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"type": "boolean"}}
                            }
                        }
                    },
                }
            }
        }
    }

    cases = build_functional_cases(requirements, use_cases, openapi)

    assert cases[0]["requirement_ids"] == ["FR-1", "FR-2"]
    assert cases[0]["allowed_operation_ids"] == ["listOrders"]
    assert cases[0]["use_case_flow"] == {
        "name": "List orders",
        "preconditions": ["The customer is authenticated."],
        "trigger": "The customer requests the order list.",
        "main_scenario": [
            {"step_number": 1, "sentence": "System returns the customer's orders."}
        ],
    }


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
            "/orders/{orderId}/archive": {
                "post": {
                    "operationId": "archiveOrder",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"204": {"description": "Order archived"}},
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
            {"step_id": "archive", "operation_id": "archiveOrder"},
        ],
    )
    responses = iter(
        [
            httpx.Response(201, json={"orderId": "order-42", "status": "created"}),
            httpx.Response(200, json={"confirmed": True}),
            httpx.Response(204),
        ]
    )
    requests: list[tuple[str, str, dict]] = []

    def fake_transport(method: str, url: str, **kwargs: object) -> httpx.Response:
        requests.append((method, url, kwargs))
        return next(responses)

    monkeypatch.setattr(httpx, "request", fake_transport)

    proposals: list[str] = []

    def propose(request: object) -> str:
        location = str(getattr(request, "location"))
        proposals.append(location)
        return {
            "body.description": "A valid order",
            "body.confirmationCode": "CONFIRM-1",
        }[location]

    result = execute_functional_plan(
        plan,
        openapi=document,
        target_url="http://app.test",
        propose_input=propose,
    )

    assert result["gateStatus"] == "PASS"
    assert requests[0][0:2] == ("POST", "http://app.test/orders")
    assert set(requests[0][2]["json"]) == {"description"}
    assert requests[1][1] == "http://app.test/orders/order-42/confirm"
    assert requests[1][2]["json"]["orderId"] == "order-42"
    assert set(requests[1][2]["json"]) == {"orderId", "confirmationCode"}
    assert proposals == ["body.description", "body.confirmationCode"]
    assert result["steps"][1]["inputSources"]["path.orderId"] == "previous-response"
    assert result["steps"][1]["inputSources"]["body.orderId"] == "previous-response"
    assert requests[2][0:2] == ("POST", "http://app.test/orders/order-42/archive")
    assert result["steps"][2]["statusCode"] == 204


def test_executor_uses_openapi_examples_and_bounds_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _post_input_openapi(
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["ready"]},
                "requestedOn": {"type": "string", "format": "date"},
                "quantity": {"type": "integer", "minimum": 2, "maximum": 5},
                "note": {"type": "string", "example": "Handle gently"},
            },
            "required": ["status", "requestedOn", "quantity", "note"],
        }
    )
    sent: list[dict] = []

    def fake_transport(_method: str, _url: str, **kwargs: object) -> httpx.Response:
        sent.append(kwargs["json"])  # type: ignore[arg-type]
        return httpx.Response(201, json=True)

    monkeypatch.setattr(httpx, "request", fake_transport)
    result = execute_functional_plan(
        FunctionalTestCase.model_validate(_case()),
        openapi=document,
        target_url="http://app.test",
        propose_input=lambda _request: pytest.fail("OpenAPI already contains enough evidence"),
    )

    assert result["gateStatus"] == "PASS"
    assert sent == [
        {
            "status": "ready",
            "requestedOn": "2026-01-02",
            "quantity": 2,
            "note": "Handle gently",
        }
    ]
    assert result["inputValues"] == []


def test_array_without_success_evidence_asks_for_only_that_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """근거 없는 배열을 빈 값으로 단정하지 않고 배열 하나만 제안받는다."""

    document = _post_input_openapi(
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["SUM"]},
                "values": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["mode", "values"],
        },
        summary="Apply the selected operation to the supplied values.",
    )
    sent: list[dict] = []
    requested: list[object] = []

    def fake_transport(_method: str, _url: str, **kwargs: object) -> httpx.Response:
        sent.append(kwargs["json"])  # type: ignore[arg-type]
        return httpx.Response(201, json=True)

    def propose(request: object) -> list[int]:
        requested.append(request)
        return [2, 3]

    monkeypatch.setattr(httpx, "request", fake_transport)
    result = execute_functional_plan(
        FunctionalTestCase.model_validate(_case()),
        openapi=document,
        target_url="http://app.test",
        propose_input=propose,
    )

    assert result["gateStatus"] == "PASS"
    assert sent == [{"mode": "SUM", "values": [2, 3]}]
    assert len(requested) == 1
    assert getattr(requested[0], "location") == "body.values"
    assert result["inputValues"] == [
        {
            "operation_id": "createOrder",
            "location": "body.values",
            "value": [2, 3],
        }
    ]


def test_executor_preserves_one_llm_leaf_value_across_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _post_input_openapi(
        {
            "type": "object",
            "properties": {
                "description": {"type": "string", "minLength": 5, "maxLength": 30}
            },
            "required": ["description"],
        },
        summary="Create an order from the customer's description.",
    )
    sent: list[dict] = []

    def fake_transport(_method: str, _url: str, **kwargs: object) -> httpx.Response:
        sent.append(kwargs["json"])  # type: ignore[arg-type]
        return httpx.Response(201, json=True)

    monkeypatch.setattr(httpx, "request", fake_transport)
    plan = FunctionalTestCase.model_validate(_case())
    contexts: list[str] = []

    def propose(request: object) -> str:
        contexts.append(str(getattr(request, "operation_context")))
        return "First stable value"

    first = execute_functional_plan(
        plan,
        openapi=document,
        target_url="http://app.test",
        propose_input=propose,
    )
    preserved = [FunctionalInputValue.model_validate(item) for item in first["inputValues"]]
    second = execute_functional_plan(
        plan,
        openapi=document,
        target_url="http://app.test",
        propose_input=lambda _request: pytest.fail("Repair must reuse the first input"),
        preserved_inputs=preserved,
    )

    assert first["gateStatus"] == second["gateStatus"] == "PASS"
    assert contexts == ["Create an order from the customer's description."]
    assert sent == [
        {"description": "First stable value"},
        {"description": "First stable value"},
    ]
    assert second["inputValues"] == first["inputValues"]


def test_invalid_llm_leaf_is_a_test_defect_without_calling_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _post_input_openapi(
        {
            "type": "object",
            "properties": {"quantity": {"type": "integer"}},
            "required": ["quantity"],
        }
    )
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *_args, **_kwargs: pytest.fail("Invalid test input must not reach the app"),
    )

    result = execute_functional_plan(
        FunctionalTestCase.model_validate(_case()),
        openapi=document,
        target_url="http://app.test",
        propose_input=lambda _request: "not-an-integer",
    )

    assert result["gateStatus"] == "FAIL"
    assert result["defectClass"] == "TEST_DEFECT"
    assert result["finding"]["code"] == "TEST_INPUT_INVALID"


def test_dynamic_repair_reuses_candidate_input_values_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dynamic_module = importlib.import_module("app.testing.nodes.dynamic_functional")
    openapi = _post_input_openapi(
        {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        }
    )
    fixed_plan = {
        "cases": [
            {
                "case_id": "UC-1",
                "requirement_ids": ["FR-1"],
                "use_case_id": "UC-1",
                "steps": [{"step_id": "create", "operation_id": "createOrder"}],
            }
        ],
        "inputValues": {
            "UC-1": [
                {
                    "operation_id": "createOrder",
                    "location": "body.description",
                    "value": "Preserved order",
                }
            ]
        },
    }
    sent: list[dict] = []

    def fake_transport(_method: str, _url: str, **kwargs: object) -> httpx.Response:
        sent.append(kwargs["json"])  # type: ignore[arg-type]
        return httpx.Response(201, json=True)

    monkeypatch.setattr(httpx, "request", fake_transport)
    monkeypatch.setattr(
        dynamic_module,
        "OpenAI",
        lambda **_kwargs: pytest.fail("A repair with preserved values must not call the LLM"),
    )
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
                                {
                                    "use_case_id": "UC-1",
                                    "name": "Create order",
                                    "requirement_ids": ["FR-1"],
                                }
                            ],
                            "traceability": {"requirements": {}},
                        }
                    },
                    "openapi": {"content": openapi},
                }
            },
            "fixed_test_plan": fixed_plan,
        }
    )

    report = result["dynamic_functional_report"]
    assert report["gateStatus"] == "PASS"
    assert report["candidatePlan"] == fixed_plan
    assert sent == [{"description": "Preserved order"}]


def test_implementation_repair_check_reuses_the_same_leaf_input(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenHands 안의 재검사도 최초 Testing의 leaf 값을 그대로 사용한다."""

    repair_check = importlib.import_module("app.testing.repair_check")
    captured: dict[str, object] = {}

    @contextmanager
    def running(*_args, **_kwargs):
        yield "http://app.test", {}

    def execute(_case, **kwargs):
        captured["inputs"] = kwargs["preserved_inputs"]
        return {"status": "passed", "gateStatus": "PASS", "steps": []}

    monkeypatch.setattr(repair_check, "running_application", running)
    monkeypatch.setattr(repair_check, "execute_functional_plan", execute)
    profile = {
        "openapi": {},
        "case_id": "case-order",
        "candidate_plan": {
            "cases": [_case()],
            "inputValues": {
                "case-order": [
                    {
                        "operation_id": "createOrder",
                        "location": "body.description",
                        "value": "Preserved order",
                    }
                ]
            },
        },
    }

    result = repair_check.verify_testing_repair_gate(
        tmp_path,
        "testing-dynamic-functional",
        profile,
    )

    assert result["gateStatus"] == "PASS"
    assert [item.value for item in captured["inputs"]] == ["Preserved order"]


def test_schema_generated_4xx_requests_test_profile_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "paths": {
            "/orders/{orderId}": {
                "get": {
                    "operationId": "getOrder",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"type": "boolean"}}
                            }
                        }
                    },
                }
            }
        }
    }
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *_args, **_kwargs: httpx.Response(404, json={"message": "not found"}),
    )

    result = execute_functional_plan(
        FunctionalTestCase.model_validate(
            {
                **_case(operation_id="getOrder"),
                "use_case_id": "UC-1",
            }
        ),
        openapi=document,
        target_url="http://app.test",
    )

    assert result["defectClass"] == "TEST_DEFECT"
    assert result["finding"]["code"] == "TEST_PROFILE_DATA_UNAVAILABLE"
    assert result["finding"]["generatedInputs"] == ["path.orderId"]


def test_generated_request_body_does_not_hide_a_product_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM이 body 값을 만들었어도 애플리케이션의 일반 400은 제품 실패로 남긴다."""

    document = _post_input_openapi(
        {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        }
    )
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *_args, **_kwargs: httpx.Response(400, json={"message": "rejected"}),
    )

    result = execute_functional_plan(
        FunctionalTestCase.model_validate(_case()),
        openapi=document,
        target_url="http://app.test",
        propose_input=lambda _request: "A valid order",
    )

    assert result["defectClass"] == "SUT_DEFECT"
    assert result["finding"]["code"] == "HTTP_STATUS_NOT_SUCCESS"


def test_all_cases_are_reported_when_one_fails(
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
                "steps": [
                    {"step_id": "two", "operation_id": "runTwo"},
                    {"step_id": "two-again", "operation_id": "runTwo"},
                ],
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
                    "requirements": {
                        "content": [
                            {"id": "FR-1", "type": "functional"},
                            {"id": "FR-POLICY", "type": "functional"},
                        ]
                    },
                    "use_cases": {
                        "content": {
                            "use_case_specs": [
                                {"use_case_id": "UC-1", "requirement_ids": ["FR-1"]},
                                {"use_case_id": "UC-2", "requirement_ids": ["FR-1"]},
                            ],
                            "traceability": {
                                "requirements": {
                                    "FR-POLICY": {"modeled_as_constraint": True}
                                }
                            },
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
    assert report["caseId"] == "UC-1"
    assert [item["caseId"] for item in report["cases"]] == ["UC-1", "UC-2"]
    assert len(report["candidatePlan"]["cases"][1]["steps"]) == 1
    assert report["gateStatus"] == "FAIL"
    assert report["cases"][0]["result"]["reason"] == "broken app"
    assert report["requirements"]["ids"] == []
    assert report["requirements"]["unverifiedIds"] == ["FR-1", "FR-POLICY"]


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
