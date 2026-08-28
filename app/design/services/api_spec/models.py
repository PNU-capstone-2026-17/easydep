"""API 제안과 승인 endpoint가 공유하는 타입 계약이다.

LLM은 OpenAPI 대신 의도적으로 얕은 이 모델만 제안한다. 같은 Pydantic 계약을 제안
경계와 결정론적 정규화 뒤에 사용하며, 렌더된 OpenAPI는 진실의 원천이 아닌 투영이다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ApiSpecRecord(BaseModel):
    """기존 제안 schema의 Pydantic extra 수용 정책을 유지하는 공통 기반이다."""


class ApiField(ApiSpecRecord):
    name: str
    type: str = Field(default="string")
    required: bool = Field(default=True)
    description: str = Field(default="")


class ApiResponse(ApiSpecRecord):
    status: int = Field(default=200)
    description: str = Field(default="")
    schema_name: str = Field(default="")
    is_array: bool = Field(default=False)


class ApiControlArgument(ApiSpecRecord):
    """HTTP 요청 값 하나가 Control parameter로 흐르는 명시적 연결이다."""

    name: str
    source: str


class ApiControlOutcome(ApiSpecRecord):
    """문서화된 HTTP status 하나를 만드는 이름 있는 Control 결과다."""

    status: int
    outcome: str = Field(min_length=1)


class ApiControlBinding(ApiSpecRecord):
    """HTTP operation 하나의 뒤에 있는 실행 가능한 application 계약이다."""

    control: str
    method: str
    arguments: list[ApiControlArgument] = Field(default_factory=list)
    outcomes: list[ApiControlOutcome] = Field(default_factory=list)


class ApiEndpoint(ApiSpecRecord):
    path: str = Field(default="/")
    method: str = Field(default="get")
    summary: str = Field(default="")
    operation_id: str = Field(default="")
    path_params: list[ApiField] = Field(default_factory=list)
    query_params: list[ApiField] = Field(default_factory=list)
    request_schema: str = Field(default="")
    responses: list[ApiResponse] = Field(default_factory=list)
    source_classes: list[str] = Field(default_factory=list)
    use_case_ids: list[str] = Field(default_factory=list)
    control_binding: ApiControlBinding | None = None


class ApiSchema(ApiSpecRecord):
    name: str
    description: str = Field(default="")
    fields: list[ApiField] = Field(default_factory=list)
    source_class: str = Field(default="")


class ApiSpecModel(ApiSpecRecord):
    """``api_spec_model``에 저장되는 canonical endpoint 모델이다."""

    title: str = Field(default="API")
    version: str = Field(default="1.0.0")
    Endpoints: list[ApiEndpoint] = Field(default_factory=list)
    Schemas: list[ApiSchema] = Field(default_factory=list)
