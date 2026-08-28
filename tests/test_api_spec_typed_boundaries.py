"""API typed 경계가 저장·OpenAPI·하류 소비 계약을 보존하는지 검사한다."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.design.graphs import subgraphs as design_subgraphs
from app.design.knowledge.detectors import api_spec_findings
from app.design.rtm import build_design_rtm
from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec import service
from app.design.services.api_spec.models import ApiSpecModel
from app.design.services.api_spec.normalization import normalize_api_spec_model
from app.design.services.api_spec.projection import build_openapi_from_model
from app.design.services.api_spec.validation import (
    ApiSpecValidationReport,
    validate_api_spec_model,
)
from app.design.services.sequence_diagram.projection import SequenceCollection
from app.implementation.generation.frontend_scaffold import validate_openapi


def _bce_model() -> BCEModel:
    return BCEModel.model_validate(
        {
            "Classes": [
                {
                    "className": "CatalogBoundary",
                    "stereotype": "Boundary",
                    "use_case_ids": ["UC1"],
                    "operations": [
                        {
                            "operationId": "proposal-id",
                            "name": "browseCatalog",
                            "parameters": [
                                {"name": "filter", "type": "CourseFilter"}
                            ],
                            "returnType": "List<Course>",
                            "stepRefs": ["UC1:main:1"],
                        }
                    ],
                },
                {
                    "className": "CatalogControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC1"],
                    "operations": [
                        {
                            "operationId": "proposal-id",
                            "name": "searchCatalog",
                            "parameters": [
                                {"name": "filter", "type": "CourseFilter"}
                            ],
                            "returnType": "List<Course>",
                            "stepRefs": ["UC1:main:1", "UC1:main:2"],
                        }
                    ],
                },
                {
                    "className": "Course",
                    "stereotype": "Entity",
                    "use_case_ids": ["UC1"],
                    "fields": ["courseId : String", "title : String"],
                    "identifier": ["courseId"],
                },
            ],
            "DataTypes": [
                {
                    "name": "CourseFilter",
                    "kind": "valueObject",
                    "fields": ["keyword : String"],
                }
            ],
            "Relationships": [],
            "Collaborations": [],
        }
    )


def _sequence_model(*, include_control_call: bool = True) -> SequenceCollection:
    messages = [
        {
            "source": "Student",
            "target": "CatalogBoundary",
            "label": "browseCatalog(filter:CourseFilter)",
            "type": "sync",
            "use_case_ids": ["UC1"],
            "step_ids": ["UC1:main:1"],
            "call_id": "UC1:main:1::call:1",
            "arguments": [
                {
                    "parameter": "filter",
                    "type": "CourseFilter",
                    "source_kind": "input",
                    "source_ref": "UC1:main:1#filter",
                }
            ],
        }
    ]
    if include_control_call:
        messages.append(
            {
                "source": "CatalogBoundary",
                "target": "CatalogControl",
                "label": "searchCatalog(filter:CourseFilter)",
                "type": "sync",
                "use_case_ids": ["UC1"],
                "step_ids": ["UC1:main:1", "UC1:main:2"],
                "call_id": "UC1:main:1::call:2",
                "arguments": [
                    {
                        "parameter": "filter",
                        "type": "CourseFilter",
                        "source_kind": "call_parameter",
                        "source_ref": "UC1:main:1::call:1#filter",
                    }
                ],
            }
        )
    return SequenceCollection.model_validate(
        {
            "Diagrams": [
                {
                    "use_case_id": "UC1",
                    "use_case_name": "Browse catalog",
                    "Participants": [
                        {
                            "name": "Student",
                            "alias": "Student",
                            "kind": "actor",
                        },
                        {
                            "name": "CatalogBoundary",
                            "alias": "CatalogBoundary",
                            "kind": "boundary",
                            "source_class": "CatalogBoundary",
                        },
                        {
                            "name": "CatalogControl",
                            "alias": "CatalogControl",
                            "kind": "control",
                            "source_class": "CatalogControl",
                        },
                    ],
                    "Messages": messages,
                }
            ]
        }
    )


def _proposal() -> ApiSpecModel:
    return ApiSpecModel.model_validate(
        {
            "title": "Catalog API",
            "version": "1.0.0",
            "Endpoints": [
                {
                    "path": "/courses",
                    "method": "get",
                    "summary": "Browse courses",
                    "operation_id": "browseCourses",
                    "responses": [
                        {"status": 200, "description": "Matching courses"}
                    ],
                    "source_classes": ["CatalogBoundary"],
                    "use_case_ids": ["UC1"],
                    "control_binding": {
                        "control": "CatalogControl",
                        "method": "searchCatalog",
                        "arguments": [
                            {"name": "filter", "source": "$query.filter"}
                        ],
                        "outcomes": [{"status": 200, "outcome": "found"}],
                    },
                }
            ],
            "Schemas": [
                {
                    "name": "Course",
                    "source_class": "Course",
                    "fields": [
                        {"name": "courseId", "type": "string"},
                        {"name": "title", "type": "string"},
                    ],
                }
            ],
        }
    )


def test_typed_normalization_uses_exact_bce_contract_without_plantuml() -> None:
    proposal = _proposal()

    normalized = normalize_api_spec_model(proposal, _bce_model())

    endpoint = normalized.Endpoints[0]
    assert [(field.name, field.type) for field in endpoint.query_params] == [
        ("filter", "CourseFilter")
    ]
    assert endpoint.responses[0].schema_name == "Course"
    assert endpoint.responses[0].is_array is True
    assert endpoint.source_classes == ["CatalogBoundary", "CatalogControl"]
    assert proposal.Endpoints[0].query_params == []


def test_generation_service_accepts_typed_inputs_and_returns_normalized_model(
    monkeypatch,
) -> None:
    calls: list[type[ApiSpecModel]] = []

    def propose(_messages, schema):
        calls.append(schema)
        return _proposal().model_dump()

    monkeypatch.setattr(service, "parse_structured", propose)

    result = service.generate_api_spec_model(
        "UC1: A student browses the catalog.", _bce_model(), _sequence_model()
    )

    assert calls == [ApiSpecModel]
    assert isinstance(result, ApiSpecModel)
    assert result.Endpoints[0].query_params[0].type == "CourseFilter"
    assert result.Endpoints[0].responses[0].is_array is True


def test_empty_feedback_preserves_model_without_an_llm_call(monkeypatch) -> None:
    current = normalize_api_spec_model(_proposal(), _bce_model())

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("empty feedback must not call structured LLM")

    monkeypatch.setattr(service, "parse_structured", unexpected_call)

    revised = service.revise_api_spec_model(
        current,
        "",
        "UC1: A student browses the catalog.",
        _bce_model(),
        _sequence_model(),
    )

    assert revised is current


def test_revision_service_uses_one_structured_call_and_returns_typed_model(
    monkeypatch,
) -> None:
    current = normalize_api_spec_model(_proposal(), _bce_model())
    calls: list[type[ApiSpecModel]] = []

    def revise(_messages, schema):
        calls.append(schema)
        payload = current.model_dump()
        payload["Endpoints"][0]["summary"] = "Browse the current catalog"
        return payload

    monkeypatch.setattr(service, "parse_structured", revise)

    revised = service.revise_api_spec_model(
        current,
        "Clarify the endpoint summary.",
        "UC1: A student browses the catalog.",
        _bce_model(),
        _sequence_model(),
        {"browseCourses"},
    )

    assert calls == [ApiSpecModel]
    assert isinstance(revised, ApiSpecModel)
    assert revised.Endpoints[0].summary == "Browse the current catalog"
    assert revised.Endpoints[0].query_params[0].type == "CourseFilter"


def test_accepted_model_round_trips_existing_json_and_openapi_contract() -> None:
    normalized = normalize_api_spec_model(_proposal(), _bce_model())

    stored = normalized.model_dump()
    restored = ApiSpecModel.model_validate(json.loads(json.dumps(stored)))
    openapi = build_openapi_from_model(restored)

    assert restored.model_dump() == stored
    assert openapi == build_openapi_from_model(restored)
    operation = openapi["paths"]["/courses"]["get"]
    assert operation["parameters"] == [
        {
            "name": "filter",
            "in": "query",
            "required": True,
            "schema": {"type": "object"},
        }
    ]
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/Course"},
    }
    assert operation["x-easydep-control"] == {
        "control": "CatalogControl",
        "method": "searchCatalog",
        "arguments": {"filter": "$query.filter"},
        "outcomes": {"200": "found"},
    }
    validate_openapi(openapi)


def test_typed_validation_is_observational_and_checks_sequence_binding() -> None:
    normalized = normalize_api_spec_model(_proposal(), _bce_model())
    before = normalized.model_dump()

    accepted = validate_api_spec_model(
        normalized, _bce_model(), _sequence_model()
    )
    rejected = validate_api_spec_model(
        normalized, _bce_model(), _sequence_model(include_control_call=False)
    )

    assert accepted.valid is True
    assert accepted.errors == []
    assert rejected.valid is False
    assert rejected.errors == [
        "browseCourses: control call is absent from sequence model"
    ]
    assert normalized.model_dump() == before


def test_accepted_api_model_keeps_rtm_traceability_shape() -> None:
    bce_model = _bce_model()
    sequence_model = _sequence_model()
    normalized = normalize_api_spec_model(_proposal(), bce_model)
    state = {
        "usecase_spec": {
            "use_cases": [{"id": "UC1", "name": "Browse catalog"}]
        },
        "extracted_bce_classes": bce_model.model_dump(by_alias=True),
        "sequence_diagram_model": sequence_model.model_dump(),
        "api_spec_model": normalized.model_dump(),
    }

    rtm = build_design_rtm(state)
    endpoint = next(
        row
        for row in rtm["rows"]
        if row["stage"] == "api_spec" and row["element"] == "browseCourses"
    )

    assert endpoint["sources"] == {
        "class": ["CatalogBoundary", "CatalogControl"],
        "use_case": ["UC1"],
    }


def test_graph_spec_validates_raw_inputs_and_dumps_typed_result(monkeypatch) -> None:
    captured: list[tuple[BCEModel, SequenceCollection]] = []

    def generate(
        _scenario_text: str,
        bce_model: BCEModel,
        sequence_model: SequenceCollection,
    ) -> ApiSpecModel:
        captured.append((bce_model, sequence_model))
        return _proposal()

    monkeypatch.setattr(design_subgraphs, "extract_api_spec_model", generate)
    state = {
        "usecase_spec": {
            "use_cases": [{"id": "UC1", "name": "Browse catalog"}]
        },
        "extracted_bce_classes": _bce_model().model_dump(by_alias=True),
        "sequence_diagram_model": _sequence_model().model_dump(),
    }

    stored = design_subgraphs.API_SPEC_SPEC.extract(state)

    assert len(captured) == 1
    assert isinstance(captured[0][0], BCEModel)
    assert isinstance(captured[0][1], SequenceCollection)
    assert stored == _proposal().model_dump()

    with pytest.raises(ValidationError):
        design_subgraphs.API_SPEC_SPEC.extract(
            {**state, "extracted_bce_classes": {"Classes": "invalid"}}
        )
    with pytest.raises(ValidationError):
        design_subgraphs.API_SPEC_SPEC.render({"Endpoints": "invalid"})


def test_graph_spec_observes_typed_report_without_changing_semantic_findings(
    monkeypatch,
    caplog,
) -> None:
    model = normalize_api_spec_model(_proposal(), _bce_model()).model_dump()
    model["Endpoints"][0]["path"] = "/courses/{courseId}"
    state = {
        "usecase_spec": {
            "use_cases": [{"id": "UC1", "name": "Browse catalog"}]
        },
        "extracted_bce_classes": _bce_model().model_dump(by_alias=True),
        "sequence_diagram_model": _sequence_model().model_dump(),
    }
    observed: list[tuple[ApiSpecModel, BCEModel, SequenceCollection]] = []

    def observe(
        accepted: ApiSpecModel,
        bce_model: BCEModel,
        sequence_model: SequenceCollection,
    ) -> ApiSpecValidationReport:
        observed.append((accepted, bce_model, sequence_model))
        return ApiSpecValidationReport(
            valid=False,
            errors=["browseCourses: stricter observational finding"],
        )

    monkeypatch.setattr(design_subgraphs, "validate_api_spec_model", observe)

    with caplog.at_level("WARNING", logger=design_subgraphs.__name__):
        findings = design_subgraphs.API_SPEC_SPEC.check(model, state)
    expected = api_spec_findings(model, state)

    assert len(observed) == 1
    assert isinstance(observed[0][0], ApiSpecModel)
    assert isinstance(observed[0][1], BCEModel)
    assert isinstance(observed[0][2], SequenceCollection)
    assert findings == expected
    assert all(item.rule_id != "api.typed-contract" for item in findings)
    record = next(
        item
        for item in caplog.records
        if item.message == "typed API validation reported observational results"
    )
    assert record.api_typed_validation == {
        "valid": False,
        "errors": ["browseCourses: stricter observational finding"],
    }
