from __future__ import annotations

import pytest

from app.design.contracts.application_runtime import (
    application_security_required,
    application_security_source_refs,
)


@pytest.mark.parametrize(
    "text",
    [
        "The system shall not require authentication for any request.",
        "The API must not need authentication.",
        "The endpoint does not require authentication.",
        "Authentication is not required.",
        "No authentication required.",
        "Authorization is not required.",
        "No authorization required.",
        "The system allows all requests without authentication.",
    ],
)
def test_explicit_no_auth_requirement_is_not_positive_security_evidence(
    text: str,
) -> None:
    requirements = [{"id": "R-NO-AUTH", "text": text}]

    assert application_security_source_refs({}, requirements) == []
    assert application_security_required({}, requirements) is False


def test_positive_auth_requirement_remains_security_evidence() -> None:
    requirements = [
        {
            "id": "R-AUTH",
            "text": "Authentication is required for protected operations.",
        }
    ]

    assert application_security_source_refs({}, requirements) == [
        "requirement:R-AUTH"
    ]
    assert application_security_required({}, requirements) is True


def test_prohibition_on_unauthenticated_access_remains_positive() -> None:
    requirements = [
        {
            "id": "R-PROTECTED",
            "text": "The system must not allow access without authentication.",
        }
    ]

    assert application_security_source_refs({}, requirements) == [
        "requirement:R-PROTECTED"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "시스템은 어떠한 요청에도 인증을 요구하지 않아야 한다.",
        "시스템은 어떠한 요청에도 인가를 요구하지 않아야 한다.",
        "시스템은 인증 없이 모든 요청을 허용한다.",
    ],
)
def test_korean_explicit_no_auth_requirement_is_not_positive(text: str) -> None:
    requirements = [
        {
            "id": "R-KO-NO-AUTH",
            "text": text,
        }
    ]

    assert application_security_source_refs({}, requirements) == []


def test_negative_requirement_does_not_override_openapi_security() -> None:
    api_spec = {
        "components": {
            "securitySchemes": {
                "basicAuth": {"type": "http", "scheme": "basic"}
            }
        }
    }
    requirements = [
        {"id": "R-PUBLIC", "text": "Authentication is not required."}
    ]

    assert application_security_source_refs(api_spec, requirements) == [
        "apiSpec:security"
    ]


def test_separate_negative_and_positive_requirements_keep_positive_ref() -> None:
    requirements = [
        {"id": "R-PUBLIC", "text": "No authentication required."},
        {
            "id": "R-ADMIN",
            "text": "Administrators must authenticate before changing settings.",
        },
    ]

    assert application_security_source_refs({}, requirements) == [
        "requirement:R-ADMIN"
    ]


def test_mixed_requirement_keeps_positive_authorization_clause() -> None:
    requirements = [
        {
            "id": "R-MIXED",
            "text": (
                "Authentication is not required for public reads, but "
                "authorization is required for administrative changes."
            ),
        }
    ]

    assert application_security_source_refs({}, requirements) == [
        "requirement:R-MIXED"
    ]


def test_korean_prohibition_on_unauthenticated_access_remains_positive() -> None:
    requirements = [
        {
            "id": "R-KO-PROTECTED",
            "text": "인증 없이 접근을 허용해서는 안 된다.",
        }
    ]

    assert application_security_source_refs({}, requirements) == [
        "requirement:R-KO-PROTECTED"
    ]
