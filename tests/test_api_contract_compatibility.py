"""API와 BCE 타입 호환성을 공개 detector 결과로 검증한다."""

from app.design.knowledge.detectors import api_spec_findings


def _argument_findings(actual: str, expected: str) -> list[str]:
    """공개 API 검증 결과에서 Control 인자 타입 finding만 반환한다."""
    state = {
        "extracted_bce_classes": {
            "Classes": [{
                "className": "CatalogControl",
                "stereotype": "Control",
                "methods": [f"create(value : {expected}): void"],
            }]
        },
        "use_cases": [{"id": "UC1"}],
        "sequence_diagram_model": {"Diagrams": []},
    }
    model = {
        "Endpoints": [{
            "path": "/catalog",
            "method": "post",
            "operation_id": "createCatalog",
            "request_schema": actual,
            "responses": [{"status": 204, "description": "Created"}],
            "source_classes": ["CatalogControl"],
            "use_case_ids": ["UC1"],
            "control_binding": {
                "control": "CatalogControl",
                "method": "create",
                "arguments": [{"name": "value", "source": "$body"}],
                "outcomes": [{"status": 204, "outcome": "created"}],
            },
        }],
        "Schemas": [{"name": actual, "fields": []}],
    }
    return [
        finding.message
        for finding in api_spec_findings(model, state)
        if finding.rule_id == "api.control-arguments-match"
    ]


def test_request_dto_is_compatible_with_domain_control_parameter() -> None:
    assert _argument_findings("TermCreateRequest", "Term") == []
    assert _argument_findings("EnrollmentRequest", "Enrollment") == []


def test_unrelated_api_types_are_not_compatible() -> None:
    assert _argument_findings("DepartmentCreateRequest", "Course")
    assert _argument_findings("CourseResponse", "Course")


def test_java_date_time_is_compatible_with_json_string() -> None:
    assert _argument_findings("string", "java.time.LocalDate") == []
    assert _argument_findings("string", "java.time.LocalDateTime") == []
