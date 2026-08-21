"""Compose the design-owned WorkloadGraph deployment bundle."""

from __future__ import annotations

import copy
from typing import Any

from app.design.services.deployment_diagram.planner import (
    BLOCKING_CLASSES,
    build_deployment_plan,
    build_provider_resource_plan,
    extract_planning_facts,
    normalize_workload_graph,
    planning_context,
)


def _target_contexts(resource_spec: dict[str, Any]) -> list[dict[str, Any]]:
    targets = [
        dict(item)
        for item in resource_spec.get("deploymentTargets") or []
        if isinstance(item, dict)
    ]
    if not targets:
        return [planning_context(resource_spec)]
    return [
        planning_context(
            {
                **resource_spec,
                "provider": target.get("provider"),
                "region": target.get("region"),
                "selectedZones": list(target.get("zones") or []),
                "deploymentTargets": [target],
            }
        )
        for target in targets
    ]


def build_deployment_diagram_bundle(
    workload_graph_candidate: dict[str, Any],
    resource_spec: dict[str, Any] | None,
    *,
    planning_facts: dict[str, Any] | None = None,
    planning_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build WorkloadGraph, provider-neutral plan, and CSP projections.

    ``planning_inputs`` is accepted at the design adapter boundary so structured
    requirement/design models and their digests travel with the bundle.  Tests and
    importers may supply an already-normalized ``planning_facts`` document.
    """

    spec = copy.deepcopy(resource_spec or {})
    facts = copy.deepcopy(planning_facts) if planning_facts else extract_planning_facts(
        resource_spec=spec, **(planning_inputs or {})
    )
    graph = normalize_workload_graph(workload_graph_candidate, planning_facts=facts)
    projections: list[dict[str, Any]] = []
    for context in _target_contexts(spec):
        deployment_plan = build_deployment_plan(graph, context)
        resource_plan = build_provider_resource_plan(
            deployment_plan,
            graph,
            provider=str(context.get("provider") or ""),
            region=str(context.get("region") or ""),
        )
        blocking = [
            item
            for item in resource_plan.get("issues") or []
            if item.get("classification") in BLOCKING_CLASSES
        ]
        projections.append(
            {
                "status": "needsInput" if blocking else "completed",
                "provider": context.get("provider"),
                "region": context.get("region"),
                "planningContext": context,
                "deploymentPlan": deployment_plan,
                "deploymentPlanStructureDigest": deployment_plan["structureDigest"],
                "resourcePlan": resource_plan,
                "resourcePlanStructureDigest": resource_plan["structureDigest"],
                "issues": blocking,
            }
        )
    return {
        "schemaVersion": "easydep-deployment-diagram",
        "status": (
            "needsInput"
            if any(item.get("status") == "needsInput" for item in projections)
            else "completed"
        ),
        "mode": "single" if len(projections) == 1 else "alternatives",
        "planningFacts": facts,
        "workloadGraph": graph,
        "resourceSpec": spec,
        "projections": projections,
    }


def hydrate_deployment_diagram_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Hydrate the sole supported WorkloadGraph deployment bundle."""

    if bundle.get("schemaVersion") != "easydep-deployment-diagram":
        raise ValueError("unsupported deployment diagram schema")
    projections = list(bundle.get("projections") or [])
    primary = projections[0] if len(projections) == 1 else {}
    return {
        "deployment_diagram_bundle": bundle,
        "deployment_diagram_model": dict(bundle.get("workloadGraph") or {}),
        "deployment_workload_graph": dict(bundle.get("workloadGraph") or {}),
        "deployment_plan": dict(primary.get("deploymentPlan") or {}),
        "deployment_resource_plan": dict(primary.get("resourcePlan") or {}),
    }
