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
