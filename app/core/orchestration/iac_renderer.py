"""Deterministic OpenTofu rendering for ResourcePlan.

The renderer is intentionally boring: it has no inference and no LLM boundary.
Every provider resource block is emitted from a typed ResourcePlan node, while
the provider-specific body only realizes the node's already-selected primitive.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.cloudkb.depkb.provider_cache import PINNED_PROVIDERS

RESOURCE_PLAN_SCHEMA = "easydep-resource-plan"


def _label(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]", "_", str(value or "resource"))
    if text[:1].isdigit():
        text = f"r_{text}"
    return text or "resource"


def _cloud_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9-]", "-", str(value or "resource").lower()).strip("-")


def _quoted(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _attrs(node: dict[str, Any]) -> dict[str, Any]:
    return dict(node.get("attributes") or {})


def _block(kind: str, label: str, body: str) -> str:
    body = body.replace("; ", "\n").replace("{ ", "{\n").replace(" }", "\n}")
    indented = "\n".join(f"  {line}" if line else "" for line in body.splitlines())
    return f'resource "{kind}" "{label}" {{\n{indented}\n}}\n'


def _expression_map(values: dict[str, str]) -> str:
    return "{ " + ", ".join(f"{key} = {value}" for key, value in values.items()) + " }"


def _bootstrap_expression(
    node_id: str, vars_by_compute: dict[str, dict[str, str]]
) -> tuple[str, str]:
    compute_id = node_id.removeprefix("compute-template-")
    return (
        f"bootstrap_{_label(compute_id)}.sh.tftpl",
        _expression_map(vars_by_compute.get(compute_id, {})),
    )


def _registry_expression(
    provider: str,
    plan: dict[str, Any],
    context: _Context,
    registry_ref: str,
    workload_label: str,
) -> str:
    return {
        "aws": f"{context.address(registry_ref)}.repository_url",
        "azure": (
            f'format("%s/{workload_label}", '
            f"{context.address(registry_ref)}.login_server)"
        ),
        "gcp": (
            f'"{plan.get("region")}-docker.pkg.dev/${{var.project_id}}/'
            f'${{{context.address(registry_ref)}.repository_id}}/{workload_label}"'
        ),
    }[provider]


@dataclass
class _Context:
    plan: dict[str, Any]

    def __post_init__(self) -> None:
        self.nodes = {
            str(item.get("id") or ""): item for item in self.plan.get("nodes") or []
        }
        self.addresses: dict[str, str] = {}
        for node_id, node in self.nodes.items():
            types = list(node.get("terraformTypes") or [])
            if node.get("handling") == "create" and types:
                self.addresses[node_id] = f"{types[0]}.{_label(node_id)}"
        self.references = list(self.plan.get("references") or [])
        self.consumed_reference_ids: set[str] = set()
        self.shared_values = {
            str(item.get("id") or ""): item
            for item in self.plan.get("sharedValues") or []
        }
        self.embedded_blocks = {
            str(item.get("id") or ""): item
            for item in self.plan.get("embeddedBlocks") or []
        }
        self.binding_slots = {
            str(item.get("id") or ""): item
            for item in self.plan.get("bindingSlots") or []
        }

    def address(self, node_id: str) -> str:
        try:
            return self.addresses[node_id]
        except KeyError as error:
            raise ValueError(f"No Terraform address for ResourcePlan node: {node_id}") from error

    def target(self, node_id: str, path: str) -> str | None:
        reference = next(
            (
                item
                for item in self.references
                if item.get("consumerRef") == node_id
                and item.get("consumerPath") == path
            ),
            None,
        )
        if reference is None:
            return None
        self.consumed_reference_ids.add(str(reference.get("id") or ""))
        return str(reference.get("producerRef") or "")

    def targets(self, node_id: str, path: str) -> list[str]:
        matches = [
            reference
            for reference in self.references
            if reference.get("consumerRef") == node_id
            and reference.get("consumerPath") == path
        ]
        self.consumed_reference_ids.update(str(item.get("id") or "") for item in matches)
        return [str(reference.get("producerRef") or "") for reference in matches]

    def ref(self, node_id: str, attribute: str = "id") -> str:
        return f"{self.address(node_id)}.{attribute}"

    def producer_expression(self, producer_ref: str, attribute: str) -> str:
        if producer_ref in self.shared_values:
            return f"local.{_label(producer_ref)}"
        if producer_ref in self.binding_slots:
            return f"var.{_label(producer_ref)}"
        if producer_ref == "boot-image":
            provider = str(self.plan.get("provider") or "")
            return {
                "aws": "var.boot_image_id",
                "azure": '"Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest"',
                "gcp": '"projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"',
            }[provider]
        return self.ref(producer_ref, attribute)

    def dependency_ref(self, node_id: str, path: str, *, required: bool = True) -> str:
        reference = next(
            (
                reference
                for reference in self.references
                if reference.get("consumerRef") == node_id
                and reference.get("consumerPath") == path
            ),
            None,
        )
        if not reference:
            if required:
                raise ValueError(f"ResourcePlan node {node_id} has no {path} dependency")
            return ""
        target = str(reference.get("producerRef") or "")
        attribute = str(reference.get("producerAttribute") or "id")
        self.consumed_reference_ids.add(str(reference.get("id") or ""))
        return self.producer_expression(target, attribute)

    def dependency_refs(self, node_id: str, path: str) -> list[str]:
        matches = [
            reference
            for reference in self.references
            if reference.get("consumerRef") == node_id
            and reference.get("consumerPath") == path
        ]
        self.consumed_reference_ids.update(str(item.get("id") or "") for item in matches)
        return [
            self.producer_expression(
                str(reference.get("producerRef") or ""),
                str(reference.get("producerAttribute") or "id"),
            )
            for reference in matches
        ]


def _locals_file(plan: dict[str, Any]) -> str:
    values = list(plan.get("sharedValues") or [])
    if not values:
        return ""
    assignments = "\n".join(
        f"  {_label(item.get('id'))} = {json.dumps(item.get('value'), ensure_ascii=False)}"
        for item in values
    )
    return f"locals {{\n{assignments}\n}}\n"


def _provider_file(provider: str, region: str) -> str:
    contract = PINNED_PROVIDERS[provider]
    local_name = {"azure": "azurerm", "gcp": "google"}.get(provider, provider)
    configuration = {
        "aws": (
            'provider "aws" {\n'
            f"  region                      = {_quoted(region)}\n"
            "  skip_credentials_validation = true\n"
            "  skip_requesting_account_id  = true\n"
            "  skip_metadata_api_check     = true\n"
            "  skip_region_validation      = true\n"
            "}"
        ),
        "azure": (
            'provider "azurerm" {\n'
            "  features {}\n"
            "  subscription_id = var.subscription_id\n"
            "}"
        ),
        "gcp": (
            'provider "google" {\n'
            "  project = var.project_id\n"
            f"  region  = {_quoted(region)}\n"
            "}"
        ),
    }[provider]
    return (
        "terraform {\n"
        '  required_version = ">= 1.8.0"\n'
        "  required_providers {\n"
        f"    {local_name} = {{\n"
        f"      source  = {_quoted(contract['source'])}\n"
        f"      version = {_quoted('= ' + contract['version'])}\n"
        "    }\n"
        "  }\n"
        "}\n\n"
        f"{configuration}\n"
    )


def _variable_file(plan: dict[str, Any]) -> str:
    provider = str(plan.get("provider") or "")
    lines = [
        'variable "resource_prefix" {',
        "  type    = string",
        '  default = "easydep"',
        "}",
        "",
        'variable "vm_sku" {',
        "  type    = string",
        f'  default = "{ {"aws": "t3.small", "azure": "Standard_B2s", "gcp": "e2-small"}[provider] }"',
        "}",
    ]
    if provider == "azure":
        lines.extend(
            [
                "",
                'variable "subscription_id" {',
                "  type = string",
                "}",
                "",
                'variable "ssh_public_key" {',
                "  type      = string",
                "  sensitive = true",
                "}",
            ]
        )
    elif provider == "gcp":
        lines.extend(["", 'variable "project_id" {', "  type = string", "}"])
    else:
        lines.extend(
            [
                "",
                'variable "ssh_public_key" {',
                "  type      = string",
                "  sensitive = true",
                '  default   = ""',
                "}",
                "",
                'variable "boot_image_id" {',
                "  type = string",
                "  validation {",
                '    condition     = startswith(var.boot_image_id, "ami-")',
                '    error_message = "boot_image_id must be an explicit AMI id."',
                "  }",
                "}",
            ]
        )
    for workload in plan.get("workloads") or []:
        workload_id = _label(workload.get("id"))
        artifact = workload.get("artifact") or {}
        if artifact.get("kind") == "generatedApplication":
            lines.extend(
                [
                    "",
                    f'variable "image_digest_{workload_id}" {{',
                    "  type = string",
                    "  validation {",
                    f"    condition     = startswith(var.image_digest_{workload_id}, \"sha256:\")",
                    f'    error_message = "image_digest_{workload_id} must be an immutable sha256 digest."',
                    "  }",
                    "}",
                ]
            )
        for interface in workload.get("interfaces") or []:
            if isinstance(interface.get("port"), int):
                continue
            interface_id = _label(interface.get("id"))
            lines.extend(
                [
                    "",
                    f'variable "container_port_{workload_id}_{interface_id}" {{',
                    "  type = number",
                    "}",
                ]
            )
    secret_slots = [
        item for item in plan.get("bindingSlots") or [] if item.get("kind") == "secretReference"
    ]
    for item in secret_slots:
        lines.extend(
            [
                "",
                f'variable "{_label(item.get("id"))}" {{',
                "  type      = string",
                "  sensitive = true",
                "}",
            ]
        )
    external_endpoint_slots = [
        item
        for item in plan.get("bindingSlots") or []
        if item.get("kind") == "externalEndpoint"
    ]
    for item in external_endpoint_slots:
        lines.extend(
            [
                "",
                f'variable "{_label(item.get("id"))}" {{',
                "  type = string",
                "}",
            ]
        )
    return "\n".join(lines) + "\n"


def _storage_setup_lines(
    plan: dict[str, Any],
    context: _Context,
    *,
    provider: str,
    compute_id: str,
    template_vars: dict[str, str],
) -> list[str]:
    """Prepare only the block devices owned by one compute unit.

    Attachments are created after a standalone VM, so bootstrap deliberately
    waits for the stable provider device path.  Formatting is guarded by
    ``blkid`` and the UUID mount is persisted in fstab.
    """

    bindings = [
        item
        for item in plan.get("storageBindings") or []
        if item.get("computeUnitRef") == compute_id
    ]
    lines: list[str] = []
    for fallback_index, binding in enumerate(bindings):
        storage_id = str(binding.get("storageRef") or f"storage-{fallback_index}")
        storage_label = _label(storage_id)
        disk_node = context.nodes.get(f"data-disk-{storage_id}") or {}
        attributes = _attrs(disk_node)
        index = int(attributes.get("attachmentIndex", fallback_index))
        conventional_device = chr(ord("f") + index)
        if provider == "aws" and disk_node.get("handling") == "create":
            disk_key = f"disk_id_{storage_label}"
            template_vars[disk_key] = context.ref(f"data-disk-{storage_id}")
            lines.extend(
                [
                    f'EXPECTED_VOLUME="${{{disk_key}}}"',
                    'EXPECTED_VOLUME_NO_DASH=$(printf "%s" "$EXPECTED_VOLUME" | tr -d "-")',
                    'DISK_DEVICE=""',
                    'for ATTEMPT in $(seq 1 60); do',
                    '  for CANDIDATE in /dev/nvme*n1; do',
                    '    [ -b "$CANDIDATE" ] || continue',
                    '    if command -v ebsnvme-id >/dev/null 2>&1 && ebsnvme-id "$CANDIDATE" 2>/dev/null | tr -d "-" | grep -q "$EXPECTED_VOLUME_NO_DASH"; then DISK_DEVICE="$CANDIDATE"; break; fi',
                    '  done',
                    f'  [ -n "$DISK_DEVICE" ] || [ ! -b "/dev/xvd{conventional_device}" ] || DISK_DEVICE="/dev/xvd{conventional_device}"',
                    f'  [ -n "$DISK_DEVICE" ] || [ ! -b "/dev/sd{conventional_device}" ] || DISK_DEVICE="/dev/sd{conventional_device}"',
                    '  [ -n "$DISK_DEVICE" ] && break',
                    '  sleep 2',
                    'done',
                ]
            )
        else:
            expected = {
                "aws": f"/dev/xvd{conventional_device}",
                "azure": f"/dev/disk/azure/scsi1/lun{10 + index}",
                "gcp": f"/dev/disk/by-id/google-easydep-{_cloud_label(storage_id)}",
            }[provider]
            lines.extend(
                [
                    'DISK_DEVICE=""',
                    'for ATTEMPT in $(seq 1 60); do',
                    f'  [ ! -b {_quoted(expected)} ] || DISK_DEVICE={_quoted(expected)}',
                    '  [ -n "$DISK_DEVICE" ] && break',
                    '  sleep 2',
                    'done',
                ]
            )
        guest_path = f"/mnt/easydep/{storage_label}/data"
        lines.extend(
            [
                f'[ -n "$DISK_DEVICE" ] || {{ echo "disk {storage_id} did not attach" >&2; exit 1; }}',
                'if ! blkid "$DISK_DEVICE" >/dev/null 2>&1; then mkfs.ext4 "$DISK_DEVICE"; fi',
                'DISK_UUID=$(blkid -s UUID -o value "$DISK_DEVICE")',
                f"mkdir -p {_quoted(guest_path)}",
                f'grep -q "UUID=$DISK_UUID {_quoted(guest_path)} " /etc/fstab || printf "UUID=%s {guest_path} ext4 defaults,nofail 0 2\\n" "$DISK_UUID" >> /etc/fstab',
                f"mountpoint -q {_quoted(guest_path)} || mount {_quoted(guest_path)}",
                f"chmod 0750 {_quoted(guest_path)}",
            ]
        )
    return lines


def _runtime_files(
    plan: dict[str, Any], context: _Context
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    provider = str(plan.get("provider") or "")
    files: dict[str, str] = {}
    vars_by_compute: dict[str, dict[str, str]] = {}
    for unit in plan.get("runtimeUnits") or []:
        compute_id = str(unit.get("computeUnitRef") or "")
        template_vars: dict[str, str] = {}
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "if command -v dnf >/dev/null 2>&1; then",
            "  dnf install -y docker awscli nvme-cli",
            "else",
            "  apt-get update",
            "  apt-get install -y docker.io curl jq",
            "fi",
            "systemctl enable --now docker",
        ]
        lines.extend(
            _storage_setup_lines(
                plan,
                context,
                provider=provider,
                compute_id=compute_id,
                template_vars=template_vars,
            )
        )
        network = str(unit.get("containerNetwork") or "easydep")
        lines.append(
            f"docker network inspect {_quoted(network)} >/dev/null 2>&1 || docker network create {_quoted(network)}"
        )
        authenticated_registries: set[str] = set()
        azure_identity_ready = False
        for container in unit.get("containers") or []:
            workload_id = str(container.get("workloadRef") or "")
            workload_label = _label(workload_id)
            registry_ref = str(container.get("registryRef") or "")
            if registry_ref:
                registry_key = f"registry_{workload_label}"
                digest_key = f"image_digest_{workload_label}"
                template_vars[registry_key] = _registry_expression(
                    provider, plan, context, registry_ref, workload_label
                )
                template_vars[digest_key] = f"var.image_digest_{workload_label}"
                image = f"${{{registry_key}}}@${{{digest_key}}}"
                if registry_ref not in authenticated_registries:
                    authenticated_registries.add(registry_ref)
                    if provider == "aws":
                        lines.append(
                            f'aws ecr get-login-password --region {_quoted(plan.get("region"))} | docker login --username AWS --password-stdin "$(printf %s \"${{{registry_key}}}\" | cut -d/ -f1)"'
                        )
                    elif provider == "azure":
                        lines.extend(
                            [
                                "command -v az >/dev/null 2>&1 || curl -sL https://aka.ms/InstallAzureCLIDeb | bash",
                                "az login --identity --allow-no-subscriptions >/dev/null",
                                f'az acr login --name "$(printf %s \"${{{registry_key}}}\" | cut -d. -f1)"',
                            ]
                        )
                        azure_identity_ready = True
                    else:
                        lines.extend(
                            [
                                'REGISTRY_TOKEN=$(curl -fsS -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" | jq -r .access_token)',
                                f'printf %s "$REGISTRY_TOKEN" | docker login -u oauth2accesstoken --password-stdin "$(printf %s \"${{{registry_key}}}\" | cut -d/ -f1)"',
                            ]
                        )
            else:
                image = str(container.get("image") or "")
            port_args: list[str] = []
            for interface in container.get("interfaces") or []:
                port = interface.get("port")
                if isinstance(port, int):
                    rendered_port = str(port)
                else:
                    key = f"port_{workload_label}_{_label(interface.get('id'))}"
                    template_vars[key] = f"var.container_port_{workload_label}_{_label(interface.get('id'))}"
                    rendered_port = f"${{{key}}}"
                if interface.get("exposure") == "public":
                    port_args.append(f"-p {rendered_port}:{rendered_port}")
            mount_args = [
                f"-v /mnt/easydep/{_label(item.get('storageRef'))}/data:{item.get('mountPath')}"
                for item in container.get("mounts") or []
                if isinstance(item.get("mountPath"), str)
            ]
            env_args: list[str] = []
            endpoint_configuration_ids = {
                str(item.get("configurationRef") or "")
                for item in container.get("runtimeBindings") or []
                if item.get("kind") == "endpointEnvironment"
            }
            for binding in container.get("runtimeBindings") or []:
                if binding.get("kind") != "endpointEnvironment":
                    continue
                env_name = str(binding.get("environmentName") or "")
                host = binding.get("endpointHost")
                if binding.get("endpointProducerRef"):
                    endpoint_key = f"endpoint_{workload_label}_{_label(binding.get('configurationRef'))}"
                    owner = str(unit.get("bootstrapOwnerRef") or compute_id)
                    template_vars[endpoint_key] = context.dependency_ref(
                        owner, f"bootstrap.environment.{env_name}"
                    )
                    host = f"${{{endpoint_key}}}"
                elif binding.get("endpointValueBindingRef"):
                    endpoint_key = f"endpoint_{workload_label}_{_label(binding.get('configurationRef'))}"
                    template_vars[endpoint_key] = context.producer_expression(
                        str(binding.get("endpointValueBindingRef")), "value"
                    )
                    host = f"${{{endpoint_key}}}"
                    lines.append(f"export {env_name}={_quoted(host)}")
                    env_args.append(f"-e {env_name}")
                    continue
                port = binding.get("port")
                if not isinstance(port, int):
                    target_label = _label(binding.get("targetWorkloadRef"))
                    interface_label = _label(binding.get("targetInterfaceRef"))
                    port_key = f"endpoint_port_{workload_label}_{_label(binding.get('configurationRef'))}"
                    template_vars[port_key] = (
                        f"var.container_port_{target_label}_{interface_label}"
                    )
                    port = f"${{{port_key}}}"
                projection = str(binding.get("projection") or "url")
                protocol = str(binding.get("protocol") or "http")
                if projection == "port":
                    value = str(port)
                elif projection == "host":
                    value = str(host or "")
                else:
                    value = f"{protocol}://{host}:{port}"
                lines.append(f"export {env_name}={_quoted(value)}")
                env_args.append(f"-e {env_name}")
            for config in container.get("configuration") or []:
                config_id = str(config.get("id") or config.get("name") or "secret")
                if config_id in endpoint_configuration_ids:
                    continue
                env_name = str(config.get("name") or _label(config_id).upper())
                is_secret = config.get("sensitive") is True or str(
                    config.get("kind") or ""
                ) in {"secret", "secretBinding"}
                if is_secret:
                    secret_key = f"secret_ref_{workload_label}_{_label(config_id)}"
                    variable_name = _label(
                        f"secret-reference-{workload_id}-{config_id}"
                    )
                    template_vars[secret_key] = f"var.{variable_name}"
                    if provider == "aws":
                        lines.append(
                            f'export {env_name}="$(aws secretsmanager get-secret-value --region {_quoted(plan.get("region"))} --secret-id \"${{{secret_key}}}\" --query SecretString --output text)"'
                        )
                    elif provider == "azure":
                        if not azure_identity_ready:
                            lines.extend(
                                [
                                    "command -v az >/dev/null 2>&1 || curl -sL https://aka.ms/InstallAzureCLIDeb | bash",
                                    "az login --identity --allow-no-subscriptions >/dev/null",
                                ]
                            )
                            azure_identity_ready = True
                        lines.append(
                            f'export {env_name}="$(az keyvault secret show --id \"${{{secret_key}}}\" --query value -o tsv)"'
                        )
                    else:
                        project_key = f"project_id_{workload_label}_{_label(config_id)}"
                        template_vars[project_key] = "var.project_id"
                        lines.extend(
                            [
                                f'SECRET_RESOURCE="${{{secret_key}}}"',
                                f'case "$SECRET_RESOURCE" in projects/*) ;; *) SECRET_RESOURCE="projects/${{{project_key}}}/secrets/$SECRET_RESOURCE" ;; esac',
                                'SECRET_TOKEN=$(curl -fsS -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" | jq -r .access_token)',
                                f'export {env_name}="$(curl -fsS -H \"Authorization: Bearer $SECRET_TOKEN\" \"https://secretmanager.googleapis.com/v1/$SECRET_RESOURCE/versions/latest:access\" | jq -r .payload.data | tr \"_-\" \"/+\" | base64 -d)"',
                            ]
                        )
                elif config.get("value") is not None:
                    lines.append(f"export {env_name}={_quoted(config.get('value'))}")
                env_args.append(f"-e {env_name}")
            lines.extend(
                [
                    f"docker rm -f {_quoted(workload_id)} >/dev/null 2>&1 || true",
                    " ".join(
                        [
                            "docker run -d --restart unless-stopped",
                            f"--name {_quoted(workload_id)}",
                            f"--network {_quoted(network)}",
                            *port_args,
                            *mount_args,
                            *env_args,
                            _quoted(image),
                        ]
                    ),
                ]
            )
        template_name = f"bootstrap_{_label(compute_id)}.sh.tftpl"
        files[template_name] = "\n".join(lines) + "\n"
        vars_by_compute[compute_id] = template_vars
    return files, vars_by_compute


def _aws_resources(
    plan: dict[str, Any],
    context: _Context,
    vars_by_compute: dict[str, dict[str, str]],
) -> str:
    blocks: list[str] = []
    for node_id, node in context.nodes.items():
        if node.get("handling") != "create":
            continue
        kind = (node.get("terraformTypes") or [""])[0]
        label = _label(node_id)
        attributes = _attrs(node)
        if kind == "aws_ecr_repository":
            body = f'name = "${{var.resource_prefix}}-{label}"\nimage_tag_mutability = "IMMUTABLE"\nforce_delete = false'
        elif kind == "aws_iam_role":
            body = (
                f'name = "${{var.resource_prefix}}-{label}"\n'
                'assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })'
            )
        elif kind == "aws_iam_instance_profile":
            body = f'name = "${{var.resource_prefix}}-{label}"\nrole = {context.dependency_ref(node_id, "role")}'
        elif kind == "aws_iam_role_policy_attachment":
            body = f'role = {context.dependency_ref(node_id, "role")}\npolicy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"'
        elif kind == "aws_iam_role_policy":
            body = (
                f'name = "${{var.resource_prefix}}-{label}"\n'
                f'role = {context.dependency_ref(node_id, "role")}\n'
                "policy = jsonencode({ Version = \"2012-10-17\", Statement = [{ "
                'Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], '
                f"Resource = {context.dependency_ref(node_id, 'scope')} }}] }})"
            )
        elif kind == "aws_vpc":
            body = 'cidr_block = "10.80.0.0/16"\nenable_dns_support = true\nenable_dns_hostnames = true'
        elif kind in {"aws_internet_gateway", "aws_route_table"}:
            body = f"vpc_id = {context.dependency_ref(node_id, 'vpc_id')}"
        elif kind == "aws_route":
            route_table = context.dependency_ref(node_id, "route_table_id")
            gateway = context.target(node_id, "gateway_id")
            nat = context.target(node_id, "nat_gateway_id")
            body = f'route_table_id = {route_table}\ndestination_cidr_block = "0.0.0.0/0"'
            if gateway:
                body += f"\ngateway_id = {context.ref(gateway)}"
            elif nat:
                body += f"\nnat_gateway_id = {context.ref(nat)}"
        elif kind == "aws_subnet":
            body = (
                f"vpc_id = {context.dependency_ref(node_id, 'vpc_id')}\n"
                f"cidr_block = {_quoted(attributes.get('cidr'))}\n"
                f"map_public_ip_on_launch = {str(bool(attributes.get('public'))).lower()}"
            )
            if attributes.get("zone"):
                body += f"\navailability_zone = {_quoted(attributes.get('zone'))}"
        elif kind == "aws_route_table_association":
            body = (
                f"subnet_id = {context.dependency_ref(node_id, 'subnet_id')}\n"
                f"route_table_id = {context.dependency_ref(node_id, 'route_table_id')}"
            )
        elif kind == "aws_eip":
            body = 'domain = "vpc"'
            if node_id.startswith("public-ip-"):
                body += f"\ninstance = {context.dependency_ref(node_id, 'instance')}"
        elif kind == "aws_nat_gateway":
            body = (
                f"allocation_id = {context.dependency_ref(node_id, 'allocation_id')}\n"
                f"subnet_id = {context.dependency_ref(node_id, 'subnet_id')}"
            )
        elif kind == "aws_security_group":
            public_paths = attributes.get("publicInterfaces") or []
            ingress = ""
            if public_paths:
                path = public_paths[0]
                port = path.get("port") if isinstance(path.get("port"), int) else 8080
                ingress = f'\ningress {{ from_port = {port}; to_port = {port}; protocol = "tcp"; cidr_blocks = ["0.0.0.0/0"] }}'
            source_groups = context.dependency_refs(node_id, "ingress.security_groups[]")
            if source_groups:
                ingress += (
                    '\ningress { from_port = 1; to_port = 65535; protocol = "tcp"; '
                    f"security_groups = [{', '.join(source_groups)}] }}"
                )
            body = f'name_prefix = "${{var.resource_prefix}}-{label}-"\nvpc_id = {context.dependency_ref(node_id, "vpc_id")}\negress {{ from_port = 0; to_port = 0; protocol = "-1"; cidr_blocks = ["0.0.0.0/0"] }}{ingress}'
        elif kind == "aws_instance":
            profile = context.target(node_id, "iam_instance_profile")
            bootstrap_file, bootstrap_vars = _bootstrap_expression(
                node_id, vars_by_compute
            )
            body = (
                f"ami = {context.dependency_ref(node_id, 'ami')}\n"
                "instance_type = var.vm_sku\n"
                f"subnet_id = {context.dependency_ref(node_id, 'subnet_id')}\n"
                f"vpc_security_group_ids = [{', '.join(context.dependency_refs(node_id, 'vpc_security_group_ids[]'))}]\n"
                f'user_data = templatefile("${{path.module}}/{bootstrap_file}", {bootstrap_vars})'
            )
            if attributes.get("privateIp"):
                body += f"\nprivate_ip = {_quoted(attributes.get('privateIp'))}"
            if profile:
                body += f"\niam_instance_profile = {context.ref(profile, 'name')}"
        elif kind == "aws_launch_template":
            profile = context.target(node_id, "iam_instance_profile")
            bootstrap_file, bootstrap_vars = _bootstrap_expression(
                node_id, vars_by_compute
            )
            body = (
                f"image_id = {context.dependency_ref(node_id, 'image_id')}\n"
                "instance_type = var.vm_sku\n"
                f"vpc_security_group_ids = [{', '.join(context.dependency_refs(node_id, 'vpc_security_group_ids[]'))}]\n"
                f'user_data = base64encode(templatefile("${{path.module}}/{bootstrap_file}", {bootstrap_vars}))'
            )
            for child_id, child in context.embedded_blocks.items():
                if child.get("ownerRef") != node_id or child.get("blockPath") != "block_device_mappings":
                    continue
                child_attrs = _attrs(child)
                index = int(child_attrs.get("attachmentIndex") or 0)
                device = chr(ord("f") + index)
                body += (
                    f'\nblock_device_mappings {{ device_name = "/dev/sd{device}"; '
                    f'ebs {{ volume_size = {int(child_attrs.get("capacityGiB") or 10)}; '
                    'volume_type = "gp3"; delete_on_termination = '
                    f'{str(child_attrs.get("deletionPolicy") != "retain").lower()} }} }}'
                )
            if profile:
                body += f"\niam_instance_profile {{ name = {context.ref(profile, 'name')} }}"
        elif kind == "aws_autoscaling_group":
            replica = int(attributes.get("replicaCount") or 1)
            subnets = context.dependency_refs(node_id, "vpc_zone_identifier[]")
            target_groups = context.targets(node_id, "target_group_arns[]")
            body = (
                f"min_size = {replica}\nmax_size = {replica}\ndesired_capacity = {replica}\n"
                f"vpc_zone_identifier = [{', '.join(subnets)}]\n"
                f"launch_template {{ id = {context.dependency_ref(node_id, 'launch_template.id')}; version = \"$Latest\" }}"
            )
            if target_groups:
                body += "\ntarget_group_arns = [" + ", ".join(context.ref(item, "arn") for item in target_groups) + "]"
        elif kind == "aws_lb":
            subnets = context.targets(node_id, "subnets[]")
            internal = str(attributes.get("scheme") or "public") == "internal"
            body = f'name = substr("${{var.resource_prefix}}-lb-${{substr(sha1({_quoted(node_id)}), 0, 8)}}", 0, 32)\ninternal = {str(internal).lower()}\nload_balancer_type = "network"\nsubnets = [{", ".join(context.ref(item) for item in subnets)}]'
        elif kind == "aws_lb_target_group":
            port = attributes.get("port") if isinstance(attributes.get("port"), int) else 8080
            health = next(
                (
                    block
                    for block in context.embedded_blocks.values()
                    if block.get("ownerRef") == node_id
                    and block.get("blockPath") == "health_check"
                ),
                {},
            )
            health_attrs = _attrs(health)
            health_path = health_attrs.get("path")
            path = "/health" if isinstance(health_path, dict) else str(health_path or "/health")
            body = f'name = substr("${{var.resource_prefix}}-tg-${{substr(sha1({_quoted(node_id)}), 0, 8)}}", 0, 32)\nport = {port}\nprotocol = "TCP"\nvpc_id = {context.dependency_ref(node_id, "vpc_id")}\nhealth_check {{ protocol = "HTTP"; path = {_quoted(path)}; port = "traffic-port" }}'
        elif kind == "aws_lb_listener":
            port = attributes.get("port") if isinstance(attributes.get("port"), int) else 8080
            body = f'load_balancer_arn = {context.dependency_ref(node_id, "load_balancer_arn")}\nport = {port}\nprotocol = "TCP"\ndefault_action {{ type = "forward"; target_group_arn = {context.dependency_ref(node_id, "default_action.target_group_arn")} }}'
        elif kind == "aws_ebs_volume":
            body = (
                "availability_zone = "
                f"{context.dependency_ref(node_id, 'availability_zone')}\n"
                f"size = {int(attributes.get('capacityGiB') or 10)}\n"
                'type = "gp3"'
            )
            if attributes.get("deletionPolicy") == "retain":
                body += "\nlifecycle { prevent_destroy = true }"
        elif kind == "aws_volume_attachment":
            index = int(attributes.get("attachmentIndex") or 0)
            device = chr(ord("f") + index)
            body = f'device_name = "/dev/sd{device}"\nvolume_id = {context.dependency_ref(node_id, "volume_id")}\ninstance_id = {context.dependency_ref(node_id, "instance_id")}'
        else:
            raise ValueError(f"Unsupported AWS ResourcePlan primitive: {node_id}/{kind}")
        blocks.append(_block(kind, label, body))
    return "\n".join(blocks)


def _azure_resources(
    plan: dict[str, Any],
    context: _Context,
    vars_by_compute: dict[str, dict[str, str]],
) -> str:
    blocks: list[str] = []
    region = str(plan.get("region") or "")
    for node_id, node in context.nodes.items():
        if node.get("handling") != "create":
            continue
        kind = (node.get("terraformTypes") or [""])[0]
        label = _label(node_id)
        cloud_label = _cloud_label(node_id)
        attributes = _attrs(node)
        rg_target = context.target(node_id, "resource_group_name")
        rg_name = context.ref(rg_target, "name") if rg_target else "azurerm_resource_group.resource_group.name"
        if kind == "azurerm_resource_group":
            body = f'name = "${{var.resource_prefix}}-rg"\nlocation = {_quoted(region)}'
        elif kind == "azurerm_container_registry":
            body = f'name = replace("${{var.resource_prefix}}{cloud_label}", "-", "")\nresource_group_name = {rg_name}\nlocation = {_quoted(region)}\nsku = "Basic"\nadmin_enabled = false'
        elif kind == "azurerm_user_assigned_identity":
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nresource_group_name = {rg_name}\nlocation = {_quoted(region)}'
        elif kind == "azurerm_role_assignment":
            scope_target = context.target(node_id, "scope")
            principal_target = context.target(node_id, "principal_id")
            if not principal_target:
                raise ValueError(f"Azure role assignment has no principal: {node_id}")
            scope = (
                context.ref(scope_target)
                if scope_target in context.addresses
                else f"var.{_label('secret-reference-' + node_id.removeprefix('secret-access-binding-'))}"
            )
            role = (
                "AcrPull"
                if str(node.get("providerPrimitiveKind") or "") == "registry-pull-binding"
                else "Key Vault Secrets User"
            )
            body = f"scope = {scope}\nrole_definition_name = {_quoted(role)}\nprincipal_id = {context.ref(principal_target, 'principal_id')}"
        elif kind == "azurerm_virtual_network":
            body = f'name = "${{var.resource_prefix}}-vnet"\naddress_space = ["10.80.0.0/16"]\nlocation = {_quoted(region)}\nresource_group_name = {rg_name}'
        elif kind == "azurerm_subnet":
            network_target = context.target(node_id, "virtual_network_name")
            body = (
                f'name = "${{var.resource_prefix}}-{cloud_label}"\n'
                f"resource_group_name = {context.ref('resource-group', 'name')}\n"
                f"virtual_network_name = {context.ref(network_target or 'network', 'name')}\n"
                f"address_prefixes = [{_quoted(attributes.get('cidr'))}]"
            )
        elif kind == "azurerm_public_ip":
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nlocation = {_quoted(region)}\nresource_group_name = {rg_name}\nallocation_method = "Static"\nsku = "Standard"'
        elif kind == "azurerm_nat_gateway":
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nlocation = {_quoted(region)}\nresource_group_name = {rg_name}\nsku_name = "Standard"'
        elif kind == "azurerm_nat_gateway_public_ip_association":
            body = f"nat_gateway_id = {context.dependency_ref(node_id, 'nat_gateway_id')}\npublic_ip_address_id = {context.dependency_ref(node_id, 'public_ip_address_id')}"
        elif kind == "azurerm_subnet_nat_gateway_association":
            body = f"subnet_id = {context.dependency_ref(node_id, 'subnet_id')}\nnat_gateway_id = {context.dependency_ref(node_id, 'nat_gateway_id')}"
        elif kind == "azurerm_network_security_group":
            public_paths = attributes.get("publicInterfaces") or []
            rule = ""
            if public_paths:
                port = public_paths[0].get("port")
                if not isinstance(port, int):
                    port = 8080
                rule = (
                    '\nsecurity_rule { name = "public-http"; priority = 100; direction = "Inbound"; access = "Allow"; protocol = "Tcp"; '
                    f'source_port_range = "*"; destination_port_range = "{port}"; source_address_prefix = "Internet"; destination_address_prefix = "*" }}'
                )
            for index, internal_rule in enumerate(attributes.get("internalRules") or [], start=1):
                connection_ref = str(internal_rule.get("connectionRef") or "internal")
                port = internal_rule.get("port")
                if not isinstance(port, int):
                    port = 8080
                source_prefix = context.dependency_ref(
                    node_id,
                    f"security_rule[{connection_ref}].source_address_prefix",
                )
                rule += (
                    f'\nsecurity_rule {{ name = "internal-{index}"; priority = {100 + index}; '
                    'direction = "Inbound"; access = "Allow"; protocol = "Tcp"; '
                    f'source_port_range = "*"; destination_port_range = "{port}"; '
                    f"source_address_prefix = {source_prefix}; destination_address_prefix = \"*\" }}"
                )
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nlocation = {_quoted(region)}\nresource_group_name = {rg_name}{rule}'
        elif kind == "azurerm_network_interface":
            subnet_target = context.target(node_id, "subnet_id")
            public_target = context.target(node_id, "public_ip_address_id")
            subnet_ref = context.ref(subnet_target or "")
            allocation = attributes.get("privateAddressAllocation") or "Dynamic"
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nlocation = {_quoted(region)}\nresource_group_name = {rg_name}\nip_configuration {{ name = "primary"; subnet_id = {subnet_ref}; private_ip_address_allocation = {_quoted(allocation)}'
            if attributes.get("privateIp"):
                body += f"; private_ip_address = {_quoted(attributes.get('privateIp'))}"
            if public_target:
                body += f"; public_ip_address_id = {context.ref(public_target)}"
            body += " }"
        elif kind == "azurerm_network_interface_security_group_association":
            body = f"network_interface_id = {context.dependency_ref(node_id, 'network_interface_id')}\nnetwork_security_group_id = {context.dependency_ref(node_id, 'network_security_group_id')}"
        elif kind == "azurerm_linux_virtual_machine":
            nic_target = context.target(node_id, "network_interface_ids[]")
            identity_target = context.target(node_id, "identity.identity_ids[]")
            nic_ref = context.ref(nic_target or "")
            bootstrap_file, bootstrap_vars = _bootstrap_expression(
                node_id, vars_by_compute
            )
            body = (
                f'name = "${{var.resource_prefix}}-{cloud_label}"\nresource_group_name = {rg_name}\nlocation = {_quoted(region)}\n'
                f"size = var.vm_sku\nadmin_username = \"easydep\"\nnetwork_interface_ids = [{nic_ref}]\n"
                'disable_password_authentication = true\nadmin_ssh_key { username = "easydep"; public_key = var.ssh_public_key }\n'
                'os_disk { caching = "ReadWrite"; storage_account_type = "Standard_LRS" }\n'
                'source_image_reference { publisher = "Canonical"; offer = "0001-com-ubuntu-server-jammy"; sku = "22_04-lts-gen2"; version = "latest" }\n'
                f'custom_data = base64encode(templatefile("${{path.module}}/{bootstrap_file}", {bootstrap_vars}))'
            )
            if identity_target:
                body += f'\nidentity {{ type = "UserAssigned"; identity_ids = [{context.ref(identity_target)}] }}'
            if attributes.get("zone"):
                body += f'\nzone = {_quoted(attributes.get("zone"))}'
        elif kind == "azurerm_linux_virtual_machine_scale_set":
            subnet_target = context.target(
                node_id, "network_interface.ip_configuration.subnet_id"
            )
            filter_target = context.target(
                node_id, "network_interface.network_security_group_id"
            )
            identity_target = context.target(node_id, "identity.identity_ids[]")
            replica = int(attributes.get("replicaCount") or 1)
            zones = attributes.get("zones") or ["1"]
            bootstrap_file, bootstrap_vars = _bootstrap_expression(
                node_id, vars_by_compute
            )
            body = (
                f'name = "${{var.resource_prefix}}-{cloud_label}"\nresource_group_name = {rg_name}\nlocation = {_quoted(region)}\n'
                f"sku = var.vm_sku\ninstances = {replica}\nzones = {json.dumps(zones)}\nadmin_username = \"easydep\"\n"
                'disable_password_authentication = true\nadmin_ssh_key { username = "easydep"; public_key = var.ssh_public_key }\n'
                'os_disk { caching = "ReadWrite"; storage_account_type = "Standard_LRS" }\n'
                'source_image_reference { publisher = "Canonical"; offer = "0001-com-ubuntu-server-jammy"; sku = "22_04-lts-gen2"; version = "latest" }\n'
                f'network_interface {{ name = "primary"; primary = true; network_security_group_id = {context.ref(filter_target or "")}\n'
                f'  ip_configuration {{ name = "primary"; primary = true; subnet_id = {context.ref(subnet_target or "")}'
            )
            backend_targets = context.targets(
                node_id,
                "network_interface.ip_configuration.load_balancer_backend_address_pool_ids[]",
            )
            if backend_targets:
                body += "; load_balancer_backend_address_pool_ids = [" + ", ".join(context.ref(item) for item in backend_targets) + "]"
            body += " }\n}\n"
            body += f'custom_data = base64encode(templatefile("${{path.module}}/{bootstrap_file}", {bootstrap_vars}))'
            for child_id, child in context.embedded_blocks.items():
                if child.get("ownerRef") != node_id or child.get("blockPath") != "data_disk":
                    continue
                child_attrs = _attrs(child)
                index = int(child_attrs.get("attachmentIndex") or 0)
                body += (
                    f'\ndata_disk {{ lun = {10 + index}; caching = "ReadWrite"; '
                    f'storage_account_type = "Standard_LRS"; disk_size_gb = {int(child_attrs.get("capacityGiB") or 10)} }}'
                )
            if identity_target:
                body += f'\nidentity {{ type = "UserAssigned"; identity_ids = [{context.ref(identity_target)}] }}'
        elif kind == "azurerm_lb":
            frontend_child = next(
                (
                    child_id
                    for child_id, child in context.embedded_blocks.items()
                    if child.get("blockPath") == "frontend_ip_configuration"
                    and child.get("ownerRef") == node_id
                ),
                None,
            )
            if not frontend_child:
                raise ValueError(f"Azure load balancer has no frontend: {node_id}")
            internal = str(attributes.get("scheme") or "public") == "internal"
            if internal:
                subnet_target = context.target(frontend_child, "subnet_id")
                frontend_name = context.dependency_ref(frontend_child, "name")
                frontend = (
                    f"frontend_ip_configuration {{ name = {frontend_name}; "
                    'private_ip_address_allocation = "Dynamic"; '
                    f"subnet_id = {context.ref(subnet_target or '')} }}"
                )
            else:
                public_target = context.target(frontend_child, "public_ip_address_id")
                frontend_name = context.dependency_ref(frontend_child, "name")
                frontend = (
                    f"frontend_ip_configuration {{ name = {frontend_name}; "
                    f"public_ip_address_id = {context.ref(public_target or '')} }}"
                )
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nlocation = {_quoted(region)}\nresource_group_name = {rg_name}\nsku = "Standard"\n{frontend}'
        elif kind == "azurerm_lb_backend_address_pool":
            body = f'name = "backend"\nloadbalancer_id = {context.dependency_ref(node_id, "loadbalancer_id")} '
        elif kind == "azurerm_lb_probe":
            port = attributes.get("port") if isinstance(attributes.get("port"), int) else 8080
            body = f'name = "health"\nloadbalancer_id = {context.dependency_ref(node_id, "loadbalancer_id")}\nprotocol = "Http"\nport = {port}\nrequest_path = "/health"'
        elif kind == "azurerm_lb_rule":
            port = attributes.get("frontendPort") if isinstance(attributes.get("frontendPort"), int) else 8080
            frontend_name = context.dependency_ref(node_id, "frontend_ip_configuration_name")
            body = f'name = "http"\nloadbalancer_id = {context.dependency_ref(node_id, "loadbalancer_id")}\nprotocol = "Tcp"\nfrontend_port = {port}\nbackend_port = {port}\nfrontend_ip_configuration_name = {frontend_name}\nbackend_address_pool_ids = [{context.dependency_ref(node_id, "backend_address_pool_ids[]")}]\nprobe_id = {context.dependency_ref(node_id, "probe_id")} '
        elif kind == "azurerm_managed_disk":
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nlocation = {_quoted(region)}\nresource_group_name = {rg_name}\nstorage_account_type = "Standard_LRS"\ncreate_option = "Empty"\ndisk_size_gb = {int(attributes.get("capacityGiB") or 10)}\nzone = {context.dependency_ref(node_id, "zone")}'
            if attributes.get("deletionPolicy") == "retain":
                body += "\nlifecycle { prevent_destroy = true }"
        elif kind == "azurerm_virtual_machine_data_disk_attachment":
            index = int(attributes.get("attachmentIndex") or 0)
            body = f"managed_disk_id = {context.dependency_ref(node_id, 'managed_disk_id')}\nvirtual_machine_id = {context.dependency_ref(node_id, 'virtual_machine_id')}\nlun = {10 + index}\ncaching = \"ReadWrite\""
        elif kind == "azurerm_network_interface_backend_address_pool_association":
            body = f"network_interface_id = {context.dependency_ref(node_id, 'network_interface_id')}\nip_configuration_name = \"primary\"\nbackend_address_pool_id = {context.dependency_ref(node_id, 'backend_address_pool_id')}"
        else:
            raise ValueError(f"Unsupported Azure ResourcePlan primitive: {node_id}/{kind}")
        blocks.append(_block(kind, label, body))
    return "\n".join(blocks)


def _gcp_resources(
    plan: dict[str, Any],
    context: _Context,
    vars_by_compute: dict[str, dict[str, str]],
) -> str:
    blocks: list[str] = []
    region = str(plan.get("region") or "")
    for node_id, node in context.nodes.items():
        if node.get("handling") != "create":
            continue
        kind = (node.get("terraformTypes") or [""])[0]
        label = _label(node_id)
        cloud_label = _cloud_label(node_id)
        attributes = _attrs(node)
        if kind == "google_artifact_registry_repository":
            body = f'location = {_quoted(region)}\nrepository_id = "${{var.resource_prefix}}-{cloud_label}"\nformat = "DOCKER"'
        elif kind == "google_service_account":
            body = f'account_id = substr("${{var.resource_prefix}}-id-${{substr(sha1({_quoted(node_id)}), 0, 6)}}", 0, 30)\ndisplay_name = "EasyDep {cloud_label}"'
        elif kind == "google_artifact_registry_repository_iam_member":
            scope = context.target(node_id, "scope")
            principal = context.target(node_id, "member")
            body = (
                f"project = var.project_id\nlocation = {context.ref(scope or '', 'location')}\n"
                f"repository = {context.ref(scope or '', 'repository_id')}\n"
                'role = "roles/artifactregistry.reader"\n'
                f'member = "serviceAccount:${{{context.address(principal or "")}.email}}"'
            )
        elif kind == "google_secret_manager_secret_iam_member":
            principal = context.target(node_id, "member")
            body = f"project = var.project_id\nsecret_id = {context.dependency_ref(node_id, 'scope')}\nrole = \"roles/secretmanager.secretAccessor\"\nmember = \"serviceAccount:${{{context.address(principal or '')}.email}}\""
        elif kind == "google_compute_network":
            body = 'name = "${var.resource_prefix}-network"\nauto_create_subnetworks = false\nrouting_mode = "REGIONAL"'
        elif kind == "google_compute_subnetwork":
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nregion = {_quoted(region)}\nnetwork = {context.dependency_ref(node_id, "network")}\nip_cidr_range = {_quoted(attributes.get("cidr"))}\nprivate_ip_google_access = true'
        elif kind == "google_compute_router":
            body = f'name = "${{var.resource_prefix}}-router"\nregion = {_quoted(region)}\nnetwork = {context.dependency_ref(node_id, "network")} '
        elif kind == "google_compute_router_nat":
            body = f'name = "${{var.resource_prefix}}-nat"\nregion = {_quoted(region)}\nrouter = {context.dependency_ref(node_id, "router")}\nnat_ip_allocate_option = "AUTO_ONLY"\nsource_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"'
            subnet_targets = context.targets(node_id, "subnetwork[].name")
            for subnet in subnet_targets:
                body += f'\nsubnetwork {{ name = {context.ref(subnet)}; source_ip_ranges_to_nat = ["ALL_IP_RANGES"] }}'
        elif kind == "google_compute_firewall":
            public_paths = attributes.get("publicInterfaces") or []
            ports = []
            for path in public_paths:
                port = path.get("port")
                ports.append(str(port if isinstance(port, int) else 8080))
            target_tags = context.dependency_refs(node_id, "target_tags[]")
            source_tags = context.dependency_refs(node_id, "source_tags[]")
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nnetwork = {context.dependency_ref(node_id, "network")}\ndirection = "INGRESS"\ntarget_tags = [{", ".join(target_tags)}]\nallow {{ protocol = "tcp"; ports = {json.dumps(ports or ["1-65535"])} }}'
            if source_tags:
                body += f"\nsource_tags = [{', '.join(source_tags)}]"
            else:
                body += '\nsource_ranges = ["0.0.0.0/0"]'
        elif kind == "google_compute_instance":
            subnet = context.target(node_id, "network_interface.subnetwork")
            tag_values = context.dependency_refs(node_id, "tags[]")
            identity = context.target(node_id, "service_account.email")
            public_address = context.target(
                node_id, "network_interface.access_config.nat_ip"
            )
            zone = attributes.get("zone") or f"{region}-a"
            bootstrap_file, bootstrap_vars = _bootstrap_expression(
                node_id, vars_by_compute
            )
            body = (
                f'name = "${{var.resource_prefix}}-{cloud_label}"\nzone = {_quoted(zone)}\nmachine_type = var.vm_sku\n'
                f"boot_disk {{ initialize_params {{ image = {context.dependency_ref(node_id, 'boot_disk.initialize_params.image')} }} }}\n"
                f"network_interface {{ subnetwork = {context.ref(subnet or '')}"
            )
            if attributes.get("privateIp"):
                body += f"; network_ip = {_quoted(attributes.get('privateIp'))}"
            if public_address:
                body += f"; access_config {{ nat_ip = {context.dependency_ref(node_id, 'network_interface.access_config.nat_ip')} }}"
            body += " }\n"
            if identity:
                body += f"service_account {{ email = {context.ref(identity, 'email')}; scopes = [\"cloud-platform\"] }}\n"
            body += f'metadata_startup_script = templatefile("${{path.module}}/{bootstrap_file}", {bootstrap_vars})\ntags = [{", ".join(tag_values)}]'
        elif kind == "google_compute_instance_template":
            subnet = context.target(node_id, "network_interface.subnetwork")
            identity = context.target(node_id, "service_account.email")
            tag_values = context.dependency_refs(node_id, "tags[]")
            bootstrap_file, bootstrap_vars = _bootstrap_expression(
                node_id, vars_by_compute
            )
            body = (
                f'name_prefix = "${{var.resource_prefix}}-{cloud_label}-"\nmachine_type = var.vm_sku\n'
                f"disk {{ source_image = {context.dependency_ref(node_id, 'boot_disk.initialize_params.image')}; auto_delete = true; boot = true }}\n"
                f"network_interface {{ subnetwork = {context.ref(subnet or '')} }}\n"
            )
            if identity:
                body += f"service_account {{ email = {context.ref(identity, 'email')}; scopes = [\"cloud-platform\"] }}\n"
            for child_id, child in context.embedded_blocks.items():
                if child.get("ownerRef") != node_id or child.get("blockPath") != "disk":
                    continue
                child_attrs = _attrs(child)
                storage_ref = _cloud_label(child_attrs.get("storageRef") or child_id)
                body += (
                    f'\ndisk {{ device_name = "easydep-{storage_ref}"; type = "PERSISTENT"; '
                    f'disk_size_gb = {int(child_attrs.get("capacityGiB") or 10)}; '
                    'disk_type = "pd-balanced"; auto_delete = '
                    f'{str(child_attrs.get("deletionPolicy") != "retain").lower()}; boot = false }}'
                )
            body += f'\ntags = [{", ".join(tag_values)}]\nmetadata = {{ startup-script = templatefile("${{path.module}}/{bootstrap_file}", {bootstrap_vars}) }}\nlifecycle {{ create_before_destroy = true }}'
        elif kind in {
            "google_compute_region_instance_group_manager",
            "google_compute_instance_group_manager",
        }:
            template = context.target(node_id, "version.instance_template")
            template_ref = context.ref(template or "", "self_link")
            replica = int(attributes.get("replicaCount") or 1)
            zones = attributes.get("zones") or []
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nbase_instance_name = "${{var.resource_prefix}}-{cloud_label}"\ntarget_size = {replica}\nversion {{ instance_template = {template_ref} }}'
            if kind == "google_compute_region_instance_group_manager":
                body += f"\nregion = {_quoted(region)}"
                if zones:
                    body += f"\ndistribution_policy_zones = {json.dumps(zones)}"
            else:
                body += f"\nzone = {_quoted(zones[0] if zones else region + '-a')}"
        elif kind == "google_compute_address":
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nregion = {_quoted(region)}\naddress_type = "EXTERNAL"'
        elif kind == "google_compute_region_health_check":
            port = attributes.get("port") if isinstance(attributes.get("port"), int) else 8080
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nregion = {_quoted(region)}\nhttp_health_check {{ port = {port}; request_path = "/health" }}'
        elif kind == "google_compute_region_backend_service":
            health = context.target(node_id, "health_checks[]")
            health_ref = context.ref(health or "")
            backend_block = next(
                (
                    block_id
                    for block_id, block in context.embedded_blocks.items()
                    if block.get("ownerRef") == node_id and block.get("blockPath") == "backend"
                ),
                None,
            )
            if not backend_block:
                raise ValueError(f"GCP backend service has no backend block: {node_id}")
            group_ref = context.dependency_ref(backend_block, "group")
            scheme = "INTERNAL" if attributes.get("scheme") == "internal" else "EXTERNAL"
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nregion = {_quoted(region)}\nprotocol = "TCP"\nload_balancing_scheme = "{scheme}"\nhealth_checks = [{health_ref}]\nbackend {{ group = {group_ref} }}'
        elif kind == "google_compute_forwarding_rule":
            backend_ref = context.dependency_ref(node_id, "backend_service")
            internal = attributes.get("scheme") == "internal"
            scheme = "INTERNAL" if internal else "EXTERNAL"
            port = attributes.get("port") if isinstance(attributes.get("port"), int) else 8080
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nregion = {_quoted(region)}\nload_balancing_scheme = "{scheme}"\nip_protocol = "TCP"\nports = ["{port}"]\nbackend_service = {backend_ref}'
            if internal:
                body += (
                    f"\nnetwork = {context.dependency_ref(node_id, 'network')}"
                    f"\nsubnetwork = {context.dependency_ref(node_id, 'subnetwork')}"
                )
        elif kind == "google_compute_disk":
            body = f'name = "${{var.resource_prefix}}-{cloud_label}"\nzone = {context.dependency_ref(node_id, "zone")}\nsize = {int(attributes.get("capacityGiB") or 10)}\ntype = "pd-balanced"'
            if attributes.get("deletionPolicy") == "retain":
                body += "\nlifecycle { prevent_destroy = true }"
        elif kind == "google_compute_attached_disk":
            storage_ref = _cloud_label(attributes.get("storageRef") or node_id)
            body = f'device_name = "easydep-{storage_ref}"\ndisk = {context.dependency_ref(node_id, "disk")}\ninstance = {context.dependency_ref(node_id, "instance")}\nzone = {context.dependency_ref(node_id, "zone")}'
        else:
            raise ValueError(f"Unsupported GCP ResourcePlan primitive: {node_id}/{kind}")
        blocks.append(_block(kind, label, body))
    return "\n".join(blocks)


def _output_file(
    plan: dict[str, Any], context: _Context
) -> tuple[str, list[tuple[str, str, str]]]:
    provider = str(plan.get("provider") or "")
    outputs: list[str] = []
    generated: list[tuple[str, str, str]] = []
    for unit in plan.get("runtimeUnits") or []:
        for container in unit.get("containers") or []:
            registry_ref = str(container.get("registryRef") or "")
            if not registry_ref:
                continue
            workload_id = str(container.get("workloadRef") or "")
            workload_label = _label(workload_id)
            expression = _registry_expression(
                provider, plan, context, registry_ref, workload_label
            )
            output_name = f"registry_{workload_label}_url"
            outputs.extend(
                [
                    f'output "{output_name}" {{',
                    f"  value = {expression}",
                    "}",
                    "",
                ]
            )
            generated.append((workload_label, output_name, registry_ref))
    return "\n".join(outputs), generated


def _script_files(
    provider: str,
    plan: dict[str, Any],
    context: _Context,
    generated: list[tuple[str, str, str]],
) -> dict[str, str]:
    environment = {
        "aws": "AWS_PROFILE=${AWS_PROFILE:-default}",
        "azure": "ARM_SUBSCRIPTION_ID=${ARM_SUBSCRIPTION_ID:?set ARM_SUBSCRIPTION_ID}",
        "gcp": "GOOGLE_PROJECT=${GOOGLE_PROJECT:?set GOOGLE_PROJECT}",
    }[provider]
    prefix = f"#!/usr/bin/env bash\nset -euo pipefail\nexport {environment}\n"
    doctor_cli = {"aws": "aws", "azure": "az", "gcp": "gcloud"}[provider]
    deploy = [
        prefix.rstrip(),
        "tofu init",
    ]
    if generated:
        placeholder_digest = "sha256:" + "0" * 64
        for workload_label, _, _ in generated:
            deploy.append(
                f'export TF_VAR_image_digest_{workload_label}="${{TF_VAR_image_digest_{workload_label}:-{placeholder_digest}}}"'
            )
        targets = " ".join(
            f"-target={context.address(registry_ref)}"
            for _, _, registry_ref in generated
        )
        deploy.append(f"tofu apply -auto-approve {targets}")
        for workload_label, output_name, _ in generated:
            deploy.extend(
                [
                    f'REGISTRY_URL=$(tofu output -raw {output_name})',
                    'REGISTRY_HOST=$(printf "%s" "$REGISTRY_URL" | cut -d/ -f1)',
                ]
            )
            if provider == "aws":
                deploy.append(
                    f'aws ecr get-login-password --region {_quoted(plan.get("region"))} | docker login --username AWS --password-stdin "$REGISTRY_HOST"'
                )
            elif provider == "azure":
                deploy.append(
                    'az acr login --name "$(printf "%s" "$REGISTRY_HOST" | cut -d. -f1)"'
                )
            else:
                deploy.append('gcloud auth configure-docker "$REGISTRY_HOST" --quiet')
            deploy.extend(
                [
                    f'IMAGE_TAG="$REGISTRY_URL:easydep-{workload_label}"',
                    'docker build --pull -t "$IMAGE_TAG" ..',
                    'PUSH_OUTPUT=$(docker push "$IMAGE_TAG" 2>&1)',
                    'printf "%s\\n" "$PUSH_OUTPUT"',
                    'IMAGE_DIGEST=$(printf "%s\\n" "$PUSH_OUTPUT" | sed -n "s/.*digest: \\(sha256:[0-9a-f]\\{64\\}\\).*/\\1/p" | tail -1)',
                    '[ -n "$IMAGE_DIGEST" ] || { echo "docker push did not report an immutable digest" >&2; exit 1; }',
                    f'export TF_VAR_image_digest_{workload_label}="$IMAGE_DIGEST"',
                ]
            )
    deploy.append('tofu apply "$@"')
    return {
        "doctor.sh": prefix
        + "command -v tofu >/dev/null\ncommand -v docker >/dev/null\n"
        + f"command -v {doctor_cli} >/dev/null\n"
        + "tofu version\n",
        "plan.sh": prefix + "tofu init\ntofu validate\ntofu plan -out=easydep.tfplan \"$@\"\n",
        "deploy.sh": "\n".join(deploy) + "\n",
        "status.sh": prefix + "tofu output -json\n",
        "destroy.sh": prefix + "tofu destroy \"$@\"\n",
    }


def rendered_resource_types(files: dict[str, str]) -> list[str]:
    pattern = re.compile(r'^resource\s+"([^"]+)"\s+"[^"]+"\s*\{', re.MULTILINE)
    return sorted(
        match.group(1)
        for name, content in files.items()
        if name.endswith(".tf")
        for match in pattern.finditer(content)
    )


def render_open_tofu(resource_plan: dict[str, Any]) -> dict[str, str]:
    """Render one complete, provider-selected OpenTofu module."""

    if resource_plan.get("schemaVersion") != RESOURCE_PLAN_SCHEMA:
        raise ValueError("Deterministic rendering requires ResourcePlan")
    if resource_plan.get("unresolved"):
        raise ValueError("ResourcePlan has unresolved structural decisions")
    provider = str(resource_plan.get("provider") or "")
    if provider not in PINNED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    context = _Context(resource_plan)
    bootstrap_files, vars_by_compute = _runtime_files(resource_plan, context)
    outputs, generated = _output_file(resource_plan, context)
    renderer = {
        "aws": _aws_resources,
        "azure": _azure_resources,
        "gcp": _gcp_resources,
    }[provider]
    locals_content = _locals_file(resource_plan)
    files = {
        "easydep-provider.tf": _provider_file(
            provider, str(resource_plan.get("region") or "")
        ),
        "variables.tf": _variable_file(resource_plan),
        "main.tf": renderer(resource_plan, context, vars_by_compute),
        "outputs.tf": outputs,
        **bootstrap_files,
        **_script_files(provider, resource_plan, context, generated),
        **({"easydep-locals.tf": locals_content} if locals_content else {}),
    }
    expected_types = sorted(
        item
        for node in resource_plan.get("nodes") or []
        if node.get("handling") == "create"
        for item in node.get("terraformTypes") or []
    )
    actual_types = rendered_resource_types(files)
    if actual_types != expected_types:
        raise ValueError(
            f"Rendered Terraform diverges from ResourcePlan: expected={expected_types}, actual={actual_types}"
        )
    unconsumed = sorted(
        str(item.get("id") or "")
        for item in resource_plan.get("references") or []
        if str(item.get("id") or "") not in context.consumed_reference_ids
    )
    if unconsumed:
        raise ValueError(f"ResourcePlan references were not rendered: {unconsumed}")
    return {name: content.rstrip() + "\n" for name, content in files.items()}


__all__ = ["render_open_tofu", "rendered_resource_types"]
