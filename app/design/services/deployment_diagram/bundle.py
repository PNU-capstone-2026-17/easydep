"""Compose the design-owned deployment diagram source of truth.

The LLM-produced logical deployment model remains editable.  This module combines
that model with RESOURCE_SPEC and the provider dependency knowledge to produce a
deterministic, provider-native ResourcePlan.  Renderers consume the resulting
bundle; they never ask an LLM to draw cloud infrastructure.
"""

from __future__ import annotations

from typing import Any

from app.core.infra_planning import plan_for_anchors
from app.core.orchestration.provider_deployment import (
    build_provider_deployment_model,
    resource_plan_digest,
)
from app.design.services.deployment_diagram.topology import (
    derive_deployment_topology,
    logical_persistent_workload_present,
    provider_projection_policy,
)

_BLOCKING_ISSUE_CLASSES = {"invalid", "unsupported", "needsInput", "unjustified"}


def _target_specs(resource_spec: dict[str, Any]) -> list[dict[str, Any]]:
    targets = [
        dict(item)
        for item in resource_spec.get("deploymentTargets") or []
        if isinstance(item, dict)
    ]
    if not targets:
        return [dict(resource_spec)]
    return [
        {
            **resource_spec,
            "provider": target.get("provider"),
            "region": target.get("region"),
            "selectedZones": list(target.get("zones") or []),
            "deploymentTargets": [target],
        }
        for target in targets
    ]


def _projection(logical_model: dict[str, Any], resource_spec: dict[str, Any]) -> dict[str, Any]:
    provider = str(resource_spec.get("provider") or "").strip().lower()
    region = str(resource_spec.get("region") or "").strip()
    if provider not in {"aws", "azure", "gcp"} or not region:
        return {
            "status": "needsInput",
            "provider": provider or None,
            "region": region or None,
            "issues": [
                {
                    "field": "deploymentTarget",
                    "classification": "needsInput",
                    "reason": "A provider and region are required for a provider-native deployment diagram.",
                }
            ],
        }

    persistent_workload_present = logical_persistent_workload_present(logical_model)
    topology = derive_deployment_topology(
        provider=provider,
        resource_spec=resource_spec,
        logical_deployment_model=logical_model,
        persistent_storage_required=persistent_workload_present,
    )
    blocking = [
        issue
        for issue in topology.get("issues") or []
        if issue.get("classification") in _BLOCKING_ISSUE_CLASSES
    ]
    if blocking:
        return {
            "status": "needsInput",
            "provider": provider,
            "region": region,
            "topology": topology,
            "issues": blocking,
        }

    projection_policy = provider_projection_policy(topology)
    anchors = ["vm"]
    if persistent_workload_present:
        anchors.append("disk")
    load_balanced = topology.get("publicIngress") == "loadBalanced"
    if load_balanced:
        anchors.append("loadBalancer")
    dependency_plan = plan_for_anchors(
        anchors,
        provider,
        region,
        capability_ids=("load-balanced-ingress",) if load_balanced else (),
    ).design
    resource_plan = build_provider_deployment_model(
        provider=provider,
        region=region,
        dependency_plan=dependency_plan,
        projection_policy=projection_policy,
        topology_policy=topology,
        logical_deployment_model=logical_model,
        persistent_storage_required=persistent_workload_present,
        runtime_hints={
            "application": {},
            "state": {
                "image": "postgres:17-bookworm",
                "basis": "project-policy:self-hosted-relational-state-postgresql/v1",
            },
        },
    )
    return {
        "status": "needsInput" if resource_plan.get("unresolved") else "completed",
        "provider": provider,
        "region": region,
        "topology": topology,
        "providerProjectionPolicy": projection_policy,
        "resourcePlan": resource_plan,
        "resourcePlanDigest": resource_plan_digest(resource_plan),
        "issues": list(topology.get("issues") or []),
    }


def build_deployment_diagram_bundle(
    logical_model: dict[str, Any], resource_spec: dict[str, Any] | None
) -> dict[str, Any]:
    """Return the persisted source for both deployment-diagram views."""
    spec = dict(resource_spec or {})
    projections = [_projection(logical_model, target) for target in _target_specs(spec)]
    return {
        "schemaVersion": "easydep-deployment-diagram/v1",
        "mode": "single" if len(projections) == 1 else "alternatives",
        "logicalModel": logical_model,
        "resourceSpec": spec,
        "projections": projections,
    }


def hydrate_deployment_diagram_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Restore all state fields derived from a stored bundle.

    Old artifact versions stored only the logical model.  Treat those as a legacy
    logical-only bundle so existing projects continue to load.
    """
    if bundle.get("schemaVersion") != "easydep-deployment-diagram/v1":
        return {
            "deployment_diagram_model": bundle,
            "deployment_diagram_bundle": {
                "schemaVersion": "easydep-deployment-diagram/v1",
                "mode": "legacyLogicalOnly",
                "logicalModel": bundle,
                "resourceSpec": {},
                "projections": [],
            },
        }
    projections = list(bundle.get("projections") or [])
    primary = projections[0] if len(projections) == 1 else {}
    return {
        "deployment_diagram_bundle": bundle,
        "deployment_diagram_model": dict(bundle.get("logicalModel") or {}),
        "deployment_topology": dict(primary.get("topology") or {}),
        "deployment_resource_plan": dict(primary.get("resourcePlan") or {}),
    }
