"""Black-box tests for the deterministic EasyDep Arazzo executor."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.testing.progress import testing_progress_scope as _testing_progress_scope
from app.testing.utils.arazzo_executor import execute_arazzo_workflow
from app.testing.utils.functional_executor import InputValueRequest

TARGET_URL = "http://127.0.0.1:8765"


def _response(status: int, body: Any = None) -> httpx.Response:
    request = httpx.Request("GET", TARGET_URL)
    if body is None:
        return httpx.Response(status, request=request)
    return httpx.Response(status, json=body, request=request)


class _HttpRecorder:
    def __init__(
        self, responses: Iterable[httpx.Response | Exception | Callable[..., Any]]
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        parsed = urlsplit(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == TARGET_URL
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(method, url, **kwargs)
        if not isinstance(item, httpx.Response):
            raise TypeError("fake HTTP response must be an httpx.Response")
        return item


def _openapi() -> dict[str, Any]:
    item = {
        "type": "object",
        "required": ["id", "name"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "ok": {"type": "boolean"},
            "value": {"type": "integer"},
        },
    }
    health = {
        "type": "object",
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }
    request_item = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
    }
    expected_error = {
        "type": "object",
        "required": ["code"],
        "properties": {"code": {"type": "string"}},
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Inventory", "version": "1.0.0"},
        # This must never be used by the executor: target_url is authoritative.
        "servers": [{"url": "https://untrusted.example.invalid"}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": health}},
                        }
                    },
                }
            },
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": request_item}},
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
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "X-Token",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {"schema": {"type": "array", "items": item}}
                            },
                        }
                    },
                },
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": item}},
                        }
                    },
                },
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"204": {"description": "deleted"}},
                },
            },
            "/echo": {
                "post": {
                    "operationId": "echoItem",
                    "requestBody": {"content": {"application/json": {"schema": request_item}}},
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": request_item}},
                        }
                    },
                }
            },
            "/expected-error": {
                "get": {
                    "operationId": "expectedError",
                    "responses": {
                        "400": {
                            "description": "expected",
                            "content": {"application/json": {"schema": expected_error}},
                        }
                    },
                }
            },
            "/unstable": {
                "get": {
                    "operationId": "unstable",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": item}},
                        },
                        "500": {"description": "error"},
                    },
                }
            },
        },
    }


def _document(workflows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "arazzo": "1.1.0",
        "info": {"title": "Inventory workflow", "version": "1.0.0"},
        "sourceDescriptions": [{"name": "application", "url": "openapi.json", "type": "openapi"}],
        "workflows": workflows,
    }


def _workflow(workflow_id: str, *steps: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"workflowId": workflow_id, "steps": list(steps), **extra}


def _run(
    monkeypatch: pytest.MonkeyPatch,
    document: dict[str, Any],
    recorder: _HttpRecorder,
    workflow_id: str = "main",
    **kwargs: Any,
) -> dict[str, Any]:
    monkeypatch.setattr(httpx, "request", recorder)
    return execute_arazzo_workflow(
        document,
        workflow_id,
        openapi=_openapi(),
        target_url=TARGET_URL,
        **kwargs,
    )


def _assert_result(result: dict[str, Any], *, gate: str, defect: str | None = None) -> None:
    assert result["gateStatus"] == gate
    if defect is not None:
        assert result["defectClass"] == defect


def test_single_request_uses_target_url_and_ignores_openapi_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True})])
    result = _run(
        monkeypatch,
        _document([_workflow("main", {"stepId": "health", "operationId": "health"})]),
        recorder,
    )

    _assert_result(result, gate="PASS")
    assert [call["method"] for call in recorder.calls] == ["GET"]
    assert [call["url"] for call in recorder.calls] == [f"{TARGET_URL}/health"]


def test_executor_emits_workflow_and_step_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True})])
    events: list[dict[str, Any]] = []

    with _testing_progress_scope(events.append):
        result = _run(
            monkeypatch,
            _document([_workflow("main", {"stepId": "health", "operationId": "health"})]),
            recorder,
        )

    _assert_result(result, gate="PASS")
    assert [(event["scope"], event["status"]) for event in events] == [
        ("workflow", "RUNNING"),
        ("step", "RUNNING"),
        ("step", "PASS"),
        ("workflow", "PASS"),
    ]
    assert events[1]["operation_id"] == "health"
    assert events[2]["status_code"] == 200
    assert events[2]["contract_status"] == "PASS"


def test_post_output_is_reused_by_repeated_get_path_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [
            _response(201, {"id": "item-7", "name": "book"}),
            _response(200, {"id": "item-7", "name": "book"}),
        ]
    )
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "create",
                    "operationId": "createItem",
                    "requestBody": {"contentType": "application/json", "payload": {"name": "book"}},
                    "outputs": {"itemId": "$response.body#/id"},
                },
                {
                    "stepId": "read-one",
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "value": "$steps.create.outputs.itemId"}
                    ],
                },
                {
                    "stepId": "read-two",
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "value": "$steps.create.outputs.itemId"}
                    ],
                },
            )
        ]
    )
    recorder.responses.insert(1, _response(200, {"id": "item-7", "name": "book"}))
    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="PASS")
    assert [call["url"] for call in recorder.calls] == [
        f"{TARGET_URL}/items",
        f"{TARGET_URL}/items/item-7",
        f"{TARGET_URL}/items/item-7",
    ]


def test_explicit_query_header_and_body_replacement_are_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"name": "new", "role": "admin"}), _response(200, [])])
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "echo",
                    "operationId": "echoItem",
                    "requestBody": {
                        "contentType": "application/json",
                        "payload": {"name": "old", "role": "user"},
                        "replacements": [
                            {"target": "/name", "value": "new"},
                            {"target": "/role", "value": "admin"},
                        ],
                    },
                },
                {
                    "stepId": "search",
                    "operationId": "searchItems",
                    "parameters": [
                        {"name": "q", "in": "query", "value": "books"},
                        {"name": "X-Token", "in": "header", "value": "test-token"},
                    ],
                },
            )
        ]
    )
    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="PASS")
    assert recorder.calls[0]["json"] == {"name": "new", "role": "admin"}
    assert parse_qs(urlsplit(recorder.calls[1]["url"]).query) == {"q": ["books"]}
    assert recorder.calls[1]["headers"]["X-Token"] == "test-token"


def test_expected_4xx_can_be_success_when_criterion_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(400, {"code": "EXPECTED"})])
    step = {
        "stepId": "expected",
        "operationId": "expectedError",
        "successCriteria": [{"condition": "$statusCode == 400"}],
    }
    result = _run(monkeypatch, _document([_workflow("main", step)]), recorder)

    _assert_result(result, gate="PASS")


def test_prior_output_comparison_simple_criterion_is_evaluated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [
            _response(201, {"id": "item-8", "name": "book"}),
            _response(200, {"id": "item-8", "name": "book"}),
        ]
    )
    result = _run(
        monkeypatch,
        _document(
            [
                _workflow(
                    "main",
                    {
                        "stepId": "create",
                        "operationId": "createItem",
                        "requestBody": {"payload": {"name": "book"}},
                        "outputs": {"itemId": "$response.body#/id"},
                    },
                    {
                        "stepId": "read",
                        "operationId": "getItem",
                        "parameters": [
                            {"name": "id", "in": "path", "value": "$steps.create.outputs.itemId"}
                        ],
                        "successCriteria": [
                            {"condition": "$response.body#/id == $steps.create.outputs.itemId"}
                        ],
                    },
                )
            ]
        ),
        recorder,
    )

    _assert_result(result, gate="PASS")


@pytest.mark.parametrize(
    "criterion",
    [
        {"condition": "$statusCode == 200"},
        {
            "context": "$response.body",
            "type": {"type": "jsonpath", "version": "rfc9535"},
            "condition": "$.ok",
        },
    ],
)
def test_simple_and_rfc9535_jsonpath_criteria(
    monkeypatch: pytest.MonkeyPatch, criterion: dict[str, Any]
) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True})])
    result = _run(
        monkeypatch,
        _document(
            [
                _workflow(
                    "main",
                    {"stepId": "health", "operationId": "health", "successCriteria": [criterion]},
                )
            ]
        ),
        recorder,
    )

    _assert_result(result, gate="PASS")


def test_jsonpath_passes_for_a_nonempty_node_list_even_when_value_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": False})])
    criterion = {
        "context": "$response.body",
        "type": "jsonpath",
        "condition": "$.ok",
    }

    result = _run(
        monkeypatch,
        _document(
            [
                _workflow(
                    "main",
                    {"stepId": "health", "operationId": "health", "successCriteria": [criterion]},
                )
            ]
        ),
        recorder,
    )

    _assert_result(result, gate="PASS")


def test_jsonpath_filter_resolves_embedded_runtime_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [
            _response(
                200,
                [
                    {"id": "item-1", "name": "book"},
                    {"id": "item-2", "name": "pen"},
                ],
            )
        ]
    )
    criterion = {
        "context": "$response.body",
        "type": {"type": "jsonpath", "version": "rfc9535"},
        "condition": "$[?@.name == '{$inputs.expectedName}']",
    }
    step = {
        "stepId": "search",
        "operationId": "searchItems",
        "parameters": [
            {"name": "q", "in": "query", "value": "all"},
            {"name": "X-Token", "in": "header", "value": "test-token"},
        ],
        "successCriteria": [criterion],
    }
    workflow = _workflow(
        "main",
        step,
        inputs={
            "type": "object",
            "required": ["expectedName"],
            "properties": {"expectedName": {"type": "string"}},
        },
    )

    result = _run(
        monkeypatch,
        _document([workflow]),
        recorder,
        workflow_inputs={"expectedName": "book"},
    )

    _assert_result(result, gate="PASS")
    assert result["steps"][0]["criteria"] == [{"criterion": criterion, "passed": True}]


def test_simple_criteria_support_case_insensitive_strings_and_logical_operators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"id": "item-1", "name": "BOOK"})])
    step = {
        "stepId": "read",
        "operationId": "getItem",
        "parameters": [{"name": "id", "in": "path", "value": "item-1"}],
        "successCriteria": [
            {
                "condition": (
                    "$statusCode == 200 && "
                    "($response.body#/name == 'book' || $response.body#/name == 'magazine')"
                )
            }
        ],
    }

    result = _run(monkeypatch, _document([_workflow("main", step)]), recorder)

    _assert_result(result, gate="PASS")


def test_simple_numeric_operator_coerces_a_numeric_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True})])
    step = {
        "stepId": "health",
        "operationId": "health",
        "successCriteria": [{"condition": "$inputs.threshold > 2"}],
    }
    workflow = _workflow(
        "main",
        step,
        inputs={
            "type": "object",
            "required": ["threshold"],
            "properties": {"threshold": {"type": "string"}},
        },
    )

    result = _run(
        monkeypatch,
        _document([workflow]),
        recorder,
        workflow_inputs={"threshold": "10"},
    )

    _assert_result(result, gate="PASS")


def test_on_success_goto_runs_local_target(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True}), _response(200, {"ok": True})])
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "first",
                    "operationId": "health",
                    "onSuccess": [{"name": "jump", "type": "goto", "stepId": "last"}],
                },
                {"stepId": "skipped", "operationId": "health"},
                {"stepId": "last", "operationId": "health"},
            )
        ]
    )
    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="PASS")
    assert len(recorder.calls) == 2


def test_on_failure_goto_runs_cleanup_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([_response(500, {"error": "broken"}), _response(204)])
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "work",
                    "operationId": "unstable",
                    "onFailure": [{"name": "cleanup", "type": "goto", "workflowId": "cleanup"}],
                },
            ),
            _workflow(
                "cleanup",
                {
                    "stepId": "delete",
                    "operationId": "deleteItem",
                    "parameters": [{"name": "id", "in": "path", "value": "item-1"}],
                },
            ),
        ]
    )
    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")
    cleanup = result.get("cleanup") or result.get("cleanupEvidence")
    assert cleanup is not None
    assert "delete" in str(cleanup)
    assert "PASS" in str(cleanup)


def test_transport_failure_still_runs_unconditional_cleanup_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [
            httpx.ConnectError("connection dropped", request=httpx.Request("POST", TARGET_URL)),
            _response(204),
        ]
    )
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "work",
                    "operationId": "unstable",
                    "onFailure": [{"name": "cleanup", "type": "goto", "workflowId": "cleanup"}],
                },
            ),
            _workflow(
                "cleanup",
                {
                    "stepId": "delete",
                    "operationId": "deleteItem",
                    "parameters": [{"name": "id", "in": "path", "value": "item-1"}],
                },
            ),
        ]
    )

    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="INCONCLUSIVE", defect="ENVIRONMENT_DEFECT")
    assert [call["method"] for call in recorder.calls] == ["GET", "DELETE"]
    assert "PASS" in str(result.get("cleanupEvidence"))


def test_failure_goto_step_runs_cleanup_without_masking_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [
            _response(200, {"id": "item-1", "name": "book"}),
            _response(200, {"ok": True}),
        ]
    )
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "work",
                    "operationId": "unstable",
                    "successCriteria": [{"condition": "$statusCode == 201"}],
                    "onFailure": [{"name": "cleanup", "type": "goto", "stepId": "cleanup"}],
                },
                {"stepId": "cleanup", "operationId": "health"},
            )
        ]
    )

    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")
    assert result["finding"]["code"] == "SUCCESS_CRITERIA_FAILED"
    assert "PASS" in str(result.get("cleanupEvidence"))


def test_cleanup_failure_is_retained_as_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder(
        [_response(500, {"error": "broken"}), _response(500, {"error": "cleanup-broken"})]
    )
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "work",
                    "operationId": "unstable",
                    "onFailure": [{"name": "cleanup", "type": "goto", "workflowId": "cleanup"}],
                },
            ),
            _workflow(
                "cleanup",
                {
                    "stepId": "delete",
                    "operationId": "deleteItem",
                    "parameters": [{"name": "id", "in": "path", "value": "item-1"}],
                },
            ),
        ]
    )
    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")
    cleanup = result.get("cleanup") or result.get("cleanupEvidence")
    assert cleanup is not None
    assert "cleanup-broken" in str(cleanup)


def test_response_schema_failure_still_runs_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": "not-a-boolean"}), _response(204)])
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "work",
                    "operationId": "health",
                    "outputs": {"missing": "$response.body#/id"},
                    "onFailure": [
                        {
                            "name": "cleanup",
                            "type": "goto",
                            "workflowId": "cleanup",
                        }
                    ],
                },
            ),
            _workflow(
                "cleanup",
                {
                    "stepId": "delete",
                    "operationId": "deleteItem",
                    "parameters": [{"name": "id", "in": "path", "value": "item-1"}],
                },
            ),
        ]
    )

    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")
    assert result["contractStatus"] == "FAIL"
    assert [call["method"] for call in recorder.calls] == ["GET", "DELETE"]
    assert "PASS" in str(result.get("cleanupEvidence"))


def test_default_retry_does_not_repeat_without_retry_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [_response(500, {"error": "broken"}), _response(200, {"id": "late", "name": "book"})]
    )
    result = _run(
        monkeypatch,
        _document([_workflow("main", {"stepId": "unstable", "operationId": "unstable"})]),
        recorder,
    )

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")
    assert len(recorder.calls) == 1


def test_bounded_retry_repeats_then_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder(
        [_response(500, {"error": "temporary"}), _response(200, {"id": "item-1", "name": "book"})]
    )
    step = {
        "stepId": "unstable",
        "operationId": "unstable",
        "onFailure": [{"name": "retry", "type": "retry", "retryLimit": 1}],
    }
    events: list[dict[str, Any]] = []
    with _testing_progress_scope(events.append):
        result = _run(monkeypatch, _document([_workflow("main", step)]), recorder)

    _assert_result(result, gate="PASS")
    assert len(recorder.calls) == 2
    assert any(
        event["scope"] == "step"
        and event["status"] == "RUNNING"
        and event.get("attempt") == 2
        for event in events
    )


def test_retry_without_limit_uses_the_spec_default_of_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [
            _response(500, {"error": "temporary"}),
            _response(200, {"id": "item-1", "name": "book"}),
        ]
    )
    step = {
        "stepId": "unstable",
        "operationId": "unstable",
        "onFailure": [{"name": "retry", "type": "retry"}],
    }

    result = _run(monkeypatch, _document([_workflow("main", step)]), recorder)

    _assert_result(result, gate="PASS")
    assert len(recorder.calls) == 2


def test_workflow_dependency_runs_first_and_satisfies_cross_workflow_step_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True}), _response(200, {"ok": True})])
    document = _document(
        [
            _workflow(
                "setup",
                {"stepId": "prepare", "operationId": "health"},
            ),
            _workflow(
                "main",
                {
                    "stepId": "verify",
                    "operationId": "health",
                    "dependsOn": ["$workflows.setup.steps.prepare"],
                },
                dependsOn=["setup"],
            ),
        ]
    )

    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="PASS")
    assert [item.get("stepId") for item in result["steps"]] == ["prepare", "verify"]


def test_workflow_default_failure_action_runs_cleanup_with_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(500, {"error": "broken"}), _response(204)])
    document = _document(
        [
            _workflow(
                "main",
                {"stepId": "work", "operationId": "unstable"},
                failureActions=[
                    {
                        "name": "cleanup",
                        "type": "goto",
                        "workflowId": "cleanup",
                        "parameters": [{"name": "itemId", "value": "item-1"}],
                    }
                ],
            ),
            _workflow(
                "cleanup",
                {
                    "stepId": "delete",
                    "operationId": "deleteItem",
                    "parameters": [{"name": "id", "in": "path", "value": "$inputs.itemId"}],
                },
                inputs={
                    "type": "object",
                    "required": ["itemId"],
                    "properties": {"itemId": {"type": "string"}},
                },
            ),
        ]
    )

    result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")
    assert recorder.calls[1]["url"] == f"{TARGET_URL}/items/item-1"
    assert "PASS" in str(result.get("cleanupEvidence"))


def test_local_workflow_call_executes_child_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True})])
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "child-call",
                    "workflowId": "child",
                    "parameters": [{"name": "mode", "value": "quick"}],
                },
            ),
            _workflow(
                "child",
                {"stepId": "health", "operationId": "health"},
                inputs={"type": "object", "properties": {"mode": {"type": "string"}}},
            ),
        ]
    )
    events: list[dict[str, Any]] = []
    with _testing_progress_scope(events.append):
        result = _run(monkeypatch, document, recorder)

    _assert_result(result, gate="PASS")
    assert recorder.calls[0]["url"] == f"{TARGET_URL}/health"
    assert [(event["status"], event["workflow_id"]) for event in events if event["scope"] == "workflow"] == [
        ("RUNNING", "main"),
        ("RUNNING", "child"),
        ("PASS", "child"),
        ("PASS", "main"),
    ]


def test_local_workflow_call_reuses_child_inputs_and_scopes_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder([_response(200, {"id": "child-item", "name": "book"})])
    proposed: list[InputValueRequest] = []
    document = _document(
        [
            _workflow("main", {"stepId": "child-call", "workflowId": "child"}),
            _workflow(
                "child",
                {"stepId": "get-child", "operationId": "getItem"},
                inputs={
                    "type": "object",
                    "required": ["mode"],
                    "properties": {"mode": {"type": "string"}},
                },
            ),
        ]
    )

    result = _run(
        monkeypatch,
        document,
        recorder,
        workflow_inputs_by_id={"child": {"mode": "preserved"}},
        propose_input=lambda request: proposed.append(request) or "child-item",
    )

    _assert_result(result, gate="PASS")
    assert [(item.operation_context, item.operation_id, item.location) for item in proposed] == [
        ("child", "getItem", "path.id")
    ]
    assert result["workflowInputsById"]["child"] == {"mode": "preserved"}
    assert result["steps"][0]["workflowId"] == "child"
    assert result["steps"][1]["calledWorkflowId"] == "child"


def test_workflow_inputs_are_fixed_and_reused_without_proposing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _HttpRecorder(
        [
            _response(200, {"id": "fixed-item", "name": "book"}),
            _response(200, {"id": "fixed-item", "name": "book"}),
        ]
    )
    proposed: list[Any] = []
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "first",
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "value": "$inputs.itemId"}],
                },
                {
                    "stepId": "second",
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "value": "$inputs.itemId"}],
                },
                inputs={
                    "type": "object",
                    "required": ["itemId"],
                    "properties": {"itemId": {"type": "string"}},
                },
            )
        ]
    )
    result = _run(
        monkeypatch,
        document,
        recorder,
        workflow_inputs={"itemId": "fixed-item"},
        propose_input=lambda request: proposed.append(request) or "proposed",
    )

    _assert_result(result, gate="PASS")
    assert proposed == []
    assert [call["url"] for call in recorder.calls] == [
        f"{TARGET_URL}/items/fixed-item",
        f"{TARGET_URL}/items/fixed-item",
    ]


def test_transport_failure_is_inconclusive_environment_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", f"{TARGET_URL}/health")
    recorder = _HttpRecorder([httpx.ConnectError("offline", request=request)])
    result = _run(
        monkeypatch,
        _document([_workflow("main", {"stepId": "health", "operationId": "health"})]),
        recorder,
    )

    _assert_result(result, gate="INCONCLUSIVE", defect="ENVIRONMENT_DEFECT")


def test_server_500_is_sut_defect(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([_response(500, {"error": "broken"})])
    result = _run(
        monkeypatch,
        _document([_workflow("main", {"stepId": "unstable", "operationId": "unstable"})]),
        recorder,
    )

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")
    assert result["contractStatus"] == "FAIL"


def test_response_schema_mismatch_is_sut_defect(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": "not-a-boolean"})])
    result = _run(
        monkeypatch,
        _document([_workflow("main", {"stepId": "health", "operationId": "health"})]),
        recorder,
    )

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")


def test_criterion_mismatch_is_sut_defect(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True})])
    step = {
        "stepId": "health",
        "operationId": "health",
        "successCriteria": [{"condition": "$statusCode == 201"}],
    }
    result = _run(monkeypatch, _document([_workflow("main", step)]), recorder)

    _assert_result(result, gate="FAIL", defect="SUT_DEFECT")


def test_missing_fixed_workflow_input_is_test_defect(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([])
    document = _document(
        [
            _workflow(
                "main",
                {
                    "stepId": "get",
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "value": "$inputs.itemId"}],
                },
            )
        ]
    )
    result = _run(monkeypatch, document, recorder, workflow_inputs={})

    _assert_result(result, gate="FAIL", defect="TEST_DEFECT")
    assert recorder.calls == []


def test_contract_only_pass_is_not_semantic_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _HttpRecorder([_response(200, {"ok": True})])
    result = _run(
        monkeypatch,
        _document([_workflow("main", {"stepId": "health", "operationId": "health"})]),
        recorder,
    )

    _assert_result(result, gate="PASS")
    assert result.get("semanticStatus") in {"UNVERIFIED", "INCONCLUSIVE"}
