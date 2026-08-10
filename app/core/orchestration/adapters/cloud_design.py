"""Design-time cloud dependency enrichment.

This boundary deliberately uses only depkb. Capacity, performance, price, VM
family, and instance-count decisions belong to later implementation planning.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.core.infra_planning import plan_for_anchors
from app.requirements.capability_contract import (
    SUPPORTED_DEPENDENCY_CAPABILITY_IDS,
    link_dependency_capability,
    requires_load_balanced_ingress,
    requires_persistent_storage,
)


def _alias(value: str) -> str:
    return "resource_" + "".join(c if c.isalnum() else "_" for c in value)


def _modeled_outcome(capability_ids: list[str], persistent: bool) -> str:
    if "persistent-block-storage" in capability_ids:
        return "disk" if persistent else "no_disk"
    if "https-load-balanced-ingress" in capability_ids:
        return "https-load-balanced-ingress"
    return "load-balanced-ingress"


def _render_cloud_deployment(
    design: dict[str, Any], provider: str, region: str
) -> str:
    """Render an English-only, deterministic Docker-on-VM deployment view."""
    nodes = design.get("nodes") or []
    edges = design.get("edges") or []
    lines = [
        "@startuml",
        "!theme plain",
        "skinparam shadowing false",
        f'title Docker-on-VM deployment - {provider} / {region}',
        f'cloud "{provider.upper()} ({region})" as cloud_provider',
    ]
    for node in sorted(nodes, key=lambda item: str(item.get("id", ""))):
        resource_id = str(node.get("id") or "unknown")
        role = str(node.get("provisioningStatus") or "notMandatoryForProvisioning")
        stereotype = {
            "selectedStartResource": "selected",
            "mandatoryForProvisioning": "mandatory",
            "notMandatoryForProvisioning": "non-mandatory",
        }.get(role, "unclassified")
        shape = "database" if resource_id == "disk" else "node"
        lines.append(
            f'{shape} "{resource_id}" as {_alias(resource_id)} <<{stereotype}>>'
        )
    for edge in edges:
        source = _alias(str(edge.get("from") or ""))
        target = _alias(str(edge.get("to") or ""))
        if source != "resource_" and target != "resource_":
            lines.append(f"{source} --> {target} : mandatory for provisioning")
    lines.extend(
        [
            "cloud_provider o-- resource_vm : contains",
            'node "Docker runtime" as docker_runtime',
            'artifact "Application container" as docker_application',
            "resource_vm --> docker_runtime : hosts",
            "docker_runtime --> docker_application : runs",
            "note bottom",
            "  Capacity, performance, price, VM family, and instance count are deferred.",
            "end note",
            "@enduml",
        ]
    )
    return "\n".join(lines)


class CloudDesignAdapter:
    def finalize(
        self,
        *,
        requirements_result: dict[str, Any],
        design_result: dict[str, Any],
        use_cloud_kb: bool = True,
    ) -> dict[str, Any]:
        resource_spec = requirements_result.get("resource_spec") or {}
        provider = str(resource_spec.get("provider") or "")
        region = str(resource_spec.get("region") or "")
        artifacts = design_result.get("artifacts") or {}
        logical_puml = str(
            design_result.get("deployment_diagram_puml")
            or artifacts.get("deployment_diagram")
            or ""
        )
        if not use_cloud_kb:
            return {
                "status": "completed",
                "reason": "cloud knowledge base disabled by evaluation variant",
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "dependency_plan": {},
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
        all_needs = requirements_result.get("deployment_needs") or {}
        accepted = {
            key: value
            for key, value in all_needs.items()
            if isinstance(value, dict)
            and value.get("decision", "accepted") == "accepted"
        }
        capabilities_by_need = {
            key: sorted(
                (
                    set(value.get("dependencyCapabilityIds") or [])
                    | {
                        candidate
                        for candidate in [link_dependency_capability(
                            key, str(value.get("role") or "")
                        )]
                        if candidate
                    }
                )
                & SUPPORTED_DEPENDENCY_CAPABILITY_IDS
            )
            for key, value in accepted.items()
        }
        # Stored CapabilityContract/v1 development runs predate stable IDs.
        if "persistent_storage" in accepted and not capabilities_by_need.get(
            "persistent_storage"
        ):
            capabilities_by_need["persistent_storage"] = [
                "persistent-block-storage"
            ]
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
        persistent_storage_required = requires_persistent_storage(normalized_needs)
        if persistent_storage_required:
            anchors.append("disk")
        load_balanced_capabilities = {
            "load-balanced-ingress",
            "https-load-balanced-ingress",
        }
        if resource_spec.get("multiZone") is True:
            selected_capabilities.add("load-balanced-ingress")
        if requires_load_balanced_ingress(normalized_needs) or (
            resource_spec.get("multiZone") is True
        ):
            anchors.append("loadBalancer")

        # HTTPS is the more specific realization when both IDs were emitted.
        if "https-load-balanced-ingress" in selected_capabilities:
            selected_capabilities.discard("load-balanced-ingress")
        projection_capabilities = tuple(sorted(
            selected_capabilities & load_balanced_capabilities
        ))

        plan = plan_for_anchors(
            anchors,
            provider,
            region,
            capability_ids=projection_capabilities,
        )
        cloud_puml = _render_cloud_deployment(plan.design, provider, region)
        return {
            "status": "completed",
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
                        [{
                            "source": "deployment_needs",
                            "field": key,
                            "capabilityIds": capability_ids,
                            "outcome": _modeled_outcome(
                                capability_ids,
                                persistent_storage_required,
                            ),
                        }
                        for key, capability_ids in sorted(capabilities_by_need.items())
                        if capability_ids]
                    ),
                    *(
                        [{
                            "source": "resource_spec",
                            "field": "multiZone",
                            "outcome": "loadBalancer",
                        }]
                        if resource_spec.get("multiZone") is True
                        else []
                    ),
                ],
                "unmodeledAcceptedNeeds": sorted(
                    key
                    for key in accepted
                    if not capabilities_by_need.get(key)
                ),
            },
            "dependency_plan": plan.design,
            "open_questions": list(plan.questions),
            "unmeasured": list(plan.unmeasured),
            "logical_deployment_diagram_puml": logical_puml,
            "deployment_diagram_puml": cloud_puml,
            "infra_intent": asdict(plan.intent),
            "kb_used": ["depkb"],
            "deferred": ["capacity", "performance", "price", "vm_selection"],
        }
