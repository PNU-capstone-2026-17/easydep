"""Black-box tests for deterministic Arazzo workflow planning."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.testing.utils.arazzo_planner import (
    ArazzoPlanningError,
    attach_workflow_trace,
    build_arazzo_document,
    build_workflow_candidates,
)


def _openapi() -> dict[str, Any]:
    item = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Inventory API", "version": "1.0.0"},
        "servers": [{"url": "https://untrusted.example.invalid"}],
        "paths": {
            "/items": {
                "post": {
                    "operationId": "createItem",
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
                            "description": "created",
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
    with pytest.raises(ArazzoPlanningError, match="exact OpenAPI operation link"):
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


def test_all_unique_frozen_operations_remain_available_without_rtm_links() -> None:
    candidates = _candidates()
    available = set().union(*(_operation_ids(candidate) for candidate in candidates))

    assert {"createItem", "getItem", "auditItem", "health"} <= available


def test_trace_and_scenario_links_only_rank_or_annotate_operations() -> None:
    candidate = _candidate_for(_candidates(), "UC-1")
    before = deepcopy(candidate)

    assert _operation_ids(candidate) >= {"createItem", "getItem"}
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
