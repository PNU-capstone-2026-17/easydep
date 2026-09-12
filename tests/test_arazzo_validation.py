"""Black-box checks for the supported EasyDep Arazzo validation boundary."""

from __future__ import annotations

from copy import deepcopy

import pytest

import app.testing.schemas.arazzo as arazzo_schema
from app.testing.schemas.arazzo import ArazzoValidationError, validate_arazzo_document


def _openapi() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Inventory API", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "operationId": "createItem",
                    "responses": {"201": {"description": "created"}},
                },
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"204": {"description": "deleted"}},
                },
            },
        },
    }


def _document() -> dict:
    return {
        "arazzo": "1.1.0",
        "info": {"title": "Inventory smoke tests", "version": "1.0.0"},
        "sourceDescriptions": [{"name": "application", "url": "openapi.json", "type": "openapi"}],
        "workflows": [
            {
                "workflowId": "smoke",
                "steps": [{"stepId": "list", "operationId": "listItems"}],
            }
        ],
    }


def _validate(document: dict) -> None:
    """Call the public validator without coupling tests to its return value."""

    validate_arazzo_document(document, openapi=_openapi())


def test_valid_minimal_arazzo_11_document() -> None:
    _validate(_document())


def test_same_operation_id_can_be_used_by_distinct_steps() -> None:
    document = _document()
    document["workflows"][0]["steps"] = [
        {"stepId": "first-list", "operationId": "listItems"},
        {"stepId": "second-list", "operationId": "listItems", "dependsOn": ["first-list"]},
    ]

    _validate(document)


def test_local_goto_and_cleanup_workflow_are_valid() -> None:
    document = _document()
    document["workflows"] = [
        {
            "workflowId": "smoke",
            "steps": [
                {
                    "stepId": "create",
                    "operationId": "createItem",
                    "outputs": {"itemId": "$response.body#/id"},
                    "onSuccess": [{"name": "continue", "type": "goto", "stepId": "read"}],
                },
                {
                    "stepId": "read",
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "value": "$steps.create.outputs.itemId"}
                    ],
                    "onFailure": [{"name": "cleanup", "type": "goto", "workflowId": "cleanup"}],
                },
            ],
        },
        {
            "workflowId": "cleanup",
            "steps": [{"stepId": "delete", "operationId": "deleteItem"}],
        },
    ]

    _validate(document)


def test_prior_step_outputs_and_criteria_are_resolvable() -> None:
    document = _document()
    document["workflows"][0]["steps"] = [
        {
            "stepId": "create",
            "operationId": "createItem",
            "outputs": {"itemId": "$response.body#/id"},
            "successCriteria": [{"condition": "$statusCode == 201"}],
        },
        {
            "stepId": "read",
            "operationId": "getItem",
            "dependsOn": ["create"],
            "parameters": [{"name": "id", "in": "path", "value": "$steps.create.outputs.itemId"}],
            "successCriteria": [
                {"condition": "$statusCode == 200"},
                {"condition": "$response.body#/id == $steps.create.outputs.itemId"},
            ],
        },
    ]

    _validate(document)


def test_double_brace_template_placeholder_is_rejected_before_execution() -> None:
    document = _document()
    document["workflows"][0]["steps"][0] = {
        "stepId": "read",
        "operationId": "getItem",
        "parameters": [{"name": "id", "in": "path", "value": "{{id}}"}],
    }

    with pytest.raises(ArazzoValidationError, match="not Arazzo Runtime Expressions"):
        _validate(document)


def test_step_output_must_be_a_complete_arazzo_runtime_expression() -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["outputs"] = {
        "result": "$.response.body"
    }

    with pytest.raises(ArazzoValidationError, match="one complete Arazzo Runtime Expression"):
        _validate(document)


def test_literal_parameter_must_satisfy_the_openapi_schema() -> None:
    document = _document()
    document["workflows"][0]["steps"][0] = {
        "stepId": "read",
        "operationId": "getItem",
        "parameters": [{"name": "id", "in": "path", "value": 42}],
    }

    with pytest.raises(ArazzoValidationError, match="frozen OpenAPI schema"):
        _validate(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "https://example.com/openapi.yaml"),
    ],
)
def test_external_source_is_rejected(field: str, value: str) -> None:
    document = _document()
    document["sourceDescriptions"][0][field] = value

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_asyncapi_source_is_rejected() -> None:
    document = _document()
    document["sourceDescriptions"][0] = {
        "name": "events",
        "url": "./asyncapi.yaml",
        "type": "asyncapi",
    }
    document["workflows"][0]["steps"] = [
        {
            "stepId": "publish",
            "channelPath": "$sourceDescriptions.events#/channels/items",
            "action": "send",
        }
    ]

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_operation_path_is_rejected() -> None:
    document = _document()
    document["workflows"][0]["steps"] = [
        {
            "stepId": "list",
            "operationPath": "$sourceDescriptions.inventory#/paths/~1items/get",
        }
    ]

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_unknown_operation_id_is_rejected() -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["operationId"] = "doesNotExist"

    with pytest.raises(ArazzoValidationError):
        _validate(document)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document["workflows"][0].__setitem__("dependsOn", ["missing-workflow"]),
        lambda document: document["workflows"][0]["steps"][0].__setitem__(
            "onSuccess", [{"name": "jump", "type": "goto", "workflowId": "missing-workflow"}]
        ),
        lambda document: document["workflows"][0]["steps"][0].__setitem__(
            "dependsOn", ["missing-step"]
        ),
        lambda document: document["workflows"][0]["steps"][0].__setitem__(
            "onSuccess", [{"name": "jump", "type": "goto", "stepId": "missing-step"}]
        ),
    ],
)
def test_broken_workflow_and_step_references_are_rejected(mutate) -> None:
    document = _document()
    mutate(document)

    with pytest.raises(ArazzoValidationError):
        _validate(document)


@pytest.mark.parametrize(
    "retry_action",
    [
        {"name": "retry-too-many", "type": "retry", "retryLimit": 4},
    ],
)
def test_retry_limit_must_be_bounded_to_three_attempts(retry_action: dict) -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["onFailure"] = [retry_action]

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_retry_without_limit_uses_the_spec_default() -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["onFailure"] = [{"name": "retry-once", "type": "retry"}]

    _validate(document)


def test_local_goto_cycle_is_rejected() -> None:
    document = _document()
    document["workflows"][0]["steps"] = [
        {
            "stepId": "one",
            "operationId": "listItems",
            "onSuccess": [{"name": "to-two", "type": "goto", "stepId": "two"}],
        },
        {
            "stepId": "two",
            "operationId": "listItems",
            "onSuccess": [{"name": "to-one", "type": "goto", "stepId": "one"}],
        },
    ]

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_malformed_easydep_trace_extension_is_rejected() -> None:
    document = deepcopy(_document())
    document["x-easydep-trace"] = "not-a-trace-object"

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_local_workflow_call_step_accepts_parameters() -> None:
    document = _document()
    document["workflows"] = [
        {
            "workflowId": "parent",
            "steps": [
                {
                    "stepId": "call-child",
                    "workflowId": "child",
                    "parameters": [
                        {"name": "item-id", "value": "literal-item"},
                    ],
                }
            ],
        },
        {
            "workflowId": "child",
            "inputs": {
                "type": "object",
                "properties": {"item-id": {"type": "string"}},
                "required": ["item-id"],
            },
            "steps": [{"stepId": "list", "operationId": "listItems"}],
        },
    ]

    _validate(document)


def test_workflow_call_to_unknown_workflow_is_rejected() -> None:
    document = _document()
    document["workflows"][0]["steps"] = [
        {
            "stepId": "call-missing",
            "workflowId": "missing-workflow",
            "parameters": [{"name": "item-id", "value": "literal-item"}],
        }
    ]

    with pytest.raises(ArazzoValidationError):
        _validate(document)


@pytest.mark.parametrize(
    "expression",
    [
        "$steps.missing.outputs.itemId",  # unresolved local step
        "$sourceDescriptions.remote#/outputs/itemId",  # external source reference
        "$unsupported.value",  # unsupported runtime-expression namespace
    ],
)
def test_malformed_or_unsupported_runtime_expression_is_rejected(expression: str) -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["parameters"] = [{"name": "item-id", "value": expression}]

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_workflow_trace_extension_is_accepted_on_workflow_object() -> None:
    document = _document()
    document["workflows"][0]["x-easydep-trace"] = {
        "requirementIds": ["REQ-1"],
        "useCaseIds": ["UC-1"],
        "evidenceRefs": ["evidence-1"],
    }

    _validate(document)


@pytest.mark.parametrize(
    "location",
    ["top-level", "step"],
)
def test_workflow_trace_extension_is_rejected_outside_workflow(location: str) -> None:
    document = _document()
    trace = {"requirementIds": ["REQ-1"]}
    if location == "top-level":
        document["x-easydep-trace"] = trace
    else:
        document["workflows"][0]["steps"][0]["x-easydep-trace"] = trace

    with pytest.raises(ArazzoValidationError):
        _validate(document)


@pytest.mark.parametrize(
    "trace",
    [
        {"requirementIds": []},
        {"requirementIds": [""]},
        {"unknownIds": ["REQ-1"]},
    ],
)
def test_empty_or_unknown_trace_ids_are_rejected(trace: dict) -> None:
    document = _document()
    document["workflows"][0]["x-easydep-trace"] = trace

    with pytest.raises(ArazzoValidationError):
        _validate(document)


def test_trace_ids_are_checked_when_the_frozen_catalog_is_supplied() -> None:
    document = _document()
    document["workflows"][0]["x-easydep-trace"] = {
        "requirementIds": ["REQ-missing"],
    }

    with pytest.raises(ArazzoValidationError, match="unknown IDs"):
        validate_arazzo_document(
            document,
            openapi=_openapi(),
            trace_catalog={"requirementIds": {"REQ-1"}},
        )


def test_vendored_schema_is_required_and_has_no_weak_fallback(monkeypatch) -> None:
    monkeypatch.setattr(arazzo_schema, "_SCHEMA_FILE", "missing-schema.json")

    with pytest.raises(ArazzoValidationError, match="schema is unavailable"):
        _validate(_document())


@pytest.mark.parametrize("criterion_type", ["regex", "xpath"])
def test_unsupported_criterion_types_are_rejected(criterion_type: str) -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["successCriteria"] = [
        {
            "context": "$response.body",
            "condition": ".+",
            "type": criterion_type,
        }
    ]

    with pytest.raises(ArazzoValidationError, match="criterion profile"):
        _validate(document)


def test_recursive_local_workflow_call_is_rejected() -> None:
    document = _document()
    document["workflows"][0]["steps"] = [{"stepId": "recursive", "workflowId": "smoke"}]

    with pytest.raises(ArazzoValidationError, match="recursion cycle"):
        _validate(document)


@pytest.mark.parametrize(
    "parameter",
    [
        {"name": "q", "value": "books"},
        {"name": "missing", "in": "query", "value": "books"},
        {"name": "session", "in": "cookie", "value": "token"},
    ],
)
def test_openapi_step_parameters_are_explicit_supported_and_declared(parameter: dict) -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["parameters"] = [parameter]
    openapi = _openapi()
    openapi["paths"]["/items"]["get"]["parameters"] = [
        {"name": "q", "in": "query", "required": False, "schema": {"type": "string"}},
        {"name": "session", "in": "cookie", "required": False, "schema": {"type": "string"}},
    ]

    with pytest.raises(ArazzoValidationError):
        validate_arazzo_document(document, openapi=openapi)


@pytest.mark.parametrize("location", ["cookie", "querystring"])
def test_required_unsupported_openapi_parameter_is_rejected(location: str) -> None:
    document = _document()
    openapi = _openapi()
    openapi["paths"]["/items"]["get"]["parameters"] = [
        {"name": "session", "in": location, "required": True, "schema": {"type": "string"}}
    ]

    with pytest.raises(ArazzoValidationError, match="unsupported required"):
        validate_arazzo_document(document, openapi=openapi)


@pytest.mark.parametrize(
    "request_body",
    [
        {"contentType": "text/plain", "payload": "not-json"},
        {
            "contentType": "application/json",
            "payload": {"name": "book"},
            "replacements": [
                {"target": "$.name", "targetSelectorType": "jsonpath", "value": "new"}
            ],
        },
    ],
)
def test_request_bodies_are_json_and_replacements_are_json_pointers(request_body: dict) -> None:
    document = _document()
    document["workflows"][0]["steps"][0] = {
        "stepId": "create",
        "operationId": "createItem",
        "requestBody": request_body,
    }
    openapi = _openapi()
    openapi["paths"]["/items"]["post"]["requestBody"] = {
        "content": {"application/json": {"schema": {"type": "object"}}}
    }

    with pytest.raises(ArazzoValidationError):
        validate_arazzo_document(document, openapi=openapi)


def test_non_json_openapi_request_body_is_rejected_before_execution() -> None:
    document = _document()
    document["workflows"][0]["steps"][0] = {
        "stepId": "create",
        "operationId": "createItem",
    }
    openapi = _openapi()
    openapi["paths"]["/items"]["post"]["requestBody"] = {
        "content": {"text/plain": {"schema": {"type": "string"}}}
    }

    with pytest.raises(ArazzoValidationError, match="non-JSON"):
        validate_arazzo_document(document, openapi=openapi)


def test_literal_request_body_is_validated_against_openapi_before_execution() -> None:
    document = _document()
    document["workflows"][0]["steps"][0] = {
        "stepId": "create",
        "operationId": "createItem",
        "requestBody": {
            "contentType": "application/json",
            "payload": {"termId": "2025-FALL"},
        },
    }
    openapi = _openapi()
    openapi["paths"]["/items"]["post"]["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                    "additionalProperties": False,
                }
            }
        },
    }

    with pytest.raises(ArazzoValidationError, match="requestBody.payload.*'id' is a required"):
        validate_arazzo_document(document, openapi=openapi)


def test_request_body_with_replacements_is_validated_after_resolution() -> None:
    document = _document()
    document["workflows"][0]["steps"][0] = {
        "stepId": "create",
        "operationId": "createItem",
        "requestBody": {
            "contentType": "application/json",
            "payload": {},
            "replacements": [
                {"target": "/id", "value": "$steps.seed.outputs.id"}
            ],
        },
    }
    openapi = _openapi()
    openapi["paths"]["/items"]["post"]["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"type": "object", "required": ["id"]}
            }
        },
    }

    # The ordinary runtime-expression validation remains responsible for the
    # reference; the preflight schema check must not reject the empty template.
    with pytest.raises(ArazzoValidationError, match="unknown local step"):
        validate_arazzo_document(document, openapi=openapi)


@pytest.mark.parametrize(
    "retry",
    [
        {"name": "retry", "type": "retry", "stepId": "list"},
        {"name": "retry", "type": "retry", "workflowId": "smoke"},
        {"name": "retry", "type": "retry", "retryAfter": 1},
    ],
)
def test_retry_cannot_target_or_delay_another_execution(retry: dict) -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["onFailure"] = [retry]

    with pytest.raises(ArazzoValidationError, match="only retries its current step"):
        _validate(document)


def test_workflow_level_parameters_are_rejected_in_the_initial_profile() -> None:
    document = _document()
    document["workflows"][0]["parameters"] = [{"name": "q", "in": "query", "value": "books"}]

    with pytest.raises(ArazzoValidationError, match="declare parameters on each operation step"):
        _validate(document)


def test_step_timeout_is_rejected_instead_of_being_silently_ignored() -> None:
    document = _document()
    document["workflows"][0]["steps"][0]["timeout"] = 500

    with pytest.raises(ArazzoValidationError, match="configured request timeout"):
        _validate(document)


def test_local_workflow_call_control_fields_are_rejected() -> None:
    document = _document()
    document["workflows"].append(
        {
            "workflowId": "child",
            "steps": [{"stepId": "child-list", "operationId": "listItems"}],
        }
    )
    document["workflows"][0]["steps"][0] = {
        "stepId": "call-child",
        "workflowId": "child",
        "onFailure": [{"name": "end", "type": "end"}],
    }

    with pytest.raises(ArazzoValidationError, match="unsupported control fields"):
        _validate(document)
