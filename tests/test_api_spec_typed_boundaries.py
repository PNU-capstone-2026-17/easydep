"""API typed 경계가 저장·OpenAPI·하류 소비 계약을 보존하는지 검사한다."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.design.contracts.api_spec import ApiSpecModel, ApiSpecProposal
from app.design.graphs import subgraphs as design_subgraphs
from app.design.knowledge.detectors import (
    api_executable_schema_fields,
    api_spec_findings,
)
from app.design.rtm import build_design_rtm
from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec import service
from app.design.services.api_spec.normalization import (
    normalize_api_spec_model,
    normalize_stored_api_spec_model,
)
from app.design.services.api_spec.projection import build_openapi_from_model
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
            "Collaborations": [{
                "collaborationId": "UC1",
                "useCaseIds": ["UC1"],
                "entryActor": "Student",
                "calls": [
                    {
                        "callId": "UC1::call:1",
                        "receiverOperationId": (
                            "CatalogBoundary::browseCatalog(filter:CourseFilter)"
                        ),
                        "stepRefs": ["UC1:main:1"],
                    },
                    {
                        "callId": "UC1::call:2",
                        "parentCallId": "UC1::call:1",
                        "receiverOperationId": (
                            "CatalogControl::searchCatalog(filter:CourseFilter)"
                        ),
                        "stepRefs": ["UC1:main:1", "UC1:main:2"],
                    },
                ],
            }],
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


def _proposal() -> ApiSpecProposal:
    return ApiSpecProposal.model_validate(
        {
            "title": "Catalog API",
            "version": "1.0.0",
            "Endpoints": [
                {
                    "interaction_id": (
                        "CatalogBoundary::browseCatalog(filter:CourseFilter) -> "
                        "CatalogControl::searchCatalog(filter:CourseFilter)"
                    ),
                    "path": "/courses",
                    "method": "get",
                    "summary": "Browse courses",
                    "operation_id": "browseCourses",
                    "query_params": [
                        {"name": "filter", "type": "CourseFilter"}
                    ],
                    "responses": [
                        {"status": 200, "description": "Matching courses"}
                    ],
                }
            ],
            "Schemas": [
                {
                    "name": "Course",
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
    assert proposal.Endpoints[0].query_params[0].type == "CourseFilter"


def test_bce_enumeration_is_an_executable_openapi_schema() -> None:
    payload = _bce_model().model_dump(by_alias=True)
    for class_payload in payload["Classes"][:2]:
        class_payload["operations"][0]["returnType"] = "ValidationOutcome"
    payload["DataTypes"].append({
        "name": "ValidationOutcome",
        "kind": "enumeration",
        "fields": [],
        "values": ["VALID", "REJECTED"],
    })

    normalized = normalize_api_spec_model(
        _proposal(), BCEModel.model_validate(payload)
    )
    openapi = build_openapi_from_model(normalized)
    model_payload = normalized.model_dump()

    enum_schema = next(
        schema for schema in normalized.Schemas
        if schema.name == "ValidationOutcome"
    )
    assert enum_schema.values == ["VALID", "REJECTED"]
    assert openapi["components"]["schemas"]["ValidationOutcome"] == {
        "type": "string",
        "enum": ["VALID", "REJECTED"],
    }
    assert api_executable_schema_fields(model_payload, {}) == []


def test_void_control_canonicalizes_success_response_to_no_content() -> None:
    """A void BCE operation must not leave an unrepairable 200 body contract."""

    bce_payload = _bce_model().model_dump(by_alias=True)
    for class_payload in bce_payload["Classes"][:2]:
        class_payload["operations"][0]["returnType"] = "void"
    bce_model = BCEModel.model_validate(bce_payload)

    normalized = normalize_api_spec_model(_proposal(), bce_model)

    endpoint = normalized.Endpoints[0]
    response = endpoint.responses[0]
    assert response.status == 204
    assert response.schema_name == ""
    assert response.is_array is False
    assert endpoint.control_binding is not None
    assert [outcome.model_dump() for outcome in endpoint.control_binding.outcomes] == [
        {"status": 204, "outcome": "completed"}
    ]

    openapi = build_openapi_from_model(normalized)
    assert openapi["paths"]["/courses"]["get"]["responses"] == {
        "204": {"description": "Completed successfully with no response body."}
    }
    findings = api_spec_findings(
        normalized.model_dump(),
        {
            "extracted_bce_classes": bce_model.model_dump(by_alias=True),
            "sequence_diagram_model": _sequence_model().model_dump(),
        },
    )
    assert not [
        finding
        for finding in findings
        if finding.rule_id == "api.control-outcomes-cover-responses"
    ]


def test_body_field_keeps_the_custom_control_parameter_type() -> None:
    """enum이나 값 객체를 요청 본문에서 일반 문자열로 바꾸지 않는다."""

    payload = _proposal().model_dump()
    endpoint = payload["Endpoints"][0]
    endpoint["method"] = "post"
    endpoint["query_params"] = []
    endpoint["request_schema"] = "CatalogSearchRequest"

    normalized = normalize_api_spec_model(
        ApiSpecProposal.model_validate(payload), _bce_model(),
    )

    request = next(
        schema for schema in normalized.Schemas
        if schema.name == "CatalogSearchRequest"
    )
    assert request.fields[0].type == "CourseFilter"


def test_openapi_component_ref_is_canonicalized_before_body_binding() -> None:
    """렌더링된 component ref도 BCE DTO body parameter에 연결된다."""

    payload = _proposal().model_dump()
    endpoint = payload["Endpoints"][0]
    endpoint["method"] = "post"
    endpoint["query_params"] = []
    endpoint["request_schema"] = "#/components/schemas/CourseFilter"
    payload["Schemas"].append(
        {
            "name": "#/components/schemas/CourseFilter",
            "fields": [{"name": "keyword", "type": "string"}],
        }
    )

    normalized = normalize_api_spec_model(
        ApiSpecProposal.model_validate(payload), _bce_model(),
    )

    endpoint = normalized.Endpoints[0]
    assert endpoint.request_schema == "CourseFilter"
    assert endpoint.control_binding is not None
    assert [argument.model_dump() for argument in endpoint.control_binding.arguments] == [
        {"name": "filter", "source": "$body"}
    ]
    assert [schema.name for schema in normalized.Schemas].count("CourseFilter") == 1
    openapi = build_openapi_from_model(normalized)
    assert openapi["paths"]["/courses"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/CourseFilter"}


def test_stored_api_model_rebuilds_legacy_body_binding() -> None:
    """과거 checkpoint의 빈 binding도 현재 BCE 계약으로 복구된다."""

    payload = _proposal().model_dump()
    endpoint = payload["Endpoints"][0]
    endpoint["method"] = "post"
    endpoint["query_params"] = []
    endpoint["request_schema"] = "#/components/schemas/CourseFilter"
    endpoint["control_binding"] = {
        "control": "CatalogControl",
        "method": "searchCatalog",
        "arguments": [],
        "outcomes": [{"status": 200, "outcome": "ok"}],
    }
    payload["Schemas"].append(
        {
            "name": "#/components/schemas/CourseFilter",
            "fields": [{"name": "keyword", "type": "string"}],
        }
    )

    restored = normalize_stored_api_spec_model(
        ApiSpecModel.model_validate(payload),
        _bce_model(),
    )

    endpoint = restored.Endpoints[0]
    assert endpoint.request_schema == "CourseFilter"
    assert endpoint.control_binding is not None
    assert [argument.model_dump() for argument in endpoint.control_binding.arguments] == [
        {"name": "filter", "source": "$body"}
    ]


def test_generation_service_accepts_typed_inputs_and_returns_normalized_model(
    monkeypatch,
) -> None:
    calls: list[type[ApiSpecProposal]] = []

    def propose(_messages, schema):
        calls.append(schema)
        return _proposal().model_dump()

    monkeypatch.setattr(service, "parse_structured", propose)

    result = service.generate_api_spec_model(
        "UC1: A student browses the catalog.", _bce_model()
    )

    assert calls == [ApiSpecProposal]
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
    )

    assert revised is current


def test_revision_service_uses_one_structured_call_and_returns_typed_model(
    monkeypatch,
) -> None:
    current = normalize_api_spec_model(_proposal(), _bce_model())
    calls: list[type[ApiSpecProposal]] = []

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
        {"browseCourses"},
    )

    assert calls == [ApiSpecProposal]
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
            "schema": {"$ref": "#/components/schemas/CourseFilter"},
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
        "outcomes": {"200": "ok"},
    }
    assert operation["x-easydep-use-case-ids"] == ["UC1"]
    validate_openapi(openapi)


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
    captured: list[BCEModel] = []

    def generate(
        _scenario_text: str,
        bce_model: BCEModel,
    ) -> ApiSpecModel:
        captured.append(bce_model)
        return normalize_api_spec_model(_proposal(), bce_model)

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
    assert isinstance(captured[0], BCEModel)
    assert stored == normalize_api_spec_model(_proposal(), _bce_model()).model_dump()

    with pytest.raises(ValidationError):
        design_subgraphs.API_SPEC_SPEC.extract(
            {**state, "extracted_bce_classes": {"Classes": "invalid"}}
        )
    with pytest.raises(ValidationError):
        design_subgraphs.API_SPEC_SPEC.render({"Endpoints": "invalid"})
