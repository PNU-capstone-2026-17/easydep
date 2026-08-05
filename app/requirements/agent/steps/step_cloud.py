"""Derive generic deployment needs from classified software requirements.

Natural-language interpretation belongs to one structured LLM call. Code only checks
the output envelope and requirement references. This stage describes what deployment must
provide; selecting VM, load balancer, disk, or any CSP product belongs to design.
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
from app.requirements.schemas import DeploymentNeedsResult

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_SYSTEM = """Derive deployment needs from the supplied software requirements.
Return a generic deploymentNeeds dictionary. Each dictionary key is a concise,
snake_case capability identifier chosen for this application. Keep product, language,
framework, protocol, version, and endpoint names out of the key; put grounded details in
metadata instead (for example, use `container_runtime`, not `java21_container_runtime`).
Each value contains:
- role: what capability the deployment must provide and why
- required: true when the requirements mandate it; false for a supported preference
- requirementIds: one or more exact requirement IDs from the input
- metadata: free-form structured details, including an `unresolved` string array when useful

Do not select or name concrete cloud resources, CSP products, VM counts, instance types,
load balancers, disks, Kubernetes objects, or implementation products. For example,
derive `durable storage across restarts`, not `AWS EBS`; derive `survive one instance
failure`, not `two VMs and a load balancer`. Do not invent needs without supporting
requirement IDs. Do not repeat application behavior such as CRUD, business functions,
API endpoints, programming languages, or frameworks as deployment needs. Include only
properties that change the deployment boundary, such as ingress, persistence, availability,
capacity, performance, security, runtime configuration, or observability. Metadata must
contain only constraints explicitly stated in the referenced requirements. Never introduce
defaults such as protocol versions, consistency levels, backup policies, replication, domain
names, or certificate authorities. The `unresolved` list is only for a missing value clearly
required to interpret an expressed constraint; it is not a generic deployment checklist.
Use an empty metadata object when there are no grounded details. Merge equivalent needs and
keep distinct roles separate.

A statement that high availability is not required is a relaxed availability constraint,
not a mandate for one instance or no replication. Represent it generically (for example,
`availability_requirement` with `high_availability: false`) and do not infer topology,
instance count, failover, or replication from it. More generally, an allowed simplification
is not a required implementation choice."""


@contract("derive_deployment_needs", requires=("classified",), produces=("deployment_needs",))
def derive_deployment_needs(state: AgentState) -> dict:
    """Return a generic need dictionary grounded through existing requirement IDs."""
    classified = list(state.get("classified") or [])
    known = {str(item.get("id")) for item in classified if item.get("id")}
    listing = [
        {"id": item.get("id"), "text": item.get("text", ""), "type": item.get("type")}
        for item in classified
    ]
    try:
        result = invoke_structured(
            DeploymentNeedsResult,
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(content=json.dumps(listing, ensure_ascii=False)),
            ],
        )
    except Exception as exc:  # noqa: BLE001 - absence must be visible, not fabricated
        telemetry.record_degradation(
            "deployment_needs.extraction", f"{type(exc).__name__}: {exc}"
        )
        return {"deployment_needs": {}, "phase": "deployment_needs"}

    needs: dict[str, dict] = {}
    for key, need in result.deployment_needs.items():
        if not _KEY.fullmatch(key):
            telemetry.record_degradation("deployment_needs.invalid_key", key)
            continue
        requirement_ids = list(dict.fromkeys(
            requirement_id for requirement_id in need.requirement_ids
            if requirement_id in known
        ))
        if not requirement_ids:
            telemetry.record_degradation("deployment_needs.ungrounded", key)
            continue
        role = need.role.strip()
        if not role:
            telemetry.record_degradation("deployment_needs.empty_role", key)
            continue
        needs[key] = {
            "role": role,
            "required": need.required,
            "requirementIds": requirement_ids,
            "metadata": need.metadata,
        }

    return {"deployment_needs": needs, "phase": "deployment_needs"}
