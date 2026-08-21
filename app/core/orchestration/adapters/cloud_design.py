"""Boundary exposing the design-owned deployment bundle to implementation.

Provider primitives are already projected deterministically inside the design
deployment-diagram subgraph.  This adapter never rebuilds topology from
RESOURCE_SPEC and never supplies database/runtime defaults.
"""

from __future__ import annotations

from typing import Any

from app.design.services.deployment_diagram.planner import (
    RESOURCE_PLAN_SCHEMA,
    resource_plan_structure_digest,
    validate_provider_resource_plan,
)
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)


class CloudDesignAdapter:
    def finalize(
        self,
        *,
        requirements_result: dict[str, Any],
        design_result: dict[str, Any],
        use_cloud_kb: bool = True,
    ) -> dict[str, Any]:
        del requirements_result, use_cloud_kb
        artifacts = design_result.get("artifacts") or {}
        logical_puml = str(
            design_result.get("deployment_diagram_puml")
            or artifacts.get("deployment_diagram")
            or ""
        )
        bundle = dict(design_result.get("deployment_diagram_bundle") or {})
        if bundle.get("schemaVersion") != "easydep-deployment-diagram":
            return {
                "status": "needsRegeneration",
                "reason": "deployment-diagram-missing",
                "open_questions": [
                    {
                        "field": "deploymentDiagram",
                        "classification": "needsInput",
                        "question": "Regenerate the deployment diagram from its WorkloadGraph before IaC generation.",
                    }
                ],
                "deployment_diagram_bundle": bundle,
                "logical_deployment_diagram_puml": logical_puml,
                "deployment_diagram_puml": logical_puml,
                "kb_used": [],
                "deferred": ["runtimeBinding", "vm_selection"],
            }

        projections = list(bundle.get("projections") or [])
        runtime_puml = deployment_bundle_runtime_puml(bundle)
        provisioning_puml = deployment_bundle_provisioning_puml(bundle)
        if len(projections) != 1:
            return {
                "status": "alternativesReady",
                "mode": "alternatives",
                "requires_target_selection": True,
                "provider_deployments": [
                    {
                        "status": item.get("status"),
                        "provider": item.get("provider"),
                        "region": item.get("region"),
                        "resource_plan": item.get("resourcePlan"),
                        "deployment_plan": item.get("deploymentPlan"),
                    }
                    for item in projections
                ],
                "resource_plan": {
                    "mode": "alternatives",
                    "alternatives": [item.get("resourcePlan") for item in projections],
                },
                "deployment_diagram_bundle": bundle,
                "deployment_diagram_puml": runtime_puml,
                "deployment_diagram_provisioning_puml": provisioning_puml,
                "open_questions": [
                    {
                        "field": "selectedDeploymentTarget",
                        "question": "Which provider alternative should proceed to IaC generation?",
                        "choices": [
                            f"{item.get('provider')}:{item.get('region')}"
                            for item in projections
                        ],
                    }
                ],
                "kb_used": [],
            }

        projection = dict(projections[0])
        resource_plan = dict(projection.get("resourcePlan") or {})
        deployment_plan = dict(projection.get("deploymentPlan") or {})
        if resource_plan.get("schemaVersion") != RESOURCE_PLAN_SCHEMA:
            return {
                "status": "failed",
                "reason": "deployment-diagram-missing-resource-plan",
                "open_questions": [],
                "deployment_diagram_bundle": bundle,
            }
        try:
            validate_provider_resource_plan(resource_plan)
        except ValueError as error:
            return {
                "status": "failed",
                "reason": "invalid-resource-plan",
                "error": str(error),
                "open_questions": [],
                "deployment_diagram_bundle": bundle,
            }

        workload_graph = dict(bundle.get("workloadGraph") or {})
        storage = list(deployment_plan.get("storageBindings") or [])
        load_balanced = any(
            item.get("ingressKind") == "loadBalancer"
            for item in deployment_plan.get("networkPaths") or []
        )
        anchors = ["vm", *(["disk"] if storage else []), *(["loadBalancer"] if load_balanced else [])]
        return {
            "status": projection.get("status") or "needsInput",
            "provider": projection.get("provider"),
            "region": projection.get("region"),
            "anchors": anchors,
            "resource_plan": resource_plan,
            "resource_plan_digest": resource_plan.get("structureDigest"),
            "resource_plan_structure_digest": resource_plan_structure_digest(resource_plan),
            "deployment_plan": deployment_plan,
            "workload_graph": workload_graph,
            "deployment_diagram_model": workload_graph,
            "deployment_diagram_bundle": bundle,
            "deployment_diagram_puml": runtime_puml,
            "deployment_diagram_provisioning_puml": provisioning_puml,
            "logical_deployment_diagram_puml": logical_puml,
            "open_questions": list(projection.get("issues") or []),
            "dependency_plan": {},
            "dependency_coverage": {
                "modeledInputs": [
                    {
                        "source": "deployment-diagram",
                        "field": "workloadGraph",
                        "outcome": resource_plan.get("structureDigest"),
                    }
                ],
                "unmodeledAcceptedNeeds": [],
            },
            "kb_used": [],
            "deferred": ["runtimeBinding", "vm_selection"],
        }
