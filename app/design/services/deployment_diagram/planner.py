"""Deterministic WorkloadGraph -> DeploymentPlan -> ResourcePlan pipeline.

The workload graph is the only model an LLM may propose.  Compute placement and
provider resources are derived here from typed constraints and project policy.
This module intentionally contains no database-engine or application-family
defaults.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from app.design.services.deployment_diagram.provider_template import (
    build_complete_provider_template,
    provider_template_structure_digest,
    validate_complete_provider_template,
)

WORKLOAD_GRAPH_SCHEMA = "easydep-workload-graph"
DEPLOYMENT_PLAN_SCHEMA = "easydep-deployment-plan"
RESOURCE_PLAN_SCHEMA = "easydep-resource-plan"
RUNTIME_BINDING_SCHEMA = "easydep-runtime-binding"

SUPPORTED_PROVIDERS = frozenset({"aws", "azure", "gcp"})
SUPPORTED_PROTOCOLS = frozenset({"http", "tcp"})
SUPPORTED_PREBUILT_RUNTIME_CATALOG = frozenset({"docker-on-vm/prebuilt-image"})
BLOCKING_CLASSES = frozenset({"invalid", "unsupported", "needsInput", "unjustified"})
ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")

def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "item"


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _refs(value: Any) -> list[str]:
    return list(dict.fromkeys(str(item) for item in value or [] if str(item).strip()))


def _issue(
    field: str,
    reason: str,
    *,
    classification: str = "needsInput",
    source_refs: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "field": field,
        "classification": classification,
        "reason": reason,
        "sourceRefs": list(dict.fromkeys(str(item) for item in source_refs if item)),
    }


def _derivation(
    rule: str,
    decision: str,
    *,
    source_refs: Iterable[str] = (),
) -> dict[str, Any]:
    refs = list(dict.fromkeys(str(item) for item in source_refs if item))
    if not refs:
        refs = [f"project-policy:{rule}"]
    return {"rule": rule, "decision": decision, "sourceRefs": refs}


def extract_planning_facts(
    *,
    refined_requirements: Any = None,
    capability_contract: dict[str, Any] | None = None,
    resource_intake: dict[str, Any] | None = None,
    resource_spec: dict[str, Any] | None = None,
    usecase_spec: Any = None,
    class_model: Any = None,
    sequence_model: Any = None,
    api_spec: Any = None,
    erd_model: Any = None,
    artifact_versions: dict[str, Any] | None = None,
    additional_planning_facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize design inputs into auditable PlanningFact records.

    Facts retain values and provenance; natural-language statements are never
    copied into a ResourcePlan.  Capability ``needsQuestion`` decisions become
    blocking issues before workload planning starts.
    """

    artifacts = {
        "refinedRequirements": refined_requirements or [],
        "capabilityContract": capability_contract or {},
        "resourceIntake": resource_intake or {},
        "resourceSpec": resource_spec or {},
        "usecaseSpec": usecase_spec or {},
        "classModel": class_model or {},
        "sequenceModel": sequence_model or {},
        "apiSpec": api_spec or {},
        "erdModel": erd_model or {},
        "deploymentPlanningFacts": additional_planning_facts or [],
    }
    inputs = [
        {
            "artifact": name,
            "version": (artifact_versions or {}).get(name),
            "digest": _canonical_digest(value),
        }
        for name, value in artifacts.items()
    ]
    facts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    def add_fact(
        fact_id: str,
        kind: str,
        value: Any,
        source_refs: Iterable[str],
        rule: str,
        authority: str,
        status: str = "accepted",
    ) -> None:
        facts.append(
            {
                "id": fact_id,
                "kind": kind,
                "value": value,
                "sourceRefs": _refs(source_refs),
                "derivationRule": rule,
                "authority": authority,
                "status": status,
            }
        )

    spec = dict(resource_spec or {})
    for field in (
        "provider",
        "region",
        "candidateZones",
        "deploymentTargets",
        "monthlyBudgetUSD",
        "minVCpu",
        "minMemoryGiB",
        "trafficPattern",
        "scale",
        "dataResidency",
    ):
        if field in spec:
            add_fact(
                f"resource-{_slug(field)}",
                "planningContext",
                {"field": field, "value": copy.deepcopy(spec[field])},
                [f"resourceSpec:{field}"],
                "resource-spec-copy",
                "explicit",
            )

    provenance = list((resource_intake or {}).get("provenance") or [])
    for index, item in enumerate(provenance, start=1):
        if not isinstance(item, dict):
            continue
        add_fact(
            f"resource-provenance-{index}",
            "resourceProvenance",
            {"field": item.get("field"), "value": item.get("value")},
            [f"resourceIntake:provenance:{index}"],
            "resource-intake-provenance",
            "observed",
        )

    for index, capability in enumerate(
        (capability_contract or {}).get("capabilities") or [], start=1
    ):
        if not isinstance(capability, dict):
            continue
        decision = str(capability.get("decision") or "")
        source_refs = [
            *(f"requirement:{item}" for item in capability.get("requirementIds") or []),
            *(
                f"capability:{capability.get('id')}:evidence:{evidence_index}"
                for evidence_index, _span in enumerate(
                    capability.get("evidenceSpans") or [], start=1
                )
            ),
        ]
        add_fact(
            f"capability-{_slug(capability.get('id') or index)}",
            "capability",
            {
                "capabilityId": capability.get("id"),
                "necessity": capability.get("necessity"),
                "dependencyCapabilityIds": list(
                    capability.get("dependencyCapabilityIds") or []
                ),
                "typedConstraints": copy.deepcopy(
                    capability.get("typedConstraints") or []
                ),
            },
            source_refs,
            "accepted-capability-to-planning-candidate",
            "explicit" if capability.get("origin") == "explicit" else "derived",
            decision or "unknown",
        )
        if decision == "needsQuestion":
            issues.append(
                _issue(
                    f"capabilityContract.capabilities.{index - 1}",
                    "A capability decision still needs a user answer before deployment planning.",
                    source_refs=source_refs,
                )
            )

    api_paths = (api_spec or {}).get("paths") if isinstance(api_spec, dict) else None
    if api_paths:
        add_fact(
            "design-http-api",
            "applicationInterfaceCandidate",
            {"protocol": "http", "endpointCount": sum(len(v) for v in api_paths.values() if isinstance(v, dict))},
            ["apiSpec:paths"],
            "structured-api-interface",
            "derived",
        )

    erd_present = bool(erd_model)
    if isinstance(erd_model, str):
        erd_present = bool(erd_model.strip() and "@startuml" in erd_model)
    if erd_present:
        add_fact(
            "design-logical-data-model",
            "persistentDataRequirement",
            {"schemaMigrationRequired": True, "runtimeEngine": None},
            ["erdModel"],
            "erd-proves-logical-data-not-runtime",
            "derived",
        )

    for fact in additional_planning_facts or []:
        if not isinstance(fact, dict):
            continue
        facts.append(copy.deepcopy(fact))

    return {
        "schemaVersion": "easydep-planning-facts",
        "facts": facts,
        "inputArtifacts": inputs,
        "inputDigest": _canonical_digest(inputs),
        "issues": issues,
    }


def planning_context(resource_spec: dict[str, Any] | None) -> dict[str, Any]:
    spec = dict(resource_spec or {})
    targets = [dict(item) for item in spec.get("deploymentTargets") or [] if isinstance(item, dict)]
    return {
        "schemaVersion": "easydep-planning-context",
        "provider": spec.get("provider"),
        "region": spec.get("region"),
        "candidateZones": list(spec.get("candidateZones") or spec.get("selectedZones") or []),
        "deploymentTargets": targets,
        "monthlyBudgetUSD": spec.get("monthlyBudgetUSD"),
        "minVCpu": spec.get("minVCpu"),
        "minMemoryGiB": spec.get("minMemoryGiB"),
        "trafficPattern": spec.get("trafficPattern"),
        "scale": copy.deepcopy(spec.get("scale")),
        "dataResidency": spec.get("dataResidency"),
    }


def planning_inputs_stale(
    persisted_facts: dict[str, Any],
    *,
    refined_requirements: Any = None,
    capability_contract: dict[str, Any] | None = None,
    resource_intake: dict[str, Any] | None = None,
    resource_spec: dict[str, Any] | None = None,
    usecase_spec: Any = None,
    class_model: Any = None,
    sequence_model: Any = None,
    api_spec: Any = None,
    erd_model: Any = None,
    artifact_versions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare stored artifact versions/digests with current upstream inputs."""

    current = extract_planning_facts(
        refined_requirements=refined_requirements,
        capability_contract=capability_contract,
        resource_intake=resource_intake,
        resource_spec=resource_spec,
        usecase_spec=usecase_spec,
        class_model=class_model,
        sequence_model=sequence_model,
        api_spec=api_spec,
        erd_model=erd_model,
        artifact_versions=artifact_versions,
    )
    old_by_name = {
        str(item.get("artifact")): item
        for item in persisted_facts.get("inputArtifacts") or []
    }
    changed = [
        {
            "artifact": item.get("artifact"),
            "persistedVersion": (old_by_name.get(str(item.get("artifact"))) or {}).get(
                "version"
            ),
            "currentVersion": item.get("version"),
            "persistedDigest": (old_by_name.get(str(item.get("artifact"))) or {}).get(
                "digest"
            ),
            "currentDigest": item.get("digest"),
        }
        for item in current.get("inputArtifacts") or []
        if (old_by_name.get(str(item.get("artifact"))) or {}).get("digest")
        != item.get("digest")
        or (old_by_name.get(str(item.get("artifact"))) or {}).get("version")
        != item.get("version")
    ]
    return {"stale": bool(changed), "changedArtifacts": changed, "current": current}


def _constraint_value(constraint: dict[str, Any], default: Any = None) -> Any:
    value = constraint.get("value", default)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def validate_workload_graph(
    graph: dict[str, Any] | None,
    *,
    planning_facts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    model = graph or {}
    issues: list[dict[str, Any]] = [
        copy.deepcopy(item) for item in model.get("issues") or [] if isinstance(item, dict)
    ]
    if model.get("schemaVersion") != WORKLOAD_GRAPH_SCHEMA:
        issues.append(
            _issue(
                "schemaVersion",
                f"WorkloadGraph must use {WORKLOAD_GRAPH_SCHEMA}.",
                classification="invalid",
            )
        )

    workloads = [item for item in model.get("workloads") or [] if isinstance(item, dict)]
    dependencies = [
        item for item in model.get("externalDependencies") or [] if isinstance(item, dict)
    ]
    constraints = [item for item in model.get("constraints") or [] if isinstance(item, dict)]
    all_items = [*workloads, *dependencies]
    ids = [str(item.get("id") or "") for item in all_items]
    known = {item for item in ids if item}
    if any(not item for item in ids) or len(known) != len(ids):
        issues.append(
            _issue(
                "workloads",
                "Workload and external dependency ids must be non-empty and globally unique.",
                classification="invalid",
            )
        )
    if not workloads:
        issues.append(
            _issue(
                "workloads",
                "At least one explicitly justified deployable workload is required.",
            )
        )

    interface_ids: dict[str, set[str]] = {}
    storage_ids: set[str] = set()
    configuration_by_workload: dict[str, list[dict[str, Any]]] = {}
    for workload in workloads:
        workload_id = str(workload.get("id") or "")
        refs = _refs(workload.get("sourceRefs"))
        if not refs:
            issues.append(
                _issue(
                    f"workloads.{workload_id}.sourceRefs",
                    "Every proposed workload requires source references.",
                    classification="unjustified",
                )
            )
        artifact = workload.get("artifact") or {}
        artifact_kind = str(artifact.get("kind") or artifact.get("type") or "")
        if artifact_kind not in {"generatedApplication", "prebuiltImage"}:
            issues.append(
                _issue(
                    f"workloads.{workload_id}.artifact",
                    "Artifact must be generatedApplication or an explicitly selected prebuiltImage.",
                    classification="invalid",
                    source_refs=refs,
                )
            )
        if artifact_kind == "prebuiltImage":
            if not artifact.get("image") or not artifact.get("engine"):
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.artifact",
                        "A prebuilt image workload needs an explicit image and engine.",
                        source_refs=refs,
                    )
                )
            if artifact.get("deploymentMode") != "container" or artifact.get(
                "runtimeCatalogRef"
            ) not in SUPPORTED_PREBUILT_RUNTIME_CATALOG:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.artifact.runtimeCatalogRef",
                        "The selected prebuilt workload is not in the supported Docker-on-VM runtime catalog.",
                        classification="unsupported",
                        source_refs=refs,
                    )
                )

        safety = str(workload.get("replicationSafety") or "unknown")
        if safety not in {"singleton", "interchangeable", "unknown"}:
            issues.append(
                _issue(
                    f"workloads.{workload_id}.replicationSafety",
                    "replicationSafety must be singleton, interchangeable, or unknown.",
                    classification="invalid",
                    source_refs=refs,
                )
            )

        local_interfaces: set[str] = set()
        for interface in workload.get("interfaces") or []:
            interface_id = str(interface.get("id") or "")
            if not interface_id or interface_id in local_interfaces:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.interfaces",
                        "Interface ids must be non-empty and unique within a workload.",
                        classification="invalid",
                        source_refs=refs,
                    )
                )
                continue
            local_interfaces.add(interface_id)
            protocol = str(interface.get("protocol") or "").lower()
            if not protocol:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.interfaces.{interface_id}.protocol",
                        "A connection protocol cannot be guessed.",
                        source_refs=_refs(interface.get("sourceRefs")) or refs,
                    )
                )
            elif protocol not in SUPPORTED_PROTOCOLS:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.interfaces.{interface_id}.protocol",
                        f"Protocol {protocol} is outside the HTTP and internal TCP scope.",
                        classification="unsupported",
                        source_refs=_refs(interface.get("sourceRefs")) or refs,
                    )
                )
            exposure = str(interface.get("exposure") or "unknown")
            if exposure not in {"public", "internal", "outbound", "unknown"}:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.interfaces.{interface_id}.exposure",
                        "Interface exposure must be public, internal, outbound, or unknown.",
                        classification="invalid",
                    )
                )
            elif exposure == "unknown":
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.interfaces.{interface_id}.exposure",
                        "Whether this interface is publicly reachable is structurally ambiguous.",
                        source_refs=_refs(interface.get("sourceRefs")) or refs,
                    )
                )
            elif protocol == "tcp" and exposure != "internal":
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.interfaces.{interface_id}.exposure",
                        "TCP interfaces are supported only for internal workload communication.",
                        classification="unsupported",
                        source_refs=_refs(interface.get("sourceRefs")) or refs,
                    )
                )
        interface_ids[workload_id] = local_interfaces

        local_configuration_ids: set[str] = set()
        local_environment_names: set[str] = set()
        configuration_by_workload[workload_id] = list(
            workload.get("configuration") or []
        )
        for configuration in configuration_by_workload[workload_id]:
            configuration_id = str(configuration.get("id") or "")
            environment_name = str(configuration.get("name") or "")
            if not configuration_id or configuration_id in local_configuration_ids:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.configuration",
                        "Configuration ids must be non-empty and unique within a workload.",
                        classification="invalid",
                        source_refs=refs,
                    )
                )
            local_configuration_ids.add(configuration_id)
            if (
                not ENVIRONMENT_NAME.fullmatch(environment_name)
                or environment_name in local_environment_names
            ):
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.configuration.{configuration_id}.name",
                        "Configuration environment names must be unique UPPER_SNAKE_CASE identifiers.",
                        classification="invalid",
                        source_refs=_refs(configuration.get("sourceRefs")) or refs,
                    )
                )
            local_environment_names.add(environment_name)
            kind = str(configuration.get("kind") or "value")
            if kind == "endpointBinding":
                if not configuration.get("connectionRef"):
                    issues.append(
                        _issue(
                            f"workloads.{workload_id}.configuration.{configuration_id}.connectionRef",
                            "An endpoint binding must reference its workload connection.",
                            source_refs=_refs(configuration.get("sourceRefs")) or refs,
                        )
                    )
                if configuration.get("projection") not in {"url", "host", "port"}:
                    issues.append(
                        _issue(
                            f"workloads.{workload_id}.configuration.{configuration_id}.projection",
                            "An endpoint binding projection must be url, host, or port.",
                            classification="invalid",
                            source_refs=_refs(configuration.get("sourceRefs")) or refs,
                        )
                    )
            if kind in {"secret", "secretBinding"} and configuration.get("value") is not None:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.configuration.{configuration_id}.value",
                        "Secret values must never be stored in the deployment design.",
                        classification="invalid",
                        source_refs=_refs(configuration.get("sourceRefs")) or refs,
                    )
                )

        for storage in workload.get("storage") or []:
            storage_id = str(storage.get("id") or "")
            if not storage_id or storage_id in storage_ids:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.storage",
                        "Storage ids must be non-empty and globally unique.",
                        classification="invalid",
                        source_refs=refs,
                    )
                )
                continue
            storage_ids.add(storage_id)
            if storage.get("persistence") != "persistent":
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.storage.{storage_id}.persistence",
                        "Only explicit persistent block storage belongs in WorkloadGraph storage.",
                        classification="invalid",
                    )
                )
            capacity = storage.get("capacityGiB")
            if isinstance(capacity, bool) or not isinstance(capacity, (int, float)) or capacity <= 0:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.storage.{storage_id}.capacityGiB",
                        "Persistent block storage requires a positive capacityGiB.",
                    )
                )
            if storage.get("deletionPolicy") not in {"retain", "delete"}:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.storage.{storage_id}.deletionPolicy",
                        "Persistent storage deletion/retention policy must be explicit.",
                        source_refs=_refs(storage.get("sourceRefs")) or refs,
                    )
                )
            mount_path = storage.get("mountPath")
            artifact_kind = str((workload.get("artifact") or {}).get("kind") or "")
            if artifact_kind == "generatedApplication" and (
                not isinstance(mount_path, str) or not mount_path.startswith("/")
            ):
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.storage.{storage_id}.mountPath",
                        "Generated applications with storage require a design-selected absolute POSIX mount path.",
                        source_refs=_refs(storage.get("sourceRefs")) or refs,
                    )
                )

    for dependency in dependencies:
        dependency_id = str(dependency.get("id") or "")
        interface_ids[dependency_id] = {
            str(item.get("id") or "")
            for item in dependency.get("interfaces") or []
            if str(item.get("id") or "")
        }
        if not _refs(dependency.get("sourceRefs")):
            issues.append(
                _issue(
                    f"externalDependencies.{dependency_id}.sourceRefs",
                    "External dependencies must be explicitly grounded.",
                    classification="unjustified",
                )
            )

    connection_ids: set[str] = set()
    for connection in model.get("connections") or []:
        connection_id = str(connection.get("id") or "")
        if not connection_id or connection_id in connection_ids:
            issues.append(
                _issue(
                    "connections",
                    "Connection ids must be non-empty and unique.",
                    classification="invalid",
                )
            )
        connection_ids.add(connection_id)
        for field in ("sourceRef", "targetRef"):
            endpoint = str(connection.get(field) or "")
            if endpoint not in known:
                issues.append(
                    _issue(
                        f"connections.{connection_id}.{field}",
                        f"Connection endpoint does not exist: {endpoint or '<empty>'}.",
                        classification="invalid",
                    )
                )
        for endpoint_field, interface_field in (
            ("sourceRef", "sourceInterfaceRef"),
            ("targetRef", "targetInterfaceRef"),
        ):
            interface_ref = str(connection.get(interface_field) or "")
            endpoint = str(connection.get(endpoint_field) or "")
            if interface_ref and interface_ref not in interface_ids.get(endpoint, set()):
                issues.append(
                    _issue(
                        f"connections.{connection_id}.{interface_field}",
                        f"Connection interface does not exist on {endpoint}: {interface_ref}.",
                        classification="invalid",
                    )
                )
        protocol = str(connection.get("protocol") or "").lower()
        if not protocol:
            issues.append(
                _issue(
                    f"connections.{connection_id}.protocol",
                    "Connection protocol must be explicit.",
                    source_refs=_refs(connection.get("sourceRefs")),
                )
            )
        elif protocol not in SUPPORTED_PROTOCOLS:
            issues.append(
                _issue(
                    f"connections.{connection_id}.protocol",
                    f"Protocol {protocol} is outside the HTTP and internal TCP scope.",
                    classification="unsupported",
                    source_refs=_refs(connection.get("sourceRefs")),
                )
            )

    connections_by_id = {
        str(item.get("id") or ""): item for item in model.get("connections") or []
    }
    endpoint_bindings_by_connection: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for workload_id, configurations in configuration_by_workload.items():
        for configuration in configurations:
            if str(configuration.get("kind") or "") != "endpointBinding":
                continue
            connection_ref = str(configuration.get("connectionRef") or "")
            connection = connections_by_id.get(connection_ref)
            if connection is None:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.configuration.{configuration.get('id')}.connectionRef",
                        f"Endpoint binding references an unknown connection: {connection_ref or '<empty>'}.",
                        classification="invalid",
                    )
                )
                continue
            if str(connection.get("sourceRef") or "") != workload_id:
                issues.append(
                    _issue(
                        f"workloads.{workload_id}.configuration.{configuration.get('id')}.connectionRef",
                        "Endpoint bindings belong to the source workload of the referenced connection.",
                        classification="invalid",
                    )
                )
            endpoint_bindings_by_connection.setdefault(connection_ref, []).append(
                (workload_id, configuration)
            )
    generated_workloads = {
        str(item.get("id") or "")
        for item in workloads
        if str((item.get("artifact") or {}).get("kind") or "")
        == "generatedApplication"
    }
    for connection in model.get("connections") or []:
        connection_id = str(connection.get("id") or "")
        source_ref = str(connection.get("sourceRef") or "")
        bindings = endpoint_bindings_by_connection.get(connection_id, [])
        if source_ref in generated_workloads and len(bindings) != 1:
            issues.append(
                _issue(
                    f"connections.{connection_id}.endpointBinding",
                    "Each generated source workload connection requires exactly one endpoint environment binding.",
                    source_refs=_refs(connection.get("sourceRefs")),
                )
            )

    for constraint in constraints:
        constraint_id = str(constraint.get("id") or "")
        refs = _refs(constraint.get("sourceRefs"))
        if not refs:
            issues.append(
                _issue(
                    f"constraints.{constraint_id}.sourceRefs",
                    "Every non-policy constraint requires source references.",
                    classification="unjustified",
                )
            )
        for workload_ref in constraint.get("workloadRefs") or []:
            if str(workload_ref) not in {str(item.get("id") or "") for item in workloads}:
                issues.append(
                    _issue(
                        f"constraints.{constraint_id}.workloadRefs",
                        f"Constraint references an unknown workload: {workload_ref}.",
                        classification="invalid",
                        source_refs=refs,
                    )
                )

    facts = list((planning_facts or {}).get("facts") or [])
    has_logical_data = any(item.get("kind") == "persistentDataRequirement" for item in facts)
    data_runtime_explicit = bool(storage_ids) or any(
        str((item.get("artifact") or {}).get("kind") or "") == "prebuiltImage"
        for item in workloads
    )
    if has_logical_data and not data_runtime_explicit:
        issues.append(
            _issue(
                "dataExecutionMode",
                "The ERD proves persistent data but does not select a database engine, external service, or workload-owned storage model.",
                source_refs=["erdModel"],
            )
        )
    issues.extend(
        copy.deepcopy(item)
        for item in (planning_facts or {}).get("issues") or []
        if isinstance(item, dict)
    )
    # Avoid multiplying identical issues as feedback is reapplied.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in issues:
        key = (
            str(item.get("field") or ""),
            str(item.get("classification") or ""),
            str(item.get("reason") or ""),
        )
        unique[key] = item
    return list(unique.values())


def _upsert_by_id(items: list[dict[str, Any]], value: dict[str, Any]) -> None:
    item_id = str(value.get("id") or "")
    current = next((item for item in items if str(item.get("id") or "") == item_id), None)
    if current is None:
        items.append(value)
    else:
        current.update(value)


def _apply_explicit_contract_facts(
    graph: dict[str, Any], planning_facts: dict[str, Any] | None
) -> None:
    """Project accepted explicit contracts without asking the LLM to copy values."""

    workloads = graph.setdefault("workloads", [])
    connections = graph.setdefault("connections", [])
    constraints = graph.setdefault("constraints", [])
    workload_by_id = {
        str(item.get("id") or ""): item for item in workloads if isinstance(item, dict)
    }
    facts = [
        item
        for item in (planning_facts or {}).get("facts") or []
        if isinstance(item, dict)
        and item.get("authority") == "explicit"
        and item.get("status") == "accepted"
    ]
    allowed_constraint_kinds: dict[str, set[str]] = {}
    for fact in facts:
        authorized: set[str] = set()
        value = dict(fact.get("value") or {})
        if fact.get("kind") == "workloadContract" and value.get("replicaCount") is not None:
            authorized.add("replicaCount")
        if fact.get("kind") == "constraintContract" and value.get("kind"):
            authorized.add(str(value["kind"]))
        if fact.get("kind") == "capability":
            authorized.update(
                str(item.get("kind"))
                for item in value.get("typedConstraints") or []
                if isinstance(item, dict) and item.get("kind")
            )
        for reference in [str(fact.get("id") or ""), *_refs(fact.get("sourceRefs"))]:
            if reference:
                allowed_constraint_kinds.setdefault(reference, set()).update(authorized)
    kept_constraints: list[dict[str, Any]] = []
    globally_allowed_kinds = {
        kind for kinds in allowed_constraint_kinds.values() for kind in kinds
    }
    enforce_contract_constraints = any(
        fact.get("kind")
        in {"workloadContract", "connectionContract", "constraintContract"}
        for fact in facts
    )
    for constraint in constraints:
        refs = _refs(constraint.get("sourceRefs"))
        if enforce_contract_constraints and not refs:
            graph["derivations"].append(
                _derivation(
                    "reject-ungrounded-constraint",
                    f"Ignored {constraint.get('id') or constraint.get('kind')}; it has no source references.",
                )
            )
            continue
        kind = str(constraint.get("kind") or "")
        if enforce_contract_constraints and kind not in globally_allowed_kinds:
            graph["derivations"].append(
                _derivation(
                    "reject-unauthorized-constraint",
                    f"Ignored {constraint.get('id') or kind}; no accepted fact authorizes {kind}.",
                    source_refs=refs,
                )
            )
            continue
        matched = [reference for reference in refs if reference in allowed_constraint_kinds]
        if matched and not any(
            kind in allowed_constraint_kinds[reference] for reference in matched
        ):
            graph["derivations"].append(
                _derivation(
                    "reject-unauthorized-constraint",
                    f"Ignored {constraint.get('id') or kind}; its cited fact does not authorize {kind}.",
                    source_refs=refs,
                )
            )
            continue
        kept_constraints.append(constraint)
    constraints[:] = kept_constraints
    for fact in facts:
        if fact.get("kind") != "workloadContract":
            continue
        value = dict(fact.get("value") or {})
        workload_id = str(value.get("workloadId") or "")
        if not workload_id:
            continue
        refs = _refs(fact.get("sourceRefs"))
        workload = workload_by_id.get(workload_id)
        if workload is None:
            workload = {
                "id": workload_id,
                "name": workload_id,
                "artifact": {},
                "interfaces": [],
                "storage": [],
                "configuration": [],
                "resourceRequirements": {},
                "replicationSafety": "unknown",
                "sourceRefs": refs,
            }
            workloads.append(workload)
            workload_by_id[workload_id] = workload
        workload["sourceRefs"] = _refs([*workload.get("sourceRefs", []), *refs])
        artifact_kind = value.get("artifactKind")
        if artifact_kind:
            workload["artifact"] = {
                "kind": artifact_kind,
                **{
                    key: value[key]
                    for key in ("image", "engine", "deploymentMode", "runtimeCatalogRef")
                    if key in value
                },
            }
        interface = value.get("interface")
        if isinstance(interface, dict):
            protocol = str(interface.get("protocol") or "interface")
            interface_id = str(
                interface.get("id") or f"{workload_id}-{protocol}"
            )
            workload.setdefault("interfaces", [])[:] = [
                item
                for item in workload["interfaces"]
                if str(item.get("protocol") or "") != protocol
                or str(item.get("id") or "") == interface_id
            ]
            _upsert_by_id(
                workload["interfaces"],
                {
                    "id": interface_id,
                    **copy.deepcopy(interface),
                    "sourceRefs": refs,
                },
            )
        storage = value.get("storage")
        if isinstance(storage, dict):
            storage_id = str(storage.get("id") or f"{workload_id}-data")
            workload.setdefault("storage", [])[:] = [
                item
                for item in workload["storage"]
                if item.get("persistence") != "persistent"
                or str(item.get("id") or "") == storage_id
            ]
            _upsert_by_id(
                workload["storage"],
                {
                    "id": storage_id,
                    **copy.deepcopy(storage),
                    "sourceRefs": refs,
                },
            )
        if value.get("replicaCount") is not None:
            _upsert_by_id(
                constraints,
                {
                    "id": f"{workload_id}-replicas",
                    "kind": "replicaCount",
                    "workloadRefs": [workload_id],
                    "value": value["replicaCount"],
                    "required": True,
                    "sourceRefs": refs,
                },
            )

    for fact in facts:
        if fact.get("kind") != "connectionContract":
            continue
        value = dict(fact.get("value") or {})
        source_id = str(value.get("sourceWorkloadRef") or "")
        target_id = str(value.get("targetWorkloadRef") or "")
        if not source_id or not target_id:
            continue
        refs = _refs(fact.get("sourceRefs"))
        connection_id = str(
            value.get("connectionId")
            or f"{source_id}-to-{target_id}"
        )
        connections[:] = [
            item
            for item in connections
            if not (
                str(item.get("sourceRef") or "") == source_id
                and str(item.get("targetRef") or "") == target_id
                and str(item.get("id") or "") != connection_id
            )
        ]
        target = workload_by_id.get(target_id) or {}
        protocol = str(value.get("protocol") or "")
        target_interface = next(
            (
                item
                for item in target.get("interfaces") or []
                if str(item.get("protocol") or "") == protocol
            ),
            {},
        )
        _upsert_by_id(
            connections,
            {
                "id": connection_id,
                "sourceRef": source_id,
                "targetRef": target_id,
                "protocol": protocol,
                "sourceInterfaceRef": "",
                "targetInterfaceRef": str(target_interface.get("id") or ""),
                "sourceRefs": refs,
            },
        )
        source = workload_by_id.get(source_id)
        if source is None:
            continue
        configuration = source.setdefault("configuration", [])
        target_name = re.sub(r"[^A-Z0-9]+", "_", target_id.upper()).strip("_")
        if value.get("endpointBindingRequired"):
            _upsert_by_id(
                configuration,
                {
                    "id": f"{target_id}-endpoint",
                    "name": f"{target_name}_HOST",
                    "kind": "endpointBinding",
                    "connectionRef": connection_id,
                    "projection": "host",
                    "sensitive": False,
                    "sourceRefs": refs,
                },
            )
        if value.get("secretBindingRequired"):
            _upsert_by_id(
                configuration,
                {
                    "id": f"{target_id}-credential",
                    "name": f"{target_name}_PASSWORD",
                    "kind": "secretBinding",
                    "sensitive": True,
                    "sourceRefs": refs,
                },
            )
            _upsert_by_id(
                target.setdefault("configuration", []),
                {
                    "id": "runtime-credential",
                    "name": "POSTGRES_PASSWORD",
                    "kind": "secretBinding",
                    "sensitive": True,
                    "sourceRefs": refs,
                },
            )


def normalize_workload_graph(
    candidate: dict[str, Any] | None,
    *,
    planning_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph = copy.deepcopy(candidate or {})
    graph.setdefault("schemaVersion", WORKLOAD_GRAPH_SCHEMA)
    graph.setdefault("workloads", [])
    graph.setdefault("externalDependencies", [])
    graph.setdefault("connections", [])
    graph.setdefault("constraints", [])
    graph.setdefault("derivations", [])
    _apply_explicit_contract_facts(graph, planning_facts)
    for workload in graph.get("workloads") or []:
        for configuration in workload.get("configuration") or []:
            if not configuration.get("id") and configuration.get("name"):
                configuration["id"] = _slug(configuration.get("name"))
    existing_constraint_ids = {
        str(item.get("id") or "") for item in graph.get("constraints") or []
    }
    for fact in (planning_facts or {}).get("facts") or []:
        if fact.get("kind") != "capability" or fact.get("status") != "accepted":
            continue
        value = fact.get("value") or {}
        for index, constraint in enumerate(value.get("typedConstraints") or [], start=1):
            if not isinstance(constraint, dict) or not constraint.get("kind"):
                continue
            constraint_id = str(
                constraint.get("id") or f"{fact.get('id')}-constraint-{index}"
            )
            if constraint_id in existing_constraint_ids:
                continue
            graph["constraints"].append(
                {
                    **copy.deepcopy(constraint),
                    "id": constraint_id,
                    "sourceRefs": _refs(constraint.get("sourceRefs"))
                    or _refs(fact.get("sourceRefs")),
                }
            )
            existing_constraint_ids.add(constraint_id)
    graph["issues"] = validate_workload_graph(graph, planning_facts=planning_facts)
    if planning_facts:
        graph["inputArtifacts"] = copy.deepcopy(planning_facts.get("inputArtifacts") or [])
        graph["inputDigest"] = planning_facts.get("inputDigest")
    graph["structureDigest"] = workload_graph_structure_digest(graph)
    return graph


def _constraints_by_workload(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {
        str(item.get("id")): {
            "replicaCount": 1,
            "zones": [],
            "minimumZones": 1,
            "managedReplacement": False,
            "isolation": False,
            "colocate": set(),
            "sourceRefs": [],
        }
        for item in graph.get("workloads") or []
    }
    for constraint in graph.get("constraints") or []:
        kind = str(constraint.get("kind") or "")
        workload_refs = [str(item) for item in constraint.get("workloadRefs") or []]
        refs = _refs(constraint.get("sourceRefs"))
        for workload_ref in workload_refs:
            policy = policies.get(workload_ref)
            if policy is None:
                continue
            policy["sourceRefs"] = _refs([*policy["sourceRefs"], *refs])
            if kind == "replicaCount":
                value = _constraint_value(constraint, 1)
                if isinstance(value, int) and not isinstance(value, bool):
                    policy["replicaCount"] = value
            elif kind in {"zoneSpread", "zonePlacement"}:
                value = _constraint_value(constraint, [])
                if isinstance(value, list):
                    policy["zones"] = list(value)
                    policy["minimumZones"] = max(1, len(value))
                elif isinstance(value, dict):
                    policy["zones"] = list(value.get("zones") or [])
                    minimum = value.get("minimumZones") or value.get("count") or 1
                    if isinstance(minimum, int) and not isinstance(minimum, bool):
                        policy["minimumZones"] = minimum
                elif isinstance(value, int) and not isinstance(value, bool):
                    policy["minimumZones"] = value
            elif kind == "managedReplacement":
                policy["managedReplacement"] = bool(_constraint_value(constraint, True))
            elif kind in {"separate", "isolate", "securityIsolation", "resourceIsolation"}:
                policy["isolation"] = True
            elif kind == "colocate":
                policy["colocate"].update(workload_refs)
    return policies


def build_deployment_plan(
    graph: dict[str, Any], planning_context_value: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Place arbitrary workloads using the fixed policy rules."""

    context = copy.deepcopy(planning_context_value or {})
    issues = [copy.deepcopy(item) for item in graph.get("issues") or []]
    derivations = [copy.deepcopy(item) for item in graph.get("derivations") or []]
    policies = _constraints_by_workload(graph)
    workloads = list(graph.get("workloads") or [])
    by_id = {str(item.get("id")): item for item in workloads}

    candidate_zones = _refs(context.get("candidateZones"))
    for workload_id, policy in policies.items():
        minimum_zones = int(policy.get("minimumZones") or 1)
        if minimum_zones > 1 and not policy["zones"]:
            if len(candidate_zones) >= minimum_zones:
                policy["zones"] = candidate_zones[:minimum_zones]
                derivations.append(
                    _derivation(
                        "required-zone-count-from-candidates",
                        f"Selected {minimum_zones} candidate zones for {workload_id}.",
                        source_refs=policy["sourceRefs"],
                    )
                )
            else:
                issues.append(
                    _issue(
                        f"constraints.zoneSpread.{workload_id}",
                        f"At least {minimum_zones} candidate zones are required.",
                        source_refs=policy["sourceRefs"],
                    )
                )
        if policy["zones"] and len(set(policy["zones"])) < minimum_zones:
            issues.append(
                _issue(
                    f"constraints.zoneSpread.{workload_id}",
                    f"Zone selection does not satisfy minimumZones={minimum_zones}.",
                    classification="invalid",
                    source_refs=policy["sourceRefs"],
                )
            )
        if minimum_zones > int(policy["replicaCount"] or 1):
            issues.append(
                _issue(
                    f"constraints.zoneSpread.{workload_id}",
                    "Occupied zone count cannot exceed fixed replica count.",
                    classification="invalid",
                    source_refs=policy["sourceRefs"],
                )
            )

    for workload_id, policy in policies.items():
        count = int(policy["replicaCount"] or 1)
        if count < 1:
            issues.append(
                _issue(
                    f"constraints.replicaCount.{workload_id}",
                    "replicaCount must be at least one.",
                    classification="invalid",
                    source_refs=policy["sourceRefs"],
                )
            )
            policy["replicaCount"] = 1
            count = 1
        safety = str(by_id[workload_id].get("replicationSafety") or "unknown")
        if count > 1 and safety != "interchangeable":
            issues.append(
                _issue(
                    f"workloads.{workload_id}.replicationSafety",
                    "Multiple replicas require explicit interchangeable replication safety.",
                    source_refs=policy["sourceRefs"] or _refs(by_id[workload_id].get("sourceRefs")),
                )
            )
        if count > 1:
            for storage in by_id[workload_id].get("storage") or []:
                if storage.get("replicaSemantics") != "perReplica":
                    issues.append(
                        _issue(
                            f"workloads.{workload_id}.storage.{storage.get('id')}.replicaSemantics",
                            "Persistent block storage with multiple replicas requires explicit perReplica semantics; shared filesystems are out of scope.",
                            classification="unsupported",
                            source_refs=_refs(storage.get("sourceRefs")) or policy["sourceRefs"],
                        )
                    )

    for constraint in graph.get("constraints") or []:
        if str(constraint.get("kind") or "") != "colocate":
            continue
        refs = [str(item) for item in constraint.get("workloadRefs") or []]
        signatures = {
            (
                policies[item]["replicaCount"],
                tuple(sorted(policies[item]["zones"])),
                policies[item]["managedReplacement"],
                policies[item]["minimumZones"],
            )
            for item in refs
            if item in policies
        }
        if len(signatures) > 1:
            issues.append(
                _issue(
                    f"constraints.{constraint.get('id')}",
                    "Colocated workloads must have identical replica, zone, and managed-lifecycle policies.",
                    classification="invalid",
                    source_refs=_refs(constraint.get("sourceRefs")),
                )
            )

    # Compatible workloads share a compute unit.  A policy signature is the
    # structural lifecycle boundary; explicit isolation gets its own signature.
    groups: dict[tuple[Any, ...], list[str]] = {}
    for workload in workloads:
        workload_id = str(workload.get("id"))
        policy = policies[workload_id]
        zones = tuple(sorted(str(item) for item in policy["zones"] if str(item)))
        signature = (
            int(policy["replicaCount"]),
            zones,
            bool(policy["managedReplacement"]),
            int(policy["minimumZones"]),
            workload_id if policy["isolation"] else "shared",
        )
        groups.setdefault(signature, []).append(workload_id)

    # A separate constraint naming several workloads means pairwise separation,
    # even when their other lifecycle policies match.
    for constraint in graph.get("constraints") or []:
        if str(constraint.get("kind") or "") not in {
            "separate",
            "isolate",
            "securityIsolation",
            "resourceIsolation",
        }:
            continue
        refs = [str(item) for item in constraint.get("workloadRefs") or []]
        for workload_ref in refs:
            for signature, members in list(groups.items()):
                if workload_ref in members and len(members) > 1:
                    members.remove(workload_ref)
                    groups[(*signature, workload_ref)] = [workload_ref]

    compute_units: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []
    for index, (signature, workload_ids) in enumerate(groups.items(), start=1):
        replica_count = int(signature[0])
        zones = list(signature[1])
        if not zones and candidate_zones:
            zones = candidate_zones[:1]
        managed = replica_count > 1 or bool(signature[2])
        compute_id = f"compute-{index}"
        cpu_values = [
            (by_id[item].get("resourceRequirements") or {}).get("minVCpu")
            for item in workload_ids
        ]
        memory_values = [
            (by_id[item].get("resourceRequirements") or {}).get("minMemoryGiB")
            for item in workload_ids
        ]
        cpu_known = all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in cpu_values)
        memory_known = all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in memory_values)
        requirements: dict[str, Any] = {}
        if cpu_known:
            requirements["minVCpu"] = sum(cpu_values)
        if memory_known:
            requirements["minMemoryGiB"] = sum(memory_values)
        late_fields: list[str] = []
        if not (cpu_known and memory_known):
            late_fields.append("vmSku")
        compute_units.append(
            {
                "id": compute_id,
                "kind": "managedVmGroup" if managed else "standaloneVm",
                "replicaCount": replica_count,
                "zones": zones,
                "managedReplacement": managed,
                "resourceRequirements": requirements,
                "vmSku": {"binding": "late", "field": "vmSku"},
                "sourceRefs": _refs(
                    item
                    for workload_id in workload_ids
                    for item in (
                        policies[workload_id]["sourceRefs"]
                        or ["project-policy:default-colocation-and-single-replica"]
                    )
                ),
            }
        )
        for workload_id in workload_ids:
            placements.append(
                {
                    "id": f"placement-{_slug(workload_id)}",
                    "workloadRef": workload_id,
                    "computeUnitRef": compute_id,
                    "sourceRefs": policies[workload_id]["sourceRefs"]
                    or ["project-policy:default-colocation-and-single-replica"],
                }
            )
        rule = "managed-vm-group-selection" if managed else "standalone-vm-default"
        derivations.append(
            _derivation(
                rule,
                f"Placed {', '.join(workload_ids)} on {compute_id}.",
                source_refs=compute_units[-1]["sourceRefs"],
            )
        )

    placement_by_workload = {
        item["workloadRef"]: item["computeUnitRef"] for item in placements
    }
    compute_by_id = {item["id"]: item for item in compute_units}
    storage_bindings: list[dict[str, Any]] = []
    for workload in workloads:
        workload_id = str(workload.get("id"))
        for storage in workload.get("storage") or []:
            storage_bindings.append(
                {
                    "id": f"storage-binding-{_slug(storage.get('id'))}",
                    "workloadRef": workload_id,
                    "storageRef": storage.get("id"),
                    "computeUnitRef": placement_by_workload.get(workload_id),
                    "kind": "blockDisk",
                    "capacityGiB": storage.get("capacityGiB"),
                    "mountPath": storage.get("mountPath")
                    or {"binding": "late", "field": "mountPath"},
                    "deletionPolicy": storage.get("deletionPolicy"),
                    "replicaSemantics": storage.get("replicaSemantics", "singleAttachment"),
                    "sourceRefs": _refs(storage.get("sourceRefs"))
                    or _refs(workload.get("sourceRefs")),
                }
            )
            derivations.append(
                _derivation(
                    "persistent-storage-to-block-disk",
                    f"Created one block disk binding for {storage.get('id')}.",
                    source_refs=storage_bindings[-1]["sourceRefs"],
                )
            )

    network_paths: list[dict[str, Any]] = []
    public_compute: set[str] = set()
    for workload in workloads:
        workload_id = str(workload.get("id"))
        compute_ref = placement_by_workload.get(workload_id)
        for interface in workload.get("interfaces") or []:
            exposure = str(interface.get("exposure") or "unknown")
            if exposure != "public":
                continue
            public_compute.add(str(compute_ref))
            compute = compute_by_id.get(str(compute_ref), {})
            ingress_kind = (
                "loadBalancer" if compute.get("kind") == "managedVmGroup" else "directPublicIp"
            )
            network_paths.append(
                {
                    "id": f"public-{_slug(workload_id)}-{_slug(interface.get('id'))}",
                    "kind": "publicIngress",
                    "ingressKind": ingress_kind,
                    "targetWorkloadRef": workload_id,
                    "targetInterfaceRef": interface.get("id"),
                    "computeUnitRef": compute_ref,
                    "protocol": str(interface.get("protocol") or "").lower(),
                    "port": interface.get("port")
                    or {"binding": "late", "field": "containerPort"},
                    "sourceRefs": _refs(interface.get("sourceRefs"))
                    or _refs(workload.get("sourceRefs")),
                }
            )

    for connection in graph.get("connections") or []:
        source = str(connection.get("sourceRef") or "")
        target = str(connection.get("targetRef") or "")
        kind = "internal" if source in by_id and target in by_id else "outbound"
        target_interface = next(
            (
                item
                for item in (by_id.get(target) or {}).get("interfaces") or []
                if str(item.get("id") or "")
                == str(connection.get("targetInterfaceRef") or "")
            ),
            {},
        )
        network_paths.append(
            {
                "id": f"network-{_slug(connection.get('id'))}",
                "kind": kind,
                "connectionRef": connection.get("id"),
                "sourceRef": source,
                "targetRef": target,
                "protocol": str(connection.get("protocol") or "").lower(),
                "port": target_interface.get("port")
                or {"binding": "late", "field": "containerPort"},
                "sourceRefs": _refs(connection.get("sourceRefs")),
            }
        )

    for compute in compute_units:
        if compute["id"] not in public_compute or compute["kind"] == "managedVmGroup":
            network_paths.append(
                {
                    "id": f"egress-{compute['id']}",
                    "kind": "natEgress",
                    "computeUnitRef": compute["id"],
                    "purposes": ["registryPull", "externalHttp"],
                    "sourceRefs": ["project-policy:private-compute-nat-egress"],
                }
            )

    runtime_bindings: list[dict[str, Any]] = []
    connections_by_id = {
        str(item.get("id") or ""): item for item in graph.get("connections") or []
    }
    external_ids = {
        str(item.get("id") or "")
        for item in graph.get("externalDependencies") or []
    }
    for workload in workloads:
        workload_id = str(workload.get("id") or "")
        for storage in workload.get("storage") or []:
            runtime_bindings.append(
                {
                    "id": f"runtime-mount-{_slug(workload_id)}-{_slug(storage.get('id'))}",
                    "kind": "storageMount",
                    "workloadRef": workload_id,
                    "storageRef": storage.get("id"),
                    "mountPath": storage.get("mountPath"),
                    "sourceRefs": _refs(storage.get("sourceRefs"))
                    or _refs(workload.get("sourceRefs")),
                    "derivationRule": "workload-storage-mount-contract",
                }
            )
        for configuration in workload.get("configuration") or []:
            configuration_id = str(configuration.get("id") or "")
            kind = str(configuration.get("kind") or "value")
            if kind in {"secret", "secretBinding"}:
                runtime_bindings.append(
                    {
                        "id": f"runtime-secret-{_slug(workload_id)}-{_slug(configuration_id)}",
                        "kind": "secretEnvironment",
                        "workloadRef": workload_id,
                        "configurationRef": configuration_id,
                        "environmentName": configuration.get("name"),
                        "secretReference": {
                            "binding": "deployment",
                            "field": f"workloads.{workload_id}.configuration.{configuration_id}.secretRef",
                        },
                        "sourceRefs": _refs(configuration.get("sourceRefs"))
                        or _refs(workload.get("sourceRefs")),
                        "derivationRule": "secret-reference-to-runtime-environment",
                    }
                )
            elif kind == "endpointBinding":
                connection_ref = str(configuration.get("connectionRef") or "")
                connection = connections_by_id.get(connection_ref) or {}
                target_ref = str(connection.get("targetRef") or "")
                source_compute = placement_by_workload.get(workload_id)
                target_compute = placement_by_workload.get(target_ref)
                if target_ref in external_ids:
                    strategy = "externalInput"
                elif source_compute and source_compute == target_compute:
                    strategy = "containerDns"
                elif (compute_by_id.get(str(target_compute)) or {}).get("kind") == "managedVmGroup":
                    strategy = "internalLoadBalancer"
                else:
                    strategy = "staticPrivateIp"
                target_interface = next(
                    (
                        item
                        for item in (by_id.get(target_ref) or {}).get("interfaces") or []
                        if str(item.get("id") or "")
                        == str(connection.get("targetInterfaceRef") or "")
                    ),
                    {},
                )
                runtime_bindings.append(
                    {
                        "id": f"runtime-endpoint-{_slug(workload_id)}-{_slug(configuration_id)}",
                        "kind": "endpointEnvironment",
                        "workloadRef": workload_id,
                        "configurationRef": configuration_id,
                        "environmentName": configuration.get("name"),
                        "connectionRef": connection_ref,
                        "targetWorkloadRef": target_ref,
                        "targetComputeUnitRef": target_compute,
                        "targetInterfaceRef": connection.get("targetInterfaceRef"),
                        "strategy": strategy,
                        "projection": configuration.get("projection"),
                        "protocol": str(connection.get("protocol") or "").lower(),
                        "port": target_interface.get("port")
                        or {"binding": "late", "field": "containerPort"},
                        "sourceRefs": _refs(configuration.get("sourceRefs"))
                        or _refs(connection.get("sourceRefs")),
                        "derivationRule": f"endpoint-{_slug(strategy)}",
                    }
                )

    selected_zones = _refs(
        zone for compute in compute_units for zone in compute.get("zones") or []
    )
    late_bindings: list[dict[str, Any]] = []
    for compute in compute_units:
        late_bindings.append(
            {
                "id": f"late-vm-sku-{compute['id']}",
                "field": f"computeUnits.{compute['id']}.vmSku",
                "kind": "vmSku",
                "structural": False,
            }
        )
    for workload in workloads:
        for interface in workload.get("interfaces") or []:
            if not interface.get("port"):
                late_bindings.append(
                    {
                        "id": f"late-port-{_slug(workload.get('id'))}-{_slug(interface.get('id'))}",
                        "field": f"workloads.{workload.get('id')}.interfaces.{interface.get('id')}.port",
                        "kind": "containerPort",
                        "structural": False,
                    }
                )
        if (workload.get("artifact") or {}).get("kind") == "generatedApplication":
            late_bindings.append(
                {
                    "id": f"late-image-{_slug(workload.get('id'))}",
                    "field": f"workloads.{workload.get('id')}.artifact.imageDigest",
                    "kind": "imageDigest",
                    "structural": False,
                }
            )

    plan = {
        "schemaVersion": DEPLOYMENT_PLAN_SCHEMA,
        "workloadGraphDigest": graph.get("structureDigest")
        or workload_graph_structure_digest(graph),
        "computeUnits": compute_units,
        "placements": placements,
        "storageBindings": storage_bindings,
        "networkPaths": network_paths,
        "runtimeBindings": runtime_bindings,
        "locationPlan": {
            "region": context.get("region"),
            "zonePolicy": "explicit" if selected_zones else "providerSelectedSingleZone",
            "selectedZones": selected_zones,
            "candidateZones": _refs(context.get("candidateZones")),
            "singleRegion": True,
        },
        "lateBindings": late_bindings,
        "issues": issues,
        "derivations": derivations,
    }
    plan["structureDigest"] = deployment_plan_structure_digest(plan)
    return plan


def validate_deployment_plan(plan: dict[str, Any]) -> None:
    if plan.get("schemaVersion") != DEPLOYMENT_PLAN_SCHEMA:
        raise ValueError("unsupported DeploymentPlan schemaVersion")
    compute_ids = [str(item.get("id") or "") for item in plan.get("computeUnits") or []]
    if any(not item for item in compute_ids) or len(compute_ids) != len(set(compute_ids)):
        raise ValueError("DeploymentPlan compute unit ids must be non-empty and unique")
    known_compute = set(compute_ids)
    workload_refs: set[str] = set()
    for placement in plan.get("placements") or []:
        if str(placement.get("computeUnitRef") or "") not in known_compute:
            raise ValueError("DeploymentPlan placement has a dangling compute reference")
        workload_ref = str(placement.get("workloadRef") or "")
        if not workload_ref or workload_ref in workload_refs:
            raise ValueError("Each workload must have exactly one placement")
        workload_refs.add(workload_ref)
    for binding in plan.get("storageBindings") or []:
        if str(binding.get("computeUnitRef") or "") not in known_compute:
            raise ValueError("DeploymentPlan storage binding has a dangling compute reference")
        if str(binding.get("workloadRef") or "") not in workload_refs:
            raise ValueError("DeploymentPlan storage binding has a dangling workload reference")
    binding_ids: set[str] = set()
    for binding in plan.get("runtimeBindings") or []:
        binding_id = str(binding.get("id") or "")
        if not binding_id or binding_id in binding_ids:
            raise ValueError("DeploymentPlan runtime binding ids must be non-empty and unique")
        binding_ids.add(binding_id)
        if str(binding.get("workloadRef") or "") not in workload_refs:
            raise ValueError("DeploymentPlan runtime binding has a dangling workload reference")


def build_provider_resource_plan(
    deployment_plan: dict[str, Any],
    graph: dict[str, Any],
    *,
    provider: str,
    region: str,
) -> dict[str, Any]:
    """Project provider-neutral decisions to one complete CSP template."""

    validate_deployment_plan(deployment_plan)
    normalized_provider = str(provider or "").lower()
    if normalized_provider not in SUPPORTED_PROVIDERS or not region:
        issues = copy.deepcopy(deployment_plan.get("issues") or [])
        if normalized_provider not in SUPPORTED_PROVIDERS:
            issues.append(
                _issue(
                    "provider",
                    "Provider must be selected as aws, azure, or gcp.",
                    classification="needsInput",
                    source_refs=["project-policy:explicit-deployment-target"],
                )
            )
        if not region:
            issues.append(
                _issue(
                    "region",
                    "A provider region must be selected.",
                    classification="needsInput",
                    source_refs=["project-policy:explicit-deployment-target"],
                )
            )
        unresolved = {
            "schemaVersion": RESOURCE_PLAN_SCHEMA,
            "provider": normalized_provider,
            "region": str(region or ""),
            "deploymentPlanDigest": deployment_plan.get("structureDigest")
            or deployment_plan_structure_digest(deployment_plan),
            "nodes": [],
            "edges": [],
            "workloads": copy.deepcopy(graph.get("workloads") or []),
            "placements": copy.deepcopy(deployment_plan.get("placements") or []),
            "storageBindings": copy.deepcopy(
                deployment_plan.get("storageBindings") or []
            ),
            "networkPaths": copy.deepcopy(deployment_plan.get("networkPaths") or []),
            "runtimeBindings": copy.deepcopy(
                deployment_plan.get("runtimeBindings") or []
            ),
            "locationPlan": copy.deepcopy(deployment_plan.get("locationPlan") or {}),
            "runtimeUnits": [],
            "bindingSlots": [],
            "lateBindings": copy.deepcopy(deployment_plan.get("lateBindings") or []),
            "issues": issues,
            "unresolved": [
                item
                for item in issues
                if item.get("classification") in BLOCKING_CLASSES
            ],
            "derivations": [
                *copy.deepcopy(deployment_plan.get("derivations") or []),
                _derivation(
                    "explicit-deployment-target",
                    "Stopped provider projection until CSP and Region are explicit.",
                    source_refs=["project-policy:explicit-deployment-target"],
                ),
            ],
        }
        unresolved["structureDigest"] = provider_template_structure_digest(unresolved)
        return unresolved
    return build_complete_provider_template(
        deployment_plan,
        graph,
        provider=normalized_provider,
        region=region,
    )


def validate_provider_resource_plan(plan: dict[str, Any]) -> None:
    validate_complete_provider_template(plan)
def workload_graph_structure_digest(graph: dict[str, Any]) -> str:
    structural = copy.deepcopy(graph)
    for field in ("issues", "derivations", "inputArtifacts", "inputDigest", "structureDigest"):
        structural.pop(field, None)
    for workload in structural.get("workloads") or []:
        artifact = workload.get("artifact") or {}
        artifact.pop("imageDigest", None)
        for interface in workload.get("interfaces") or []:
            interface.pop("port", None)
            interface.pop("healthPath", None)
        for configuration in workload.get("configuration") or []:
            configuration.pop("value", None)
            configuration.pop("secretRef", None)
    return _canonical_digest(structural)


def deployment_plan_structure_digest(plan: dict[str, Any]) -> str:
    structural = copy.deepcopy(plan)
    for field in ("issues", "derivations", "lateBindings", "structureDigest"):
        structural.pop(field, None)
    for compute in structural.get("computeUnits") or []:
        compute.pop("vmSku", None)
    for path in structural.get("networkPaths") or []:
        path.pop("port", None)
    return _canonical_digest(structural)


def resource_plan_structure_digest(plan: dict[str, Any]) -> str:
    return provider_template_structure_digest(plan)

    structural = copy.deepcopy(plan)
    for field in (
        "issues",
        "unresolved",
        "derivations",
        "lateBindings",
        "structureDigest",
    ):
        structural.pop(field, None)
    for node in structural.get("nodes") or []:
        node.pop("port", None)
    for workload in structural.get("workloads") or []:
        artifact = workload.get("artifact") or {}
        artifact.pop("imageDigest", None)
        for interface in workload.get("interfaces") or []:
            interface.pop("port", None)
            interface.pop("healthPath", None)
        for configuration in workload.get("configuration") or []:
            configuration.pop("value", None)
            configuration.pop("secretRef", None)
    return _canonical_digest(structural)


def bind_runtime_contract(
    graph: dict[str, Any],
    deployment_plan: dict[str, Any],
    runtime_contracts: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind observed runtime values without silently changing plan structure."""

    contracts = (
        list(runtime_contracts)
        if isinstance(runtime_contracts, list)
        else list(runtime_contracts.get("workloads") or [runtime_contracts])
    )
    bound_graph = copy.deepcopy(graph)
    before_graph_digest = workload_graph_structure_digest(bound_graph)
    before_plan_digest = deployment_plan_structure_digest(deployment_plan)
    workloads = {str(item.get("id")): item for item in bound_graph.get("workloads") or []}
    structural_changes: list[dict[str, Any]] = []

    for contract in contracts:
        workload_id = str(contract.get("workloadId") or contract.get("workloadRef") or "")
        workload = workloads.get(workload_id)
        if workload is None:
            structural_changes.append(
                _issue(
                    f"runtimeContracts.{workload_id}",
                    "Implementation observed a workload absent from WorkloadGraph.",
                    classification="requiresRegeneration",
                )
            )
            continue
        artifact = workload.setdefault("artifact", {})
        if contract.get("imageDigest"):
            artifact["imageDigest"] = contract["imageDigest"]
        interfaces = {str(item.get("id")): item for item in workload.get("interfaces") or []}
        for observed in contract.get("interfaces") or []:
            interface_id = str(observed.get("interfaceId") or observed.get("id") or "")
            interface = interfaces.get(interface_id)
            if interface is None:
                structural_changes.append(
                    _issue(
                        f"runtimeContracts.{workload_id}.interfaces.{interface_id}",
                        "Implementation introduced a new interface; deployment design must be regenerated.",
                        classification="requiresRegeneration",
                    )
                )
                continue
            if observed.get("exposure") and observed.get("exposure") != interface.get("exposure"):
                structural_changes.append(
                    _issue(
                        f"runtimeContracts.{workload_id}.interfaces.{interface_id}.exposure",
                        "Implementation changed endpoint exposure; deployment design must be regenerated.",
                        classification="requiresRegeneration",
                    )
                )
                continue
            for field in ("port", "healthPath"):
                if observed.get(field) is not None:
                    interface[field] = observed[field]
        known_storage = {str(item.get("id")): item for item in workload.get("storage") or []}
        observed_storage_ids: set[str] = set()
        for mount in contract.get("mounts") or []:
            storage_id = str(mount.get("storageId") or "")
            observed_storage_ids.add(storage_id)
            storage = known_storage.get(storage_id)
            if storage is None:
                structural_changes.append(
                    _issue(
                        f"runtimeContracts.{workload_id}.mounts.{storage_id}",
                        "Implementation requires new storage; deployment design must be regenerated.",
                        classification="requiresRegeneration",
                    )
                )
            elif mount.get("mountPath"):
                if mount["mountPath"] != storage.get("mountPath"):
                    structural_changes.append(
                        _issue(
                            f"runtimeContracts.{workload_id}.mounts.{storage_id}.mountPath",
                            "Implementation mount path differs from the deployment design contract.",
                            classification="requiresRegeneration",
                        )
                    )
        known_configuration = {
            str(item.get("name")): item for item in workload.get("configuration") or []
        }
        observed_configuration_names: set[str] = set()
        for observed in contract.get("configuration") or []:
            name = str(observed.get("name") or "")
            observed_configuration_names.add(name)
            target = known_configuration.get(name)
            if target is None:
                structural_changes.append(
                    _issue(
                        f"runtimeContracts.{workload_id}.configuration.{name}",
                        "Implementation consumes undeclared configuration; deployment design must be regenerated.",
                        classification="requiresRegeneration",
                    )
                )
                continue
            if observed.get("secretRef"):
                target["secretRef"] = observed["secretRef"]
            elif "value" in observed and target.get("kind") not in {"secret", "secretBinding"}:
                target["value"] = observed["value"]
        if artifact.get("kind") == "generatedApplication":
            for storage_id in sorted(set(known_storage) - observed_storage_ids):
                structural_changes.append(
                    _issue(
                        f"runtimeContracts.{workload_id}.mounts.{storage_id}",
                        "Implementation does not expose a planned storage mount.",
                        classification="requiresRegeneration",
                    )
                )
            for name in sorted(set(known_configuration) - observed_configuration_names):
                structural_changes.append(
                    _issue(
                        f"runtimeContracts.{workload_id}.configuration.{name}",
                        "Implementation does not consume a planned environment binding.",
                        classification="requiresRegeneration",
                    )
                )

    if structural_changes:
        return {
            "schemaVersion": RUNTIME_BINDING_SCHEMA,
            "status": "requiresDeploymentDesignRegeneration",
            "issues": structural_changes,
            "workloadGraph": graph,
            "deploymentPlan": deployment_plan,
            "structureDigest": before_plan_digest,
        }

    bound_plan = build_deployment_plan(bound_graph, {
        "region": (deployment_plan.get("locationPlan") or {}).get("region"),
        "candidateZones": (deployment_plan.get("locationPlan") or {}).get("candidateZones") or [],
    })
    after_graph_digest = workload_graph_structure_digest(bound_graph)
    after_plan_digest = deployment_plan_structure_digest(bound_plan)
    if before_graph_digest != after_graph_digest or before_plan_digest != after_plan_digest:
        return {
            "schemaVersion": RUNTIME_BINDING_SCHEMA,
            "status": "requiresDeploymentDesignRegeneration",
            "issues": [
                _issue(
                    "runtimeContract",
                    "Runtime observations changed the deployment structure digest.",
                    classification="requiresRegeneration",
                )
            ],
            "workloadGraph": graph,
            "deploymentPlan": deployment_plan,
            "structureDigest": before_plan_digest,
        }
    return {
        "schemaVersion": RUNTIME_BINDING_SCHEMA,
        "status": "bound",
        "workloadGraph": bound_graph,
        "deploymentPlan": bound_plan,
        "structureDigest": before_plan_digest,
        "issues": [],
    }
