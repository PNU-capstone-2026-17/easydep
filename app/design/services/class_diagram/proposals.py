"""Temporary proposal contracts returned from the LLM design decision space.

``BCEModel`` and ``SequenceCollection`` are the only persistence boundaries. Each
proposal contains one LLM decision and excludes repair telemetry and compatibility
fields.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.design.schemas.class_model import ClassParameter


class Proposal(BaseModel):
    """Base contract that rejects unrecognized LLM fields in every proposal."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InventoryField(Proposal):
    """One name-and-type field declared by an inventory item."""
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)


class InventoryItem(Proposal):
    """One candidate BCE class or structural data type."""
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]*$")
    kind: Literal["Boundary", "Control", "Entity", "valueObject", "enumeration"]
    description: str
    fields: list[InventoryField]
    identifier: list[str]
    values: list[str]
    use_case_ids: list[str] = Field(alias="useCaseIds")

    @field_validator("use_case_ids")
    @classmethod
    def unique_use_case_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values))

class InventoryRelationship(Proposal):
    """One finite structural relationship candidate; call dependencies are excluded."""
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: Literal["Association", "Aggregation", "Composition", "Inheritance"]
    source_multiplicity: str = Field(alias="sourceMultiplicity", min_length=1)
    target_multiplicity: str = Field(alias="targetMultiplicity", min_length=1)
    description: str


class InventoryProposal(Proposal):
    """Complete replacement inventory returned by the structural LLM."""
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
    """Method contract to add to one class and its supporting step references."""
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
    """Methods grouped by owner class in one operation fragment."""
    class_name: str = Field(alias="className", min_length=1)
    operations: list[OperationProposal] = Field(min_length=1)

    @model_validator(mode="after")
    def operation_names_are_unique(self) -> ClassOperations:
        names = [operation.name for operation in self.operations]
        if len(names) != len(set(names)):
            raise ValueError("operation names must be unique within a class fragment")
        return self


class FragmentDataType(Proposal):
    """Local DTO or enum owned by one use-case operation signature."""
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]*$")
    kind: Literal["valueObject", "enumeration"]
    fields: list[InventoryField]
    values: list[str]


class OperationFragment(Proposal):
    """Complete replacement fragment returned by the operation LLM for one use case."""
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
    """Minimal LLM decision containing a called operation and flat parent position.

    Call IDs, step references, and argument bindings are derived deterministically
    in the canonical model.
    """
    receiver_operation_id: str = Field(alias="receiverOperationId", min_length=1)
    # 루트도 null을 명시하게 한다. 필드를 선택 사항으로 두면 구조화 출력은 모든
    # 호출에서 이 값을 생략해도 schema를 통과하고, 뒤의 호출 관계 검사만 실패한다.
    parent_call_index: int | None = Field(
        alias="parentCallIndex",
        ge=1,
        description=(
            "One-based parent position in the complete flat calls array; "
            "null for a root. The position never restarts after another root."
        ),
    )


class CallPlanProposal(Proposal):
    """Minimal ordered call plan for the actor entries in one use case."""
    calls: list[ProposedCall]


class CombinedUnitCall(Proposal):
    """Call that selects an operation directly by its ``owner.method`` name."""

    operation_ref: str = Field(
        alias="operationRef",
        pattern=r"^[A-Z][A-Za-z0-9]*\.[A-Za-z_][A-Za-z0-9_]*$",
    )
    parent_call_index: int | None = Field(
        alias="parentCallIndex",
        ge=1,
        description=(
            "One-based parent position in the complete flat calls array; "
            "null for a root. The position never restarts after another root."
        ),
    )


class CombinedUnitProposal(Proposal):
    """Temporary contract containing an operation fragment and its call tree."""

    fragment: OperationFragment
    calls: list[CombinedUnitCall]


class FeedbackScope(Proposal):
    """Feedback assignment to one stage kind and its allowed owner IDs."""
    kind: Literal["inventory", "operation", "collaboration"]
    ids: list[str] = Field(default_factory=list)
