import httpx
import pytest

from app.testing.schemas.arazzo import ArazzoValidationError, validate_arazzo_document
from app.testing.utils.arazzo_executor import execute_arazzo_workflow
from app.testing.utils.arazzo_expression import interpolate
from app.testing.utils.functional_executor import security_parameters


def _openapi():
    return {"openapi": "3.0.3", "info": {"title": "secured", "version": "1"},
            "components": {"securitySchemes": {"login": {"type": "http", "scheme": "basic"}}},
            "security": [{"login": []}], "paths": {"/account": {"get": {"operationId": "read",
            "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}}}}}


def _workflow():
    return {"arazzo": "1.1.0", "info": {"title": "x", "version": "1"}, "sourceDescriptions": [{"name": "application", "type": "openapi", "url": "openapi.json"}],
            "workflows": [{"workflowId": "w", "steps": [{"stepId": "s", "operationId": "read", "parameters": [{"in": "header", "name": "Authorization", "value": "Basic eA=="}], "successCriteria": [{"condition": "$statusCode == 200"}]}]}]}


def test_explicit_authorization_is_valid_and_not_overwritten(monkeypatch):
    calls = []
    def request(method, url, **kwargs):
        calls.append(kwargs); return httpx.Response(200, json={}, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, "request", request)
    assert execute_arazzo_workflow(_workflow(), "w", openapi=_openapi(), target_url="http://x", require_explicit_values=True)["gateStatus"] == "PASS"
    assert calls[0]["headers"]["Authorization"] == "Basic eA==" and calls[0]["auth"] is None


def test_security_override_and_literal_braces():
    doc = _openapi(); doc["paths"]["/account"]["get"]["security"] = []
    with pytest.raises(ArazzoValidationError, match="absent"):
        validate_arazzo_document(_workflow(), openapi=doc)
    assert security_parameters(_openapi(), {})[0]["name"] == "Authorization"
    assert interpolate('{"days":"MON"}', {}) == '{"days":"MON"}'
    assert interpolate('id={$inputs.id}; literal={unchanged}', {"inputs": {"id": "42"}}) == "id=42; literal={unchanged}"
