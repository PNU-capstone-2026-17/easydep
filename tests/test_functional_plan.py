"""Integration tests for Arazzo planning in the dynamic Testing node."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest

from app.testing.nodes import dynamic_functional as dynamic
from app.testing.utils.arazzo_planner import (
    attach_workflow_trace,
    build_arazzo_document,
    build_workflow_candidates,
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


def test_structured_output_is_a_standard_arazzo_workflow_subset() -> None:
    response_format = dynamic._response_format()
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    jsonschema.Draft202012Validator(schema).validate(
        {
            "workflowId": "workflow-UC-1",
            "steps": [
                {
                    "stepId": "health",
                    "operationId": "health",
                    "successCriteria": [{"condition": "$statusCode == 200"}],
                    "onFailure": [
                        {"name": "retryOnce", "type": "retry", "retryLimit": 1}
                    ],
                }
            ],
        }
    )
    prompt = dynamic._prompt(
        build_workflow_candidates(_requirements(), _use_cases(), _openapi())[0]
    )
    assert "Arazzo v1.1 Workflow Object" in prompt
    assert "trace-linked" in prompt
    assert "FunctionalTestCase" not in prompt
    assert "Testing-stage workflow-planning subtask" in dynamic.PLAN_ROLE_PROMPT
    assert "immutable" in dynamic.PLAN_ROLE_PROMPT


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "parameters",
            [{"name": "sample", "in": "query", "value": "{{sample}}"}],
        ),
        (
            "parameters",
            [{"name": "sample", "in": "query", "value": "$inputs.sample"}],
        ),
        ("outputs", {"sample": "$.response.body"}),
        (
            "successCriteria",
            [{"condition": "$statusCode == 200", "type": "simple"}],
        ),
    ],
)
def test_structured_output_rejects_non_arazzo_placeholder_syntax(
    field: str,
    value: Any,
) -> None:
    schema = dynamic._response_format()["json_schema"]["schema"]
    workflow = {
        "workflowId": "workflow-UC-1",
        "steps": [
            {
                "stepId": "health",
                "operationId": "health",
                field: value,
            }
        ],
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(workflow)


def test_empty_object_parameter_value_is_treated_as_unspecified() -> None:
    workflow = {
        "workflowId": "workflow-UC-1",
        "steps": [
            {
                "stepId": "health",
                "operationId": "health",
                "requestBody": {"contentType": "application/json", "payload": {}},
                "parameters": [
                    {"name": "search", "in": "query", "value": {}},
                    {"name": "limit", "in": "query", "value": 10},
                    {"name": "invented", "in": "query", "value": "bad"},
                ],
            }
        ],
    }
    candidate = {
        "operations": [
            {
                "operationId": "health",
                "requestBody": None,
                "parameters": [
                    {"name": "search", "in": "query"},
                    {"name": "limit", "in": "query"},
                ],
            }
        ]
    }

    normalized = dynamic._normalize_authored_workflow(workflow, candidate)

    assert normalized["steps"][0]["parameters"] == [
        {"name": "limit", "in": "query", "value": 10}
    ]
    assert "requestBody" not in normalized["steps"][0]
    assert workflow["steps"][0]["parameters"][0]["value"] == {}
    dynamic._validate_authored_workflow(normalized)


def test_name_value_output_list_is_normalized_to_arazzo_output_map() -> None:
    workflow = {
        "workflowId": "workflow-UC-1",
        "steps": [
            {
                "stepId": "export",
                "operationId": "exportData",
                "outputs": [
                    {"name": "exportFile", "value": "$response.body#/"},
                ],
            }
        ],
    }
    candidate = {
        "operations": [
            {
                "operationId": "exportData",
                "requestBody": None,
                "parameters": [],
            }
        ]
    }

    normalized = dynamic._normalize_authored_workflow(workflow, candidate)

    assert normalized["steps"][0]["outputs"] == {
        "exportFile": "$response.body#/"
    }
    assert isinstance(workflow["steps"][0]["outputs"], list)
    dynamic._validate_authored_workflow(normalized)


def test_ambiguous_output_list_remains_invalid() -> None:
    workflow = {
        "workflowId": "workflow-UC-1",
        "steps": [
            {
                "stepId": "export",
                "operationId": "exportData",
                "outputs": [
                    {"name": "result", "value": "$response.body#/first"},
                    {"name": "result", "value": "$response.body#/second"},
                ],
            }
        ],
    }
    candidate = {
        "operations": [
            {
                "operationId": "exportData",
                "requestBody": None,
                "parameters": [],
            }
        ]
    }

    normalized = dynamic._normalize_authored_workflow(workflow, candidate)

    with pytest.raises(dynamic.ArazzoValidationError):
        dynamic._validate_authored_workflow(normalized)


def test_missing_schema_valid_workflow_output_is_classified_as_sut_defect() -> None:
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
                "finding": {"code": "RUNTIME_EXPRESSION_UNRESOLVED"},
            }
        ],
    }

    dynamic._classify_missing_workflow_data(result, workflow, candidate, openapi)

    assert result["defectClass"] == "SUT_DEFECT"
    assert result["finding"]["code"] == "REQUIRED_WORKFLOW_DATA_MISSING"
    assert result["finding"]["operationId"] == "searchOfferings"


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


def test_generated_document_gets_one_bounded_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = build_workflow_candidates(_requirements(), _use_cases(), _openapi())
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

    document = dynamic._generate_document(object(), candidates, _openapi())

    assert len(calls) == 2
    assert calls[0] == ""
    assert "invented" in calls[1]
    assert document["workflows"][0]["steps"][0]["operationId"] == "health"


def test_generated_document_retries_only_the_failed_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = build_workflow_candidates(_requirements(2), _use_cases(2), _openapi())
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

    document = dynamic._generate_document(object(), candidates, _openapi())

    assert calls == {"workflow-UC-1": 1, "workflow-UC-2": 2}
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
