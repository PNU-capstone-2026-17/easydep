"""Small, factual operation contracts emitted alongside generated scaffolds."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.design.contracts.api_spec import ApiSpecModel
from app.design.schemas.class_model import BCEModel
from app.design.schemas.sequence_model import SequenceCollection

from ..planning.method_projection import project_method_calls
from .java_scaffold import java_method_name, java_type


class OperationParameterContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    type: str


class CollaboratorContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    owner: str
    owner_fqcn: str = Field(alias="ownerFqcn")
    method: str
    arguments: list[OperationParameterContract] = Field(default_factory=list)
    return_type: str = Field(alias="returnType")


class EndpointInputBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    source: str


class EndpointOutputBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: int
    outcome: str


class EndpointContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    operation_id: str = Field(alias="operationId")
    method: str
    path: str
    request_type: str | None = Field(default=None, alias="requestType")
    response_types: list[str] = Field(default_factory=list, alias="responseTypes")
    control: str | None = None
    control_method: str | None = Field(default=None, alias="controlMethod")
    input_bindings: list[EndpointInputBinding] = Field(default_factory=list, alias="inputBindings")
    output_bindings: list[EndpointOutputBinding] = Field(
        default_factory=list, alias="outputBindings"
    )


class GeneratedOperationContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    operation_id: str = Field(alias="operationId")
    stable_id: str | None = Field(default=None, alias="stableId")
    owner: str
    source: str
    writable_source: str | None = Field(default=None, alias="writableSource")
    signature: str
    parameters: list[OperationParameterContract] = Field(default_factory=list)
    return_type: str = Field(alias="returnType")
    constructor_dependencies: list[str] = Field(
        default_factory=list, alias="constructorDependencies"
    )
    collaborators: list[CollaboratorContract] = Field(default_factory=list)
    endpoints: list[EndpointContract] = Field(default_factory=list)
    completion_marker: str | None = Field(default=None, alias="completionMarker")


class GeneratedOperationContracts(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str = Field(
        default="easydep-generated-operation-contracts/v1", alias="schemaVersion"
    )
    contracts: list[GeneratedOperationContract]


def build_generated_operation_contracts(
    *,
    bce_model: BCEModel,
    sequence_model: SequenceCollection,
    api_model: ApiSpecModel,
    base_package: str,
    application_prefix: str = "application",
) -> GeneratedOperationContracts:
    """Build contracts only from typed design/projection inputs; omit unknown facts."""
    projection = project_method_calls(bce_model=bce_model, sequence_model=sequence_model)
    projected = {item.method.operation_id: item for item in projection.methods}
    endpoints_by_operation: dict[str, list[EndpointContract]] = {}
    for endpoint in api_model.Endpoints:
        binding = endpoint.control_binding
        if binding is None:
            continue
        operation_id = next(
            (
                item.operation_id
                for owner in bce_model.Classes
                if owner.class_name == binding.control
                for item in owner.operations
                if item.name == binding.method
            ),
            None,
        )
        if operation_id is None:
            continue
        endpoints_by_operation.setdefault(operation_id, []).append(
            EndpointContract(
                operationId=endpoint.operation_id,
                method=endpoint.method.upper(),
                path=endpoint.path,
                requestType=_api_fqcn(endpoint.request_schema, base_package),
                responseTypes=[
                    value
                    for value in (
                        _api_fqcn(item.schema_name, base_package) for item in endpoint.responses
                    )
                    if value is not None
                ],
                control=binding.control,
                controlMethod=binding.method,
                inputBindings=[
                    EndpointInputBinding.model_validate(item.model_dump())
                    for item in binding.arguments
                ],
                outputBindings=[
                    EndpointOutputBinding.model_validate(item.model_dump())
                    for item in binding.outcomes
                ],
            )
        )

    contracts: list[GeneratedOperationContract] = []
    declared = {item.class_name for item in bce_model.Classes} | {
        item.name for item in bce_model.DataTypes
    }
    for owner in sorted(bce_model.Classes, key=lambda item: item.class_name):
        source = _source_for(owner.class_name, base_package, application_prefix)
        writable_source = _writable_source_for(
            owner.stereotype, owner.class_name, base_package, application_prefix
        )
        for operation in owner.operations:
            params = [
                OperationParameterContract(
                    name=item.name,
                    type=_bce_fqcn(
                        java_type(item.type, declared_types=declared), base_package, declared
                    ),
                )
                for item in operation.parameters
            ]
            projected_method = projected.get(operation.operation_id)
            collaborators: list[CollaboratorContract] = []
            collaborator_keys: set[tuple[object, ...]] = set()
            dependencies: set[str] = set()
            if projected_method:
                for slice_ in projected_method.slices:
                    for call in slice_.outgoing:
                        if call.generation != "code" or call.target is None:
                            continue
                        target = call.target
                        owner_fqcn = _owner_fqcn(target.stereotype, target.class_name, base_package)
                        dependency_key = (
                            owner_fqcn,
                            java_method_name(target.name),
                            target.return_type,
                        )
                        dependencies.add(owner_fqcn)
                        if dependency_key in collaborator_keys:
                            continue
                        collaborator_keys.add(dependency_key)
                        collaborators.append(
                            CollaboratorContract(
                                owner=target.class_name,
                                ownerFqcn=_owner_fqcn(
                                    target.stereotype, target.class_name, base_package
                                ),
                                method=java_method_name(target.name),
                                arguments=[
                                    OperationParameterContract(
                                        name=item.parameter,
                                        type=_bce_fqcn(
                                            java_type(item.expected_type, declared_types=declared),
                                            base_package,
                                            declared,
                                        ),
                                    )
                                    for item in call.arguments
                                ],
                                returnType=_bce_fqcn(
                                    java_type(target.return_type, declared_types=declared),
                                    base_package,
                                    declared,
                                ),
                            )
                        )
            marker = f"EASYDEP-IMPLEMENT: complete {operation.stable_id or operation.operation_id}"
            rendered_void_body = bool(
                projected_method
                and projected_method.generation == "code"
                and operation.return_type == "void"
                and any(
                    call.generation == "code" and call.target is not None
                    for slice_ in projected_method.slices
                    for call in slice_.outgoing
                )
            )
            contracts.append(
                GeneratedOperationContract(
                    operationId=operation.operation_id,
                    stableId=operation.stable_id,
                    owner=owner.class_name,
                    source=source,
                    writableSource=writable_source,
                    signature=operation.method_signature(),
                    parameters=params,
                    returnType=_bce_fqcn(
                        java_type(operation.return_type, declared_types=declared),
                        base_package,
                        declared,
                    ),
                    constructorDependencies=(
                        sorted(dependencies) if owner.stereotype == "Control" else []
                    ),
                    collaborators=collaborators,
                    endpoints=endpoints_by_operation.get(operation.operation_id, []),
                    completionMarker=(
                        marker
                        if owner.stereotype == "Entity"
                        or (owner.stereotype == "Control" and not rendered_void_body)
                        else None
                    ),
                )
            )
    return GeneratedOperationContracts(contracts=contracts)


def write_generated_operation_contracts(root: Path, contracts: GeneratedOperationContracts) -> Path:
    path = root / "reports" / "generated-operation-contracts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contracts.model_dump(by_alias=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _owner_fqcn(stereotype: str, name: str, base_package: str) -> str:
    if stereotype == "Control":
        return f"{base_package}.bce.{name}"
    return f"{base_package}.bce.{name}"


_JAVA_FQCN = {
    "List": "java.util.List",
    "Set": "java.util.Set",
    "Collection": "java.util.Collection",
    "Iterable": "java.lang.Iterable",
    "Optional": "java.util.Optional",
    "UUID": "java.util.UUID",
    "BigDecimal": "java.math.BigDecimal",
    "BigInteger": "java.math.BigInteger",
    "Instant": "java.time.Instant",
    "LocalDate": "java.time.LocalDate",
    "LocalDateTime": "java.time.LocalDateTime",
    "LocalTime": "java.time.LocalTime",
    "OffsetDateTime": "java.time.OffsetDateTime",
    "ZonedDateTime": "java.time.ZonedDateTime",
    "String": "java.lang.String",
    "Integer": "java.lang.Integer",
    "Long": "java.lang.Long",
    "Boolean": "java.lang.Boolean",
}


def _bce_fqcn(value: str, base_package: str, declared: set[str]) -> str:
    return re.sub(
        r"\b[A-Za-z_$][A-Za-z0-9_$]*\b",
        lambda match: (
            f"{base_package}.bce.{match.group(0)}"
            if match.group(0) in declared
            else _JAVA_FQCN.get(match.group(0), match.group(0))
        ),
        value,
    )


def _api_fqcn(value: str, base_package: str) -> str | None:
    if not value:
        return None
    return f"{base_package}.api.model.{value.rsplit('.', 1)[-1]}"


def _source_for(name: str, base_package: str, application_prefix: str) -> str:
    package = base_package.replace(".", "/")
    return f"{application_prefix}/src/main/java/{package}/bce/{name}.java"


def _writable_source_for(
    stereotype: str, name: str, base_package: str, application_prefix: str
) -> str | None:
    package = base_package.replace(".", "/")
    if stereotype == "Boundary":
        return None
    if stereotype == "Control":
        return f"{application_prefix}/src/main/java/{package}/application/impl/{name}Service.java"
    return _source_for(name, base_package, application_prefix)
