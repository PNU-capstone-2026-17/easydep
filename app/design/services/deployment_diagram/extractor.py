"""Ask the LLM only for a source-grounded WorkloadGraph candidate."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.design.services.common.structured import parse_structured


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
    def deployment_node_ids_are_globally_unique(self) -> "WorkloadGraphProposal":
        """Reject ambiguous placement inputs before they reach the planner."""

        identifiers = [
            item.id for item in [*self.workloads, *self.externalDependencies]
        ]
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
DeploymentModel = WorkloadGraphProposal


WORKLOAD_GRAPH_EXTRACTION_SYSTEM_PROMPT = """
You identify deployable workloads for EasyDep's Docker-on-VM planner. Return
only a WorkloadGraph candidate. You do not choose VMs, VM groups, disks, public
IPs, load balancers, NAT gateways, cloud firewall resources, or providers.

Grounding and boundaries:
- Use structured requirement, capability, use-case, BCE class, sequence, API, and
  ERD artifacts. Put precise artifact element references in every sourceRefs list.
- Explicit deploymentPlanningFacts are deployment-only source artifacts. Apply
  accepted workloadContract and connectionContract values exactly; do not copy
  them into application classes, API operations, or logical ERD entities.
- A private connection does not imply resource isolation or separate compute.
  Add isolation only when an accepted fact explicitly contains that constraint.
- One generated application may contain many BCE classes. Never split workloads
  from class count, Entity classes, actors, supporting actors, or ERD tables.
- The application described by the API, BCE Controls, and use-case implementation
  is a generatedApplication. A requirement that this application be packaged as
  a container does not make it a prebuiltImage.
- Actors and browsers are not workloads. Systems EasyDep will not build are
  externalDependencies only when an artifact explicitly identifies them.
- An ERD proves a logical persistent-data need. It does NOT select PostgreSQL, a
  database container, an engine, a disk, a port, or a mount path. Do not create a
  DB workload or storage from the ERD alone.
- A prebuiltImage workload is legal only when the inputs explicitly select its
  image, engine, container deployment mode, and runtimeCatalogRef
  docker-on-vm/prebuilt-image. Never choose these values yourself.
- API endpoints justify an HTTP interface on the generated application, but do
  not prove public Internet exposure. Use exposure=unknown unless a typed accepted
  capability or explicit requirement resolves public versus internal.
- Sequence messages may justify connections. Never guess their protocol or port.
- storage belongs under its owning workload. Include it only for explicitly
  selected persistent block storage; deletionPolicy and capacity require evidence.
- For a generated application, choose an absolute POSIX mountPath as part of the
  design contract whenever explicit persistent storage is selected. The generated
  source code will implement that path. Do not invent a mount path for a prebuilt
  image unless its explicit runtime catalog contract supplies it.
- Every generated application that calls another workload or external dependency
  needs one configuration entry with kind=endpointBinding, a stable id, an
  UPPER_SNAKE_CASE environment-variable name, connectionRef, and projection
  (url, host, or port). You may choose clear environment-variable names because
  they are design outputs consumed by implementation. Do not guess endpoint values.
- Secret inputs use kind=secretBinding and an UPPER_SNAKE_CASE environment-variable
  name. Never include a secret value or provider credential.
- Configuration ids and names must be unique within a workload. Ordinary
  non-secret configuration may use kind=value; include a value only when grounded.
- replicationSafety is interchangeable only with explicit safety evidence,
  singleton only with explicit singleton semantics, otherwise unknown.
- Copy typed replica, zone, replacement, colocate, separate, and isolation
  constraints. Do not translate vague prose into a structural constraint.

Populate exactly the response schema and include no markdown or prose.
"""


def extract_deployment_model(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
    api_spec: dict[str, Any],
    erd_puml: str,
    *,
    refined_requirements: Any = None,
    capability_contract: dict[str, Any] | None = None,
    resource_intake: dict[str, Any] | None = None,
    class_model: Any = None,
    sequence_model: Any = None,
    erd_model: Any = None,
    deployment_planning_facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not scenario_text:
        return {}
    structured = {
        "refinedRequirements": refined_requirements or [],
        "capabilityContract": capability_contract or {},
        "resourceIntake": resource_intake or {},
        "useCaseSpecification": scenario_text,
        "apiSpec": api_spec,
        "deploymentPlanningFacts": deployment_planning_facts or [],
    }
    # PlantUML is a deterministic projection of each structured model. Sending
    # both representations wastes context and can present stale, conflicting
    # evidence to the model. Keep PlantUML only as a legacy-input fallback.
    if class_model:
        structured["classModel"] = class_model
    else:
        structured["classDiagramPlantUML"] = class_diagram_puml
    if sequence_model:
        structured["sequenceModel"] = sequence_model
    else:
        structured["sequenceDiagramPlantUML"] = sequence_diagram_puml
    if erd_model:
        structured["erdModel"] = erd_model
    else:
        structured["erdPlantUML"] = erd_puml
    messages = [
        {"role": "system", "content": WORKLOAD_GRAPH_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(structured, ensure_ascii=False, indent=2),
        },
    ]
    return parse_structured(messages, WorkloadGraphProposal)
