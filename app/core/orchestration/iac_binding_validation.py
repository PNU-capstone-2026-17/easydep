"""Provider-neutral observations of deployment bindings in generated IaC."""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import hcl2

from app.core.orchestration.app_cloud_contracts import ConsistencyDiagnostic


@dataclass(frozen=True)
class Observation:
    kind: str
    value: int | str | None
    source: str
    confidence: str


_STRONG_PORT_KEYS = {"backend_port", "target_port", "container_port"}
_PORT_CONTEXT = {
    "backend",
    "backend_http_settings",
    "target_group",
    "health_check",
    "probe",
}

_VM_SIZE_FIELDS = {
    "aws": (
        ("aws_instance", "instance_type"),
        ("aws_launch_template", "instance_type"),
    ),
    "azure": (
        ("azurerm_linux_virtual_machine", "size"),
        ("azurerm_linux_virtual_machine_scale_set", "sku"),
    ),
    "gcp": (
        ("google_compute_instance", "machine_type"),
        ("google_compute_instance_template", "machine_type"),
    ),
}

_MANAGED_GROUP_RESOURCES = {
    "aws": {"aws_autoscaling_group", "aws_launch_template"},
    "azure": {"azurerm_linux_virtual_machine_scale_set"},
    "gcp": {"google_compute_instance_template"},
}

_DISK_ATTACHMENT_TYPES = {
    "aws": {"aws_volume_attachment"},
    "azure": {"azurerm_virtual_machine_data_disk_attachment"},
    "gcp": {"google_compute_attached_disk"},
}

_EMBEDDED_REQUIRED_KEYS = {
    "aws": {
        "health-check": {"health_check"},
    },
    "azure": {
        "network-interface": {"network_interface"},
        "frontend-ip-config": {"frontend_ip_configuration"},
        "backend-membership": {"load_balancer_backend_address_pool_ids"},
    },
    "gcp": {
        "network-interface": {"network_interface"},
    },
}


def _walk(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, (*path, str(key).lower()))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, (*path, str(index)))
    else:
        yield path, value


def _literal_port(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 1 <= value <= 65535:
        return value
    if isinstance(value, str) and re.fullmatch(r'"?\d{1,5}"?', value.strip()):
        parsed = int(value.strip('"'))
        return parsed if 1 <= parsed <= 65535 else None
    return None


def _hcl_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return stripped


def _without_comments(content: str) -> str:
    without_blocks = re.sub(r"(?s)/\*.*?\*/", "", content)
    return "\n".join(
        line for line in without_blocks.splitlines() if not re.match(r"^\s*(?:#|//)", line)
    )


def validate_vm_selection_binding(
    files: dict[str, str], *, provider: str, expected_spec_name: str | None
) -> dict[str, Any]:
    """추천 VM 규격이 CSP VM 리소스 또는 변수 기본값에 반영됐는지 관측한다."""
    if not expected_spec_name:
        return {
            "status": "not-applicable",
            "expected": None,
            "observations": [],
            "diagnostics": [],
        }
    target = _VM_SIZE_FIELDS.get(provider)
    if target is None:
        return {
            "status": "failed",
            "expected": expected_spec_name,
            "observations": [],
            "diagnostics": [
                {
                    "code": "BIND-VM-SIZE-001",
                    "message": f"No VM size observer is defined for provider {provider!r}.",
                }
            ],
        }
    targets = set(target)
    variables: dict[str, str] = {}
    parsed_files: list[tuple[str, dict[str, Any]]] = []
    for name, content in sorted(files.items()):
        if not name.endswith(".tf"):
            continue
        try:
            parsed = hcl2.load(io.StringIO(content))
        except Exception:
            continue
        parsed_files.append((name, parsed))
        for block in parsed.get("variable") or []:
            for raw_name, body in block.items():
                default = _hcl_string((body or {}).get("default"))
                if default is not None:
                    variables[raw_name.strip('"')] = default

    observations: list[dict[str, Any]] = []
    for name, parsed in parsed_files:
        for block in parsed.get("resource") or []:
            for raw_type, instances in block.items():
                resource_type = raw_type.strip('"')
                matching = [
                    attribute
                    for expected_type, attribute in targets
                    if expected_type == resource_type
                ]
                if not matching:
                    continue
                attribute = matching[0]
                for raw_name, body in (instances or {}).items():
                    raw_value = _hcl_string((body or {}).get(attribute))
                    value = raw_value
                    variable = None
                    match = re.fullmatch(r"\$\{var\.([A-Za-z0-9_-]+)\}", raw_value or "")
                    if match:
                        variable = match.group(1)
                        value = variables.get(variable)
                    observations.append(
                        {
                            "source": f"{name}:{resource_type}.{raw_name.strip(chr(34))}.{attribute}",
                            "raw": raw_value,
                            "variable": variable,
                            "value": value,
                        }
                    )
    matched = any(item.get("value") == expected_spec_name for item in observations)
    diagnostics = (
        []
        if matched
        else [
            {
                "code": "BIND-VM-SIZE-001",
                "message": "Generated IaC does not bind the selected VM specification.",
                "details": {
                    "expected": expected_spec_name,
                    "observed": [item.get("value") for item in observations],
                },
            }
        ]
    )
    return {
        "status": "passed" if matched else "failed",
        "expected": expected_spec_name,
        "observations": observations,
        "diagnostics": diagnostics,
    }


def validate_managed_group_binding(
    files: dict[str, str], *, provider: str, required: bool
) -> dict[str, Any]:
    """Verify only that the selected provider-native managed compute group is present."""
    if not required:
        return {"status": "not-applicable", "observations": [], "diagnostics": []}
    expected = _MANAGED_GROUP_RESOURCES.get(provider)
    if expected is None:
        return {
            "status": "failed",
            "observations": [],
            "diagnostics": [
                {
                    "code": "BIND-GROUP-001",
                    "message": f"No managed compute-group observer is defined for {provider!r}.",
                }
            ],
        }

    observed_types: set[str] = set()
    for name, content in sorted(files.items()):
        if not name.endswith(".tf"):
            continue
        try:
            parsed = hcl2.load(io.StringIO(content))
        except Exception:
            continue
        for block in parsed.get("resource") or []:
            for raw_type, instances in block.items():
                resource_type = raw_type.strip('"')
                observed_types.add(resource_type)

    missing_resources = sorted(expected - observed_types)
    if provider == "gcp" and not (
        {
            "google_compute_instance_group_manager",
            "google_compute_region_instance_group_manager",
        }
        & observed_types
    ):
        missing_resources.append(
            "google_compute_instance_group_manager|google_compute_region_instance_group_manager"
        )
    diagnostics: list[dict[str, Any]] = []
    if missing_resources:
        diagnostics.append(
            {
                "code": "BIND-GROUP-001",
                "message": "Generated IaC does not use the required provider-managed compute group.",
                "details": {"missingResourceTypes": missing_resources},
            }
        )
    return {
        "status": "failed" if diagnostics else "passed",
        "observations": sorted(observed_types),
        "diagnostics": diagnostics,
    }


def _terraform_resource_observations(
    files: dict[str, str],
) -> tuple[Counter[str], set[str], set[str]]:
    counts: Counter[str] = Counter()
    dynamic_count_types: set[str] = set()
    observed_keys: set[str] = set()
    for name, content in sorted(files.items()):
        if not name.endswith(".tf"):
            continue
        try:
            parsed = hcl2.load(io.StringIO(content))
        except Exception:
            continue
        for block in parsed.get("resource") or []:
            for raw_type, instances in block.items():
                resource_type = raw_type.strip('"')
                for _raw_name, body in (instances or {}).items():
                    body = body or {}
                    count = body.get("count", 1)
                    if isinstance(count, int) and count >= 0:
                        counts[resource_type] += count
                    else:
                        counts[resource_type] += 1
                        dynamic_count_types.add(resource_type)
                    if "for_each" in body:
                        dynamic_count_types.add(resource_type)
                    for path, _value in _walk(body):
                        observed_keys.update(path)
    return counts, dynamic_count_types, observed_keys


def _terraform_resource_bodies(files: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    bodies: dict[str, list[dict[str, Any]]] = {}
    for name, content in sorted(files.items()):
        if not name.endswith(".tf"):
            continue
        try:
            parsed = hcl2.load(io.StringIO(content))
        except Exception:
            continue
        for block in parsed.get("resource") or []:
            for raw_type, instances in block.items():
                resource_type = raw_type.strip('"')
                for body in (instances or {}).values():
                    bodies.setdefault(resource_type, []).append(body or {})
    return bodies


def _body_text(body: dict[str, Any]) -> str:
    return "\n".join(str(value) for _path, value in _walk(body)).lower()


def _body_values(body: dict[str, Any], key: str) -> list[Any]:
    return [value for path, value in _walk(body) if path and path[-1] == key]


def _terraform_reference_pairs(files: dict[str, str]) -> set[tuple[str, str]]:
    """Return source-type -> referenced-resource-type pairs from parsed HCL."""
    resources: list[tuple[str, str, Any]] = []
    for name, content in sorted(files.items()):
        if not name.endswith(".tf"):
            continue
        try:
            parsed = hcl2.load(io.StringIO(content))
        except Exception:
            continue
        for block in parsed.get("resource") or []:
            for raw_type, instances in block.items():
                resource_type = raw_type.strip('"')
                for raw_name, body in (instances or {}).items():
                    resources.append((resource_type, str(raw_name).strip('"'), body or {}))
    addresses = {
        f"{resource_type}.{resource_name}": resource_type
        for resource_type, resource_name, _body in resources
    }
    pairs: set[tuple[str, str]] = set()
    for source_type, _source_name, body in resources:
        values = "\n".join(str(value) for _path, value in _walk(body))
        for address, target_type in addresses.items():
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(address)}(?:\.|\[)", values):
                pairs.add((source_type, target_type))
    return pairs


_GCP_REQUIRED_REFERENCE_IDS = {
    ("forwarding-rule", "public-ip"),
    ("forwarding-rule", "backend-service"),
    ("backend-service", "health-check"),
    ("backend-service", "backend-group"),
    ("backend-service", "compute-group"),
    ("backend-group", "compute-instance"),
    ("compute-group", "compute-template"),
    ("firewall", "network"),
    ("subnet", "network"),
    ("cloud-router", "network"),
    ("cloud-nat", "cloud-router"),
    ("cloud-nat", "subnet"),
}

_PROVISIONING_REFERENCE_LABELS = {
    "belongs to",
    "binds",
    "checks with",
    "contains instance",
    "contains role",
    "creates instances from",
    "depends on",
    "exposes",
    "forwards to",
    "grants pull access to",
    "grants secret read to",
    "is attached to",
    "is deployed in",
    "is placed in",
    "joins",
    "joins through",
    "places instances in",
    "pulls image digest from",
    "registers instance",
    "registers instances with",
    "registers with",
    "routes to",
    "scopes pull access to",
    "uses",
    "uses address",
    "uses backend",
    "uses identity",
    "uses policy",
    "uses secret identity",
}


def validate_resource_plan_binding(
    files: dict[str, str], *, resource_plan: dict[str, Any]
) -> dict[str, Any]:
    """Check provider resources declared by the shared plan without guessing runtime success."""
    if not resource_plan:
        return {
            "status": "not-applicable",
            "observations": [],
            "notObserved": [],
            "diagnostics": [],
        }
    counts, dynamic_count_types, observed_keys = _terraform_resource_observations(files)
    resource_bodies = _terraform_resource_bodies(files)
    reference_pairs = _terraform_reference_pairs(files)
    requirements: Counter[tuple[str, ...]] = Counter()
    plan_ids: dict[tuple[str, ...], list[str]] = {}
    not_observed: list[dict[str, Any]] = []
    for node in resource_plan.get("nodes") or []:
        if node.get("handling") != "create":
            continue
        alternatives = tuple(sorted(str(item) for item in node.get("terraformTypes") or []))
        if not alternatives:
            not_observed.append(
                {
                    "planId": node.get("id"),
                    "reason": "No source-level Terraform observer is defined.",
                    "nextGate": "terraform-plan-json-or-cloud-runtime",
                }
            )
            continue
        minimum_count = max(1, int(node.get("minimumCount") or 1))
        requirements[alternatives] += minimum_count
        plan_ids.setdefault(alternatives, []).extend([str(node.get("id") or "")] * minimum_count)

    diagnostics: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    provider = str(resource_plan.get("provider") or "")
    for node in resource_plan.get("nodes") or []:
        if node.get("handling") != "configureInsideOwner":
            continue
        provider_kind = str(node.get("providerKind") or "")
        required_keys = _EMBEDDED_REQUIRED_KEYS.get(provider, {}).get(provider_kind, set())
        if not required_keys:
            continue
        embedded_observed = bool(required_keys & observed_keys)
        observations.append(
            {
                "planId": node.get("id"),
                "ownerRef": node.get("ownerRef"),
                "embeddedKeys": sorted(required_keys),
                "observed": embedded_observed,
            }
        )
        if not embedded_observed:
            diagnostics.append(
                {
                    "code": "BIND-RESOURCE-PLAN-EMBEDDED-001",
                    "message": (
                        "Generated IaC omits a ResourcePlan component that must be "
                        "configured inside its owner resource."
                    ),
                    "details": observations[-1],
                }
            )
    endpoint_protocols = {
        str(node.get("protocol") or "").strip().lower()
        for node in resource_plan.get("nodes") or []
        if node.get("group") == "endpoint" and node.get("protocol")
    }
    tls_patterns = (
        re.compile(r"(?i)\blisten\s+443\s+ssl\b"),
        re.compile(r"(?i)\bssl_certificate(?:_key)?\b"),
        re.compile(r"(?i)\btls_(?:cert|key)\b"),
        re.compile(r"(?i)\bprotocol\s*=\s*[\"']HTTPS[\"']"),
    )
    tls_locations = sorted(
        name
        for name, content in files.items()
        if any(pattern.search(_without_comments(content)) for pattern in tls_patterns)
    )
    if endpoint_protocols:
        observations.append(
            {
                "kind": "externalEndpointProtocol",
                "expected": sorted(endpoint_protocols),
                "tlsTerminationObserved": bool(tls_locations),
                "locations": tls_locations,
            }
        )
    if endpoint_protocols == {"http"} and tls_locations:
        diagnostics.append(
            {
                "code": "BIND-RESOURCE-PLAN-ENDPOINT-001",
                "message": "Generated IaC upgrades an HTTP ResourcePlan endpoint to HTTPS.",
                "details": observations[-1],
            }
        )
    if "https" in endpoint_protocols and not tls_locations:
        diagnostics.append(
            {
                "code": "BIND-RESOURCE-PLAN-ENDPOINT-002",
                "message": "Generated IaC has no observable TLS termination for an HTTPS endpoint.",
                "details": observations[-1],
            }
        )
    for alternatives, expected in sorted(requirements.items()):
        observed = sum(counts[item] for item in alternatives)
        dynamic = bool(set(alternatives) & dynamic_count_types)
        observations.append(
            {
                "planIds": plan_ids[alternatives],
                "terraformTypes": list(alternatives),
                "expectedCount": expected,
                "observedMinimumCount": observed,
                "dynamicCount": dynamic,
            }
        )
        if observed >= expected:
            continue
        if dynamic:
            not_observed.append(
                {
                    "planIds": plan_ids[alternatives],
                    "reason": "A dynamic count or for_each prevents an exact source-level count.",
                    "nextGate": "terraform-plan-json",
                }
            )
            continue
        diagnostics.append(
            {
                "code": "BIND-RESOURCE-PLAN-NODE-001",
                "message": "Generated IaC omits a provider resource required by ResourcePlan.",
                "details": {
                    "planIds": plan_ids[alternatives],
                    "terraformTypes": list(alternatives),
                    "expectedCount": expected,
                    "observedCount": observed,
                },
            }
        )

    requires_disk_attachment = any(
        edge.get("label") == "attaches" and str(edge.get("to") or "").startswith("disk")
        for edge in resource_plan.get("edges") or []
    )
    if requires_disk_attachment:
        attachment_types = _DISK_ATTACHMENT_TYPES.get(provider, set())
        embedded_attachment = bool(
            {"attached_disk", "data_disk", "storage_data_disk"} & observed_keys
        )
        observed_attachment = bool(attachment_types & set(counts)) or embedded_attachment
        observations.append(
            {
                "relation": "attaches",
                "terraformTypes": sorted(attachment_types),
                "observed": observed_attachment,
            }
        )
        if not observed_attachment:
            diagnostics.append(
                {
                    "code": "BIND-RESOURCE-PLAN-EDGE-001",
                    "message": "Generated IaC creates a persistent disk but does not attach it to compute.",
                    "details": {"relation": "attaches", "provider": provider},
                }
            )

    planned_types = {
        resource_type for alternatives in requirements for resource_type in alternatives
    }
    allowed_unplanned_types = (
        _DISK_ATTACHMENT_TYPES.get(provider, set()) if requires_disk_attachment else set()
    )
    authoritative_plan = bool(
        resource_plan.get("deploymentTopology") and resource_plan.get("providerProjectionPolicy")
    )
    unexpected_types = (
        sorted(set(counts) - planned_types - allowed_unplanned_types) if authoritative_plan else []
    )
    if unexpected_types:
        diagnostics.append(
            {
                "code": "BIND-RESOURCE-PLAN-NODE-002",
                "message": (
                    "Generated IaC creates provider resources that are absent from ResourcePlan."
                ),
                "details": {"unexpectedTerraformTypes": unexpected_types},
            }
        )

    if authoritative_plan:
        by_kind = {
            str(node.get("providerKind") or ""): node
            for node in resource_plan.get("nodes") or []
            if node.get("providerKind")
        }
        required_scripts = {
            "doctor.sh",
            "plan.sh",
            "deploy.sh",
            "status.sh",
            "destroy.sh",
        }
        if "disk" in by_kind:
            required_scripts.add("purge.sh")
        missing_scripts = sorted(required_scripts - set(files))
        if missing_scripts:
            diagnostics.append(
                {
                    "code": "BIND-DEPLOYMENT-BUNDLE-001",
                    "message": ("Generated deployment bundle omits required user-run entrypoints."),
                    "details": {"missingFiles": missing_scripts},
                }
            )

        def require_body_keys(
            resource_types: set[str], required: set[str], code: str, message: str
        ) -> None:
            bodies = [
                body
                for resource_type in resource_types
                for body in resource_bodies.get(resource_type, [])
            ]
            if not bodies:
                return
            missing = []
            for index, body in enumerate(bodies):
                keys = {segment for path, _value in _walk(body) for segment in path}
                if not required & keys:
                    missing.append(index)
            if missing:
                diagnostics.append(
                    {
                        "code": code,
                        "message": message,
                        "details": {
                            "resourceTypes": sorted(resource_types),
                            "missingBodyIndexes": missing,
                            "acceptedKeys": sorted(required),
                        },
                    }
                )

        if "boot-image" in by_kind:
            if provider == "aws":
                require_body_keys(
                    {"aws_instance", "aws_launch_template"},
                    {"ami", "image_id"},
                    "BIND-BOOT-IMAGE-001",
                    "AWS compute omits its AMI reference.",
                )
            elif provider == "azure":
                require_body_keys(
                    {
                        "azurerm_linux_virtual_machine",
                        "azurerm_linux_virtual_machine_scale_set",
                    },
                    {"source_image_id", "source_image_reference"},
                    "BIND-BOOT-IMAGE-001",
                    "Azure compute omits its Virtual Machine Image reference.",
                )
            elif provider == "gcp":
                require_body_keys(
                    {"google_compute_instance", "google_compute_instance_template"},
                    {"image", "source_image"},
                    "BIND-BOOT-IMAGE-001",
                    "Google Cloud compute omits its OS Image reference.",
                )

        expected_cidrs = {
            str(cidr)
            for node in resource_plan.get("nodes") or []
            for cidr in node.get("cidrBlocks") or []
        }
        terraform_source = "\n".join(
            content for name, content in files.items() if name.endswith(".tf")
        )
        missing_cidrs = sorted(cidr for cidr in expected_cidrs if cidr not in terraform_source)
        if missing_cidrs:
            diagnostics.append(
                {
                    "code": "BIND-ADDRESS-PLAN-001",
                    "message": "Generated IaC does not implement the deterministic CIDR plan.",
                    "details": {"missingCidrs": missing_cidrs},
                }
            )
        expected_private_addresses = {
            str(node.get("privateAddress"))
            for node in resource_plan.get("nodes") or []
            if node.get("privateAddress")
        }
        missing_private_addresses = sorted(
            address for address in expected_private_addresses if address not in terraform_source
        )
        if missing_private_addresses:
            diagnostics.append(
                {
                    "code": "BIND-STATE-ENDPOINT-001",
                    "message": "Dedicated state VM does not use its planned static private address.",
                    "details": {"missingAddresses": missing_private_addresses},
                }
            )

        disk_node = by_kind.get("disk")
        if disk_node and disk_node.get("deletionPolicy") == "retain":
            disk_types = {str(item) for item in disk_node.get("terraformTypes") or []}
            unprotected = []
            for resource_type in disk_types:
                for index, body in enumerate(resource_bodies.get(resource_type, [])):
                    protected = any(
                        path and path[-1] == "prevent_destroy" and value is True
                        for path, value in _walk(body)
                    )
                    if not protected:
                        unprotected.append(f"{resource_type}[{index}]")
            if unprotected:
                diagnostics.append(
                    {
                        "code": "BIND-DISK-RETENTION-001",
                        "message": ("A retained data disk has no lifecycle.prevent_destroy guard."),
                        "details": {"resources": unprotected},
                    }
                )

        if provider == "gcp" and "cloud-nat" in by_kind:
            invalid_nat = []
            for index, body in enumerate(resource_bodies.get("google_compute_router_nat", [])):
                text = _body_text(body)
                keys = {segment for path, _value in _walk(body) for segment in path}
                if not (
                    "auto_only" in text
                    and "list_of_subnetworks" in text
                    and "all_ip_ranges" in text
                    and "subnetwork" in keys
                ):
                    invalid_nat.append(index)
            if invalid_nat:
                diagnostics.append(
                    {
                        "code": "BIND-GCP-NAT-001",
                        "message": "Cloud NAT does not implement the selected-subnetwork AUTO_ONLY policy.",
                        "details": {"bodyIndexes": invalid_nat},
                    }
                )

        if provider == "gcp" and (by_kind.get("network") or {}).get("preserveDefaultInternetRoute"):
            removed_default_route = [
                index
                for index, body in enumerate(resource_bodies.get("google_compute_network", []))
                if any(
                    path and path[-1] == "delete_default_routes_on_create" and value is True
                    for path, value in _walk(body)
                )
            ]
            if removed_default_route:
                diagnostics.append(
                    {
                        "code": "BIND-GCP-DEFAULT-ROUTE-001",
                        "message": (
                            "The selected Cloud NAT path requires the VPC Network's "
                            "default internet route to remain available."
                        ),
                        "details": {"bodyIndexes": removed_default_route},
                    }
                )

        if provider == "azure":
            invalid_addresses = []
            for index, body in enumerate(resource_bodies.get("azurerm_public_ip", [])):
                text = _body_text(body)
                if "standard" not in text or "static" not in text:
                    invalid_addresses.append(index)
            if invalid_addresses:
                diagnostics.append(
                    {
                        "code": "BIND-AZURE-PUBLIC-IP-001",
                        "message": "Azure public addresses must use Standard SKU and Static allocation.",
                        "details": {"bodyIndexes": invalid_addresses},
                    }
                )

        traffic_filter_types = {
            "aws": {"aws_security_group", "aws_vpc_security_group_ingress_rule"},
            "azure": {"azurerm_network_security_group"},
            "gcp": {"google_compute_firewall"},
        }[provider]
        traffic_bodies = [
            body
            for resource_type in traffic_filter_types
            for body in resource_bodies.get(resource_type, [])
        ]
        if traffic_bodies and not any("0.0.0.0/0" in _body_text(body) for body in traffic_bodies):
            diagnostics.append(
                {
                    "code": "BIND-PUBLIC-INGRESS-001",
                    "message": "Public HTTP topology has no client source range in its traffic policy.",
                }
            )
        if provider == "gcp" and any(
            node.get("providerKind") == "firewall" for node in resource_plan.get("nodes") or []
        ):
            unselected = []
            for index, body in enumerate(resource_bodies.get("google_compute_firewall", [])):
                keys = {segment for path, _value in _walk(body) for segment in path}
                if not {"target_tags", "target_service_accounts"} & keys:
                    unselected.append(index)
            if unselected:
                diagnostics.append(
                    {
                        "code": "BIND-GCP-FIREWALL-001",
                        "message": "GCP firewall rules must select the intended backend VMs.",
                        "details": {"bodyIndexes": unselected},
                    }
                )
        if (resource_plan.get("deploymentTopology") or {}).get("workloadLayout") != "primaryOnly":
            public_database_rules = [
                index
                for index, body in enumerate(traffic_bodies)
                if "5432" in _body_text(body) and "0.0.0.0/0" in _body_text(body)
            ]
            if public_database_rules:
                diagnostics.append(
                    {
                        "code": "BIND-DATABASE-INGRESS-001",
                        "message": "PostgreSQL port 5432 must not be publicly reachable.",
                        "details": {"bodyIndexes": public_database_rules},
                    }
                )

        group_node = by_kind.get("compute-group")
        if group_node:
            expected_capacity = int(group_node.get("desiredCapacity") or 1)
            capacity_fields = {
                "aws": ("aws_autoscaling_group", {"desired_capacity", "min_size", "max_size"}),
                "azure": ("azurerm_linux_virtual_machine_scale_set", {"instances"}),
                "gcp": (
                    (
                        "google_compute_region_instance_group_manager"
                        if group_node.get("zoneSpreadRequired")
                        else "google_compute_instance_group_manager"
                    ),
                    {"target_size"},
                ),
            }
            group_type, fields = capacity_fields[provider]
            invalid_capacity = []
            for index, body in enumerate(resource_bodies.get(group_type, [])):
                values = {key: _body_values(body, key) for key in fields}
                if any(expected_capacity not in field_values for field_values in values.values()):
                    invalid_capacity.append({"index": index, "values": values})
            if not resource_bodies.get(group_type) or invalid_capacity:
                diagnostics.append(
                    {
                        "code": "BIND-GROUP-CAPACITY-001",
                        "message": "Managed group does not implement the exact fixed replica count.",
                        "details": {
                            "resourceType": group_type,
                            "expected": expected_capacity,
                            "invalid": invalid_capacity,
                        },
                    }
                )

        if "secret-ref" in by_kind:
            terraform_text = "\n".join(
                content for name, content in files.items() if name.endswith(".tf")
            )
            secret_variable = re.search(
                r'(?is)variable\s+"database_secret_ref"\s*\{(?P<body>.*?)\}',
                terraform_text,
            )
            if not secret_variable or not re.search(
                r"(?i)sensitive\s*=\s*true", secret_variable.group("body")
            ):
                diagnostics.append(
                    {
                        "code": "BIND-SECRET-REF-001",
                        "message": (
                            "Database deployment must accept a caller-owned secret reference "
                            "through sensitive variable database_secret_ref."
                        ),
                    }
                )

    for edge in resource_plan.get("edges") or []:
        if edge.get("relation") == "connectsTo":
            not_observed.append(
                {
                    "from": edge.get("from"),
                    "to": edge.get("to"),
                    "reason": "Runtime endpoint reachability is not proven by Terraform source.",
                    "nextGate": "cloud-runtime-business-probe",
                }
            )
    by_id = {str(node.get("id") or ""): node for node in resource_plan.get("nodes") or []}
    required_edges = {
        (str(edge.get("from") or ""), str(edge.get("to") or ""))
        for edge in resource_plan.get("edges") or []
        if str(edge.get("label") or "") in _PROVISIONING_REFERENCE_LABELS
    }
    for source_id, target_id in sorted(required_edges):
        source_node = by_id.get(source_id) or {}
        target_node = by_id.get(target_id) or {}
        source_types = tuple(str(item) for item in source_node.get("terraformTypes") or [])
        target_types = tuple(str(item) for item in target_node.get("terraformTypes") or [])
        if not source_types or not target_types:
            continue
        observed = any(
            (source_type, target_type) in reference_pairs
            for source_type in source_types
            for target_type in target_types
        )
        observations.append(
            {
                "relation": f"{source_id}->{target_id}",
                "sourceTerraformTypes": list(source_types),
                "targetTerraformTypes": list(target_types),
                "observed": observed,
            }
        )
        if not observed:
            diagnostics.append(
                {
                    "code": "BIND-RESOURCE-PLAN-EDGE-002",
                    "message": (
                        "Generated IaC does not reference a required ResourcePlan dependency."
                    ),
                    "details": observations[-1],
                }
            )

    return {
        "status": "failed" if diagnostics else "passed",
        "observations": observations,
        "notObserved": not_observed,
        "diagnostics": diagnostics,
    }


def observe_terraform_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Reduce Plan JSON to non-sensitive resource counts, addresses, and references."""
    resources: list[dict[str, str]] = []

    def walk_module(module: dict[str, Any]) -> None:
        for item in module.get("resources") or []:
            resource_type = str(item.get("type") or "")
            address = str(item.get("address") or "")
            if resource_type and address:
                resources.append({"address": address, "type": resource_type})
        for child in module.get("child_modules") or []:
            walk_module(child)

    planned = plan.get("planned_values") or {}
    root = planned.get("root_module") or {}
    walk_module(root)
    counts = Counter(item["type"] for item in resources)
    address_types = {item["address"]: item["type"] for item in resources}
    reference_pairs: set[tuple[str, str]] = set()

    def expression_references(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            found.update(str(item) for item in value.get("references") or [])
            for child in value.values():
                found.update(expression_references(child))
        elif isinstance(value, list):
            for child in value:
                found.update(expression_references(child))
        return found

    def walk_configuration(module: dict[str, Any]) -> None:
        for item in module.get("resources") or []:
            source_address = str(item.get("address") or "")
            source_type = str(item.get("type") or address_types.get(source_address) or "")
            if not source_type:
                continue
            for reference in expression_references(item.get("expressions") or {}):
                target_address = ".".join(reference.split(".")[:2])
                target_type = address_types.get(target_address)
                if target_type:
                    reference_pairs.add((source_type, target_type))
        for module_call in (module.get("module_calls") or {}).values():
            child = (module_call or {}).get("module") or {}
            if isinstance(child, dict):
                walk_configuration(child)

    configuration = (plan.get("configuration") or {}).get("root_module") or {}
    walk_configuration(configuration)
    return {
        "status": "available",
        "formatVersion": plan.get("format_version"),
        "terraformVersion": plan.get("terraform_version"),
        "resourceCounts": dict(sorted(counts.items())),
        "addresses": sorted(item["address"] for item in resources),
        "referencePairs": [
            {"fromType": source, "toType": target} for source, target in sorted(reference_pairs)
        ],
    }


def validate_resource_plan_against_plan(
    resource_plan: dict[str, Any], plan_observation: dict[str, Any]
) -> dict[str, Any]:
    """Use resolved Plan counts for the node and disk-attachment checks."""
    if not resource_plan:
        return {"status": "not-applicable", "checks": [], "diagnostics": []}
    if plan_observation.get("status") != "available":
        return {
            "status": "not-observed",
            "checks": [],
            "diagnostics": [],
            "reason": plan_observation.get("reason") or "Terraform Plan JSON is unavailable.",
        }
    counts = Counter(
        {
            str(key): int(value)
            for key, value in (plan_observation.get("resourceCounts") or {}).items()
        }
    )
    requirements: Counter[tuple[str, ...]] = Counter()
    plan_ids: dict[tuple[str, ...], list[str]] = {}
    for node in resource_plan.get("nodes") or []:
        if node.get("handling") != "create":
            continue
        alternatives = tuple(sorted(str(item) for item in node.get("terraformTypes") or []))
        if not alternatives:
            continue
        minimum_count = max(1, int(node.get("minimumCount") or 1))
        requirements[alternatives] += minimum_count
        plan_ids.setdefault(alternatives, []).extend([str(node.get("id") or "")] * minimum_count)
    checks: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for alternatives, expected in sorted(requirements.items()):
        observed = sum(counts[item] for item in alternatives)
        status = "passed" if observed >= expected else "failed"
        checks.append(
            {
                "kind": "resourceCount",
                "planIds": plan_ids[alternatives],
                "terraformTypes": list(alternatives),
                "expectedCount": expected,
                "observedCount": observed,
                "status": status,
            }
        )
        if status == "failed":
            diagnostics.append(
                {
                    "code": "BIND-RESOURCE-PLAN-JSON-NODE-001",
                    "message": "Terraform Plan omits a provider resource required by ResourcePlan.",
                    "details": checks[-1],
                }
            )

    provider = str(resource_plan.get("provider") or "")
    requires_disk_attachment = any(
        edge.get("label") == "attaches" and str(edge.get("to") or "").startswith("disk")
        for edge in resource_plan.get("edges") or []
    )
    planned_types = {
        resource_type for alternatives in requirements for resource_type in alternatives
    }
    allowed_unplanned_types = (
        _DISK_ATTACHMENT_TYPES.get(provider, set()) if requires_disk_attachment else set()
    )
    authoritative_plan = bool(
        resource_plan.get("deploymentTopology") and resource_plan.get("providerProjectionPolicy")
    )
    unexpected_types = (
        sorted(set(counts) - planned_types - allowed_unplanned_types) if authoritative_plan else []
    )
    if unexpected_types:
        diagnostics.append(
            {
                "code": "BIND-RESOURCE-PLAN-JSON-NODE-002",
                "message": (
                    "Terraform Plan creates provider resources that are absent from ResourcePlan."
                ),
                "details": {"unexpectedTerraformTypes": unexpected_types},
            }
        )
    if requires_disk_attachment:
        attachment_types = _DISK_ATTACHMENT_TYPES.get(provider, set())
        observed = sum(counts[item] for item in attachment_types)
        # GCP may express the attachment inside google_compute_instance; source-level
        # validation records that alternative, so Plan absence is not promoted to failure.
        status = (
            "not-observed"
            if provider == "gcp" and observed == 0
            else ("passed" if observed else "failed")
        )
        checks.append(
            {
                "kind": "diskAttachment",
                "terraformTypes": sorted(attachment_types),
                "observedCount": observed,
                "status": status,
                "nextGate": "cloud-runtime-mount" if status == "not-observed" else None,
            }
        )
        if status == "failed":
            diagnostics.append(
                {
                    "code": "BIND-RESOURCE-PLAN-JSON-EDGE-001",
                    "message": "Terraform Plan has no separate disk attachment resource.",
                    "details": checks[-1],
                }
            )
    observed_reference_pairs = {
        (str(item.get("fromType") or ""), str(item.get("toType") or ""))
        for item in plan_observation.get("referencePairs") or []
    }
    by_id = {str(node.get("id") or ""): node for node in resource_plan.get("nodes") or []}
    required_edges = {
        (str(edge.get("from") or ""), str(edge.get("to") or ""))
        for edge in resource_plan.get("edges") or []
        if str(edge.get("label") or "") in _PROVISIONING_REFERENCE_LABELS
    }
    for source_id, target_id in sorted(required_edges):
        source_node = by_id.get(source_id) or {}
        target_node = by_id.get(target_id) or {}
        source_types = tuple(str(item) for item in source_node.get("terraformTypes") or [])
        target_types = tuple(str(item) for item in target_node.get("terraformTypes") or [])
        if not source_types or not target_types:
            continue
        observed = any(
            (source_type, target_type) in observed_reference_pairs
            for source_type in source_types
            for target_type in target_types
        )
        status = "passed" if observed else "failed"
        checks.append(
            {
                "kind": "resourceReference",
                "relation": f"{source_id}->{target_id}",
                "sourceTerraformTypes": list(source_types),
                "targetTerraformTypes": list(target_types),
                "status": status,
            }
        )
        if not observed:
            diagnostics.append(
                {
                    "code": "BIND-RESOURCE-PLAN-JSON-EDGE-002",
                    "message": (
                        "Terraform Plan configuration omits a required ResourcePlan reference."
                    ),
                    "details": checks[-1],
                }
            )
    return {
        "status": "failed" if diagnostics else "passed",
        "checks": checks,
        "diagnostics": diagnostics,
    }


def _port_observations(files: dict[str, str]) -> list[Observation]:
    observations: list[Observation] = []
    for name, content in sorted(files.items()):
        if name.endswith(".tf"):
            try:
                parsed = hcl2.load(io.StringIO(content))
            except Exception:  # syntax is owned by the earlier HCL gate
                continue
            for path, value in _walk(parsed):
                if not path:
                    continue
                key = path[-1]
                context = set(path[:-1])
                strong = key in _STRONG_PORT_KEYS or (
                    key == "port" and bool(context & _PORT_CONTEXT)
                )
                if not strong:
                    continue
                port = _literal_port(value)
                observations.append(
                    Observation(
                        kind="backendPort",
                        value=port,
                        source=f"{name}:{'.'.join(path)}",
                        confidence="observed" if port is not None else "unresolved",
                    )
                )
        for match in re.finditer(
            r"(?i)docker\s+run\b[^\r\n]*?(?:-p|--publish(?:=|\s+))\s*"
            r"(?:[0-9.]+:)?(?:\d+:)?(?P<container>\d{1,5})(?:/tcp)?",
            content,
        ):
            port = _literal_port(match.group("container"))
            observations.append(
                Observation(
                    kind="containerPort",
                    value=port,
                    source=f"{name}:docker-publish",
                    confidence="observed" if port is not None else "unresolved",
                )
            )
    return observations


_GUEST_MOUNT_PATTERN = re.compile(r"(?im)\bmount\s+(?:[^\r\n]+\s)?(?P<path>/[^\s;&|]+)")
_CONTAINER_MOUNT_PATTERNS = (
    re.compile(r"(?i)(?:target|destination|dst)=(?P<path>/[^,\s'\"]+)"),
    re.compile(r"(?i)(?:-v|--volume)\s+[^\s:]+:(?P<path>/[^\s:]+)"),
)


def _mount_observations(files: dict[str, str]) -> list[Observation]:
    observations: list[Observation] = []
    for name, content in sorted(files.items()):
        for kind, patterns in (
            ("guestMountPath", (_GUEST_MOUNT_PATTERN,)),
            ("containerMountPath", _CONTAINER_MOUNT_PATTERNS),
        ):
            for pattern in patterns:
                for match in pattern.finditer(content):
                    path = match.group("path").rstrip("/)]}'\"")
                    dynamic = any(token in path for token in ("${", "%{", "{{"))
                    observations.append(
                        Observation(
                            kind=kind,
                            value=None if dynamic else path,
                            source=f"{name}:{kind}",
                            confidence="unresolved" if dynamic else "observed",
                        )
                    )
        if re.search(r"(?im)\bmount\b[^\r\n]*(?:\$\{|%\{|\{\{)", content):
            observations.append(
                Observation(
                    kind="guestMountPath",
                    value=None,
                    source=f"{name}:dynamic-mount-command",
                    confidence="unresolved",
                )
            )
    return observations


def _unguarded_filesystem_initializations(files: dict[str, str]) -> list[str]:
    """명백히 무조건 실행되는 mkfs만 찾는다; 복잡한 셸 흐름은 추측하지 않는다."""
    locations: list[str] = []
    for name, content in sorted(files.items()):
        inside_guard = False
        for index, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if re.search(r"(?i)\bif\b.*\b(?:blkid|lsblk|file)\b", line):
                inside_guard = True
            if re.search(r"(?i)\bfi\b", line):
                inside_guard = False
                continue
            if not re.search(r"(?i)\bmkfs(?:\.[a-z0-9_-]+)?\b", line):
                continue
            inline_guard = bool(
                re.search(r"(?i)\b(?:blkid|lsblk|file)\b.*(?:&&|\|\|).*\bmkfs", line)
                or re.search(r"(?i)\bif\b.*\bmkfs", line)
            )
            if not inside_guard and not inline_guard:
                locations.append(f"{name}:{index}")
    return locations


def _ambiguous_storage_device_selections(files: dict[str, str]) -> list[str]:
    """순서가 보장되지 않은 블록 장치 목록의 첫 항목 선택만 보수적으로 거부한다."""
    locations: list[str] = []
    for name, content in sorted(files.items()):
        for index, line in enumerate(content.splitlines(), start=1):
            if re.search(r"(?i)\blsblk\b.*\bhead\s+(?:-n\s*)?1\b", line):
                locations.append(f"{name}:{index}")
    return locations


def validate_iac_bindings(
    files: dict[str, str],
    *,
    application_port: int,
    mount_path: str | None,
) -> dict[str, Any]:
    """Fail only on observable contradictions or an absent required mount operation."""
    port_observations = _port_observations(files)
    mount_observations = _mount_observations(files)
    diagnostics: list[ConsistencyDiagnostic] = []
    unresolved: list[dict[str, Any]] = []

    literal_ports = {item.value for item in port_observations if isinstance(item.value, int)}
    if literal_ports and application_port not in literal_ports:
        diagnostics.append(
            ConsistencyDiagnostic(
                code="BIND-PORT-001",
                message="Generated IaC has an observable backend/container port that conflicts with the application contract.",
                locations=[item.source for item in port_observations],
                details={
                    "expected": application_port,
                    "observed": sorted(literal_ports),
                },
            )
        )
    elif not literal_ports:
        unresolved.append(
            {
                "code": "BIND-PORT-UNRESOLVED",
                "expected": application_port,
                "reason": "No literal backend/container port was statically observable.",
            }
        )

    if mount_path:
        container_observations = [
            item for item in mount_observations if item.kind == "containerMountPath"
        ]
        relevant_observations = container_observations or [
            item for item in mount_observations if item.kind == "guestMountPath"
        ]
        literal_mounts = {
            str(item.value) for item in relevant_observations if isinstance(item.value, str)
        }
        if mount_path not in literal_mounts:
            if any(item.confidence == "unresolved" for item in relevant_observations):
                unresolved.append(
                    {
                        "code": "BIND-STORAGE-UNRESOLVED",
                        "expected": mount_path,
                        "reason": "A dynamic mount command exists but its target is not statically known.",
                    }
                )
            else:
                diagnostics.append(
                    ConsistencyDiagnostic(
                        code="BIND-STORAGE-001",
                        message="Generated IaC does not expose persistent storage at the contracted application path.",
                        locations=[item.source for item in relevant_observations],
                        details={
                            "expected": mount_path,
                            "observed": sorted(literal_mounts),
                            "observedBoundary": (
                                "container" if container_observations else "guest"
                            ),
                        },
                    )
                )
        destructive_initializations = _unguarded_filesystem_initializations(files)
        if destructive_initializations:
            diagnostics.append(
                ConsistencyDiagnostic(
                    code="BIND-STORAGE-DESTRUCTIVE-INIT",
                    message=(
                        "Persistent storage formatting is unconditionally repeated during "
                        "guest initialization."
                    ),
                    locations=destructive_initializations,
                    details={
                        "expected": "format-only-when-no-filesystem-exists",
                        "observed": "unguarded-mkfs",
                    },
                )
            )
        ambiguous_devices = _ambiguous_storage_device_selections(files)
        if ambiguous_devices:
            diagnostics.append(
                ConsistencyDiagnostic(
                    code="BIND-STORAGE-DEVICE-AMBIGUOUS",
                    message=(
                        "Persistent storage bootstrap selects the first enumerated block "
                        "device without a stable provider identity."
                    ),
                    locations=ambiguous_devices,
                    details={
                        "expected": "stable-provider-device-identity",
                        "observed": "first-enumerated-block-device",
                    },
                )
            )

    return {
        "status": "failed" if diagnostics else "passed",
        "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        "unresolved": unresolved,
        "observations": [item.__dict__ for item in [*port_observations, *mount_observations]],
    }
