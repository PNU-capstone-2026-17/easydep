"""LLM이 한정된 선택 공간에서 반환하는 일시적 제안 계약이다.

``BCEModel``과 ``SequenceCollection``만 저장한다. 이 작은 계약들은 LLM의 결정 하나를
유한한 후보로 제한하며 repair telemetry나 호환성 필드를 포함하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.design.schemas.class_model import ClassParameter


class Proposal(BaseModel):
    """설명되지 않은 LLM 필드를 거부하는 모든 일시 제안의 기반 계약이다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InventoryField(Proposal):
    """inventory item이 선언하는 이름과 타입 하나다."""
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)


class InventoryItem(Proposal):
    """BCE class 또는 구조 DataType 후보 하나다."""
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]*$")
    kind: Literal["Boundary", "Control", "Entity", "valueObject", "enumeration"]
    description: str = ""
    fields: list[InventoryField]
    identifier: list[str]
    values: list[str]
    use_case_ids: list[str] = Field(alias="useCaseIds")

    @field_validator("use_case_ids")
    @classmethod
    def unique_use_case_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values))

class InventoryRelationship(Proposal):
    """inventory 안의 유한 구조 관계 후보다. 호출 dependency는 허용하지 않는다."""
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: Literal["Association", "Aggregation", "Composition", "Inheritance"]
    source_multiplicity: str = Field(alias="sourceMultiplicity", min_length=1)
    target_multiplicity: str = Field(alias="targetMultiplicity", min_length=1)
    description: str = ""


class InventoryProposal(Proposal):
    """구조 LLM이 반환하는 전체 교체 inventory다."""
    items: list[InventoryItem] = Field(min_length=1)
    Relationships: list[InventoryRelationship]

    @model_validator(mode="after")
    def names_are_unique(self) -> InventoryProposal:
        names = [item.name for item in self.items]
        if len(names) != len(set(names)):
            raise ValueError("inventory names must be unique")
        return self


StepRef = Annotated[
    str,
    Field(pattern=r"^[^:\s]+:(?:main:[^:\s]+|extension:[^:\s]+:[^:\s]+)$"),
]


class OperationProposal(Proposal):
    """한 클래스에 추가할 method contract와 그 근거 step 목록이다."""
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: list[ClassParameter] = Field(default_factory=list)
    return_type: str = Field(alias="returnType", min_length=1)
    step_refs: list[StepRef] = Field(alias="stepRefs", min_length=1)

    @field_validator("step_refs")
    @classmethod
    def unique_step_refs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values))

    @model_validator(mode="after")
    def parameter_names_are_unique(self) -> OperationProposal:
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("operation parameter names must be unique")
        return self


class ClassOperations(Proposal):
    """한 operation fragment에서 같은 owner class에 속하는 method 묶음이다."""
    class_name: str = Field(alias="className", min_length=1)
    operations: list[OperationProposal] = Field(min_length=1)

    @model_validator(mode="after")
    def operation_names_are_unique(self) -> ClassOperations:
        names = [operation.name for operation in self.operations]
        if len(names) != len(set(names)):
            raise ValueError("operation names must be unique within a class fragment")
        return self


class FragmentDataType(Proposal):
    """한 유스케이스 operation signature가 소유하는 지역 DTO 또는 enum이다."""
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]*$")
    kind: Literal["valueObject", "enumeration"]
    fields: list[InventoryField]
    values: list[str]


class OperationFragment(Proposal):
    """operation LLM이 한 유스케이스에 대해 반환하는 전체 교체 조각이다."""
    DataTypes: list[FragmentDataType]
    Classes: list[ClassOperations] = Field(min_length=1)

    @model_validator(mode="after")
    def class_sets_are_unique(self) -> OperationFragment:
        names = [item.class_name for item in self.Classes]
        if len(names) != len(set(names)):
            raise ValueError("a fragment may contain each class once")
        type_names = [item.name for item in self.DataTypes]
        if len(type_names) != len(set(type_names)):
            raise ValueError("a fragment may declare each DataType once")
        return self


class ProposedCall(Proposal):
    """호출 operation과 앞선 부모 위치만 담는 최소 LLM 결정이다.

    call ID, stepRefs, argument binding은 승인 모델에서 결정론적으로 계산한다.
    """
    receiver_operation_id: str = Field(alias="receiverOperationId", min_length=1)
    parent_call_index: int | None = Field(default=None, alias="parentCallIndex", ge=1)


class CallPlanProposal(Proposal):
    """한 execution group의 순서 있는 최소 호출 계획이다."""
    calls: list[ProposedCall] = Field(min_length=1)


class FeedbackScope(Proposal):
    """피드백을 한 단계 종류와 허용된 소유 ID 목록에 배정한 결과다."""
    kind: Literal["inventory", "operation", "collaboration"]
    ids: list[str] = Field(default_factory=list)



