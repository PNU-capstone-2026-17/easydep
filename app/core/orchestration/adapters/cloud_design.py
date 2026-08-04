"""Design-time cloud dependency enrichment.

This boundary deliberately uses only depkb. Capacity, performance, price, VM
family, and instance-count decisions belong to later implementation planning.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.core.cloudkb.depkb.plantuml import deployment_puml
from app.core.infra_planning import plan_for_anchors


class CloudDesignAdapter:
    def finalize(
        self,
        *,
        requirements_result: dict[str, Any],
        design_result: dict[str, Any],
    ) -> dict[str, Any]:
        resource_spec = requirements_result.get("resource_spec") or {}
        provider = str(resource_spec.get("provider") or "")
        region = str(resource_spec.get("region") or "")
        logical_puml = str(design_result.get("deployment_diagram_puml") or "")
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
        model = design_result.get("deployment_diagram_model") or {}
        if any(
            str(node.get("kind", "")).lower() == "database"
            for node in model.get("Nodes", [])
        ):
            anchors.append("disk")
        if resource_spec.get("multiZone") is True:
            anchors.append("loadBalancer")

        plan = plan_for_anchors(anchors, provider, region)
        cloud_puml = deployment_puml(
            plan.intent,
            title=f"Docker-on-VM deployment - {provider} / {region}",
        )
        docker_note = (
            'artifact "Docker application container" as docker_application\n'
            'docker_application ..> vm : deployed on\n'
        )
        cloud_puml = cloud_puml.replace("@enduml", docker_note + "@enduml")
        return {
            "status": "completed",
            "provider": provider,
            "region": region,
            "anchors": anchors,
            "dependency_plan": plan.design,
            "open_questions": list(plan.questions),
            "unmeasured": list(plan.unmeasured),
            "logical_deployment_diagram_puml": logical_puml,
            "deployment_diagram_puml": cloud_puml,
            "infra_intent": asdict(plan.intent),
            "kb_used": ["depkb"],
            "deferred": ["capacity", "performance", "price", "vm_selection"],
        }
