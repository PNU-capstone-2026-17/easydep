"""API 호환 facade가 기존 정규화·수정·OpenAPI shape를 보존하는지 검사한다."""

from app.design.services.api_spec import reviser
from app.design.services.api_spec.extractor import normalize_api_spec_model
from app.design.services.api_spec.openapi import build_openapi_from_model


def test_api_model_fills_control_traceability_and_explicit_body_fields() -> None:
    model = {
        "Endpoints": [
            {
                "path": "/sessions",
                "method": "post",
                "request_schema": "SignInRequest",
                "source_classes": [],
                "control_binding": {
                    "control": "SignInController",
                    "method": "createSession",
                    "arguments": [
                        {"name": "studentId", "source": "$body.username"}
                    ],
                },
            }
        ],
        "Schemas": [{"name": "SignInRequest", "fields": []}],
    }

    normalized = normalize_api_spec_model(model)

    endpoint = normalized["Endpoints"][0]
    assert endpoint["source_classes"] == ["SignInController"]
    assert normalized["Schemas"][0]["fields"][0]["name"] == "username"


def test_api_model_splits_qualified_control_target_when_contract_is_exact() -> None:
    model = {
        "Endpoints": [{
            "path": "/login",
            "method": "post",
            "control_binding": {
                "control": "AuthenticationController.authenticate",
                "method": "post",
                "arguments": [],
            },
        }],
        "Schemas": [],
    }
    class_diagram = """@startuml
class AuthenticationController <<Control>> {
  + authenticate(username : String, password : String): AuthenticationToken
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)
    binding = normalized["Endpoints"][0]["control_binding"]

    assert binding["control"] == "AuthenticationController"
    assert binding["method"] == "authenticate"
    assert normalized["Endpoints"][0]["source_classes"] == [
        "AuthenticationController"
    ]


def test_api_model_does_not_invent_body_fields_for_whole_body_binding() -> None:
    model = {
        "Endpoints": [{
            "request_schema": "CourseCreateRequest",
            "control_binding": {
                "control": "CourseController",
                "method": "storeCourse",
                "arguments": [{"name": "course", "source": "$body"}],
            },
        }],
        "Schemas": [{"name": "CourseCreateRequest", "fields": []}],
    }

    normalized = normalize_api_spec_model(model)

    assert normalized["Schemas"][0]["fields"] == []


def test_api_model_declares_query_value_explicitly_bound_to_control() -> None:
    model = {
        "Endpoints": [{
            "path": "/courses/{courseId}/enrollment",
            "method": "delete",
            "path_params": [{"name": "courseId", "type": "string", "required": True}],
            "query_params": [],
            "control_binding": {
                "control": "DropCourseController",
                "method": "removeEnrollment",
                "arguments": [
                    {"name": "studentId", "source": "$query.studentId"},
                    {"name": "courseId", "source": "$path.courseId"},
                ],
            },
        }],
        "Schemas": [],
    }
    class_diagram = """@startuml
class DropCourseController <<Control>> {
  + removeEnrollment(studentId : String, courseId : String): void
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)

    assert normalized["Endpoints"][0]["query_params"] == [{
        "name": "studentId", "type": "string", "required": True, "description": "",
    }]


def test_api_model_preserves_explicit_query_filter_contract_type() -> None:
    model = {
        "Endpoints": [{
            "path": "/courses",
            "method": "get",
            "query_params": [],
            "control_binding": {
                "control": "CatalogController",
                "method": "searchCatalog",
                "arguments": [{"name": "filter", "source": "$query.filter"}],
            },
        }],
        "Schemas": [],
    }
    class_diagram = """@startuml
class CatalogController <<Control>> {
  + searchCatalog(filter : CourseFilter): List<Course>
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)

    assert normalized["Endpoints"][0]["query_params"] == [{
        "name": "filter", "type": "CourseFilter", "required": True, "description": "",
    }]


def test_void_control_does_not_rewrite_documented_http_response() -> None:
    model = {
        "Endpoints": [{
            "path": "/enrollments/{sectionId}",
            "responses": [{"status": 200, "schema_name": "Enrollment"}],
            "control_binding": {
                "control": "DropController",
                "method": "dropSection",
                "outcomes": [{"status": 200, "outcome": "dropped"}],
            },
        }],
    }
    class_diagram = """@startuml
class DropController <<Control>> {
  + dropSection(sectionId : String): void
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)

    response = normalized["Endpoints"][0]["responses"][0]
    assert response == {"status": 200, "schema_name": "Enrollment"}
    assert normalized["Endpoints"][0]["control_binding"]["outcomes"] == [
        {"status": 200, "outcome": "dropped"}
    ]


def test_api_revision_preserves_documented_response_when_control_is_void(monkeypatch) -> None:
    model = {
        "Endpoints": [{
            "path": "/enrollments/{sectionId}",
            "responses": [{"status": 200, "schema_name": "Enrollment"}],
            "control_binding": {
                "control": "DropController",
                "method": "dropSection",
                "outcomes": [{"status": 200, "outcome": "dropped"}],
            },
        }],
        "Schemas": [],
    }
    class_diagram = """@startuml
class DropController <<Control>> {
  + dropSection(sectionId : String): void
}
@enduml"""
    monkeypatch.setattr(reviser, "parse_structured", lambda *_args: model)

    revised = reviser.revise_api_spec_model(
        model,
        "Use the current model.",
        class_diagram_puml=class_diagram,
    )

    assert revised["Endpoints"][0]["responses"][0]["status"] == 200
    assert revised["Endpoints"][0]["control_binding"]["outcomes"][0]["status"] == 200


def test_body_field_types_follow_exact_control_parameter_types() -> None:
    model = {
        "Endpoints": [{
            "request_schema": "TermRequest",
            "control_binding": {
                "control": "TermController",
                "method": "saveTerm",
                "arguments": [
                    {"name": "year", "source": "$body.year"},
                    {"name": "openDate", "source": "$body.openDate"},
                ],
            },
        }],
        "Schemas": [{
            "name": "TermRequest",
            "fields": [
                {"name": "year", "type": "string"},
                {"name": "openDate", "type": "string"},
            ],
        }],
    }
    class_diagram = """@startuml
class TermController <<Control>> {
  + saveTerm(year : int, openDate : java.time.LocalDate): AcademicTerm
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)
    fields = {
        field["name"]: field["type"]
        for field in normalized["Schemas"][0]["fields"]
    }

    assert fields == {"year": "integer", "openDate": "string"}


def test_body_collection_field_is_normalized_to_json_array() -> None:
    model = {
        "Endpoints": [{
            "request_schema": "ScheduleFormatRequest",
            "control_binding": {
                "control": "ScheduleController",
                "method": "formatSchedule",
                "arguments": [
                    {"name": "schedule", "source": "$body.schedule"},
                    {"name": "format", "source": "$body.format"},
                ],
            },
        }],
        "Schemas": [{
            "name": "ScheduleFormatRequest",
            "fields": [
                {"name": "schedule", "type": "string"},
                {"name": "format", "type": "string"},
            ],
        }],
    }
    class_diagram = """@startuml
class ScheduleController <<Control>> {
  + formatSchedule(schedule : array<Enrollment>, format : String): String
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)

    assert normalized["Schemas"][0]["fields"][0]["type"] == "array"


def test_success_response_missing_schema_uses_exact_collection_return_contract() -> None:
    model = {
        "Endpoints": [{
            "path": "/catalog/criteria",
            "method": "get",
            "responses": [{"status": 200, "description": "Browsing criteria"}],
            "control_binding": {
                "control": "BrowseCatalogControl",
                "method": "fetchBrowsingCriteria",
                "arguments": [],
            },
        }],
        "Schemas": [],
    }
    class_diagram = """@startuml
class BrowseCatalogControl <<Control>> {
  + fetchBrowsingCriteria(): List<String>
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)
    response = normalized["Endpoints"][0]["responses"][0]

    assert response["schema_name"] == "string"
    assert response["is_array"] is True
    openapi = build_openapi_from_model(normalized)
    assert openapi["paths"]["/catalog/criteria"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"] == {
        "type": "array", "items": {"type": "string"}
    }


def test_success_response_primitive_return_replaces_invented_object_schema() -> None:
    model = {
        "Endpoints": [{
            "path": "/auth/signin",
            "method": "post",
            "responses": [{"status": 200, "schema_name": "AuthToken"}],
            "control_binding": {
                "control": "SignInControl",
                "method": "authenticate",
                "arguments": [],
            },
        }],
        "Schemas": [{"name": "AuthToken", "fields": []}],
    }
    class_diagram = """@startuml
class SignInControl <<Control>> {
  + authenticate(username : String, password : String): String
}
@enduml"""

    normalized = normalize_api_spec_model(model, class_diagram)

    assert normalized["Endpoints"][0]["responses"][0]["schema_name"] == "string"
    assert normalized["Endpoints"][0]["responses"][0]["is_array"] is False
