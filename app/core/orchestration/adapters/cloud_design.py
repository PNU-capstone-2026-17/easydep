"""Design-time cloud dependency enrichment.

This boundary deliberately uses only depkb. Capacity, performance, price, VM
family, and instance-count decisions belong to later implementation planning.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.core.infra_planning import plan_for_anchors
from app.core.orchestration.provider_deployment import (
    build_provider_deployment_model,
    resource_plan_digest,
    resource_plan_structure_digest,
)
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.topology import (
    derive_deployment_topology,
    provider_projection_policy,
)
from app.requirements.capability_contract import (
    MODELED_DEPENDENCY_CAPABILITY_IDS,
    OUT_OF_SCOPE_DEPENDENCY_CAPABILITY_IDS,
    RECOGNIZED_DEPENDENCY_CAPABILITY_IDS,
    link_dependency_capability,
    requires_persistent_storage,
)


def _alias(value: str) -> str:
    return "resource_" + "".join(c if c.isalnum() else "_" for c in value)


def _modeled_outcome(capability_ids: list[str], persistent: bool) -> str:
    if "persistent-block-storage" in capability_ids:
        return "disk" if persistent else "no_disk"
    return "load-balanced-ingress"


def _recognized_capability_ids(key: str, need: dict[str, Any]) -> set[str]:
    linked = link_dependency_capability(
        key,
        str(need.get("role") or ""),
        need.get("evidenceSpans") or (),
    )
    return (
        set(need.get("dependencyCapabilityIds") or [])
        | ({linked} if linked else set())
    ) & RECOGNIZED_DEPENDENCY_CAPABILITY_IDS


def render_cloud_deployment(
    model: dict[str, Any], provider: str, region: str, topology_policy: dict[str, Any]
) -> str:
    """Render an English-only provider-native deployment view."""
    nodes = model.get("nodes") or []
    edges = model.get("edges") or []
    lines = [
        "@startuml",
        "!theme plain",
        "skinparam shadowing false",
        f"title Docker-on-VM deployment - {provider} / {region}",
        f'cloud "{provider.upper()} ({region})" as cloud_provider',
    ]
    for node in sorted(nodes, key=lambda item: str(item.get("id", ""))):
        resource_id = str(node.get("id") or "unknown")
        shape = "database" if node.get("group") == "storage" else "node"
        stereotype = (
            "provider-managed"
            if node.get("providerKind") == "compute-group"
            else str(node.get("group"))
        )
        lines.append(f'{shape} "{node.get("name")}" as {_alias(resource_id)} <<{stereotype}>>')
    for edge in edges:
        source = _alias(str(edge.get("from") or ""))
        target = _alias(str(edge.get("to") or ""))
        if source != "resource_" and target != "resource_":
            lines.append(f"{source} --> {target} : {edge.get('label') or 'depends on'}")
    topology_note = (
        "  Topology family: "
        f"{topology_policy.get('familyId') or 'unresolved'}. "
        "This diagram makes no availability or SLA claim."
    )
    for node in nodes:
        if node.get("group") == "compute":
            lines.append(f"cloud_provider o-- {_alias(str(node.get('id')))} : contains")
    lines.extend(["note bottom", topology_note])
    if model.get("unresolved"):
        lines.append("  Some deployment decisions remain unresolved.")
    lines.extend(
        [
            "  Performance, price, VM family, and traffic autoscaling are deferred.",
            "end note",
            "@enduml",
        ]
    )
    return "\n".join(lines)


def _runtime_hints(accepted: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Collect only explicit, structured runtime facts from accepted needs."""
    application: dict[str, Any] = {}
    # The supported Docker-on-VM profile currently realizes an explicit database
    # workload as self-hosted PostgreSQL.  This is a versioned project policy, not
    # an inference from a workload name or a universal cloud dependency.
    state: dict[str, Any] = {
        "image": "postgres:17-bookworm",
        "basis": "project-policy:self-hosted-relational-state-postgresql/v1",
    }
    for need in accepted.values():
        metadata = need.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        application_image = metadata.get("application_container_image")
        state_image = metadata.get("database_container_image")
        if application_image:
            application["imageDescription"] = str(application_image)
        if state_image:
            state["image"] = str(state_image)
        port = metadata.get("port")
        if port is not None and str(port).isdigit():
            application["containerPort"] = int(port)
        health_path = metadata.get("health_path") or metadata.get("endpoint")
        if isinstance(health_path, str) and health_path.startswith("/"):
            application["healthPath"] = health_path
        names = metadata.get("environment_variables")
        if isinstance(names, list):
            application["configurationInputs"] = sorted(
                str(item) for item in names if str(item).strip()
            )
    return {"application": application, "state": state}


class CloudDesignAdapter:
    def finalize(
        self,
        *,
        requirements_result: dict[str, Any],
        design_result: dict[str, Any],
        use_cloud_kb: bool = True,
    ) -> dict[str, Any]:
        resource_spec = requirements_result.get("resource_spec") or {}
        all_needs = requirements_result.get("deployment_needs") or {}
        artifacts = design_result.get("artifacts") or {}
        logical_puml = str(
            design_result.get("deployment_diagram_puml")
            or artifacts.get("deployment_diagram")
            or ""
        )
        unsupported_https_needs = sorted(
            key
            for key, need in all_needs.items()
            if isinstance(need, dict)
            and _recognized_capability_ids(key, need)
            & OUT_OF_SCOPE_DEPENDENCY_CAPABILITY_IDS
        )
        if unsupported_https_needs:
            return {
                "status": "unsupported",
                "reason": "https-ingress-out-of-scope",
                "provider": str(resource_spec.get("provider") or ""),
                "region": str(resource_spec.get("region") or ""),
                "open_questions": [],
                "unsupportedNeeds": unsupported_https_needs,
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "kb_used": [],
                "deferred": [
                    "dependencies", "capacity", "performance", "price", "vm_selection"
                ],
            }
        deployment_targets = [
            dict(item)
            for item in resource_spec.get("deploymentTargets") or []
            if isinstance(item, dict)
        ]
        if len(deployment_targets) > 1:
            alternatives: list[dict[str, Any]] = []
            for target in deployment_targets:
                scoped_spec = {
                    **resource_spec,
                    "provider": target.get("provider"),
                    "region": target.get("region"),
                    "selectedZones": list(target.get("zones") or []),
                    "deploymentTargets": [target],
                }
                alternatives.append(
                    self.finalize(
                        requirements_result={
                            **requirements_result,
                            "resource_spec": scoped_spec,
                        },
                        design_result=design_result,
                        use_cloud_kb=use_cloud_kb,
                    )
                )
            logical_puml = str(
                design_result.get("deployment_diagram_puml")
                or (design_result.get("artifacts") or {}).get("deployment_diagram")
                or ""
            )
            return {
                "status": "alternativesReady",
                "mode": "alternatives",
                "requires_target_selection": True,
                "provider_deployments": alternatives,
                "deployment_diagrams": {
                    f"{item.get('provider')}:{item.get('region')}": item.get(
                        "deployment_diagram_puml"
                    )
                    for item in alternatives
                },
                "resource_plan": {
                    "mode": "alternatives",
                    "alternatives": [item.get("resource_plan") for item in alternatives],
                },
                # No provider alternative is silently promoted to the implementation target.
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "open_questions": [
                    {
                        "field": "selectedDeploymentTarget",
                        "question": "Which provider alternative should proceed to IaC generation?",
                        "choices": [
                            f"{target.get('provider')}:{target.get('region')}"
                            for target in deployment_targets
                        ],
                    }
                ],
            }
        if deployment_targets:
            resource_spec = {
                **resource_spec,
                "provider": deployment_targets[0].get("provider"),
                "region": deployment_targets[0].get("region"),
                "selectedZones": list(deployment_targets[0].get("zones") or []),
            }
        provider = str(resource_spec.get("provider") or "")
        region = str(resource_spec.get("region") or "")
        logical_model = design_result.get("deployment_diagram_model") or {}
        if not use_cloud_kb:
            topology_policy = derive_deployment_topology(
                provider=provider,
                resource_spec=resource_spec,
                logical_deployment_model=logical_model,
            )
            return {
                "status": "completed",
                "reason": "cloud knowledge base disabled by evaluation variant",
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "dependency_plan": {},
                "topology_policy": topology_policy,
                "kb_used": [],
                "deferred": [
                    "dependencies",
                    "capacity",
                    "performance",
                    "price",
                    "vm_selection",
                ],
            }
        if provider not in {"aws", "azure", "gcp"} or not region:
            return {
                "status": "skipped",
                "reason": "provider and region are required for cloud dependency planning",
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "kb_used": [],
                "deferred": ["capacity", "performance", "price", "vm_selection"],
            }

        anchors = ["vm"]
        accepted = {
            key: value
            for key, value in all_needs.items()
            if isinstance(value, dict) and value.get("decision", "accepted") == "accepted"
        }
        capabilities_by_need = {
            key: sorted(
                _recognized_capability_ids(key, value)
                & MODELED_DEPENDENCY_CAPABILITY_IDS
            )
            for key, value in accepted.items()
        }
        # Stored CapabilityContract/v1 development runs predate stable IDs.
        if "persistent_storage" in accepted and not capabilities_by_need.get("persistent_storage"):
            capabilities_by_need["persistent_storage"] = ["persistent-block-storage"]
        normalized_needs = {
            **all_needs,
            **{
                key: {**value, "dependencyCapabilityIds": capabilities_by_need[key]}
                for key, value in accepted.items()
            },
        }
        selected_capabilities = {
            capability_id
            for capability_ids in capabilities_by_need.values()
            for capability_id in capability_ids
        }
        database_present = any(
            str(node.get("kind") or "").strip().lower() == "database"
            for node in (logical_model.get("Nodes") or logical_model.get("nodes") or [])
            if isinstance(node, dict)
        )
        persistent_storage_required = (
            requires_persistent_storage(normalized_needs) or database_present
        )
        multi_region_needs = [
            key
            for key, need in accepted.items()
            if isinstance(need.get("metadata"), dict)
            and need["metadata"].get("placementScope") == "multiRegion"
        ]
        if multi_region_needs:
            return {
                "status": "unsupported",
                "reason": "multi-region-out-of-scope",
                "provider": provider,
                "region": region,
                "open_questions": [],
                "unsupportedNeeds": sorted(multi_region_needs),
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "kb_used": [],
                "deferred": ["dependencies", "capacity", "performance", "price", "vm_selection"],
            }
        effective_spec = dict(resource_spec)
        if (
            "publicIngress" not in effective_spec
            and "load-balanced-ingress" in selected_capabilities
        ):
            effective_spec["publicIngress"] = "loadBalanced"
        topology_policy = derive_deployment_topology(
            provider=provider,
            resource_spec=effective_spec,
            logical_deployment_model=logical_model,
            persistent_storage_required=persistent_storage_required,
        )
        blocking_topology_issues = [
            issue
            for issue in topology_policy.get("issues") or []
            if issue.get("classification")
            in {"invalid", "unsupported", "needsInput", "unjustified"}
        ]
        if blocking_topology_issues:
            return {
                "status": (
                    "unsupported"
                    if any(
                        issue.get("classification") == "unsupported"
                        for issue in blocking_topology_issues
                    )
                    else "needsInput"
                ),
                "reason": "deployment-topology-not-resolved",
                "provider": provider,
                "region": region,
                "topology_policy": topology_policy,
                "open_questions": blocking_topology_issues,
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "kb_used": [],
                "deferred": ["dependencies", "capacity", "performance", "price", "vm_selection"],
            }
        projection_policy = provider_projection_policy(topology_policy)
        persistence_need_keys = {
            key
            for key, need in accepted.items()
            if (
                "persistent-block-storage" in capabilities_by_need.get(key, [])
                or (
                    isinstance((need.get("metadata") or {}).get("applicationState"), dict)
                    and (need.get("metadata") or {})["applicationState"].get("durability")
                    == "persistent"
                )
            )
        }
        persistence_source_refs = sorted(
            {
                str(requirement_id)
                for key, need in accepted.items()
                if (
                    "persistent-block-storage" in capabilities_by_need.get(key, [])
                    or (
                        isinstance((need.get("metadata") or {}).get("applicationState"), dict)
                        and (need.get("metadata") or {})["applicationState"].get("durability")
                        == "persistent"
                    )
                )
                for requirement_id in (
                    need.get("requirementIds") or need.get("requirement_ids") or []
                )
            }
        )
        if persistent_storage_required:
            anchors.append("disk")
        load_balanced_capabilities = {"load-balanced-ingress"}
        if topology_policy.get("publicIngress") == "loadBalanced":
            selected_capabilities.add("load-balanced-ingress")
        if selected_capabilities & load_balanced_capabilities:
            anchors.append("loadBalancer")

        projection_capabilities = tuple(sorted(selected_capabilities & load_balanced_capabilities))

        plan = plan_for_anchors(
            anchors,
            provider,
            region,
            capability_ids=projection_capabilities,
        )
        deployment_model = build_provider_deployment_model(
            provider=provider,
            region=region,
            dependency_plan=plan.design,
            projection_policy=projection_policy,
            topology_policy=topology_policy,
            logical_deployment_model=logical_model,
            persistent_storage_required=persistent_storage_required,
            persistence_source_refs=persistence_source_refs,
            runtime_hints=_runtime_hints(accepted),
        )
        structure_digest = resource_plan_structure_digest(deployment_model)
        design_plan = design_result.get("deployment_resource_plan") or {}
        design_structure_digest = None
        if (
            isinstance(design_plan, dict)
            and design_plan.get("schemaVersion") == "easydep-resource-plan/v1"
            and design_plan.get("provider") == provider
            and design_plan.get("region") == region
        ):
            design_structure_digest = resource_plan_structure_digest(design_plan)
            if design_structure_digest != structure_digest:
                return {
                    "status": "failed",
                    "reason": "design-cloud-resource-plan-drift",
                    "provider": provider,
                    "region": region,
                    "designResourcePlanStructureDigest": design_structure_digest,
                    "cloudResourcePlanStructureDigest": structure_digest,
                    "logical_deployment_diagram_puml": logical_puml,
                    "deployment_diagram_puml": logical_puml,
                    "open_questions": [],
                    "kb_used": [],
                    "deferred": [],
                }
        projection_status = (
            "needsInput" if deployment_model.get("unresolved") else "completed"
        )
        diagram_bundle = {
            "schemaVersion": "easydep-deployment-diagram/v1",
            "mode": "single",
            "logicalModel": logical_model,
            "resourceSpec": effective_spec,
            "projections": [
                {
                    "status": projection_status,
                    "provider": provider,
                    "region": region,
                    "topology": topology_policy,
                    "providerProjectionPolicy": projection_policy,
                    "resourcePlan": deployment_model,
                    "resourcePlanDigest": resource_plan_digest(deployment_model),
                    "issues": list(topology_policy.get("issues") or []),
                }
            ],
        }
        cloud_puml = deployment_bundle_runtime_puml(diagram_bundle)
        return {
            "status": projection_status,
            "provider": provider,
            "region": region,
            "anchors": anchors,
            "dependency_coverage": {
                "modeledInputs": [
                    {
                        "source": "system_scope",
                        "field": "docker_on_vm",
                        "outcome": "vm",
                    },
                    *(
                        [
                            {
                                "source": "deployment_needs",
                                "field": key,
                                "capabilityIds": capability_ids,
                                "outcome": _modeled_outcome(
                                    capability_ids,
                                    persistent_storage_required,
                                ),
                            }
                            for key, capability_ids in sorted(capabilities_by_need.items())
                            if capability_ids
                        ]
                    ),
                    *(
                        [
                            {
                                "source": "deployment_needs",
                                "field": key,
                                "capabilityIds": [],
                                "outcome": "persistent-workload-with-separate-data-disk",
                                "basis": "applicationState.durability+projectPolicy",
                            }
                            for key in sorted(persistence_need_keys)
                            if not capabilities_by_need.get(key)
                        ]
                    ),
                    {
                        "source": "deployment-topology",
                        "field": "topologyFamily",
                        "requirementIds": [],
                        "outcome": topology_policy.get("familyId"),
                    },
                ],
                "unmodeledAcceptedNeeds": sorted(
                    key
                    for key in accepted
                    if not capabilities_by_need.get(key) and key not in persistence_need_keys
                ),
            },
            "dependency_plan": plan.design,
            "resource_plan": deployment_model,
            "resource_plan_digest": resource_plan_digest(deployment_model),
            "resource_plan_structure_digest": structure_digest,
            "design_resource_plan_structure_digest": design_structure_digest,
            "deployment_diagram_model": deployment_model,
            "deployment_diagram_bundle": diagram_bundle,
            "deployment_diagram_provisioning_puml": (
                deployment_bundle_provisioning_puml(diagram_bundle)
            ),
            "topology_policy": topology_policy,
            "provider_projection_policy": projection_policy,
            "open_questions": list(plan.questions),
            "unmeasured": list(plan.unmeasured),
            "logical_deployment_diagram_puml": logical_puml,
            "deployment_diagram_puml": cloud_puml,
            "infra_intent": asdict(plan.intent),
            "kb_used": ["depkb"],
            "deferred": ["capacity", "performance", "price", "vm_selection"],
        }
