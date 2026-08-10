"""Design and provisioning projections of an infrastructure intent."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .infra_intent import InfraIntent

_GROUP = {
    "network": "network", "subnet": "network", "firewall": "network",
    "publicIp": "network", "publicIPPrefix": "network", "nic": "network",
    "loadBalancer": "network", "defaultRoute": "network",
    "vm": "compute", "disk": "compute", "sshKey": "compute", "workloadIdentity": "identity",
}


def design_view(intent: InfraIntent) -> dict:
    realization = {item.id: item.behavior for item in intent.providerRealizations}
    nodes = [
        {
            "id": resource.id,
            "group": _GROUP.get(resource.id, "other"),
            "provisioningStatus": resource.provisioningStatus,
            "because": list(resource.because),
            "condition": resource.condition,
            "providerRealization": realization.get(resource.id),
        }
        for resource in intent.resources
    ]
    edges = [
        {"from": source, "to": target, "relation": "mandatoryForProvisioning"}
        for resource in intent.resources
        for source, separator, target in (reason.partition("→") for reason in resource.because)
        if separator
    ]
    return {
        "schemaVersion": intent.schemaVersion, "view": "design",
        "csp": intent.csp, "region": intent.region,
        "edgeSemantics": "A→B means A has the stated dependency finding on B",
        "nodes": nodes, "edges": edges,
        "unresolvedConditions": [asdict(item) for item in intent.decisions],
        "constraints": [asdict(item) for item in intent.constraints],
        "unavailableFindings": [asdict(item) for item in intent.unavailableFindings],
        "capabilityRealizations": list(intent.capabilityRealizations),
        "officialDependencies": list(intent.officialDependencies),
        "provenance": intent.provenance,
    }


def _wait_for(intent: InfraIntent) -> list[dict]:
    path = Path(__file__).with_name("operations.json")
    if not path.exists():
        return []
    operations = json.loads(path.read_text(encoding="utf-8"))["operations"]
    ids = {resource.id for resource in intent.resources}
    return [
        {
            "id": operation["resource"], "operation": operation["op"],
            "completionSignal": operation["doneSignal"],
            "observationStatus": operation["status"],
        }
        for operation in operations
        if operation["csp"] == intent.csp and operation["resource"] in ids
    ]


def provision_view(intent: InfraIntent) -> dict:
    statuses = {resource.id: resource.provisioningStatus for resource in intent.resources}
    realization = {item.id: item.behavior for item in intent.providerRealizations}
    return {
        "schemaVersion": intent.schemaVersion, "view": "provision", "layer": "cloud",
        "csp": intent.csp, "region": intent.region,
        "createOrder": [
            {"id": resource_id, "provisioningStatus": statuses[resource_id]}
            for resource_id in intent.createOrder
        ],
        "providerRealizations": [asdict(item) for item in intent.providerRealizations],
        "waitFor": _wait_for(intent),
        "deleteBlockedWhileAttached": [list(pair) for pair in intent.deleteBlockedWhileAttached],
        "detachRequiredBeforeDelete": [list(pair) for pair in intent.detachRequiredBeforeDelete],
        "cascadeDeletedWithOwner": [
            {"owner": owner, "resource": resource}
            for owner, resource in intent.cascadeDeletedWithOwner
        ],
        "runtimeDependencies": [
            {"subject": subject, "object": object_, "signal": signal}
            for subject, object_, signal in intent.runtimeRequiredForSignal
        ],
        "doNotCreateWhenOmitted": [
            {"id": resource_id, "behavior": behavior}
            for resource_id, behavior in sorted(realization.items())
            if behavior in {"providerDefaulted", "providerCreated"}
        ],
        "checks": [asdict(item) for item in intent.constraints],
        "blockedBy": [asdict(item) for item in intent.decisions],
        "unavailableFindings": [asdict(item) for item in intent.unavailableFindings],
        "capabilityRealizations": list(intent.capabilityRealizations),
        "officialDependencies": list(intent.officialDependencies),
        "provenance": intent.provenance,
    }
