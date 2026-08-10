from __future__ import annotations

from app.core.orchestration.api_traceability import (
    explicit_field_claims,
    missing_explicit_fields,
)
from app.core.orchestration.contracts import RunMode, StepContext, StepStatus
from app.core.orchestration.providers import BuiltinCloudDesignProvider


def _api(response_fields: list[str]) -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/conversions": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/ConversionRequest"
                    }}}},
                    "responses": {"200": {"content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/ConversionResponse"
                    }}}}},
                }
            }
        },
        "components": {"schemas": {
            "ConversionRequest": {"type": "object", "properties": {
                name: {"type": "string"}
                for name in ("category", "value", "fromUnit", "toUnit")
            }},
            "ConversionResponse": {"type": "object", "properties": {
                name: {"type": "string"} for name in response_fields
            }},
        }},
    }


def test_explicit_field_claims_preserve_request_and_response_direction():
    requirements = [{
        "id": "FR1",
        "text": (
            "The endpoint accepts a JSON payload containing the fields category, value, "
            "fromUnit, and toUnit, and returns a JSON response containing the fields "
            "result and unit."
        ),
    }]

    claims = explicit_field_claims(requirements)

    assert {(item.direction, item.field) for item in claims} == {
        ("request", "category"), ("request", "value"),
        ("request", "fromUnit"), ("request", "toUnit"),
        ("response", "result"), ("response", "unit"),
    }


def test_explicit_field_claims_stop_before_following_capability_description():
    requirements = [{
        "id": "FR1",
        "text": (
            "The endpoint accepts JSON fields category and value, and returns JSON "
            "fields result and unit, supporting units meter and centimeter."
        ),
    }]

    claims = explicit_field_claims(requirements)

    assert {(item.direction, item.field) for item in claims} == {
        ("request", "category"), ("request", "value"),
        ("response", "result"), ("response", "unit"),
    }


def test_response_payload_does_not_override_return_direction():
    requirements = [{
        "id": "FR1",
        "text": (
            "The endpoint accepts a JSON payload containing fields category and value, "
            "and returns a JSON payload containing fields result and unit."
        ),
    }]

    claims = explicit_field_claims(requirements)

    assert {(item.direction, item.field) for item in claims} == {
        ("request", "category"), ("request", "value"),
        ("response", "result"), ("response", "unit"),
    }


def test_explicit_field_claims_stop_at_when_clause():
    requirements = [{
        "id": "FR2",
        "text": (
            "The system shall accept a JSON payload containing the fields title "
            "and content when creating a note."
        ),
    }]

    claims = explicit_field_claims(requirements)

    assert [(item.direction, item.field) for item in claims] == [
        ("request", "content"), ("request", "title")
    ]


def test_explicit_field_claims_support_named_markdown_identifier():
    requirements = [
        {
            "id": "FR5",
            "text": "Each record shall contain a JSON field named **name** of type string.",
        },
        {
            "id": "FR6",
            "text": "Each record shall contain a JSON field named `value` of type string.",
        },
    ]

    claims = explicit_field_claims(requirements)

    assert [(item.requirement_id, item.field) for item in claims] == [
        ("FR5", "name"),
        ("FR6", "value"),
    ]


def test_openapi_gate_reports_only_missing_explicit_fields():
    requirements = [
        {"id": "FR1", "text": "The JSON payload contains fields category and value."},
        {"id": "FR2", "text": "The JSON response contains fields result and unit."},
        {"id": "FR3", "text": "The service should be easy to operate."},
    ]

    missing = missing_explicit_fields(
        requirements, _api(["convertedValue", "targetUnit"])
    )

    assert [(item.requirement_id, item.direction, item.field) for item in missing] == [
        ("FR2", "response", "result"),
        ("FR2", "response", "unit"),
    ]


def test_openapi_gate_passes_matching_explicit_contract():
    requirements = [
        {"id": "FR1", "text": "The JSON response contains fields result and unit."}
    ]

    assert missing_explicit_fields(requirements, _api(["result", "unit"])) == []


def test_cloud_design_provider_blocks_mismatch_before_implementation():
    result = BuiltinCloudDesignProvider().run(
        {
            "requirements_result": {
                "requirements": [{
                    "id": "FR2",
                    "text": "The JSON response contains fields result and unit.",
                }],
                "resource_spec": {
                    "provider": "gcp", "region": "asia-northeast3"
                },
            },
            "design_result": {
                "artifacts": {"class_diagram": "class A", "api_spec": _api([
                    "convertedValue", "targetUnit"
                ])},
            },
            "use_cloud_kb": True,
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.FAILED
    assert result.diagnostics[0].code == "ValueError"
    assert "FR2:response:result" in result.diagnostics[0].message


def test_cloud_design_provider_repairs_structured_api_once_before_enrichment():
    calls = []

    class CloudAdapter:
        @staticmethod
        def finalize(**kwargs):
            return {"status": "completed", "api": kwargs["design_result"]["artifacts"]["api_spec"]}

    def revise(current, feedback, context, targets):
        calls.append((current, feedback, context, targets))
        return {"corrected": True}

    provider = BuiltinCloudDesignProvider(
        adapter=CloudAdapter(),
        revise_api=revise,
        render_api=lambda _model: _api(["result", "unit"]),
    )
    result = provider.run(
        {
            "requirements_result": {
                "requirements": [{
                    "id": "FR1",
                    "text": "The JSON response contains fields result and unit.",
                }],
            },
            "design_result": {
                "artifacts": {
                    "class_diagram": "class A",
                    "sequence_diagram": "A -> B",
                    "api_spec": _api(["result", "targetUnit"]),
                },
                "api_spec_model": {"original": True},
            },
            "use_cloud_kb": True,
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert len(calls) == 1
    assert "FR1:response:unit" in calls[0][1]
    assert result.metrics["llm_calls"] == 1
    assert result.metrics["api_traceability_repaired"] is True
    assert result.metrics["api_traceability_mismatches_observed"] == 1
    assert result.output["design_result"]["api_spec_model"] == {"corrected": True}
