"""동적 기능 검사의 작은 실행 계획 계약이다."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class FunctionalInputValue(BaseModel):
    """한 HTTP 입력에서 OpenAPI만으로 정할 수 없어 따로 제안받은 값이다.

    테스트 계획과 입력값을 분리하면 LLM이 경로, 메서드, 본문 전체를 다시 만들지 않아도
    된다. ``location``은 ``path.orderId``나 ``body.customer.email``처럼 실제 입력의
    위치를 가리키므로, 수리 후 같은 테스트를 다시 실행할 때도 정확히 같은 값을 쓴다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    value: JsonValue

    @field_validator("operation_id", "location")
    @classmethod
    def _strip_input_identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("입력 식별자는 비어 있을 수 없습니다.")
        return cleaned


class FunctionalTestStep(BaseModel):
    """한 HTTP 호출을 가리키는 계획의 순서 있는 한 단계다.

    배열 순서는 검증된 use-case 순서를 나타내고 ``step_id``는 실행 증거에서 호출을
    식별한다. 동일 operation의 반복 호출은 입력별 의미를 표현할 계약이 없으므로 허용하지 않는다.
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
        operation_ids = [step.operation_id for step in self.steps]
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("operation_id cannot be repeated within one functional case.")
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
    "FunctionalInputValue",
    "FunctionalTestCase",
    "FunctionalTestPlan",
    "FunctionalTestStep",
]
