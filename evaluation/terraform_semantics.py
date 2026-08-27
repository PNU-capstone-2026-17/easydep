"""Terraform HCL에서 CSP별 VM 배포 의미를 관측한다.

python-hcl2 owns HCL parsing. This module only maps the provider constructs that are
inside the experiment's documented Docker-on-VM scope.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import hcl2
from lark.exceptions import LarkError

from app.cloudkb.depkb.provider_realizations import capability_realizations
from evaluation.component_projection import analyze_component_projections

TYPE_TO_CONCEPT = {
    # AWS
    "aws_vpc": "network",
    "aws_subnet": "subnet",
    "aws_security_group": "firewall",
    "aws_vpc_security_group_ingress_rule": "firewallRule",
    "aws_instance": "vm",
    "aws_autoscaling_group": "vmGroup",
    "aws_launch_template": "vmTemplate",
    "aws_ebs_volume": "dataDisk",
    "aws_volume_attachment": "diskAttachment",
    "aws_lb": "loadBalancer",
    "aws_alb": "loadBalancer",
    "aws_lb_target_group": "backendPool",
    "aws_lb_target_group_attachment": "backendAttachment",
    "aws_lb_listener": "listener",
    "aws_eip": "publicIp",
    "aws_internet_gateway": "internetGateway",
    "aws_nat_gateway": "egressNat",
    "aws_route_table": "routeTable",
    "aws_route": "route",
    "aws_route_table_association": "routeAssociation",
    "aws_iam_instance_profile": "workloadIdentity",
    "aws_iam_role": "identityRole",
    "aws_iam_role_policy_attachment": "identityBinding",
    "aws_network_interface": "nic",
    "aws_key_pair": "accessMaterial",
    "aws_acm_certificate": "certificate",
    # Azure
    "azurerm_virtual_network": "network",
    "azurerm_subnet": "subnet",
    "azurerm_network_security_group": "firewall",
    "azurerm_network_security_rule": "firewallRule",
    "azurerm_linux_virtual_machine": "vm",
    "azurerm_linux_virtual_machine_scale_set": "vmGroup",
    "azurerm_managed_disk": "dataDisk",
    "azurerm_virtual_machine_data_disk_attachment": "diskAttachment",
    "azurerm_lb": "loadBalancer",
    "azurerm_lb_backend_address_pool": "backendPool",
    "azurerm_network_interface_backend_address_pool_association": "backendAttachment",
    "azurerm_lb_rule": "listener",
    "azurerm_lb_probe": "healthCheck",
    "azurerm_application_gateway": "loadBalancer",
    "azurerm_network_interface_application_gateway_backend_address_pool_association": "backendAttachment",
    "azurerm_public_ip": "publicIp",
    "azurerm_network_interface": "nic",
    "azurerm_nat_gateway": "egressNat",
    "azurerm_nat_gateway_public_ip_association": "publicIpNatAssociation",
    "azurerm_subnet_nat_gateway_association": "subnetNatAssociation",
    "azurerm_subnet_network_security_group_association": "securityAssociation",
    "azurerm_network_interface_security_group_association": "securityAssociation",
    "azurerm_user_assigned_identity": "workloadIdentity",
    "azurerm_resource_group": "resourceScope",
    "azurerm_role_assignment": "identityBinding",
    "azurerm_route_table": "routeTable",
    "azurerm_route": "route",
    # GCP
    "google_compute_network": "network",
    "google_compute_subnetwork": "subnet",
    "google_compute_firewall": "firewall",
    "google_compute_instance": "vm",
    "google_compute_instance_template": "vmTemplate",
    "google_compute_region_instance_group_manager": "vmGroup",
    "google_compute_instance_group_manager": "vmGroup",
    "google_compute_disk": "dataDisk",
    "google_compute_attached_disk": "diskAttachment",
    "google_compute_forwarding_rule": "loadBalancer",
    "google_compute_global_forwarding_rule": "loadBalancer",
    "google_compute_backend_service": "backendPool",
    "google_compute_region_backend_service": "backendPool",
    "google_compute_instance_group": "backendAttachment",
    "google_compute_network_endpoint_group": "backendAttachment",
    "google_compute_region_network_endpoint_group": "backendAttachment",
    "google_compute_health_check": "healthCheck",
    "google_compute_region_health_check": "healthCheck",
    "google_compute_address": "publicIp",
    "google_compute_global_address": "publicIp",
    "google_compute_target_https_proxy": "listener",
    "google_compute_target_http_proxy": "listener",
    "google_compute_url_map": "listenerRule",
    "google_compute_ssl_certificate": "certificate",
    "google_compute_managed_ssl_certificate": "certificate",
    "google_compute_router": "router",
    "google_compute_router_nat": "egressNat",
    "google_service_account": "workloadIdentity",
    "google_project_iam_member": "identityBinding",
    "google_service_account_iam_member": "identityBinding",
}

RELATION_BY_TYPE = {
    "aws_route": "routesTo",
    "aws_route_table_association": "associates",
    "aws_volume_attachment": "attaches",
    "aws_lb_target_group_attachment": "attaches",
    "azurerm_nat_gateway_public_ip_association": "associates",
    "azurerm_subnet_nat_gateway_association": "associates",
    "azurerm_subnet_network_security_group_association": "associates",
    "azurerm_network_interface_security_group_association": "associates",
    "azurerm_virtual_machine_data_disk_attachment": "attaches",
    "azurerm_network_interface_backend_address_pool_association": "attaches",
    "azurerm_network_interface_application_gateway_backend_address_pool_association": "attaches",
    "google_compute_attached_disk": "attaches",
    "google_compute_router_nat": "configures",
}

REFERENCE = re.compile(
    r"(?:\$\{)?(?P<data>data\.)?(?P<type>(?:aws|azurerm|google)_[A-Za-z0-9_]+)\."
    r"(?P<name>[A-Za-z0-9_-]+)"
)
SOURCE_SUFFIXES = (
    ".java",
    ".properties",
    ".yml",
    ".yaml",
    ".sh",
    ".ps1",
    ".tf",
    ".tftpl",
    ".tpl",
    "Dockerfile",
)


def _unquote(value: object) -> str:
    return str(value).strip('"')


def _resources(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    resources: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(root.rglob("*.tf")):
        if any(part in {".terraform", ".git", "build"} for part in path.parts):
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                parsed = hcl2.load(stream)
        except (OSError, ValueError, LarkError) as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        for declaration_kind in ("resource", "data"):
            for declaration in parsed.get(declaration_kind, []):
                for raw_type, instances in declaration.items():
                    resource_type = _unquote(raw_type)
                    for raw_name, attributes in instances.items():
                        name = _unquote(raw_name)
                        prefix = "data." if declaration_kind == "data" else ""
                        resources.append(
                            {
                                "address": f"{prefix}{resource_type}.{name}",
                                "providerType": resource_type,
                                "declarationKind": declaration_kind,
                                "name": name,
                                "concept": TYPE_TO_CONCEPT.get(resource_type, "unmapped"),
                                "attributes": attributes,
                                "source": path.relative_to(root).as_posix(),
                            }
                        )
    return resources, errors


def _references(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_references(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_references(item) for item in value), set())
    if not isinstance(value, str):
        return set()
    return {
        f"{'data.' if match.group('data') else ''}{match.group('type')}.{match.group('name')}"
        for match in REFERENCE.finditer(value)
    }


def _blocks(attributes: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = attributes.get(name, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _resource_count(resource: dict[str, Any]) -> int | None:
    attributes = resource["attributes"]
    direct = _integer(attributes.get("count"))
    if direct is not None:
        return direct
    for name in ("desired_capacity", "instances", "target_size"):
        value = _integer(attributes.get(name))
        if value is not None:
            return value
    return 1 if resource["concept"] == "vm" else None


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip('"').lower() == "true"


def _vm_has_public_ip(resources: list[dict[str, Any]]) -> bool:
    public_addresses = {item["address"] for item in resources if item["concept"] == "publicIp"}
    for item in resources:
        attributes = item["attributes"]
        if item["providerType"] == "aws_instance" and _truthy(
            attributes.get("associate_public_ip_address")
        ):
            return True
        if item["providerType"] == "google_compute_instance":
            if any(
                _blocks(block, "access_config")
                for block in _blocks(attributes, "network_interface")
            ):
                return True
        if item["providerType"] == "azurerm_network_interface":
            for block in _blocks(attributes, "ip_configuration"):
                if _references(block) & public_addresses:
                    return True
    return False


def _internet_ingress(resources: list[dict[str, Any]]) -> bool:
    for item in resources:
        attributes = item["attributes"]
        candidate_blocks: list[dict[str, Any]] = []
        if item["providerType"] == "aws_security_group":
            candidate_blocks = _blocks(attributes, "ingress")
        elif item["providerType"] == "azurerm_network_security_rule":
            direction = str(attributes.get("direction", "")).strip('"').lower()
            access = str(attributes.get("access", "")).strip('"').lower()
            if direction == "inbound" and access == "allow":
                candidate_blocks = [attributes]
        elif item["providerType"] == "google_compute_firewall":
            direction = str(attributes.get("direction", '"INGRESS"')).strip('"').upper()
            if direction == "INGRESS":
                candidate_blocks = [attributes]
        for block in candidate_blocks:
            text = json.dumps(block, ensure_ascii=False).lower()
            if "0.0.0.0/0" in text or '"internet"' in text or "::/0" in text:
                return True
    return False


def _port_443(value: Any) -> bool:
    if isinstance(value, dict):
        start = _integer(value.get("from_port"))
        end = _integer(value.get("to_port"))
        if start is not None and end is not None and start <= 443 <= end:
            return True
        for key in ("destination_port_range", "port", "ports"):
            candidate = value.get(key)
            if candidate == 443 or str(candidate).strip('"') == "443":
                return True
        return any(_port_443(item) for item in value.values())
    if isinstance(value, list):
        return any(_port_443(item) for item in value)
    if isinstance(value, (str, int)):
        return str(value).strip('"') == "443"
    return False


def _deployment_text(root: Path) -> str:
    chunks: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in {".git", "build", ".gradle"} for part in path.parts):
            continue
        if path.name == "Dockerfile" or path.suffix.lower() in SOURCE_SUFFIXES:
            try:
                chunks.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
    return "\n".join(chunks)


def _public_https(resources: list[dict[str, Any]], deployment_text: str) -> bool:
    runtime_tls = bool(
        re.search(
            r"(?is)(?:caddy|nginx|traefik|certbot|certificate|\btls\b|\bhttps\b)"
            r".*?(?<!\d)443(?!\d)|(?<!\d)443(?!\d).*?"
            r"(?:caddy|nginx|traefik|certbot|certificate|\btls\b|\bhttps\b)",
            deployment_text,
        )
    )
    if runtime_tls:
        return True
    for item in resources:
        if item["concept"] not in {"listener", "loadBalancer"}:
            continue
        if not _port_443(item["attributes"]):
            continue
        attributes = json.dumps(item["attributes"], ensure_ascii=False).lower()
        if re.search(r'"(?:protocol|load_balancing_scheme)"[^\n]*(?:https|ssl)', attributes):
            return True
    return False


def _semantic_edges(resources: list[dict[str, Any]]) -> tuple[list[dict[str, str]], set[str]]:
    by_address = {item["address"]: item for item in resources}
    edges: set[tuple[str, str]] = set()
    concepts = {item["concept"] for item in resources if item["concept"] != "unmapped"}
    for item in resources:
        for target_address in _references(item["attributes"]):
            target = by_address.get(target_address)
            if target and item["concept"] != "unmapped" and target["concept"] != "unmapped":
                edges.add((item["concept"], target["concept"]))
        attributes = item["attributes"]
        if item["providerType"] == "google_compute_instance":
            concepts.update({"nic", "disk"})
            edges.update({("vm", "nic"), ("vm", "disk")})
            for block in _blocks(attributes, "network_interface"):
                refs = _references(block)
                for address in refs:
                    target = by_address.get(address)
                    if target and target["concept"] in {"subnet", "network"}:
                        edges.add(("nic", target["concept"]))
        elif item["providerType"] == "aws_instance" and attributes.get("subnet_id") is not None:
            concepts.add("nic")
            edges.add(("vm", "nic"))
            if _references(attributes.get("subnet_id")):
                edges.add(("nic", "subnet"))
    return [{"from": source, "to": target} for source, target in sorted(edges)], concepts


def _resource_graph(resources: list[dict[str, Any]]) -> dict[str, Any]:
    """주소와 방향을 보존한 Terraform 선언 그래프를 만든다."""
    by_address = {item["address"]: item for item in resources}
    edges: set[tuple[str, str, str]] = set()
    for item in resources:
        relation = RELATION_BY_TYPE.get(item["providerType"], "references")
        for target_address in _references(item["attributes"]):
            if target_address in by_address and target_address != item["address"]:
                edges.add((item["address"], target_address, relation))
    return {
        "nodes": [
            {
                "id": item["address"],
                "providerType": item["providerType"],
                "declarationKind": item["declarationKind"],
                "concept": item["concept"],
                "source": item["source"],
            }
            for item in resources
        ],
        "edges": [
            {
                "from": source,
                "to": target,
                "fromConcept": by_address[source]["concept"],
                "toConcept": by_address[target]["concept"],
                "relation": relation,
            }
            for source, target, relation in sorted(edges)
        ],
    }


def analyze_terraform_semantics(
    root: Path, *, expected_mount_path: str | None = None
) -> dict[str, Any]:
    resources, errors = _resources(root)
    edges, concepts = _semantic_edges(resources)
    resource_graph = _resource_graph(resources)
    resource_graph["attributes"] = {
        "vmPublicIp": _vm_has_public_ip(resources),
        "internetIngressRule": _internet_ingress(resources),
    }
    text = _deployment_text(root)
    component_projections = analyze_component_projections(
        resources, text, expected_mount_path=expected_mount_path
    )
    vm_counts = [
        _resource_count(item) for item in resources if item["concept"] in {"vm", "vmGroup"}
    ]
    known_vm_counts = [value for value in vm_counts if value is not None]
    zones: set[str] = set()
    for item in resources:
        for key in ("availability_zone", "zone", "zones"):
            value = item["attributes"].get(key)
            values = value if isinstance(value, list) else [value]
            zones.update(
                _unquote(candidate)
                for candidate in values
                if isinstance(candidate, str) and "${" not in candidate
            )
    disk_sizes: list[int] = []
    attached_addresses: set[str] = set()
    for item in resources:
        if item["concept"] == "diskAttachment":
            attached_addresses.update(_references(item["attributes"]))
        if item["providerType"] == "google_compute_instance":
            for block in _blocks(item["attributes"], "attached_disk"):
                attached_addresses.update(_references(block))
    for item in resources:
        if item["concept"] != "dataDisk" or item["address"] not in attached_addresses:
            continue
        for key in ("size", "disk_size_gb"):
            size = _integer(item["attributes"].get(key))
            if size is not None:
                disk_sizes.append(size)
    capabilities = {
        "vmCount": sum(known_vm_counts)
        if vm_counts and len(known_vm_counts) == len(vm_counts)
        else None,
        "availabilityZones": len(zones) if zones else None,
        "loadBalancer": "loadBalancer" in concepts,
        "persistentData": bool(attached_addresses),
        "dataDiskGiB": max(disk_sizes) if disk_sizes else None,
        "volumeMount": (
            expected_mount_path
            if expected_mount_path in (component_projections.get("guestMountPaths") or [])
            else next(iter(component_projections.get("guestMountPaths") or []), None)
        ),
        "publicHttps": _public_https(resources, text),
        "healthPath": "/health" if "/health" in text else None,
        "applicationPort": 8080 if re.search(r"(?<!\d)8080(?!\d)", text) else None,
    }
    return {
        "status": "failed" if errors or not resources else "available",
        "parser": "python-hcl2",
        "resources": [
            {
                key: item[key]
                for key in ("address", "providerType", "declarationKind", "concept", "source")
            }
            | {
                "blocks": sorted(
                    key
                    for key, value in item["attributes"].items()
                    if isinstance(value, list) and any(isinstance(entry, dict) for entry in value)
                )
            }
            for item in resources
        ],
        "concepts": sorted(concepts),
        "edges": edges,
        "resourceGraph": resource_graph,
        "componentProjections": component_projections,
        "capabilities": capabilities,
        "unmappedProviderTypes": sorted(
            {item["providerType"] for item in resources if item["concept"] == "unmapped"}
        ),
        "errors": errors,
    }


def _projection_checks(actual: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    resources = actual.get("resources") or []
    provider_types = {item.get("providerType") for item in resources}
    blocks = {
        (item.get("providerType"), block)
        for item in resources
        for block in item.get("blocks") or []
    }
    checks = []
    for realization in capability_realizations(provider, "load-balanced-ingress"):
        for component in realization["components"]:
            evidence = component["terraformEvidence"]
            accepted = evidence.get("resourceTypes") or []
            present = bool(provider_types & set(accepted))
            if evidence.get("ownerType") and evidence.get("ownerBlock"):
                present = (evidence["ownerType"], evidence["ownerBlock"]) in blocks
            checks.append(
                {
                    "kind": "providerProjection",
                    "realizationId": realization["id"],
                    "componentId": component["id"],
                    "nativePath": component["nativePath"],
                    "representation": component["representation"],
                    "coverage": component.get("coverage", "required"),
                    "status": "passed" if present else "failed",
                }
            )
    return checks


def score_semantics(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    expected_provider = expected.get("provider")
    actual_provider = (actual.get("componentProjections") or {}).get("provider")
    if expected_provider:
        checks.append(
            {
                "kind": "providerBoundary",
                "expected": expected_provider,
                "actual": actual_provider,
                "status": (
                    "passed"
                    if actual_provider == expected_provider
                    else "unknown"
                    if actual_provider is None
                    else "failed"
                ),
            }
        )
    actual_capabilities = actual.get("capabilities", {})
    for name, requirement in expected.get("requiredCapabilities", {}).items():
        observed = actual_capabilities.get(name)
        status = "unknown"
        if isinstance(requirement, bool):
            status = (
                "passed"
                if observed is requirement
                else ("unknown" if observed is None else "failed")
            )
        elif isinstance(requirement, (int, float, str)):
            status = (
                "passed"
                if observed == requirement
                else ("unknown" if observed is None else "failed")
            )
        elif isinstance(requirement, dict) and name != "load":
            if observed is not None:
                minimum = requirement.get("min")
                maximum = requirement.get("max")
                status = (
                    "passed"
                    if (
                        (minimum is None or observed >= minimum)
                        and (maximum is None or observed <= maximum)
                    )
                    else "failed"
                )
        checks.append(
            {
                "kind": "capability",
                "name": name,
                "expected": requirement,
                "actual": observed,
                "status": status,
            }
        )
    concepts = set(actual.get("concepts", []))
    forbidden_map = {
        "dataDisk": "dataDisk",
        "loadBalancer": "loadBalancer",
        "certificate": "certificate",
    }
    for name in expected.get("forbiddenConcepts", []):
        concept = forbidden_map.get(name)
        if concept:
            checks.append(
                {
                    "kind": "forbidden",
                    "name": name,
                    "status": "failed" if concept in concepts else "passed",
                }
            )
    actual_edges = {(item["from"], item["to"]) for item in actual.get("edges", [])}
    for dependency in expected.get("requiredDependencies", []):
        source = dependency["from"]
        targets = str(dependency["to"]).split("|")
        # A conditional path is checked only when both endpoints are present.  Looking
        # at the target alone activated subnet->network even when no subnet existed.
        # Default-network deployments must not be penalized for an undeclared custom
        # path, while a declared-but-unconnected path must still fail.
        active = not dependency.get("condition") or (
            source in concepts and any(target in concepts for target in targets)
        )
        status = (
            "not-applicable"
            if not active
            else (
                "passed"
                if any((source, target) in actual_edges for target in targets)
                else "failed"
            )
        )
        checks.append(
            {"kind": "dependency", "from": source, "to": dependency["to"], "status": status}
        )
    if expected.get("requiredCapabilities", {}).get("loadBalancer") is True and expected.get(
        "legacyProviderProjection", True
    ):
        checks.extend(_projection_checks(actual, str(expected.get("provider") or "")))
    component_deltas = expected.get("componentDeltas") or (
        [expected["componentDelta"]] if expected.get("componentDelta") else []
    )
    dependency_expectations = expected.get("componentDependencyExpectations") or {}
    observed_relations = {
        (delta_id, item.get("from"), item.get("to")): bool(item.get("observedPairs"))
        for delta_id, delta in (actual.get("componentProjections", {}).get("deltas", {})).items()
        for item in delta.get("relations") or []
    }
    for item in dependency_expectations.get("structuralReferences") or []:
        key = (item["delta"], item["from"], item["to"])
        checks.append(
            {
                "kind": "componentDependencyReference",
                **item,
                "status": "passed" if observed_relations.get(key) else "failed",
            }
        )
    for gate, kind in (
        ("cardinalities", "componentCardinality"),
        ("constraints", "componentConstraintRequirement"),
    ):
        checks.extend(
            {"kind": kind, **item, "status": "not-measured"}
            for item in dependency_expectations.get(gate) or []
        )
    for component_delta in component_deltas:
        delta = actual.get("componentProjections", {}).get("deltas", {}).get(component_delta)
        if delta is None:
            checks.append(
                {
                    "kind": "componentProjection",
                    "delta": component_delta,
                    "status": "unknown",
                    "reason": "projection-unavailable",
                }
            )
        else:
            checks.extend(
                {
                    "kind": "componentProjection",
                    "delta": component_delta,
                    **item,
                }
                for item in delta["components"]
            )
            checks.extend(
                {
                    "kind": "componentRelation",
                    "delta": component_delta,
                    **item,
                }
                for item in delta["relations"]
            )
            checks.extend(
                {
                    "kind": "componentConstraint",
                    "delta": component_delta,
                    **item,
                }
                for item in delta["constraints"]
            )
    scored = [item for item in checks if item["status"] in {"passed", "failed", "unknown"}]
    passed = sum(item["status"] == "passed" for item in scored)
    failed = sum(item["status"] == "failed" for item in scored)
    unknown = sum(item["status"] == "unknown" for item in scored)
    return {
        "status": "completed",
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "notApplicable": sum(item["status"] == "not-applicable" for item in checks),
        "notMeasured": sum(item["status"] == "not-measured" for item in checks),
        "observedUnverified": sum(
            item["status"] == "observed-unverified" for item in checks
        ),
        "passRate": round(passed / len(scored), 6) if scored else None,
        "checks": checks,
    }
