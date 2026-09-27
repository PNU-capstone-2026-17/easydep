"""Integration tests for Arazzo planning in the dynamic Testing node."""

from __future__ import annotations

import json
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest

from app.testing.nodes import dynamic_functional as dynamic
from app.testing.utils.arazzo_planner import (
    attach_workflow_trace,
    build_arazzo_document,
    build_execution_candidates,
    build_workflow_candidates,
    use_case_display_name,
)
from app.testing.utils.functional_executor import InputValueRequest


def _requirements(count: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "id": f"FR-{index}",
            "type": "FR",
            "statement": f"The user can check service {index}.",
        }
        for index in range(1, count + 1)
    ]


def _use_cases(count: int = 1, *, guarantees: bool = True) -> dict[str, Any]:
    return {
        "use_case_specs": [
            {
                "use_case_id": f"UC-{index}",
                "requirement_ids": [f"FR-{index}"],
                "name": f"Check service {index}",
                "preconditions": [],
                "trigger": "The user requests service status.",
                "main_scenario": [
                    {
                        "step_number": 1,
                        "sentence": "The system returns the service status.",
                    }
                ],
                "success_guarantee": (
                    [
                        {
                            "sentence": "The service reports that it is available.",
                            "covered_req_ids": [f"FR-{index}"],
                        }
                    ]
                    if guarantees
                    else []
                ),
                "minimal_guarantee": [],
            }
            for index in range(1, count + 1)
        ],
        "traceability": {
            "requirements": {
                f"FR-{index}": {"use_cases": [f"UC-{index}"]} for index in range(1, count + 1)
            }
        },
    }


def _openapi() -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Status API", "version": "1.0.0"},
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "x-easydep-use-case-ids": ["UC-1", "UC-2"],
                    "responses": {
                        "200": {
                            "description": "available",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["ok"],
                                        "properties": {"ok": {"type": "boolean"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def _openapi_with_secondary_operation(*use_case_ids: str) -> dict[str, Any]:
    openapi = _openapi()
    openapi["paths"]["/secondary"] = {
        "get": {
            "operationId": "secondaryHealth",
            "x-easydep-use-case-ids": list(use_case_ids or ("UC-1",)),
            "responses": {
                "200": {
                    "description": "secondary available",
                    "content": {
                        "application/json": {"schema": {"type": "object"}}
                    },
                }
            },
        }
    }
    return openapi


def _document(count: int = 1, *, criteria: bool = True) -> dict[str, Any]:
    candidates = build_workflow_candidates(_requirements(count), _use_cases(count), _openapi())
    workflows = []
    for candidate in candidates:
        step: dict[str, Any] = {"stepId": "health", "operationId": "health"}
        if criteria:
            step["successCriteria"] = [{"condition": "$response.body#/ok == true"}]
        workflows.append(
            attach_workflow_trace(
                {
                    "workflowId": candidate["workflowId"],
                    "steps": [step],
                },
                candidate,
            )
        )
    return build_arazzo_document(workflows)


def _state(count: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "app_id": "app-1",
        "target_url": "http://127.0.0.1:8765",
        "testing_input": {
            "contract_artifacts": {
                "requirements": {"content": _requirements(count)},
                "use_cases": {"content": _use_cases(count)},
                "openapi": {"content": _openapi()},
            }
        },
        "fixed_arazzo_document": _document(count),
        "fixed_workflow_inputs": {},
        "fixed_input_values": {},
        "preserved_workflow_results": [],
        "priority_workflow_id": "",
        **extra,
    }


def _pass(workflow_id: str, *, semantic: str = "PASS") -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "status": "passed",
        "gateStatus": "PASS",
        "defectClass": None,
        "steps": [
            {
                "stepId": "health",
                "operationId": "health",
                "contractStatus": "PASS",
                "semanticStatus": semantic,
            }
        ],
        "workflowInputs": {},
        "outputs": {},
        "contractStatus": "PASS",
        "semanticStatus": semantic,
    }


def _two_step_decision_candidate() -> dict[str, Any]:
    connection = {
        "connectionId": "createItem.bodyId->getItem.path:id",
        "sourceStepId": "createItem",
        "sourceSlot": "body.id",
        "outputName": "bodyId",
        "outputExpression": "$response.body#/id",
        "targetStepId": "getItem",
        "targetInputSlot": "path:id",
        "value": "$steps.createItem.outputs.bodyId",
    }
    return {
        "workflowId": "workflow-UC-1",
        "trace": {"requirementIds": [], "useCaseIds": ["UC-1"], "evidenceRefs": []},
        "planningModel": {
            "availableSteps": [
                {
                    "stepId": "createItem",
                    "operationId": "createItem",
                    "successStatuses": ["201"],
                    "inputs": [],
                    "outputs": [{"outputName": "bodyId", "outputExpression": "$response.body#/id"}],
                },
                {
                    "stepId": "getItem",
                    "operationId": "getItem",
                    "successStatuses": ["200"],
                    "inputs": [{"inputSlot": "path:id", "connections": [connection]}],
                    "outputs": [],
                },
            ]
        },
    }


def test_structured_output_is_a_workflow_decision_subset() -> None:
    response_format = dynamic._response_format()
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    jsonschema.Draft202012Validator(schema).validate(
        {
            "workflowId": "workflow-UC-1",
            "orderedStepIds": ["health"],
            "connectionIds": [],
            "successCriteria": [{"stepId": "health", "statusCode": 200}],
        }
    )
    assert "parameters" not in schema["properties"]
    assert "requestBody" not in schema["properties"]
    assert "outputs" not in schema["properties"]
    openapi = _openapi()
    candidate = build_workflow_candidates(_requirements(), _use_cases(), openapi)[0]
    candidate["planningModel"] = dynamic._planning_model(
        candidate, build_execution_candidates([candidate], openapi)
    )
    prompt = dynamic._prompt(
        candidate
    )
    assert "workflow decision JSON" in prompt
    assert "connectionIds" in prompt
    assert '"availableSteps"' in prompt
    assert '"paths"' not in prompt
    assert "trace-linked" in prompt
    assert "FunctionalTestCase" not in prompt
    assert "Testing-stage workflow-planning subtask" in dynamic.PLAN_ROLE_PROMPT
    assert "immutable" in dynamic.PLAN_ROLE_PROMPT


def test_candidate_response_schema_closes_connection_ids_to_supplied_catalog() -> None:
    candidate = _two_step_decision_candidate()
    response_schema = dynamic._response_format(candidate)["json_schema"]["schema"]
    decision = {
        "workflowId": "workflow-UC-1",
        "orderedStepIds": ["createItem", "getItem"],
        "connectionIds": ["createItem.bodyId->getItem.path:id"],
    }

    jsonschema.Draft202012Validator(response_schema).validate(decision)
    with pytest.raises(jsonschema.ValidationError, match="is not one of"):
        jsonschema.Draft202012Validator(response_schema).validate(
            {**decision, "connectionIds": ["invented.connection"]}
        )
    assert dynamic._WORKFLOW_DECISION_SCHEMA["properties"]["connectionIds"]["items"] == {
        "type": "string"
    }


@pytest.mark.parametrize(
    "invalid_field",
    [
        {"successCriteria": [{"condition": "$statusCode == 200"}]},
        {"request": {}},
        {"response": {}},
        {"retry": {}},
    ],
)
def test_structured_output_rejects_non_step_or_non_arazzo_fields(
    invalid_field: dict[str, Any],
) -> None:
    schema = dynamic._response_format()["json_schema"]["schema"]
    workflow = {
        "workflowId": "workflow-UC-1",
        "steps": [{"stepId": "health", "operationId": "health"}],
        **invalid_field,
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(workflow)


def test_workflow_decision_compiles_finite_choices_to_canonical_arazzo() -> None:
    workflow = dynamic._compile_workflow_decision(
        {
            "workflowId": "workflow-UC-1",
            "orderedStepIds": ["createItem", "getItem"],
            "connectionIds": ["createItem.bodyId->getItem.path:id"],
            "successCriteria": [{"stepId": "createItem", "statusCode": 201}],
        },
        _two_step_decision_candidate(),
    )

    assert workflow["steps"] == [
        {
            "stepId": "createItem",
            "operationId": "createItem",
            "outputs": {"bodyId": "$response.body#/id"},
            "successCriteria": [{"condition": "$statusCode == 201"}],
        },
        {
            "stepId": "getItem",
            "operationId": "getItem",
            "parameters": [
                {"name": "id", "in": "path", "value": "$steps.createItem.outputs.bodyId"}
            ],
        },
    ]


@pytest.mark.parametrize(
    ("ordered_step_ids", "connection_ids", "message"),
    [
        (["createItem"], ["missing"], "unknown connection ID"),
        (["getItem", "createItem"], ["createItem.bodyId->getItem.path:id"], "earlier selected step"),
        (
            ["createItem", "getItem"],
            ["createItem.bodyId->getItem.path:id", "createItem.bodyId->getItem.path:id"],
            "multiple connections",
        ),
        (["createItem", "getItem"], ["createItem.bodyId->getItem.path:missing"], "unknown connection ID"),
    ],
)
def test_workflow_decision_rejects_unknown_forward_or_duplicate_connections(
    ordered_step_ids: list[str], connection_ids: list[str], message: str
) -> None:
    with pytest.raises(dynamic.ArazzoPlanningError, match=message):
        dynamic._compile_workflow_decision(
            {
                "workflowId": "workflow-UC-1",
                "orderedStepIds": ordered_step_ids,
                "connectionIds": connection_ids,
            },
            _two_step_decision_candidate(),
        )


def test_workflow_decision_rejects_status_code_not_declared_by_openapi_projection() -> None:
    with pytest.raises(dynamic.ArazzoPlanningError, match="ungrounded success status"):
        dynamic._compile_workflow_decision(
            {
                "workflowId": "workflow-UC-1",
                "orderedStepIds": ["createItem"],
                "connectionIds": [],
                "successCriteria": [{"stepId": "createItem", "statusCode": 202}],
            },
            _two_step_decision_candidate(),
        )


def test_openapi_invalid_literal_parameter_is_deferred_to_input_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi_with_secondary_operation("UC-1")
    operation = openapi["paths"]["/health"]["get"]
    operation["parameters"] = [
        {
            "name": "criteria",
            "in": "query",
            "required": True,
            "schema": {"$ref": "#/components/schemas/SearchCriteria"},
        }
    ]
    openapi["components"] = {
        "schemas": {
            "SearchCriteria": {
                "type": "object",
                "required": ["status"],
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
            }
        }
    }
    candidates = build_workflow_candidates(_requirements(), _use_cases(), openapi)
    calls = 0

    def generate(_client: object, candidate: dict[str, Any], error: str = "") -> dict[str, Any]:
        nonlocal calls
        calls += 1
        assert error == ""
        decision = {
            "workflowId": candidate["workflowId"],
            "orderedStepIds": ["health"],
            "connectionIds": [],
        }
        return dynamic._compile_workflow_decision(decision, candidate)

    monkeypatch.setattr(dynamic, "_generate", generate)

    document = dynamic._generate_document(object(), candidates, openapi)

    assert calls == 1
    assert "parameters" not in document["workflows"][0]["steps"][0]


def test_invalid_response_output_pointer_is_rejected_before_execution() -> None:
    candidate = {
        "workflowId": "workflow-UC-3",
        "requirements": [],
        "useCase": {},
        "operations": [
            {
                "operationId": "requestSwap",
                "responses": [
                    {
                        "status": "200",
                        "schema": {
                            "type": "object",
                            "properties": {"isValid": {"type": "boolean"}},
                        },
                    }
                ],
            }
        ],
        "trace": {"useCaseIds": ["UC3"]},
    }
    document = build_arazzo_document(
        [
            attach_workflow_trace(
                {
                    "workflowId": "workflow-UC-3",
                    "steps": [
                        {
                            "stepId": "swap",
                            "operationId": "requestSwap",
                            "outputs": {"result": "$response.body#/result"},
                        }
                    ],
                },
                candidate,
            )
        ]
    )
    openapi = {
        "openapi": "3.0.3",
        "info": {"title": "API", "version": "1.0.0"},
        "paths": {
            "/swap": {
                "post": {
                    "operationId": "requestSwap",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"isValid": {"type": "boolean"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    with pytest.raises(dynamic.ArazzoValidationError, match="absent from the frozen OpenAPI"):
        dynamic._validate_document(document, [candidate], openapi)


def test_optional_workflow_output_pointer_is_not_classified_as_sut_defect() -> None:
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "API", "version": "1.0.0"},
        "paths": {},
        "components": {
            "schemas": {
                "Offering": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                }
            }
        },
    }
    workflow = {
        "workflowId": "workflow-UC-1",
        "steps": [
            {
                "stepId": "search",
                "operationId": "searchOfferings",
                "outputs": {"firstId": "$response.body#/0/id"},
            }
        ],
    }
    candidate = {
        "workflowId": "workflow-UC-1",
        "operations": [
            {
                "operationId": "searchOfferings",
                "responses": [
                    {
                        "status": "200",
                        "schema": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"$ref": "#/components/schemas/Offering"},
                        },
                    },
                    {
                        "status": "206",
                        "schema": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Offering"},
                        },
                    }
                ],
            }
        ],
    }
    result = {
        "gateStatus": "FAIL",
        "defectClass": "TEST_DEFECT",
        "reason": "JSON Pointer does not resolve: #/0/id",
        "finding": {
            "code": "RUNTIME_EXPRESSION_UNRESOLVED",
            "message": "JSON Pointer does not resolve: #/0/id",
            "stepId": "search",
        },
        "steps": [
            {
                "stepId": "search",
                "statusCode": 206,
                "finding": {"code": "RUNTIME_EXPRESSION_UNRESOLVED"},
            }
        ],
    }

    dynamic._classify_missing_workflow_data(result, workflow, candidate, openapi)

    assert result["defectClass"] == "TEST_DEFECT"
    assert result["finding"]["code"] == "RUNTIME_EXPRESSION_UNRESOLVED"


def test_invented_workflow_output_pointer_remains_test_defect() -> None:
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "API", "version": "1.0.0"},
        "paths": {},
    }
    workflow = {
        "workflowId": "workflow-UC-1",
        "steps": [
            {
                "stepId": "search",
                "operationId": "searchOfferings",
                "outputs": {"invented": "$response.body#/invented"},
            }
        ],
    }
    candidate = {
        "workflowId": "workflow-UC-1",
        "operations": [
            {
                "operationId": "searchOfferings",
                "responses": [
                    {
                        "status": "200",
                        "schema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                        },
                    }
                ],
            }
        ],
    }
    result = {
        "gateStatus": "FAIL",
        "defectClass": "TEST_DEFECT",
        "reason": "JSON Pointer does not resolve: #/invented",
        "finding": {
            "code": "RUNTIME_EXPRESSION_UNRESOLVED",
            "message": "JSON Pointer does not resolve: #/invented",
            "stepId": "search",
        },
    }

    dynamic._classify_missing_workflow_data(result, workflow, candidate, openapi)

    assert result["defectClass"] == "TEST_DEFECT"
    assert result["finding"]["code"] == "RUNTIME_EXPRESSION_UNRESOLVED"


@pytest.mark.parametrize("source_value", [None, []])
def test_empty_required_nested_output_is_classified_as_sut_defect(source_value) -> None:
    workflow = {
        "workflowId": "workflow-UC1",
        "steps": [
            {
                "stepId": "search",
                "operationId": "searchOfferings",
                "outputs": {"offeringsList": "$response.body#/items"},
            },
            {
                "stepId": "details",
                "operationId": "getOffering",
                "parameters": [
                    {
                        "in": "path",
                        "name": "offeringId",
                        "value": "$steps.search.outputs.offeringsList#/0/id",
                    }
                ],
            },
        ],
    }
    result = {
        "gateStatus": "FAIL",
        "defectClass": "TEST_DEFECT",
        "failedStepId": "details",
        "reason": "JSON Pointer does not resolve: #/0/id",
        "finding": {
            "code": "RUNTIME_EXPRESSION_UNRESOLVED",
            "message": "JSON Pointer does not resolve: #/0/id",
            "stepId": "details",
        },
        "steps": [
            {
                "stepId": "search",
                "statusCode": 200,
                "outputs": {"offeringsList": source_value},
            },
            {"stepId": "details", "status": "failed"},
        ],
    }

    candidate = {
        "operations": [
            {
                "operationId": "searchOfferings",
                "responses": [
                    {
                        "status": "200",
                        "schema": {
                            "type": "object",
                            "required": ["items"],
                            "properties": {
                                "items": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "string"}},
                                    },
                                }
                            },
                        },
                    }
                ],
            }
        ]
    }
    dynamic._classify_missing_workflow_data(
        result,
        workflow,
        candidate,
        {
            "openapi": "3.0.3",
            "info": {"title": "API", "version": "1.0.0"},
            "paths": {},
        },
    )

    assert result["defectClass"] == "SUT_DEFECT"
    assert result["finding"]["code"] == "REQUIRED_WORKFLOW_DATA_MISSING"
    assert result["finding"]["operationId"] == "searchOfferings"


def test_optional_empty_collection_does_not_imply_a_sut_defect() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        },
    }

    assert not dynamic._schema_guarantees_pointer(
        schema,
        "#/0/id",
        {"openapi": "3.0.3", "info": {"title": "API", "version": "1.0.0"}, "paths": {}},
    )


def test_plan_progress_identifies_completed_retrying_and_failed_use_cases(monkeypatch):
    from app.testing.progress import testing_progress_scope

    openapi = _openapi()
    openapi["paths"]["/health"]["get"]["x-easydep-use-case-ids"].append("UC-3")
    openapi["paths"]["/secondary"] = _openapi_with_secondary_operation("UC-2")[
        "paths"
    ]["/secondary"]
    candidates = build_workflow_candidates(_requirements(3), _use_cases(3), openapi)
    events = []

    def generate(_client, candidate, error=""):
        if candidate["workflowId"] == "workflow-UC-2":
            raise dynamic.ArazzoValidationError("Invalid reference")
        return attach_workflow_trace(
            {
                "workflowId": candidate["workflowId"],
                "steps": [{"stepId": "health", "operationId": "health"}],
            },
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate", generate)
    with testing_progress_scope(events.append), pytest.raises(ValueError, match="Invalid reference"):
        dynamic._generate_document(object(), candidates, openapi)
    assert [(e["workflow_id"], e["status"]) for e in events[:3]] == [
        ("workflow-UC-1", "PENDING"), ("workflow-UC-2", "PENDING"),
        ("workflow-UC-3", "PENDING"),
    ]
    assert [event["total_workflows"] for event in events[:3]] == [3, 3, 3]
    statuses = {
        workflow_id: [
            event["status"]
            for event in events
            if event["workflow_id"] == workflow_id
        ]
        for workflow_id in ("workflow-UC-1", "workflow-UC-2", "workflow-UC-3")
    }
    assert statuses == {
        "workflow-UC-1": ["PENDING", "RUNNING", "PENDING"],
        "workflow-UC-2": ["PENDING", "RUNNING", "RUNNING", "FAIL"],
        "workflow-UC-3": ["PENDING", "RUNNING", "PENDING"],
    }
    failed = next(event for event in events if event["status"] == "FAIL")
    assert failed["attempt"] == 2
    assert failed["use_case_id"] == "UC-2"
    assert failed["use_case_name"] == "Check service 2"
    assert not [event for event in events if event["status"] == "DEFERRED"]


def test_generated_document_gets_one_bounded_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi_with_secondary_operation("UC-1")
    candidates = build_workflow_candidates(_requirements(), _use_cases(), openapi)
    calls: list[str] = []

    def generate(_client: object, candidate: dict[str, Any], error: str = "") -> dict[str, Any]:
        calls.append(error)
        operation_id = "invented" if len(calls) == 1 else "health"
        return attach_workflow_trace(
            {
                "workflowId": candidate["workflowId"],
                "steps": [{"stepId": "health", "operationId": operation_id}],
            },
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate", generate)

    document = dynamic._generate_document(object(), candidates, openapi)

    assert len(calls) == 2
    assert calls[0] == ""
    assert "invented" in calls[1]
    assert document["workflows"][0]["steps"][0]["operationId"] == "health"


def test_single_operation_document_is_authored_by_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi()
    candidates = build_workflow_candidates(_requirements(), _use_cases(), openapi)

    def generate(_client: object, candidate: dict[str, Any], _error: str = "") -> dict[str, Any]:
        return dynamic._compile_workflow_decision(
            {"workflowId": candidate["workflowId"], "orderedStepIds": ["health"], "connectionIds": []},
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate", generate)

    document = dynamic._generate_document(object(), candidates, openapi)

    assert document["workflows"][0]["steps"] == [
        {"stepId": "health", "operationId": "health"}
    ]


def test_single_operation_testing_uses_plan_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    monkeypatch.setattr(dynamic, "_client", lambda: client)
    monkeypatch.setattr(
        dynamic,
        "_generate",
        lambda _client, candidate, _error="": dynamic._compile_workflow_decision(
            {"workflowId": candidate["workflowId"], "orderedStepIds": ["health"], "connectionIds": []},
            candidate,
        ),
    )

    monkeypatch.setattr(
        dynamic,
        "execute_arazzo_workflow",
        lambda _document, workflow_id, **_kwargs: _pass(workflow_id),
    )

    report = dynamic.dynamic_functional_node(
        _state(fixed_arazzo_document=None)
    )["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    assert report["candidatePlan"]["workflows"][0]["steps"] == [{"stepId": "health", "operationId": "health"}]


def test_demo_skip_preserves_plan_without_executing_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Demo mode makes a durable plan but must not claim HTTP assertions ran."""

    def unexpected_execution(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        pytest.fail("demo validation skip must not invoke the Arazzo executor")

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", unexpected_execution)

    report = dynamic.dynamic_functional_node(
        _state(validation_skipped=True)
    )["dynamic_functional_report"]

    assert report["status"] == "PASSED"
    assert report["gateStatus"] == "PASS"
    assert report["validationSkipped"] is True
    assert report["validationSkipReason"] == "demo"
    assert report["candidatePlan"]["workflows"]
    planned = report["workflows"]
    assert planned[0]["status"] == "PASSED"
    assert planned[0]["result"]["gateStatus"] == "PASS"
    assert planned[0]["useCaseName"] == "Check service 1"
    assert planned[0]["summary"] == "Check service 1"
    operation = planned[0]["operations"][0]
    assert (operation["method"], operation["path"], operation["operationId"]) == (
        "GET", "/health", "health"
    )
    assert operation["responses"][0]["status"] == "200"
    assert report["executedWorkflowCount"] == 0


def test_demo_workflow_progress_transitions_pending_plan_to_terminal_passes() -> None:
    from app.testing import service as testing_service
    from app.testing.progress import reduce_testing_progress, testing_progress_scope

    events: list[dict[str, Any]] = []
    with testing_progress_scope(events.append):
        report = dynamic.dynamic_functional_node(
            _state(2, validation_skipped=True)
        )["dynamic_functional_report"]
        testing_service._emit_demo_terminal_workflow_progress(
            {"validationSkipped": True, "reports": {"dynamicFunctional": report}}
        )

    lifecycle = [event for event in events if event["scope"] == "workflow" and event["phase"] == "dynamic"]
    assert [(event["workflow_id"], event["status"]) for event in lifecycle] == [
        ("workflow-UC-1", "PENDING"),
        ("workflow-UC-2", "PENDING"),
        ("workflow-UC-1", "PASS"),
        ("workflow-UC-2", "PASS"),
    ]
    assert [event["phase"] for event in lifecycle] == ["dynamic", "dynamic", "dynamic", "dynamic"]
    assert [event["use_case_name"] for event in lifecycle] == [
        "Check service 1", "Check service 2", "Check service 1", "Check service 2",
    ]
    progress: dict[str, Any] = {}
    for event in events:
        progress = reduce_testing_progress(progress, event)
    assert progress["workflow_counts"] == {
        "total": 2,
        "passed": 2,
        "failed": 0,
        "running": 0,
        "pending": 0,
        "reused": 0,
        "inconclusive": 0,
        "deferred": 0,
        "completed": 2,
    }
    assert report["workflowCounts"] == {
        "total": 2,
        "completed": 2,
        "passed": 2,
        "failed": 0,
        "running": 0,
        "pending": 0,
    }
    assert all("status" not in workflow for workflow in report["candidatePlan"]["workflows"])


def test_workflow_summary_uses_each_use_case_name_with_readable_missing_name_fallback() -> None:
    use_cases = _use_cases(2)
    use_cases["use_case_specs"][1].pop("name")
    candidates = build_workflow_candidates(_requirements(2), use_cases, _openapi())

    assert [use_case_display_name(candidate) for candidate in candidates] == [
        "Check service 1", "Use case UC-2"
    ]
    public_records = [
        dynamic._workflow_record(
            attach_workflow_trace(
                {"workflowId": candidate["workflowId"], "steps": [{"stepId": "health", "operationId": "health"}]},
                candidate,
            ),
            _pass(str(candidate["workflowId"])),
            [],
            {},
            {},
            candidate,
        )
        for candidate in candidates
    ]
    assert [record["summary"] for record in public_records] == [
        "Check service 1", "Use case UC-2"
    ]


def test_validation_skip_disabled_still_invokes_workflow_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def execute(_document: dict[str, Any], workflow_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(workflow_id)
        return _pass(workflow_id)

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)

    report = dynamic.dynamic_functional_node(
        _state(validation_skipped=False)
    )["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    assert calls == ["workflow-UC-1"]


def test_demo_skip_keeps_plan_generation_errors_as_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_generation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ValueError("planner response is invalid")

    monkeypatch.setattr(dynamic, "_generate_document", fail_generation)

    report = dynamic.dynamic_functional_node(
        _state(validation_skipped=True, fixed_arazzo_document=None)
    )["dynamic_functional_report"]

    assert report["gateStatus"] == "FAIL"
    assert "planner response is invalid" in report["reason"]


def test_generated_document_retries_only_the_failed_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi_with_secondary_operation("UC-1", "UC-2")
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), openapi)
    calls = {str(candidate["workflowId"]): 0 for candidate in candidates}

    def generate(_client: object, candidate: dict[str, Any], error: str = "") -> dict[str, Any]:
        workflow_id = str(candidate["workflowId"])
        calls[workflow_id] += 1
        operation_id = (
            "invented"
            if workflow_id == "workflow-UC-2" and calls[workflow_id] == 1
            else "health"
        )
        return attach_workflow_trace(
            {
                "workflowId": workflow_id,
                "steps": [{"stepId": "health", "operationId": operation_id}],
            },
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate", generate)

    document = dynamic._generate_document(object(), candidates, openapi)

    assert calls == {"workflow-UC-1": 1, "workflow-UC-2": 2}
    assert [workflow["workflowId"] for workflow in document["workflows"]] == [
        "workflow-UC-1",
        "workflow-UC-2",
    ]


def test_independent_llm_workflow_plans_are_generated_concurrently(monkeypatch) -> None:
    openapi = _openapi_with_secondary_operation("UC-1", "UC-2")
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), openapi)
    rendezvous = Barrier(2)

    def generate(
        _client: object,
        candidate: dict[str, Any],
        error: str = "",
    ) -> dict[str, Any]:
        assert error == ""
        rendezvous.wait(timeout=3)
        return attach_workflow_trace(
            {
                "workflowId": candidate["workflowId"],
                "steps": [{"stepId": "health", "operationId": "health"}],
            },
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate", generate)

    document = dynamic._generate_document(object(), candidates, openapi)

    assert [workflow["workflowId"] for workflow in document["workflows"]] == [
        "workflow-UC-1",
        "workflow-UC-2",
    ]


def test_openrouter_structured_output_requires_parameter_support() -> None:
    connection = SimpleNamespace(provider="openrouter")
    profile = SimpleNamespace(extra_body=lambda _provider: None)

    assert dynamic._structured_output_extra_body(connection, profile) == {
        "provider": {"require_parameters": True}
    }


def test_workflow_generation_uses_low_reasoning_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request: dict[str, Any] = {}
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content='{"workflowId":"workflow-UC-1","orderedStepIds":["health"],"connectionIds":[]}'
                ),
            )
        ]
    )

    class Completions:
        def create(self, **kwargs: Any) -> Any:
            request.update(kwargs)
            return response

    profile = SimpleNamespace(
        temperature=0.2,
        supported_reasoning=("low", "medium", "high"),
        top_p=None,
        completion_limit=lambda _requested: 16384,
        resolve_reasoning=lambda requested=None: requested,
        extra_body=lambda _provider: None,
    )
    connection = SimpleNamespace(provider="cloudflare", model="@cf/zai-org/glm-5.3-flash")
    monkeypatch.setattr(dynamic, "build_arazzo_llm_connection", lambda: connection)
    monkeypatch.setattr(dynamic, "profile_for", lambda *_args, **_kwargs: profile)

    openapi = _openapi()
    candidate = build_workflow_candidates(_requirements(), _use_cases(), openapi)[0]
    candidate["planningModel"] = dynamic._planning_model(
        candidate, build_execution_candidates([candidate], openapi)
    )
    dynamic._generate(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), candidate)

    assert request["model"] == "@cf/zai-org/glm-5.3-flash"
    assert request["reasoning_effort"] == "low"


@pytest.mark.parametrize("failure", ["reference", "schema", "length"])
def test_generation_repairs_rejected_candidate_without_executing_it(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    openapi = _openapi_with_secondary_operation("UC-1")
    openapi["paths"]["/health"]["get"]["parameters"] = [
        {"name": "studentId", "in": "query", "schema": {"type": "string"}}
    ]
    candidates = build_workflow_candidates(_requirements(), _use_cases(), openapi)
    valid = {
        "workflowId": candidates[0]["workflowId"],
        "orderedStepIds": ["health"],
        "connectionIds": [],
    }
    rejected = deepcopy(valid)
    if failure == "schema":
        rejected["invented"] = True
    else:
        rejected["orderedStepIds"] = ["previousStep"]
    requests = []

    def complete(**request):
        requests.append(request)
        first = len(requests) == 1
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="length" if first and failure == "length" else "stop",
            message=SimpleNamespace(content=json.dumps(rejected if first else valid)),
        )])

    monkeypatch.setattr(dynamic, "build_arazzo_llm_connection", lambda: SimpleNamespace(
        provider="openrouter", model="openai/gpt-oss-120b"
    ))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
    document = dynamic._generate_document(client, candidates, openapi)

    assert len(requests) == 2
    assert requests[0]["reasoning_effort"] == "low"
    assert requests[1]["reasoning_effort"] == "low"
    assert requests[1]["messages"][0] == {
        "role": "system",
        "content": dynamic.PLAN_ROLE_PROMPT,
    }
    correction = requests[1]["messages"][-1]["content"]
    if failure != "length":
        assert json.dumps(rejected, separators=(",", ":")) in correction
        assert "unknown step" in correction or "Additional properties" in correction
    if failure == "reference":
        assert "unknown step" in correction
    assert document["workflows"][0]["steps"] == [{"stepId": "health", "operationId": "health"}]


def test_unrepaired_unknown_step_remains_a_test_defect(monkeypatch: pytest.MonkeyPatch) -> None:
    def generate(*_args, **_kwargs):
        raise dynamic.ArazzoValidationError(
            "value references an unknown local step: $steps.previousStep.outputs.studentId"
        )

    def unexpected_execution(*_args, **_kwargs):
        pytest.fail("Invalid plan must not execute against the generated application")

    monkeypatch.setattr(dynamic, "_client", object)
    monkeypatch.setattr(dynamic, "_generate", generate)
    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", unexpected_execution)
    state = _state(fixed_arazzo_document=None)
    state["testing_input"]["contract_artifacts"]["openapi"]["content"] = (
        _openapi_with_secondary_operation("UC-1")
    )
    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]
    assert report["defectClass"] == "TEST_DEFECT"
    assert report["defect"]["repairOwner"] == "testing"


@pytest.mark.parametrize("weaken_oracle", [False, True])
def test_execution_error_log_is_used_for_local_plan_repair(monkeypatch, weaken_oracle):
    executions = []
    prompts = []

    def execute(document, workflow_id, **kwargs):
        executions.append(deepcopy(document))
        if len(executions) == 1:
            return {
                "gateStatus": "FAIL", "defectClass": "TEST_DEFECT",
                "reason": "JSON Pointer does not resolve: #/invented",
                "finding": {"code": "RUNTIME_EXPRESSION_UNRESOLVED", "stepId": "health"},
                "steps": [{"stepId": "health", "operationId": "health", "statusCode": 200}],
                "workflowInputs": {"kept": "original"},
            }
        assert kwargs["workflow_inputs"] == {"kept": "original"}
        return _pass(workflow_id)

    def generate(_client, candidate, error=""):
        prompts.append(error)
        revised = deepcopy(_document()["workflows"][0])
        revised["steps"][0]["outputs"] = {"payload": "$response.body"}
        if weaken_oracle:
            revised["steps"][0].pop("successCriteria")
        return revised

    monkeypatch.setattr(dynamic, "_client", object)
    monkeypatch.setattr(dynamic, "_generate", generate)
    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    report = dynamic.dynamic_functional_node(_state())["dynamic_functional_report"]
    assert "RUNTIME_EXPRESSION_UNRESOLVED" in prompts[0]
    assert "#/invented" in prompts[0]
    assert "Execution failed with TEST_DEFECT" in prompts[0]
    assert len(executions) == (1 if weaken_oracle else 2)
    assert report["gateStatus"] == ("FAIL" if weaken_oracle else "PASS")
    assert report["planRepairs"][0]["status"] == ("FAILED" if weaken_oracle else "PASS")


@pytest.mark.parametrize("defect,method", [("SUT_DEFECT", "get"), ("ENVIRONMENT_DEFECT", "get"), ("TEST_DEFECT", "post")])
def test_execution_repair_respects_ownership_and_replay_boundary(monkeypatch, defect, method):
    state = _state()
    path = state["testing_input"]["contract_artifacts"]["openapi"]["content"]["paths"]["/health"]
    operation = path.pop("get")
    path[method] = operation
    calls = []

    def execute(*args, **kwargs):
        calls.append(True)
        return {"gateStatus": "FAIL", "defectClass": defect, "reason": "failure"}

    def unexpected_generation(*args, **kwargs):
        pytest.fail("This failure must not trigger local plan repair/replay")

    def repair_generation(*_args, **_kwargs):
        revised = deepcopy(_document()["workflows"][0])
        revised["steps"][0]["outputs"] = {"payload": "$response.body"}
        return revised

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    monkeypatch.setattr(
        dynamic,
        "_client",
        object if defect == "TEST_DEFECT" else unexpected_generation,
    )
    monkeypatch.setattr(dynamic, "_generate", repair_generation)
    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]
    assert len(calls) == 1
    assert report["defectClass"] == defect
    if defect == "TEST_DEFECT":
        assert report["planRepairs"][0]["status"] == "READY_FOR_RERUN"
    else:
        assert report["planRepairs"] == []


def test_dynamic_report_keeps_one_actionable_analysis_per_failed_use_case(monkeypatch) -> None:
    state = _state(2)

    def execute(_document, workflow_id, **_kwargs):
        return {
            "workflowId": workflow_id,
            "gateStatus": "FAIL",
            "defectClass": "SUT_DEFECT",
            "reason": f"{workflow_id} returned HTTP 500",
            "failedWorkflowId": workflow_id,
            "failedStepId": "health",
            "finding": {"code": "HTTP_STATUS_NOT_SUCCESS", "stepId": "health"},
            "steps": [
                    {
                        "workflowId": workflow_id,
                        "stepId": "health",
                        "operationId": "health",
                        "status": "failed",
                        "request": {"method": "GET", "path": "/health"},
                    "responseBody": "failure",
                }
            ],
        }

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert [item["workflowId"] for item in report["failureAnalyses"]] == [
        "workflow-UC-1",
        "workflow-UC-2",
    ]
    assert {item["repairAction"] for item in report["failureAnalyses"]} == {
        "delegate_implementation_repair"
    }
    assert all(item["finding"]["request"]["path"] == "/health" for item in report["failureAnalyses"])


def test_incomplete_structured_output_is_rejected_before_json_parsing() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(content='{"workflowId":"workflow-UC-1"'),
            )
        ]
    )

    with pytest.raises(dynamic.ArazzoPlanningError, match="completion token limit"):
        dynamic._completion_content(response, operation="Arazzo workflow generation")


def test_preserved_candidate_plan_is_pure_arazzo_and_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def execute(_document: dict[str, Any], workflow_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(workflow_id)
        return _pass(workflow_id)

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)

    report = dynamic.dynamic_functional_node(_state())["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    assert report["candidatePlan"] == _document()
    assert report["candidatePlan"]["arazzo"] == "1.1.0"
    assert "cases" not in report["candidatePlan"]
    assert calls == ["workflow-UC-1"]
    assert report["executionOrder"] == ["workflow-UC-1"]


def test_fixed_leaf_input_is_reused_without_an_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposed: list[InputValueRequest] = []

    def execute(
        _document: dict[str, Any],
        workflow_id: str,
        *,
        propose_input,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        value = propose_input(
            InputValueRequest(
                operation_id="health",
                location="query.sample",
                schema={"type": "string"},
            )
        )
        assert value == "fixed"
        return _pass(workflow_id)

    def propose(_client: object, request: InputValueRequest) -> str:
        proposed.append(request)
        return "new"

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    monkeypatch.setattr(dynamic, "_propose_input", propose)
    state = _state(
        fixed_input_values={
            "workflow-UC-1": [
                {
                    "operationId": "health",
                    "location": "query.sample",
                    "value": "fixed",
                }
            ],
        }
    )

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    assert proposed == []
    assert report["inputValues"]["workflow-UC-1"][0]["value"] == "fixed"


def test_failure_reports_exact_workflow_step_and_continues_remaining_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def execute(_document: dict[str, Any], workflow_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(workflow_id)
        return {
            "workflowId": workflow_id,
            "status": "failed",
            "gateStatus": "FAIL",
            "defectClass": "SUT_DEFECT",
            "reason": "The criterion failed.",
            "finding": {"code": "SUCCESS_CRITERIA_FAILED", "message": "failed"},
            "steps": [
                {
                    "stepId": "health",
                    "operationId": "health",
                    "request": {"method": "GET", "path": "/health"},
                    "responseBody": {"ok": False},
                    "semanticStatus": "FAIL",
                    "finding": {
                        "code": "SUCCESS_CRITERIA_FAILED",
                        "criterion": {"condition": "$response.body#/ok == true"},
                    },
                }
            ],
            "workflowInputs": {},
            "contractStatus": "PASS",
            "semanticStatus": "FAIL",
        }

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)

    report = dynamic.dynamic_functional_node(_state(2))["dynamic_functional_report"]

    assert calls == ["workflow-UC-1", "workflow-UC-2"]
    assert report["failedWorkflowId"] == "workflow-UC-1"
    assert report["failedStepId"] == "health"
    assert report["failedWorkflowIds"] == ["workflow-UC-1", "workflow-UC-2"]
    assert report["pendingWorkflowIds"] == []
    assert report["finding"]["operationId"] == "health"
    assert report["finding"]["criterion"]["condition"].endswith("== true")
    assert report["failedRequestDigest"]


def test_failed_workflow_is_reexecuted_first_without_reordering_the_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def execute(_document: dict[str, Any], workflow_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(workflow_id)
        return _pass(workflow_id)

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    document = _document(2)
    state = _state(
        2,
        fixed_arazzo_document=document,
        priority_workflow_id="workflow-UC-2",
    )

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert calls == ["workflow-UC-2", "workflow-UC-1"]
    assert report["candidatePlan"] == document


def test_passed_workflow_is_reused_and_only_remaining_workflow_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(2)
    first_workflow = document["workflows"][0]
    preserved = {
        "workflowId": "workflow-UC-1",
        "requirementIds": ["FR-1"],
        "useCaseIds": ["UC-1"],
        "workflow": deepcopy(first_workflow),
        "inputValues": [],
        "workflowInputsById": {"workflow-UC-1": {}},
        "inputValuesById": {"workflow-UC-1": []},
        "result": _pass("workflow-UC-1"),
    }
    calls: list[str] = []

    def execute(_document: dict[str, Any], workflow_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(workflow_id)
        return _pass(workflow_id)

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    state = _state(
        2,
        fixed_arazzo_document=document,
        preserved_workflow_results=[preserved],
        previous_job_id="job-previous",
    )

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert calls == ["workflow-UC-2"]
    assert report["reusedWorkflowIds"] == ["workflow-UC-1"]
    assert report["workflows"][0]["result"]["reused"] is True
    assert report["workflows"][0]["result"]["reusedFromJobId"] == "job-previous"


def test_passed_workflow_is_rerun_when_preserved_inputs_do_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    preserved_result = _pass("workflow-UC-1")
    preserved_result["workflowInputs"] = {"seed": "old"}
    preserved = {
        "workflowId": "workflow-UC-1",
        "requirementIds": ["FR-1"],
        "useCaseIds": ["UC-1"],
        "workflow": deepcopy(document["workflows"][0]),
        "inputValues": [],
        "workflowInputsById": {"workflow-UC-1": {"seed": "old"}},
        "inputValuesById": {"workflow-UC-1": []},
        "result": preserved_result,
    }
    calls: list[str] = []

    def execute(_document: dict[str, Any], workflow_id: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(workflow_id)
        return _pass(workflow_id)

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    state = _state(
        fixed_arazzo_document=document,
        fixed_workflow_inputs={"workflow-UC-1": {"seed": "new"}},
        preserved_workflow_results=[preserved],
    )

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    assert calls == ["workflow-UC-1"]
    assert report["reusedWorkflowIds"] == []


def test_nested_input_proposals_are_saved_under_the_executor_workflow_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def execute(
        _document: dict[str, Any], workflow_id: str, *, propose_input, **_kwargs: Any
    ) -> dict[str, Any]:
        if workflow_id == "workflow-UC-1":
            assert (
                propose_input(
                    InputValueRequest(
                        operation_id="health",
                        location="query.child",
                        schema={"type": "string"},
                        operation_context="workflow-UC-2",
                    )
                )
                == "child-value"
            )
        return {
            **_pass(workflow_id),
            "workflowInputsById": {workflow_id: {}},
        }

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    monkeypatch.setattr(
        dynamic,
        "_propose_input",
        lambda _client, _request: "child-value",
    )

    report = dynamic.dynamic_functional_node(_state(2))["dynamic_functional_report"]

    assert "workflow-UC-1" not in report["inputValues"]
    assert report["inputValues"]["workflow-UC-2"] == [
        {
            "operationId": "health",
            "location": "query.child",
            "value": "child-value",
        }
    ]


def test_semantic_coverage_requires_direct_success_guarantee() -> None:
    candidates = build_workflow_candidates(
        _requirements(), _use_cases(guarantees=False), _openapi()
    )
    workflow_id = candidates[0]["workflowId"]
    results = [
        {
            "workflowId": workflow_id,
            "result": _pass(workflow_id, semantic="PASS"),
        }
    ]

    coverage = dynamic._requirements(results, candidates)

    assert coverage["contractIds"] == ["FR-1"]
    assert coverage["ids"] == []
    assert coverage["unverifiedIds"] == ["FR-1"]


def test_legacy_custom_candidate_plan_is_rejected() -> None:
    state = _state(fixed_arazzo_document={"cases": []})

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert report["gateStatus"] == "FAIL"
    assert report["defectClass"] == "TEST_DEFECT"
    assert "Arazzo" in report["reason"]
