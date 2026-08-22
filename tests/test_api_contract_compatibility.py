from app.design.knowledge.detectors import _contract_types_compatible


def test_request_dto_is_compatible_with_domain_control_parameter() -> None:
    assert _contract_types_compatible("TermCreateRequest", "Term")
    assert _contract_types_compatible("EnrollmentRequest", "Enrollment")


def test_unrelated_api_types_are_not_compatible() -> None:
    assert not _contract_types_compatible("DepartmentCreateRequest", "Course")
    assert not _contract_types_compatible("CourseResponse", "Course")


def test_java_date_time_is_compatible_with_json_string() -> None:
    assert _contract_types_compatible("string", "java.time.LocalDate")
    assert _contract_types_compatible("string", "java.time.LocalDateTime")
