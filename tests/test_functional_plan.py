"""Integration tests for Arazzo planning in the dynamic Testing node."""

from __future__ import annotations

import json
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel

from app.testing.nodes import dynamic_functional as dynamic
from app.testing.utils.arazzo_planner import (
    attach_workflow_trace,
    build_arazzo_document,
    build_execution_candidates,
    build_workflow_candidates,
    use_case_display_name,
)


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
        "connectionIds": ["c1"],
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


def test_workflow_decision_requires_one_trace_linked_target_among_setup_steps() -> None:
    candidate = _two_step_decision_candidate()
    candidate["operations"] = [{"operationId": "getItem"}]
    planning_model = dynamic._planning_model(
        candidate, candidate["planningModel"]["availableSteps"]
    )
    assert planning_model["targetOperationIds"] == ["getItem"]
    assert planning_model["optionalSetupStepIds"] == ["createItem"]
    assert planning_model["connectionChoicesByInput"] == {
        "getItem.path:id": [
            {"choice": "c1", "sourceStepId": "createItem", "sourceOutput": "bodyId"}
        ]
    }

    with pytest.raises(dynamic.ArazzoValidationError, match="is not one of"):
        dynamic._validate_workflow_decision(
            {
                "workflowId": "workflow-UC-1",
                "orderedStepIds": ["createItem", "getItem"],
                "connectionIds": [
                    "searchCourseOfferings.body0OfferingId->registerForCourse.path:courseOfferingId"
                ],
            },
            candidate,
        )

    with pytest.raises(dynamic.ArazzoPlanningError, match="trace-linked target"):
        dynamic._compile_workflow_decision(
            {
                "workflowId": "workflow-UC-1",
                "orderedStepIds": ["createItem"],
                "connectionIds": [],
            },
            candidate,
        )

    workflow = dynamic._compile_workflow_decision(
        {
            "workflowId": "workflow-UC-1",
            "orderedStepIds": ["createItem", "getItem"],
            "connectionIds": ["createItem.bodyId->getItem.path:id"],
        },
        candidate,
    )
    assert [step["operationId"] for step in workflow["steps"]] == ["createItem", "getItem"]


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


def test_openapi_required_input_without_fixed_plan_value_is_rejected_during_planning(
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
    def reject_missing_value(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise dynamic.ArazzoPlanningError("Every required selected input needs a fixed value or producer")

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", reject_missing_value)
    document, failures = dynamic._generate_document(object(), candidates, openapi)

    assert document is None
    assert len(failures) == 1
    assert failures[0]["defectClass"] == "TEST_DEFECT"
    assert "Every required selected input" in failures[0]["reason"]


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


@pytest.mark.parametrize("min_items, accepted", [(None, False), (1, True)])
def test_indexed_array_producer_requires_a_nonempty_response_guarantee(
    min_items: int | None, accepted: bool
) -> None:
    items_schema: dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
    }
    if min_items is not None:
        items_schema["minItems"] = min_items
    candidate = {
        "workflowId": "workflow-UC-3",
        "requirements": [],
        "useCase": {},
        "operations": [
            {
                "operationId": "listItems",
                "responses": [{"status": "200", "schema": items_schema}],
            },
            {
                "operationId": "getItem",
                "responses": [{"status": "200", "schema": {"type": "object"}}],
            },
        ],
        "trace": {"useCaseIds": ["UC3"]},
    }
    document = build_arazzo_document([
        attach_workflow_trace(
            {
                "workflowId": candidate["workflowId"],
                "steps": [
                    {
                        "stepId": "list",
                        "operationId": "listItems",
                        "outputs": {"firstId": "$response.body#/0/id"},
                    },
                    {
                        "stepId": "details",
                        "operationId": "getItem",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "query",
                                "value": "$steps.list.outputs.firstId",
                            }
                        ],
                    },
                ],
            },
            candidate,
        )
    ])
    openapi = {
        "openapi": "3.0.3",
        "info": {"title": "API", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": items_schema}},
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "query", "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }

    if accepted:
        dynamic._validate_document(document, [candidate], openapi)
    else:
        with pytest.raises(dynamic.ArazzoValidationError, match="indexed array value not guaranteed"):
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

    def generate_candidate(_client, candidate, _openapi, total, _execution_candidates):
        dynamic._emit_plan_progress(candidate, "RUNNING", total_workflows=total, attempt=1)
        if candidate["workflowId"] == "workflow-UC-2":
            dynamic._emit_plan_progress(candidate, "FAIL", total_workflows=total, attempt=1)
            raise dynamic.ArazzoValidationError("Invalid reference")
        workflow = attach_workflow_trace(
            {
                "workflowId": candidate["workflowId"],
                "steps": [{"stepId": "health", "operationId": "health"}],
            },
            candidate,
        )
        dynamic._emit_plan_progress(candidate, "PENDING", total_workflows=total, attempt=1)
        return workflow

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", generate_candidate)
    with testing_progress_scope(events.append):
        document, failures = dynamic._generate_document(object(), candidates, openapi)
    assert [workflow["workflowId"] for workflow in document["workflows"]] == ["workflow-UC-1", "workflow-UC-3"]
    assert len(failures) == 1
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
        "workflow-UC-2": ["PENDING", "RUNNING", "FAIL"],
        "workflow-UC-3": ["PENDING", "RUNNING", "PENDING"],
    }
    failed = next(event for event in events if event["status"] == "FAIL")
    assert failed["attempt"] == 1
    assert failed["use_case_id"] == "UC-2"
    assert failed["use_case_name"] == "Check service 2"
    assert not [event for event in events if event["status"] == "DEFERRED"]


def test_generated_document_gets_one_bounded_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi_with_secondary_operation("UC-1")
    candidates = build_workflow_candidates(_requirements(), _use_cases(), openapi)
    calls: list[str] = []

    def generate_candidate(_client, candidate, _openapi, _total, _execution_candidates):
        calls.append(str(candidate["workflowId"]))
        return attach_workflow_trace(
            {
                "workflowId": candidate["workflowId"],
                "steps": [{"stepId": "health", "operationId": "health"}],
            },
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", generate_candidate)

    document, _planning_failures = dynamic._generate_document(object(), candidates, openapi)

    assert calls == ["workflow-UC-1"]
    assert document["workflows"][0]["steps"][0]["operationId"] == "health"


def test_single_operation_document_is_authored_by_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi()
    candidates = build_workflow_candidates(_requirements(), _use_cases(), openapi)

    def generate_candidate(_client, candidate, _openapi, _total, _execution_candidates):
        return attach_workflow_trace(
            {"workflowId": candidate["workflowId"], "steps": [{"stepId": "health", "operationId": "health"}]},
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", generate_candidate)

    document, _planning_failures = dynamic._generate_document(object(), candidates, openapi)

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
        "_generate_candidate_workflow",
        lambda _client, candidate, _openapi, _total, _execution_candidates: attach_workflow_trace(
            {"workflowId": candidate["workflowId"], "steps": [{"stepId": "health", "operationId": "health"}]},
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


def test_plan_generation_failure_keeps_workflow_identity_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executions: list[str] = []

    def fail_graph(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise dynamic.ArazzoPlanningError("No valid graph could be authored.")

    monkeypatch.setattr(dynamic, "_client", lambda: object())
    monkeypatch.setattr(dynamic, "_generate_workflow_graph", fail_graph)
    monkeypatch.setattr(
        dynamic,
        "execute_arazzo_workflow",
        lambda _document, workflow_id, **_kwargs: executions.append(workflow_id),
    )

    report = dynamic.dynamic_functional_node(
        _state(fixed_arazzo_document=None)
    )["dynamic_functional_report"]

    assert report["gateStatus"] == "FAIL"
    assert report["finding"] == {
        "code": "WORKFLOW_PLAN_GENERATION_FAILED",
        "stage": "planning",
        "workflowId": "workflow-UC-1",
        "useCaseId": "UC-1",
    }
    assert report["candidatePlan"] is None
    assert report["planningFailures"][0]["executionStatus"] == "NOT_RUN"
    assert executions == []


def test_execution_repair_replans_setup_graph_and_keeps_target_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {"workflowId": "workflow-UC-1", "operations": []}
    original = {
        "workflowId": "workflow-UC-1",
        "steps": [{
            "stepId": "target", "operationId": "getTarget",
            "successCriteria": [{"condition": "$steps.target.outputs.id == 'expected'"}],
        }],
    }
    document = {"workflows": [original]}
    authoring = {"planningModel": {"targetOperationIds": ["getTarget"]}}
    graph_contexts: list[dict[str, Any] | None] = []

    monkeypatch.setattr(dynamic, "build_execution_candidates", lambda *_args: [])
    monkeypatch.setattr(dynamic, "_authoring_candidate", lambda *_args: deepcopy(authoring))
    monkeypatch.setattr(dynamic, "_select_semantic_producers", lambda *_args: [])
    monkeypatch.setattr(
        dynamic,
        "_generate_workflow_graph",
        lambda _client, _candidate, correction_context=None: graph_contexts.append(correction_context) or {"graph": True},
    )
    monkeypatch.setattr(dynamic, "_validated_graph_projection", lambda *_args: ({}, {}, []))
    monkeypatch.setattr(dynamic, "_select_literal_values", lambda *_args: [])
    monkeypatch.setattr(
        dynamic,
        "_compile_workflow_decision",
        lambda *_args: {
            "workflowId": "workflow-UC-1",
            "steps": [
                {"stepId": "setup", "operationId": "createSetup"},
                {
                    **deepcopy(original["steps"][0]),
                    "stepId": "target-after-setup",
                    "successCriteria": [{
                        "condition": "$steps.target-after-setup.outputs.id == 'expected'"
                    }],
                },
            ],
        },
    )
    monkeypatch.setattr(dynamic, "_validate_document", lambda doc, *_args: doc)

    updated, evidence = dynamic._repair_execution_plan(
        object(), document, original, candidate, [candidate], {},
        {"reason": "Required path value was invalid", "finding": {"code": "INPUT_VALUE_INVALID"}},
    )

    assert [step["operationId"] for step in updated["workflows"][0]["steps"]] == [
        "createSetup", "getTarget"
    ]
    assert updated["workflows"][0]["steps"][1]["stepId"] == "target-after-setup"
    assert graph_contexts == [{"executionFailure": evidence}]


def test_execution_repair_does_not_normalize_setup_references_in_target_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {"workflowId": "workflow-UC-1", "operations": []}
    original = {
        "workflowId": "workflow-UC-1",
        "steps": [
            {"stepId": "existing-setup", "operationId": "createSetup"},
            {
                "stepId": "target",
                "operationId": "getTarget",
                "successCriteria": [{
                    "condition": "$steps.existing-setup.outputs.id == 'expected'"
                }],
            },
        ],
    }
    document = {"workflows": [original]}
    authoring = {"planningModel": {"targetOperationIds": ["getTarget"]}}

    monkeypatch.setattr(dynamic, "build_execution_candidates", lambda *_args: [])
    monkeypatch.setattr(dynamic, "_authoring_candidate", lambda *_args: deepcopy(authoring))
    monkeypatch.setattr(dynamic, "_select_semantic_producers", lambda *_args: [])
    monkeypatch.setattr(dynamic, "_generate_workflow_graph", lambda *_args, **_kwargs: {"graph": True})
    monkeypatch.setattr(dynamic, "_validated_graph_projection", lambda *_args: ({}, {}, []))
    monkeypatch.setattr(dynamic, "_select_literal_values", lambda *_args: [])
    monkeypatch.setattr(
        dynamic,
        "_compile_workflow_decision",
        lambda *_args: {
            "workflowId": "workflow-UC-1",
            "steps": [
                {"stepId": "new-setup", "operationId": "createSetup"},
                {
                    "stepId": "target-renamed",
                    "operationId": "getTarget",
                    "successCriteria": [{
                        "condition": "$steps.new-setup.outputs.id == 'expected'"
                    }],
                },
            ],
        },
    )

    with pytest.raises(dynamic.ArazzoValidationError, match="success criteria"):
        dynamic._repair_execution_plan(
            object(), document, original, candidate, [candidate], {},
            {"reason": "invalid setup", "finding": {"code": "INPUT_VALUE_INVALID"}},
        )


def _planning_failure(candidate: dict[str, Any], message: str = "Invalid graph") -> dict[str, Any]:
    return dynamic._planning_failure_analysis(
        candidate, dynamic.ArazzoValidationError(message)
    )


class _RequiredFixedInputSlot(BaseModel):
    fixedInputSlot: str


def _pydantic_fixed_input_slot_error() -> dynamic.PydanticValidationError:
    try:
        _RequiredFixedInputSlot.model_validate({})
    except dynamic.PydanticValidationError as error:
        return error
    raise AssertionError("Expected fixed-input schema validation to fail")


@pytest.mark.parametrize(
    "error",
    [
        jsonschema.ValidationError("'fixedInputOccurrenceId' is a required property"),
        pytest.param(_pydantic_fixed_input_slot_error(), id="pydantic-validation"),
    ],
    ids=["jsonschema-validation", "pydantic-validation"],
)
def test_planning_schema_validation_failures_route_to_test_plan_repair(
    error: Exception,
) -> None:
    candidate = build_workflow_candidates(_requirements(), _use_cases(), _openapi())[0]

    failure = dynamic._planning_failure_analysis(candidate, error)

    assert failure["defectClass"] == "TEST_DEFECT"
    assert failure["repairAction"] == "repair_test_plan"
    assert failure["repairOwner"] == "testing"


def test_planning_failure_isolated_while_valid_workflow_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), _openapi())
    valid_document = _document(2)
    valid_document["workflows"] = [valid_document["workflows"][0]]
    executed: list[str] = []

    monkeypatch.setattr(
        dynamic,
        "_generate_document",
        lambda *_args: (valid_document, [_planning_failure(candidates[1])]),
    )
    monkeypatch.setattr(
        dynamic,
        "execute_arazzo_workflow",
        lambda _document, workflow_id, **_kwargs: executed.append(workflow_id) or _pass(workflow_id),
    )

    report = dynamic.dynamic_functional_node(_state(
        2,
        fixed_arazzo_document=None,
        fixed_workflow_inputs={"workflow-UC-2": {"stale": "checkpoint-value"}},
        fixed_input_values={
            "workflow-UC-2": [
                {"operationId": "health", "location": "query.stale", "value": "checkpoint-value"}
            ]
        },
    ))["dynamic_functional_report"]

    assert executed == ["workflow-UC-1"]
    assert [item["workflowId"] for item in report["candidatePlan"]["workflows"]] == [
        "workflow-UC-1"
    ]
    assert report["gateStatus"] == "FAIL"
    assert report["planningFailures"] == report["failureAnalyses"]
    failure = report["planningFailures"][0]
    assert failure["workflowId"] == "workflow-UC-2"
    assert failure["requirementIds"] == ["FR-2"]
    assert failure["defectClass"] == "TEST_DEFECT"
    assert failure["executionStatus"] == "NOT_RUN"
    assert failure["steps"] == []
    assert report["workflowCounts"] == {
        "total": 2, "completed": 2, "passed": 1, "failed": 1, "running": 0, "pending": 0,
    }
    assert report["requirements"]["unverifiedIds"] == ["FR-2"]
    assert report["workflowInputs"]["workflow-UC-2"] == {"stale": "checkpoint-value"}


def test_all_planning_failures_do_not_create_or_execute_an_empty_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), _openapi())
    executions: list[str] = []
    monkeypatch.setattr(
        dynamic,
        "_generate_document",
        lambda *_args: (None, [_planning_failure(candidate) for candidate in candidates]),
    )
    monkeypatch.setattr(
        dynamic,
        "execute_arazzo_workflow",
        lambda _document, workflow_id, **_kwargs: executions.append(workflow_id),
    )

    report = dynamic.dynamic_functional_node(
        _state(2, fixed_arazzo_document=None)
    )["dynamic_functional_report"]

    assert executions == []
    assert report["candidatePlan"] is None
    assert report["gateStatus"] == "FAIL"
    assert report["workflowCounts"]["total"] == 2
    assert all(item["executionStatus"] == "NOT_RUN" and item["steps"] == []
               for item in report["planningFailures"])


def test_all_successful_plans_keep_the_normal_dynamic_pass_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    monkeypatch.setattr(dynamic, "_generate_document", lambda *_args: (_document(2), []))
    monkeypatch.setattr(
        dynamic,
        "execute_arazzo_workflow",
        lambda _document, workflow_id, **_kwargs: executed.append(workflow_id) or _pass(workflow_id),
    )

    report = dynamic.dynamic_functional_node(
        _state(2, fixed_arazzo_document=None)
    )["dynamic_functional_report"]

    assert executed == ["workflow-UC-1", "workflow-UC-2"]
    assert report["gateStatus"] == "PASS"
    assert report["planningFailures"] == []
    assert report["failureAnalyses"] == []


@pytest.mark.parametrize("workers", [1, 2])
def test_planning_keeps_valid_candidates_after_a_local_failure(
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    openapi = _openapi()
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), openapi)

    def generate_candidate(
        _client: object, candidate: dict[str, Any], _openapi: dict[str, Any],
        _total: int, _execution_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if candidate["workflowId"] == "workflow-UC-2":
            raise dynamic.ArazzoValidationError("invalid graph")
        return attach_workflow_trace(
            {"workflowId": candidate["workflowId"], "steps": [{"stepId": "health", "operationId": "health"}]},
            candidate,
        )

    monkeypatch.setattr(dynamic, "_FUNCTIONAL_PLAN_MAX_WORKERS", workers)
    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", generate_candidate)
    document, failures = dynamic._generate_document(object(), candidates, openapi)

    assert [item["workflowId"] for item in document["workflows"]] == ["workflow-UC-1"]
    assert [(item["workflowId"], item["defectClass"], item["executionStatus"])
            for item in failures] == [("workflow-UC-2", "TEST_DEFECT", "NOT_RUN")]


def test_generated_document_retries_only_the_failed_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi_with_secondary_operation("UC-1", "UC-2")
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), openapi)
    calls = {str(candidate["workflowId"]): 0 for candidate in candidates}

    def generate_candidate(_client, candidate, _openapi, _total, _execution_candidates):
        workflow_id = str(candidate["workflowId"])
        calls[workflow_id] += 1
        if workflow_id == "workflow-UC-2":
            raise dynamic.ArazzoValidationError("invented operation")
        return attach_workflow_trace(
            {
                "workflowId": workflow_id,
                "steps": [{"stepId": "health", "operationId": "health"}],
            },
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", generate_candidate)

    document, planning_failures = dynamic._generate_document(object(), candidates, openapi)

    assert calls == {"workflow-UC-1": 1, "workflow-UC-2": 1}
    assert [workflow["workflowId"] for workflow in document["workflows"]] == [
        "workflow-UC-1",
    ]
    assert planning_failures[0]["workflowId"] == "workflow-UC-2"


def test_independent_llm_workflow_plans_are_generated_concurrently(monkeypatch) -> None:
    openapi = _openapi_with_secondary_operation("UC-1", "UC-2")
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), openapi)
    rendezvous = Barrier(2)

    def generate_candidate(_client, candidate, _openapi, _total, _execution_candidates):
        rendezvous.wait(timeout=3)
        return attach_workflow_trace(
            {
                "workflowId": candidate["workflowId"],
                "steps": [{"stepId": "health", "operationId": "health"}],
            },
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", generate_candidate)

    document, _planning_failures = dynamic._generate_document(object(), candidates, openapi)

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


def test_workflow_generation_uses_medium_reasoning_by_default(
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
    assert request["reasoning_effort"] == "medium"


@pytest.mark.parametrize("failure", ["reference", "schema", "length"])
def test_generation_repairs_rejected_candidate_without_executing_it(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    openapi = _openapi_with_secondary_operation("UC-1")
    openapi["paths"]["/health"]["get"]["parameters"] = [
        {"name": "studentId", "in": "query", "schema": {"type": "string"}}
    ]
    candidates = build_workflow_candidates(_requirements(), _use_cases(), openapi)
    def reject_candidate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise dynamic.ArazzoValidationError(f"rejected {failure} candidate")

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", reject_candidate)
    document, planning_failures = dynamic._generate_document(object(), candidates, openapi)

    # Graph correction is internal to a candidate authoring run.  A terminal
    # rejection remains a test-plan defect and never manufactures execution.
    assert document is None
    assert planning_failures[0]["defectClass"] == "TEST_DEFECT"
    assert failure in planning_failures[0]["reason"]


def test_unrepaired_unknown_step_remains_a_test_defect(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_execution(*_args, **_kwargs):
        pytest.fail("Invalid plan must not execute against the generated application")

    candidate = build_workflow_candidates(
        _requirements(), _use_cases(), _openapi_with_secondary_operation("UC-1")
    )[0]
    monkeypatch.setattr(
        dynamic,
        "_generate_document",
        lambda *_args: (None, [_planning_failure(candidate, "value references an unknown local step")]),
    )
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

    revised = deepcopy(_document()["workflows"][0])
    revised["steps"][0]["outputs"] = {"payload": "$response.body"}
    if weaken_oracle:
        revised["steps"][0].pop("successCriteria")

    def generate(_client, _candidate, correction_context=None):
        prompts.append(json.dumps(correction_context, ensure_ascii=False))
        return {"graph": True}

    def compile_decision(_decision, _candidate):
        return deepcopy(revised)

    def project(_candidate, _graph):
        return _candidate, {}, []

    monkeypatch.setattr(dynamic, "_select_semantic_producers", lambda *_args: [])
    monkeypatch.setattr(dynamic, "_validated_graph_projection", project)
    monkeypatch.setattr(dynamic, "_select_literal_values", lambda *_args: [])
    monkeypatch.setattr(dynamic, "_compile_workflow_decision", compile_decision)
    monkeypatch.setattr(dynamic, "_validate_document", lambda document, *_args: document)

    monkeypatch.setattr(dynamic, "_client", object)
    monkeypatch.setattr(dynamic, "_generate_workflow_graph", generate)
    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    report = dynamic.dynamic_functional_node(_state())["dynamic_functional_report"]
    assert "RUNTIME_EXPRESSION_UNRESOLVED" in prompts[0]
    assert "#/invented" in prompts[0]
    assert '"executionFailure"' in prompts[0]
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
        return {"graph": True}

    revised = deepcopy(_document()["workflows"][0])
    revised["steps"][0]["outputs"] = {"payload": "$response.body"}

    monkeypatch.setattr(dynamic, "_select_semantic_producers", lambda *_args: [])
    monkeypatch.setattr(dynamic, "_validated_graph_projection", lambda candidate, _graph: (candidate, {}, []))
    monkeypatch.setattr(dynamic, "_select_literal_values", lambda *_args: [])
    monkeypatch.setattr(
        dynamic, "_compile_workflow_decision",
        lambda *_args: deepcopy(revised),
    )
    monkeypatch.setattr(dynamic, "_validate_document", lambda document, *_args: document)
    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    monkeypatch.setattr(
        dynamic,
        "_client",
        object if defect == "TEST_DEFECT" else unexpected_generation,
    )
    monkeypatch.setattr(dynamic, "_generate_workflow_graph", repair_generation)
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
    monkeypatch.setattr(
        dynamic,
        "_generate_candidate_workflow",
        lambda *_args, **_kwargs: pytest.fail("complete preserved plans need no generation"),
    )

    report = dynamic.dynamic_functional_node(_state())["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    assert report["candidatePlan"] == _document()
    assert report["candidatePlan"]["arazzo"] == "1.1.0"
    assert "cases" not in report["candidatePlan"]
    assert calls == ["workflow-UC-1"]
    assert report["executionOrder"] == ["workflow-UC-1"]


def test_partial_preserved_plan_generates_only_missing_workflows_and_keeps_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state["testing_input"]["contract_artifacts"]["requirements"]["content"] = _requirements(11)
    state["testing_input"]["contract_artifacts"]["use_cases"]["content"] = _use_cases(11)
    openapi = deepcopy(state["testing_input"]["contract_artifacts"]["openapi"]["content"])
    openapi["paths"]["/health"]["get"]["x-easydep-use-case-ids"] = [
        f"UC-{index}" for index in range(1, 12)
    ]
    state["testing_input"]["contract_artifacts"]["openapi"]["content"] = openapi
    candidates = build_workflow_candidates(_requirements(11), _use_cases(11), openapi)
    all_workflows = [
        attach_workflow_trace(
            {"workflowId": candidate["workflowId"], "steps": [{"stepId": "health", "operationId": "health"}]},
            candidate,
        )
        for candidate in candidates
    ]
    preserved_indexes = {0, 1, 3, 5, 6, 8, 9, 10}
    state["fixed_arazzo_document"] = build_arazzo_document(
        [workflow for index, workflow in enumerate(all_workflows) if index in preserved_indexes]
    )
    generated: list[str] = []

    def generate(_client: Any, candidate: dict[str, Any], *_args: Any) -> dict[str, Any]:
        workflow_id = str(candidate["workflowId"])
        generated.append(workflow_id)
        if workflow_id == "workflow-UC-5":
            raise dynamic.ArazzoPlanningError("synthetic planning failure")
        return attach_workflow_trace(
            {"workflowId": workflow_id, "steps": [{"stepId": "health", "operationId": "health"}]},
            candidate,
        )

    monkeypatch.setattr(dynamic, "_generate_candidate_workflow", generate)
    monkeypatch.setattr(
        dynamic,
        "execute_arazzo_workflow",
        lambda _document, workflow_id, **_kwargs: _pass(workflow_id),
    )

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert generated == ["workflow-UC-3", "workflow-UC-5", "workflow-UC-8"]
    assert [item["workflowId"] for item in report["candidatePlan"]["workflows"]] == [
        "workflow-UC-1",
        "workflow-UC-2",
        "workflow-UC-3",
        "workflow-UC-4",
        "workflow-UC-6",
        "workflow-UC-7",
        "workflow-UC-8",
        "workflow-UC-9",
        "workflow-UC-10",
        "workflow-UC-11",
    ]
    assert [item["workflowId"] for item in report["planningFailures"]] == ["workflow-UC-5"]


def test_fixed_leaf_input_is_replayed_from_plan_without_runtime_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi = _openapi()
    openapi["paths"]["/health"]["get"]["parameters"] = [
        {
            "name": "sample",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
        }
    ]
    document = _document()
    document["workflows"][0]["steps"][0]["parameters"] = [
        {"name": "sample", "in": "query", "value": "fixed"}
    ]
    executions: list[dict[str, Any]] = []

    def execute(_document: dict[str, Any], workflow_id: str, **kwargs: Any) -> dict[str, Any]:
        executions.append(kwargs)
        return _pass(workflow_id)

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    state = _state(fixed_arazzo_document=document)
    state["testing_input"]["contract_artifacts"]["openapi"]["content"] = openapi

    report = dynamic.dynamic_functional_node(state)["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    assert len(executions) == 1
    assert executions[0]["propose_input"] is None
    assert executions[0]["require_explicit_values"] is True
    assert report["candidatePlan"]["workflows"][0]["steps"][0]["parameters"] == [
        {"name": "sample", "in": "query", "value": "fixed"}
    ]


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


def test_nested_workflow_inputs_are_preserved_without_runtime_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def execute(_document: dict[str, Any], workflow_id: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((workflow_id, deepcopy(kwargs)))
        return _pass(workflow_id)

    monkeypatch.setattr(dynamic, "execute_arazzo_workflow", execute)
    report = dynamic.dynamic_functional_node(
        _state(2, fixed_workflow_inputs={"workflow-UC-2": {"seed": "child-value"}})
    )["dynamic_functional_report"]

    assert report["gateStatus"] == "PASS"
    child_call = next(kwargs for workflow_id, kwargs in calls if workflow_id == "workflow-UC-2")
    assert child_call["workflow_inputs"] == {"seed": "child-value"}
    assert child_call["workflow_inputs_by_id"]["workflow-UC-2"] == {
        "seed": "child-value"
    }
    assert all(kwargs["propose_input"] is None for _, kwargs in calls)
    assert all(kwargs["require_explicit_values"] is True for _, kwargs in calls)


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


def test_identity_producer_requests_require_explicit_caller_identifier_contract() -> None:
    candidate = {
        "useCase": {
            "trigger": "A user requests a resource operation.",
            "main_scenario": [{"sentence": "The system creates the resource."}],
            "success_guarantee": [{"sentence": "The resource exists."}],
            "public_contract": {"required_values": [
                {"name": "resource_id", "value_type": "identifier", "source": "caller_input"},
                {"name": "sample_id", "value_type": "identifier", "source": "system_result"},
            ]},
        },
        "operations": [{"operationId": "consume"}],
        "setupOperations": [{"operationId": "create", "method": "POST"}],
        "planningModel": {"targetOperationIds": ["consume"]},
    }
    steps = [{
        "operationId": "consume",
        "inputs": [
            {"inputSlot": "path:externalKey", "type": "string", "format": "uuid"},
            {"inputSlot": "path:sampleName", "type": "string"},
            {"inputSlot": "path:count", "type": "integer"},
        ],
    }, {
        "operationId": "create",
        "inputs": [{"inputSlot": "path:parentId", "type": "string", "format": "uuid"}],
    }]

    requests = dynamic._identity_input_requests(candidate, steps, target_only=True)
    assert requests == [{
        "targetOperationId": "consume",
        "targetInputSlot": "path:externalKey",
        "inputContract": {"type": "string", "format": "uuid", "literalAllowed": False},
        "useCaseEvidence": {
            "trigger": candidate["useCase"]["trigger"],
            "main_scenario": candidate["useCase"]["main_scenario"],
            "success_guarantee": candidate["useCase"]["success_guarantee"],
            "public_contract": {"required_values": candidate["useCase"]["public_contract"]["required_values"]},
        },
    }]


def test_integer_path_literals_allow_operands_but_reject_marked_resource_identity() -> None:
    assert dynamic._literal_input_allowed({"inputSlot": "path:count", "type": "integer"})
    assert not dynamic._literal_input_allowed({
        "inputSlot": "path:resourceId", "type": "integer", "resourceRole": "resource_id",
    })


def test_selected_semantic_producer_must_be_the_actual_graph_binding() -> None:
    candidate = {
        "workflowId": "workflow-resource",
        "operations": [{"operationId": "consume"}],
        "setupOperations": [{"operationId": "create", "method": "POST"}],
        "planningModel": {
            "targetOperationIds": ["consume"],
            "producerSelections": [{
                "targetOperationId": "consume", "targetInputSlot": "path:resourceId",
                "sourceOperationId": "create", "sourceOutputName": "bodyId",
            }],
            "availableSteps": [
                {"stepId": "create", "operationId": "create", "method": "POST", "inputs": [],
                 "outputs": [{"outputName": "bodyId", "slot": "body.id", "type": "string",
                              "format": "uuid", "cardinality": "one", "outputExpression": "$response.body#/id"},
                             {"outputName": "otherId", "slot": "body.otherId", "type": "string",
                              "format": "uuid", "cardinality": "one", "outputExpression": "$response.body#/otherId"}]},
                {"stepId": "consume", "operationId": "consume", "method": "POST", "outputs": [],
                 "inputs": [{"inputSlot": "path:resourceId", "type": "string", "format": "uuid",
                             "cardinality": "one", "connections": []}]},
            ],
        },
    }
    graph = {
        "workflowId": "workflow-resource",
        "occurrences": [
            {"occurrenceId": "o1", "operationId": "create"},
            {"occurrenceId": "o2", "operationId": "consume"},
        ],
        "requiredInputs": [{
            "targetOccurrenceId": "o2", "targetInputSlot": "path:resourceId", "literalNeeded": False,
            "sourceOccurrenceId": "o1", "sourceOutputName": "bodyId",
        }],
        "distinctResourcePairs": [],
    }

    selected, decision, _ = dynamic._validated_graph_projection(candidate, graph)

    assert decision["connectionIds"] == ["o1.bodyId->o2.path:resourceId"]
    assert selected["planningModel"]["availableSteps"][1]["inputs"][0]["connections"][0]["sourceStepId"] == "o1"
    wrong = deepcopy(graph)
    wrong["requiredInputs"][0]["sourceOutputName"] = "otherId"
    with pytest.raises(dynamic.ArazzoPlanningError, match="did not honor the selected semantic producer"):
        dynamic._validated_graph_projection(candidate, wrong)


def test_collection_selection_is_exposed_only_when_schema_catalog_has_candidates() -> None:
    candidate = {
        "workflowId": "workflow-no-arrays",
        "planningModel": {"availableSteps": [{
            "operationId": "read", "inputs": [], "outputs": [], "successStatuses": ["200"],
        }]},
    }

    schema = dynamic._graph_response_format(candidate)["json_schema"]["schema"]

    assert "collectionSelections" not in schema["properties"]
    assert "collectionSelections" not in dynamic._graph_catalog(candidate)[0]


def test_openapi_projection_builds_finite_collection_item_paths_without_index_selectors() -> None:
    candidate = {
        "workflowId": "workflow-list",
        "operations": [{"operationId": "listEntries"}],
        "setupOperations": [],
    }
    openapi = {"paths": {"/entries": {"get": {
        "operationId": "listEntries",
        "responses": {"200": {"description": "Entries", "content": {"application/json": {
            "schema": {"type": "object", "properties": {"entries": {
                "type": "array", "items": {"type": "object", "properties": {
                    "entryId": {"type": "string", "format": "uuid"},
                    "offeringId": {"type": "string"},
                }},
            }}}
        }}}},
    }}}}

    step = build_execution_candidates([candidate], openapi)[0]

    assert step["collectionSelectionCandidates"] == [
        {
            "selectionId": "listEntries:bodyEntries0EntryId=>bodyEntries0OfferingId",
            "arrayRootPointer": "#/entries", "matchOutputName": "bodyEntries0EntryId",
            "matchOutputExpression": "$response.body#/entries/0/entryId",
            "matchType": "string", "matchFormat": "uuid",
            "matchItemPointerParts": ["entryId"], "selectedOutputName": "bodyEntries0OfferingId",
            "selectedOutputExpression": "$response.body#/entries/0/offeringId",
            "selectedType": "string", "selectedFormat": "",
            "selectedItemPointerParts": ["offeringId"],
        },
        {
            "selectionId": "listEntries:bodyEntries0OfferingId=>bodyEntries0EntryId",
            "arrayRootPointer": "#/entries", "matchOutputName": "bodyEntries0OfferingId",
            "matchOutputExpression": "$response.body#/entries/0/offeringId",
            "matchType": "string", "matchFormat": "",
            "matchItemPointerParts": ["offeringId"], "selectedOutputName": "bodyEntries0EntryId",
            "selectedOutputExpression": "$response.body#/entries/0/entryId",
            "selectedType": "string", "selectedFormat": "uuid",
            "selectedItemPointerParts": ["entryId"],
        },
    ]



def test_get_collection_producer_is_available_only_as_deferred_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    candidate = {
        "workflowId": "workflow-get-collection",
        "operations": [{"operationId": "consume"}],
        "setupOperations": [{"operationId": "list", "method": "GET", "linkedUseCaseEvidence": []}],
        "useCase": {"public_contract": {"required_values": []}},
        "planningModel": {
            "targetOperationIds": ["consume"],
            "availableSteps": [
                {"operationId": "consume", "stepId": "consume", "method": "POST", "inputs": [
                    {"inputSlot": "path:resourceId", "type": "string", "format": "uuid", "cardinality": "one"},
                ], "outputs": []},
                {"operationId": "list", "stepId": "list", "method": "GET", "inputs": [], "outputs": [
                    {"outputName": "body0Id", "slot": "body[].id", "type": "string", "format": "uuid",
                     "cardinality": "one", "outputExpression": "$response.body#/0/id"},
                    {"outputName": "body0ParentId", "slot": "body[].parentId", "type": "string", "format": "uuid",
                     "cardinality": "one", "outputExpression": "$response.body#/0/parentId"},
                ], "collectionSelectionCandidates": [{
                    "selectionId": "list:match=>body0Id", "selectedOutputName": "body0Id",
                    "selectedOutputExpression": "$response.body#/0/id", "selectedType": "string",
                    "selectedFormat": "uuid", "matchOutputName": "body0ParentId",
                    "matchOutputExpression": "$response.body#/0/parentId", "matchType": "string",
                    "matchFormat": "uuid", "arrayRootPointer": "#",
                    "matchItemPointerParts": ["parentId"], "selectedItemPointerParts": ["id"],
                }, {
                    "selectionId": "list:match=>body0ParentId", "selectedOutputName": "body0ParentId",
                    "selectedOutputExpression": "$response.body#/0/parentId", "selectedType": "string",
                    "selectedFormat": "uuid", "matchOutputName": "body0Id",
                    "matchOutputExpression": "$response.body#/0/id", "matchType": "string",
                    "matchFormat": "uuid", "arrayRootPointer": "#",
                    "matchItemPointerParts": ["id"], "selectedItemPointerParts": ["parentId"],
                }],
                },
            ],
        },
    }
    list_step = candidate["planningModel"]["availableSteps"][1]
    for index in range(2, 12):
        output_name = f"bodyExtra{index}Id"
        list_step["outputs"].append({
            "outputName": output_name, "slot": f"body[].extra{index}Id", "type": "string", "format": "uuid",
            "cardinality": "one", "outputExpression": f"$response.body#/0/extra{index}Id",
        })
        list_step["collectionSelectionCandidates"].append({
            "selectionId": f"list:body0ParentId=>{output_name}", "arrayRootPointer": "#",
            "matchOutputName": "body0ParentId", "matchType": "string", "matchFormat": "uuid",
            "selectedOutputName": output_name, "selectedType": "string", "selectedFormat": "uuid",
        })
    observed: dict[str, Any] = {}

    class FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            payload = json.loads(kwargs["messages"][1]["content"])
            observed.update(payload)
            decision = {"decision": "select", "sourceOptionId": "list.body0Id",
                        "evidenceRefs": []}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(decision)))])

    monkeypatch.setattr(dynamic, "build_arazzo_llm_connection", lambda: SimpleNamespace(
        provider="cloudflare", model="openai/gpt-oss-120b"
    ))

    selections = dynamic._select_semantic_producers(
        SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())), candidate, {"components": {}}
    )

    assert len(observed["producerOptions"]) == 12
    assert observed["producerOptions"][0]["collectionLookupAvailable"] is True
    catalog = observed["finiteCollectionCatalog"]
    assert len(catalog) == 1
    item_field_names = [field["outputName"] for field in catalog[0]["itemFields"]]
    assert len(item_field_names) == len(set(item_field_names)) == 12
    assert {
        field["outputName"]: field["outputExpression"] for field in catalog[0]["itemFields"]
    } == {output["outputName"]: output["outputExpression"] for output in list_step["outputs"]}
    assert all(option["collectionArrayRoots"] == ["list:#"] for option in observed["producerOptions"])
    assert "selectionId" not in json.dumps(catalog)
    assert any("matching row, uniqueness" in rule for rule in observed["rules"])
    assert selections == [{
        "targetOperationId": "consume", "targetInputSlot": "path:resourceId",
        "decision": "deferred_collection_lookup", "sourceOperationId": "list",
        "sourceOutputName": "body0Id",
    }]
    list_step["collectionSelectionCandidates"].extend({
        "selectionId": f"list:match{index}=>body0Id", "arrayRootPointer": "#",
        "matchOutputName": f"body0Parent{index}", "matchType": "string", "matchFormat": "uuid",
        "matchItemPointerParts": [f"parent{index}"], "selectedOutputName": "body0Id",
        "selectedType": "string", "selectedFormat": "uuid", "selectedItemPointerParts": ["id"],
    } for index in range(1, 26))
    candidate["planningModel"]["producerSelections"] = selections
    graph_catalog = dynamic._graph_catalog(candidate)
    projected_choices = graph_catalog[1]["collectionSelectionCandidates"]
    full_choices = list_step["collectionSelectionCandidates"]
    graph_schema = dynamic._graph_response_format(candidate)["json_schema"]["schema"]
    selection_ids = graph_schema["properties"]["collectionSelections"]["items"]["properties"]["selectionId"]["enum"]
    graph_choices = [item for item in full_choices if item["selectedOutputName"] == "body0Id"]
    assert "collectionSelections" in graph_schema["required"]
    assert graph_schema["properties"]["collectionSelections"]["minItems"] == 1
    assert "list:match=>body0Id" in selection_ids
    assert len(selection_ids) == len(graph_choices)
    assert len({field["outputName"] for field in catalog[0]["itemFields"]}) == len(catalog[0]["itemFields"])
    assert len(json.dumps(projected_choices, separators=(",", ":"))) < len(json.dumps(full_choices, separators=(",", ":")))
    assert all(set(choice) == {
        "selectionId", "matchOutputName", "matchType", "matchFormat",
        "selectedOutputName", "selectedType", "selectedFormat",
    } for choice in projected_choices)


def test_deferred_collection_selection_compiles_finite_selector_from_fixed_literal() -> None:
    candidate = {
        "workflowId": "workflow-collection",
        "trace": {"useCaseIds": ["UC-collection"]},
        "operations": [{"operationId": "cancel"}],
        "setupOperations": [
            {"operationId": "createOffering", "method": "POST"},
            {"operationId": "listEntries", "method": "GET"},
        ],
        "planningModel": {
            "targetOperationIds": ["cancel"],
            "producerSelections": [{
                "targetOperationId": "cancel", "targetInputSlot": "path:entryId",
                "decision": "deferred_collection_lookup", "sourceOperationId": "listEntries",
                "sourceOutputName": "body0EntryId",
            }],
            "availableSteps": [
                {"stepId": "createOffering", "operationId": "createOffering", "method": "POST",
                 "successStatuses": ["201"], "inputs": [{
                     "inputSlot": "body.offeringId", "type": "string", "format": "uuid",
                     "cardinality": "one", "pointerParts": ("offeringId",),
                 }], "outputs": []},
                {"stepId": "listEntries", "operationId": "listEntries", "method": "GET",
                 "successStatuses": ["200"], "inputs": [], "outputs": [
                     {"outputName": "body0EntryId", "slot": "body[].entryId", "type": "string",
                      "format": "uuid", "cardinality": "one", "outputExpression": "$response.body#/entries/0/entryId",
                      "collectionItemRef": {"arrayRootPointer": "#/entries", "itemPointerParts": ["entryId"]}},
                     {"outputName": "body0OfferingId", "slot": "body[].offeringId", "type": "string",
                      "format": "uuid", "cardinality": "one", "outputExpression": "$response.body#/entries/0/offeringId",
                      "collectionItemRef": {"arrayRootPointer": "#/entries", "itemPointerParts": ["offeringId"]}},
                 ], "collectionSelectionCandidates": [{
                     "selectionId": "listEntries:body0OfferingId=>body0EntryId",
                     "arrayRootPointer": "#/entries", "matchOutputName": "body0OfferingId",
                     "matchOutputExpression": "$response.body#/entries/0/offeringId",
                         "matchType": "string", "matchFormat": "",
                     "matchItemPointerParts": ["offeringId"], "selectedOutputName": "body0EntryId",
                     "selectedOutputExpression": "$response.body#/entries/0/entryId",
                     "selectedType": "string", "selectedFormat": "uuid",
                     "selectedItemPointerParts": ["entryId"],
                 }]},
                {"stepId": "cancel", "operationId": "cancel", "method": "POST", "successStatuses": ["204"],
                 "inputs": [{"inputSlot": "path:entryId", "type": "string", "format": "uuid",
                             "cardinality": "one", "connections": []}], "outputs": []},
            ],
        },
    }
    graph = {
        "workflowId": "workflow-collection",
        "occurrences": [
            {"occurrenceId": "o1", "operationId": "createOffering"},
            {"occurrenceId": "o2", "operationId": "listEntries"},
            {"occurrenceId": "o3", "operationId": "cancel"},
        ],
        "requiredInputs": [
            {"targetOccurrenceId": "o1", "targetInputSlot": "body.offeringId", "literalNeeded": True},
            {"targetOccurrenceId": "o3", "targetInputSlot": "path:entryId", "literalNeeded": False,
             "sourceOccurrenceId": "o2", "sourceOutputName": "body0EntryId"},
        ],
        "distinctResourcePairs": [],
        "collectionSelections": [{
            "selectionId": "listEntries:body0OfferingId=>body0EntryId",
            "collectionOccurrenceId": "o2", "targetOccurrenceId": "o3", "targetInputSlot": "path:entryId",
            "fixedInputOccurrenceId": "o1", "fixedInputSlot": "body.offeringId",
        }],
    }

    selected, decision, literals = dynamic._validated_graph_projection(candidate, graph)
    assert [(item[0], item[1]) for item in literals] == [("o1", "body.offeringId")]
    decision["fixedInputs"] = [{"targetStepId": "o1", "targetInputSlot": "body.offeringId",
                                "value": "123e4567-e89b-12d3-a456-426614174000"}]
    workflow = dynamic._compile_workflow_decision(decision, selected)

    assert workflow["steps"][1]["outputs"]["body0EntryId"] == {
        "type": "jsonpath", "context": "$response.body",
        "selector": '$["entries"][?@["offeringId"] == "123e4567-e89b-12d3-a456-426614174000"]["entryId"]',
    }
    assert "[0]" not in workflow["steps"][1]["outputs"]["body0EntryId"]["selector"]
    assert "{$" not in workflow["steps"][1]["outputs"]["body0EntryId"]["selector"]
    openapi = {"openapi": "3.0.3", "info": {"title": "API", "version": "1"}, "paths": {
            "/offerings": {"post": {
                "operationId": "createOffering",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "required": ["offeringId"],
                    "properties": {"offeringId": {"type": "string", "format": "uuid"}},
                }}}},
                "responses": {"201": {"description": "created"}},
            }},
        "/entries": {"get": {"operationId": "listEntries", "responses": {
            "200": {"description": "listed", "content": {"application/json": {"schema": {
                "type": "object", "properties": {"entries": {"type": "array", "items": {
                    "type": "object", "properties": {
                        "entryId": {"type": "string", "format": "uuid"},
                        "offeringId": {"type": "string", "format": "uuid"},
                    },
                }}}},
            }}}},
        }}},
        "/entries/{entryId}": {"post": {"operationId": "cancel", "parameters": [
            {"name": "entryId", "in": "path", "required": True,
             "schema": {"type": "string", "format": "uuid"}},
        ], "responses": {"204": {"description": "canceled"}}}},
    }
    import jsonpath_rfc9535

    jsonpath_rfc9535.compile(workflow["steps"][1]["outputs"]["body0EntryId"]["selector"])


def test_graph_reuses_earlier_fixed_mutation_input_for_typed_path_input() -> None:
    candidate = {
        "workflowId": "workflow-shared-fixture",
        "trace": {"useCaseIds": ["UC-shared-fixture"]},
        "operations": [{"operationId": "register"}],
        "setupOperations": [{"operationId": "createOffering", "method": "POST"}],
        "planningModel": {
            "targetOperationIds": ["register"],
            "availableSteps": [
                {"stepId": "createOffering", "operationId": "createOffering", "method": "POST",
                 "successStatuses": ["200"], "outputs": [], "inputs": [{
                     "inputSlot": "body.courseOfferingId", "type": "string", "format": "uuid",
                     "cardinality": "one", "pointerParts": ("courseOfferingId",),
                 }]},
                {"stepId": "register", "operationId": "register", "method": "POST",
                 "successStatuses": ["201"], "outputs": [], "inputs": [{
                     "inputSlot": "path:courseOfferingId", "type": "string", "format": "uuid",
                     "cardinality": "one", "connections": [],
                 }]},
            ],
        },
    }
    graph = {
        "workflowId": "workflow-shared-fixture",
        "occurrences": [
            {"occurrenceId": "o1", "operationId": "createOffering"},
            {"occurrenceId": "o2", "operationId": "register"},
        ],
        "requiredInputs": [
            {"targetOccurrenceId": "o1", "targetInputSlot": "body.courseOfferingId", "literalNeeded": True},
            {"targetOccurrenceId": "o2", "targetInputSlot": "path:courseOfferingId", "literalNeeded": False,
             "sourceInputOccurrenceId": "o1", "sourceInputSlot": "body.courseOfferingId"},
        ],
        "distinctResourcePairs": [],
    }

    selected, decision, literals = dynamic._validated_graph_projection(candidate, graph)

    assert [(item[0], item[1]) for item in literals] == [("o1", "body.courseOfferingId")]
    assert decision["fixedInputReuses"] == [{
        "sourceStepId": "o1", "sourceInputSlot": "body.courseOfferingId",
        "targetStepId": "o2", "targetInputSlot": "path:courseOfferingId",
    }]
    decision["fixedInputs"] = [{
        "targetStepId": "o1", "targetInputSlot": "body.courseOfferingId",
        "value": "123e4567-e89b-12d3-a456-426614174000",
    }]
    workflow = dynamic._compile_workflow_decision(decision, selected)

    assert workflow["steps"][0]["requestBody"]["payload"]["courseOfferingId"] == "123e4567-e89b-12d3-a456-426614174000"
    assert workflow["steps"][1]["parameters"] == [{
        "name": "courseOfferingId", "in": "path", "value": "123e4567-e89b-12d3-a456-426614174000",
    }]


@pytest.mark.parametrize("matches, expected", [([], False), (["123e4567-e89b-12d3-a456-426614174000"], True), ([
    "123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174001",
], False), (["not-a-uuid"], False)])
def test_collection_selector_scalar_cardinality_and_uuid_validation_before_downstream(
    matches: list[str], expected: bool,
) -> None:
    from app.testing.utils.arazzo_executor import _ExecutionError, _parameters, operation_for_id

    openapi = {"paths": {"/entries/{entryId}": {"delete": {
        "operationId": "cancelEntry",
        "parameters": [{"name": "entryId", "in": "path", "required": True,
                        "schema": {"type": "string", "format": "uuid"}}],
    }}}}
    operation = operation_for_id(openapi, "cancelEntry")
    selector_output = {
        "type": "jsonpath", "context": "$response.body",
        "selector": '$[?@["offeringId"] == "chosen"]["entryId"]',
    }
    context = {"response": {"body": [{"offeringId": "chosen", "entryId": item} for item in matches]}}
    if not expected:
        with pytest.raises(_ExecutionError, match="Input path.entryId is invalid"):
            _parameters(operation, {"parameters": [{"name": "entryId", "in": "path", "value": selector_output}]},
                        context, openapi, None)
        return
    paths, _query, _headers, _body = _parameters(
        operation, {"parameters": [{"name": "entryId", "in": "path", "value": selector_output}]},
        context, openapi, None,
    )
    assert paths == {"entryId": matches[0]}


@pytest.mark.parametrize(
    "schema, guaranteed",
    [
        ({"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}, True),
        ({"type": "object", "properties": {"id": {"type": "string"}}}, False),
        ({"type": "object", "required": ["id"], "properties": {"id": {"type": ["string", "null"]}}}, False),
    ],
)
def test_semantic_producer_catalog_does_not_overstate_optional_or_nullable_outputs(
    schema: dict[str, Any], guaranteed: bool,
) -> None:
    operation = {"responses": [{"status": "201", "schema": schema}]}
    output = {"outputExpression": "$response.body#/id"}

    assert dynamic._output_guarantees_non_null(operation, output, {"components": {}}) is guaranteed


def test_operation_identity_evidence_preserves_declared_system_result_refs_without_slot_mapping() -> None:
    linked = [{
        "useCaseId": "UC7",
        "required_values": [
            {
                "value_ref": "val_waitlist_entry",
                "name": "waitlist_entry_id",
                "source": "system_result",
                "value_type": "identifier",
                "identity_obligation_ref": "ob_waitlist_entry",
                "requirement_ids": ["RR9"],
            },
            {"value_ref": "val_actor", "name": "student_id", "source": "authenticated_actor_context"},
        ],
    }]

    assert dynamic._operation_identity_evidence(linked) == [{
        "useCaseId": "UC7",
        "scope": "operation",
        "value": {
            "value_ref": "val_waitlist_entry",
            "name": "waitlist_entry_id",
            "value_type": "identifier",
            "identity_obligation_ref": "ob_waitlist_entry",
            "requirement_ids": ["RR9"],
        },
    }]


@pytest.mark.parametrize(
    "schema, guaranteed",
    [
        ({"type": "string", "format": "uuid"}, True),
        ({"type": ["string", "null"], "format": "uuid"}, False),
        ({"type": "string", "nullable": True}, False),
    ],
)
def test_root_scalar_response_can_be_reported_as_non_null_when_schema_guarantees_it(
    schema: dict[str, Any], guaranteed: bool,
) -> None:
    operation = {"responses": [{"status": "201", "schema": schema}]}
    output = {"outputExpression": "$response.body#"}

    assert dynamic._output_guarantees_non_null(operation, output, {"components": {}}) is guaranteed


def test_semantic_producer_selection_closes_only_selected_source_path_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    candidate = {
        "workflowId": "workflow-closure",
        "operations": [{"operationId": "consume"}],
        "setupOperations": [
            {"operationId": "join", "method": "POST", "linkedUseCaseEvidence": [], "responses": [
                {"status": "201", "schema": {"type": "string", "format": "uuid"}},
            ]},
            {"operationId": "manage", "method": "POST", "linkedUseCaseEvidence": [], "responses": [
                {"status": "201", "schema": {"type": "object", "properties": {
                    "offeringId": {"type": "string", "format": "uuid"},
                }}},
            ]},
        ],
        "useCase": {"trigger": "A caller requests cancellation.",
                    "main_scenario": [{"sentence": "The system creates a waitlist entry."}],
                    "success_guarantee": [{"sentence": "The entry is canceled."}],
                    "public_contract": {"required_values": []}},
        "planningModel": {
            "targetOperationIds": ["consume"],
            "availableSteps": [
                {"operationId": "consume", "stepId": "consume", "method": "POST", "inputs": [
                    {"inputSlot": "path:resourceId", "type": "string", "format": "uuid", "cardinality": "one"},
                ], "outputs": []},
                {"operationId": "join", "stepId": "join", "method": "POST", "inputs": [
                    {"inputSlot": "path:offeringId", "type": "string", "cardinality": "one"},
                    {"inputSlot": "body.courseOfferingId", "type": "string", "format": "uuid", "cardinality": "one"},
                ], "outputs": [
                    {"outputName": "bodyValue", "slot": "body", "type": "string", "format": "uuid",
                     "cardinality": "one", "outputExpression": "$response.body#", "responseDescription": "identifier returned"},
                ]},
                {"operationId": "manage", "stepId": "manage", "method": "POST", "inputs": [
                    {"inputSlot": "body.courseOfferingId", "type": "string", "format": "uuid", "cardinality": "one"},
                ], "outputs": [
                    {"outputName": "bodyOfferingId", "slot": "body.offeringId", "type": "string", "format": "uuid",
                     "cardinality": "one", "outputExpression": "$response.body#/offeringId", "responseDescription": "offering identifier"},
                ]},
            ],
        },
    }
    selected_options = {
        ("consume", "path:resourceId"): "join.bodyValue",
        ("join", "path:offeringId"): "manage.bodyOfferingId",
    }
    observed_targets: list[tuple[str, str]] = []
    raw_decisions: list[dict[str, Any]] = []
    observed_options: list[list[str]] = []
    observed_use_case_evidence: list[dict[str, Any]] = []

    class FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            payload = json.loads(kwargs["messages"][1]["content"])
            target = payload["target"]
            key = (target["targetOperationId"], target["targetInputSlot"])
            observed_targets.append(key)
            observed_options.append([item["optionId"] for item in payload["producerOptions"]])
            observed_use_case_evidence.append(target["useCaseEvidence"])
            assert "not an already-persisted test fixture" in " ".join(payload["rules"])
            choice = selected_options[key]
            decision = {
                "decision": "select", "sourceOptionId": choice, "evidenceRefs": [],
            }
            raw_decisions.append(deepcopy(decision))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(decision)))])

    class FakeClient:
        chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(dynamic, "build_arazzo_llm_connection", lambda: SimpleNamespace(
        provider="cloudflare", model="openai/gpt-oss-120b"
    ))

    selections = dynamic._select_semantic_producers(FakeClient(), candidate, {"components": {}})

    assert observed_targets == [
        ("consume", "path:resourceId"), ("join", "path:offeringId"),
    ]
    assert [(item["sourceOperationId"], item["sourceOutputName"]) for item in selections] == [
        ("join", "bodyValue"), ("manage", "bodyOfferingId"),
    ]
    assert [item["sourceOptionId"] for item in raw_decisions] == [
        "join.bodyValue", "manage.bodyOfferingId",
    ]
    assert observed_options == [
        ["join.bodyValue", "manage.bodyOfferingId"], ["manage.bodyOfferingId"],
    ]
    assert observed_use_case_evidence[0]["main_scenario"][0]["sentence"] == "The system creates a waitlist entry."
    assert all(item["targetInputSlot"].startswith("path:") for item in selections)
