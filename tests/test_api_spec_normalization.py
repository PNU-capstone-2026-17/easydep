from app.design.services.api_spec.extractor import normalize_api_spec_model


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


def test_void_control_success_is_normalized_to_empty_http_response() -> None:
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
    assert response == {"status": 204, "schema_name": "", "is_array": False}
    assert normalized["Endpoints"][0]["control_binding"]["outcomes"] == [
        {"status": 204, "outcome": "dropped"}
    ]


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
