"""API 명세: 결정론적 변환과 요소-우선 피드백 흐름 (네트워크 불필요).

요소→OpenAPI 변환이 유효한 OpenAPI 3.0 dict를 내는지, 그리고 피드백이 OpenAPI dict가
아니라 요소 모델을 편집한 뒤 같은 변환으로 재렌더되는지 확인한다.
"""
from __future__ import annotations

import app.design.nodes.api_spec as api_nodes
from app.design.graphs.api_spec_graph import (
    api_spec_feedback_graph,
    api_spec_graph,
)
from app.design.nodes.api_spec import (
    convert_to_api_spec_code,
    revise_api_elements,
)
from app.design.services.api_spec.openapi_builder import (
    generate_openapi_spec_from_json,
)


def test_api_spec_conversion_creates_valid_openapi_dict():
    elements = {
        "title": "Stock Purchase API",
        "description": "Trading API",
        "version": "1.0.0",
        "endpoints": [
            {
                "path": "/purchases",
                "method": "post",
                "summary": "Initiate purchase",
                "tag": "Stock Purchase",
                "request_body_schema_ref": "PurchaseRequest",
                "request_body_required": True,
                "responses": [
                    {
                        "status_code": "201",
                        "description": "Created",
                        "schema_ref": "PurchaseRecord",
                    },
                    {
                        "status_code": "400",
                        "description": "Bad Request",
                        "schema_ref": "ErrorResponse",
                    },
                ],
            }
        ],
        "schemas": [
            {
                "name": "PurchaseRequest",
                "type": "object",
                "properties": [
                    {
                        "name": "siteName",
                        "type": "string",
                        "required": True,
                        "example": "E*Trade",
                    }
                ],
            },
            {
                "name": "PurchaseRecord",
                "type": "object",
                "properties": [
                    {
                        "name": "purchaseId",
                        "type": "string",
                        "required": True,
                        "example": "P123",
                    }
                ],
            },
            {
                "name": "ErrorResponse",
                "type": "object",
                "properties": [
                    {
                        "name": "message",
                        "type": "string",
                        "required": True,
                        "example": "Error occurred",
                    }
                ],
            },
        ],
    }

    spec = generate_openapi_spec_from_json(elements)

    assert spec["openapi"] == "3.0.3"
    assert spec["info"]["title"] == "Stock Purchase API"
    assert "/purchases" in spec["paths"]
    assert "post" in spec["paths"]["/purchases"]
    assert "201" in spec["paths"]["/purchases"]["post"]["responses"]
    assert "PurchaseRequest" in spec["components"]["schemas"]
    assert "PurchaseRecord" in spec["components"]["schemas"]


def test_empty_api_model_yields_empty_dict():
    assert generate_openapi_spec_from_json({}) == {}
    assert generate_openapi_spec_from_json({"endpoints": [], "schemas": []}) == {}


def test_api_feedback_edits_elements_then_reconverts(monkeypatch):
    def fake_revise(current_elements, feedback, scenario_text="", class_diagram_puml="", sequence_diagram_puml=""):
        return {
            "title": "Renamed API",
            "endpoints": [
                {
                    "path": "/health",
                    "method": "get",
                    "summary": "Health check",
                    "responses": [{"status_code": "200", "description": "OK"}],
                }
            ],
            "schemas": [],
        }

    monkeypatch.setattr(api_nodes, "revise_api_elements_fn", fake_revise)

    state = {
        "extracted_api_elements": {
            "title": "Old API",
            "endpoints": [],
            "schemas": [],
        },
        "api_spec_feedback": "add health check endpoint",
        "usecase_spec": {},
    }
    revised = revise_api_elements(state)
    assert revised["extracted_api_elements"]["title"] == "Renamed API"

    merged = {**state, **revised}
    out = convert_to_api_spec_code(merged)
    assert out["api_spec"]["info"]["title"] == "Renamed API"
    assert "/health" in out["api_spec"]["paths"]


def test_api_generation_graph_structure():
    nodes = set(api_spec_graph.get_graph().nodes)
    assert "extract_api_elements" in nodes
    assert "convert_to_api_spec_code" in nodes
    assert "validate_api_spec_syntax" in nodes


def test_api_feedback_graph_edits_model_not_text():
    nodes = set(api_spec_feedback_graph.get_graph().nodes)
    assert "revise_api_elements" in nodes
    assert "convert_to_api_spec_code" in nodes
    assert "validate_api_spec_syntax" in nodes
