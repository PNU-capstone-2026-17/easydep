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
    "configures",
    "selects subnetwork",
    "creates instances from",
    "depends on",
    "evaluates targets with",
    "exposes",
    "forwards to",
    "is attached to",
    "is placed in",
    "joins",
    "joins through",
    "matches",
    "places instances in",
    "provides egress for",
    "registers instances with",
    "registers with",
    "routes to",
    "serves region of",
    "uses",
    "uses address",
    "uses backend",
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
        puml = "@startuml\n!theme plain\nnode \"Deployment target unresolved\"\n@enduml"
    if message:
        puml = puml.replace(
            "@enduml", f'note bottom\n  {_text(message)}\nend note\n@enduml'
        )
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

    compute_kind = "compute-group" if topology.get("computeManagement") == "managedGroup" else "compute-instance"
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
                ingress_zones[index]
                if index < len(ingress_zones)
                else f"distinct AZ {index + 1}"
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
        f"\\nfixed capacity: {replicas}"
        if managed_group
        else "\\n1 standalone instance"
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
                shape = (
                    "database"
                    if workload.get("designKind") == "database"
                    else "component"
                )
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
            shape = (
                "database" if workload.get("designKind") == "database" else "component"
            )
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
            shape = "database" if workload.get("designKind") == "database" else "component"
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
            lines.append(
                f'    node "{_text(public_ip.get("name"))}" as public_address'
            )
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
            if provider_kind
            not in {"firewall", "security-group"}
            or filter_role.lower() in filter_name.lower()
            else f"{filter_name}\\n{filter_role}"
        )
        lines.append(
            f'      node "{display_name}" as traffic_filter_{_id(filter_id)} <<traffic policy>>'
        )
    if load_balanced and provider == "aws":
        minimum_ingress_subnets = int(
            placement_constraints.get("minimumIngressSubnets") or 1
        )
        for index in range(minimum_ingress_subnets):
            zone = (
                ingress_zones[index]
                if index < len(ingress_zones)
                else f"distinct AZ {index + 1}"
            )
            lines.append(
                f'      node "Ingress Subnet {index + 1}\\n{_text(zone)}" as ingress_subnet_{index + 1}'
            )
        lines.extend(
            [
                f'      node "{ingress_name}" as public_ingress <<HTTP ingress>>',
                f'      component "{_text(nodes.get("listener", {}).get("name"))}" as ingress_listener',
                f'      node "{_text(nodes.get("backend-group", {}).get("name"))}" as ingress_backend_group',
                f'      component "{_text(nodes.get("health-check", {}).get("name"))}" as ingress_health_check <<health policy>>',
            ]
        )
    elif load_balanced and provider == "azure":
        lines.extend(
            [
                f'      node "{_text(nodes.get("ingress-subnet", {}).get("name") or "Application Gateway Subnet")}\\n<<dedicated>>" as gateway_subnet {{',
                f'        node "{ingress_name}" as public_ingress <<HTTP ingress>> {{',
                f'          component "{_text(nodes.get("frontend-ip-config", {}).get("name"))}" as ingress_frontend_ip_config',
                f'          component "{_text(nodes.get("frontend-port", {}).get("name"))}" as ingress_frontend_port',
                f'          component "{_text(nodes.get("listener", {}).get("name"))}" as ingress_listener',
                f'          component "{_text(nodes.get("routing-rule", {}).get("name"))}" as ingress_routing_rule',
                f'          component "{_text(nodes.get("backend-group", {}).get("name"))}" as ingress_backend_group',
                f'          component "{_text(nodes.get("backend-settings", {}).get("name"))}" as ingress_backend_settings',
                f'          component "{_text(nodes.get("health-check", {}).get("name"))}" as ingress_health_check <<health policy>>',
                "        }",
                "      }",
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
                f'  node "{_text(public_ip.get("name") if public_ip else "Global External IP Address")}\\n<<global>>" as public_address',
                f'  node "{ingress_name}\\n<<global>>" as public_ingress <<HTTP ingress>>',
                f'  node "{_text(nodes.get("target-http-proxy", {}).get("name"))}\\n<<global>>" as ingress_target_proxy',
                f'  node "{_text(nodes.get("url-map", {}).get("name"))}\\n<<global>>" as ingress_url_map',
                f'  node "{_text(nodes.get("backend-service", {}).get("name"))}\\n<<global>>" as ingress_backend_service',
                f'  node "{_text(nodes.get("health-check", {}).get("name"))}\\n<<global>>" as ingress_health_check <<health policy>>',
            ]
        )
    lines.extend(["}", ""])
    if provider == "gcp":
        lines.append(
            "provider_network ..[#6f7c73]> provider_subnet : contains regional subnetwork"
        )

    if minimum_subnets > 1:
        for replica in range(1, replicas + 1):
            subnet_index = (
                (replica - 1) % len(subnet_aliases)
                if topology.get("zoneLayout") == "multiZoneSpread"
                else 0
            )
            target_alias = (
                f"primary_instance_{replica}" if managed_group else "primary_compute"
            )
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
        minimum_ingress_subnets = int(
            placement_constraints.get("minimumIngressSubnets") or 1
        )
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
        lines.append(
            f"{endpoint_alias} -[#2f6b50]-> public_address : accepts HTTP"
        )
    elif endpoint and load_balanced:
        lines.append(
            f"{endpoint_alias} -[#2f6b50]-> public_ingress : accepts HTTP"
        )
    if load_balanced and public_ip and provider != "azure":
        lines.append(
            "public_address -[#2f6b50]-> public_ingress : receives HTTP for"
        )
    primary_application = next(
        (
            item
            for item in workloads
            if str(item.get("id") or "") in primary_workload_ids
            and item.get("designKind") != "database"
        ),
        None,
    )
    if primary_application:
        application_aliases = runtime_aliases.get(
            str(primary_application.get("id") or ""), []
        )
        if load_balanced:
            if provider == "aws":
                lines.extend(
                    [
                        "public_ingress -[#2f6b50]-> ingress_listener : accepts HTTP on port 80",
                        "ingress_listener -[#2f6b50]-> ingress_backend_group : selects target group",
                    ]
                )
                backend_alias = "ingress_backend_group"
            elif provider == "azure":
                lines.extend(
                    [
                        "public_address -[#2f6b50]-> ingress_frontend_ip_config : assigned frontend address",
                        "ingress_frontend_ip_config -[#2f6b50]-> ingress_listener : accepts HTTP",
                        "ingress_frontend_port ..[#8a6d3b]> ingress_listener : HTTP port 80",
                        "ingress_listener -[#2f6b50]-> ingress_routing_rule : matches",
                        "ingress_routing_rule -[#2f6b50]-> ingress_backend_group : selects backend pool",
                        "ingress_backend_settings ..[#8a6d3b]> ingress_backend_group : connection settings",
                    ]
                )
                backend_alias = "ingress_backend_group"
            else:
                lines.extend(
                    [
                        "public_ingress -[#2f6b50]-> ingress_target_proxy : targets HTTP proxy",
                        "ingress_target_proxy -[#2f6b50]-> ingress_url_map : applies URL map",
                        "ingress_url_map -[#2f6b50]-> ingress_backend_service : selects backend service",
                    ]
                )
                backend_alias = (
                    "primary_compute"
                    if managed_group
                    else "ingress_backend_group"
                )
                lines.append(
                    f"ingress_backend_service -[#2f6b50]-> {backend_alias} : selects instance group"
                )
            for replica, alias in enumerate(application_aliases, start=1):
                lines.append(
                    f"{backend_alias} -[#2f6b50]-> {alias} : forwards to replica {replica}"
                )
                lines.append(
                    f"ingress_health_check ..[#8a6d3b]> {alias} : probes health"
                )
        elif public_ip and application_aliases:
            lines.append(
                f"public_address -[#2f6b50]-> {application_aliases[0]} : HTTP"
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
                        f'{source_alias} -[#2f6b50]-> {target_alias} : {_text(edge.get("label") or "connects")}'
                    )
    database_aliases = [
        alias
        for workload in workloads
        if workload.get("designKind") == "database"
        for alias in runtime_aliases.get(str(workload.get("id") or ""), [])
    ]
    application_aliases = (
        runtime_aliases.get(str(primary_application.get("id") or ""), [])
        if primary_application
        else []
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
        targets = (
            database_aliases if "postgresql" in filter_id else application_aliases
        )
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
                f'{owner_alias} ..[#6f7c73]> persistent_disk : attached block device'
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
    included = {
        str(node.get("id") or ""): node
        for node in plan.get("nodes") or []
        if node.get("entityClass") in {"providerResource", "providerComponent", "externalArtifact"}
    }
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
                f"{node_name}\\ndesired capacity: {replica_count}"
                f"\\nplacement: {_text(placement)}"
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
                zone = (
                    node_zones[index]
                    if index < len(node_zones)
                    else f"distinct AZ {index + 1}"
                )
                aliases.append(alias)
                lines.append(
                    f'node "{node_name} {index + 1}\\n{_text(zone)}" as {alias} <<{_text(stereotype)}>>'
                )
            provision_aliases[node_id] = aliases
        else:
            alias = f"provision_{_id(node_id)}"
            provision_aliases[node_id] = [alias]
            lines.append(
                f'node "{node_name}" as {alias} <<{_text(stereotype)}>>'
            )
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
            "configures": "configuration input for",
            "creates instances from": "template for",
            "depends on": "required by",
            "evaluates targets with": "health policy for",
            "forwards to": "default target for",
            "is attached to": "attachment point for",
            "is placed in": "placement for",
            "joins": "membership input for",
            "joins through": "membership input for",
            "matches": "listener input for",
            "places instances in": "placement input for",
            "provides egress for": "egress provider for",
            "registers instances with": "registration target for",
            "registers with": "registration target for",
            "routes to": "route target for",
            "serves region of": "regional network for",
            "selects subnetwork": "subnetwork input for",
            "uses backend": "backend for",
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
                f'{prerequisite_alias} -[#6f7780,dashed]-> {dependent_alias} : {_text(relationship)}'
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
            "  Every arrow means prerequisite -> dependent.",
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
