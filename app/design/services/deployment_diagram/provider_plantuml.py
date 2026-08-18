"""Deterministic provider-native deployment-diagram renderers.

The runtime view answers where software runs and how requests/data flow.  The
provisioning view answers which cloud objects must exist first.  Keeping those
semantics separate prevents one arrow from ambiguously meaning both traffic and
creation order.
"""

from __future__ import annotations

import re
from typing import Any

from app.design.services.deployment_diagram.plantuml import generate_deployment_from_model

# Only audited IaC references and ordering constraints belong in the
# provisioning view. New ResourcePlan relationship labels must be reviewed and
# added deliberately; functional reachability such as firewall traffic is not a
# creation dependency.
_PROVISIONING_RELATIONSHIPS = {
    "attaches",
    "belongs to",
    "binds",
    "checks with",
    "contains instance",
    "contains role",
    "configures",
    "selects subnetwork",
    "creates instances from",
    "depends on",
    "evaluates targets with",
    "exposes",
    "forwards to",
    "grants pull access to",
    "grants secret read to",
    "is attached to",
    "is deployed in",
    "is placed in",
    "joins",
    "joins through",
    "matches",
    "places instances in",
    "provides egress for",
    "pulls image digest from",
    "registers instance",
    "registers instances with",
    "registers with",
    "routes to",
    "scopes pull access to",
    "scopes secret read to",
    "serves region of",
    "uses",
    "uses address",
    "uses backend",
    "uses identity",
    "uses image",
    "uses policy",
    "uses secret identity",
}

# These entries remain first-class ResourcePlan nodes because Terraform must
# create or configure them explicitly.  In the reader-facing diagram they are
# projected as labelled relationships between the cloud resources they join.
# This keeps IaC validation exact without presenting provider implementation
# objects such as aws_volume_attachment as if they were CSP resources.
_FOLDED_RELATION_KINDS = {
    "aws": {
        "application-default-route",
        "application-ingress-rule",
        "application-route-association",
        "disk-attachment",
        "ingress-default-route",
        "ingress-route-association",
        "registry-pull-binding",
        "secret-access-binding",
        "state-secret-access-binding",
    },
    "azure": {
        "backend-membership",
        "disk-attachment",
        "nat-association",
        "nat-public-ip-association",
        "registry-pull-binding",
        "secret-access-binding",
        "security-group-association",
        "state-secret-access-binding",
    },
    "gcp": {
        "disk-attachment",
        "registry-pull-binding",
        "secret-access-binding",
        "state-secret-access-binding",
    },
}

_FOLDED_RELATION_LABELS = {
    "application-default-route": "default route to NAT Gateway",
    "application-ingress-rule": "allows application port",
    "application-route-association": "uses route table",
    "backend-membership": "registered in backend pool",
    "disk-attachment": "attached",
    "ingress-default-route": "default route to Internet Gateway",
    "ingress-route-association": "uses route table",
    "nat-association": "uses NAT Gateway",
    "nat-public-ip-association": "uses public IP",
    "registry-pull-binding": "grants Registry pull",
    "secret-access-binding": "grants Secret read",
    "security-group-association": "applies network security group",
    "state-secret-access-binding": "grants State Secret read",
}


def _id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", value) or "unknown"


def _text(value: Any) -> str:
    return str(value or "").replace('"', "'").replace("\n", " ")


def _primary(bundle: dict[str, Any]) -> dict[str, Any] | None:
    projections = list(bundle.get("projections") or [])
    return projections[0] if len(projections) == 1 else None


def _fallback(bundle: dict[str, Any], message: str = "") -> str:
    logical = dict(bundle.get("logicalModel") or {})
    puml = generate_deployment_from_model(logical)
    if not puml:
        puml = '@startuml\n!theme plain\nnode "Deployment target unresolved"\n@enduml'
    if message:
        puml = puml.replace("@enduml", f"note bottom\n  {_text(message)}\nend note\n@enduml")
    return puml


def _provider_label(provider: str) -> str:
    return {"aws": "AWS", "azure": "Microsoft Azure", "gcp": "Google Cloud"}.get(
        provider, provider.upper()
    )


def _node_by_kind(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("providerKind") or node.get("id") or ""): node
        for node in plan.get("nodes") or []
        if isinstance(node, dict) and node.get("providerKind")
    }


def _workload_alias(workload: dict[str, Any]) -> str:
    return "runtime_" + _id(str(workload.get("id") or "application"))


def _replica_alias(workload: dict[str, Any], replica: int) -> str:
    return f"{_workload_alias(workload)}_replica_{replica}"


def _instance_label(provider: str) -> str:
    return {
        "aws": "EC2 Instance",
        "azure": "Virtual Machine Scale Set instance",
        "gcp": "Compute Engine VM",
    }.get(provider, "VM instance")


def render_runtime_deployment(bundle: dict[str, Any]) -> str:
    """Render the primary deployment view: boundaries, placement, and traffic."""
    projections = list(bundle.get("projections") or [])
    if not projections:
        return _fallback(bundle)
    projection = _primary(bundle)
    if projection is None:
        return _fallback(
            bundle,
            "Multiple provider alternatives exist. Select one target before rendering a provider-native view.",
        )
    if projection.get("status") not in {"completed", "needsInput"}:
        reasons = "; ".join(
            str(item.get("reason") or "") for item in projection.get("issues") or []
        )
        return _fallback(bundle, reasons or "Provider-native deployment target is unresolved.")

    plan = dict(projection.get("resourcePlan") or {})
    topology = dict(projection.get("topology") or {})
    provider = str(projection.get("provider") or "")
    region = str(projection.get("region") or "")
    nodes = _node_by_kind(plan)
    workloads = list(plan.get("workloads") or [])
    logical_artifacts = [
        item
        for item in (
            (bundle.get("logicalModel") or {}).get("Artifacts")
            or (bundle.get("logicalModel") or {}).get("artifacts")
            or []
        )
        if isinstance(item, dict) and item.get("name")
    ]
    allocations = {
        str(item.get("workloadRef") or ""): item for item in plan.get("allocations") or []
    }
    selected_zones = list(topology.get("selectedZones") or [])
    zone_label = (
        ", ".join(selected_zones)
        if provider == "aws" and selected_zones
        else f"regional: {region}"
        if provider in {"azure", "gcp"}
        else "one selected zone"
    )
    placement_constraints = dict(plan.get("placementConstraints") or {})
    minimum_subnets = int(placement_constraints.get("minimumSubnets") or 1)
    ingress_zones = list(
        placement_constraints.get("selectedIngressZones")
        or topology.get("selectedIngressZones")
        or selected_zones
    )
    load_balanced = topology.get("publicIngress") == "loadBalanced"
    ingress_name = _text(
        (nodes.get("load-balancer") or nodes.get("forwarding-rule") or {}).get("name")
        or "Public load balancer"
    )

    compute_kind = (
        "compute-group"
        if topology.get("computeManagement") == "managedGroup"
        else "compute-instance"
    )
    primary_compute_id = str(plan.get("computeNodeId") or "")
    compute = next(
        (
            node
            for node in plan.get("nodes") or []
            if str(node.get("id") or "") == primary_compute_id
        ),
        nodes.get(compute_kind, {}),
    )
    network = nodes.get("network", {})
    subnet = nodes.get("subnet", {})
    traffic_filters = [
        node
        for node in plan.get("nodes") or []
        if node.get("providerKind")
        in {
            "application-ingress-rule",
            "firewall",
            "load-balancer-security-group",
            "security-group",
        }
    ]
    family_id = str(topology.get("familyId") or "")

    lines = [
        "@startuml",
        "!theme plain",
        "left to right direction",
        "skinparam shadowing false",
        "skinparam linetype ortho",
        "skinparam ArrowColor #2f6b50",
        "skinparam node {",
        "  BackgroundColor #ffffff",
        "  BorderColor #68736b",
        "}",
        "skinparam cloud {",
        "  BackgroundColor #f5f7f4",
        "  BorderColor #7f8d83",
        "}",
        (
            f"title Runtime deployment - {_provider_label(provider)} / {_text(region)}"
            f"\\n{_text(family_id)}"
        ),
        'actor "Internet client" as internet_client',
        f'cloud "{_provider_label(provider)}" as provider_boundary {{',
    ]
    if provider == "gcp":
        lines.extend(
            [
                f'  node "{_text(network.get("name") or "VPC Network")}\\n<<global>>" as provider_network',
                f'  frame "Region: {_text(region)}" as region_boundary {{',
            ]
        )
    else:
        lines.extend(
            [
                f'  frame "Region: {_text(region)}" as region_boundary {{',
                f'    node "{_text(network.get("name") or "Network")}" as provider_network {{',
            ]
        )
    subnet_aliases: list[str] = []
    if minimum_subnets > 1:
        for index in range(minimum_subnets):
            alias = f"provider_subnet_{index + 1}"
            zone = (
                ingress_zones[index] if index < len(ingress_zones) else f"distinct AZ {index + 1}"
            )
            subnet_aliases.append(alias)
            lines.append(
                f'      node "{_text(subnet.get("name") or "Subnet")} {index + 1}\\n{_text(zone)}" as {alias}'
            )
    else:
        subnet_aliases.append("provider_subnet")
        lines.append(
            f'      node "{_text(subnet.get("name") or "Subnet")}\\n{_text(zone_label)}" as provider_subnet {{'
        )

    compute_name = _text(compute.get("name") or "Compute")
    replicas = int(topology.get("replicaCount") or 1)
    managed_group = topology.get("computeManagement") == "managedGroup"
    compute_suffix = (
        f"\\nfixed capacity: {replicas}" if managed_group else "\\n1 standalone instance"
    )
    lines.append(f'        node "{compute_name}{compute_suffix}" as primary_compute {{')
    primary_workload_ids = [
        ref
        for ref, allocation in allocations.items()
        if str(allocation.get("computeRef") or "") == primary_compute_id
    ]
    runtime_aliases: dict[str, list[str]] = {}
    if managed_group:
        for replica in range(1, replicas + 1):
            if selected_zones:
                zone = (
                    selected_zones[(replica - 1) % len(selected_zones)]
                    if topology.get("zoneLayout") == "multiZoneSpread"
                    else selected_zones[0]
                )
            else:
                zone = "selected zone"
            lines.append(
                f'          node "{_instance_label(provider)} {replica}\\n{_text(zone)}" as primary_instance_{replica} {{'
            )
            for workload in workloads:
                workload_id = str(workload.get("id") or "")
                if workload_id not in primary_workload_ids:
                    continue
                alias = _replica_alias(workload, replica)
                runtime_aliases.setdefault(workload_id, []).append(alias)
                shape = "database" if workload.get("stateMode") == "persistent" else "component"
                lines.append(
                    f'            {shape} "{_text(workload.get("name") or "Application")}\\nreplica {replica}\\n<<Docker container>>" as {alias}'
                )
            lines.append("          }")
    else:
        for workload in workloads:
            workload_id = str(workload.get("id") or "")
            if workload_id not in primary_workload_ids:
                continue
            alias = _workload_alias(workload)
            runtime_aliases.setdefault(workload_id, []).append(alias)
            shape = "database" if workload.get("stateMode") == "persistent" else "component"
            lines.append(
                f'          {shape} "{_text(workload.get("name") or "Application")}\\n<<Docker container>>" as {alias}'
            )
    lines.append("        }")
    external_compute_refs = sorted(
        {
            str(allocation.get("computeRef") or "")
            for allocation in allocations.values()
            if str(allocation.get("computeRef") or "") != primary_compute_id
        }
    )
    for compute_ref in external_compute_refs:
        compute_node: dict[str, Any] = next(
            (node for node in plan.get("nodes") or [] if node.get("id") == compute_ref),
            {},
        )
        compute_alias = "compute_" + _id(compute_ref)
        lines.append(
            f'        node "{_text(compute_node.get("name") or "State VM")}\\n1 state instance; single zone" as {compute_alias} {{'
        )
        for workload in workloads:
            allocation = allocations.get(str(workload.get("id") or ""), {})
            if str(allocation.get("computeRef") or "") != compute_ref:
                continue
            workload_id = str(workload.get("id") or "")
            alias = _workload_alias(workload)
            runtime_aliases.setdefault(workload_id, []).append(alias)
            shape = "database" if workload.get("stateMode") == "persistent" else "component"
            lines.append(
                f'          {shape} "{_text(workload.get("name") or "State workload")}\\n<<Docker container>>" as {alias}'
            )
        lines.append("        }")
    if minimum_subnets == 1:
        lines.append("      }")
    disk = nodes.get("disk")
    public_ip = nodes.get("public-ip")
    if provider == "gcp":
        if public_ip and not load_balanced:
            lines.append(f'    node "{_text(public_ip.get("name"))}" as public_address')
        if disk:
            lines.append(
                f'    database "{_text(disk.get("name"))}" as persistent_disk <<zonal; retained>>'
            )
        if load_balanced and nodes.get("backend-group"):
            lines.append(
                f'    node "{_text(nodes["backend-group"].get("name"))}\\n<<zonal>>" as ingress_backend_group'
            )
        lines.append("  }")
    for traffic_filter in traffic_filters:
        filter_id = str(traffic_filter.get("id") or "policy")
        provider_kind = str(traffic_filter.get("providerKind") or "")
        filter_role = "PostgreSQL" if "postgresql" in filter_id else "Application"
        filter_name = _text(traffic_filter.get("name"))
        display_name = (
            filter_name
            if provider_kind not in {"firewall", "security-group"}
            or filter_role.lower() in filter_name.lower()
            else f"{filter_name}\\n{filter_role}"
        )
        lines.append(
            f'      node "{display_name}" as traffic_filter_{_id(filter_id)} <<traffic policy>>'
        )
    if load_balanced and provider == "aws":
        minimum_ingress_subnets = int(placement_constraints.get("minimumIngressSubnets") or 1)
        for index in range(minimum_ingress_subnets):
            zone = (
                ingress_zones[index] if index < len(ingress_zones) else f"distinct AZ {index + 1}"
            )
            lines.append(
                f'      node "Ingress Subnet {index + 1}\\n{_text(zone)}" as ingress_subnet_{index + 1}'
            )
        lines.extend(
            [
                f'      node "{ingress_name}" as public_ingress <<TCP ingress>>',
                f'      component "{_text(nodes.get("listener", {}).get("name"))}" as ingress_listener',
                f'      node "{_text(nodes.get("backend-group", {}).get("name"))}" as ingress_backend_group',
                f'      component "{_text(nodes.get("health-check", {}).get("name"))}" as ingress_health_check <<health policy>>',
            ]
        )
    elif load_balanced and provider == "azure":
        lines.extend(
            [
                f'      node "{ingress_name}" as public_ingress <<TCP ingress>> {{',
                f'        component "{_text(nodes.get("frontend-ip-config", {}).get("name"))}" as ingress_frontend_ip_config',
                "      }",
                f'      component "{_text(nodes.get("routing-rule", {}).get("name"))}" as ingress_routing_rule',
                f'      node "{_text(nodes.get("backend-group", {}).get("name"))}" as ingress_backend_group',
                f'      component "{_text(nodes.get("health-check", {}).get("name"))}" as ingress_health_check <<health policy>>',
            ]
        )
    if provider != "gcp":
        lines.append("    }")
        if public_ip:
            lines.append(f'    node "{_text(public_ip.get("name"))}" as public_address')
        if disk:
            lines.append(
                f'    database "{_text(disk.get("name"))}" as persistent_disk <<retained>>'
            )
        lines.append("  }")
    elif load_balanced:
        lines.extend(
            [
                f'  node "{_text(public_ip.get("name") if public_ip else "Regional External IP Address")}\\n<<regional>>" as public_address',
                f'  node "{ingress_name}\\n<<regional>>" as public_ingress <<TCP ingress>>',
                f'  node "{_text(nodes.get("backend-service", {}).get("name"))}\\n<<regional>>" as ingress_backend_service',
                f'  node "{_text(nodes.get("health-check", {}).get("name"))}\\n<<regional>>" as ingress_health_check <<health policy>>',
            ]
        )
    lines.extend(["}", ""])
    registry = nodes.get("app-registry")
    app_identity = nodes.get("registry-pull-identity")
    secret_ref = nodes.get("secret-ref")
    app_secret_binding = nodes.get("secret-access-binding")
    state_secret_binding = nodes.get("state-secret-access-binding")
    persistent_workload_present = any(
        workload.get("stateMode") == "persistent" for workload in workloads
    )
    if registry:
        lines.extend(
            [
                'actor "User-run deploy.sh\\n<<local CSP authentication>>" as user_deploy',
                'frame "Application delivery dependencies" as delivery_dependencies {',
                f'  node "{_text(registry.get("name"))}" as application_registry_dependency',
                f'  node "{_text(app_identity.get("name") if app_identity else "App runtime identity")}" as application_identity_dependency',
                *[
                    f'  artifact "{_text(item.get("name"))}" as application_source_artifact_{index}'
                    for index, item in enumerate(logical_artifacts, start=1)
                ],
                '  artifact "Application image@sha256\\n<<immutable digest>>" as application_image_dependency',
                '  cloud "VM outbound path\\n(public address or managed NAT)" as workload_outbound_dependency',
                "}",
                "user_deploy -[#2f6b50]-> application_registry_dependency : creates and authenticates push",
                "user_deploy -[#2f6b50]-> application_image_dependency : builds and pushes once",
                "application_registry_dependency -[#2f6b50]-> application_image_dependency : stores digest",
                "application_identity_dependency ..[#8a6d3b]> application_image_dependency : authorizes pull",
                *[
                    f"application_source_artifact_{index} -[#2f6b50]-> application_image_dependency : packaged by Docker build"
                    for index, _item in enumerate(logical_artifacts, start=1)
                ],
            ]
        )
    elif logical_artifacts:
        lines.extend(
            f'artifact "{_text(item.get("name"))}" as application_source_artifact_{index}'
            for index, item in enumerate(logical_artifacts, start=1)
        )
    if persistent_workload_present:
        lines.extend(
            [
                'frame "Database runtime dependencies" as database_dependencies {',
                f'  artifact "{_text(secret_ref.get("name") if secret_ref else "Existing provider Secret")}\\nPOSTGRES_DB · POSTGRES_USER · POSTGRES_PASSWORD" as database_secret_dependency',
                f'  node "{_text(app_secret_binding.get("name") if app_secret_binding else "App Secret read binding")}" as app_secret_binding_dependency',
                *(
                    [
                        f'  node "{_text(state_secret_binding.get("name"))}" as state_secret_binding_dependency'
                    ]
                    if state_secret_binding
                    else []
                ),
                '  artifact "docker.io/library/postgres:17-bookworm" as postgres_image_dependency',
                "}",
                "database_secret_dependency ..[#8a6d3b]> app_secret_binding_dependency : read scope",
                *(
                    [
                        "database_secret_dependency ..[#8a6d3b]> state_secret_binding_dependency : read scope"
                    ]
                    if state_secret_binding
                    else []
                ),
            ]
        )
    if provider == "gcp":
        lines.append("provider_network ..[#6f7c73]> provider_subnet : contains regional subnetwork")

    if minimum_subnets > 1:
        for replica in range(1, replicas + 1):
            subnet_index = (
                (replica - 1) % len(subnet_aliases)
                if topology.get("zoneLayout") == "multiZoneSpread"
                else 0
            )
            target_alias = f"primary_instance_{replica}" if managed_group else "primary_compute"
            lines.append(
                f"{subnet_aliases[subnet_index]} ..[#6f7c73]> {target_alias} : places instance"
            )
            if not managed_group:
                break
        for compute_ref in external_compute_refs:
            lines.append(
                f"{subnet_aliases[0]} ..[#6f7c73]> compute_{_id(compute_ref)} : places state VM"
            )
    if load_balanced and provider == "aws":
        minimum_ingress_subnets = int(placement_constraints.get("minimumIngressSubnets") or 1)
        for index in range(minimum_ingress_subnets):
            lines.append(
                f"ingress_subnet_{index + 1} ..[#6f7c73]> public_ingress : attaches frontend"
            )

    endpoint = next(
        (node for node in plan.get("nodes") or [] if node.get("group") == "endpoint"),
        None,
    )
    endpoint_alias = "public_endpoint"
    if endpoint:
        lines.append(
            f'interface "{_text(endpoint.get("name") or "Public HTTP endpoint")}" as {endpoint_alias}'
        )
        lines.append(f"internet_client -[#2f6b50]-> {endpoint_alias} : HTTP")
    if endpoint and public_ip:
        lines.append(f"{endpoint_alias} -[#2f6b50]-> public_address : accepts HTTP")
    elif endpoint and load_balanced:
        lines.append(f"{endpoint_alias} -[#2f6b50]-> public_ingress : accepts HTTP")
    if load_balanced and public_ip and provider != "azure":
        lines.append("public_address -[#2f6b50]-> public_ingress : receives HTTP for")
    primary_application = next(
        (
            item
            for item in workloads
            if str(item.get("id") or "") in primary_workload_ids
            and item.get("stateMode") != "persistent"
        ),
        None,
    )
    if primary_application:
        application_aliases = runtime_aliases.get(str(primary_application.get("id") or ""), [])
        if load_balanced:
            if provider == "aws":
                lines.extend(
                    [
                        "public_ingress -[#2f6b50]-> ingress_listener : accepts TCP on port 80",
                        "ingress_listener -[#2f6b50]-> ingress_backend_group : selects target group",
                    ]
                )
                backend_alias = "ingress_backend_group"
            elif provider == "azure":
                lines.extend(
                    [
                        "public_address -[#2f6b50]-> ingress_frontend_ip_config : assigned frontend address",
                        "ingress_frontend_ip_config -[#2f6b50]-> ingress_routing_rule : accepts TCP on port 80",
                        "ingress_routing_rule -[#2f6b50]-> ingress_backend_group : selects backend pool",
                    ]
                )
                backend_alias = "ingress_backend_group"
            else:
                lines.extend(
                    [
                        "public_ingress -[#2f6b50]-> ingress_backend_service : forwards TCP packets",
                    ]
                )
                backend_alias = "primary_compute" if managed_group else "ingress_backend_group"
                lines.append(
                    f"ingress_backend_service -[#2f6b50]-> {backend_alias} : selects instance group"
                )
            for replica, alias in enumerate(application_aliases, start=1):
                lines.append(
                    f"{backend_alias} -[#2f6b50]-> {alias} : forwards to replica {replica}"
                )
                lines.append(f"ingress_health_check ..[#8a6d3b]> {alias} : probes health")
        elif public_ip and application_aliases:
            lines.append(f"public_address -[#2f6b50]-> {application_aliases[0]} : HTTP")
        for alias in application_aliases:
            if registry:
                lines.append(
                    f"application_image_dependency -[#2f6b50]-> {alias} : runs immutable image"
                )
                lines.append(
                    f"workload_outbound_dependency ..[#8a6d3b]> {alias} : reaches Registry"
                )
            if app_secret_binding:
                lines.append(
                    f"app_secret_binding_dependency ..[#8a6d3b]> {alias} : injects DB credentials"
                )
    for edge in plan.get("edges") or []:
        if "design-connection" not in edge.get("evidence", []):
            continue
        source = next((item for item in workloads if item.get("id") == edge.get("from")), None)
        target = next((item for item in workloads if item.get("id") == edge.get("to")), None)
        if source and target:
            source_aliases = runtime_aliases.get(str(source.get("id") or ""), [])
            target_aliases = runtime_aliases.get(str(target.get("id") or ""), [])
            for source_alias in source_aliases:
                for target_alias in target_aliases:
                    lines.append(
                        f"{source_alias} -[#2f6b50]-> {target_alias} : {_text(edge.get('label') or 'connects')}"
                    )
    database_aliases = [
        alias
        for workload in workloads
        if workload.get("stateMode") == "persistent"
        for alias in runtime_aliases.get(str(workload.get("id") or ""), [])
    ]
    application_aliases = (
        runtime_aliases.get(str(primary_application.get("id") or ""), [])
        if primary_application
        else []
    )
    for alias in database_aliases:
        lines.append(f"postgres_image_dependency -[#2f6b50]-> {alias} : runs pinned image")
        lines.append(f"workload_outbound_dependency ..[#8a6d3b]> {alias} : reaches Docker Hub")
        if state_secret_binding:
            lines.append(
                f"state_secret_binding_dependency ..[#8a6d3b]> {alias} : injects DB credentials"
            )
        elif app_secret_binding:
            lines.append(
                f"app_secret_binding_dependency ..[#8a6d3b]> {alias} : injects DB credentials"
            )
    for traffic_filter in traffic_filters:
        filter_id = str(traffic_filter.get("id") or "policy")
        provider_kind = str(traffic_filter.get("providerKind") or "")
        if provider == "aws" and provider_kind == "load-balancer-security-group":
            lines.append(
                f"traffic_filter_{_id(filter_id)} ..[#8a6d3b]> public_ingress : allows HTTP port 80"
            )
            continue
        if provider == "aws" and provider_kind == "application-ingress-rule":
            lines.append(
                "traffic_filter_load_balancer_security_group ..[#8a6d3b]> "
                f"traffic_filter_{_id(filter_id)} : allowed source"
            )
            lines.append(
                f"traffic_filter_{_id(filter_id)} ..[#8a6d3b]> "
                "traffic_filter_security_group : permits load-balancer source"
            )
            continue
        targets = database_aliases if "postgresql" in filter_id else application_aliases
        for target_alias in targets:
            lines.append(
                f"traffic_filter_{_id(filter_id)} ..[#8a6d3b]> {target_alias} : allows required traffic"
            )
    if disk:
        attachment = next(
            (
                item
                for item in plan.get("edges") or []
                if item.get("from") == "disk-attachment"
                and item.get("to") != "disk"
                and item.get("label") == "binds"
            ),
            None,
        )
        if attachment:
            owner_ref = str(attachment.get("to") or "")
            owner_alias = (
                "primary_compute"
                if owner_ref == primary_compute_id
                else f"compute_{_id(owner_ref)}"
            )
            lines.append(
                f"{owner_alias} ..[#6f7c73]> persistent_disk : attach; format-if-empty; UUID mount; Docker bind"
            )
    if plan.get("unresolved"):
        lines.extend(
            [
                "note bottom",
                "  Deployment inputs remain unresolved; IaC promotion is blocked.",
                "end note",
            ]
        )
    lines.extend(
        [
            "legend bottom",
            "  |= Line |= Meaning |",
            "  | <color:#2f6b50>solid arrow</color> | runtime request or data flow |",
            "  | <color:#8a6d3b>dotted arrow</color> | health or traffic policy configuration |",
            "  | <color:#6f7c73>dotted arrow</color> | placement or attachment |",
            "endlegend",
            "@enduml",
        ]
    )
    return "\n".join(lines)


def render_provisioning_dependencies(bundle: dict[str, Any]) -> str:
    """Render prerequisite -> dependent creation relationships only."""
    projection = _primary(bundle)
    if projection is None or projection.get("status") not in {
        "completed",
        "needsInput",
    }:
        return _fallback(bundle, "Provisioning dependencies require one resolved provider target.")
    plan = dict(projection.get("resourcePlan") or {})
    provider = str(projection.get("provider") or "")
    region = str(projection.get("region") or "")
    topology = dict(projection.get("topology") or {})
    family_id = str(topology.get("familyId") or "")
    placement_constraints = dict(plan.get("placementConstraints") or {})
    ingress_zones = list(
        placement_constraints.get("selectedIngressZones")
        or topology.get("selectedIngressZones")
        or topology.get("selectedZones")
        or []
    )
    projected_nodes = {
        str(node.get("id") or ""): node
        for node in plan.get("nodes") or []
        if node.get("entityClass") in {"providerResource", "providerComponent", "externalArtifact"}
    }
    folded = {
        node_id: node
        for node_id, node in projected_nodes.items()
        if str(node.get("providerKind") or "") in _FOLDED_RELATION_KINDS.get(provider, set())
    }
    included = {node_id: node for node_id, node in projected_nodes.items() if node_id not in folded}
    lines = [
        "@startuml",
        "!theme plain",
        "top to bottom direction",
        "skinparam shadowing false",
        "skinparam linetype polyline",
        "skinparam nodesep 12",
        (
            f"title Provisioning dependencies - {_provider_label(provider)} / {_text(region)}"
            f"\\n{_text(family_id)}"
        ),
    ]
    provision_aliases: dict[str, list[str]] = {}
    workload_names = {
        str(workload.get("id") or ""): _text(workload.get("name") or "Workload")
        for workload in plan.get("workloads") or []
    }
    display_name_counts: dict[str, int] = {}
    for node in included.values():
        name = _text(node.get("name"))
        display_name_counts[name] = display_name_counts.get(name, 0) + 1
    for node_id, node in sorted(included.items()):
        handling = str(node.get("handling") or "create")
        stereotype = "reference" if handling == "referenceExisting" else handling
        node_name = _text(node.get("name") or node_id)
        if node_id == "compute-group":
            replica_count = int(topology.get("replicaCount") or 1)
            zones = list(topology.get("selectedZones") or [])
            placement = ", ".join(zones) if zones else "selected zone"
            node_name = (
                f"{node_name}\\ndesired capacity: {replica_count}\\nplacement: {_text(placement)}"
            )
        if display_name_counts.get(node_name, 0) > 1:
            logical_ref = str(node.get("logicalRef") or "")
            role = workload_names.get(logical_ref, "Application")
            node_name = f"{node_name}\\n{role}"
        minimum_count = int(node.get("minimumCount") or 1)
        if minimum_count > 1:
            aliases: list[str] = []
            node_zones = (
                ingress_zones
                if node_id in {"ingress-subnet", "ingress-route-association"}
                else list(topology.get("selectedZones") or [])
            )
            for index in range(minimum_count):
                alias = f"provision_{_id(node_id)}_{index + 1}"
                zone = node_zones[index] if index < len(node_zones) else f"distinct AZ {index + 1}"
                aliases.append(alias)
                lines.append(
                    f'node "{node_name} {index + 1}\\n{_text(zone)}" as {alias} <<{_text(stereotype)}>>'
                )
            provision_aliases[node_id] = aliases
        else:
            alias = f"provision_{_id(node_id)}"
            provision_aliases[node_id] = [alias]
            lines.append(f'node "{node_name}" as {alias} <<{_text(stereotype)}>>')
    for edge in plan.get("edges") or []:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        label = str(edge.get("label") or "depends on")
        if (
            source not in included
            or target not in included
            or label not in _PROVISIONING_RELATIONSHIPS
        ):
            continue
        # ResourcePlan edges are dependent -> prerequisite.  This view deliberately
        # reverses them so every arrow can be read prerequisite -> dependent.
        relationship = {
            "belongs to": "contains",
            "attaches": "attachment input for",
            "binds": "binding input for",
            "checks with": "health policy for",
            "contains instance": "add to group",
            "contains role": "role for",
            "configures": "configuration input for",
            "creates instances from": "template for",
            "depends on": "required by",
            "evaluates targets with": "health policy for",
            "forwards to": "default target for",
            "grants pull access to": "pull principal for",
            "grants secret read to": "secret principal for",
            "is attached to": "attachment point for",
            "is deployed in": "deployment container for",
            "is placed in": "placement for",
            "joins": "membership input for",
            "joins through": "membership input for",
            "matches": "listener input for",
            "places instances in": "placement input for",
            "provides egress for": "egress provider for",
            "pulls image digest from": "image source for",
            "registers instance": "instance input for",
            "registers instances with": "registration target for",
            "registers with": "registration target for",
            "routes to": "route target for",
            "scopes pull access to": "pull scope for",
            "scopes secret read to": "secret scope for",
            "serves region of": "regional network for",
            "selects subnetwork": "subnetwork input for",
            "uses backend": "backend for",
            "uses identity": "runtime identity for",
            "uses image": "boot image for",
            "uses policy": "policy for",
            "uses secret identity": "secret identity for",
            "exposes": "associate address",
            "addresses": "associate address",
            "uses": "referenced by",
            "uses address": "assign address",
        }.get(label, "required by")
        prerequisite_aliases = provision_aliases[target]
        dependent_aliases = provision_aliases[source]
        if target == "ingress-subnet" and source == "nat-gateway":
            prerequisite_aliases = prerequisite_aliases[:1]
        pairs: list[tuple[str, str]]
        if (
            label == "binds"
            and len(prerequisite_aliases) == len(dependent_aliases)
            and len(prerequisite_aliases) > 1
        ):
            pairs = list(zip(prerequisite_aliases, dependent_aliases, strict=True))
        else:
            pairs = [
                (prerequisite_alias, dependent_alias)
                for prerequisite_alias in prerequisite_aliases
                for dependent_alias in dependent_aliases
            ]
        for prerequisite_alias, dependent_alias in pairs:
            lines.append(
                f"{prerequisite_alias} -[#6f7780,dashed]-> {dependent_alias} : {_text(relationship)}"
            )
    folded_lines: set[tuple[str, str, str]] = set()
    for relation_id, relation_node in sorted(folded.items()):
        neighbors: list[str] = []
        principal = ""
        for edge in plan.get("edges") or []:
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            label = str(edge.get("label") or "")
            if source == relation_id and target in included:
                if label == "is deployed in":
                    continue
                neighbors.append(target)
                if label in {"grants pull access to", "grants secret read to"}:
                    principal = target
            elif target == relation_id and source in included:
                neighbors.append(source)
        neighbors = list(dict.fromkeys(neighbors))
        if len(neighbors) < 2:
            continue
        anchor = principal if principal in neighbors else neighbors[0]
        relation_label = _FOLDED_RELATION_LABELS.get(
            str(relation_node.get("providerKind") or ""), "associated"
        )
        for other in neighbors:
            if other == anchor:
                continue
            anchor_aliases = provision_aliases.get(anchor, [])
            other_aliases = provision_aliases.get(other, [])
            if len(anchor_aliases) == len(other_aliases) and len(anchor_aliases) > 1:
                relation_pairs = zip(anchor_aliases, other_aliases, strict=True)
            else:
                relation_pairs = (
                    (anchor_alias, other_alias)
                    for anchor_alias in anchor_aliases
                    for other_alias in other_aliases
                )
            for anchor_alias, other_alias in relation_pairs:
                key = tuple(sorted((anchor_alias, other_alias))) + (relation_label,)
                if key in folded_lines:
                    continue
                folded_lines.add(key)
                lines.append(
                    f"{anchor_alias} -[#c47713,dashed]- {other_alias} : {_text(relation_label)}"
                )
    if plan.get("unresolved"):
        lines.extend(
            [
                "note bottom",
                "  Deployment inputs remain unresolved; IaC promotion is blocked.",
                "end note",
            ]
        )
    lines.extend(
        [
            "legend bottom",
            "  Arrow: prerequisite -> dependent.",
            "  Line: an IaC attachment, association, permission, or route applied between resources.",
            "  Runtime traffic is intentionally omitted.",
            "endlegend",
            "@enduml",
        ]
    )
    return "\n".join(lines)


def deployment_bundle_runtime_puml(bundle: dict[str, Any]) -> str:
    if bundle.get("schemaVersion") != "easydep-deployment-diagram/v1":
        bundle = {
            "schemaVersion": "easydep-deployment-diagram/v1",
            "mode": "legacyLogicalOnly",
            "logicalModel": bundle,
            "resourceSpec": {},
            "projections": [],
        }
    return render_runtime_deployment(bundle)


def deployment_bundle_provisioning_puml(bundle: dict[str, Any]) -> str:
    if bundle.get("schemaVersion") != "easydep-deployment-diagram/v1":
        bundle = {
            "schemaVersion": "easydep-deployment-diagram/v1",
            "mode": "legacyLogicalOnly",
            "logicalModel": bundle,
            "resourceSpec": {},
            "projections": [],
        }
    return render_provisioning_dependencies(bundle)
