"""클래스 설계 산출물의 엄격한 영속 계약.

연산은 클래스 수준 시그니처다. 협업은 그 시그니처의 구체적인 실행을 기록하며,
argument provenance와 호출 순서를 저장하는 유일한 위치다.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _parameter_value(parameter: object, name: str) -> str:
    if isinstance(parameter, dict):
        return str(parameter.get(name) or "")
    return str(getattr(parameter, name, "") or "")


def canonical_operation_id(
    class_name: str, operation_name: str, parameters: Sequence[object],
) -> str:
    """타입이 지정된 시그니처의 안정적인 연산 식별자를 반환한다."""

    signature = ",".join(
        f"{_parameter_value(parameter, 'name')}:{_parameter_value(parameter, 'type')}"
        for parameter in parameters
    )
    return f"{class_name}::{operation_name}({signature})"


def operation_method_signature(
    operation_name: str, parameters: Sequence[object], return_type: str,
) -> str:
    """UML 시그니처가 필요한 호출자를 위한 표시 투영을 반환한다."""

    arguments = ", ".join(
        f"{_parameter_value(parameter, 'name')} : {_parameter_value(parameter, 'type')}"
        for parameter in parameters
    )
    return f"{operation_name}({arguments}): {return_type}"


def canonical_call_id(collaboration_id: str, position: int) -> str:
    """1부터 시작하는 호출 위치로 결정론적 호출 식별자를 만든다."""

    return f"{collaboration_id}::call:{position}"


class ClassModelBase(BaseModel):
    """저장되는 클래스 모델 레코드가 공유하는 엄격한 설정이다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClassParameter(ClassModelBase):
    name: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)


class ClassOperation(ClassModelBase):
    operation_id: str = Field(alias="operationId", min_length=1)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: list[ClassParameter] = Field(default_factory=list)
    return_type: str = Field(default="void", alias="returnType", min_length=1)
    step_refs: list[str] = Field(default_factory=list, alias="stepRefs")

    @field_validator("step_refs")
    @classmethod
    def normalize_step_refs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values))

    def method_signature(self) -> str:
        return operation_method_signature(self.name, self.parameters, self.return_type)


class AcceptedBCEClass(ClassModelBase):
    class_name: str = Field(alias="className", min_length=1)
    stereotype: Literal["Boundary", "Control", "Entity"]
    description: str = ""
    fields: list[str] = Field(default_factory=list)
    use_case_ids: list[str] = Field(alias="use_case_ids", default_factory=list)
    identifier: list[str] = Field(default_factory=list)
    operations: list[ClassOperation] = Field(default_factory=list)

    @field_validator("class_name")
    @classmethod
    def reject_unknown_class_placeholder(cls, value: str) -> str:
        normalized = "".join(character for character in value.casefold() if character.isalnum())
        if normalized.startswith("unknownclass"):
            raise ValueError("className must identify a concrete BCE class")
        return value

    @model_validator(mode="after")
    def canonicalize_operations(self) -> AcceptedBCEClass:
        names = [operation.name for operation in self.operations]
        if len(names) != len(set(names)):
            raise ValueError("operation names must be unique within a class")
        for operation in self.operations:
            parameter_names = [parameter.name for parameter in operation.parameters]
            if len(parameter_names) != len(set(parameter_names)):
                raise ValueError("operation parameter names must be unique")
            operation.operation_id = canonical_operation_id(
                self.class_name, operation.name, operation.parameters
            )
        return self


class DataType(ClassModelBase):
    name: str = Field(min_length=1)
    kind: Literal["valueObject", "enumeration"]
    fields: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def reject_unknown_data_type(cls, value: str) -> str:
        normalized = "".join(character for character in value.casefold() if character.isalnum())
        if normalized.startswith("unknownclass"):
            raise ValueError("DataType name must be concrete")
        return value

    @model_validator(mode="after")
    def require_a_minimal_definition(self) -> DataType:
        if self.kind == "enumeration" and not self.values:
            raise ValueError("an enumeration needs values")
        if self.kind == "valueObject" and not self.fields:
            raise ValueError("a valueObject needs fields")
        if not self.fields and not self.values:
            raise ValueError("a DataType needs fields and/or values")
        return self


class AcceptedBCERelationship(ClassModelBase):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: str = "Association"
    source_multiplicity: str = Field(default="", alias="sourceMultiplicity")
    target_multiplicity: str = Field(default="", alias="targetMultiplicity")
    description: str = ""


class ArgumentBinding(ClassModelBase):
    parameter: str = Field(min_length=1)
    source_ref: str = Field(alias="sourceRef", min_length=1)


class CollaborationCall(ClassModelBase):
    call_id: str = Field(alias="callId", min_length=1)
    parent_call_id: str | None = Field(default=None, alias="parentCallId")
    receiver_operation_id: str = Field(alias="receiverOperationId", min_length=1)
    step_refs: list[str] = Field(default_factory=list, alias="stepRefs")
    argument_bindings: list[ArgumentBinding] = Field(
        default_factory=list, alias="argumentBindings"
    )

    @field_validator("step_refs")
    @classmethod
    def normalize_step_refs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values))


class Collaboration(ClassModelBase):
    collaboration_id: str = Field(alias="collaborationId", min_length=1)
    use_case_ids: list[str] = Field(alias="useCaseIds", min_length=1)
    entry_actor: str | None = Field(default=None, alias="entryActor")
    calls: list[CollaborationCall] = Field(default_factory=list)

    @model_validator(mode="after")
    def canonicalize_call_ids(self) -> Collaboration:
        for position, call in enumerate(self.calls, start=1):
            call.call_id = canonical_call_id(self.collaboration_id, position)
        return self


class BCEModel(ClassModelBase):
    Classes: list[AcceptedBCEClass] = Field(default_factory=list)
    DataTypes: list[DataType] = Field(default_factory=list)
    Relationships: list[AcceptedBCERelationship] = Field(default_factory=list)
    Collaborations: list[Collaboration] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_named_elements(self) -> BCEModel:
        names = [item.class_name for item in self.Classes]
        if len(names) != len(set(names)):
            raise ValueError("className values must be unique")
        data_type_names = [item.name for item in self.DataTypes]
        if len(data_type_names) != len(set(data_type_names)):
            raise ValueError("DataType names must be unique")
        if set(names) & set(data_type_names):
            raise ValueError("Class and DataType names must not overlap")
        collaboration_ids = [item.collaboration_id for item in self.Collaborations]
        if len(collaboration_ids) != len(set(collaboration_ids)):
            raise ValueError("collaborationId values must be unique")
        return self
