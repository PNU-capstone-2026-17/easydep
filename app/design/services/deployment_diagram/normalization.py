"""WorkloadGraph를 검증하고 planning fact를 적용해 결정론적으로 정규화한다."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.design.services.deployment_diagram.digest import (
    workload_graph_structure_digest,
)
from app.design.services.deployment_diagram.planning_constants import (
    ENVIRONMENT_NAME,
    SUPPORTED_PREBUILT_RUNTIME_CATALOG,
    SUPPORTED_PROTOCOLS,
    WORKLOAD_GRAPH_SCHEMA,
)
from app.design.services.deployment_diagram.planning_primitives import (
    derivation as _derivation,
)
from app.design.services.deployment_diagram.planning_primitives import (
    issue as _issue,
)
from app.design.services.deployment_diagram.planning_primitives import (
    refs as _refs,
)
from app.design.services.deployment_diagram.planning_primitives import (
    slug as _slug,
)


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
    """WorkloadGraph의 구조와 planning fact 정합성을 결정론적으로 검사한다.

    Args:
        graph: 검증할 WorkloadGraph JSON이다.
        planning_facts: 선택적인 승인 PlanningFact 문서다.

    Returns:
        기존 검증 순서로 누적한 issue 목록이다.

    Notes:
        입력을 변경하거나 LLM을 호출하지 않는다.
    """

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
    duplicate_ids = sorted(item for item in known if ids.count(item) > 1)
    if any(not item for item in ids) or duplicate_ids:
        detail = f" Duplicate ids: {', '.join(duplicate_ids)}." if duplicate_ids else ""
        issues.append(
            _issue(
                "workloads",
                "Workload and external dependency ids must be non-empty and globally unique."
                + detail,
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
            if (
                artifact.get("deploymentMode") != "container"
                or artifact.get("runtimeCatalogRef") not in SUPPORTED_PREBUILT_RUNTIME_CATALOG
            ):
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
        configuration_by_workload[workload_id] = list(workload.get("configuration") or [])
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
            if (
                isinstance(capacity, bool)
                or not isinstance(capacity, (int, float))
                or capacity <= 0
            ):
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

    connections_by_id = {str(item.get("id") or ""): item for item in model.get("connections") or []}
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
        if str((item.get("artifact") or {}).get("kind") or "") == "generatedApplication"
    }
    for connection in model.get("connections") or []:
        connection_id = str(connection.get("id") or "")
        source_ref = str(connection.get("sourceRef") or "")
        bindings = endpoint_bindings_by_connection.get(connection_id, [])
        if source_ref not in generated_workloads:
            continue
        projections = [str(binding.get("projection") or "") for _, binding in bindings]
        valid_shape = projections == ["url"] or (
            len(projections) == 2 and set(projections) == {"host", "port"}
        )
        if not valid_shape:
            issues.append(
                _issue(
                    f"connections.{connection_id}.endpointBinding",
                    "A generated source connection requires one URL binding or a host/port binding pair.",
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
        str((item.get("artifact") or {}).get("kind") or "") == "prebuiltImage" for item in workloads
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
    globally_allowed_kinds = {kind for kinds in allowed_constraint_kinds.values() for kind in kinds}
    enforce_contract_constraints = any(
        fact.get("kind") in {"workloadContract", "connectionContract", "constraintContract"}
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
            interface_id = str(interface.get("id") or f"{workload_id}-{protocol}")
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
        connection_id = str(value.get("connectionId") or f"{source_id}-to-{target_id}")
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
        target_interface: dict[str, Any] = next(
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
            # URL 형식은 애플리케이션별 스키마를 알아야 만들 수 있다. 명시적인 연결
            # 계약만 있는 경우에는 모든 TCP/HTTP client가 조합할 수 있는 HOST와 PORT를
            # 한 쌍으로 제공한다.
            for projection in ("host", "port"):
                _upsert_by_id(
                    configuration,
                    {
                        "id": f"{target_id}-{projection}",
                        "name": f"{target_name}_{projection.upper()}",
                        "kind": "endpointBinding",
                        "connectionRef": connection_id,
                        "projection": projection,
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
    """후보 WorkloadGraph에 승인 fact와 canonical digest를 적용한다.

    Args:
        candidate: LLM 또는 체크포인트에서 받은 WorkloadGraph JSON이다.
        planning_facts: 적용할 승인 PlanningFact 문서다.

    Returns:
        기존 key·issue·derivation 순서를 유지한 정규화 WorkloadGraph다.

    Notes:
        입력을 깊은 복사하며 placement나 provider resource를 만들지 않는다.
    """

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
    existing_constraint_ids = {str(item.get("id") or "") for item in graph.get("constraints") or []}
    for fact in (planning_facts or {}).get("facts") or []:
        if fact.get("kind") != "capability" or fact.get("status") != "accepted":
            continue
        value = fact.get("value") or {}
        for index, constraint in enumerate(value.get("typedConstraints") or [], start=1):
            if not isinstance(constraint, dict) or not constraint.get("kind"):
                continue
            constraint_id = str(constraint.get("id") or f"{fact.get('id')}-constraint-{index}")
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
