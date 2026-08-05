"""Deterministic Terraform rendering for Azure, AWS, and GCP."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "easydep-iac-render/v1alpha1"
MANAGED_FILES = ("terraform/main.tf", "terraform/variables.tf", "terraform/outputs.tf")
SUPPORTED_PROVIDERS = ("azure", "aws", "gcp")


def validate_terraform(application: Path) -> dict[str, object]:
    """Run Terraform's parser/provider validation in an isolated copy when available."""
    configured = os.environ.get("EASYDEP_TERRAFORM_PATH")
    executable = configured if configured and Path(configured).is_file() else shutil.which("terraform")
    source = application / "terraform"
    if executable is None:
        return {"status": "FAILED", "errors": ["terraform executable is not installed"]}
    if not source.is_dir():
        return {"status": "FAILED", "errors": ["Terraform directory is missing"]}
    with tempfile.TemporaryDirectory(prefix="easydep-terraform-") as directory:
        work = Path(directory) / "terraform"
        shutil.copytree(source, work)
        commands = ([executable, "fmt", "-recursive", "-no-color"], [executable, "init", "-backend=false", "-input=false", "-no-color"], [executable, "validate", "-no-color"])
        for command in commands:
            result = subprocess.run(command, cwd=work, text=True, capture_output=True, check=False, timeout=120)
            if result.returncode:
                return {"status": "FAILED", "command": command[1], "errors": [result.stderr.strip() or result.stdout.strip()]}
    return {"status": "SUCCEEDED", "commands": ["fmt", "init -backend=false", "validate"]}


def render_iac(run_root: Path, spec: Any) -> dict[str, object]:
    cloud_path = spec.inputs.get("cloud")
    if cloud_path is None or not cloud_path.is_file():
        raise ValueError("IaC rendering requires a cloud resource specification")
    cloud = json.loads(cloud_path.read_text(encoding="utf-8"))
    provider = str(cloud.get("provider", "azure")).lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported Terraform provider: {provider}. Supported: {', '.join(SUPPORTED_PROVIDERS)}")
    resources = [item for item in cloud.get("resources", []) if isinstance(item, dict)]
    if not resources:
        raise ValueError("Cloud resource specification has no resources")
    validate_resource_spec(provider, resources)
    application = run_root / "application"
    terraform = application / "terraform"
    terraform.mkdir(parents=True, exist_ok=True)
    names = _names(resources)
    files = {"main.tf": _main(provider, resources, names), "variables.tf": _variables(provider, resources), "outputs.tf": _outputs(provider, resources)}
    for filename, content in files.items():
        (terraform / filename).write_text(content.rstrip() + "\n", encoding="utf-8")
    intent_path = run_root / "reports" / "deployment-intent.json"
    intent = json.loads(intent_path.read_text(encoding="utf-8")) if intent_path.is_file() else {}
    conformance = validate_deployment_iac_conformance(cloud, intent, application)
    terraform_validation = validate_terraform(application)
    report = {"schemaVersion": SCHEMA_VERSION, "renderer": f"deterministic-terraform-{provider}", "provider": provider, "renderedFiles": [f"application/terraform/{name}" for name in files], "requiredVariables": _required_variables(provider), "sourceConformance": conformance, "terraformValidation": terraform_validation, "sourceEvidence": {"cloudResourceSpecification": True, "deploymentIntent": bool(intent)}}
    reports = run_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "iac-render.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if conformance["status"] == "FAILED":
        raise ValueError("Deployment/IaC conformance failed:\n- " + "\n- ".join(conformance["errors"]))
    if terraform_validation["status"] == "FAILED":
        raise ValueError("Terraform validation failed:\n- " + "\n- ".join(terraform_validation.get("errors", [])))
    bundle = sync_deployment_bundle(application)
    report["deploymentBundle"] = bundle.relative_to(application.parent).as_posix()
    (reports / "iac-render.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def sync_deployment_bundle(application: Path) -> Path:
    """Create a self-contained, managed deployment bundle after IaC validation succeeds."""
    bundle = application / "deployment-bundle"
    marker = bundle / ".easydep-managed"
    if bundle.exists():
        if not marker.is_file():
            raise ValueError(f"Refusing to replace unmanaged deployment bundle: {bundle}")
        shutil.rmtree(bundle)
    shutil.copytree(
        application,
        bundle / "application",
        ignore=shutil.ignore_patterns("deployment-bundle", "build", ".gradle", "__pycache__"),
    )
    marker.write_text("easydep deployment bundle\n", encoding="utf-8")
    (bundle / "README.md").write_text(
        "# EasyDep deployment bundle\n\n"
        "Run `sh application/k8s/deploy.sh application/terraform -auto-approve` "
        "from this directory after configuring provider credentials.\n",
        encoding="utf-8",
    )
    return bundle


def validate_deployment_iac_conformance(cloud: dict[str, Any], intent: dict[str, Any], application: Path) -> dict[str, object]:
    source_path = application / "terraform" / "main.tf"
    errors: list[str] = []
    warnings: list[str] = []
    if not source_path.is_file():
        return {"status": "FAILED", "errors": ["Terraform main.tf is missing"], "warnings": warnings}
    provider = str(cloud.get("provider", "azure")).lower()
    source = source_path.read_text(encoding="utf-8")
    resources = [item for item in cloud.get("resources", []) if isinstance(item, dict)]
    for item in resources:
        mapped = _type(provider, item)
        if mapped is None:
            warnings.append(f"cloud resource {item.get('type')} has no deterministic Terraform mapping")
        elif f'resource "{mapped}"' not in source:
            errors.append(f"cloud resource {item.get('type')} is missing from Terraform")
        elif _terraform_resource_block(source, mapped, _logical(item)) is None:
            errors.append(f"cloud resource {item.get('name')} has no matching Terraform resource identity")
        else:
            block = _terraform_resource_block(source, mapped, _logical(item)) or ""
            for expected in _expected_attributes(provider, item):
                if expected not in block:
                    errors.append(f"cloud resource {item.get('name')} property is missing from Terraform: {expected}")
    cluster = next((r for r in resources if _role(provider, r) == "cluster"), None)
    registry = next((r for r in resources if _role(provider, r) == "registry"), None)
    access = {"azure": 'resource "azurerm_role_assignment"', "aws": 'resource "aws_iam_role_policy_attachment"', "gcp": 'resource "google_artifact_registry_repository_iam_member"'}
    if cluster and registry and access[provider] not in source:
        errors.append(f"{provider} Kubernetes cluster and registry require an image-pull access binding in Terraform")
    if cluster and provider == "aws" and 'resource "aws_eks_node_group"' not in source:
        errors.append("EKS requires a managed node group for Kubernetes workloads")
    if cluster and provider == "gcp" and 'resource "google_container_node_pool"' not in source:
        errors.append("GKE requires a node pool for Kubernetes workloads")
    if provider == "aws" and any(_type(provider, item) == "aws_subnet" for item in resources) and "vpc_id = aws_vpc." not in source:
        errors.append("AWS subnet must reference the rendered VPC")
    if provider == "gcp" and any(_type(provider, item) == "google_compute_subnetwork" for item in resources) and "network = google_compute_network." not in source:
        errors.append("GCP subnetwork must reference the rendered network")
    for workload in intent.get("workloads", []) if isinstance(intent, dict) else []:
        name = str(workload.get("name", ""))
        if name and not (application / "k8s" / name).is_dir():
            errors.append(f"deployment workload {name} has no rendered Kubernetes manifests")
    unresolved_images = [workload for workload in intent.get("workloads", []) if "__EASYDEP_REGISTRY_" in str(workload.get("image", ""))] if isinstance(intent, dict) else []
    if unresolved_images:
        registries = {_resource_id(item) for item in resources if _role(provider, item) == "registry"}
        clusters = {_resource_id(item) for item in resources if _role(provider, item) == "cluster"}
        default_cluster = next(iter(clusters)) if len(clusters) == 1 else None
        for workload in unresolved_images:
            reference = workload.get("registryRef")
            if not isinstance(reference, str) or reference not in registries:
                errors.append(f"deployment workload {workload.get('name')} must declare a valid registryRef")
                continue
            cluster_reference = workload.get("clusterRef", default_cluster)
            if not isinstance(cluster_reference, str) or cluster_reference not in clusters:
                errors.append(f"deployment workload {workload.get('name')} must declare a valid clusterRef")
                continue
            cluster_item = next(item for item in resources if _resource_id(item) == cluster_reference)
            registry_item = next(item for item in resources if _resource_id(item) == reference)
            binding = _pull_binding_name(provider, cluster_item, registry_item)
            binding_type = access[provider]
            if f'{binding_type} "{binding}"' not in source:
                errors.append(f"deployment workload {workload.get('name')} registryRef {reference} has no image-pull binding for clusterRef {cluster_reference}")
        if not (application / "k8s" / "render-images.sh").is_file():
            errors.append("registry image rendering script is missing")
        if not (application / "k8s" / "deploy.sh").is_file():
            errors.append("IaC-to-Kubernetes deployment script is missing")
        if not (application / "k8s" / "build-push.sh").is_file():
            errors.append("container build-and-push script is missing")
        outputs = application / "terraform" / "outputs.tf"
        if not outputs.is_file() or 'output "registry_image_bases"' not in outputs.read_text(encoding="utf-8"):
            errors.append("Terraform registry_image_bases output is missing")
    if not intent:
        warnings.append("Deployment intent is absent; workload-to-infrastructure validation was skipped")
    return {"status": "FAILED" if errors else ("SUCCEEDED_WITH_WARNINGS" if warnings else "SUCCEEDED"), "errors": errors, "warnings": warnings}


def validate_resource_spec(provider: str, resources: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    identities: set[tuple[str, str]] = set()
    references = _reference_index(resources)
    ids: set[str] = set()
    for index, item in enumerate(resources):
        mapped = _type(provider, item)
        name = item.get("name")
        if mapped is None:
            errors.append(f"resources[{index}].type is not supported for {provider}: {item.get('type')}")
            continue
        if not isinstance(name, str) or not name.strip():
            errors.append(f"resources[{index}].name must be a non-empty string")
            continue
        identity = (mapped, _logical(item))
        if identity in identities:
            errors.append(f"resources[{index}] duplicates Terraform identity {mapped}.{identity[1]}")
        identities.add(identity)
        resource_id = _resource_id(item)
        if resource_id in ids:
            errors.append(f"resources[{index}] duplicates resource id {resource_id}")
        ids.add(resource_id)
        unknown_dependencies = sorted(_depends_on(item) - set(references))
        if unknown_dependencies:
            errors.append(f"resources[{index}].dependsOn references unknown resources: {', '.join(unknown_dependencies)}")
    clusters = [item for item in resources if _role(provider, item) == "cluster"]
    if len(clusters) > 1:
        errors.append("IaC generation currently supports one Kubernetes cluster per cloud resource specification")
    if provider == "aws":
        for cluster in (item for item in resources if _type(provider, item) == "aws_eks_cluster"):
            subnets = _related_resources(cluster, resources, provider, "aws_subnet")
            zones = {str(item.get("availabilityZone", "")) for item in subnets}
            if len(subnets) < 2 or "" in zones or len(zones) < 2:
                errors.append(f"EKS cluster {_resource_name(cluster)} requires at least two related subnets in distinct availabilityZone values")
    if provider == "gcp":
        for cluster in (item for item in resources if _type(provider, item) == "google_container_cluster"):
            if len(_related_resources(cluster, resources, provider, "google_compute_subnetwork")) != 1:
                errors.append(f"GKE cluster {_resource_name(cluster)} requires exactly one related subnetwork")
    if provider == "azure":
        vnets = {_resource_name(item): item for item in resources if _type(provider, item) == "azurerm_virtual_network"}
        zones = {_resource_name(item): item for item in resources if _type(provider, item) == "azurerm_private_dns_zone"}
        for cluster in (item for item in resources if _type(provider, item) == "azurerm_kubernetes_cluster"):
            reference = str(cluster.get("networking", {}).get("subnet", ""))
            vnet_name, _, subnet_name = reference.partition("/")
            if reference and (not vnet_name or not subnet_name or subnet_name not in {str(item.get("name")) for item in vnets.get(vnet_name, {}).get("subnets", [])}):
                errors.append(f"AKS cluster {_resource_name(cluster)} networking.subnet must reference a declared VNet subnet")
        for server in (item for item in resources if _type(provider, item) == "azurerm_mysql_flexible_server"):
            networking = server.get("networking", {})
            reference = str(networking.get("delegatedSubnet", ""))
            vnet_name, _, subnet_name = reference.partition("/")
            zone = zones.get(str(networking.get("privateDnsZone", "")))
            valid_subnet = subnet_name in {str(item.get("name")) for item in vnets.get(vnet_name, {}).get("subnets", [])}
            if not vnet_name or not valid_subnet or zone is None or _single_related_resource(zone, resources, provider, "azurerm_virtual_network") is None:
                errors.append(f"MySQL server {_resource_name(server)} private networking requires a declared delegated subnet and DNS zone linked by dependsOn")
    if errors:
        raise ValueError("Invalid cloud resource specification:\n- " + "\n- ".join(errors))


def _variables(provider: str, resources: list[dict[str, Any]]) -> str:
    provider_blocks = {
        "azure": ['terraform {\n  required_version = ">= 1.6.0"\n  required_providers {\n    azurerm = {\n      source = "hashicorp/azurerm"\n      version = "~> 4.0"\n    }\n  }\n}', 'provider "azurerm" {\n  features {}\n}', 'variable "resource_group_name" {\n  type = string\n}', 'variable "location" {\n  type = string\n}', 'variable "mysql_administrator_login" {\n  type = string\n  default = "easydepadmin"\n}', 'variable "mysql_administrator_password" {\n  type = string\n  sensitive = true\n}'],
        "aws": ['terraform {\n  required_version = ">= 1.6.0"\n  required_providers {\n    aws = {\n      source = "hashicorp/aws"\n      version = "~> 5.0"\n    }\n  }\n}', 'provider "aws" {\n  region = var.region\n}', 'variable "region" {\n  type = string\n}', 'variable "eks_cluster_role_arn" {\n  type = string\n}', 'variable "eks_node_role_arns" {\n  type = map(string)\n}', 'variable "eks_node_role_names" {\n  type = map(string)\n}', 'variable "subnet_ids" {\n  type = list(string)\n  default = []\n}', 'variable "existing_vpc_id" {\n  type = string\n  default = null\n}', 'variable "availability_zones" {\n  type = list(string)\n  default = []\n}', 'variable "db_username" {\n  type = string\n  default = "easydepadmin"\n}', 'variable "db_password" {\n  type = string\n  sensitive = true\n}'],
        "gcp": ['terraform {\n  required_version = ">= 1.6.0"\n  required_providers {\n    google = {\n      source = "hashicorp/google"\n      version = "~> 6.0"\n    }\n  }\n}', 'provider "google" {\n  project = var.project_id\n  region = var.region\n}', 'variable "project_id" {\n  type = string\n}', 'variable "region" {\n  type = string\n}', 'variable "network_name" {\n  type = string\n  default = null\n}', 'variable "gke_node_service_accounts" {\n  type = map(string)\n}'],
    }[provider]
    for item in resources:
        raw, logical = str(item.get("name", "")), _logical(item)
        if raw.startswith("<") and raw.endswith(">"):
            provider_blocks.append(f'variable "{logical}_name" {{\n  type = string\n}}')
    return "\n\n".join(provider_blocks)


def _required_variables(provider: str) -> list[dict[str, str]]:
    values = {
        "azure": [("resource_group_name", "target Azure resource group"), ("location", "target Azure region"), ("mysql_administrator_password", "MySQL administrator password")],
        "aws": [("region", "target AWS region"), ("eks_cluster_role_arn", "EKS control-plane IAM role ARN"), ("eks_node_role_arns", "EKS cluster logical name to node IAM role ARN"), ("eks_node_role_names", "EKS cluster logical name to node IAM role name"), ("db_password", "RDS administrator password")],
        "gcp": [("project_id", "target GCP project ID"), ("region", "target GCP region"), ("gke_node_service_accounts", "GKE cluster logical name to node service-account email")],
    }[provider]
    return [{"name": name, "description": description} for name, description in values]


def _main(provider: str, resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    builders = {"azure": _azure, "aws": _aws, "gcp": _gcp}
    return builders[provider](resources, names)


def _azure(resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    blocks: list[str] = []
    cluster = registry = None
    vnets = {_resource_name(item): item for item in resources if _type("azure", item) == "azurerm_virtual_network"}
    dns_zones = {str(item.get("name")): _logical(item) for item in resources if _type("azure", item) == "azurerm_private_dns_zone"}
    for item in resources:
        logical, kind, name = _logical(item), _type("azure", item), names[_logical(item)]
        if kind == "azurerm_virtual_network":
            blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  address_space = [{json.dumps(item.get("addressSpace", "10.0.0.0/16"))}]\n  location = var.location\n  resource_group_name = var.resource_group_name\n}}')
            for subnet in item.get("subnets", []):
                subnet_name = str(subnet.get("name", "subnet"))
                subnet_id = _tf_id(subnet_name)
                delegation = ""
                if subnet.get("delegations"):
                    service = str(subnet["delegations"][0])
                    delegation = f'\n  delegation {{\n    name = "delegation"\n    service_delegation {{\n      name = {json.dumps(service)}\n      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]\n    }}\n  }}'
                blocks.append(f'resource "azurerm_subnet" "{logical}_{subnet_id}" {{\n  name = {json.dumps(subnet_name)}\n  resource_group_name = var.resource_group_name\n  virtual_network_name = azurerm_virtual_network.{logical}.name\n  address_prefixes = [{json.dumps(str(subnet.get("addressPrefix", "10.0.1.0/24")))}]{delegation}\n}}')
        elif kind == "azurerm_container_registry":
            registry = logical; blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  resource_group_name = var.resource_group_name\n  location = var.location\n  sku = "Basic"\n  admin_enabled = false\n}}')
        elif kind == "azurerm_kubernetes_cluster":
            cluster = logical
            pool = next(iter(item.get("nodePools", [])), {})
            networking = item.get("networking", {})
            subnet_reference = str(networking.get("subnet", ""))
            vnet_name, _, subnet_name = subnet_reference.partition("/")
            target_vnet = vnets.get(vnet_name)
            subnet_id = f'\n    vnet_subnet_id = azurerm_subnet.{_logical(target_vnet)}_{_tf_id(subnet_name)}.id' if target_vnet and subnet_name else ""
            scaling = bool(pool.get("enableAutoScaling", False))
            autoscaling = f'\n    min_count = {int(pool.get("minCount", 1))}\n    max_count = {int(pool.get("maxCount", 3))}' if scaling else ""
            blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  location = var.location\n  resource_group_name = var.resource_group_name\n  dns_prefix = replace({name}, "-", "")\n  private_cluster_enabled = {str(bool(networking.get("privateCluster", False))).lower()}\n  default_node_pool {{\n    name = {json.dumps(str(pool.get("name", "system")))}\n    vm_size = {json.dumps(str(pool.get("vmSize", "Standard_B2s")))}\n    node_count = {int(pool.get("count", 1))}\n    enable_auto_scaling = {str(scaling).lower()}{autoscaling}{subnet_id}\n  }}\n  identity {{ type = "SystemAssigned" }}\n}}')
            for extra in list(item.get("nodePools", []))[1:]:
                extra_scaling = bool(extra.get("enableAutoScaling", False))
                extra_range = f'\n  min_count = {int(extra.get("minCount", 1))}\n  max_count = {int(extra.get("maxCount", 3))}' if extra_scaling else ""
                blocks.append(f'resource "azurerm_kubernetes_cluster_node_pool" "{logical}_{_tf_id(str(extra.get("name", "user")))}" {{\n  name = {json.dumps(str(extra.get("name", "user")))}\n  kubernetes_cluster_id = azurerm_kubernetes_cluster.{logical}.id\n  vm_size = {json.dumps(str(extra.get("vmSize", "Standard_B2s")))}\n  node_count = {int(extra.get("count", 1))}\n  enable_auto_scaling = {str(extra_scaling).lower()}{extra_range}{subnet_id}\n}}')
        elif kind == "azurerm_mysql_flexible_server":
            networking = item.get("networking", {})
            delegated_reference = str(networking.get("delegatedSubnet", ""))
            delegated_vnet_name, _, delegated = delegated_reference.partition("/")
            delegated_vnet = vnets.get(delegated_vnet_name)
            dns = dns_zones.get(str(networking.get("privateDnsZone", "")))
            private_lines = ""
            if delegated_vnet and delegated:
                private_lines += f'\n  delegated_subnet_id = azurerm_subnet.{_logical(delegated_vnet)}_{_tf_id(delegated)}.id'
            if dns:
                private_lines += f'\n  private_dns_zone_id = azurerm_private_dns_zone.{dns}.id\n  depends_on = [azurerm_private_dns_zone_virtual_network_link.{dns}_link]'
            public_access = str(networking.get("publicNetworkAccess", "Enabled")).lower() != "disabled"
            mysql_version = str(item.get("version", "8.0"))
            if mysql_version == "8.0":
                mysql_version = "8.0.21"
            blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  resource_group_name = var.resource_group_name\n  location = var.location\n  administrator_login = var.mysql_administrator_login\n  administrator_password = var.mysql_administrator_password\n  sku_name = {json.dumps(str(item.get("sku", "Standard_B2s")))}\n  version = {json.dumps(mysql_version)}\n  backup_retention_days = {int(item.get("backupRetentionDays", 7))}{private_lines}\n  storage {{ size_gb = {int(item.get("storageGb", 32))} }}\n}}')
            for database in item.get("databases", []):
                database_id = _tf_id(str(database))
                blocks.append(f'resource "azurerm_mysql_flexible_database" "{logical}_{database_id}" {{\n  name = {json.dumps(str(database))}\n  server_name = azurerm_mysql_flexible_server.{logical}.name\n  resource_group_name = var.resource_group_name\n  charset = "utf8mb4"\n  collation = "utf8mb4_unicode_ci"\n}}')
        elif kind == "azurerm_key_vault":
            blocks.append(f'data "azurerm_client_config" "current" {{}}\n\nresource "{kind}" "{logical}" {{\n  name = {name}\n  location = var.location\n  resource_group_name = var.resource_group_name\n  tenant_id = data.azurerm_client_config.current.tenant_id\n  sku_name = "standard"\n}}')
        elif kind == "azurerm_log_analytics_workspace": blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  location = var.location\n  resource_group_name = var.resource_group_name\n  sku = "PerGB2018"\n}}')
        elif str(item.get("type")) == "Microsoft.Network/privateDnsZones": blocks.append(f'resource "azurerm_private_dns_zone" "{logical}" {{\n  name = {name}\n  resource_group_name = var.resource_group_name\n}}')
    for zone in (item for item in resources if _type("azure", item) == "azurerm_private_dns_zone"):
        dns = _logical(zone)
        vnet = _single_related_resource(zone, resources, "azure", "azurerm_virtual_network")
        if vnet:
            blocks.append(f'resource "azurerm_private_dns_zone_virtual_network_link" "{dns}_link" {{\n  name = "{dns}-vnet-link"\n  resource_group_name = var.resource_group_name\n  private_dns_zone_name = azurerm_private_dns_zone.{dns}.name\n  virtual_network_id = azurerm_virtual_network.{_logical(vnet)}.id\n}}')
    clusters = [item for item in resources if _type("azure", item) == "azurerm_kubernetes_cluster"]
    registries = [item for item in resources if _type("azure", item) == "azurerm_container_registry"]
    for target_cluster in clusters:
        targets = _related_resources(target_cluster, resources, "azure", "azurerm_container_registry") or (registries if len(registries) == 1 else [])
        for target_registry in targets:
            cluster_id, registry_id = _logical(target_cluster), _logical(target_registry)
            blocks.append(f'resource "azurerm_role_assignment" "{cluster_id}_{registry_id}_acr_pull" {{\n  scope = azurerm_container_registry.{registry_id}.id\n  role_definition_name = "AcrPull"\n  principal_id = azurerm_kubernetes_cluster.{cluster_id}.kubelet_identity[0].object_id\n}}')
    return "\n\n".join(blocks)


def _aws(resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    blocks: list[str] = []; cluster = registry = None
    for item in resources:
        logical, kind, name = _logical(item), _type("aws", item), names[_logical(item)]
        if kind == "aws_vpc":
            blocks.append(f'resource "aws_vpc" "{logical}" {{\n  cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.0.0/16")))}\n  enable_dns_hostnames = true\n  enable_dns_support = true\n  tags = {{ Name = {name} }}\n}}')
        elif kind == "aws_subnet":
            zone = item.get("availabilityZone")
            zone_expr = json.dumps(str(zone)) if zone else "null"
            vpc = _single_related_resource(item, resources, "aws", "aws_vpc")
            vpc_expr = f'aws_vpc.{_logical(vpc)}.id' if vpc else 'var.existing_vpc_id'
            blocks.append(f'resource "aws_subnet" "{logical}" {{\n  vpc_id = {vpc_expr}\n  cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.1.0/24")))}\n  availability_zone = {zone_expr}\n  tags = {{ Name = {name} }}\n}}')
        elif kind == "aws_ecr_repository": registry = logical; blocks.append(f'resource "aws_ecr_repository" "{logical}" {{\n  name = {name}\n}}')
        elif kind == "aws_eks_cluster":
            cluster = logical
            subnets = [_logical(subnet) for subnet in _related_resources(item, resources, "aws", "aws_subnet")]
            subnet_ids = "[" + ", ".join(f"aws_subnet.{item}.id" for item in subnets) + "]"
            blocks.append(f'resource "aws_eks_cluster" "{logical}" {{\n  name = {name}\n  role_arn = var.eks_cluster_role_arn\n  vpc_config {{ subnet_ids = {subnet_ids} }}\n}}')
            for pool in item.get("nodePools", [{"name": "default"}]):
                pool_name = str(pool.get("name", "default"))
                minimum, desired, maximum = int(pool.get("minCount", 1)), int(pool.get("count", 1)), int(pool.get("maxCount", 3))
                blocks.append(f'resource "aws_eks_node_group" "{logical}_{_tf_id(pool_name)}" {{\n  cluster_name = aws_eks_cluster.{logical}.name\n  node_group_name = {json.dumps(pool_name)}\n  node_role_arn = var.eks_node_role_arns[{json.dumps(logical)}]\n  subnet_ids = {subnet_ids}\n  scaling_config {{\n    desired_size = {desired}\n    min_size = {minimum}\n    max_size = {maximum}\n  }}\n}}')
        elif kind == "aws_db_instance": blocks.append(f'resource "aws_db_instance" "{logical}" {{\n  identifier = {name}\n  engine = {json.dumps(str(item.get("engine", "mysql")))}\n  instance_class = {json.dumps(str(item.get("instanceClass", "db.t3.micro")))}\n  allocated_storage = {int(item.get("allocatedStorage", 20))}\n  username = var.db_username\n  password = var.db_password\n  skip_final_snapshot = true\n}}')
        elif kind == "aws_secretsmanager_secret": blocks.append(f'resource "aws_secretsmanager_secret" "{logical}" {{\n  name = {name}\n}}')
        elif kind == "aws_cloudwatch_log_group": blocks.append(f'resource "aws_cloudwatch_log_group" "{logical}" {{\n  name = {name}\n  retention_in_days = 30\n}}')
    clusters = [item for item in resources if _type("aws", item) == "aws_eks_cluster"]
    registries = [item for item in resources if _type("aws", item) == "aws_ecr_repository"]
    for target_cluster in clusters:
        targets = _related_resources(target_cluster, resources, "aws", "aws_ecr_repository") or (registries if len(registries) == 1 else [])
        for target_registry in targets:
            blocks.append(f'resource "aws_iam_role_policy_attachment" "{_logical(target_cluster)}_{_logical(target_registry)}_ecr_pull" {{\n  role = var.eks_node_role_names[{json.dumps(_logical(target_cluster))}]\n  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"\n}}')
    return "\n\n".join(blocks)


def _gcp(resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    blocks: list[str] = []; cluster = registry = None
    for item in resources:
        logical, kind, name = _logical(item), _type("gcp", item), names[_logical(item)]
        if kind == "google_compute_network":
            blocks.append(f'resource "google_compute_network" "{logical}" {{\n  name = {name}\n  auto_create_subnetworks = false\n}}')
        elif kind == "google_compute_subnetwork":
            network = _single_related_resource(item, resources, "gcp", "google_compute_network")
            network_expr = f'google_compute_network.{_logical(network)}.id' if network else 'var.network_name'
            blocks.append(f'resource "google_compute_subnetwork" "{logical}" {{\n  name = {name}\n  ip_cidr_range = {json.dumps(str(item.get("ipCidrRange", "10.0.1.0/24")))}\n  region = var.region\n  network = {network_expr}\n}}')
        elif kind == "google_artifact_registry_repository": registry = logical; blocks.append(f'resource "google_artifact_registry_repository" "{logical}" {{\n  location = var.region\n  repository_id = {name}\n  format = "DOCKER"\n}}')
        elif kind == "google_container_cluster":
            cluster = logical
            subnet = _single_related_resource(item, resources, "gcp", "google_compute_subnetwork")
            network = _single_related_resource(subnet, resources, "gcp", "google_compute_network") if subnet else None
            network_expr = f'google_compute_network.{_logical(network)}.id' if network else 'null'
            subnet_expr = f'google_compute_subnetwork.{_logical(subnet)}.id' if subnet else 'null'
            blocks.append(f'resource "google_container_cluster" "{logical}" {{\n  name = {name}\n  location = var.region\n  network = {network_expr}\n  subnetwork = {subnet_expr}\n  remove_default_node_pool = true\n  initial_node_count = 1\n}}')
            for pool in item.get("nodePools", [{"name": "default"}]):
                pool_name = str(pool.get("name", "default"))
                blocks.append(f'resource "google_container_node_pool" "{logical}_{_tf_id(pool_name)}" {{\n  name = {json.dumps(pool_name)}\n  location = google_container_cluster.{logical}.location\n  cluster = google_container_cluster.{logical}.name\n  node_count = {int(pool.get("count", 1))}\n  node_config {{\n    service_account = var.gke_node_service_accounts[{json.dumps(logical)}]\n    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]\n  }}\n}}')
        elif kind == "google_sql_database_instance": blocks.append(f'resource "google_sql_database_instance" "{logical}" {{\n  name = {name}\n  database_version = {json.dumps(str(item.get("databaseVersion", "MYSQL_8_0")))}\n  region = var.region\n  settings {{ tier = {json.dumps(str(item.get("tier", "db-f1-micro")))} }}\n}}')
        elif kind == "google_secret_manager_secret": blocks.append(f'resource "google_secret_manager_secret" "{logical}" {{\n  secret_id = {name}\n  replication {{ auto {{}} }}\n}}')
        elif kind == "google_logging_project_bucket_config": blocks.append(f'resource "google_logging_project_bucket_config" "{logical}" {{\n  location = "global"\n  bucket_id = {name}\n  retention_days = 30\n}}')
    clusters = [item for item in resources if _type("gcp", item) == "google_container_cluster"]
    registries = [item for item in resources if _type("gcp", item) == "google_artifact_registry_repository"]
    granted: set[tuple[str, str]] = set()
    for target_cluster in clusters:
        targets = _related_resources(target_cluster, resources, "gcp", "google_artifact_registry_repository") or (registries if len(registries) == 1 else [])
        for target_registry in targets:
            registry_id = _logical(target_registry)
            grant = (_logical(target_cluster), registry_id)
            if grant in granted:
                continue
            granted.add(grant)
            blocks.append(f'resource "google_artifact_registry_repository_iam_member" "{_logical(target_cluster)}_{registry_id}_gke_artifact_pull" {{\n  location = google_artifact_registry_repository.{registry_id}.location\n  repository = google_artifact_registry_repository.{registry_id}.name\n  role = "roles/artifactregistry.reader"\n  member = "serviceAccount:${{var.gke_node_service_accounts[{json.dumps(_logical(target_cluster))}]}}"\n}}')
    return "\n\n".join(blocks)


def _outputs(provider: str, resources: list[dict[str, Any]]) -> str:
    values = [f'output "provider" {{ value = {json.dumps(provider)} }}']
    registries = [item for item in resources if _role(provider, item) == "registry"]
    if registries:
        registry_values: list[str] = []
        for item in registries:
            logical, kind = _logical(item), _type(provider, item)
            if kind == "azurerm_container_registry":
                registry_value = f"azurerm_container_registry.{logical}.login_server"
            elif kind == "aws_ecr_repository":
                registry_value = f"aws_ecr_repository.{logical}.repository_url"
            else:
                registry_value = f'format("%s-docker.pkg.dev/%s/%s", var.region, var.project_id, google_artifact_registry_repository.{logical}.repository_id)'
            registry_values.append(f"{json.dumps(_resource_id(item))} = {registry_value}")
        values.append('output "registry_image_bases" { value = {' + " ".join(registry_values) + '} }')
    for item in resources:
        kind, logical = _type(provider, item), _logical(item)
        if _role(provider, item) == "cluster": values.append(f'output "{logical}_cluster_name" {{ value = {kind}.{logical}.name }}')
    return "\n\n".join(values)


def _pull_binding_name(provider: str, cluster: dict[str, Any], registry: dict[str, Any]) -> str:
    suffix = "acr_pull" if provider == "azure" else "ecr_pull" if provider == "aws" else "gke_artifact_pull"
    return f"{_logical(cluster)}_{_logical(registry)}_{suffix}"


def _type(provider: str, item: dict[str, Any]) -> str | None:
    kind = str(item.get("type", "")).lower()
    azure = {"microsoft.network/virtualnetworks": "azurerm_virtual_network", "microsoft.network/privatednszones": "azurerm_private_dns_zone", "microsoft.containerregistry/registries": "azurerm_container_registry", "microsoft.containerservice/managedclusters": "azurerm_kubernetes_cluster", "microsoft.dbformysql/flexibleservers": "azurerm_mysql_flexible_server", "microsoft.keyvault/vaults": "azurerm_key_vault", "microsoft.operationalinsights/workspaces": "azurerm_log_analytics_workspace"}
    aws = {"aws::ec2::vpc": "aws_vpc", "aws::ec2::subnet": "aws_subnet", "aws::ecr::repository": "aws_ecr_repository", "aws::eks::cluster": "aws_eks_cluster", "aws::rds::dbinstance": "aws_db_instance", "aws::secretsmanager::secret": "aws_secretsmanager_secret", "aws::logs::loggroup": "aws_cloudwatch_log_group"}
    gcp = {"compute.googleapis.com/network": "google_compute_network", "compute.googleapis.com/subnetwork": "google_compute_subnetwork", "artifactregistry.googleapis.com/repository": "google_artifact_registry_repository", "container.googleapis.com/cluster": "google_container_cluster", "sqladmin.googleapis.com/instance": "google_sql_database_instance", "secretmanager.googleapis.com/secret": "google_secret_manager_secret", "logging.googleapis.com/logbucket": "google_logging_project_bucket_config"}
    return {"azure": azure, "aws": aws, "gcp": gcp}[provider].get(kind)


def _role(provider: str, item: dict[str, Any]) -> str | None:
    kind = _type(provider, item)
    if kind in {"azurerm_kubernetes_cluster", "aws_eks_cluster", "google_container_cluster"}: return "cluster"
    if kind in {"azurerm_container_registry", "aws_ecr_repository", "google_artifact_registry_repository"}: return "registry"
    return None


def _resource_name(item: dict[str, Any]) -> str:
    return str(item.get("name", ""))


def _resource_id(item: dict[str, Any]) -> str:
    explicit = item.get("id")
    return str(explicit) if isinstance(explicit, str) and explicit.strip() else f"{item.get('type')}:{_resource_name(item)}"


def _reference_index(resources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Accept explicit IDs; accept names only when they identify one resource."""
    result = {_resource_id(item): item for item in resources}
    names: dict[str, list[dict[str, Any]]] = {}
    for item in resources:
        names.setdefault(_resource_name(item), []).append(item)
    for name, matches in names.items():
        if len(matches) == 1:
            result[name] = matches[0]
    return result


def _depends_on(item: dict[str, Any]) -> set[str]:
    values = item.get("dependsOn", [])
    return {str(value) for value in values if isinstance(value, str)} if isinstance(values, list) else set()


def _ancestor_names(item: dict[str, Any], references: dict[str, dict[str, Any]], seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    result: set[str] = set()
    for name in _depends_on(item):
        if name in seen:
            continue
        result.add(name)
        target = references.get(name)
        if target is not None:
            result.update(_ancestor_names(target, references, seen | {name}))
    return result


def _related_resources(item: dict[str, Any], resources: list[dict[str, Any]], provider: str, terraform_type: str) -> list[dict[str, Any]]:
    """Find resources connected by explicit dependencies, never by list position."""
    candidates = [candidate for candidate in resources if _type(provider, candidate) == terraform_type]
    references = _reference_index(resources)
    ancestors = _ancestor_names(item, references)
    direct = [candidate for candidate in candidates if _resource_name(candidate) in _depends_on(item) or _resource_id(candidate) in _depends_on(item)]
    if direct:
        return direct
    related = [candidate for candidate in candidates if _resource_name(candidate) in ancestors]
    if related:
        return related
    roots = ancestors | {_resource_name(item), _resource_id(item)}
    return [candidate for candidate in candidates if _ancestor_names(candidate, references) & roots]


def _single_related_resource(item: dict[str, Any], resources: list[dict[str, Any]], provider: str, terraform_type: str) -> dict[str, Any] | None:
    related = _related_resources(item, resources, provider, terraform_type)
    if len(related) == 1:
        return related[0]
    candidates = [candidate for candidate in resources if _type(provider, candidate) == terraform_type]
    return candidates[0] if len(candidates) == 1 else None


def _expected_attributes(provider: str, item: dict[str, Any]) -> list[str]:
    """Stable source-spec values that must survive deterministic rendering."""
    kind = _type(provider, item)
    if kind == "azurerm_virtual_network":
        return [f'address_space = [{json.dumps(item.get("addressSpace", "10.0.0.0/16"))}]']
    if kind == "azurerm_container_registry":
        return [f'sku = {json.dumps(str(item.get("sku", "Basic")))}']
    if kind == "azurerm_kubernetes_cluster":
        network = item.get("networking", {})
        pool = next(iter(item.get("nodePools", [])), {})
        return [f'private_cluster_enabled = {str(bool(network.get("privateCluster", False))).lower()}', f'vm_size = {json.dumps(str(pool.get("vmSize", "Standard_B2s")))}']
    if kind == "azurerm_mysql_flexible_server":
        version = str(item.get("version", "8.0"))
        if version == "8.0":
            version = "8.0.21"
        return [f'sku_name = {json.dumps(str(item.get("sku", "Standard_B2s")))}', f'version = {json.dumps(version)}', f'size_gb = {int(item.get("storageGb", 32))}']
    if kind == "aws_vpc": return [f'cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.0.0/16")))}']
    if kind == "aws_subnet": return [f'cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.1.0/24")))}']
    if kind == "aws_db_instance": return [f'engine = {json.dumps(str(item.get("engine", "mysql")))}', f'instance_class = {json.dumps(str(item.get("instanceClass", "db.t3.micro")))}', f'allocated_storage = {int(item.get("allocatedStorage", 20))}']
    if kind == "google_compute_subnetwork": return [f'ip_cidr_range = {json.dumps(str(item.get("ipCidrRange", "10.0.1.0/24")))}']
    if kind == "google_sql_database_instance": return [f'database_version = {json.dumps(str(item.get("databaseVersion", "MYSQL_8_0")))}', f'tier = {json.dumps(str(item.get("tier", "db-f1-micro")))}']
    return []


def _terraform_resource_block(source: str, terraform_type: str, logical: str) -> str | None:
    match = re.search(rf'resource "{re.escape(terraform_type)}" "{re.escape(logical)}"\s*\{{', source)
    if match is None:
        return None
    start = source.find("{", match.start())
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1]
    return None


def _names(resources: list[dict[str, Any]]) -> dict[str, str]:
    return {_logical(item): f"var.{_logical(item)}_name" if str(item.get("name", "")).startswith("<") else json.dumps(str(item.get("name", _logical(item)))) for item in resources}


def _logical(item: dict[str, Any]) -> str:
    return _tf_id(str(item.get("name") or str(item.get("type", "resource")).split("/")[-1]))


def _tf_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_").lower()
    return value if value and not value[0].isdigit() else f"resource_{value or 'item'}"
