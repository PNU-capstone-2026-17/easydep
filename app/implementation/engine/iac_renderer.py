"""Deterministic Terraform rendering for Azure, AWS, and GCP."""
from __future__ import annotations

import json
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
    executable = shutil.which("terraform")
    source = application / "terraform"
    if executable is None:
        return {"status": "SKIPPED", "reason": "terraform executable is not installed"}
    if not source.is_dir():
        return {"status": "FAILED", "errors": ["Terraform directory is missing"]}
    with tempfile.TemporaryDirectory(prefix="easydep-terraform-") as directory:
        work = Path(directory) / "terraform"
        shutil.copytree(source, work)
        commands = ([executable, "fmt", "-check", "-recursive", "-no-color"], [executable, "init", "-backend=false", "-input=false", "-no-color"], [executable, "validate", "-no-color"])
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
    report = {"schemaVersion": SCHEMA_VERSION, "renderer": f"deterministic-terraform-{provider}", "provider": provider, "renderedFiles": [f"application/terraform/{name}" for name in files], "sourceConformance": conformance, "terraformValidation": terraform_validation, "sourceEvidence": {"cloudResourceSpecification": True, "deploymentIntent": bool(intent)}}
    reports = run_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "iac-render.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if conformance["status"] == "FAILED":
        raise ValueError("Deployment/IaC conformance failed:\n- " + "\n- ".join(conformance["errors"]))
    if terraform_validation["status"] == "FAILED":
        raise ValueError("Terraform validation failed:\n- " + "\n- ".join(terraform_validation.get("errors", [])))
    return report


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
        elif not re.search(rf'resource "{re.escape(mapped)}" "{re.escape(_logical(item))}"', source):
            errors.append(f"cloud resource {item.get('name')} has no matching Terraform resource identity")
        else:
            for expected in _expected_attributes(provider, item):
                if expected not in source:
                    errors.append(f"cloud resource {item.get('name')} property is missing from Terraform: {expected}")
    cluster = next((r for r in resources if _role(provider, r) == "cluster"), None)
    registry = next((r for r in resources if _role(provider, r) == "registry"), None)
    access = {"azure": 'resource "azurerm_role_assignment" "aks_acr_pull"', "aws": 'resource "aws_iam_role_policy_attachment" "eks_ecr_pull"', "gcp": 'resource "google_artifact_registry_repository_iam_member" "gke_artifact_pull"'}
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
    if not intent:
        warnings.append("Deployment intent is absent; workload-to-infrastructure validation was skipped")
    return {"status": "FAILED" if errors else ("SUCCEEDED_WITH_WARNINGS" if warnings else "SUCCEEDED"), "errors": errors, "warnings": warnings}


def validate_resource_spec(provider: str, resources: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    identities: set[tuple[str, str]] = set()
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
    if errors:
        raise ValueError("Invalid cloud resource specification:\n- " + "\n- ".join(errors))


def _variables(provider: str, resources: list[dict[str, Any]]) -> str:
    provider_blocks = {
        "azure": ['terraform { required_version = ">= 1.6.0"\n  required_providers { azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" } }\n}', 'provider "azurerm" { features {} }', 'variable "resource_group_name" { type = string }', 'variable "location" { type = string }', 'variable "mysql_administrator_login" { type = string\n  default = "easydepadmin" }', 'variable "mysql_administrator_password" { type = string\n  sensitive = true }'],
        "aws": ['terraform { required_version = ">= 1.6.0"\n  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }\n}', 'provider "aws" { region = var.region }', 'variable "region" { type = string }', 'variable "eks_cluster_role_arn" { type = string }', 'variable "eks_node_role_arn" { type = string }', 'variable "eks_node_role_name" { type = string }', 'variable "subnet_ids" { type = list(string)\n  default = [] }', 'variable "existing_vpc_id" { type = string\n  default = null }', 'variable "availability_zones" { type = list(string)\n  default = [] }', 'variable "db_username" { type = string\n  default = "easydepadmin" }', 'variable "db_password" { type = string\n  sensitive = true }'],
        "gcp": ['terraform { required_version = ">= 1.6.0"\n  required_providers { google = { source = "hashicorp/google", version = "~> 6.0" } }\n}', 'provider "google" { project = var.project_id\n  region = var.region }', 'variable "project_id" { type = string }', 'variable "region" { type = string }', 'variable "network_name" { type = string\n  default = null }', 'variable "gke_node_service_account" { type = string }'],
    }[provider]
    for item in resources:
        raw, logical = str(item.get("name", "")), _logical(item)
        if raw.startswith("<") and raw.endswith(">"):
            provider_blocks.append(f'variable "{logical}_name" {{ type = string }}')
    return "\n\n".join(provider_blocks)


def _main(provider: str, resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    builders = {"azure": _azure, "aws": _aws, "gcp": _gcp}
    return builders[provider](resources, names)


def _azure(resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    blocks: list[str] = []
    cluster = registry = None
    vnet = next((_logical(item) for item in resources if _type("azure", item) == "azurerm_virtual_network"), None)
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
                    delegation = f'\n  delegation {{ name = "delegation"\n    service_delegation {{ name = {json.dumps(service)}\n      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"] }}\n  }}'
                blocks.append(f'resource "azurerm_subnet" "{logical}_{subnet_id}" {{\n  name = {json.dumps(subnet_name)}\n  resource_group_name = var.resource_group_name\n  virtual_network_name = azurerm_virtual_network.{logical}.name\n  address_prefixes = [{json.dumps(str(subnet.get("addressPrefix", "10.0.1.0/24")))}]{delegation}\n}}')
        elif kind == "azurerm_container_registry":
            registry = logical; blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  resource_group_name = var.resource_group_name\n  location = var.location\n  sku = "Basic"\n  admin_enabled = false\n}}')
        elif kind == "azurerm_kubernetes_cluster":
            cluster = logical
            pool = next(iter(item.get("nodePools", [])), {})
            networking = item.get("networking", {})
            subnet_name = str(networking.get("subnet", "")).rsplit("/", 1)[-1]
            subnet_id = f'\n    vnet_subnet_id = azurerm_subnet.{vnet}_{_tf_id(subnet_name)}.id' if vnet and subnet_name else ""
            scaling = bool(pool.get("enableAutoScaling", False))
            autoscaling = f'\n    min_count = {int(pool.get("minCount", 1))}\n    max_count = {int(pool.get("maxCount", 3))}' if scaling else ""
            blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  location = var.location\n  resource_group_name = var.resource_group_name\n  dns_prefix = replace({name}, "-", "")\n  private_cluster_enabled = {str(bool(networking.get("privateCluster", False))).lower()}\n  default_node_pool {{\n    name = {json.dumps(str(pool.get("name", "system")))}\n    vm_size = {json.dumps(str(pool.get("vmSize", "Standard_B2s")))}\n    node_count = {int(pool.get("count", 1))}\n    enable_auto_scaling = {str(scaling).lower()}{autoscaling}{subnet_id}\n  }}\n  identity {{ type = "SystemAssigned" }}\n}}')
        elif kind == "azurerm_mysql_flexible_server":
            networking = item.get("networking", {})
            delegated = str(networking.get("delegatedSubnet", "")).rsplit("/", 1)[-1]
            dns = dns_zones.get(str(networking.get("privateDnsZone", "")))
            private_lines = ""
            if vnet and delegated:
                private_lines += f'\n  delegated_subnet_id = azurerm_subnet.{vnet}_{_tf_id(delegated)}.id'
            if dns:
                private_lines += f'\n  private_dns_zone_id = azurerm_private_dns_zone.{dns}.id\n  depends_on = [azurerm_private_dns_zone_virtual_network_link.{dns}_link]'
            public_access = str(networking.get("publicNetworkAccess", "Enabled")).lower() != "disabled"
            blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  resource_group_name = var.resource_group_name\n  location = var.location\n  administrator_login = var.mysql_administrator_login\n  administrator_password = var.mysql_administrator_password\n  sku_name = {json.dumps(str(item.get("sku", "Standard_B2s")))}\n  version = {json.dumps(str(item.get("version", "8.0")))}\n  backup_retention_days = {int(item.get("backupRetentionDays", 7))}\n  public_network_access_enabled = {str(public_access).lower()}{private_lines}\n  storage {{ size_gb = {int(item.get("storageGb", 32))} }}\n}}')
            for database in item.get("databases", []):
                database_id = _tf_id(str(database))
                blocks.append(f'resource "azurerm_mysql_flexible_database" "{logical}_{database_id}" {{\n  name = {json.dumps(str(database))}\n  server_name = azurerm_mysql_flexible_server.{logical}.name\n  resource_group_name = var.resource_group_name\n  charset = "utf8mb4"\n  collation = "utf8mb4_unicode_ci"\n}}')
        elif kind == "azurerm_key_vault":
            blocks.append(f'data "azurerm_client_config" "current" {{}}\n\nresource "{kind}" "{logical}" {{\n  name = {name}\n  location = var.location\n  resource_group_name = var.resource_group_name\n  tenant_id = data.azurerm_client_config.current.tenant_id\n  sku_name = "standard"\n}}')
        elif kind == "azurerm_log_analytics_workspace": blocks.append(f'resource "{kind}" "{logical}" {{\n  name = {name}\n  location = var.location\n  resource_group_name = var.resource_group_name\n  sku = "PerGB2018"\n}}')
        elif str(item.get("type")) == "Microsoft.Network/privateDnsZones": blocks.append(f'resource "azurerm_private_dns_zone" "{logical}" {{\n  name = {name}\n  resource_group_name = var.resource_group_name\n}}')
    if vnet:
        for _, dns in dns_zones.items():
            blocks.append(f'resource "azurerm_private_dns_zone_virtual_network_link" "{dns}_link" {{\n  name = "{dns}-vnet-link"\n  resource_group_name = var.resource_group_name\n  private_dns_zone_name = azurerm_private_dns_zone.{dns}.name\n  virtual_network_id = azurerm_virtual_network.{vnet}.id\n}}')
    if cluster and registry: blocks.append(f'resource "azurerm_role_assignment" "aks_acr_pull" {{\n  scope = azurerm_container_registry.{registry}.id\n  role_definition_name = "AcrPull"\n  principal_id = azurerm_kubernetes_cluster.{cluster}.kubelet_identity[0].object_id\n}}')
    return "\n\n".join(blocks)


def _aws(resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    blocks: list[str] = []; cluster = registry = None
    vpc = next((_logical(item) for item in resources if _type("aws", item) == "aws_vpc"), None)
    subnets = [_logical(item) for item in resources if _type("aws", item) == "aws_subnet"]
    for item in resources:
        logical, kind, name = _logical(item), _type("aws", item), names[_logical(item)]
        if kind == "aws_vpc":
            blocks.append(f'resource "aws_vpc" "{logical}" {{\n  cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.0.0/16")))}\n  enable_dns_hostnames = true\n  enable_dns_support = true\n  tags = {{ Name = {name} }}\n}}')
        elif kind == "aws_subnet":
            zone = item.get("availabilityZone")
            zone_expr = json.dumps(str(zone)) if zone else f'try(var.availability_zones[{subnets.index(logical)}], null)'
            vpc_expr = f'aws_vpc.{vpc}.id' if vpc else 'var.existing_vpc_id'
            blocks.append(f'resource "aws_subnet" "{logical}" {{\n  vpc_id = {vpc_expr}\n  cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.1.0/24")))}\n  availability_zone = {zone_expr}\n  tags = {{ Name = {name} }}\n}}')
        elif kind == "aws_ecr_repository": registry = logical; blocks.append(f'resource "aws_ecr_repository" "{logical}" {{\n  name = {name}\n}}')
        elif kind == "aws_eks_cluster":
            cluster = logical
            subnet_ids = "[" + ", ".join(f"aws_subnet.{item}.id" for item in subnets) + "]" if subnets else "var.subnet_ids"
            blocks.append(f'resource "aws_eks_cluster" "{logical}" {{\n  name = {name}\n  role_arn = var.eks_cluster_role_arn\n  vpc_config {{ subnet_ids = {subnet_ids} }}\n}}')
        elif kind == "aws_db_instance": blocks.append(f'resource "aws_db_instance" "{logical}" {{\n  identifier = {name}\n  engine = {json.dumps(str(item.get("engine", "mysql")))}\n  instance_class = {json.dumps(str(item.get("instanceClass", "db.t3.micro")))}\n  allocated_storage = {int(item.get("allocatedStorage", 20))}\n  username = var.db_username\n  password = var.db_password\n  skip_final_snapshot = true\n}}')
        elif kind == "aws_secretsmanager_secret": blocks.append(f'resource "aws_secretsmanager_secret" "{logical}" {{\n  name = {name}\n}}')
        elif kind == "aws_cloudwatch_log_group": blocks.append(f'resource "aws_cloudwatch_log_group" "{logical}" {{\n  name = {name}\n  retention_in_days = 30\n}}')
    if cluster:
        subnet_ids = "[" + ", ".join(f"aws_subnet.{item}.id" for item in subnets) + "]" if subnets else "var.subnet_ids"
        blocks.append(f'resource "aws_eks_node_group" "{cluster}_default" {{\n  cluster_name = aws_eks_cluster.{cluster}.name\n  node_group_name = "default"\n  node_role_arn = var.eks_node_role_arn\n  subnet_ids = {subnet_ids}\n  scaling_config {{ desired_size = 1 min_size = 1 max_size = 3 }}\n}}')
    if cluster and registry: blocks.append('resource "aws_iam_role_policy_attachment" "eks_ecr_pull" {\n  role = var.eks_node_role_name\n  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"\n}')
    return "\n\n".join(blocks)


def _gcp(resources: list[dict[str, Any]], names: dict[str, str]) -> str:
    blocks: list[str] = []; cluster = registry = None
    network = next((_logical(item) for item in resources if _type("gcp", item) == "google_compute_network"), None)
    subnet = next((_logical(item) for item in resources if _type("gcp", item) == "google_compute_subnetwork"), None)
    for item in resources:
        logical, kind, name = _logical(item), _type("gcp", item), names[_logical(item)]
        if kind == "google_compute_network":
            blocks.append(f'resource "google_compute_network" "{logical}" {{\n  name = {name}\n  auto_create_subnetworks = false\n}}')
        elif kind == "google_compute_subnetwork":
            network_expr = f'google_compute_network.{network}.id' if network else 'var.network_name'
            blocks.append(f'resource "google_compute_subnetwork" "{logical}" {{\n  name = {name}\n  ip_cidr_range = {json.dumps(str(item.get("ipCidrRange", "10.0.1.0/24")))}\n  region = var.region\n  network = {network_expr}\n}}')
        elif kind == "google_artifact_registry_repository": registry = logical; blocks.append(f'resource "google_artifact_registry_repository" "{logical}" {{\n  location = var.region\n  repository_id = {name}\n  format = "DOCKER"\n}}')
        elif kind == "google_container_cluster":
            cluster = logical
            network_expr = f'google_compute_network.{network}.id' if network else 'null'
            subnet_expr = f'google_compute_subnetwork.{subnet}.id' if subnet else 'null'
            blocks.append(f'resource "google_container_cluster" "{logical}" {{\n  name = {name}\n  location = var.region\n  network = {network_expr}\n  subnetwork = {subnet_expr}\n  remove_default_node_pool = true\n  initial_node_count = 1\n}}')
        elif kind == "google_sql_database_instance": blocks.append(f'resource "google_sql_database_instance" "{logical}" {{\n  name = {name}\n  database_version = {json.dumps(str(item.get("databaseVersion", "MYSQL_8_0")))}\n  region = var.region\n  settings {{ tier = {json.dumps(str(item.get("tier", "db-f1-micro")))} }}\n}}')
        elif kind == "google_secret_manager_secret": blocks.append(f'resource "google_secret_manager_secret" "{logical}" {{\n  secret_id = {name}\n  replication {{ auto {{}} }}\n}}')
        elif kind == "google_logging_project_bucket_config": blocks.append(f'resource "google_logging_project_bucket_config" "{logical}" {{\n  location = "global"\n  bucket_id = {name}\n  retention_days = 30\n}}')
    if cluster: blocks.append(f'resource "google_container_node_pool" "{cluster}_default" {{\n  name = "default"\n  location = google_container_cluster.{cluster}.location\n  cluster = google_container_cluster.{cluster}.name\n  node_count = 1\n  node_config {{ service_account = var.gke_node_service_account\n    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"] }}\n}}')
    if cluster and registry: blocks.append(f'resource "google_artifact_registry_repository_iam_member" "gke_artifact_pull" {{\n  location = google_artifact_registry_repository.{registry}.location\n  repository = google_artifact_registry_repository.{registry}.name\n  role = "roles/artifactregistry.reader"\n  member = "serviceAccount:${{var.gke_node_service_account}}"\n}}')
    return "\n\n".join(blocks)


def _outputs(provider: str, resources: list[dict[str, Any]]) -> str:
    values = [f'output "provider" {{ value = {json.dumps(provider)} }}']
    for item in resources:
        kind, logical = _type(provider, item), _logical(item)
        if _role(provider, item) == "cluster": values.append(f'output "{logical}_cluster_name" {{ value = {kind}.{logical}.name }}')
    return "\n\n".join(values)


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
        return [f'sku_name = {json.dumps(str(item.get("sku", "Standard_B2s")))}', f'version = {json.dumps(str(item.get("version", "8.0")))}', f'size_gb = {int(item.get("storageGb", 32))}']
    if kind == "aws_vpc": return [f'cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.0.0/16")))}']
    if kind == "aws_subnet": return [f'cidr_block = {json.dumps(str(item.get("cidrBlock", "10.0.1.0/24")))}']
    if kind == "aws_db_instance": return [f'engine = {json.dumps(str(item.get("engine", "mysql")))}', f'instance_class = {json.dumps(str(item.get("instanceClass", "db.t3.micro")))}', f'allocated_storage = {int(item.get("allocatedStorage", 20))}']
    if kind == "google_compute_subnetwork": return [f'ip_cidr_range = {json.dumps(str(item.get("ipCidrRange", "10.0.1.0/24")))}']
    if kind == "google_sql_database_instance": return [f'database_version = {json.dumps(str(item.get("databaseVersion", "MYSQL_8_0")))}', f'tier = {json.dumps(str(item.get("tier", "db-f1-micro")))}']
    return []


def _names(resources: list[dict[str, Any]]) -> dict[str, str]:
    return {_logical(item): f"var.{_logical(item)}_name" if str(item.get("name", "")).startswith("<") else json.dumps(str(item.get("name", _logical(item)))) for item in resources}


def _logical(item: dict[str, Any]) -> str:
    return _tf_id(str(item.get("name") or str(item.get("type", "resource")).split("/")[-1]))


def _tf_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_").lower()
    return value if value and not value[0].isdigit() else f"resource_{value or 'item'}"
