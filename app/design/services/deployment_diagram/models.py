"""LLM 제안과 저장 JSON이 공유하는 typed WorkloadGraph 계약이다.

기존 Pydantic extra 수용 범위와 field alias·default를 바꾸지 않는다. canonical 이름은
WorkloadGraph이며 기존 schema/telemetry operation 이름을 위해 실제 class 이름
WorkloadGraphProposal과 DeploymentModel alias를 함께 유지한다.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class WorkloadArtifact(BaseModel):
    kind: Literal["generatedApplication", "prebuiltImage"]
    image: str | None = None
    engine: str | None = None
    deploymentMode: Literal["container"] | None = None
    runtimeCatalogRef: str | None = None


class WorkloadInterface(BaseModel):
    id: str
    name: str = ""
    protocol: str = ""
    exposure: Literal["public", "internal", "outbound", "unknown"] = "unknown"
    port: int | None = None
    healthPath: str | None = None
    sourceRefs: list[str] = Field(default_factory=list)


class WorkloadStorage(BaseModel):
    id: str
    persistence: Literal["persistent"] = "persistent"
    capacityGiB: float | None = None
    mountPath: str | None = None
    deletionPolicy: Literal["retain", "delete"] | None = None
    replicaSemantics: Literal["singleAttachment", "perReplica"] | None = None
    sourceRefs: list[str] = Field(default_factory=list)


class WorkloadConfiguration(BaseModel):
    id: str
    name: str
    kind: Literal["value", "secret", "secretBinding", "endpointBinding"] = "value"
    value: Any = None
    connectionRef: str | None = None
    projection: Literal["url", "host", "port"] | None = None
    sensitive: bool = False
    sourceRefs: list[str] = Field(default_factory=list)


class ResourceRequirements(BaseModel):
    minVCpu: float | None = None
    minMemoryGiB: float | None = None


class Workload(BaseModel):
    id: str
    name: str
    artifact: WorkloadArtifact
    interfaces: list[WorkloadInterface] = Field(default_factory=list)
    storage: list[WorkloadStorage] = Field(default_factory=list)
    configuration: list[WorkloadConfiguration] = Field(default_factory=list)
    resourceRequirements: ResourceRequirements = Field(default_factory=ResourceRequirements)
    replicationSafety: Literal["singleton", "interchangeable", "unknown"] = "unknown"
    sourceRefs: list[str] = Field(default_factory=list)


class ExternalDependency(BaseModel):
    id: str
    name: str
    interfaces: list[WorkloadInterface] = Field(default_factory=list)
    sourceRefs: list[str] = Field(default_factory=list)


class WorkloadConnection(BaseModel):
    id: str
    sourceRef: str
    targetRef: str
    protocol: str = ""
    sourceInterfaceRef: str = ""
    targetInterfaceRef: str = ""
    sourceRefs: list[str] = Field(default_factory=list)


class WorkloadConstraint(BaseModel):
    id: str
    kind: Literal[
        "replicaCount",
        "zoneSpread",
        "zonePlacement",
        "managedReplacement",
        "colocate",
        "separate",
        "isolate",
        "securityIsolation",
        "resourceIsolation",
    ]
    workloadRefs: list[str] = Field(default_factory=list)
    value: Any = None
    required: bool = True
    sourceRefs: list[str] = Field(default_factory=list)


class WorkloadGraphProposal(BaseModel):
    schemaVersion: Literal["easydep-workload-graph"] = "easydep-workload-graph"
    workloads: list[Workload] = Field(default_factory=list)
    externalDependencies: list[ExternalDependency] = Field(default_factory=list)
    connections: list[WorkloadConnection] = Field(default_factory=list)
    constraints: list[WorkloadConstraint] = Field(default_factory=list)
    derivations: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def deployment_node_ids_are_globally_unique(self) -> WorkloadGraphProposal:
        """planner에 들어가기 전에 workload와 외부 dependency의 중복 ID를 거부한다."""

        identifiers = [item.id for item in self.workloads]
        identifiers.extend(item.id for item in self.externalDependencies)
        duplicates = sorted(
            identifier for identifier in set(identifiers) if identifiers.count(identifier) > 1
        )
        if duplicates:
            raise ValueError(
                "workload and external dependency ids must be globally unique; "
                f"duplicates: {', '.join(duplicates)}"
            )
        return self


# Domain name used by the generic feedback reviser.
WorkloadGraph = WorkloadGraphProposal
DeploymentModel = WorkloadGraphProposal
