"""Black-box tests for deterministic Arazzo workflow planning."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.testing.utils.arazzo_planner import (
    ArazzoPlanningError,
    attach_workflow_trace,
    build_arazzo_document,
    build_deterministic_workflow,
    build_execution_candidates,
    build_workflow_candidates,
    _setup_use_case_evidence,
)


def _openapi() -> dict[str, Any]:
    item = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {
            "id": {"type": "string", "description": "The created inventory item identifier."},
            "name": {"type": "string"},
        },
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Inventory API", "version": "1.0.0"},
        "servers": [{"url": "https://untrusted.example.invalid"}],
        "paths": {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "summary": "Create an inventory item",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "x-easydep-scenario-step-refs": ["UC-1:main:1"],
                    "parameters": [
                        {
                            "name": "X-Request-ID",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "A newly created inventory item is returned.",
                            "content": {"application/json": {"schema": item}},
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "x-easydep-use-case-ids": ["UC-1"],
                    "x-easydep-scenario-step-refs": ["UC-1:main:2"],
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": item}},
                        }
                    },
                }
            },
            "/audit": {
                "get": {
                    "operationId": "auditItem",
                    "x-easydep-use-case-ids": ["UC-2"],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/health": {
                "get": {
                    "operationId": "health",
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }


def _requirements() -> list[dict[str, Any]]:
    return [
        {
            "id": "REQ-1",
            "type": "FR",
            "text": "A customer can create and inspect an item.",
            "acceptanceCriteria": [
                {"id": "AC-1", "text": "The item is created with its name."},
                {"id": "AC-2", "text": "The created item can be retrieved by identifier."},
            ],
        },
        {
            "id": "REQ-2",
            "type": "FR",
            "text": "A customer can review audit information.",
            "acceptanceCriteria": [
                {"id": "AC-3", "text": "The audit endpoint returns successfully."}
            ],
        },
    ]


def _use_cases() -> dict[str, Any]:
    return {
        "use_case_specs": [
            {
                "use_case_id": "UC-1",
                "name": "Create and inspect item",
                "requirement_ids": ["REQ-1"],
                "preconditions": ["The inventory service is available."],
                "trigger": "The customer submits an item name.",
                "main_scenario": ["Create the item.", "Retrieve the created item."],
                "acceptance_criteria": ["The returned identifier is stable."],
                "public_contract": {"required_values": [{"field": "name", "value": "sample item"}]},
                "extensions": {"operationAlternatives": ["createItem"]},
            },
            {
                "use_case_id": "UC-2",
                "name": "Review audit information",
                "requirement_ids": ["REQ-2"],
                "preconditions": [],
                "trigger": "The customer opens the audit view.",
                "main_scenario": ["Read the audit endpoint."],
                "acceptance_criteria": ["The audit response is available."],
            },
        ],
        "traceability": {
            "requirements": {
                "REQ-1": {"use_cases": ["UC-1"]},
                "REQ-2": {"use_cases": ["UC-2"]},
            }
        },
    }


def _candidates() -> list[dict[str, Any]]:
    return build_workflow_candidates(_requirements(), _use_cases(), _openapi())


@pytest.mark.parametrize(
    ("mutate", "operation_id", "location"),
    [
        (
            lambda document: document["paths"]["/items/{id}"]["get"]["parameters"][
                0
            ].update(schema={"type": "object"}),
            "getItem",
            "parameter path:id",
        ),
        (
            lambda document: document["paths"]["/items"]["post"]["requestBody"]["content"][
                "application/json"
            ].update(schema={"type": "object", "properties": {}, "additionalProperties": False}),
            "createItem",
            "requestBody",
        ),
        (
            lambda document: document["paths"]["/items"]["post"]["responses"]["201"]["content"][
                "application/json"
            ].update(schema={"type": "object", "properties": {}, "additionalProperties": False}),
            "createItem",
            "response 201",
        ),
    ],
)
def test_execution_candidates_reject_closed_empty_input_or_response_schema(
    mutate, operation_id: str, location: str
) -> None:
    openapi = _openapi()
    mutate(openapi)
    selected = [_candidate_for(build_workflow_candidates(_requirements(), _use_cases(), openapi), "UC-1")]

    with pytest.raises(ArazzoPlanningError, match=rf"{operation_id}.*{location}"):
        build_execution_candidates(selected, openapi)


def test_execution_candidates_expose_finite_typed_connection_choices() -> None:
    candidates = build_execution_candidates([_candidate_for(_candidates(), "UC-1")], _openapi())

    assert [(item["stepId"], item["operationId"]) for item in candidates] == [
        ("createItem", "createItem"),
        ("getItem", "getItem"),
        ("auditItem", "auditItem"),
        ("health", "health"),
    ]
    get_item = candidates[1]
    assert get_item["inputs"][0]["inputSlot"] == "path:id"
    assert get_item["inputs"][0]["connections"] == [
        {
            "connectionId": "createItem.bodyId->getItem.path:id",
            "sourceStepId": "createItem",
            "sourceSlot": "body.id",
            "outputName": "bodyId",
            "outputExpression": "$response.body#/id",
            "targetStepId": "getItem",
            "targetInputSlot": "path:id",
            "value": "$steps.createItem.outputs.bodyId",
        },
        {
            "connectionId": "createItem.bodyName->getItem.path:id",
            "sourceStepId": "createItem",
            "sourceSlot": "body.name",
            "outputName": "bodyName",
            "outputExpression": "$response.body#/name",
            "targetStepId": "getItem",
            "targetInputSlot": "path:id",
            "value": "$steps.createItem.outputs.bodyName",
        },
    ]
    assert candidates[0]["successStatuses"] == ["201"]
    assert candidates[0]["summary"] == "Create an inventory item"
    assert candidates[0]["outputs"][0]["description"] == "The created inventory item identifier."
    assert candidates[0]["outputs"][0]["responseDescription"] == (
        "A newly created inventory item is returned."
    )


def test_uuid_source_format_can_flow_to_unformatted_string_target() -> None:
    openapi = _openapi()
    create_schema = openapi["paths"]["/items"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]
    create_schema["properties"]["id"]["format"] = "uuid"
    create_schema["properties"]["name"]["format"] = "date"
    openapi["paths"]["/items/{id}"]["get"]["parameters"][0]["schema"] = {
        "type": "string",
        "format": "",
    }
    selected = [_candidate_for(build_workflow_candidates(_requirements(), _use_cases(), openapi), "UC-1")]

    get_item = next(
        step for step in build_execution_candidates(selected, openapi)
        if step["operationId"] == "getItem"
    )
    connections = get_item["inputs"][0]["connections"]

    assert [(item["sourceStepId"], item["sourceSlot"]) for item in connections] == [
        ("createItem", "body.id"),
        ("createItem", "body.name"),
    ]


@pytest.mark.parametrize(
    ("source_type", "source_format", "target_type", "target_format", "source_array", "expected"),
    [
        ("string", None, "string", "email", False, True),
        ("string", "uuid", "string", None, False, True),
        ("string", "email", "string", None, False, True),
        ("string", "date", "string", "uuid", False, False),
        ("string", "date", "string", None, False, True),
        ("integer", None, "integer", "int32", False, False),
        ("integer", "int32", "integer", None, False, True),
        ("integer", "int32", "integer", "int64", False, False),
        ("integer", "int32", "integer", "int32", False, True),
        ("integer", None, "integer", None, True, False),
    ],
)
def test_format_compatibility_requires_source_evidence_for_target_format(
    source_type: str,
    source_format: str | None,
    target_type: str,
    target_format: str | None,
    source_array: bool,
    expected: bool,
) -> None:
    openapi = _openapi()
    source_schema: dict[str, Any] = {"type": source_type}
    target_schema: dict[str, Any] = {"type": target_type}
    if source_format:
        source_schema["format"] = source_format
    if target_format:
        target_schema["format"] = target_format
    if source_array:
        source_schema = {"type": "array", "items": source_schema}
    response_schema = openapi["paths"]["/items"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]
    response_schema["properties"]["id"] = source_schema
    openapi["paths"]["/items/{id}"]["get"]["parameters"][0]["schema"] = target_schema
    selected = [_candidate_for(build_workflow_candidates(_requirements(), _use_cases(), openapi), "UC-1")]

    get_item = next(
        step for step in build_execution_candidates(selected, openapi)
        if step["operationId"] == "getItem"
    )
    candidates = [
        item for item in get_item["inputs"][0]["connections"]
        if item["sourceStepId"] == "createItem" and item["sourceSlot"] == "body.id"
    ]

    assert bool(candidates) is expected


def test_object_query_parameter_keeps_openapi_name_and_serialization() -> None:
    openapi = _openapi()
    openapi["paths"]["/search"] = {
        "get": {
            "operationId": "searchItems",
            "x-easydep-use-case-ids": ["UC-2"],
            "parameters": [{
                "name": "searchCriteria",
                "in": "query",
                "required": True,
                "style": "deepObject",
                "explode": True,
                "schema": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "active": {"type": "boolean"},
                    },
                },
            }],
            "responses": {"200": {"description": "ok"}},
        }
    }
    selected = [_candidate_for(build_workflow_candidates(_requirements(), _use_cases(), openapi), "UC-2")]

    search = next(
        step for step in build_execution_candidates(selected, openapi)
        if step["operationId"] == "searchItems"
    )

    assert [slot["inputSlot"] for slot in search["inputs"]] == ["query:searchCriteria"]
    assert search["inputs"][0]["type"] == "object"
    assert search["inputs"][0]["parameterName"] == "searchCriteria"
    assert search["inputs"][0]["parameterStyle"] == "deepObject"
    assert search["inputs"][0]["parameterExplode"] is True
    assert search["inputs"][0]["valueSchema"]["required"] == ["name"]


@pytest.mark.parametrize(
    ("style", "explode", "reason"),
    [
        ("pipeDelimited", False, "object query parameter style"),
        ("deepObject", False, "object query parameter style"),
    ],
)
def test_object_query_parameter_rejects_unsupported_serialization_precisely(
    style: str, explode: bool, reason: str,
) -> None:
    openapi = _openapi()
    openapi["paths"]["/search"] = {
        "get": {
            "operationId": "searchItems",
            "x-easydep-use-case-ids": ["UC-2"],
            "parameters": [{
                "name": "searchCriteria", "in": "query", "required": True,
                "style": style, "explode": explode,
                "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            }],
            "responses": {"200": {"description": "ok"}},
        }
    }
    selected = [_candidate_for(build_workflow_candidates(_requirements(), _use_cases(), openapi), "UC-2")]

    with pytest.raises(ArazzoPlanningError, match=reason):
        build_execution_candidates(selected, openapi)


def test_execution_candidates_offer_untraced_setup_with_typed_connection() -> None:
    openapi = _openapi()
    openapi["paths"]["/audit"]["get"]["parameters"] = [
        {
            "name": "itemId",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
        }
    ]
    candidate = _candidate_for(
        build_workflow_candidates(_requirements(), _use_cases(), openapi), "UC-2"
    )

    choices = build_execution_candidates([candidate], openapi)
    audit = next(step for step in choices if step["operationId"] == "auditItem")

    assert candidate["trace"]["useCaseIds"] == ["UC-2"]
    assert {operation["operationId"] for operation in candidate["setupOperations"]} >= {
        "createItem",
        "getItem",
        "health",
    }
    linked_setup = next(
        operation for operation in candidate["setupOperations"]
        if operation["operationId"] == "createItem"
    )
    assert linked_setup["linkedUseCaseEvidence"] == [{
        "useCaseId": "UC-1",
        "name": "Create and inspect item",
        "required_values": [{"field": "name", "value": "sample item"}],
        "preconditions": ["The inventory service is available."],
        "trigger": "The customer submits an item name.",
        "main_scenario": ["Create the item.", "Retrieve the created item."],
        "acceptance_criteria": ["The returned identifier is stable."],
        "extensions": {"operationAlternatives": ["createItem"]},
    }]
    assert audit["inputs"][0]["connections"] == [
        {
            "connectionId": "createItem.bodyId->auditItem.query:itemId",
            "sourceStepId": "createItem",
            "sourceSlot": "body.id",
            "outputName": "bodyId",
            "outputExpression": "$response.body#/id",
            "targetStepId": "auditItem",
            "targetInputSlot": "query:itemId",
            "value": "$steps.createItem.outputs.bodyId",
        },
        {
            "connectionId": "createItem.bodyName->auditItem.query:itemId",
            "sourceStepId": "createItem",
            "sourceSlot": "body.name",
            "outputName": "bodyName",
            "outputExpression": "$response.body#/name",
            "targetStepId": "auditItem",
            "targetInputSlot": "query:itemId",
            "value": "$steps.createItem.outputs.bodyName",
        },
        {
            "connectionId": "getItem.bodyId->auditItem.query:itemId",
            "sourceStepId": "getItem",
            "sourceSlot": "body.id",
            "outputName": "bodyId",
            "outputExpression": "$response.body#/id",
            "targetStepId": "auditItem",
            "targetInputSlot": "query:itemId",
            "value": "$steps.getItem.outputs.bodyId",
        },
        {
            "connectionId": "getItem.bodyName->auditItem.query:itemId",
            "sourceStepId": "getItem",
            "sourceSlot": "body.name",
            "outputName": "bodyName",
            "outputExpression": "$response.body#/name",
            "targetStepId": "auditItem",
            "targetInputSlot": "query:itemId",
            "value": "$steps.getItem.outputs.bodyName",
        },
    ]


def test_setup_use_case_evidence_preserves_required_values_and_operation_branches() -> None:
    branches = [
        {"condition": f"action is {action}", "handling_steps": [{"sentence": f"Apply {action}."}]}
        for action in ("create", "update", "publish", "cancel")
    ]
    evidence = _setup_use_case_evidence(
        {"traceHints": {"useCaseIds": ["case-1"]}},
        {"case-1": {
            "use_case_id": "case-1",
            "name": "Manage a resource",
            "public_contract": {"required_values": [{"name": "action"}]},
            "preconditions": ["The resource exists."],
            "trigger": "The user requests an update.",
            "main_scenario": ["Update the resource."],
            "alternative_scenarios": ["Reject an invalid update."],
            "success_guarantee": "The resource is updated.",
            "minimal_guarantee": "The resource remains available.",
            "acceptance_criteria": ["The updated value is returned."],
            "extensions": branches,
        }},
    )

    assert evidence == [{
        "useCaseId": "case-1",
        "name": "Manage a resource",
        "required_values": [{"name": "action"}],
        "preconditions": ["The resource exists."],
        "trigger": "The user requests an update.",
        "main_scenario": ["Update the resource."],
        "alternative_scenarios": ["Reject an invalid update."],
        "success_guarantee": "The resource is updated.",
        "minimal_guarantee": "The resource remains available.",
        "acceptance_criteria": ["The updated value is returned."],
        "extensions": branches,
    }]


def test_execution_candidates_include_read_only_setup_outputs_for_path_inputs() -> None:
    openapi = _openapi()
    item_schema = openapi["paths"]["/items"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]
    openapi["paths"]["/audit/{itemId}"] = openapi["paths"].pop("/audit")
    openapi["paths"]["/audit/{itemId}"]["get"]["parameters"] = [
        {"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}
    ]
    openapi["paths"]["/lookup"] = {
        "get": {
            "operationId": "lookupExistingItem",
            "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": item_schema}}}},
        }
    }
    openapi["paths"]["/seed"] = {
        "post": {
            "operationId": "seedItem",
            "responses": {"201": {"description": "ok", "content": {"application/json": {"schema": item_schema}}}},
        }
    }
    selected = [_candidate_for(build_workflow_candidates(_requirements(), _use_cases(), openapi), "UC-2")]

    audit = next(
        step for step in build_execution_candidates(selected, openapi)
        if step["operationId"] == "auditItem"
    )
    sources = {
        connection["sourceStepId"]
        for connection in audit["inputs"][0]["connections"]
    }

    assert audit["method"] == "GET"
    assert {"createItem", "seedItem", "lookupExistingItem", "getItem"} <= sources


def test_execution_candidates_escape_json_pointer_tokens_and_ground_statuses() -> None:
    openapi = _openapi()
    openapi["paths"]["/items"]["post"]["responses"]["201"]["content"]["application/json"][
        "schema"
    ] = {
        "type": "object",
        "properties": {"a/b": {"type": "string"}, "til~de": {"type": "string"}},
    }
    output = build_execution_candidates([_candidate_for(_candidates(), "UC-1")], openapi)[0]

    assert [(item["outputName"], item["outputExpression"]) for item in output["outputs"]] == [
        ("bodyAB", "$response.body#/a~1b"),
        ("bodyTilDe", "$response.body#/til~0de"),
    ]
    assert output["successStatuses"] == ["201"]


def test_candidates_use_natural_use_case_order() -> None:
    requirements = [
        {"id": f"REQ-{index}", "type": "FR", "text": f"Requirement {index}"}
        for index in range(1, 13)
    ]
    use_cases = {
        "use_case_specs": [
            {
                "use_case_id": f"UC{index}",
                "name": f"Use case {index}",
                "requirement_ids": [f"REQ-{index}"],
                "main_scenario": [f"Run use case {index}"],
            }
            for index in range(12, 0, -1)
        ],
        "traceability": {
            "requirements": {
                f"REQ-{index}": {"use_cases": [f"UC{index}"]}
                for index in range(1, 13)
            }
        },
    }
    openapi = {
        "openapi": "3.0.3",
        "info": {"title": "API", "version": "1.0.0"},
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "x-easydep-use-case-ids": [f"UC{index}" for index in range(1, 13)],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    candidates = build_workflow_candidates(requirements, use_cases, openapi)

    assert [item["workflowId"] for item in candidates] == [
        f"workflow-UC{index}" for index in range(1, 13)
    ]


def _candidate_for(candidates: list[dict[str, Any]], use_case_id: str) -> dict[str, Any]:
    for candidate in candidates:
        if candidate["useCase"]["use_case_id"] == use_case_id:
            return candidate
    raise AssertionError(f"missing candidate for {use_case_id}")


def _operation_ids(candidate: dict[str, Any]) -> set[str]:
    values = candidate.get("operations") or []
    return {
        item["operationId"]
        for item in values
        if isinstance(item, dict) and isinstance(item.get("operationId"), str)
    }


def test_builds_one_candidate_per_functional_use_case() -> None:
    candidates = _candidates()

    assert len(candidates) == 2
    assert {
        _candidate_for(candidates, "UC-1")["useCase"]["use_case_id"],
        _candidate_for(candidates, "UC-2")["useCase"]["use_case_id"],
    } == {"UC-1", "UC-2"}


def test_single_operation_candidate_compiles_to_contract_only_workflow() -> None:
    candidate = _candidate_for(_candidates(), "UC-2")

    workflow = build_deterministic_workflow(candidate)

    assert workflow == {
        "workflowId": "workflow-UC-2",
        "steps": [{"stepId": "auditItem", "operationId": "auditItem"}],
        "x-easydep-trace": candidate["trace"],
    }


def test_multi_operation_candidate_requires_explicit_data_flow() -> None:
    candidate = _candidate_for(_candidates(), "UC-1")

    assert build_deterministic_workflow(candidate) is None


def test_candidate_preserves_requirement_use_case_and_acceptance_context() -> None:
    candidate = _candidate_for(_candidates(), "UC-1")

    assert [item["id"] for item in candidate["requirements"]] == ["REQ-1"]
    context = candidate["useCase"]
    rendered = str(context)
    assert "The inventory service is available." in rendered
    assert "The customer submits an item name." in rendered
    assert "The returned identifier is stable." in rendered
    assert "Create the item." in rendered
    requirement_context = str(candidate["requirements"])
    assert "The item is created with its name." in requirement_context
    assert "The created item can be retrieved by identifier." in requirement_context


def test_exact_operation_link_supports_a_contract_only_workflow() -> None:
    openapi = _openapi()
    openapi["paths"]["/clear"] = {
        "post": {
            "operationId": "clear",
            "x-easydep-use-case-ids": ["UC-CLEAR"],
            "responses": {"204": {"description": "cleared"}},
        }
    }
    candidates = build_workflow_candidates(
        [
            {
                "id": "REQ-CLEAR",
                "type": "NFR",
                "text": "The clear operation should remain available.",
            }
        ],
        {
            "use_case_specs": [
                {
                    "use_case_id": "UC-CLEAR",
                    "name": "Clear the current values",
                    "requirement_ids": ["REQ-CLEAR"],
                    "main_scenario": ["Clear the current values."],
                }
            ]
        },
        openapi,
    )

    candidate = candidates[0]
    assert candidate["requirements"] == []
    assert candidate["trace"]["requirementIds"] == []
    assert candidate["operations"][0]["operationId"] == "clear"
    assert candidate["operations"][0]["traceHints"]["relevant"] is True


def test_unlinked_use_case_without_a_functional_requirement_fails_closed() -> None:
    with pytest.raises(ArazzoPlanningError, match="no OpenAPI operation with an exact"):
        build_workflow_candidates(
            [{"id": "REQ-NFR", "type": "NFR", "text": "A constraint."}],
            {
                "use_case_specs": [
                    {
                        "use_case_id": "UC-UNLINKED",
                        "name": "Unlinked flow",
                        "requirement_ids": ["REQ-NFR"],
                        "main_scenario": ["Perform an unspecified action."],
                    }
                ]
            },
            _openapi(),
        )


def test_each_candidate_contains_only_exactly_traced_operations() -> None:
    candidates = _candidates()

    assert _operation_ids(_candidate_for(candidates, "UC-1")) == {"createItem", "getItem"}
    assert _operation_ids(_candidate_for(candidates, "UC-2")) == {"auditItem"}
    assert all("health" not in _operation_ids(candidate) for candidate in candidates)


def test_trace_and_scenario_links_form_the_operation_allowlist() -> None:
    candidate = _candidate_for(_candidates(), "UC-1")
    before = deepcopy(candidate)

    assert _operation_ids(candidate) == {"createItem", "getItem"}
    assert candidate["operations"][0]["traceHints"]["relevant"] is True
    assert candidate["operations"][0]["traceHints"]["scenarioRefs"]
    assert candidate["requirements"] == before["requirements"]


def test_candidate_exposes_effective_operation_contract_details() -> None:
    candidate = _candidate_for(_candidates(), "UC-1")
    operations = candidate.get("operations") or candidate.get("operation_details")
    assert isinstance(operations, (dict, list))
    rendered = str(operations)
    assert "createItem" in rendered
    assert "/items" in rendered
    assert "X-Request-ID" in rendered
    assert "201" in rendered
    assert "name" in rendered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document["paths"]["/audit"]["get"].update(operationId="createItem"),
        lambda document: document["paths"]["/audit"]["get"].pop("operationId"),
    ],
)
def test_duplicate_or_missing_operation_id_fails_closed(mutate) -> None:
    openapi = _openapi()
    mutate(openapi)

    with pytest.raises(ValueError):
        build_workflow_candidates(_requirements(), _use_cases(), openapi)


def test_workflow_envelope_and_ids_are_stable() -> None:
    first_candidates = _candidates()
    second_candidates = _candidates()
    first = build_arazzo_document(
        [
            attach_workflow_trace({"workflowId": candidate["workflowId"], "steps": []}, candidate)
            for candidate in first_candidates
        ]
    )
    second = build_arazzo_document(
        [
            attach_workflow_trace({"workflowId": candidate["workflowId"], "steps": []}, candidate)
            for candidate in second_candidates
        ]
    )

    assert first == second
    assert first["arazzo"] == "1.1.0"
    assert first["sourceDescriptions"] == [
        {"name": "application", "url": "openapi.json", "type": "openapi"}
    ]
    assert [workflow["workflowId"] for workflow in first["workflows"]] == [
        "workflow-UC-1",
        "workflow-UC-2",
    ]


def test_model_trace_is_overridden_without_changing_standard_workflow_fields() -> None:
    candidate = _candidate_for(_candidates(), "UC-1")
    workflow = {
        "workflowId": "workflow-uc-1",
        "steps": [{"stepId": "create", "operationId": "createItem"}],
        "x-easydep-trace": {"requirementIds": ["MODEL-MADE-UP"], "useCaseIds": ["MODEL-MADE-UP"]},
    }
    standard_before = {
        key: deepcopy(value) for key, value in workflow.items() if not key.startswith("x-")
    }

    traced = attach_workflow_trace(workflow, candidate)

    assert {key: traced[key] for key in standard_before if key != "workflowId"} == {
        key: value for key, value in standard_before.items() if key != "workflowId"
    }
    assert traced["workflowId"] == candidate["workflowId"]
    assert traced["x-easydep-trace"] == {
        "requirementIds": ["REQ-1"],
        "useCaseIds": ["UC-1"],
        "evidenceRefs": ["requirement:REQ-1", "use_case:UC-1"],
    }
