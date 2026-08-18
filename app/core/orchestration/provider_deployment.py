"""Project design facts and cloud dependencies into one provider-native resource plan."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

_PROVIDER_MODELS: dict[str, dict[str, Any]] = {
    "aws": {
        "resources": {
            "compute-instance": ("EC2 Instance", "compute"),
            "compute-group": ("EC2 Auto Scaling Group", "compute"),
            "compute-template": ("EC2 Launch Template", "compute"),
            "boot-image": ("Amazon Machine Image (AMI)", "compute"),
            "app-registry": ("Amazon ECR Repository", "config"),
            "registry-pull-identity": ("IAM Role / VM Registry Pull", "security"),
            "registry-pull-policy": (
                "AmazonEC2ContainerRegistryReadOnly Policy",
                "security",
            ),
            "registry-pull-binding": ("IAM Role Policy Attachment", "security"),
            "registry-instance-profile": ("IAM Instance Profile", "security"),
            "secret-ref": ("Existing AWS Secret", "config"),
            "secret-access-binding": ("IAM Role Policy / Secret Read", "security"),
            "state-secret-identity": ("IAM Role / State Secret Read", "security"),
            "state-secret-access-binding": (
                "IAM Role Policy / State Secret Read",
                "security",
            ),
            "state-secret-instance-profile": (
                "IAM Instance Profile / State VM",
                "security",
            ),
            "load-balancer": ("Network Load Balancer", "network"),
            "listener": ("Network Load Balancer Listener", "network"),
            "backend-group": ("Target Group", "network"),
            "health-check": ("Target Group / Health Check", "network"),
            "network": ("VPC", "network"),
            "subnet": ("Application Subnet", "network"),
            "state-subnet": ("State Subnet", "network"),
            "ingress-subnet": ("Ingress Subnet", "network"),
            "security-group": ("Security Group / Application", "network"),
            "load-balancer-security-group": ("Security Group / Load Balancer", "network"),
            "application-ingress-rule": (
                "Terraform: aws_vpc_security_group_ingress_rule / from Load Balancer",
                "network",
            ),
            "internet-gateway": ("Internet Gateway", "network"),
            "ingress-route-table": ("Route Table / ingress", "network"),
            "ingress-default-route": ("Terraform: aws_route / Internet Gateway", "network"),
            "ingress-route-association": (
                "Terraform: aws_route_table_association / ingress",
                "network",
            ),
            "application-route-table": ("Route Table / application", "network"),
            "application-default-route": ("Terraform: aws_route / NAT Gateway", "network"),
            "application-route-association": (
                "Terraform: aws_route_table_association / application",
                "network",
            ),
            "nat-public-ip": ("Elastic IP / NAT Gateway", "network"),
            "nat-gateway": ("NAT Gateway", "network"),
            "public-ip": ("Elastic IP", "network"),
            "disk": ("EBS Volume", "storage"),
            "disk-attachment": ("Terraform: aws_volume_attachment", "storage"),
        },
        "componentIds": {
            "nlb": "load-balancer",
            "listener": "listener",
            "target-group": "backend-group",
            "target-group-health": "health-check",
        },
        "officialAliases": {},
        "embeddedOwners": {
            "health-check": "backend-group",
        },
        "groupTopologyEdges": (
            ("compute-group", "compute-template", "uses"),
            ("compute-group", "subnet", "places instances in"),
            ("compute-template", "security-group", "uses"),
            ("compute-group", "backend-group", "registers instances with"),
            ("subnet", "network", "belongs to"),
            ("security-group", "network", "belongs to"),
        ),
        "standaloneEdges": (
            ("compute-instance", "subnet", "is placed in"),
            ("compute-instance", "security-group", "attaches"),
            ("subnet", "network", "belongs to"),
            ("security-group", "network", "belongs to"),
        ),
        "standaloneCapabilityEdges": (),
        "capabilityEdges": (
            ("listener", "load-balancer", "belongs to"),
            ("listener", "backend-group", "forwards to"),
            ("backend-group", "health-check", "evaluates targets with"),
        ),
    },
    "azure": {
        "resources": {
            "compute-instance": ("Linux Virtual Machine", "compute"),
            "compute-group": ("Virtual Machine Scale Set", "compute"),
            "resource-group": ("Resource Group", "config"),
            "boot-image": ("Virtual Machine Image", "compute"),
            "app-registry": ("Container Registry", "config"),
            "registry-pull-identity": ("User-assigned Managed Identity", "security"),
            "registry-pull-binding": ("AcrPull Role Assignment", "security"),
            "secret-ref": ("Existing Key Vault Secret", "config"),
            "secret-access-binding": ("Key Vault Secrets User Role Assignment", "security"),
            "state-secret-identity": (
                "User-assigned Managed Identity / State VM",
                "security",
            ),
            "state-secret-access-binding": (
                "Key Vault Secrets User Role Assignment / State VM",
                "security",
            ),
            "load-balancer": ("Load Balancer", "network"),
            "frontend-ip-config": (
                "Load Balancer / Frontend IP Configuration",
                "network",
            ),
            "backend-group": ("Backend Address Pool", "network"),
            "health-check": ("Probe", "network"),
            "routing-rule": ("Load Balancing Rule", "network"),
            "backend-membership": (
                "Network Interface Backend Address Pool Association",
                "network",
            ),
            "network": ("Virtual Network", "network"),
            "subnet": ("Application Subnet", "network"),
            "network-interface": ("Network Interface", "network"),
            "security-group": ("Network Security Group", "network"),
            "security-group-association": ("NIC Security Group Association", "network"),
            "public-ip": ("Public IP", "network"),
            "nat-public-ip": ("Public IP / NAT Gateway", "network"),
            "nat-gateway": ("NAT Gateway", "network"),
            "nat-association": (
                "Terraform: azurerm_subnet_nat_gateway_association",
                "network",
            ),
            "nat-public-ip-association": (
                "Terraform: azurerm_nat_gateway_public_ip_association",
                "network",
            ),
            "disk": ("Managed Disk", "storage"),
            "disk-attachment": (
                "Terraform: azurerm_virtual_machine_data_disk_attachment",
                "storage",
            ),
        },
        "componentIds": {
            "load-balancer": "load-balancer",
            "frontend-ip-config": "frontend-ip-config",
            "backend-pool": "backend-group",
            "probe": "health-check",
            "load-balancing-rule": "routing-rule",
            "backend-membership": "backend-membership",
        },
        "officialAliases": {},
        "embeddedOwners": {
            "frontend-ip-config": "load-balancer",
        },
        "groupTopologyEdges": (
            ("compute-group", "network-interface", "uses"),
            ("network-interface", "subnet", "is attached to"),
            ("network-interface", "security-group", "uses"),
            ("compute-group", "backend-membership", "joins"),
            ("subnet", "network", "belongs to"),
        ),
        "standaloneEdges": (
            ("compute-instance", "network-interface", "uses"),
            ("network-interface", "subnet", "is attached to"),
            ("security-group-association", "network-interface", "binds"),
            ("security-group-association", "security-group", "binds"),
            ("subnet", "network", "belongs to"),
        ),
        "standaloneCapabilityEdges": (
            ("network-interface", "backend-membership", "joins through"),
        ),
        "capabilityEdges": (
            ("frontend-ip-config", "load-balancer", "belongs to"),
            ("backend-group", "load-balancer", "belongs to"),
            ("health-check", "load-balancer", "belongs to"),
            ("routing-rule", "load-balancer", "belongs to"),
            ("routing-rule", "frontend-ip-config", "uses"),
            ("routing-rule", "backend-group", "routes to"),
            ("routing-rule", "health-check", "uses"),
            ("backend-membership", "backend-group", "registers instances with"),
        ),
    },
    "gcp": {
        "resources": {
            "compute-instance": ("Compute Engine VM", "compute"),
            "compute-group": ("Regional Managed Instance Group", "compute"),
            "backend-group": ("Unmanaged Instance Group", "compute"),
            "compute-template": ("Instance Template", "compute"),
            "boot-image": ("Compute Engine OS Image", "compute"),
            "app-registry": ("Artifact Registry Repository", "config"),
            "registry-pull-identity": ("Service Account / VM Registry Pull", "security"),
            "registry-pull-binding": (
                "Artifact Registry Reader IAM Member",
                "security",
            ),
            "secret-ref": ("Existing Secret Manager Secret", "config"),
            "secret-access-binding": ("Secret Manager Secret Accessor IAM Member", "security"),
            "state-secret-identity": (
                "Service Account / State Secret Read",
                "security",
            ),
            "state-secret-access-binding": (
                "Secret Manager Secret Accessor IAM Member / State VM",
                "security",
            ),
            "forwarding-rule": ("Forwarding Rule", "network"),
            "backend-service": ("Region Backend Service", "network"),
            "health-check": ("Region Health Check", "network"),
            "network": ("VPC Network", "network"),
            "subnet": ("Subnetwork", "network"),
            "cloud-router": ("Cloud Router", "network"),
            "cloud-nat": ("Cloud NAT", "network"),
            "network-interface": ("Compute resource / network_interface", "network"),
            "firewall": ("Firewall Rule", "network"),
            "public-ip": ("External IP Address", "network"),
            "disk": ("Persistent Disk", "storage"),
            "disk-attachment": ("Terraform: google_compute_attached_disk", "storage"),
        },
        "componentIds": {
            "forwarding-rule": "forwarding-rule",
            "backend-service": "backend-service",
            "instance-group": "backend-group",
            "health-check": "health-check",
        },
        "officialAliases": {"backend-group": "backend-group"},
        "embeddedOwners": {
            "network-interface": "compute-instance",
        },
        "groupTopologyEdges": (
            ("compute-group", "compute-template", "creates instances from"),
            ("compute-template", "network-interface", "uses"),
            ("network-interface", "subnet", "is attached to"),
            ("compute-group", "firewall", "is reached through"),
            ("subnet", "network", "belongs to"),
            ("firewall", "network", "belongs to"),
        ),
        "standaloneEdges": (
            ("compute-instance", "network-interface", "uses"),
            ("network-interface", "subnet", "is attached to"),
            ("subnet", "network", "belongs to"),
            ("firewall", "network", "belongs to"),
            ("firewall", "compute-instance", "allows traffic to"),
        ),
        "standaloneCapabilityEdges": (("backend-group", "compute-instance", "contains instance"),),
        "capabilityEdges": (
            ("forwarding-rule", "backend-service", "forwards to"),
            ("backend-service", "backend-group", "uses backend"),
            ("backend-service", "health-check", "checks with"),
        ),
    },
}


def _managed_l4_evidence(provider: str, enabled: bool) -> dict[str, Any]:
    """현재 선택한 관리형 L4 진입 경로에서 실제 관찰한 범위만 보고한다."""

    if not enabled:
        return {"status": "notApplicable", "evidenceRefs": []}

    selected_ingress = {
        "aws": "Network Load Balancer",
        "azure": "Load Balancer",
        "gcp": "Regional External Passthrough Network Load Balancer",
    }[provider]
    evidence_refs = {
        "aws": ["evaluation/dependency_audit/aws-managed-l4-ingress-result-20260817.json"],
        "azure": ["evaluation/dependency_audit/azure-managed-l4-ingress-result-20260817.json"],
        "gcp": ["evaluation/dependency_audit/gcp-managed-l4-ingress-result-20260817.json"],
    }[provider]
    return {
        "status": "observed",
        "evidenceRefs": evidence_refs,
        "selectedIngress": selected_ingress,
        "observed": [
            "L4 forwarding",
            "HTTP health checks",
            "two backend reachability",
            "backend process fault exclusion and operator-triggered restoration",
            "run-owned cleanup with zero residual resources",
        ],
        "notMeasured": [
            "availability SLA",
            "performance",
            "managed VM replacement",
        ],
    }


_OFFICIAL_ALIASES = {
    "load-balancer": "load-balancer",
    "backend-group": "backend-group",
    "virtual-network": "network",
    "network-interface": "network-interface",
    "security-group": "security-group",
}


_TERRAFORM_TYPES: dict[str, dict[str, tuple[str, ...]]] = {
    "aws": {
        "compute-instance": ("aws_instance",),
        "compute-group": ("aws_autoscaling_group",),
        "compute-template": ("aws_launch_template",),
        "app-registry": ("aws_ecr_repository",),
        "registry-pull-identity": ("aws_iam_role",),
        "registry-pull-binding": ("aws_iam_role_policy_attachment",),
        "registry-instance-profile": ("aws_iam_instance_profile",),
        "secret-access-binding": ("aws_iam_role_policy",),
        "state-secret-identity": ("aws_iam_role",),
        "state-secret-access-binding": ("aws_iam_role_policy",),
        "state-secret-instance-profile": ("aws_iam_instance_profile",),
        "load-balancer": ("aws_lb",),
        "listener": ("aws_lb_listener",),
        "backend-group": ("aws_lb_target_group",),
        "network": ("aws_vpc",),
        "subnet": ("aws_subnet",),
        "state-subnet": ("aws_subnet",),
        "ingress-subnet": ("aws_subnet",),
        "security-group": ("aws_security_group",),
        "load-balancer-security-group": ("aws_security_group",),
        "application-ingress-rule": ("aws_vpc_security_group_ingress_rule",),
        "internet-gateway": ("aws_internet_gateway",),
        "ingress-route-table": ("aws_route_table",),
        "ingress-default-route": ("aws_route",),
        "ingress-route-association": ("aws_route_table_association",),
        "application-route-table": ("aws_route_table",),
        "application-default-route": ("aws_route",),
        "application-route-association": ("aws_route_table_association",),
        "nat-public-ip": ("aws_eip",),
        "nat-gateway": ("aws_nat_gateway",),
        "public-ip": ("aws_eip",),
        "disk": ("aws_ebs_volume",),
        "disk-attachment": ("aws_volume_attachment",),
    },
    "azure": {
        "compute-instance": ("azurerm_linux_virtual_machine",),
        "compute-group": ("azurerm_linux_virtual_machine_scale_set",),
        "resource-group": ("azurerm_resource_group",),
        "app-registry": ("azurerm_container_registry",),
        "registry-pull-identity": ("azurerm_user_assigned_identity",),
        "registry-pull-binding": ("azurerm_role_assignment",),
        "secret-access-binding": ("azurerm_role_assignment",),
        "state-secret-identity": ("azurerm_user_assigned_identity",),
        "state-secret-access-binding": ("azurerm_role_assignment",),
        "load-balancer": ("azurerm_lb",),
        "backend-group": ("azurerm_lb_backend_address_pool",),
        "health-check": ("azurerm_lb_probe",),
        "routing-rule": ("azurerm_lb_rule",),
        "network": ("azurerm_virtual_network",),
        "subnet": ("azurerm_subnet",),
        "network-interface": ("azurerm_network_interface",),
        "security-group": ("azurerm_network_security_group",),
        "security-group-association": ("azurerm_network_interface_security_group_association",),
        "backend-membership": ("azurerm_network_interface_backend_address_pool_association",),
        "public-ip": ("azurerm_public_ip",),
        "nat-public-ip": ("azurerm_public_ip",),
        "nat-gateway": ("azurerm_nat_gateway",),
        "nat-association": ("azurerm_subnet_nat_gateway_association",),
        "nat-public-ip-association": ("azurerm_nat_gateway_public_ip_association",),
        "disk": ("azurerm_managed_disk",),
        "disk-attachment": ("azurerm_virtual_machine_data_disk_attachment",),
    },
    "gcp": {
        "compute-instance": ("google_compute_instance",),
        "compute-group": ("google_compute_region_instance_group_manager",),
        "backend-group": ("google_compute_instance_group",),
        "compute-template": ("google_compute_instance_template",),
        "app-registry": ("google_artifact_registry_repository",),
        "registry-pull-identity": ("google_service_account",),
        "registry-pull-binding": ("google_artifact_registry_repository_iam_member",),
        "secret-access-binding": ("google_secret_manager_secret_iam_member",),
        "state-secret-identity": ("google_service_account",),
        "state-secret-access-binding": ("google_secret_manager_secret_iam_member",),
        "forwarding-rule": ("google_compute_forwarding_rule",),
        "backend-service": ("google_compute_region_backend_service",),
        "health-check": ("google_compute_region_health_check",),
        "network": ("google_compute_network",),
        "subnet": ("google_compute_subnetwork",),
        "cloud-router": ("google_compute_router",),
        "cloud-nat": ("google_compute_router_nat",),
        "firewall": ("google_compute_firewall",),
        "public-ip": ("google_compute_address",),
        "disk": ("google_compute_disk",),
        "disk-attachment": ("google_compute_attached_disk",),
    },
}


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "workload"


def _logical_workloads(logical_model: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Select explicit deployable nodes; BCE classes alone never create a workload."""
    model = logical_model or {}
    nodes = list(model.get("Nodes") or model.get("nodes") or [])
    artifacts = list(model.get("Artifacts") or model.get("artifacts") or [])

    def state_mode(node: dict[str, Any]) -> str:
        explicit = str(node.get("stateMode") or node.get("state_mode") or "").strip()
        if explicit:
            return explicit
        # Compatibility for stored models produced before stateMode existed.
        return "persistent" if str(node.get("kind") or "").strip().lower() == "database" else "none"

    def owns_artifact(node: dict[str, Any]) -> bool:
        name = str(node.get("name") or "")
        return any(
            str(item.get("deployed_on") or item.get("deployedOn") or "") == name
            for item in artifacts
        )

    candidates = [
        node
        for node in nodes
        if owns_artifact(node)
        or str(node.get("kind") or "").strip().lower() == "executionenvironment"
        or state_mode(node) in {"ephemeral", "persistent"}
    ]
    names = {str(node.get("name") or "") for node in candidates}
    workloads: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, node in enumerate(candidates, start=1):
        name = str(node.get("name") or f"Workload {index}")
        parent = str(node.get("parent") or "")
        # A nested runtime is part of its deployable parent unless it owns an artifact.
        if parent in names and not owns_artifact(node):
            continue
        base = f"workload-{_slug(name)}"
        workload_id = base
        suffix = 2
        while workload_id in used_ids:
            workload_id = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(workload_id)
        workloads.append(
            {
                "id": workload_id,
                "name": name,
                "designKind": str(node.get("kind") or "node"),
                "stateMode": state_mode(node),
                "sourceRefs": sorted(str(item) for item in node.get("source_classes") or []),
            }
        )
    if workloads:
        return workloads
    return [
        {
            "id": "workload-application",
            "name": "Application workload",
            "designKind": "executionEnvironment",
            "stateMode": "none",
            "sourceRefs": ["system-scope:docker-on-vm"],
        }
    ]


def _supported_container_runtime(image: str) -> dict[str, Any] | None:
    """Return a bounded runtime contract only for an explicitly selected image.

    This registry describes the currently supported self-hosted state runtime.  It
    does not infer a database engine merely because a design node is named database.
    """
    repository = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0].lower()
    if repository != "postgres":
        return None
    return {
        "kind": "container",
        "image": image,
        "containerPort": 5432,
        "dataPath": "/var/lib/postgresql/data",
        "requiredConfiguration": [
            {"name": "POSTGRES_DB", "sensitive": False},
            {"name": "POSTGRES_USER", "sensitive": True},
            {"name": "POSTGRES_PASSWORD", "sensitive": True},
        ],
        "basis": "supported-runtime:postgres-official-image",
    }


def bind_application_runtime(
    resource_plan: dict[str, Any], application_contract: dict[str, Any]
) -> dict[str, Any]:
    """Resolve implementation-observed application values in a ResourcePlan copy."""
    plan = copy.deepcopy(resource_plan)
    facts = list(application_contract.get("facts") or [])
    port_fact = next((item for item in facts if item.get("kind") == "runtime.port"), None)
    health_fact = next((item for item in facts if item.get("kind") == "runtime.health"), None)
    port = (port_fact or {}).get("attributes", {}).get("port")
    health_path = (health_fact or {}).get("attributes", {}).get("path")
    primary_compute = plan.get("computeNodeId")
    primary_workload_id = next(
        (
            item.get("workloadRef")
            for item in plan.get("allocations") or []
            if item.get("computeRef") == primary_compute
        ),
        None,
    )
    for workload in plan.get("workloads") or []:
        if workload.get("id") != primary_workload_id:
            continue
        runtime = dict(workload.get("runtime") or {})
        runtime.update(
            {
                "kind": "container",
                "artifactRef": "implementation.application-image",
                "containerPort": port if port is not None else "runtimeDerived",
                "basis": "ApplicationRuntimeContract/v1",
            }
        )
        if health_path:
            runtime["healthPath"] = health_path
        workload["runtime"] = runtime
    by_id = {item.get("id"): item for item in plan.get("nodes") or []}
    for workload in plan.get("workloads") or []:
        node = by_id.get(workload.get("id"))
        if node is not None:
            node["runtime"] = copy.deepcopy(workload.get("runtime") or {})
    provider = str(plan.get("provider") or "")
    load_balanced = (plan.get("deploymentTopology") or {}).get("publicIngress") == "loadBalanced"
    host_port = 80 if provider == "gcp" and load_balanced else port
    for node in plan.get("nodes") or []:
        provider_kind = str(node.get("providerKind") or "")
        if provider_kind in {"listener", "routing-rule", "forwarding-rule"}:
            node["backendPort"] = host_port if host_port is not None else "runtimeDerived"
            if provider_kind != "forwarding-rule":
                node["frontendPort"] = 80
        if provider_kind == "health-check":
            node["port"] = host_port if host_port is not None else "runtimeDerived"
            node["requestPath"] = health_path or "runtimeDerived"
        if node.get("group") == "endpoint":
            node["port"] = 80 if load_balanced else (port if port is not None else "runtimeDerived")
        traffic_policy = node.get("trafficPolicy")
        if isinstance(traffic_policy, dict) and node.get("id") != "load-balancer-security-group":
            traffic_policy["port"] = host_port if host_port is not None else "runtimeDerived"
    unresolved = [
        item
        for item in plan.get("unresolved") or []
        if item.get("field") != f"workloads.{primary_workload_id}.runtime"
    ]
    if port is None:
        unresolved.append(
            {
                "field": f"workloads.{primary_workload_id}.runtime.containerPort",
                "reason": "The generated application did not expose a runtime port contract.",
            }
        )
    plan["unresolved"] = unresolved
    return plan


def _connection_edges(
    logical_model: dict[str, Any] | None,
    workloads: list[dict[str, Any]],
    provider: str,
) -> list[dict[str, Any]]:
    model = logical_model or {}
    by_name = {item["name"]: item["id"] for item in workloads}
    edges: list[dict[str, Any]] = []
    for connection in model.get("Connections") or model.get("connections") or []:
        source = by_name.get(str(connection.get("source") or ""))
        target = by_name.get(str(connection.get("target") or ""))
        if not source or not target or source == target:
            continue
        protocol = str(connection.get("protocol") or "").strip()
        edges.append(
            {
                "from": source,
                "to": target,
                "label": protocol or "connects to",
                "relation": "connectsTo",
                "evidence": ["design-connection"],
                "runtimeBinding": {
                    "targetEndpoint": "runtimeDerived",
                    "onTargetReplacement": "updateConfiguration",
                    "applicationImageRebuildRequired": False,
                    "privateNetworkPathRequired": True,
                    "trafficFilterRequired": True,
                    "evidenceRefs": [f"experiment:E1/{provider}"],
                },
            }
        )
    return edges


def _external_endpoints(
    logical_model: dict[str, Any] | None,
    workloads: list[dict[str, Any]],
    *,
    force_http: bool = False,
) -> list[dict[str, Any]]:
    model = logical_model or {}
    workload_by_name = {item["name"]: item["id"] for item in workloads}
    endpoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for connection in model.get("Connections") or model.get("connections") or []:
        source_name = str(connection.get("source") or "")
        target_name = str(connection.get("target") or "")
        target = workload_by_name.get(target_name)
        if source_name in workload_by_name or not target:
            continue
        protocol = (
            "http"
            if force_http
            else str(connection.get("protocol") or "").strip().lower() or "unspecified"
        )
        key = (target, protocol)
        if key in seen:
            continue
        seen.add(key)
        endpoints.append(
            {
                "id": f"endpoint-{_slug(target_name)}-{_slug(protocol)}",
                "name": f"Public {protocol.upper()} endpoint",
                "targetWorkloadRef": target,
                "protocol": protocol,
                "exposure": "public",
                "sourceRef": source_name,
            }
        )
    return endpoints


def _add_edge(
    edges: dict[tuple[str, str, str], dict[str, Any]],
    source: str,
    target: str,
    label: str,
    evidence: str,
) -> None:
    if source == target:
        return
    key = (source, target, label)
    existing = edges.get(key)
    pair_keys = [
        candidate for candidate in edges if candidate[0] == source and candidate[1] == target
    ]
    if label == "depends on" and pair_keys:
        existing = edges[pair_keys[0]]
        if evidence not in existing["evidence"]:
            existing["evidence"].append(evidence)
        return
    generic_key = (source, target, "depends on")
    if label != "depends on" and generic_key in edges:
        generic = edges.pop(generic_key)
        existing = edges.get(key)
        if existing is None:
            generic["label"] = label
            edges[key] = generic
            existing = generic
        for generic_evidence in generic["evidence"]:
            if generic_evidence not in existing["evidence"]:
                existing["evidence"].append(generic_evidence)
    if existing is None:
        edges[key] = {
            "from": source,
            "to": target,
            "label": label,
            "evidence": [evidence],
        }
        return
    if evidence not in existing["evidence"]:
        existing["evidence"].append(evidence)


def validate_resource_plan_structure(plan: dict[str, Any]) -> None:
    """Reject malformed plans before either diagrams or IaC can consume them."""
    if plan.get("schemaVersion") != "easydep-resource-plan/v1":
        raise ValueError("ResourcePlan schemaVersion is missing or unsupported.")
    nodes = list(plan.get("nodes") or [])
    node_ids = [str(node.get("id") or "") for node in nodes]
    if any(not node_id for node_id in node_ids):
        raise ValueError("ResourcePlan contains an empty node id.")
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("ResourcePlan node ids must be unique.")
    known = set(node_ids)
    if str(plan.get("computeNodeId") or "") not in known:
        raise ValueError("ResourcePlan computeNodeId does not reference a node.")
    for node in nodes:
        handling = str(node.get("handling") or "")
        if handling == "create" and not node.get("terraformTypes"):
            raise ValueError(f"Create node {node.get('id')} has no Terraform resource type.")
        if handling == "configureInsideOwner":
            owner = str(node.get("ownerRef") or "")
            if owner not in known:
                raise ValueError(f"Embedded node {node.get('id')} has no valid ownerRef.")
        minimum_count = node.get("minimumCount", 1)
        if (
            isinstance(minimum_count, bool)
            or not isinstance(minimum_count, int)
            or minimum_count < 1
        ):
            raise ValueError(f"Node {node.get('id')} has an invalid minimumCount.")
    edge_keys: set[tuple[str, str, str]] = set()
    connected_node_ids: set[str] = set()
    for edge in plan.get("edges") or []:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        label = str(edge.get("label") or "")
        if source not in known or target not in known:
            raise ValueError(f"ResourcePlan edge has a dangling endpoint: {source}->{target}.")
        if source == target:
            raise ValueError(f"ResourcePlan contains a self edge: {source}.")
        key = (source, target, label)
        if key in edge_keys:
            raise ValueError(f"ResourcePlan contains a duplicate edge: {key}.")
        edge_keys.add(key)
        connected_node_ids.update((source, target))
    disconnected_provider_nodes = sorted(
        str(node.get("id") or "")
        for node in nodes
        if node.get("entityClass") in {"providerResource", "providerComponent", "externalArtifact"}
        and str(node.get("id") or "") not in connected_node_ids
    )
    if disconnected_provider_nodes:
        raise ValueError(
            "ResourcePlan contains disconnected provider nodes: "
            + ", ".join(disconnected_provider_nodes)
            + "."
        )
    workload_ids = {str(item.get("id") or "") for item in plan.get("workloads") or []}
    for connection in plan.get("connections") or []:
        if {
            str(connection.get("from") or ""),
            str(connection.get("to") or ""),
        } - workload_ids:
            raise ValueError("ResourcePlan connection has an invalid workload reference.")
    compute_pools = list(plan.get("computePools") or [])
    pool_ids = {str(item.get("id") or "") for item in compute_pools}
    if not pool_ids or len(pool_ids) != len(compute_pools):
        raise ValueError("ResourcePlan compute pool ids must be non-empty and unique.")
    for pool in compute_pools:
        if str(pool.get("computeRef") or "") not in known:
            raise ValueError("ResourcePlan compute pool does not reference a compute node.")
    for allocation in plan.get("allocations") or []:
        workload_ref = str(allocation.get("workloadRef") or "")
        compute_ref = str(allocation.get("computeRef") or "")
        compute_pool_ref = str(allocation.get("computePoolRef") or "")
        if (
            workload_ref not in workload_ids
            or compute_ref not in known
            or compute_pool_ref not in pool_ids
        ):
            raise ValueError(
                f"ResourcePlan allocation has an invalid reference: {workload_ref}->{compute_ref}."
            )
        replicas = allocation.get("replicas", 1)
        if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1:
            raise ValueError(
                f"ResourcePlan allocation has an invalid replica count: {workload_ref}."
            )
    topology = dict(plan.get("deploymentTopology") or {})
    zones = [str(zone) for zone in topology.get("selectedZones") or []]
    if len(zones) != len(set(zones)):
        raise ValueError("DeploymentTopology selectedZones must be distinct.")
    if topology.get("zoneLayout") == "multiZoneSpread" and len(set(zones)) < 2:
        raise ValueError("A multi-zone ResourcePlan requires at least two distinct zones.")


def resource_plan_digest(plan: dict[str, Any]) -> str:
    validate_resource_plan_structure(plan)
    canonical = json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def resource_plan_structure_digest(plan: dict[str, Any]) -> str:
    """Hash the provider graph while excluding later runtime-contract bindings."""
    validate_resource_plan_structure(plan)
    provider_classes = {"providerResource", "providerComponent", "externalArtifact"}
    nodes = [
        {
            key: node.get(key)
            for key in (
                "id",
                "providerKind",
                "entityClass",
                "handling",
                "terraformTypes",
                "ownerRef",
                "minimumCount",
            )
            if node.get(key) is not None
        }
        for node in plan.get("nodes") or []
        if node.get("entityClass") in provider_classes
    ]
    node_ids = {str(node.get("id") or "") for node in nodes}
    edges = [
        {
            "from": edge.get("from"),
            "to": edge.get("to"),
            "label": edge.get("label"),
        }
        for edge in plan.get("edges") or []
        if str(edge.get("from") or "") in node_ids and str(edge.get("to") or "") in node_ids
    ]
    structural = {
        "provider": plan.get("provider"),
        "region": plan.get("region"),
        "computeNodeId": plan.get("computeNodeId"),
        "deploymentTopology": plan.get("deploymentTopology"),
        "placementConstraints": plan.get("placementConstraints"),
        "nodes": sorted(nodes, key=lambda item: str(item.get("id") or "")),
        "edges": sorted(
            edges,
            key=lambda item: (
                str(item.get("from") or ""),
                str(item.get("to") or ""),
                str(item.get("label") or ""),
            ),
        ),
    }
    canonical = json.dumps(
        structural,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_provider_deployment_model(
    *,
    provider: str,
    region: str,
    dependency_plan: dict[str, Any],
    projection_policy: dict[str, Any],
    topology_policy: dict[str, Any] | None = None,
    logical_deployment_model: dict[str, Any] | None = None,
    persistent_storage_required: bool = False,
    persistence_source_refs: list[str] | None = None,
    runtime_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common source consumed by the diagram and IaC stages.

    The design model contributes only explicit deployable nodes and connections.
    Provider resources still come from the selected provider projection and DepKB.
    """
    spec = _PROVIDER_MODELS[provider]
    topology_policy = topology_policy or {}
    mode = projection_policy.get("mode")
    managed = mode == "managedGroup"
    compute_node = "compute-group" if managed else "compute-instance"
    selected: set[str] = {compute_node, "network", "subnet"}
    selected.add("firewall" if provider == "gcp" else "security-group")
    if provider in {"azure", "gcp"} and not managed:
        selected.add("network-interface")
    if provider == "azure" and not managed:
        selected.add("security-group-association")
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    for realization in dependency_plan.get("capabilityRealizations") or []:
        component_ids = {
            spec["componentIds"].get(str(component.get("id") or ""))
            for component in realization.get("components") or []
        }
        selected.update(item for item in component_ids if item)
    if provider == "gcp" and managed and "backend-group" in selected:
        selected.discard("backend-group")
        selected.add("compute-group")
    for source, target, label in spec["capabilityEdges"]:
        if provider == "gcp" and managed and target == "backend-group":
            target = "compute-group"
        if source in selected and target in selected:
            _add_edge(edges, source, target, label, "capability-realization")
    for dependency in dependency_plan.get("officialDependencies") or []:
        provider_aliases = spec["officialAliases"]
        from_id = str(dependency.get("from") or "")
        to_id = str(dependency.get("to") or "")
        aliases = {**_OFFICIAL_ALIASES, "vm": compute_node}
        source = provider_aliases.get(from_id, aliases.get(from_id, from_id))
        target = provider_aliases.get(to_id, aliases.get(to_id, to_id))
        if provider == "gcp" and managed and target == "backend-group":
            target = "compute-group"
        if (
            provider == "gcp"
            and managed
            and source == "compute-group"
            and target == "network-interface"
        ):
            source = "compute-template"
        if (
            provider == "aws"
            and managed
            and source == "compute-group"
            and target == "security-group"
        ):
            source = "compute-template"
        if (
            provider == "aws"
            and topology_policy.get("publicIngress") == "loadBalanced"
            and source == "load-balancer"
            and target == "subnet"
        ):
            target = "ingress-subnet"
        if source in spec["resources"] and target in spec["resources"]:
            selected.update((source, target))
            _add_edge(edges, source, target, "depends on", "official-dependency")

    plan_aliases = {
        **_OFFICIAL_ALIASES,
        "vm": compute_node,
        "network": "network",
        "nic": "network-interface",
    }
    for dependency in dependency_plan.get("edges") or []:
        source = plan_aliases.get(str(dependency.get("from") or ""), "")
        target = plan_aliases.get(str(dependency.get("to") or ""), "")
        if source in spec["resources"] and target in spec["resources"]:
            selected.update((source, target))
            _add_edge(edges, source, target, "depends on", "dependency-plan")

    grouped_compute = compute_node == "compute-group"
    workload_layout = str(topology_policy.get("workloadLayout") or "primaryOnly")
    persistent_workload_present = workload_layout != "primaryOnly"
    isolated_persistent = workload_layout == "isolatedPersistent"
    registry_nodes = {
        "boot-image",
        "app-registry",
        "registry-pull-identity",
        "registry-pull-binding",
    }
    if provider == "aws":
        registry_nodes.update({"registry-pull-policy", "registry-instance-profile"})
    if provider == "azure":
        registry_nodes.add("resource-group")
    selected.update(registry_nodes)
    image_owner = "compute-template" if provider in {"aws", "gcp"} and managed else compute_node
    _add_edge(edges, image_owner, "boot-image", "uses image", "boot-image-policy")
    _add_edge(
        edges,
        "registry-pull-binding",
        "registry-pull-identity",
        "grants pull access to",
        "registry-policy",
    )
    _add_edge(
        edges,
        "registry-pull-binding",
        "app-registry",
        "scopes pull access to",
        "registry-policy",
    )
    if provider == "aws":
        _add_edge(
            edges,
            "registry-pull-binding",
            "registry-pull-policy",
            "uses policy",
            "registry-policy",
        )
        _add_edge(
            edges,
            "registry-instance-profile",
            "registry-pull-identity",
            "contains role",
            "registry-policy",
        )
        _add_edge(
            edges,
            image_owner,
            "registry-instance-profile",
            "uses identity",
            "registry-policy",
        )
    else:
        _add_edge(
            edges,
            image_owner,
            "registry-pull-identity",
            "uses identity",
            "registry-policy",
        )
    _add_edge(
        edges,
        image_owner,
        "app-registry",
        "pulls image digest from",
        "registry-policy",
    )
    if persistent_workload_present:
        selected.update({"secret-ref", "secret-access-binding"})
        _add_edge(
            edges,
            "secret-access-binding",
            "secret-ref",
            "scopes secret read to",
            "secret-policy",
        )
        _add_edge(
            edges,
            "secret-access-binding",
            "registry-pull-identity",
            "grants secret read to",
            "secret-policy",
        )
        _add_edge(
            edges,
            image_owner,
            ("registry-instance-profile" if provider == "aws" else "registry-pull-identity"),
            "uses secret identity",
            "secret-policy",
        )
        if isolated_persistent:
            selected.update({"state-secret-identity", "state-secret-access-binding"})
            _add_edge(
                edges,
                "state-secret-access-binding",
                "secret-ref",
                "scopes secret read to",
                "secret-policy",
            )
            _add_edge(
                edges,
                "state-secret-access-binding",
                "state-secret-identity",
                "grants secret read to",
                "secret-policy",
            )
            if provider == "aws":
                selected.add("state-secret-instance-profile")
                _add_edge(
                    edges,
                    "state-secret-instance-profile",
                    "state-secret-identity",
                    "contains role",
                    "secret-policy",
                )
    topology_edges = spec["groupTopologyEdges"] if grouped_compute else spec["standaloneEdges"]
    for source, target, label in topology_edges:
        selected.update((source, target))
        _add_edge(
            edges,
            source,
            target,
            label,
            "topology-decision",
        )
    load_balanced_topology = topology_policy.get("publicIngress") == "loadBalanced"
    private_egress_required = load_balanced_topology or isolated_persistent
    if provider == "aws":
        selected.update(
            {
                "internet-gateway",
                "ingress-route-table",
                "ingress-default-route",
                "ingress-route-association",
            }
        )
        _add_edge(edges, "internet-gateway", "network", "attaches", "egress-policy")
        _add_edge(edges, "ingress-route-table", "network", "belongs to", "egress-policy")
        _add_edge(edges, "ingress-default-route", "ingress-route-table", "uses", "egress-policy")
        _add_edge(edges, "ingress-default-route", "internet-gateway", "routes to", "egress-policy")
        if private_egress_required:
            selected.update(
                {
                    "application-route-table",
                    "application-default-route",
                    "application-route-association",
                    "nat-public-ip",
                    "nat-gateway",
                }
            )
            egress_public_subnet = "ingress-subnet" if load_balanced_topology else "subnet"
            egress_private_subnet = "subnet"
            if not load_balanced_topology and isolated_persistent:
                selected.add("state-subnet")
                egress_private_subnet = "state-subnet"
                _add_edge(edges, "state-subnet", "network", "belongs to", "egress-policy")
            if load_balanced_topology:
                selected.update(
                    {
                        "ingress-subnet",
                        "load-balancer-security-group",
                        "application-ingress-rule",
                    }
                )
                _add_edge(edges, "ingress-subnet", "network", "belongs to", "ingress-policy")
                _add_edge(edges, "load-balancer", "ingress-subnet", "uses", "ingress-policy")
                _add_edge(
                    edges,
                    "load-balancer",
                    "load-balancer-security-group",
                    "uses",
                    "ingress-policy",
                )
                _add_edge(
                    edges,
                    "load-balancer-security-group",
                    "network",
                    "belongs to",
                    "ingress-policy",
                )
                _add_edge(
                    edges,
                    "application-ingress-rule",
                    "security-group",
                    "binds",
                    "ingress-policy",
                )
                _add_edge(
                    edges,
                    "application-ingress-rule",
                    "load-balancer-security-group",
                    "uses",
                    "ingress-policy",
                )
            _add_edge(
                edges, "ingress-route-association", egress_public_subnet, "binds", "egress-policy"
            )
            _add_edge(
                edges,
                "ingress-route-association",
                "ingress-route-table",
                "binds",
                "egress-policy",
            )
            _add_edge(edges, "nat-gateway", egress_public_subnet, "is placed in", "egress-policy")
            _add_edge(edges, "nat-gateway", "nat-public-ip", "uses address", "egress-policy")
            _add_edge(edges, "application-route-table", "network", "belongs to", "egress-policy")
            _add_edge(
                edges,
                "application-default-route",
                "application-route-table",
                "uses",
                "egress-policy",
            )
            _add_edge(
                edges, "application-default-route", "nat-gateway", "routes to", "egress-policy"
            )
            _add_edge(
                edges,
                "application-route-association",
                egress_private_subnet,
                "binds",
                "egress-policy",
            )
            _add_edge(
                edges,
                "application-route-association",
                "application-route-table",
                "binds",
                "egress-policy",
            )
        else:
            _add_edge(edges, "ingress-route-association", "subnet", "binds", "egress-policy")
            _add_edge(
                edges,
                "ingress-route-association",
                "ingress-route-table",
                "binds",
                "egress-policy",
            )
    elif provider == "azure" and private_egress_required:
        selected.update(
            {
                "nat-public-ip",
                "nat-gateway",
                "nat-association",
                "nat-public-ip-association",
            }
        )
        if load_balanced_topology:
            selected.add("frontend-ip-config")
        _add_edge(edges, "nat-public-ip-association", "nat-gateway", "binds", "egress-policy")
        _add_edge(edges, "nat-public-ip-association", "nat-public-ip", "binds", "egress-policy")
        _add_edge(edges, "nat-association", "nat-gateway", "binds", "egress-policy")
        _add_edge(edges, "nat-association", "subnet", "binds", "egress-policy")
    elif provider == "gcp" and private_egress_required:
        selected.update({"cloud-router", "cloud-nat"})
        _add_edge(edges, "cloud-router", "network", "belongs to", "egress-policy")
        _add_edge(edges, "cloud-nat", "cloud-router", "uses", "egress-policy")
        _add_edge(edges, "cloud-nat", "subnet", "selects subnetwork", "egress-policy")
    if not grouped_compute:
        for source, target, label in spec["standaloneCapabilityEdges"]:
            if source in selected and target in selected:
                _add_edge(edges, source, target, label, "capability-realization")
    elif provider == "azure" and load_balanced_topology:
        # VMSS NIC configuration references the backend pool directly. There is no
        # standalone association resource for scale-set instances.
        selected.discard("backend-membership")
        edges = {
            key: edge
            for key, edge in edges.items()
            if "backend-membership" not in {str(edge["from"]), str(edge["to"])}
        }
        _add_edge(
            edges,
            "compute-group",
            "backend-group",
            "joins through",
            "capability-realization",
        )

    workloads = _logical_workloads(logical_deployment_model)
    runtime_hints = runtime_hints or {}
    application_hint = dict(runtime_hints.get("application") or {})
    state_hint = dict(runtime_hints.get("state") or {})
    persistent_candidates = [item for item in workloads if item.get("stateMode") == "persistent"]
    persistence_owner = (
        persistent_candidates[0]
        if persistent_storage_required and len(persistent_candidates) == 1
        else workloads[0]
        if persistent_storage_required and len(workloads) == 1
        else None
    )

    # The first non-persistent workload is the public tier. The selected compute
    # topology applies to that tier only; explicit persistent state remains singleton.
    primary_workload = next(
        (item for item in workloads if item.get("stateMode") != "persistent"),
        workloads[0],
    )
    external_endpoints = _external_endpoints(
        logical_deployment_model,
        workloads,
        force_http=bool((topology_policy.get("publicEndpoint") or {}).get("required")),
    )
    if not external_endpoints and (topology_policy.get("publicEndpoint") or {}).get("required"):
        external_endpoints = [
            {
                "id": f"endpoint-{_slug(primary_workload['name'])}-http",
                "name": "Public HTTP endpoint",
                "targetWorkloadRef": primary_workload["id"],
                "protocol": "http",
                "exposure": "public",
                "sourceRef": "system-scope:public-endpoint",
            }
        ]
    primary_workload["runtime"] = {
        "kind": "container",
        "artifactRef": "implementation.application-image",
        "containerPort": application_hint.get("containerPort", "runtimeDerived"),
        "healthPath": application_hint.get("healthPath", "runtimeDerived"),
        "configurationInputs": list(application_hint.get("configurationInputs") or []),
        "basis": "requirements-runtime-hint",
    }
    for workload in workloads:
        if workload is primary_workload:
            continue
        if workload.get("stateMode") != "persistent":
            continue
        image = str(state_hint.get("image") or "").strip()
        supported = _supported_container_runtime(image) if image else None
        if supported is not None:
            if state_hint.get("basis"):
                supported["selectionBasis"] = state_hint["basis"]
            workload["runtime"] = supported
    allocations: list[dict[str, Any]] = []
    extra_compute_nodes: list[dict[str, Any]] = []
    extra_provider_edges: list[tuple[str, str, str]] = []
    for workload in workloads:
        if workload["id"] == primary_workload["id"]:
            target_compute = compute_node
            replicas = int(projection_policy.get("minimumInstances") or 1)
        elif workload.get("stateMode") == "persistent" and workload_layout == "colocatedPersistent":
            target_compute = compute_node
            replicas = 1
        else:
            target_compute = f"compute-{workload['id'].removeprefix('workload-')}"
            replicas = 1
            extra_compute_nodes.append(
                {
                    "id": target_compute,
                    "name": spec["resources"]["compute-instance"][0],
                    "group": "compute",
                    "providerKind": "compute-instance",
                    "entityClass": "providerResource",
                    "handling": "create",
                    "terraformTypes": list(
                        _TERRAFORM_TYPES.get(provider, {}).get("compute-instance", ())
                    ),
                    "logicalRef": workload["id"],
                    "placement": {
                        "region": region,
                        "zonePolicy": "oneSelectedZone",
                    },
                    "privateAddressAllocation": "static",
                    "privateAddress": (
                        "10.80.30.10"
                        if provider == "aws" and not load_balanced_topology
                        else "10.80.10.20"
                    ),
                    "publicAddress": False,
                    "bootImageRef": "boot-image",
                }
            )
            suffix = workload["id"].removeprefix("workload-")
            if provider == "aws":
                filter_id = f"security-group-{suffix}"
                extra_compute_nodes.append(
                    {
                        "id": filter_id,
                        "name": spec["resources"]["security-group"][0],
                        "group": "network",
                        "providerKind": "security-group",
                        "entityClass": "providerResource",
                        "handling": "create",
                        "terraformTypes": list(_TERRAFORM_TYPES[provider]["security-group"]),
                        "logicalRef": workload["id"],
                    }
                )
                extra_provider_edges.extend(
                    [
                        (
                            target_compute,
                            (
                                "state-subnet"
                                if not load_balanced_topology and isolated_persistent
                                else "subnet"
                            ),
                            "is placed in",
                        ),
                        (target_compute, filter_id, "attaches"),
                        (filter_id, "network", "belongs to"),
                    ]
                )
            elif provider == "azure":
                nic_id = f"network-interface-{suffix}"
                filter_id = f"security-group-{suffix}"
                association_id = f"security-group-association-{suffix}"
                for node_id, provider_kind in (
                    (nic_id, "network-interface"),
                    (filter_id, "security-group"),
                    (association_id, "security-group-association"),
                ):
                    extra_compute_nodes.append(
                        {
                            "id": node_id,
                            "name": spec["resources"][provider_kind][0],
                            "group": "network",
                            "providerKind": provider_kind,
                            "entityClass": "providerResource",
                            "handling": "create",
                            "terraformTypes": list(_TERRAFORM_TYPES[provider][provider_kind]),
                            "logicalRef": workload["id"],
                            **(
                                {
                                    "privateAddressAllocation": "static",
                                    "privateAddress": "10.80.10.20",
                                }
                                if provider_kind == "network-interface"
                                else {}
                            ),
                        }
                    )
                extra_provider_edges.extend(
                    [
                        (target_compute, nic_id, "uses"),
                        (nic_id, "subnet", "is attached to"),
                        (association_id, nic_id, "binds"),
                        (association_id, filter_id, "binds"),
                    ]
                )
            else:
                filter_id = f"firewall-{suffix}"
                extra_compute_nodes.append(
                    {
                        "id": filter_id,
                        "name": spec["resources"]["firewall"][0],
                        "group": "network",
                        "providerKind": "firewall",
                        "entityClass": "providerResource",
                        "handling": "create",
                        "terraformTypes": list(_TERRAFORM_TYPES[provider]["firewall"]),
                        "logicalRef": workload["id"],
                    }
                )
                extra_provider_edges.extend(
                    [
                        (target_compute, "subnet", "is placed in"),
                        (filter_id, "network", "belongs to"),
                        (filter_id, target_compute, "allows traffic to"),
                    ]
                )
        workload["replicas"] = replicas
        workload["persistence"] = "persistent" if persistence_owner is workload else "unspecified"
        allocations.append(
            {
                "id": f"allocation-{workload['id']}",
                "workloadRef": workload["id"],
                "computeRef": target_compute,
                "replicas": replicas,
            }
        )
        _add_edge(edges, workload["id"], target_compute, "runs on", "design-allocation")

    compute_pools: list[dict[str, Any]] = []
    pool_ref_by_compute: dict[str, str] = {}
    for allocation in allocations:
        compute_ref = str(allocation["computeRef"])
        if compute_ref not in pool_ref_by_compute:
            pool_id = (
                "compute-pool-primary"
                if compute_ref == compute_node
                else f"compute-pool-{compute_ref.removeprefix('compute-')}"
            )
            pool_ref_by_compute[compute_ref] = pool_id
            primary_pool = compute_ref == compute_node
            compute_pools.append(
                {
                    "id": pool_id,
                    "computeRef": compute_ref,
                    "profile": (
                        topology_policy.get("computeProfile") if primary_pool else "standaloneOne"
                    ),
                    "replicaCount": (
                        int(projection_policy.get("minimumInstances") or 1) if primary_pool else 1
                    ),
                    "selectedZones": (
                        list(topology_policy.get("selectedZones") or [])
                        if primary_pool
                        else list(topology_policy.get("selectedZones") or [])[:1]
                    ),
                }
            )
        allocation["computePoolRef"] = pool_ref_by_compute[compute_ref]

    for source, target, label in extra_provider_edges:
        _add_edge(edges, source, target, label, "runtime-path-realization")
    for node in extra_compute_nodes:
        if node.get("providerKind") not in {"security-group", "firewall"}:
            continue
        node["trafficPolicy"] = {
            "source": (
                {"securityGroupRef": "security-group"}
                if provider == "aws"
                else {"applicationSubnetCidrRef": "subnet"}
                if provider == "azure"
                else {"sourceTag": "easydep-application"}
            ),
            "targetSelector": ({"targetTag": "easydep-state"} if provider == "gcp" else None),
            "protocol": "TCP",
            "port": 5432,
            "public": False,
        }

    if persistent_workload_present and persistence_owner is not None:
        state_allocation = next(
            (item for item in allocations if item["workloadRef"] == persistence_owner["id"]),
            None,
        )
        if state_allocation is not None:
            state_compute = str(state_allocation["computeRef"])
            _add_edge(
                edges,
                state_compute,
                "boot-image",
                "uses image",
                "boot-image-policy",
            )
            _add_edge(
                edges,
                state_compute,
                (
                    "state-secret-instance-profile"
                    if provider == "aws" and isolated_persistent
                    else "registry-instance-profile"
                    if provider == "aws"
                    else "state-secret-identity"
                    if isolated_persistent
                    else "registry-pull-identity"
                ),
                "uses secret identity",
                "secret-policy",
            )

    planned_nodes = {str(item.get("id") or ""): item for item in dependency_plan.get("nodes") or []}
    # DepKB's VM -> disk prerequisite can describe an embedded boot-disk
    # configuration (notably on GCP). It must not be projected as an independent,
    # retained data disk. A standalone disk resource exists only when an explicit
    # state-owning workload needs persistence.
    if persistence_owner is not None and planned_nodes.get("disk", {}).get(
        "provisioningStatus"
    ) in {
        "selectedStartResource",
        "mandatoryForProvisioning",
    }:
        selected.update({"disk", "disk-attachment"})
        owner_allocation = next(
            item for item in allocations if item["workloadRef"] == persistence_owner["id"]
        )
        _add_edge(
            edges,
            "disk-attachment",
            "disk",
            "binds",
            "dependency-plan",
        )
        _add_edge(
            edges,
            "disk-attachment",
            owner_allocation["computeRef"],
            "binds",
            "dependency-plan",
        )

    logical_connection_edges = _connection_edges(logical_deployment_model, workloads, provider)
    for edge in logical_connection_edges:
        edges[(edge["from"], edge["to"], edge["label"])] = edge

    endpoint_nodes: list[dict[str, Any]] = []
    load_balanced = "load-balancer" in selected or "forwarding-rule" in selected
    entry_resource = "load-balancer" if provider in {"aws", "azure"} else "forwarding-rule"
    if external_endpoints:
        if not load_balanced or provider in {"azure", "gcp"}:
            selected.add("public-ip")
        for endpoint in external_endpoints:
            endpoint["mode"] = "loadBalanced" if load_balanced else "direct"
            endpoint["protocol"] = "http"
            endpoint_nodes.append(
                {
                    **endpoint,
                    "group": "endpoint",
                    "entityClass": "runtimeElement",
                    "handling": "runtimeDerived",
                }
            )
            _add_edge(
                edges,
                endpoint["id"],
                entry_resource if load_balanced else "public-ip",
                "enters through",
                "design-endpoint",
            )
            if not load_balanced:
                if provider == "aws":
                    # An EIP association can be expressed by the EIP resource after
                    # the instance exists.
                    _add_edge(
                        edges,
                        "public-ip",
                        compute_node,
                        "exposes",
                        "provider-realization",
                    )
                else:
                    # Azure and GCP network-interface configuration references an
                    # already allocated static public address.
                    address_owner = (
                        "network-interface" if "network-interface" in selected else compute_node
                    )
                    _add_edge(
                        edges,
                        address_owner,
                        "public-ip",
                        "uses address",
                        "provider-realization",
                    )
            _add_edge(
                edges,
                endpoint["id"],
                endpoint["targetWorkloadRef"],
                "routes to",
                "design-endpoint",
            )
        if load_balanced and "public-ip" in selected:
            _add_edge(
                edges,
                entry_resource,
                "public-ip",
                "uses address",
                "provider-realization",
            )

    if provider == "azure":
        resource_group_scoped = {
            "app-registry",
            "compute-group",
            "compute-instance",
            "disk",
            "load-balancer",
            "nat-gateway",
            "nat-public-ip",
            "network",
            "network-interface",
            "public-ip",
            "registry-pull-identity",
            "security-group",
            "state-secret-identity",
            "subnet",
        }
        for node_id in sorted(selected & resource_group_scoped):
            if _TERRAFORM_TYPES[provider].get(node_id):
                _add_edge(
                    edges,
                    node_id,
                    "resource-group",
                    "is deployed in",
                    "provider-realization",
                )
        for node in extra_compute_nodes:
            _add_edge(
                edges,
                str(node["id"]),
                "resource-group",
                "is deployed in",
                "provider-realization",
            )

    address_plan = {
        "networkCidrs": ["10.80.0.0/16"],
        "applicationSubnetCidrs": [
            f"10.80.{10 + index}.0/24"
            for index in range(int(projection_policy.get("minimumSubnets") or 1))
        ],
        "ingressSubnetCidrs": [
            f"10.80.{20 + index}.0/24"
            for index in range(int(projection_policy.get("minimumIngressSubnets") or 1))
        ],
        "stateSubnetCidrs": ["10.80.30.0/24"],
        "overlapPolicy": "reject",
    }

    resources = spec["resources"]
    provider_nodes = []
    for node_id in sorted(selected):
        if node_id not in resources:
            continue
        provider_kind = node_id
        terraform_types = _TERRAFORM_TYPES.get(provider, {}).get(provider_kind, ())
        embedded_owner = spec.get("embeddedOwners", {}).get(provider_kind)
        if provider == "azure" and provider_kind == "backend-membership" and managed:
            terraform_types = ()
            embedded_owner = "compute-group"
        if provider == "gcp" and provider_kind == "network-interface" and managed:
            embedded_owner = "compute-template"
        if provider == "azure" and provider_kind == "network-interface" and managed:
            terraform_types = ()
            embedded_owner = "compute-group"
        embedded = bool(embedded_owner) and not terraform_types
        referenced = provider_kind in {
            "boot-image",
            "registry-pull-policy",
            "secret-ref",
        }
        node_name = resources[node_id][0]
        if provider == "gcp" and provider_kind == "compute-group":
            if topology_policy.get("zoneLayout") == "multiZoneSpread":
                node_name = "Regional Managed Instance Group"
                terraform_types = ("google_compute_region_instance_group_manager",)
            else:
                node_name = "Zonal Managed Instance Group"
                terraform_types = ("google_compute_instance_group_manager",)
        if provider == "gcp" and provider_kind == "public-ip":
            node_name = "Regional External IP Address"
            terraform_types = ("google_compute_address",)
        provider_nodes.append(
            {
                "id": node_id,
                "name": node_name,
                "group": resources[node_id][1],
                "providerKind": provider_kind,
                "entityClass": (
                    "externalArtifact"
                    if referenced
                    else "providerComponent"
                    if embedded
                    else "providerResource"
                ),
                "handling": (
                    "referenceExisting"
                    if referenced
                    else "configureInsideOwner"
                    if embedded
                    else "create"
                ),
                "terraformTypes": [] if referenced else list(terraform_types),
                "projectionRuleId": f"{provider}.{provider_kind}",
                **(
                    {
                        "transportProtocol": "TCP",
                        "frontendPort": 80,
                        "backendPort": (
                            80
                            if provider == "gcp"
                            else application_hint.get("hostPort", "runtimeDerived")
                        ),
                    }
                    if provider_kind in {"listener", "routing-rule", "forwarding-rule"}
                    else {}
                ),
                **(
                    {
                        "protocol": "HTTP",
                        "port": (
                            80
                            if provider == "gcp"
                            else application_hint.get("hostPort", "runtimeDerived")
                        ),
                        "requestPath": application_hint.get("healthPath", "runtimeDerived"),
                    }
                    if provider_kind == "health-check"
                    else {}
                ),
                **(
                    {
                        "requiredHostPort": 80,
                        "containerPort": application_hint.get("containerPort", "runtimeDerived"),
                        "reason": "passthrough-load-balancer-does-not-translate-ports",
                    }
                    if provider == "gcp" and load_balanced and provider_kind == "forwarding-rule"
                    else {}
                ),
                **({"ownerRef": embedded_owner} if embedded else {}),
                **(
                    {
                        "inputVariable": "database_secret_ref",
                        "valueOwner": "caller",
                        "credentialCollectionByEasyDep": False,
                        "valueFormat": "providerSecretContainingJsonObject",
                        "requiredKeys": [
                            "POSTGRES_DB",
                            "POSTGRES_USER",
                            "POSTGRES_PASSWORD",
                        ],
                    }
                    if provider_kind == "secret-ref"
                    else {}
                ),
                **(
                    {
                        "selectionPolicy": "providerDefaultLinuxImage",
                        "resolvedIdMustBeRecorded": True,
                    }
                    if provider_kind == "boot-image"
                    else {}
                ),
                **(
                    {
                        "imageReferencePolicy": "immutableDigest",
                        "provisionedBy": "userExecutedGeneratedIaC",
                    }
                    if provider_kind == "app-registry"
                    else {}
                ),
                **(
                    {
                        "desiredCapacity": int(projection_policy.get("minimumInstances") or 1),
                        "autoscaling": False,
                        "selectedZones": list(topology_policy.get("selectedZones") or []),
                        "zoneSpreadRequired": bool(projection_policy.get("zoneSpreadRequired")),
                    }
                    if provider_kind == "compute-group"
                    else {}
                ),
                **(
                    {
                        "bootImageRef": "boot-image",
                        "placement": {
                            "region": region,
                            "selectedZones": list(topology_policy.get("selectedZones") or []),
                        },
                    }
                    if provider_kind in {"compute-instance", "compute-template"}
                    else {}
                ),
                **(
                    {
                        "deletionPolicy": "retain",
                        "purgeRequiresExplicitOptIn": True,
                    }
                    if provider_kind == "disk"
                    else {}
                ),
                **(
                    {
                        "natIpAllocateOption": "AUTO_ONLY",
                        "sourceSubnetworkIpRangesToNat": "LIST_OF_SUBNETWORKS",
                        "subnetworkSourceIpRanges": ["ALL_IP_RANGES"],
                    }
                    if provider == "gcp" and provider_kind == "cloud-nat"
                    else {}
                ),
                **(
                    {"preserveDefaultInternetRoute": True}
                    if provider == "gcp" and provider_kind == "network"
                    else {}
                ),
                **(
                    {
                        "sku": "Standard",
                        "allocationMethod": "Static",
                    }
                    if provider == "azure" and provider_kind in {"public-ip", "nat-public-ip"}
                    else {}
                ),
                **(
                    {
                        "trafficPolicy": {
                            "clientSource": (
                                {"securityGroupRef": "load-balancer-security-group"}
                                if provider == "aws"
                                and load_balanced_topology
                                and node_id == "security-group"
                                else "0.0.0.0/0"
                            ),
                            "protocol": "TCP",
                            "port": (
                                80
                                if node_id == "load-balancer-security-group"
                                or (provider == "gcp" and load_balanced_topology)
                                else application_hint.get("hostPort", "runtimeDerived")
                            ),
                            "healthProbeRequired": load_balanced_topology,
                            "targetSelectorRequired": provider == "gcp",
                        }
                    }
                    if provider_kind
                    in {"security-group", "firewall", "load-balancer-security-group"}
                    else {}
                ),
                **(
                    {
                        "cidrBlocks": (
                            address_plan["networkCidrs"]
                            if node_id == "network"
                            else address_plan["applicationSubnetCidrs"]
                            if node_id == "subnet"
                            else address_plan["ingressSubnetCidrs"]
                            if node_id == "ingress-subnet"
                            else address_plan["stateSubnetCidrs"]
                        )
                    }
                    if node_id in {"network", "subnet", "ingress-subnet", "state-subnet"}
                    and not (provider == "gcp" and node_id == "network")
                    else {}
                ),
                **(
                    {
                        "scope": "zonal",
                        "placementConstraint": "sameZoneAsAttachedCompute",
                    }
                    if provider_kind == "disk"
                    else {}
                ),
                **(
                    {"minimumCount": int(projection_policy.get("minimumSubnets") or 1)}
                    if provider_kind == "subnet"
                    and int(projection_policy.get("minimumSubnets") or 1) > 1
                    else {"minimumCount": int(projection_policy.get("minimumIngressSubnets") or 1)}
                    if provider_kind == "ingress-subnet"
                    and int(projection_policy.get("minimumIngressSubnets") or 1) > 1
                    else {}
                ),
                **(
                    {"minimumCount": int(projection_policy.get("minimumIngressSubnets") or 1)}
                    if provider_kind == "ingress-route-association"
                    else {"minimumCount": int(projection_policy.get("minimumSubnets") or 1)}
                    if provider_kind == "application-route-association"
                    else {}
                ),
            }
        )
    workload_nodes = [
        {
            **item,
            "group": "runtime",
            "entityClass": "runtimeElement",
            "handling": "runtimeDerived",
        }
        for item in workloads
    ]
    unresolved: list[dict[str, str]] = []
    if persistent_storage_required and persistence_owner is None:
        unresolved.append(
            {
                "field": "persistenceOwner",
                "reason": (
                    "Persistent storage was required, but multiple deployable workloads "
                    "exist and no single state-owning workload is explicit."
                ),
            }
        )
    for workload in workloads:
        if not workload.get("runtime"):
            unresolved.append(
                {
                    "field": f"workloads.{workload['id']}.runtime",
                    "reason": (
                        "An explicit deployable workload has no supported, evidence-backed "
                        "runtime contract."
                    ),
                }
            )
    plan = {
        "schemaVersion": "easydep-resource-plan/v1",
        "provider": provider,
        "region": region,
        "computeNodeId": compute_node,
        "deploymentTopology": topology_policy,
        "providerProjectionPolicy": projection_policy,
        "placementConstraints": {
            "regionCount": 1,
            "minimumZones": int(projection_policy.get("minimumZones") or 1),
            "minimumIngressZones": int(projection_policy.get("minimumIngressZones") or 1),
            "minimumSubnets": int(projection_policy.get("minimumSubnets") or 1),
            "minimumIngressSubnets": int(projection_policy.get("minimumIngressSubnets") or 1),
            "selectedIngressZones": list(projection_policy.get("selectedIngressZones") or []),
            "subnetScope": "zonal" if provider == "aws" else "regional",
            "diskZoneAffinity": "sameZoneAsAttachedCompute",
            "addressPlan": address_plan,
        },
        "workloads": workloads,
        "connections": logical_connection_edges,
        "computePools": compute_pools,
        "allocations": allocations,
        "nodes": [
            *provider_nodes,
            *extra_compute_nodes,
            *workload_nodes,
            *{item["id"]: item for item in endpoint_nodes}.values(),
        ],
        "edges": sorted(edges.values(), key=lambda item: (item["from"], item["to"])),
        "decisions": [
            {
                "field": "topologyFamily",
                "value": topology_policy.get("familyId"),
                "basis": "DeploymentTopology/v1",
                "sourceRefs": [],
            },
            *(
                [
                    {
                        "field": "persistenceOwner",
                        "value": persistence_owner["id"],
                        "basis": (
                            "explicit-design-database-node"
                            if persistent_candidates
                            else "single-deployable-workload"
                        ),
                        "sourceRefs": persistence_owner.get("sourceRefs") or [],
                        "evidenceRefs": [f"experiment:E1/{provider}"],
                    },
                    {
                        "field": "separateDataDisk",
                        "value": True,
                        "basis": "project-policy:self-hosted-persistent-workload",
                        "sourceRefs": persistence_source_refs or [],
                        "evidenceRefs": [f"experiment:E1/{provider}"],
                    },
                    *(
                        [
                            {
                                "field": f"workloads.{persistence_owner['id']}.runtime",
                                "value": persistence_owner.get("runtime"),
                                "basis": (persistence_owner.get("runtime") or {}).get("basis"),
                                "sourceRefs": persistence_owner.get("sourceRefs") or [],
                            }
                        ]
                        if persistence_owner.get("runtime")
                        else []
                    ),
                ]
                if persistence_owner is not None
                else []
            ),
        ],
        "unresolved": unresolved,
        "runtimeEvidence": {
            "appStatePrivatePath": {
                "status": "observed" if logical_connection_edges else "notApplicable",
                "evidenceRefs": ([f"experiment:E1/{provider}"] if logical_connection_edges else []),
                "observedFunction": (
                    "State VM private address plus provider traffic filter supported state read/write"
                    if logical_connection_edges
                    else None
                ),
            },
            "stateRestartPersistence": {
                "status": "observed" if persistent_storage_required else "notApplicable",
                "evidenceRefs": (
                    [f"experiment:E1/{provider}"] if persistent_storage_required else []
                ),
                "observedFault": (
                    "state VM restart or reset" if persistent_storage_required else None
                ),
            },
            "stateReplacementRebind": {
                "status": "observed" if persistent_storage_required else "notApplicable",
                "evidenceRefs": (
                    [f"experiment:E3/{provider}"] if persistent_storage_required else []
                ),
                "observedFault": (
                    "state VM replacement, existing data-disk reattachment, and "
                    "runtime endpoint reinjection without rebuilding the application image"
                    if persistent_storage_required
                    else None
                ),
            },
            "managedL4Ingress": _managed_l4_evidence(
                provider,
                any(
                    node.get("protocol") == "http" and node.get("mode") == "loadBalanced"
                    for node in endpoint_nodes
                ),
            ),
            "scope": "development-observation-one-run-per-provider",
        },
    }
    validate_resource_plan_structure(plan)
    return plan
