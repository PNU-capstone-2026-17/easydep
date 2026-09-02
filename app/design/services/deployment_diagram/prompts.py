"""WorkloadGraph 생성·수정용 LLM prompt literal을 소유한다."""
from __future__ import annotations

import json
from typing import Any


def generation_messages(structured_inputs: dict[str, Any]) -> list[dict[str, str]]:
    """구조화된 설계 입력을 기존 JSON envelope와 들여쓰기로 직렬화한다."""

    return [
        {"role": "system", "content": WORKLOAD_GRAPH_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(structured_inputs, ensure_ascii=False, indent=2),
        },
    ]

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
  database container, an engine, a port, or a mount path. Do not create a DB
  workload from the ERD alone. The deterministic planner may add its documented
  retained workload data disk only for one single-replica Docker-on-VM application.
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
- For an external dependency, declare either one URL endpointBinding or a
  host/port endpointBinding pair. Include its explicit address only when the
  inputs provide it; otherwise leave that address for user input. Each entry has
  a stable id, a unique UPPER_SNAKE_CASE environment-variable name,
  connectionRef, and projection. Internal workload and planned-resource
  endpoints are completed deterministically by the planner, so do not duplicate
  them or invent their values.
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

DEPLOYMENT_REVISION_SYSTEM_PROMPT = """
You edit an existing WorkloadGraph candidate. Apply the user's feedback and
return the complete graph.

- Change only WorkloadGraph. Never add or edit VMs, VM groups, cloud disks, load
  balancers, public IPs, NAT, firewall resources, or any CSP resource.
- Keep every workload, external dependency, connection, interface, storage item,
  configuration item, and typed constraint grounded with sourceRefs.
- Never create a database workload, engine, or image from an ERD alone. The
  deterministic planner may add its documented retained workload data disk only
  for one single-replica Docker-on-VM application.
- Actors and browsers are not workloads.
- Do not guess exposure, protocol, port, retention, replica safety, data execution
  mode, or prebuilt image details. Keep ambiguity for deterministic validation.
- Every connection endpoint and referenced interface must exist in the full graph.
- Keep endpoint bindings only for external dependencies. Internal workload and
  planned-resource endpoints are completed by the planner. Use stable ids and
  unique UPPER_SNAKE_CASE environment names; never guess external endpoint values.
- A generated application's persistent storage has a design-selected absolute
  POSIX mountPath. Secret bindings have environment names but never secret values.

Return the full graph according to the response schema. Include no markdown or
prose outside schema fields.
"""
