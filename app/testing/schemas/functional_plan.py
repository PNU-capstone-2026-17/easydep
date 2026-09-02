"""동적 기능 검사의 작은 실행 계획 계약이다."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FunctionalTestStep(BaseModel):
    """한 HTTP 호출을 가리키는 계획의 순서 있는 한 단계다.

    같은 operationId를 여러 번 호출할 수 있다. 그래서 배열 순서만으로 단계를
    식별하지 않고, 사람이 읽을 수 있는 ``step_id``도 함께 보관한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)

    @field_validator("step_id", "operation_id")
    @classmethod
    def _strip_identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("식별자는 비어 있을 수 없습니다.")
        return cleaned


class FunctionalTestCase(BaseModel):
    """유스케이스 하나를 실행하기 위해 LLM이 고르는 최소 정보다.

    경로, HTTP method, 인증, 요청 값, 상태 코드는 OpenAPI와 executor가 이미 알고
    있으므로 이 계약에 넣지 않는다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)
    use_case_id: str = Field(min_length=1)
    steps: list[FunctionalTestStep] = Field(min_length=1)

    @field_validator("case_id", "use_case_id")
    @classmethod
    def _strip_scalar_identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("식별자는 비어 있을 수 없습니다.")
        return cleaned

    @field_validator("requirement_ids")
    @classmethod
    def _clean_requirement_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if len(cleaned) != len(values):
            raise ValueError("requirement_ids에는 비어 있지 않은 문자열만 넣을 수 있습니다.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("requirement_ids는 중복될 수 없습니다.")
        return cleaned

    @model_validator(mode="after")
    def _unique_step_ids(self) -> FunctionalTestCase:
        step_ids = [step.step_id for step in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("step_id는 계획 안에서 중복될 수 없습니다.")
        return self


class FunctionalTestPlan(BaseModel):
    """한 Testing 실행에서 보존·재실행할 유스케이스 계획 묶음이다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: list[FunctionalTestCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_case_ids(self) -> FunctionalTestPlan:
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case_id는 계획 묶음 안에서 중복될 수 없습니다.")
        return self


__all__ = [
    "FunctionalTestCase",
    "FunctionalTestPlan",
    "FunctionalTestStep",
]
