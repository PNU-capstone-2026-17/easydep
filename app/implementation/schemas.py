from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CreateImplementationJobRequest(BaseModel):
    base_package: str = "com.example.generated"
    # The original automated workflow generated explicitly reported placeholder
    # value types when a diagram referenced an otherwise undefined DTO/type.
    allow_assumptions: bool = True

    @field_validator("base_package")
    @classmethod
    def validate_base_package(cls, value: str) -> str:
        if not value or any(not part.isidentifier() for part in value.split(".")):
            raise ValueError("base_package must be a dotted Java package name")
        return value


class CreateImplementationFeedbackJobRequest(CreateImplementationJobRequest):
    feedback: str = Field(min_length=1, max_length=12000)

    @field_validator("feedback")
    @classmethod
    def validate_feedback(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("feedback must not be blank")
        return value


class ApprovalRequest(BaseModel):
    request_id: str = Field(min_length=64, max_length=64)
    approved: bool
    approved_by: str = Field(default="EasyDep user", max_length=200)
    retry_failed: bool = False
