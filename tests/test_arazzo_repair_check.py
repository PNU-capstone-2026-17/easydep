"""Black-box checks for rerunning one failed Arazzo repair workflow."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from app.testing import repair_check
from app.testing.utils.functional_executor import InputValueRequest

TARGET_URL = "http://127.0.0.1:8765"


def _openapi() -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Inventory API", "version": "1.0.0"},
        "paths": {
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def _document() -> dict[str, Any]:
    return {
        "arazzo": "1.1.0",
        "info": {"title": "Inventory workflows", "version": "1.0.0"},
        "sourceDescriptions": [{"name": "application", "url": "openapi.json", "type": "openapi"}],
        "workflows": [
            {
                "workflowId": "workflow-UC-1",
                "steps": [
                    {
                        "stepId": "get-first",
                        "operationId": "getItem",
                        "parameters": [{"name": "id", "in": "path", "value": "item-1"}],
                    }
                ],
            },
            {
                "workflowId": "workflow-UC-2",
                "inputs": {
                    "type": "object",
                    "required": ["itemId"],
                    "properties": {"itemId": {"type": "string"}},
                },
                "steps": [
                    {
                        "stepId": "get-second",
                        "operationId": "getItem",
                        "parameters": [{"name": "id", "in": "path", "value": "$inputs.itemId"}],
                        "successCriteria": [{"condition": "$response.body#/id == $inputs.itemId"}],
                    }
                ],
            },
        ],
    }


def _profile(**extra: Any) -> dict[str, Any]:
    return {
        "app_id": "inventory",
        "openapi": _openapi(),
        "candidate_plan": _document(),
        "failed_workflow_id": "workflow-UC-2",
        "workflow_inputs": {"workflow-UC-2": {"itemId": "item-42"}},
        "input_values": {
            "workflow-UC-2": [
                {
                    "operationId": "getItem",
                    "location": "path.id",
                    "value": "item-42",
                }
            ]
        },
        **extra,
    }


@contextmanager
def _fake_application(*_args: Any, **_kwargs: Any):
    yield TARGET_URL, object()


def test_repair_reruns_exact_failed_workflow_and_preserves_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def execute(_document: dict[str, Any], workflow_id: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"workflow_id": workflow_id, **kwargs})
        return {
            "workflowId": workflow_id,
            "status": "passed",
            "gateStatus": "PASS",
            "defectClass": None,
            "steps": [],
            "workflowInputs": kwargs["workflow_inputs"],
            "inputValues": [{"operationId": "getItem", "location": "path.id", "value": "item-42"}],
            "contractStatus": "PASS",
            "semanticStatus": "UNVERIFIED",
        }

    monkeypatch.setattr(repair_check, "running_application", _fake_application)
    monkeypatch.setattr(repair_check, "execute_arazzo_workflow", execute, raising=False)

    result = repair_check.verify_testing_repair_gate(tmp_path, "testing-dynamic", _profile())

    assert result["gateStatus"] == "PASS"
    assert [call["workflow_id"] for call in calls] == ["workflow-UC-2"]
    assert calls[0]["workflow_inputs"] == {"itemId": "item-42"}


def test_concrete_input_values_are_reused_without_a_new_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proposed: list[InputValueRequest] = []

    def execute(
        _document: dict[str, Any], _workflow_id: str, *, propose_input, **_kwargs: Any
    ) -> dict[str, Any]:
        value = propose_input(
            InputValueRequest(operation_id="getItem", location="path.id", schema={"type": "string"})
        )
        assert value == "item-42"
        return {
            "workflowId": "workflow-UC-2",
            "status": "passed",
            "gateStatus": "PASS",
            "steps": [],
            "workflowInputs": {"itemId": "item-42"},
            "contractStatus": "PASS",
            "semanticStatus": "UNVERIFIED",
        }

    monkeypatch.setattr(repair_check, "running_application", _fake_application)
    monkeypatch.setattr(repair_check, "execute_arazzo_workflow", execute, raising=False)
    monkeypatch.setattr(
        repair_check,
        "InputValueRequest",
        InputValueRequest,
        raising=False,
    )

    profile = _profile()
    profile["propose_input"] = lambda request: proposed.append(request) or "new-value"
    result = repair_check.verify_testing_repair_gate(tmp_path, "dynamic", profile)

    assert result["gateStatus"] == "PASS"
    assert proposed == []


def test_invalid_legacy_candidate_fails_closed_without_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launched = False

    def launch(*_args: Any, **_kwargs: Any):
        nonlocal launched
        launched = True
        return _fake_application()

    monkeypatch.setattr(repair_check, "running_application", launch)
    monkeypatch.setattr(
        repair_check,
        "execute_arazzo_workflow",
        lambda *_args, **_kwargs: pytest.fail("invalid candidate must not execute"),
        raising=False,
    )

    result = repair_check.verify_testing_repair_gate(
        tmp_path,
        "testing-dynamic",
        _profile(candidate_plan={"cases": []}),
    )

    assert launched is False
    assert result["gateStatus"] == "FAIL"
    assert result.get("gateEvidence", {}).get("defectClass", "TEST_DEFECT") == "TEST_DEFECT"


def test_failed_step_and_criterion_evidence_are_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = {
        "workflowId": "workflow-UC-2",
        "status": "failed",
        "gateStatus": "FAIL",
        "defectClass": "SUT_DEFECT",
        "reason": "The success criterion failed.",
        "workflowInputs": {"itemId": "item-42"},
        "steps": [
            {
                "stepId": "get-second",
                "operationId": "getItem",
                "request": {"method": "GET", "path": "/items/item-42"},
                "responseBody": {"id": "wrong-item"},
                "semanticStatus": "FAIL",
                "finding": {
                    "code": "SUCCESS_CRITERIA_FAILED",
                    "criterion": {"condition": "$response.body#/id == $inputs.itemId"},
                },
            }
        ],
        "finding": {"code": "SUCCESS_CRITERIA_FAILED", "stepId": "get-second"},
        "contractStatus": "PASS",
        "semanticStatus": "FAIL",
    }

    monkeypatch.setattr(repair_check, "running_application", _fake_application)
    monkeypatch.setattr(
        repair_check, "execute_arazzo_workflow", lambda *_args, **_kwargs: failed, raising=False
    )

    result = repair_check.verify_testing_repair_gate(tmp_path, "dynamic", _profile())

    assert result["gateStatus"] == "FAIL"
    evidence = result["gateEvidence"]
    assert evidence["finding"]["code"] == "SUCCESS_CRITERIA_FAILED"
    step = evidence["steps"][0]
    assert step["stepId"] == "get-second"
    assert step["finding"]["criterion"]["condition"].endswith("$inputs.itemId")
    assert step["request"]["path"] == "/items/item-42"


def test_nested_workflow_repair_reuses_all_inputs_and_retains_http_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _document()
    document["workflows"] = [
        {
            "workflowId": "workflow-UC-2",
            "steps": [{"stepId": "call-child", "workflowId": "child"}],
        },
        {
            "workflowId": "child",
            "steps": [{"stepId": "get-child", "operationId": "getItem"}],
        },
    ]
    profile = _profile(
        candidate_plan=document,
        workflow_inputs={
            "workflow-UC-2": {"mode": "parent"},
            "child": {"mode": "child"},
        },
        input_values={
            "child": [
                {
                    "operationId": "getItem",
                    "location": "path.id",
                    "value": "child-item",
                }
            ]
        },
    )

    def execute(
        _document: dict[str, Any], workflow_id: str, *, propose_input, **kwargs: Any
    ) -> dict[str, Any]:
        assert workflow_id == "workflow-UC-2"
        assert kwargs["workflow_inputs_by_id"] == profile["workflow_inputs"]
        assert (
            propose_input(
                InputValueRequest(
                    operation_id="getItem",
                    location="path.id",
                    schema={"type": "string"},
                    operation_context="child",
                )
            )
            == "child-item"
        )
        return {
            "workflowId": workflow_id,
            "failedWorkflowId": "child",
            "failedStepId": "get-child",
            "status": "failed",
            "gateStatus": "FAIL",
            "defectClass": "SUT_DEFECT",
            "reason": "Child request failed.",
            "steps": [
                {
                    "workflowId": "child",
                    "stepId": "get-child",
                    "operationId": "getItem",
                    "method": "get",
                    "path": "/items/{id}",
                    "request": {"method": "GET", "path": "/items/child-item"},
                    "statusCode": 500,
                    "responseBody": {"error": "failed"},
                    "semanticStatus": "FAIL",
                    "finding": {"code": "HTTP_STATUS_NOT_SUCCESS"},
                },
                {
                    "workflowId": "workflow-UC-2",
                    "calledWorkflowId": "child",
                    "stepId": "call-child",
                    "status": "failed",
                    "control": "workflow-call",
                },
            ],
            "finding": {
                "code": "HTTP_STATUS_NOT_SUCCESS",
                "workflowId": "child",
                "stepId": "get-child",
            },
            "contractStatus": "PASS",
            "semanticStatus": "FAIL",
        }

    monkeypatch.setattr(repair_check, "running_application", _fake_application)
    monkeypatch.setattr(repair_check, "execute_arazzo_workflow", execute, raising=False)

    result = repair_check.verify_testing_repair_gate(tmp_path, "dynamic", profile)

    evidence = result["gateEvidence"]["repairEvidence"]
    assert evidence["rerunWorkflowId"] == "workflow-UC-2"
    assert evidence["failedWorkflowId"] == "child"
    assert evidence["failedStepId"] == "get-child"
    assert evidence["steps"][0]["request"]["path"] == "/items/child-item"
    assert result["gateEvidence"]["commands"][0]["name"].startswith("child: HTTP")
