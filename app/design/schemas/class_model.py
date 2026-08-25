"""Accepted, persisted contracts for class-diagram models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClassModelBase(BaseModel):
    """Common strict configuration for persisted class-model records."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClassParameter(ClassModelBase):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)


class InputBinding(ClassModelBase):
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    parameter: str = Field(min_length=1)
    source_ref: str = Field(alias="sourceRef", min_length=1)


class ClassOperation(ClassModelBase):
    operation_id: str = Field(alias="operationId", min_length=1)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: list[ClassParameter] = Field(default_factory=list)
    return_type: str = Field(default="void", alias="returnType", min_length=1)
    step_refs: list[str] = Field(default_factory=list, alias="stepRefs")
    actor_entry: bool = Field(default=False, alias="actorEntry")
    input_bindings: list[InputBinding] = Field(
        default_factory=list, alias="inputBindings"
    )

    def method_signature(self) -> str:
        """Render the legacy method string deterministically from this contract."""

        parameters = ", ".join(
            f"{parameter.name} : {parameter.type}" for parameter in self.parameters
        )
        return f"{self.name}({parameters}): {self.return_type}"


class AcceptedBCEClass(ClassModelBase):
    class_name: str = Field(alias="className", min_length=1)
    stereotype: Literal["Boundary", "Control", "Entity"]
    description: str = ""
    fields: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    use_case_ids: list[str] = Field(alias="use_case_ids", min_length=1)
    identifier: list[str] = Field(default_factory=list)
    operations: list[ClassOperation] = Field(default_factory=list)

    @field_validator("class_name")
    @classmethod
    def reject_unknown_class_placeholder(cls, value: str) -> str:
        normalized = "".join(value.split()).casefold()
        suffix = normalized.removeprefix("unknownclass")
        if normalized.startswith("unknownclass") and (not suffix or suffix.isdigit()):
            raise ValueError("className must identify a concrete BCE class")
        return value

    @model_validator(mode="after")
    def mirror_operations_to_methods(self) -> AcceptedBCEClass:
        """Keep existing method-string consumers aligned with operation contracts."""

        if self.operations:
            self.methods = [operation.method_signature() for operation in self.operations]
        return self


class AcceptedBCERelationship(ClassModelBase):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: str = "Association"
    source_multiplicity: str = Field(default="", alias="sourceMultiplicity")
    target_multiplicity: str = Field(default="", alias="targetMultiplicity")
    description: str = ""


class BCEModel(ClassModelBase):
    Classes: list[AcceptedBCEClass] = Field(default_factory=list)
    Relationships: list[AcceptedBCERelationship] = Field(default_factory=list)
