"""Deterministic provider-native deployment-diagram renderers.

The runtime view answers where software runs and how requests/data flow.  The
provisioning view answers which cloud objects must exist first.  Keeping those
semantics separate prevents one arrow from ambiguously meaning both traffic and
creation order.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# Only audited IaC references and ordering constraints belong in the
# provisioning view. New ResourcePlan reference labels must be reviewed and
# added deliberately; functional reachability such as firewall traffic is not a
# creation dependency.
_DISPLAYABLE_PROVISIONING_RELATIONSHIPS = {
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
# projected as labelled bindings between the cloud resources they join.
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

_FOLDED_ASSOCIATION_LABELS = {
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


def _display_image_reference(value: Any) -> str:
    reference = str(value or "prebuilt image")
    return re.sub(r"@sha256:[0-9a-fA-F]{32,}$", "", reference)


def _binding_text(value: Any) -> str:
    if isinstance(value, dict) and value.get("binding") == "late":
        return f"late:{value.get('field') or 'value'}"
    return _text(value)


def _compact_reference_role(path: Any) -> str:
    """Return a short, reader-facing discriminator for duplicate references."""

    leaf = str(path or "reference").split(".")[-1].replace("[]", "")
    leaf = re.sub(r"_(?:ids?|arns?|names?|values?)$", "", leaf)
    aliases = {
        "ami": "image",
        "image": "image",
        "vpc": "network",
        "virtual_network": "network",
        "network": "network",
        "vpc_zone_identifier": "subnets",
        "subnet": "subnet",
        "security_group": "traffic filter",
        "network_security_group": "traffic filter",
        "target_group": "backend",
        "backend_pool": "backend",
        "backend_service": "backend",
        "iam_instance_profile": "identity",
        "service_account": "identity",
        "role": "identity",
        "volume": "storage",
        "disk": "storage",
    }
    return aliases.get(leaf, leaf.replace("_", " "))


def _primary(bundle: dict[str, Any]) -> dict[str, Any] | None:
    projections = list(bundle.get("projections") or [])
    return projections[0] if len(projections) == 1 else None


def _fallback(bundle: dict[str, Any], message: str = "") -> str:
    del bundle
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


def _runtime_workload_shape(workload: dict[str, Any], *, current_style: bool) -> str:
    """Use shape for runtime role."""

    if current_style:
        return "component"
    return "database" if workload.get("stateMode") == "persistent" else "component"


def _persistent_workload(workload: dict[str, Any], *, current_style: bool) -> bool:
    if not current_style:
        return workload.get("stateMode") == "persistent"
    return any(
        storage.get("persistence") == "persistent"
        for storage in workload.get("storage") or []
    )


def _runtime_contract_lines(
    plan: dict[str, Any], workload: dict[str, Any]
) -> list[str]:
    """Keep long app/resource contracts inside the workload, not on arrows."""

    workload_id = str(workload.get("id") or "")
    contract_lines: list[str] = []
    bound_configuration: set[str] = set()
    for binding in plan.get("runtimeBindings") or []:
        if str(binding.get("workloadRef") or "") != workload_id:
            continue
        kind = str(binding.get("kind") or "")
        if kind == "endpointEnvironment":
            name = str(binding.get("environmentName") or "")
            if name:
                contract_lines.append(f"[env] {name}")
            bound_configuration.add(str(binding.get("configurationRef") or ""))
        elif kind == "secretEnvironment":
            name = str(binding.get("environmentName") or "")
            if name:
                contract_lines.append(f"[secret] {name}")
            bound_configuration.add(str(binding.get("configurationRef") or ""))
        elif kind == "storageMount":
            mount_path = binding.get("mountPath")
            if isinstance(mount_path, str) and mount_path:
                contract_lines.append(f"[mount] {mount_path}")
    for configuration in workload.get("configuration") or []:
        configuration_id = str(configuration.get("id") or "")
        name = str(configuration.get("name") or "")
        if name and configuration_id not in bound_configuration:
            contract_lines.append(f"[env] {name}")
    artifact_kind = str((workload.get("artifact") or {}).get("kind") or "")
    if artifact_kind == "generatedApplication":
        contract_lines.append("[image] digest binding")
    elif artifact_kind == "prebuiltImage":
        contract_lines.append("[image] pinned digest")
    return list(dict.fromkeys(contract_lines))


def _runtime_workload_label(
    plan: dict[str, Any],
    workload: dict[str, Any],
    *,
    fallback: str,
    replica: int | None = None,
) -> str:
    parts = [_text(workload.get("name") or fallback)]
    if replica is not None:
        parts.append(f"replica {replica}")
    parts.append("<<Docker container>>")
    contracts = _runtime_contract_lines(plan, workload)
    if contracts:
        parts.extend(["[runtime contract]", *contracts])
    return "\\n".join(parts)


def render_runtime_deployment(bundle: dict[str, Any]) -> str:
    """Render the primary deployment view: boundaries, placement, and traffic."""
    current_style = bundle.get("schemaVersion") == "easydep-deployment-diagram"
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

    if current_style:
        context = _render_context(bundle)
        plan = dict(context["plan"])
        render_settings = dict(context["settings"])
        logical_artifacts = list(context["logicalArtifacts"])
    else:
        plan = dict(projection.get("resourcePlan") or {})
        render_settings = dict(projection.get("topology") or {})
        render_settings["displayCaption"] = render_settings.get("familyId")
        logical_artifacts = [
            item
            for item in (
                (bundle.get("logicalModel") or {}).get("Artifacts")
                or (bundle.get("logicalModel") or {}).get("artifacts")
                or []
            )
            if isinstance(item, dict) and item.get("name")
        ]
    provider = str(projection.get("provider") or "")
    region = str(projection.get("region") or "")
    nodes = _node_by_kind(plan)
    workloads = list(plan.get("workloads") or [])
    allocations = {
        str(item.get("workloadRef") or ""): item for item in plan.get("allocations") or []
    }
    selected_zones = list(render_settings.get("selectedZones") or [])
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
        or render_settings.get("selectedIngressZones")
        or selected_zones
    )
    load_balanced = render_settings.get("publicIngress") == "loadBalanced"
    ingress_name = _text(
        (nodes.get("load-balancer") or nodes.get("forwarding-rule") or {}).get("name")
        or "Public load balancer"
    )

    compute_kind = (
        "compute-group"
        if render_settings.get("computeManagement") == "managedGroup"
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
    display_caption = str(render_settings.get("displayCaption") or "")
    has_public_endpoint = any(
        node.get("group") == "endpoint" for node in plan.get("nodes") or []
    )

    lines = [
        "@startuml",
        "!theme plain",
        "left to right direction",
        "skinparam shadowing false",
        "skinparam linetype ortho",
        "skinparam nodesep 55",
        "skinparam ranksep 65",
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
            + (f"\\n{_text(display_caption)}" if display_caption else "")
        ),
        'actor "Internet client" as internet_client',
        f'cloud "{_provider_label(provider)}" as provider_boundary {{',
    ]
    if current_style:
        lines[lines.index("skinparam cloud {") : lines.index("skinparam cloud {")] = [
            "skinparam rectangle {",
            "  BackgroundColor #f8faf8",
            "  BorderColor #68736b",
            "}",
        ]
    if current_style and not has_public_endpoint:
        lines.remove('actor "Internet client" as internet_client')
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
    replicas = int(render_settings.get("replicaCount") or 1)
    managed_group = render_settings.get("computeManagement") == "managedGroup"
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
                    if render_settings.get("zoneLayout") == "multiZoneSpread"
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
                shape = _runtime_workload_shape(workload, current_style=current_style)
                label = _runtime_workload_label(
                    plan, workload, fallback="Application", replica=replica
                )
                lines.append(
                    f'            {shape} "{label}" as {alias}'
                )
            lines.append("          }")
    else:
        for workload in workloads:
            workload_id = str(workload.get("id") or "")
            if workload_id not in primary_workload_ids:
                continue
            alias = _workload_alias(workload)
            runtime_aliases.setdefault(workload_id, []).append(alias)
            shape = _runtime_workload_shape(workload, current_style=current_style)
            label = _runtime_workload_label(
                plan, workload, fallback="Application"
            )
            lines.append(
                f'          {shape} "{label}" as {alias}'
            )
    lines.append("        }")
    external_compute_refs = sorted(
        {
            str(allocation.get("computeRef") or "")
            for allocation in allocations.values()
            if str(allocation.get("computeRef") or "") != primary_compute_id
        }
    )
    primary_subnet_closed = False
    if current_style and minimum_subnets == 1 and external_compute_refs:
        lines.append("      }")
        primary_subnet_closed = True
    for compute_ref in external_compute_refs:
        compute_node: dict[str, Any] = next(
            (node for node in plan.get("nodes") or [] if node.get("id") == compute_ref),
            {},
        )
        compute_alias = "compute_" + _id(compute_ref)
        external_policy = next(
            (
                item
                for item in (projection.get("deploymentPlan") or {}).get(
                    "computeUnits", []
                )
                if item.get("id") == compute_ref
            ),
            {},
        )
        external_replicas = int(external_policy.get("replicaCount") or 1)
        external_subnet = next(
            (
                node
                for node in plan.get("nodes") or []
                if node.get("providerKind") == "subnet"
                and str(node.get("id") or "").startswith(f"subnet-{compute_ref}-")
            ),
            {},
        )
        if current_style and external_subnet:
            external_zone = str((external_subnet.get("attributes") or {}).get("zone") or "")
            lines.append(
                f'      node "{_text(external_subnet.get("name") or "Application Subnet")}\\n{_text(external_zone or "selected zone")}" as subnet_{_id(compute_ref)} {{'
            )
        if current_style:
            external_name = compute_node.get("name") or "Compute"
            external_suffix = (
                f"fixed capacity: {external_replicas}"
                if external_policy.get("kind") == "managedVmGroup"
                else "1 standalone instance"
            )
        else:
            external_name = compute_node.get("name") or "State VM"
            external_suffix = "1 state instance; single zone"
        lines.append(
            f'        node "{_text(external_name)}\\n{_text(external_suffix)}" as {compute_alias} {{'
        )
        for workload in workloads:
            allocation = allocations.get(str(workload.get("id") or ""), {})
            if str(allocation.get("computeRef") or "") != compute_ref:
                continue
            workload_id = str(workload.get("id") or "")
            alias = _workload_alias(workload)
            runtime_aliases.setdefault(workload_id, []).append(alias)
            shape = _runtime_workload_shape(workload, current_style=current_style)
            label = _runtime_workload_label(
                plan, workload, fallback="State workload"
            )
            lines.append(
                f'          {shape} "{label}" as {alias}'
            )
        lines.append("        }")
        if current_style and external_subnet:
            lines.append("      }")
    if minimum_subnets == 1 and not primary_subnet_closed:
        lines.append("      }")
    disks = [
        node
        for node in plan.get("nodes") or []
        if node.get("providerKind") == "disk"
    ]
    disk = disks[0] if disks else None
    disk_aliases = {
        str((item.get("attributes") or {}).get("storageRef") or item.get("id")): (
            "persistent_disk" if index == 0 else f"persistent_disk_{_id(str(item.get('id') or index))}"
        )
        for index, item in enumerate(disks)
    }
    public_ip = nodes.get("public-ip")
    if provider == "gcp":
        if public_ip and not load_balanced:
            lines.append(f'    node "{_text(public_ip.get("name"))}" as public_address')
        for item in disks:
            storage_ref = str((item.get("attributes") or {}).get("storageRef") or item.get("id"))
            lines.append(
                f'    database "{_text(item.get("name"))}" as {disk_aliases[storage_ref]} <<zonal; retained>>'
            )
        if load_balanced and nodes.get("backend-group"):
            backend_shape = "rectangle" if current_style else "node"
            backend_role = " <<backend selection>>" if current_style else ""
            lines.append(
                f'    {backend_shape} "{_text(nodes["backend-group"].get("name"))}\\n<<zonal>>" as ingress_backend_group{backend_role}'
            )
        lines.append("  }")
    for traffic_filter in traffic_filters:
        filter_id = str(traffic_filter.get("id") or "policy")
        provider_kind = str(traffic_filter.get("providerKind") or "")
        filter_role = "PostgreSQL" if "postgresql" in filter_id else "Application"
        filter_name = _text(traffic_filter.get("name"))
        if current_style:
            display_role = _text(traffic_filter.get("displayRole") or filter_role)
            display_name = (
                filter_name
                if display_role.lower() in filter_name.lower()
                else f"{filter_name}\\n{display_role}"
            )
        else:
            display_name = (
                filter_name
                if provider_kind not in {"firewall", "security-group"}
                or filter_role.lower() in filter_name.lower()
                else f"{filter_name}\\n{filter_role}"
            )
        policy_shape = "rectangle" if current_style else "node"
        lines.append(
            f'      {policy_shape} "{display_name}" as traffic_filter_{_id(filter_id)} <<traffic policy>>'
        )
    if load_balanced and provider == "aws":
        routing_shape = "rectangle" if current_style else "component"
        backend_shape = "rectangle" if current_style else "node"
        health_shape = "rectangle" if current_style else "component"
        routing_role = " <<routing policy>>" if current_style else ""
        backend_role = " <<backend selection>>" if current_style else ""
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
                f'      {routing_shape} "{_text(nodes.get("listener", {}).get("name"))}" as ingress_listener{routing_role}',
                f'      {backend_shape} "{_text(nodes.get("backend-group", {}).get("name"))}" as ingress_backend_group{backend_role}',
                f'      {health_shape} "{_text(nodes.get("health-check", {}).get("name"))}" as ingress_health_check <<health policy>>',
            ]
        )
    elif load_balanced and provider == "azure":
        configuration_shape = "rectangle" if current_style else "component"
        backend_shape = "rectangle" if current_style else "node"
        frontend_role = " <<frontend configuration>>" if current_style else ""
        routing_role = " <<routing policy>>" if current_style else ""
        backend_role = " <<backend selection>>" if current_style else ""
        lines.extend(
            [
                f'      node "{ingress_name}" as public_ingress <<TCP ingress>> {{',
                f'        {configuration_shape} "{_text(nodes.get("frontend-ip-config", {}).get("name"))}" as ingress_frontend_ip_config{frontend_role}',
                "      }",
                f'      {configuration_shape} "{_text(nodes.get("routing-rule", {}).get("name"))}" as ingress_routing_rule{routing_role}',
                f'      {backend_shape} "{_text(nodes.get("backend-group", {}).get("name"))}" as ingress_backend_group{backend_role}',
                f'      {configuration_shape} "{_text(nodes.get("health-check", {}).get("name"))}" as ingress_health_check <<health policy>>',
            ]
        )
    if provider != "gcp":
        lines.append("    }")
        if public_ip:
            lines.append(f'    node "{_text(public_ip.get("name"))}" as public_address')
        for item in disks:
            storage_ref = str((item.get("attributes") or {}).get("storageRef") or item.get("id"))
            lines.append(
                f'    database "{_text(item.get("name"))}" as {disk_aliases[storage_ref]} <<retained>>'
            )
        lines.append("  }")
    elif load_balanced:
        managed_shape = "rectangle" if current_style else "node"
        backend_role = " <<backend selection>>" if current_style else ""
        lines.extend(
            [
                f'  node "{_text(public_ip.get("name") if public_ip else "Regional External IP Address")}\\n<<regional>>" as public_address',
                f'  node "{ingress_name}\\n<<regional>>" as public_ingress <<TCP ingress>>',
                f'  {managed_shape} "{_text(nodes.get("backend-service", {}).get("name"))}\\n<<regional>>" as ingress_backend_service{backend_role}',
                f'  {managed_shape} "{_text(nodes.get("health-check", {}).get("name"))}\\n<<regional>>" as ingress_health_check <<health policy>>',
            ]
        )
    lines.extend(["}", ""])
    registry = nodes.get("app-registry")
    app_identity = nodes.get("registry-pull-identity")
    secret_ref = nodes.get("secret-ref")
    app_secret_binding = nodes.get("secret-access-binding")
    state_secret_binding = nodes.get("state-secret-access-binding")
    persistent_workload_present = any(
        _persistent_workload(workload, current_style=current_style) for workload in workloads
    )
    generated_image_aliases: dict[str, str] = {}
    prebuilt_image_aliases: dict[str, str] = {}
    secret_binding_aliases: dict[str, list[str]] = {}
    secret_identity_aliases: dict[str, str] = {}
    if current_style:
        generated_workloads = [
            workload
            for workload in workloads
            if (workload.get("artifact") or {}).get("kind")
            == "generatedApplication"
        ]
        prebuilt_workloads = [
            workload
            for workload in workloads
            if (workload.get("artifact") or {}).get("kind") == "prebuiltImage"
        ]
        if generated_workloads:
            lines.extend(
                [
                    'actor "User-run deploy.sh\\n<<local CSP authentication>>" as user_deploy',
                    'frame "Application delivery dependencies" as delivery_dependencies {',
                    '  cloud "VM outbound path\\n(public address or managed NAT)" as workload_outbound_dependency',
                ]
            )
            for workload in generated_workloads:
                workload_id = str(workload.get("id") or "application")
                alias_id = _id(workload_id)
                registry_node = next(
                    (
                        node
                        for node in plan.get("nodes") or []
                        if node.get("id") == f"registry-{workload_id}"
                    ),
                    {},
                )
                compute_ref = str(
                    allocations.get(workload_id, {}).get("computeRef") or ""
                )
                identity_node = next(
                    (
                        node
                        for node in plan.get("nodes") or []
                        if node.get("id")
                        == f"registry-pull-identity-{compute_ref}"
                    ),
                    {},
                )
                source_alias = f"application_source_artifact_{alias_id}"
                registry_alias = f"application_registry_dependency_{alias_id}"
                identity_alias = f"application_identity_dependency_{alias_id}"
                if identity_node.get("id"):
                    secret_identity_aliases[str(identity_node.get("id"))] = identity_alias
                image_alias = f"application_image_dependency_{alias_id}"
                generated_image_aliases[workload_id] = image_alias
                lines.extend(
                    [
                        f'  node "{_text(registry_node.get("name") or "Container Registry")}" as {registry_alias}',
                        f'  node "{_text(identity_node.get("name") or "VM Registry Pull Identity")}" as {identity_alias}',
                        f'  artifact "{_text(workload.get("name") or workload_id)} image source" as {source_alias}',
                        f'  artifact "{_text(workload.get("name") or workload_id)} image@sha256\\n<<immutable digest>>" as {image_alias}',
                    ]
                )
            lines.append("}")
            for workload_id, image_alias in generated_image_aliases.items():
                alias_id = _id(workload_id)
                lines.extend(
                    [
                        f"user_deploy -[#2f6b50]-> application_registry_dependency_{alias_id} : create",
                        f"user_deploy -[#2f6b50]-> {image_alias} : push",
                        f"application_registry_dependency_{alias_id} -[#2f6b50]-> {image_alias}",
                        f"application_identity_dependency_{alias_id} ..[#8a6d3b]> {image_alias} : pull",
                        f"application_source_artifact_{alias_id} -[#2f6b50]-> {image_alias} : build",
                    ]
                )
        if prebuilt_workloads:
            lines.append('frame "Prebuilt image dependencies" as prebuilt_dependencies {')
            for workload in prebuilt_workloads:
                workload_id = str(workload.get("id") or "workload")
                image_alias = f"prebuilt_image_dependency_{_id(workload_id)}"
                prebuilt_image_aliases[workload_id] = image_alias
                image = _display_image_reference(
                    (workload.get("artifact") or {}).get("image")
                )
                lines.append(
                    f'  artifact "{_text(image)}\\n<<explicit prebuilt image; immutable digest>>" as {image_alias}'
                )
            lines.append("}")
        secret_nodes = [
            node
            for node in plan.get("nodes") or []
            if node.get("providerKind") == "secret-ref"
        ]
        binding_nodes = [
            node
            for node in plan.get("nodes") or []
            if node.get("providerKind")
            in {"secret-access-binding", "state-secret-access-binding"}
        ]
        identity_nodes = [
            node
            for node in plan.get("nodes") or []
            if node.get("providerKind") in {"state-secret-identity", "secret-identity"}
        ]
        if secret_nodes or binding_nodes or identity_nodes:
            lines.append('frame "Secret runtime dependencies" as secret_dependencies {')
            for node in secret_nodes:
                lines.append(
                    f'  artifact "{_text(node.get("name") or "Existing provider Secret")}" as secret_{_id(str(node.get("id") or "secret"))}'
                )
            for node in binding_nodes:
                node_id = str(node.get("id") or "binding")
                alias = f"secret_binding_{_id(node_id)}"
                lines.append(
                    f'  node "{_text(node.get("name") or "Secret read binding")}" as {alias}'
                )
                for workload in workloads:
                    workload_id = str(workload.get("id") or "")
                    if f"-{workload_id}-" in node_id:
                        secret_binding_aliases.setdefault(workload_id, []).append(alias)
            for node in identity_nodes:
                node_id = str(node.get("id") or "identity")
                alias = f"secret_identity_{_id(node_id)}"
                secret_identity_aliases[node_id] = alias
                lines.append(
                    f'  node "{_text(node.get("name") or "Runtime Secret identity")}" as {alias}'
                )
            lines.append("}")
    elif registry:
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
    if persistent_workload_present and not current_style:
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
                if render_settings.get("zoneLayout") == "multiZoneSpread"
                else 0
            )
            target_alias = f"primary_instance_{replica}" if managed_group else "primary_compute"
            lines.append(
                f"{subnet_aliases[subnet_index]} ..[#6f7c73]> {target_alias}"
            )
            if not managed_group:
                break
        if not current_style:
            for compute_ref in external_compute_refs:
                lines.append(
                    f"{subnet_aliases[0]} ..[#6f7c73]> compute_{_id(compute_ref)} : places state VM"
                )
    if load_balanced and provider == "aws":
        minimum_ingress_subnets = int(placement_constraints.get("minimumIngressSubnets") or 1)
        for index in range(minimum_ingress_subnets):
            lines.append(
                f"ingress_subnet_{index + 1} ..[#6f7c73]> public_ingress"
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
        lines.append(f"{endpoint_alias} -[#2f6b50]-> public_address")
    elif endpoint and load_balanced:
        lines.append(f"{endpoint_alias} -[#2f6b50]-> public_ingress")
    if load_balanced and public_ip and provider != "azure":
        lines.append("public_address -[#2f6b50]-> public_ingress")
    primary_application = next(
        (
            item
            for item in workloads
            if str(item.get("id") or "") in primary_workload_ids
            and not _persistent_workload(item, current_style=current_style)
        ),
        None,
    )
    if primary_application:
        application_aliases = runtime_aliases.get(str(primary_application.get("id") or ""), [])
        if load_balanced:
            if provider == "aws":
                lines.extend(
                    [
                        "public_ingress -[#2f6b50]-> ingress_listener : TCP :80",
                        "ingress_listener -[#2f6b50]-> ingress_backend_group",
                    ]
                )
                backend_alias = "ingress_backend_group"
            elif provider == "azure":
                lines.extend(
                    [
                        "public_address -[#2f6b50]-> ingress_frontend_ip_config : assigned frontend address",
                        "ingress_frontend_ip_config -[#2f6b50]-> ingress_routing_rule : TCP :80",
                        "ingress_routing_rule -[#2f6b50]-> ingress_backend_group",
                    ]
                )
                backend_alias = "ingress_backend_group"
            else:
                lines.extend(
                    [
                        "public_ingress -[#2f6b50]-> ingress_backend_service : TCP :80",
                    ]
                )
                backend_alias = "primary_compute" if managed_group else "ingress_backend_group"
                lines.append(
                    f"ingress_backend_service -[#2f6b50]-> {backend_alias}"
                )
            for alias in application_aliases:
                lines.append(f"{backend_alias} -[#2f6b50]-> {alias}")
                lines.append(f"ingress_health_check ..[#8a6d3b]> {alias}")
        elif public_ip and application_aliases:
            lines.append(f"public_address -[#2f6b50]-> {application_aliases[0]} : HTTP")
        for alias in application_aliases:
            if registry and not current_style:
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
            connection_label = str(edge.get("label") or "connects").upper()
            source_aliases = runtime_aliases.get(str(source.get("id") or ""), [])
            target_aliases = runtime_aliases.get(str(target.get("id") or ""), [])
            for source_index, source_alias in enumerate(source_aliases):
                for target_index, target_alias in enumerate(target_aliases):
                    label = (
                        f" : {_text(connection_label)}"
                        if source_index == 0 and target_index == 0
                        else ""
                    )
                    lines.append(
                        f"{source_alias} -[#2f6b50]-> {target_alias}{label}"
                    )
    database_aliases = [
        alias
        for workload in workloads
        if _persistent_workload(workload, current_style=current_style)
        for alias in runtime_aliases.get(str(workload.get("id") or ""), [])
    ]
    application_aliases = (
        runtime_aliases.get(str(primary_application.get("id") or ""), [])
        if primary_application
        else []
    )
    for alias in database_aliases if not current_style else []:
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
    if current_style:
        for workload in workloads:
            workload_id = str(workload.get("id") or "")
            aliases = runtime_aliases.get(workload_id, [])
            image_alias = generated_image_aliases.get(workload_id)
            prebuilt_alias = prebuilt_image_aliases.get(workload_id)
            for alias_index, alias in enumerate(aliases):
                if image_alias:
                    lines.append(f"{image_alias} -[#2f6b50]-> {alias}")
                    if alias_index == 0:
                        lines.append(f"workload_outbound_dependency ..[#8a6d3b]> {alias}")
                if prebuilt_alias:
                    lines.append(f"{prebuilt_alias} -[#2f6b50]-> {alias}")
                for binding in plan.get("runtimeBindings") or []:
                    if binding.get("kind") != "secretEnvironment" or str(
                        binding.get("workloadRef") or ""
                    ) != workload_id:
                        continue
                    grant_id = (
                        f"secret-access-binding-{workload_id}-"
                        f"{binding.get('configurationRef')}"
                    )
                    identity_ref = next(
                        (
                            str(reference.get("producerRef") or "")
                            for reference in plan.get("references") or []
                            if reference.get("consumerRef") == grant_id
                            and str(reference.get("producerRef") or "")
                            in secret_identity_aliases
                        ),
                        "",
                    )
                    identity_alias = secret_identity_aliases.get(identity_ref)
                    secret_node_id = (
                        f"secret-ref-{workload_id}-{binding.get('configurationRef')}"
                    )
                    secret_alias = f"secret_{_id(secret_node_id)}"
                    if identity_alias:
                        lines.append(
                            f"{secret_alias} --[#8a6d3b] {identity_alias} : read permission"
                        )
                        lines.append(
                            f"{identity_alias} ..[#8a6d3b]> {alias} : inject"
                        )
    for traffic_filter in traffic_filters:
        filter_id = str(traffic_filter.get("id") or "policy")
        provider_kind = str(traffic_filter.get("providerKind") or "")
        if provider == "aws" and provider_kind == "load-balancer-security-group":
            label = "" if current_style else " : allows HTTP port 80"
            lines.append(
                f"traffic_filter_{_id(filter_id)} ..[#8a6d3b]> public_ingress{label}"
            )
            continue
        if provider == "aws" and provider_kind == "application-ingress-rule":
            source_label = "" if current_style else " : allowed source"
            target_label = "" if current_style else " : permits load-balancer source"
            lines.append(
                "traffic_filter_load_balancer_security_group ..[#8a6d3b]> "
                f"traffic_filter_{_id(filter_id)}{source_label}"
            )
            lines.append(
                f"traffic_filter_{_id(filter_id)} ..[#8a6d3b]> "
                f"traffic_filter_security_group{target_label}"
            )
            continue
        if current_style:
            logical_ref = str(traffic_filter.get("logicalRef") or "")
            target_workload_ids = (
                [logical_ref]
                if logical_ref in runtime_aliases
                else [
                    workload_id
                    for workload_id, allocation in allocations.items()
                    if allocation.get("computeRef") == logical_ref
                ]
            )
            targets = [
                alias
                for workload_id in target_workload_ids
                for alias in runtime_aliases.get(workload_id, [])
            ]
        else:
            targets = database_aliases if "postgresql" in filter_id else application_aliases
        for target_alias in targets:
            label = "" if current_style else " : allows required traffic"
            lines.append(
                f"traffic_filter_{_id(filter_id)} ..[#8a6d3b]> {target_alias}{label}"
            )
    if disk and current_style:
        for storage_binding in plan.get("storageBindings") or []:
            storage_ref = str(storage_binding.get("storageRef") or "")
            disk_alias = disk_aliases.get(storage_ref)
            if not disk_alias:
                continue
            owner_workload = str(storage_binding.get("workloadRef") or "")
            for owner_alias in runtime_aliases.get(owner_workload, []):
                lines.append(f"{disk_alias} ..[#6f7c73]> {owner_alias}")
    elif disk:
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
    """Render IaC references without runtime traffic."""
    current_style = bundle.get("schemaVersion") == "easydep-deployment-diagram"
    projection = _primary(bundle)
    if projection is None or projection.get("status") not in {
        "completed",
        "needsInput",
    }:
        return _fallback(bundle, "Provisioning dependencies require one resolved provider target.")
    if current_style:
        context = _render_context(bundle)
        plan = dict(context["plan"])
        render_settings = dict(context["settings"])
    else:
        plan = dict(projection.get("resourcePlan") or {})
        render_settings = dict(projection.get("topology") or {})
        render_settings["displayCaption"] = render_settings.get("familyId")
    provider = str(projection.get("provider") or "")
    region = str(projection.get("region") or "")
    display_caption = str(render_settings.get("displayCaption") or "")
    placement_constraints = dict(plan.get("placementConstraints") or {})
    ingress_zones = list(
        placement_constraints.get("selectedIngressZones")
        or render_settings.get("selectedIngressZones")
        or render_settings.get("selectedZones")
        or []
    )
    projected_nodes = {
        str(node.get("id") or ""): node
        for node in plan.get("nodes") or []
        if node.get("entityClass")
        in {
            "providerResource",
            "providerComponent",
            "externalArtifact",
            "sharedValue",
            "embeddedBlock",
        }
    }
    folded = {
        node_id: node
        for node_id, node in projected_nodes.items()
        if str(node.get("providerKind") or "")
        in _FOLDED_RELATION_KINDS.get(provider, set())
    }
    included = {node_id: node for node_id, node in projected_nodes.items() if node_id not in folded}
    embedded_by_owner: dict[str, list[dict[str, Any]]] = {}
    for node in included.values():
        if node.get("entityClass") == "embeddedBlock":
            embedded_by_owner.setdefault(str(node.get("ownerRef") or ""), []).append(node)
    lines = [
        "@startuml",
        "!theme plain",
        "top to bottom direction",
        "skinparam shadowing false",
        "skinparam linetype polyline",
        "skinparam nodesep 12",
        (
            f"title Provisioning dependencies - {_provider_label(provider)} / {_text(region)}"
            + (f"\\n{_text(display_caption)}" if display_caption else "")
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
        if node.get("entityClass") == "embeddedBlock":
            continue
        handling = str(node.get("handling") or "create")
        stereotype = "reference" if handling == "referenceExisting" else handling
        node_name = _text(node.get("name") or node_id)
        if node_id == "compute-group":
            replica_count = int(render_settings.get("replicaCount") or 1)
            zones = list(render_settings.get("selectedZones") or [])
            placement = ", ".join(zones) if zones else "selected zone"
            node_name = (
                f"{node_name}\\ndesired capacity: {replica_count}\\nplacement: {_text(placement)}"
            )
        if display_name_counts.get(node_name, 0) > 1:
            if current_style:
                role = _text(node.get("displayRole") or node_id)
            else:
                logical_ref = str(node.get("logicalRef") or "")
                role = workload_names.get(logical_ref, "Application")
            node_name = f"{node_name}\\n{role}"
        minimum_count = int(node.get("minimumCount") or 1)
        if minimum_count > 1:
            aliases: list[str] = []
            node_zones = (
                ingress_zones
                if node_id in {"ingress-subnet", "ingress-route-association"}
                else list(render_settings.get("selectedZones") or [])
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
            if node.get("entityClass") == "sharedValue":
                lines.append(
                    f'rectangle "{node_name}" as {alias} <<shared value>>'
                )
            elif embedded_by_owner.get(node_id):
                lines.append(f'node "{node_name}" as {alias} <<{_text(stereotype)}>> {{')
                for block in sorted(
                    embedded_by_owner[node_id], key=lambda item: str(item.get("id") or "")
                ):
                    block_id = str(block.get("id") or "")
                    block_alias = f"provision_{_id(block_id)}"
                    provision_aliases[block_id] = [block_alias]
                    lines.append(
                        f'  rectangle "{_text(block.get("name") or block_id)}" '
                        f'as {block_alias} <<inline block>>'
                    )
                lines.append("}")
            else:
                lines.append(f'node "{node_name}" as {alias} <<{_text(stereotype)}>>')
    visible_edges = [
        edge
        for edge in plan.get("edges") or []
        if str(edge.get("from") or "") in included
        and str(edge.get("to") or "") in included
    ]
    endpoint_counts: dict[tuple[str, str], int] = {}
    for edge in visible_edges:
        endpoints = (str(edge.get("from") or ""), str(edge.get("to") or ""))
        endpoint_counts[endpoints] = endpoint_counts.get(endpoints, 0) + 1
    for edge in visible_edges:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        label = str(edge.get("label") or "depends on")
        if current_style:
            relationship = (
                _compact_reference_role(edge.get("consumerPath"))
                if endpoint_counts[(source, target)] > 1
                else ""
            )
        else:
            if label not in _DISPLAYABLE_PROVISIONING_RELATIONSHIPS:
                continue
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
        dependent_aliases = provision_aliases[source]
        prerequisite_aliases = provision_aliases[target]
        if target == "ingress-subnet" and source == "nat-gateway":
            prerequisite_aliases = prerequisite_aliases[:1]
        pairs: list[tuple[str, str]]
        if (
            label == "binds"
            and len(prerequisite_aliases) == len(dependent_aliases)
            and len(prerequisite_aliases) > 1
        ):
            pairs = list(zip(dependent_aliases, prerequisite_aliases, strict=True))
        else:
            pairs = [
                (dependent_alias, prerequisite_alias)
                for dependent_alias in dependent_aliases
                for prerequisite_alias in prerequisite_aliases
            ]
        for dependent_alias, prerequisite_alias in pairs:
            if current_style:
                suffix = f" : {_text(relationship)}" if relationship else ""
                lines.append(
                    f"{dependent_alias} -[#6f7780,dashed]-> {prerequisite_alias}{suffix}"
                )
            else:
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
        relation_label = _FOLDED_ASSOCIATION_LABELS.get(
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
            (
                "  Arrow: dependent -> prerequisite."
                if current_style
                else "  Arrow: prerequisite -> dependent."
            ),
            "  Arrow labels appear only when duplicate references need disambiguation.",
            "  Undirected line: Terraform association, attachment, permission, or route.",
            "  Shared value: one Terraform local consumed by multiple fields.",
            "  Runtime traffic is intentionally omitted.",
            "endlegend",
            "@enduml",
        ]
    )
    return "\n".join(lines)


def _render_context(bundle: dict[str, Any]) -> dict[str, Any]:
    """Build renderer-only values from WorkloadGraph, DeploymentPlan, and ResourcePlan."""

    projections = list(bundle.get("projections") or [])
    projection = projections[0] if projections else {}
    deployment_plan = dict(projection.get("deploymentPlan") or {})
    resource_plan = copy.deepcopy(projection.get("resourcePlan") or {})
    graph = dict(bundle.get("workloadGraph") or {})
    compute_units = {
        str(item.get("id") or ""): item
        for item in deployment_plan.get("computeUnits") or []
    }
    placements = list(deployment_plan.get("placements") or [])
    public_paths = [
        item
        for item in deployment_plan.get("networkPaths") or []
        if item.get("kind") == "publicIngress"
    ]
    primary_compute_id = str(
        (public_paths[0].get("computeUnitRef") if public_paths else "")
        or (placements[0].get("computeUnitRef") if placements else "")
        or next(iter(compute_units), "")
    )
    primary_compute = compute_units.get(primary_compute_id, {})
    zones = list(primary_compute.get("zones") or [])
    replica_count = int(primary_compute.get("replicaCount") or 1)
    managed = primary_compute.get("kind") == "managedVmGroup"
    workload_by_id = {
        str(item.get("id") or ""): copy.deepcopy(item)
        for item in graph.get("workloads") or []
    }
    placement_by_workload = {
        str(item.get("workloadRef") or ""): str(item.get("computeUnitRef") or "")
        for item in placements
    }
    workload_names_by_compute: dict[str, list[str]] = {}
    for workload_id, compute_id in placement_by_workload.items():
        workload = workload_by_id.get(workload_id, {})
        workload_names_by_compute.setdefault(compute_id, []).append(
            str(workload.get("name") or workload_id)
        )
    ingress = (
        "loadBalanced"
        if any(item.get("ingressKind") == "loadBalancer" for item in public_paths)
        else "directPublicIp"
        if public_paths
        else "privateEgressOnly"
    )
    display_nodes: list[dict[str, Any]] = []
    for node in resource_plan.get("nodes") or []:
        display = copy.deepcopy(node)
        display["providerKind"] = str(
            node.get("providerPrimitiveKind") or node.get("providerKind") or ""
        )
        node_id = str(node.get("id") or "")
        logical_ref = str(node.get("logicalRef") or "")
        attributes = dict(node.get("attributes") or {})
        zone = str(attributes.get("zone") or "")
        if logical_ref in workload_by_id:
            display_role = str(
                workload_by_id[logical_ref].get("name") or logical_ref
            )
        elif logical_ref in compute_units:
            workload_names = workload_names_by_compute.get(logical_ref) or [logical_ref]
            display_role = f"Compute: {' + '.join(sorted(workload_names))}"
            if zone:
                display_role += f" / {zone}"
        elif logical_ref == "public-network":
            display_role = "Public ingress / egress"
            if zone:
                display_role += f" / {zone}"
        else:
            display_role = node_id
        display["displayRole"] = display_role
        if node_id.startswith("traffic-filter-"):
            display["logicalRef"] = node_id.removeprefix("traffic-filter-")
        display_nodes.append(display)
    for value in resource_plan.get("sharedValues") or []:
        display_nodes.append(
            {
                **copy.deepcopy(value),
                "name": (
                    f"{value.get('name') or value.get('id')}\\n"
                    f"{value.get('value')}"
                ),
                "providerKind": "shared-value",
                "entityClass": "sharedValue",
                "handling": "local",
            }
        )
    for block in resource_plan.get("embeddedBlocks") or []:
        block_path = str(block.get("blockPath") or "")
        block_kind = {
            "health_check": "health-check",
            "frontend_ip_configuration": "frontend-ip-config",
            "backend": "backend-group",
            "block_device_mappings": "disk",
            "data_disk": "disk",
            "disk": "disk",
        }.get(block_path, "embedded-block")
        display_nodes.append(
            {
                **copy.deepcopy(block),
                "providerKind": block_kind,
                "entityClass": "embeddedBlock",
                "handling": "inline",
            }
        )
    if public_paths:
        display_nodes.append(
            {
                "id": "public-endpoint",
                "name": "Public HTTP endpoint",
                "group": "endpoint",
                "providerKind": "endpoint",
                "entityClass": "runtimeElement",
                "handling": "runtimeDerived",
            }
        )
    display_edges = []
    for reference in resource_plan.get("references") or []:
        display = copy.deepcopy(reference)
        display["from"] = reference.get("consumerRef")
        display["to"] = reference.get("producerRef")
        display["label"] = (
            f"{reference.get('consumerPath') or 'value'} = "
            f"{reference.get('producerAttribute') or 'id'}"
        )
        display_edges.append(display)
    for connection in graph.get("connections") or []:
        display_edges.append(
            {
                "from": connection.get("sourceRef"),
                "to": connection.get("targetRef"),
                "label": connection.get("protocol") or "connects",
                "evidence": ["design-connection"],
            }
        )
    subnets = [
        node
        for node in display_nodes
        if node.get("providerKind") == "subnet"
        and str(node.get("id") or "").startswith(f"subnet-{primary_compute_id}-")
    ]
    ingress_subnets = [
        node
        for node in display_nodes
        if node.get("providerKind") == "subnet"
        and str(node.get("id") or "").startswith("public-subnet-")
    ]
    display_plan = {
        **resource_plan,
        "nodes": display_nodes,
        "edges": display_edges,
        "workloads": list(workload_by_id.values()),
        "allocations": [
            {
                "workloadRef": item.get("workloadRef"),
                "computeRef": item.get("computeUnitRef"),
            }
            for item in placements
        ],
        "computeNodeId": primary_compute_id,
        "placementConstraints": {
            "minimumSubnets": max(1, len(subnets)),
            "minimumIngressSubnets": max(1, len(ingress_subnets)),
            "selectedIngressZones": [
                str((node.get("attributes") or {}).get("zone") or "")
                for node in ingress_subnets
            ],
        },
    }
    logical_artifacts = [
        {
            "name": f"{workload.get('name') or workload_id} image source",
            "deployed_on": workload_id,
        }
        for workload_id, workload in workload_by_id.items()
        if (workload.get("artifact") or {}).get("kind") == "generatedApplication"
    ]
    return {
        "plan": display_plan,
        "logicalArtifacts": logical_artifacts,
        "settings": {
            "computeManagement": "managedGroup" if managed else "standalone",
            "replicaCount": replica_count,
            "selectedZones": zones,
            "selectedIngressZones": display_plan["placementConstraints"][
                "selectedIngressZones"
            ],
            "zoneLayout": "multiZoneSpread" if len(zones) > 1 else "singleZone",
            "publicIngress": ingress,
        },
    }


def deployment_bundle_runtime_puml(bundle: dict[str, Any]) -> str:
    if bundle.get("schemaVersion") != "easydep-deployment-diagram":
        raise ValueError("unsupported deployment diagram schema")
    return render_runtime_deployment(bundle)


def deployment_bundle_provisioning_puml(bundle: dict[str, Any]) -> str:
    if bundle.get("schemaVersion") != "easydep-deployment-diagram":
        raise ValueError("unsupported deployment diagram schema")
    return render_provisioning_dependencies(bundle)
