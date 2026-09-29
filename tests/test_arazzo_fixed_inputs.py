"""Fixed Arazzo plans must supply their runtime inputs explicitly."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.testing.utils.arazzo_executor import execute_arazzo_workflow

TARGET_URL = "http://127.0.0.1:8765"


class _Recorder:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def _response(status: int, payload: Any) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", TARGET_URL),
    )


def _openapi() -> dict[str, Any]:
    item = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Fixed input test", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "parameters": [
                        {
                            "name": "source",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "default": "schema-source"},
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
                },
                "get": {
                    "operationId": "searchItems",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "example": "schema-query"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": item}
                                }
                            },
                        }
                    },
                },
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": item}},
                        }
                    },
                }
            },
        },
    }


def _document(*steps: dict[str, Any]) -> dict[str, Any]:
    return {
        "arazzo": "1.1.0",
        "info": {"title": "Fixed input test", "version": "1.0.0"},
        "sourceDescriptions": [
            {"name": "application", "url": "openapi.json", "type": "openapi"}
        ],
        "workflows": [{"workflowId": "main", "steps": list(steps)}],
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    document: dict[str, Any],
    recorder: _Recorder,
    **kwargs: Any,
) -> dict[str, Any]:
    monkeypatch.setattr(httpx, "request", recorder)
    return execute_arazzo_workflow(
        document,
        "main",
        openapi=_openapi(),
        target_url=TARGET_URL,
        **kwargs,
    )


def test_strict_mode_accepts_literal_parameter_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder([_response(201, {"id": "item-1", "name": "Mug"})])
    result = _run(
        monkeypatch,
        _document(
            {
                "stepId": "create",
                "operationId": "createItem",
                "parameters": [{"name": "source", "in": "query", "value": "fixture"}],
                "requestBody": {"payload": {"name": "Mug"}},
            }
        ),
        recorder,
        require_explicit_values=True,
        propose_input=None,
    )

    assert result["gateStatus"] == "PASS"
    assert parse_qs(urlsplit(recorder.calls[0]["url"]).query) == {"source": ["fixture"]}
    assert recorder.calls[0]["json"] == {"name": "Mug"}


def test_strict_mode_resolves_response_expression_as_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(
        [
            _response(200, [{"id": "returned-id", "name": "Mug"}]),
            _response(200, {"id": "returned-id", "name": "Mug"}),
        ]
    )
    result = _run(
        monkeypatch,
        _document(
            {
                "stepId": "search",
                "operationId": "searchItems",
                "parameters": [{"name": "q", "in": "query", "value": "Mug"}],
                "outputs": {"itemId": "$response.body#/0/id"},
            },
            {
                "stepId": "read",
                "operationId": "getItem",
                "dependsOn": ["search"],
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "value": "$steps.search.outputs.itemId",
                    }
                ],
            },
        ),
        recorder,
        require_explicit_values=True,
        propose_input=None,
    )

    assert result["gateStatus"] == "PASS", result.get("finding")
    assert recorder.calls[1]["url"] == f"{TARGET_URL}/items/returned-id"


def test_strict_mode_rejects_omitted_required_schema_values_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder([])
    result = _run(
        monkeypatch,
        _document({"stepId": "create", "operationId": "createItem"}),
        recorder,
        require_explicit_values=True,
        propose_input=None,
    )

    assert result["gateStatus"] == "FAIL"
    assert result["defectClass"] == "TEST_DEFECT"
    assert result["finding"]["code"] == "INPUT_VALUE_UNAVAILABLE"
    assert recorder.calls == []


def test_default_mode_keeps_schema_default_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder([_response(201, {"id": "item-1", "name": "Mug"})])
    result = _run(
        monkeypatch,
        _document(
            {
                "stepId": "create",
                "operationId": "createItem",
                "requestBody": {"payload": {"name": "Mug"}},
            }
        ),
        recorder,
    )

    assert result["gateStatus"] == "PASS"
    assert parse_qs(urlsplit(recorder.calls[0]["url"]).query) == {
        "source": ["schema-source"]
    }
