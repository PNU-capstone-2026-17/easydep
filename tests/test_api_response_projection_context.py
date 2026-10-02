"""Focused, behavior-preserving coverage for API response projection context."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.design.contracts.api_spec import ApiEndpoint, ApiResponse, ApiSpecModel, ApiSpecProposal
from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.normalization import (
    normalize_api_spec_model,
    response_projection_context,
)


def _course_model(*, include_optional: bool = False) -> BCEModel:
    offering_fields = [
        "offeringId : String", "course : Course", "term : AcademicTerm",
        "section : String", "instructor : Professor",
    ]
    offering_refs = [
        "classfield_offering_id", "classfield_course", "classfield_term",
        "classfield_section", "classfield_instructor",
    ]
    if include_optional:
        offering_fields.append("note : Optional<String>")
        offering_refs.append("classfield_note")
    return BCEModel.model_validate({
        "Classes": [
            {"className": "StudentBoundary", "stereotype": "Boundary", "use_case_ids": ["UC4"],
             "operations": [{"operationId": "StudentBoundary::view()", "stableId": "op_boundary",
                             "name": "view", "returnType": "RegistrationScheduleView", "stepRefs": ["UC4:main:1"]}]},
            {"className": "RegistrationQueryControl", "stereotype": "Control", "use_case_ids": ["UC4"],
             "operations": [{"operationId": "RegistrationQueryControl::view()", "stableId": "op_query",
                             "name": "view", "returnType": "RegistrationScheduleView", "stepRefs": ["UC4:main:2"]}]},
            {"className": "Registration", "stableId": "class_registration", "stereotype": "Entity", "use_case_ids": ["UC4"],
             "fields": ["registrationId : String", "offering : CourseOffering"],
             "fieldRefs": ["classfield_registration_id", "classfield_offering"]},
            {"className": "CourseOffering", "stableId": "class_offering", "stereotype": "Entity", "use_case_ids": ["UC4"],
             "fields": offering_fields, "fieldRefs": offering_refs},
            {"className": "Course", "stableId": "class_course", "stereotype": "Entity", "use_case_ids": ["UC4"],
             "fields": ["courseId : String"], "fieldRefs": ["classfield_course_id"]},
            {"className": "AcademicTerm", "stableId": "class_term", "stereotype": "Entity", "use_case_ids": ["UC4"],
             "fields": ["termId : String"], "fieldRefs": ["classfield_term_id"]},
            {"className": "Professor", "stableId": "class_professor", "stereotype": "Entity", "use_case_ids": ["UC4"],
             "fields": ["professorId : String"], "fieldRefs": ["classfield_professor_id"]},
        ],
        "DataTypes": [{"name": "RegistrationScheduleView", "stableId": "dtype_view", "kind": "valueObject",
                       "fields": ["registrationList : List<Registration>"], "fieldRefs": ["field_registration_list"]}],
        "Relationships": [],
        "Collaborations": [{"collaborationId": "UC4", "useCaseIds": ["UC4"], "calls": [
            {"callId": "UC4::call:1", "stableId": "call_boundary", "receiverOperationId": "StudentBoundary::view()"},
            {"callId": "UC4::call:2", "parentCallId": "UC4::call:1", "stableId": "call_control",
             "receiverOperationId": "RegistrationQueryControl::view()"},
        ]}],
    })


def _course_api() -> ApiSpecModel:
    return ApiSpecModel(Endpoints=[ApiEndpoint(
        interaction_id="StudentBoundary::view() -> RegistrationQueryControl::view()",
        operation_id="viewRegistrationsAndSchedule",
        responses=[ApiResponse(status=200, description="ok", schema_name="RegistrationScheduleView")],
    )])


def test_context_enumerates_exact_uc4_nested_stable_paths() -> None:
    rows = response_projection_context(_course_api(), _course_model())
    paths = {tuple(row["pathFieldRefs"]) for row in rows}
    assert ("field_registration_list", "classfield_offering", "classfield_course") in paths
    assert ("field_registration_list", "classfield_offering", "classfield_term") in paths
    assert ("field_registration_list", "classfield_offering", "classfield_section") in paths
    assert next(row for row in rows if row["pathFieldRefs"] == ["field_registration_list"])["rootControlOperationStableId"] == "op_query"


def test_context_omits_optional_fields_and_is_display_rename_stable() -> None:
    model = _course_model(include_optional=True)
    before = {tuple(row["pathFieldRefs"]) for row in response_projection_context(_course_api(), model)}
    assert not any("classfield_note" in path for path in before)
    payload = model.model_dump(by_alias=True)
    offering = next(item for item in payload["Classes"] if item["className"] == "CourseOffering")
    offering["fields"][1] = "renamedCourse : Course"
    after = {tuple(row["pathFieldRefs"]) for row in response_projection_context(_course_api(), BCEModel.model_validate(payload))}
    assert before == after


def test_context_requires_aligned_stable_refs_without_changing_api_model() -> None:
    model = _course_model()
    api = _course_api()
    before = deepcopy(api.model_dump())
    payload = model.model_dump(by_alias=True)
    offering = next(item for item in payload["Classes"] if item["className"] == "CourseOffering")
    offering["fieldRefs"] = []
    with pytest.raises(ValueError, match="aligned stable field refs"):
        response_projection_context(api, BCEModel.model_validate(payload))
    assert api.model_dump() == before


def test_context_does_not_change_existing_normalization_output() -> None:
    model = _course_model()
    proposal = ApiSpecProposal.model_validate({"Endpoints": [{
        "interaction_id": "StudentBoundary::view() -> RegistrationQueryControl::view()",
        "path": "/registrations", "method": "get", "summary": "View registrations",
        "responses": [{"status": 200, "description": "ok"}],
    }]})
    before = normalize_api_spec_model(proposal, model).model_dump()
    response_projection_context(_course_api(), model)
    after = normalize_api_spec_model(proposal, model).model_dump()
    assert after == before


def test_scalar_response_has_root_control_return_context() -> None:
    model = _course_model()
    api = ApiSpecModel(Endpoints=[ApiEndpoint(
        interaction_id="StudentBoundary::view() -> RegistrationQueryControl::view()",
        operation_id="scalar", responses=[ApiResponse(status=200, description="ok", schema_name="number")],
    )])
    assert response_projection_context(api, model) == [{
        "interactionId": "StudentBoundary::view() -> RegistrationQueryControl::view()",
        "operationId": "scalar", "status": 200, "responseSchema": "number",
        "rootControlOperationStableId": "op_query", "pathFieldRefs": [], "pathNames": [], "declaredType": "number",
    }]


def test_scalar_context_ignores_unreachable_malformed_declaration() -> None:
    payload = _course_model().model_dump(by_alias=True)
    payload["DataTypes"].append({
        "name": "UnusedMalformed", "stableId": "dtype_unused", "kind": "valueObject",
        "fields": ["value : String"], "fieldRefs": [],
    })
    model = BCEModel.model_validate(payload)
    api = ApiSpecModel(Endpoints=[ApiEndpoint(
        interaction_id="StudentBoundary::view() -> RegistrationQueryControl::view()",
        operation_id="scalar", responses=[ApiResponse(status=200, description="ok", schema_name="number")],
    )])
    assert response_projection_context(api, model)[0]["declaredType"] == "number"
