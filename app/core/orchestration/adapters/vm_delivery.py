"""Generate Docker-on-VM Terraform at the implementation boundary."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shlex
import shutil
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import perf_counter, sleep
from typing import Any

import hcl2

from app.core.cloudkb.depkb.knowledge_access import query_knowledge
from app.core.cloudkb.depkb.provider_cache import (
    PINNED_PROVIDERS,
    audit_provider_cache,
    provider_cache_environment,
    provider_mirror_configuration,
)
from app.core.config import settings
from app.core.orchestration.app_cloud_contracts import (
    ApplicationRuntimeContract,
    CloudCapabilityContract,
    DeploymentBindingContract,
    contract_value,
)
from app.core.orchestration.iac_binding_validation import (
    observe_terraform_plan,
    validate_iac_bindings,
    validate_managed_group_binding,
    validate_resource_plan_against_plan,
    validate_resource_plan_binding,
    validate_vm_selection_binding,
)
from app.core.orchestration.process import run_process_tree
from app.core.orchestration.provider_deployment import (
    bind_application_runtime,
    resource_plan_digest,
)
from app.core.orchestration.provider_target import resolve_resource_spec
from app.core.orchestration.vm_selection import select_vm_candidates
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_runtime_puml,
)
from app.requirements.capability_contract import (
    accepted_needs,
    requires_persistent_storage,
)

SYSTEM_PROMPT = """You translate one supplied ResourcePlan into deployable Terraform for Docker workloads on Linux VMs.
Do not redesign, merge, or add workloads. Use only the supplied structured requirements,
ResourcePlan, and dependency evidence. Support only AWS, Azure,
or GCP. Do not use Kubernetes, managed application platforms,
managed databases, VPNs, or serverless services. Provision every resource needed by the VM,
network, the ResourcePlan's exact external endpoint protocol, optional persistent data disk,
and optional load balancer. Public endpoints in the supported profile are HTTP only. Do not add
HTTPS listeners, certificates, TLS proxies, domain validation, or port 443 resources.
Interpret dependency evidence conservatively: `existenceDecision=confirmed` proves that a
reference relation exists, while only `necessityDecision=confirmed` or `documented` supports a
necessity claim. Never turn `candidate` or `notAssessed` necessity into a universal mandatory
dependency. Components of a supplied capability realization are required for that selected
realization, without implying that they are universally required for every provider deployment.
Items in dependencyPlan.coverage.unmodeledAcceptedNeeds are requirements not modeled by the
dependency knowledge base. Do not claim that the dependency plan satisfies or evidences them;
use deploymentNeeds as their requirement source.
Use cloud-init/user-data to install Docker, run the supplied container image on the requested
port, expose the health path, and mount persistent storage when required. Never embed
credentials. EasyDep does not receive or store CSP credentials: the caller runs the generated
bundle with their locally configured AWS, Azure, or Google Cloud authentication. A database
deployment receives only a caller-created provider Secret reference through the sensitive
`database_secret_ref` variable. Never create the Secret or its value. Create only the VM identity
and least-privilege read binding required by ResourcePlan. Prefer variables for image IDs,
project/subscription identifiers, and container image. Return one JSON object with
`terraformFiles`, a map from safe
flat file names to complete contents. It must contain at least one .tf file plus doctor.sh,
plan.sh, deploy.sh, status.sh, and destroy.sh; a retained data disk also requires purge.sh.
It may include .tftpl or .tpl files referenced by Terraform. The caller runs these scripts
with locally configured cloud and Registry authentication. deploy.sh must create the planned
Registry first, build and push the application image once, resolve its sha256 digest, and only
then create compute using that immutable digest. It must resume from a matching checkpoint
instead of rebuilding an already recorded digest. Also return `deploymentNotes`, a short
array. Do not
return Markdown fences or any non-JSON text. All .tf files form one Terraform module: never
declare the same resource, variable, output, data source, or module block more than once.
Application migrations own application schemas and seed records. Infrastructure bootstrap may
create the database service and database itself, but must not create application tables or insert
domain records. Preserve every observed runtime environment value prefix, including language or
driver prefixes such as `jdbc:`; a generic database URI is not interchangeable with a JDBC URL.
When a load balancer is present, expose the application port only to that load balancer. Direct
HTTP ingress may expose the application port through the planned public address and firewall.
For AWS VM bootstrap, use current Amazon Linux 2023 commands (`dnf` and `systemctl`) unless the
input explicitly selects another image family; do not pair a caller-supplied current AMI with
legacy `amazon-linux-extras` commands.
If VM bootstrap installs packages or pulls container images, provision a working outbound path.
For a new AWS VPC this includes an internet gateway and associated default route; each bootstrapping
instance then needs a public address or a NAT path. A security-group egress rule alone is not
network reachability. On AWS Nitro instances, resolve an attached EBS volume by its volume
identity with Amazon Linux `ebsnvme-id` or NVMe controller metadata. Do not invent a Linux
`/dev/disk/by-id/aws-<volume-id>` path; AWS documents that convention for FreeBSD, not Linux.
Never assume that `/dev/sdf` is the Linux guest device name.
Persist attached-volume mounts with a filesystem UUID in `/etc/fstab` or an equivalent
systemd mount. Do not bind a freshly formatted filesystem root directly to a container data
directory. Filesystem-created entries such as `lost+found` can violate a runtime's empty-data-
directory initialization contract. Create and permission a dedicated child directory below the
guest mount, then bind that child or configure the runtime to use it as its actual data path. Long-running application
and state containers started by VM bootstrap must
declare a Docker restart policy so dependency startup order and VM reboot do not leave them down.
Inside files consumed by Terraform `templatefile`, escape shell variable interpolation as
`$${NAME}` or avoid shell variables; `${NAME}` is reserved for a key explicitly supplied in
the templatefile vars map.
The exact envelope is {"terraformFiles":{"main.tf":"..."},"deploymentNotes":["..."]};
deploymentNotes must never be placed inside terraformFiles."""

VM_SELECTION_INSTRUCTION = """When vmSelection.status is `selected`, use its recommended
specName as the VM instance type/size. Declare it as a literal or as a Terraform variable with
that exact default so independent evaluation can verify the choice. When selection is deferred,
do not claim that an arbitrary VM size satisfies capacity or budget."""

PERSISTENCE_INSTRUCTION = """Create a separate data disk only when ResourcePlan contains one.
The ResourcePlan attachment and allocation identify the owning workload and compute; never move
that disk to another workload merely because applicationPersistentStorageRequired is true. A
provider's mandatory VM boot disk is not an application data disk. Format and mount the data disk
on its owning guest. Bind it into a container at applicationMountPath only when that path is
supplied for the same owning workload. For a separate persistent workload, do not pretend that an
application mount path is its data path. Use that workload's ResourcePlan runtime.dataPath; an
unresolved runtime contract must stop generation before this prompt. The guest source path may
differ from the container target path. Format only
when no filesystem exists; bootstrap must be idempotent and must never erase an already formatted
persistent disk. Ensure the runtime's actual data directory is a dedicated child of the guest
filesystem mount, either by binding that child or by configuring the runtime data path. Select the attached disk by a stable provider device identity, never by taking
the first enumerated block device, and use a bounded wait for attachment visibility. When the
ResourcePlan has no separate disk, do not create, attach, mount, or advertise one."""

TOPOLOGY_INSTRUCTION = """Implement DeploymentTopology/v1 exactly. standaloneOne creates one
standalone VM. managedGroupOne creates a CSP-native managed group with fixed desired capacity 1.
managedGroupManySingleZone creates a fixed-size managed group with replicaCount >= 2 in one
occupied zone. managedGroupManyMultiZone creates a fixed-size managed group with replicaCount >=
2 spread over the selected zones. A managed group does not imply traffic autoscaling, automatic
repair, high availability, or an SLA. publicIngress=direct is valid only for standaloneOne and
uses its reserved public address. publicIngress=loadBalanced uses the selected provider-native L4
load balancer and private backend addresses: AWS Network Load Balancer, Azure Load Balancer, or
GCP Regional External Passthrough Network Load Balancer. The public workload is still HTTP, while
the load balancer forwards TCP and may use an HTTP readiness probe. HTTPS/TLS,
certificates, domain validation, and TLS reverse proxies are explicitly out of scope.
Never replace a managed group with duplicated standalone VMs or claim an availability outcome.
Use ResourcePlan's CIDR blocks exactly and reject overlap. Create the provider-native private
application Registry and pull identity declared by ResourcePlan; VM bootstrap must consume an
immutable `image@sha256` reference. Resolve every VM or template from the planned existing OS
image and expose the resolved image ID as an output/checkpoint value. A dedicated state VM uses
the planned static private address. Retained data disks require `lifecycle.prevent_destroy=true`.
destroy.sh must detach the disk and remove it from Terraform state before destroying the other
run-owned resources. Only purge.sh, after explicit confirmation, may remove the guard and delete
the retained disk. Normal destroy must not delete application data."""

PROVIDER_COMPATIBILITY = {
    "aws": {
        "providerConstraint": "hashicorp/aws 5.100.0",
        "rules": [
            {
                "resourceType": "aws_instance",
                "rule": (
                    "For Amazon Linux 2023, use the official public SSM parameter "
                    "/aws/service/ami-amazon-linux-latest/"
                    "al2023-ami-kernel-default-x86_64 or a required ami_id variable. "
                    "Do not guess a mutable aws_ami name filter."
                ),
            },
            {
                "resourceType": "aws_lb",
                "rule": (
                    "For the ResourcePlan load balancer set load_balancer_type=network. "
                    "Use a TCP port 80 listener and TCP backend target port; keep the "
                    "application readiness path as the target group's HTTP health check."
                ),
            },
            {
                "resourceType": "aws_lb_target_group_attachment",
                "rule": (
                    "For standaloneOne load-balanced ingress, explicitly register the "
                    "EC2 instance with target_group_arn and target_id. For an Auto "
                    "Scaling Group, use target_group_arns on the group instead."
                ),
            },
            {
                "resourceType": "aws_ecr_repository",
                "rule": (
                    "Create the application repository and an EC2 IAM role, managed "
                    "ECR read-only attachment, and instance profile. Attach the profile "
                    "to the standalone instance or Launch Template."
                ),
            },
            {
                "resourceType": "aws_iam_role_policy",
                "rule": (
                    "For a dedicated State VM, use a separate EC2 role, inline Secret read "
                    "policy, and instance profile. Do not attach the application's ECR read "
                    "policy to the State VM role."
                ),
            },
            {
                "resourceType": "aws_volume_attachment",
                "rule": (
                    "On Linux Nitro instances, enumerate /dev/nvme*n1 and call "
                    "/sbin/ebsnvme-id for each candidate, then compare its Volume ID "
                    "with the Terraform-supplied EBS volume ID after normalizing hyphens. "
                    "Do not infer the device from nvme list ordering or an invented "
                    "/dev/disk/by-id/aws-* path."
                ),
            },
        ],
    },
    "azure": {
        "providerConstraint": "hashicorp/azurerm 5.0.1",
        "rules": [
            {
                "resourceType": "azurerm_network_interface",
                "rule": "Do not set network_security_group_id on the NIC resource.",
            },
            {
                "resourceType": "azurerm_resource_group",
                "rule": (
                    "Create one deployment-owned Resource Group and reference its name "
                    "and location from every generated Azure resource."
                ),
            },
            {
                "resourceType": "azurerm_nat_gateway_public_ip_association",
                "rule": (
                    "Bind the Standard Static NAT Public IP to the NAT Gateway, in "
                    "addition to the separate Subnet-NAT Gateway association."
                ),
            },
            {
                "resourceType": "azurerm_role_assignment",
                "rule": (
                    "A dedicated State VM uses its own User-assigned Managed Identity and "
                    "Key Vault Secrets User assignment. Do not grant it AcrPull."
                ),
            },
            {
                "resourceType": "azurerm_network_interface_security_group_association",
                "rule": (
                    "Associate a NIC and NSG with network_interface_id and "
                    "network_security_group_id."
                ),
            },
            {
                "resourceType": "azurerm_lb_rule",
                "rule": (
                    "Use the Standard public Load Balancer frontend, TCP frontend port 80, "
                    "the application backend port, the Backend Address Pool ID, and Probe ID."
                ),
            },
            {
                "resourceType": "azurerm_managed_disk",
                "rule": "Set create_option; use Empty for a newly allocated data disk.",
            },
            {
                "resourceType": "azurerm_virtual_machine_data_disk_attachment",
                "rule": (
                    "Attach a managed data disk to azurerm_linux_virtual_machine with this "
                    "separate resource using managed_disk_id, virtual_machine_id, lun, and "
                    "caching. Do not add a data_disk block inside azurerm_linux_virtual_machine."
                ),
            },
        ],
    },
    "gcp": {
        "providerConstraint": "hashicorp/google 7.8.0",
        "rules": [
            {
                "resourceType": "google_compute_forwarding_rule",
                "rule": (
                    "Implement a regional external passthrough Network Load Balancer: use "
                    "load_balancing_scheme=EXTERNAL, IP protocol TCP, port 80, the regional "
                    "Address, and the Region Backend Service. Do not create a target proxy or URL map."
                ),
            },
            {
                "resourceType": "google_compute_region_backend_service",
                "rule": (
                    "Use protocol TCP and an EXTERNAL regional backend service with an HTTP "
                    "Region Health Check. Passthrough does not translate ports, so publish the "
                    "application container on host port 80."
                ),
            },
            {
                "resourceType": "google_compute_router_nat",
                "rule": (
                    "Use nat_ip_allocate_option=AUTO_ONLY, "
                    "source_subnetwork_ip_ranges_to_nat=LIST_OF_SUBNETWORKS, and a "
                    "subnetwork block selecting the planned Subnetwork with "
                    "source_ip_ranges_to_nat=[ALL_IP_RANGES]."
                ),
            },
            {
                "resourceType": "google_compute_network",
                "rule": (
                    "Keep the VPC Network default internet route when Cloud NAT is selected; "
                    "do not set delete_default_routes_on_create=true."
                ),
            },
            {
                "resourceType": "google_secret_manager_secret_iam_member",
                "rule": (
                    "A dedicated State VM uses a separate Service Account and Secret Accessor "
                    "IAM member. Do not grant that account Artifact Registry Reader."
                ),
            },
            {
                "resourceType": "google_compute_firewall",
                "rule": (
                    "Select backend VMs with target tags or service accounts. Permit "
                    "public TCP application traffic and the regional health probes on "
                    "the planned host port; never expose PostgreSQL port 5432 publicly."
                ),
            },
        ],
    },
}

SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:tf|tftpl|tpl|sh)$")
PLAN_ONLY_SSH_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g "
    "easydep-plan-only"
)


@contextmanager
def _inheriting_temporary_directory(parent: Path, prefix: str):
    """Create a workspace-readable temp directory and wait out provider file locks."""
    for _ in range(10):
        root = parent / f"{prefix}{secrets.token_hex(8)}"
        try:
            root.mkdir()
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - cryptographic collision guard
        raise FileExistsError("Could not allocate a validation directory")
    try:
        yield root
    finally:
        last_error: OSError | None = None
        for _ in range(20):
            try:
                shutil.rmtree(root)
                return
            except FileNotFoundError:
                return
            except OSError as error:
                last_error = error
                sleep(0.25)
        if last_error is not None:
            raise last_error


DOCKERFILE = """FROM gradle:8.14.2-jdk21 AS build
WORKDIR /workspace
COPY . .
RUN gradle bootJar --no-daemon

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /workspace/build/libs/*.jar app.jar
EXPOSE {application_port}
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
"""

DOCKERIGNORE = """.git
.gradle
build
infra/.terraform
"""


class BindingMismatchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class VmDeliveryAdapter:
    """LLM boundary shared by full and cloud-KB-ablation experiment arms."""

    def __init__(
        self,
        invoke: Callable[[str], str] | None = None,
        validate: Callable[[dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        self._invoke = invoke or self._invoke_llm
        if validate is not None:
            self._validate_provider_schema = validate
        elif invoke is None:
            self._validate_provider_schema = self._provider_validation
        else:
            self._validate_provider_schema = lambda _files: {
                "status": "skipped",
                "reason": "Injected test generator has no provider validator.",
            }
        self.last_timing_events: list[dict[str, Any]] = []
        self._expected_provider: str | None = None

    def _timed_invoke(self, operation: str, prompt: str) -> str:
        started_at = datetime.now(UTC)
        started = perf_counter()
        status = "failed"
        response_characters: int | None = None
        try:
            result = self._invoke(prompt)
            response_characters = len(result)
            status = "completed"
            return result
        finally:
            finished_at = datetime.now(UTC)
            self.last_timing_events.append(
                {
                    "operation": operation,
                    "status": status,
                    "startedAt": started_at.isoformat(),
                    "finishedAt": finished_at.isoformat(),
                    "elapsedSeconds": round(perf_counter() - started, 6),
                    "responseCharacters": response_characters,
                }
            )

    def _timed_hcl_validation(self, files: dict[str, str]) -> list[str]:
        started_at = datetime.now(UTC)
        started = perf_counter()
        errors = self._validate_files(files)
        finished_at = datetime.now(UTC)
        self.last_timing_events.append(
            {
                "operation": "iac.hclPreflight",
                "status": "failed" if errors else "completed",
                "startedAt": started_at.isoformat(),
                "finishedAt": finished_at.isoformat(),
                "elapsedSeconds": round(perf_counter() - started, 6),
            }
        )
        return errors

    def _record_provider_timings(self, validation: dict[str, Any], *, attempt: str) -> None:
        for report in validation.get("reports") or []:
            self.last_timing_events.append(
                {
                    "operation": f"iac.provider.{report.get('command') or 'unknown'}",
                    "attempt": attempt,
                    "status": "completed" if report.get("exitCode") == 0 else "failed",
                    "startedAt": report.get("startedAt"),
                    "finishedAt": report.get("finishedAt"),
                    "elapsedSeconds": report.get("elapsedSeconds"),
                }
            )

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
        from openai import OpenAI

        response = OpenAI(
            api_key=os.environ["API_KEY"],
            base_url=settings.base_url,
            timeout=600,
            max_retries=0,
        ).chat.completions.create(
            model=settings.model,
            temperature=settings.temperature,
            seed=settings.seed,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        SYSTEM_PROMPT
                        + "\n"
                        + VM_SELECTION_INSTRUCTION
                        + "\n"
                        + PERSISTENCE_INSTRUCTION
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or "{}"

    @staticmethod
    def _target(implementation_result: dict[str, Any]) -> Path:
        run_root = Path(str(implementation_result.get("run_root") or ""))
        application = run_root / "application"
        if not application.is_dir():
            raise ValueError(f"Generated application repository is absent: {application}")
        return application / "infra"

    @staticmethod
    def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        files = payload.get("terraformFiles")
        if not isinstance(files, dict) or "deploymentNotes" not in files:
            return payload
        nested_notes = files["deploymentNotes"]
        if (
            "deploymentNotes" in payload
            or not isinstance(nested_notes, list)
            or not all(isinstance(note, str) for note in nested_notes)
        ):
            raise ValueError("deploymentNotes must be a top-level string array")
        return {
            **payload,
            "terraformFiles": {
                name: content for name, content in files.items() if name != "deploymentNotes"
            },
            "deploymentNotes": nested_notes,
        }

    @staticmethod
    def _files(payload: dict[str, Any]) -> dict[str, str]:
        files = payload.get("terraformFiles")
        if not isinstance(files, dict) or not files:
            raise ValueError("IaC generator returned no terraformFiles")
        normalized: dict[str, str] = {}
        for raw_name, raw_content in files.items():
            name = str(raw_name)
            path = PurePosixPath(name)
            if path.name != name or not SAFE_FILE.fullmatch(name):
                raise ValueError(f"Unsafe Terraform file name: {name}")
            content = str(raw_content)
            if not content.strip():
                raise ValueError(f"Terraform file is empty: {name}")
            normalized[name] = content.rstrip() + "\n"
        if not any(name.endswith(".tf") for name in normalized):
            raise ValueError("IaC generator returned no .tf file")
        return normalized

    @staticmethod
    def _block_labels(block: Any) -> list[str]:
        return [str(label.serialize()).strip('"') for label in getattr(block, "labels", [])]

    @staticmethod
    def _source_span(element: Any) -> tuple[int, int]:
        meta = element.to_lark().meta
        return int(meta.start_pos), int(meta.end_pos)

    @classmethod
    def _normalize_provider_ownership(
        cls, files: dict[str, str]
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        """Remove only top-level declarations owned by the pinned system policy."""
        normalized = dict(files)
        events: list[dict[str, Any]] = []
        for name, content in sorted(files.items()):
            if not name.endswith(".tf"):
                continue
            try:
                tree = hcl2.parses(content)
            except Exception:  # noqa: BLE001 - the normal HCL preflight owns syntax errors
                continue
            spans: list[tuple[int, int]] = []
            removed: list[str] = []
            for block in tree.body.children:
                labels = cls._block_labels(block)
                if not labels or labels[0] not in {"terraform", "provider"}:
                    continue
                spans.append(cls._source_span(block))
                removed.append(".".join(labels[:2]))
            if not spans:
                continue
            rewritten = content
            for start, end in sorted(spans, reverse=True):
                rewritten = rewritten[:start] + rewritten[end:]
            normalized[name] = rewritten.strip() + "\n"
            events.append(
                {
                    "kind": "systemProviderOwnership",
                    "file": name,
                    "removedBlocks": sorted(removed),
                }
            )
        return normalized, events

    @classmethod
    def _normalize_native_templatefiles(
        cls, files: dict[str, str]
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        """Lower the standard template provider shape to Terraform's built-in function."""
        candidates: dict[str, dict[str, Any]] = {}
        duplicates: set[str] = set()
        for file_name, content in sorted(files.items()):
            if not file_name.endswith(".tf"):
                continue
            try:
                tree = hcl2.parses(content)
            except Exception:  # noqa: BLE001 - the normal HCL preflight owns syntax errors
                continue
            for block in tree.body.children:
                labels = cls._block_labels(block)
                if len(labels) != 3 or labels[:2] != ["data", "template_file"]:
                    continue
                instance_name = labels[2]
                attributes: dict[str, str] = {}
                for child in block.body.children:
                    if type(child).__name__ != "AttributeRule":
                        continue
                    serialized = child.serialize()
                    if not isinstance(serialized, dict) or len(serialized) != 1:
                        continue
                    attribute_name = str(next(iter(serialized)))
                    expression = child.children[-1]
                    start, end = cls._source_span(expression)
                    attributes[attribute_name] = content[start:end]
                template = attributes.get("template", "")
                match = re.fullmatch(r"\s*file\s*\((.*)\)\s*", template, flags=re.DOTALL)
                if set(attributes) - {"template", "vars"} or match is None:
                    continue
                if instance_name in candidates:
                    duplicates.add(instance_name)
                    continue
                candidates[instance_name] = {
                    "file": file_name,
                    "span": cls._source_span(block),
                    "expression": (
                        f"templatefile({match.group(1).strip()}, "
                        f"{attributes.get('vars', '{}').strip()})"
                    ),
                }
        for duplicate in duplicates:
            candidates.pop(duplicate, None)
        if not candidates:
            return dict(files), []

        rewritten = dict(files)
        spans_by_file: dict[str, list[tuple[int, int]]] = {}
        for candidate in candidates.values():
            spans_by_file.setdefault(str(candidate["file"]), []).append(candidate["span"])
        for file_name, spans in spans_by_file.items():
            content = rewritten[file_name]
            for start, end in sorted(spans, reverse=True):
                content = content[:start] + content[end:]
            rewritten[file_name] = content
        for file_name, content in list(rewritten.items()):
            if not file_name.endswith(".tf"):
                continue
            for instance_name, candidate in candidates.items():
                content = re.sub(
                    rf"\bdata\.template_file\.{re.escape(instance_name)}\.rendered\b",
                    str(candidate["expression"]),
                    content,
                )
            rewritten[file_name] = content.rstrip() + "\n"
        events = [
            {
                "kind": "nativeTemplatefileLowering",
                "file": str(candidate["file"]),
                "dataSource": f"data.template_file.{instance_name}",
            }
            for instance_name, candidate in sorted(candidates.items())
        ]
        return rewritten, events

    @classmethod
    def _normalize_generated_files(
        cls, files: dict[str, str]
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        normalized, provider_events = cls._normalize_provider_ownership(files)
        normalized, template_events = cls._normalize_native_templatefiles(normalized)
        return normalized, [*provider_events, *template_events]

    @staticmethod
    def _validate_files(files: dict[str, str]) -> list[str]:
        declarations: set[tuple[str, ...]] = set()
        errors: list[str] = []
        for name, content in sorted(files.items()):
            if not name.endswith(".tf"):
                continue
            try:
                parsed = hcl2.load(io.StringIO(content))
            except Exception as error:  # noqa: BLE001 - parser exceptions vary by lark version
                errors.append(f"{name}: HCL parse error: {error}")
                continue
            for block_type in ("resource", "data"):
                for declaration in parsed.get(block_type, []):
                    for provider_type, instances in declaration.items():
                        for instance_name in instances:
                            resource_identity = (
                                block_type,
                                str(provider_type),
                                str(instance_name),
                            )
                            if resource_identity in declarations:
                                errors.append(f"duplicate {' '.join(resource_identity)}")
                            declarations.add(resource_identity)
            for block_type in ("variable", "output", "module"):
                for declaration in parsed.get(block_type, []):
                    for instance_name in declaration:
                        named_identity = (block_type, str(instance_name))
                        if named_identity in declarations:
                            errors.append(f"duplicate {' '.join(named_identity)}")
                        declarations.add(named_identity)
            for declaration in parsed.get("variable", []):
                for raw_name, body in declaration.items():
                    default = (body or {}).get("default")
                    if isinstance(default, str) and re.search(
                        r"\$\{(?:var|local|data|module|[a-z0-9]+_[a-z0-9_]+)\.",
                        default,
                        flags=re.IGNORECASE,
                    ):
                        errors.append(
                            f"variable {raw_name} default references another Terraform object"
                        )
        return sorted(set(errors))

    @staticmethod
    def _templatefile_binding_errors(files: dict[str, str]) -> list[str]:
        """Reject supplied template variables escaped into literal shell placeholders."""
        errors: list[str] = []

        def values(value: Any):
            if isinstance(value, dict):
                for nested in value.values():
                    yield from values(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from values(nested)
            elif isinstance(value, str):
                yield value

        calls: list[tuple[str, set[str]]] = []
        for name, content in sorted(files.items()):
            if not name.endswith(".tf"):
                continue
            try:
                parsed = hcl2.loads(content)
            except Exception:  # noqa: BLE001 - HCL preflight reports syntax failures
                continue
            for value in values(parsed):
                for match in re.finditer(
                    r'templatefile\(\s*"([^"\r\n]+)"\s*,\s*\{(.*?)\}\s*\)',
                    value,
                    flags=re.DOTALL,
                ):
                    keys = set(re.findall(r"(?m)(?:^|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", match[2]))
                    calls.append((PurePosixPath(match[1]).name, keys))
        for template_name, supplied_keys in calls:
            template = files.get(template_name)
            if template is None:
                continue
            escaped = set(re.findall(r"\$\$\{([A-Za-z_][A-Za-z0-9_]*)\}", template))
            incorrectly_escaped = sorted(escaped & supplied_keys)
            if incorrectly_escaped:
                errors.append(
                    f"{template_name}: templatefile input keys are escaped into literal "
                    "shell placeholders: " + ", ".join(incorrectly_escaped)
                )
        return errors

    @staticmethod
    def _runtime_bootstrap_errors(
        files: dict[str, str],
        app_contract: ApplicationRuntimeContract,
        provider: str = "",
    ) -> list[str]:
        """Check application-owned runtime facts that Terraform syntax cannot validate."""
        text = "\n".join(files.values())
        errors = VmDeliveryAdapter._templatefile_binding_errors(files)
        application_owns_migrations = any(
            fact.kind == "build.dependency"
            and any(
                "flyway" in str(item.get("coordinate") or "").lower()
                for item in fact.attributes.get("declarations") or []
                if isinstance(item, dict)
            )
            for fact in app_contract.facts
        )
        if application_owns_migrations and re.search(
            r"(?im)^\s*(?:create|alter|drop)\s+(?:table|index|sequence|schema)\b|"
            r"^\s*insert\s+into\b",
            text,
        ):
            errors.append(
                "Infrastructure bootstrap duplicates an application schema or seed-data "
                "migration owned by the generated application."
            )

        required_environment: set[str] = set()
        observed_prefixes: dict[str, str] = {}
        for fact in app_contract.facts:
            if fact.kind == "runtime.configuration.intent":
                required_environment.update(
                    str(item) for item in fact.attributes.get("requiredKeys") or []
                )
            if fact.kind != "runtime.environment":
                continue
            name = str(fact.attributes.get("name") or "")
            prefix = str(fact.attributes.get("valuePrefix") or "")
            if name and prefix:
                observed_prefixes[name] = prefix
        missing = sorted(name for name in required_environment if name not in text)
        if missing:
            errors.append(
                "Infrastructure bootstrap omits required application environment keys: "
                + ", ".join(missing)
            )
        for name, prefix in sorted(observed_prefixes.items()):
            if name in text and prefix not in text:
                errors.append(
                    f"Infrastructure bootstrap does not preserve the observed {name} "
                    f"value prefix {prefix!r}."
                )
        if provider == "aws":
            lower = text.lower()
            bootstrap_needs_internet = any(
                marker in lower for marker in ("dnf ", "yum ", "docker pull", "docker run")
            )
            creates_vpc = 'resource "aws_vpc"' in lower
            has_public_route = (
                'resource "aws_internet_gateway"' in lower
                and ('resource "aws_route"' in lower or 'resource "aws_route_table"' in lower)
                and ('resource "aws_route_table_association"' in lower or "route_table_id" in lower)
                and "0.0.0.0/0" in lower
            )
            if bootstrap_needs_internet and creates_vpc and not has_public_route:
                errors.append(
                    "AWS VM bootstrap needs outbound internet but the new VPC has no "
                    "internet gateway, associated route table, and default route."
                )
            has_private_bootstrap_instance = bool(
                re.search(r"associate_public_ip_address\s*=\s*false", lower)
            )
            if (
                bootstrap_needs_internet
                and has_private_bootstrap_instance
                and 'resource "aws_nat_gateway"' not in lower
            ):
                errors.append(
                    "An AWS instance without a public address runs internet-dependent "
                    "bootstrap but no NAT gateway path is declared."
                )
            fixed_device = re.search(
                r"(?im)^\s*device\s*=\s*[\"']?/dev/(?:sd|xvd)[a-z0-9]+",
                text,
            )
            supported_nvme_mapping = "/dev/nvme" in lower and any(
                marker in lower for marker in ("ebsnvme-id", "nvme id-ctrl")
            )
            invented_linux_aws_symlink = "/dev/disk/by-id/aws-" in lower
            if 'resource "aws_volume_attachment"' in lower and invented_linux_aws_symlink:
                errors.append(
                    "AWS EBS bootstrap uses an undocumented Linux /dev/disk/by-id/aws-* "
                    "path instead of ebsnvme-id or NVMe controller metadata."
                )
            has_volume_attachment = 'resource "aws_volume_attachment"' in lower
            storage_bootstrap = bool(re.search(r"(?im)^\s*(?:mkfs\S*|mount)\s", text))
            if has_volume_attachment and storage_bootstrap and not supported_nvme_mapping:
                errors.append(
                    "AWS EBS format/mount bootstrap does not resolve the attached volume "
                    "by enumerating /dev/nvme devices with ebsnvme-id or NVMe "
                    "controller metadata."
                )
            elif has_volume_attachment and fixed_device and not supported_nvme_mapping:
                errors.append(
                    "AWS EBS bootstrap assumes a fixed guest device name instead of "
                    "resolving the attached volume by stable identity."
                )
            if has_volume_attachment and storage_bootstrap and "/etc/fstab" not in lower:
                errors.append(
                    "AWS EBS mount bootstrap does not persist the filesystem mount in "
                    "/etc/fstab for instance restart."
                )
        for name, content in sorted(files.items()):
            if not name.endswith((".sh", ".tpl", ".tftpl")):
                continue
            if re.search(r"(?im)^\s*docker\s+run\s+-d\b", content) and not re.search(
                r"(?i)(?:^|\s)--restart(?:=|\s+)", content
            ):
                errors.append(f"{name}: long-running Docker bootstrap has no restart policy.")
            mount_roots: set[str] = set()
            for line in content.splitlines():
                try:
                    tokens = shlex.split(line, comments=True, posix=True)
                except ValueError:
                    continue
                if tokens[:1] == ["sudo"]:
                    tokens = tokens[1:]
                if tokens[:1] != ["mount"]:
                    continue
                operands = [token for token in tokens[1:] if not token.startswith("-")]
                if operands:
                    mount_roots.add(operands[-1])
            for bind_match in re.finditer(
                r"(?i)(?:^|\s)(?:-v|--volume(?:=|\s+))\s*"
                r"([^:\s]+):([^:\s]+)",
                content,
            ):
                source = bind_match.group(1).strip("\"'")
                child_path_used = re.search(rf"{re.escape(source)}/[^\s:\"']+", content)
                if source in mount_roots and child_path_used is None:
                    errors.append(
                        f"{name}: container data bind uses the filesystem mount root "
                        f"{source} without a dedicated runtime data child directory."
                    )
            for reload_match in re.finditer(
                r"(?im)^\s*(?:sudo\s+)?systemctl\s+reload\s+([A-Za-z0-9_.@-]+)\b",
                content,
            ):
                service = reload_match.group(1)
                earlier = content[: reload_match.start()]
                started = re.search(
                    rf"(?im)^\s*(?:sudo\s+)?systemctl\s+(?:"
                    rf"(?:start|restart)\s+{re.escape(service)}|"
                    rf"enable\s+--now\s+{re.escape(service)}|"
                    rf"--now\s+enable\s+{re.escape(service)})\b",
                    earlier,
                )
                if started is None:
                    errors.append(
                        f"{name}: systemd service {service} is reloaded before it is started."
                    )
        return errors

    @staticmethod
    def _provider_contract_errors(
        files: dict[str, str], expected_provider: str | None
    ) -> list[str]:
        if not expected_provider:
            return []
        contract = PINNED_PROVIDERS.get(expected_provider)
        if contract is None:
            return [f"unsupported provider: {expected_provider}"]
        text = "\n".join(files.values())
        sources: set[str] = set()
        versions: set[str] = set()
        for name, content in files.items():
            if not name.endswith(".tf"):
                continue
            parsed = hcl2.load(io.StringIO(content))
            for terraform in parsed.get("terraform", []):
                for required in terraform.get("required_providers", []):
                    for alias, provider_contract in required.items():
                        if alias == "__is_block__" or not isinstance(provider_contract, dict):
                            continue
                        source = str(provider_contract.get("source") or "").strip('"')
                        version = str(provider_contract.get("version") or "").strip('"')
                        version = re.sub(r"^=\s*", "", version)
                        if source:
                            sources.add(source)
                        if version:
                            versions.add(version)
        errors = []
        if sources != {contract["source"]}:
            errors.append(
                f"required_providers source must be exactly {contract['source']}: "
                f"found {sorted(sources)}"
            )
        if versions != {contract["version"]}:
            errors.append(
                f"provider version must be exactly {contract['version']}: found {sorted(versions)}"
            )
        prefixes = {"aws": "aws_", "azure": "azurerm_", "gcp": "google_"}
        expected_prefix = prefixes[expected_provider]
        unsupported_types: set[str] = set()
        for name, content in files.items():
            if not name.endswith(".tf"):
                continue
            parsed = hcl2.load(io.StringIO(content))
            for block_type in ("resource", "data"):
                for declaration in parsed.get(block_type, []):
                    for provider_type in declaration:
                        normalized_type = str(provider_type).strip('"')
                        if provider_type != "__is_block__" and not normalized_type.startswith(
                            expected_prefix
                        ):
                            unsupported_types.add(normalized_type)
        if unsupported_types:
            errors.append(
                "resource and data types must use only the selected provider "
                f"namespace {expected_prefix}: found {sorted(unsupported_types)}"
            )
        foreign = {
            prefix
            for provider, prefix in prefixes.items()
            if provider != expected_provider
            and re.search(rf'\b(?:resource|data)\s+"{re.escape(prefix)}', text)
        }
        if foreign:
            errors.append(f"foreign provider resource prefixes: {sorted(foreign)}")
        return errors

    @staticmethod
    def _ensure_provider_contract(
        files: dict[str, str], expected_provider: str | None, region: str = ""
    ) -> dict[str, str]:
        """Add missing system-owned provider version and deterministic configuration."""
        if not expected_provider:
            return files
        contract = PINNED_PROVIDERS.get(expected_provider)
        if contract is None:
            return files
        managed_name = "easydep-provider.tf"
        candidate_files = dict(files)
        if managed_name in candidate_files:
            parsed_managed = hcl2.load(io.StringIO(candidate_files[managed_name]))
            unsupported = set(parsed_managed) - {"terraform", "provider"}
            if unsupported:
                raise ValueError(
                    "IaC generator used the system-managed provider file for "
                    f"unsupported blocks: {sorted(unsupported)}"
                )
            # Provider declarations are policy-owned.  A semantically narrow collision
            # can be normalized without accepting LLM-selected versions or settings.
            candidate_files.pop(managed_name)
        alias = contract["source"].split("/", 1)[1]
        required_provider_present = False
        provider_configuration_present = False
        declared_variables: set[str] = set()
        for name, content in candidate_files.items():
            if not name.endswith(".tf"):
                continue
            parsed = hcl2.load(io.StringIO(content))
            required_provider_present = required_provider_present or any(
                terraform.get("required_providers") for terraform in parsed.get("terraform", [])
            )
            provider_configuration_present = provider_configuration_present or any(
                raw_name.strip('"') == alias
                for block in parsed.get("provider") or []
                for raw_name in block
            )
            declared_variables.update(
                raw_name.strip('"') for block in parsed.get("variable") or [] for raw_name in block
            )
        provider_block = ""
        if not provider_configuration_present:
            if expected_provider == "aws" and region:
                provider_block = f'provider "aws" {{\n  region = {json.dumps(region)}\n}}\n'
            elif expected_provider == "azure":
                provider_block = 'provider "azurerm" {\n  features {}\n}\n'
            elif expected_provider == "gcp" and region:
                project_variable = next(
                    (name for name in ("project_id", "project") if name in declared_variables),
                    None,
                )
                project_line = f"  project = var.{project_variable}\n" if project_variable else ""
                provider_block = (
                    'provider "google" {\n'
                    + project_line
                    + f"  region = {json.dumps(region)}\n"
                    + "}\n"
                )
        if required_provider_present and not provider_block:
            return candidate_files
        required_block = ""
        if not required_provider_present:
            required_block = (
                "terraform {\n"
                "  required_providers {\n"
                f"    {alias} = {{\n"
                f'      source  = "{contract["source"]}"\n'
                f'      version = "= {contract["version"]}"\n'
                "    }\n"
                "  }\n"
                "}\n"
            )
        return {
            **candidate_files,
            managed_name: required_block + provider_block,
        }

    def _provider_validation(self, files: dict[str, str]) -> dict[str, Any]:
        """공급자 플러그인 스키마까지 격리 검증하고 임시 파일을 모두 폐기한다."""
        configured = os.getenv("EVALUATION_TOFU_PATH") or os.getenv("EASYDEP_TERRAFORM_PATH")
        executable = (
            configured
            if configured and Path(configured).is_file()
            else shutil.which("tofu") or shutil.which("terraform")
        )
        if not executable:
            return {
                "status": "skipped",
                "reason": "No OpenTofu or Terraform executable is available.",
            }
        plugin_cache_dir = Path(
            settings.easydep_tofu_plugin_cache or ".easydep/provider-plugin-cache"
        ).resolve()
        environment = provider_cache_environment(plugin_cache_dir)
        contract_errors = self._provider_contract_errors(files, self._expected_provider)
        if contract_errors:
            return {
                "status": "failed",
                "stage": "providerContract",
                "errors": contract_errors,
                "reports": [],
            }
        cache_audit = audit_provider_cache(plugin_cache_dir)
        expected_contract = PINNED_PROVIDERS.get(self._expected_provider or "")
        expected_package = (
            {
                "provider": expected_contract["source"].split("/", 1)[1],
                "version": expected_contract["version"],
            }
            if expected_contract
            else None
        )
        if expected_package and expected_package not in cache_audit["packages"]:
            return {
                "status": "failed",
                "stage": "providerCachePolicy",
                "errors": [f"pinned provider is absent: {expected_package}"],
                "reports": [],
            }
        validation_temp = plugin_cache_dir.parent / "provider-validation-temp"
        validation_temp.mkdir(parents=True, exist_ok=True)
        with _inheriting_temporary_directory(validation_temp, "easydep-iac-validation-") as root:
            for name, content in files.items():
                (root / name).write_text(content, encoding="utf-8")
            cli_config = root / "provider-mirror.tfrc"
            cli_config.write_text(provider_mirror_configuration(plugin_cache_dir), encoding="utf-8")
            environment.pop("TF_PLUGIN_CACHE_DIR", None)
            environment["TF_CLI_CONFIG_FILE"] = str(cli_config)
            commands = [
                [executable, "init", "-backend=false", "-input=false", "-no-color"],
                [executable, "validate", "-no-color"],
            ]
            reports = []
            for command in commands:
                started_at = datetime.now(UTC)
                started = perf_counter()
                completed = run_process_tree(
                    command,
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=180,
                )
                report = {
                    "command": command[1],
                    "exitCode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                    "startedAt": started_at.isoformat(),
                    "finishedAt": datetime.now(UTC).isoformat(),
                    "elapsedSeconds": round(perf_counter() - started, 6),
                }
                reports.append(report)
                if completed.returncode:
                    return {"status": "failed", "reports": reports}
            plan_path = root / "easydep.tfplan"
            plan_command = [
                executable,
                "plan",
                "-refresh=false",
                "-input=false",
                "-lock=false",
                "-no-color",
                f"-out={plan_path.name}",
            ]
            plan_started_at = datetime.now(UTC)
            plan_started = perf_counter()
            plan_inputs = self._plan_variable_environment(files)
            credential_environment: dict[str, str] = {}
            credential_source: str | None = None
            if self._expected_provider == "gcp" and not any(
                environment.get(name)
                for name in (
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "GOOGLE_OAUTH_ACCESS_TOKEN",
                )
            ):
                gcloud = shutil.which("gcloud")
                if gcloud:
                    credential_started_at = datetime.now(UTC)
                    credential_started = perf_counter()
                    credential = run_process_tree(
                        [gcloud, "auth", "print-access-token"],
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                        timeout=60,
                    )
                    reports.append(
                        {
                            "command": "credential-bridge",
                            "exitCode": credential.returncode,
                            "stdout": "",
                            "stderr": credential.stderr[-1000:],
                            "startedAt": credential_started_at.isoformat(),
                            "finishedAt": datetime.now(UTC).isoformat(),
                            "elapsedSeconds": round(perf_counter() - credential_started, 6),
                        }
                    )
                    access_token = credential.stdout.strip()
                    if credential.returncode == 0 and access_token:
                        credential_environment["GOOGLE_OAUTH_ACCESS_TOKEN"] = access_token
                        credential_source = "gcloud-cli-access-token"
            planned = run_process_tree(
                plan_command,
                cwd=root,
                env={**environment, **plan_inputs, **credential_environment},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=300,
            )
            reports.append(
                {
                    "command": "plan",
                    "exitCode": planned.returncode,
                    "stdout": "",
                    "stderr": planned.stderr[-4000:],
                    "startedAt": plan_started_at.isoformat(),
                    "finishedAt": datetime.now(UTC).isoformat(),
                    "elapsedSeconds": round(perf_counter() - plan_started, 6),
                }
            )
            terraform_plan: dict[str, Any]
            if planned.returncode:
                terraform_plan = {
                    "status": "not-observed",
                    "reason": "Terraform plan could not be produced with the generated defaults.",
                    "exitCode": planned.returncode,
                }
            else:
                show_started_at = datetime.now(UTC)
                show_started = perf_counter()
                shown = run_process_tree(
                    [executable, "show", "-json", plan_path.name],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=180,
                )
                reports.append(
                    {
                        "command": "show-json",
                        "exitCode": shown.returncode,
                        "stdout": "",
                        "stderr": shown.stderr[-4000:],
                        "startedAt": show_started_at.isoformat(),
                        "finishedAt": datetime.now(UTC).isoformat(),
                        "elapsedSeconds": round(perf_counter() - show_started, 6),
                    }
                )
                if shown.returncode:
                    terraform_plan = {
                        "status": "not-observed",
                        "reason": "Terraform Plan JSON could not be read.",
                        "exitCode": shown.returncode,
                    }
                else:
                    try:
                        raw_plan = shown.stdout.encode("utf-8")
                        terraform_plan = {
                            **observe_terraform_plan(json.loads(shown.stdout)),
                            "sha256": hashlib.sha256(raw_plan).hexdigest(),
                            "ephemeralInputVariables": sorted(
                                name.removeprefix("TF_VAR_") for name in plan_inputs
                            ),
                            "credentialSource": credential_source,
                        }
                    except (TypeError, ValueError, json.JSONDecodeError) as error:
                        terraform_plan = {
                            "status": "not-observed",
                            "reason": f"Terraform Plan JSON parsing failed: {type(error).__name__}",
                        }
            cache_audit = audit_provider_cache(plugin_cache_dir)
            if cache_audit["status"] != "passed":
                return {
                    "status": "failed",
                    "stage": "providerCachePolicy",
                    "errors": [f"unapproved provider cache entry: {cache_audit['unexpected']}"],
                    "reports": reports,
                }
            lock_path = root / ".terraform.lock.hcl"
            lock_content = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else ""
            selections = [
                {"source": source, "version": version}
                for source, version in re.findall(
                    r'provider "registry\.opentofu\.org/([^\"]+)"\s*\{.*?version\s*=\s*"([^"]+)"',
                    lock_content,
                    flags=re.DOTALL,
                )
            ]
            plan_available = terraform_plan.get("status") == "available"
            plan_error = next(
                (
                    str(report.get("stderr") or report.get("stdout") or "").strip()
                    for report in reversed(reports)
                    if report.get("exitCode")
                ),
                "",
            )
            return {
                "status": "passed" if plan_available else "failed",
                "stage": None if plan_available else "terraformPlan",
                "errors": (
                    []
                    if plan_available
                    else [
                        plan_error
                        or str(
                            terraform_plan.get("reason") or "Terraform Plan JSON was not observed."
                        )
                    ]
                ),
                "reports": reports,
                "pluginCache": str(plugin_cache_dir),
                "pluginCachePolicy": cache_audit,
                "providerLock": {
                    "sha256": hashlib.sha256(lock_content.encode("utf-8")).hexdigest(),
                    "selections": selections,
                },
                "terraformPlan": terraform_plan,
                "_lockFileContent": lock_content,
            }

    @staticmethod
    def _plan_variable_environment(files: dict[str, str]) -> dict[str, str]:
        """Supply non-persisted sentinels for required variables during static planning."""
        environment: dict[str, str] = {}
        for name, content in sorted(files.items()):
            if not name.endswith(".tf"):
                continue
            try:
                parsed = hcl2.load(io.StringIO(content))
            except Exception:
                continue
            for block in parsed.get("variable") or []:
                for raw_name, body in block.items():
                    body = body or {}
                    if "default" in body:
                        continue
                    variable_name = raw_name.strip('"')
                    raw_type = str(body.get("type") or "string").lower()
                    normalized_name = variable_name.lower()
                    if "public_key" in normalized_name or (
                        "ssh" in normalized_name and "key" in normalized_name
                    ):
                        value = PLAN_ONLY_SSH_PUBLIC_KEY
                    elif normalized_name in {"ami", "ami_id", "image_id"}:
                        value = "ami-00000000000000000"
                    elif "bool" in raw_type:
                        value = "false"
                    elif "number" in raw_type:
                        value = "1"
                    elif any(item in raw_type for item in ("list", "set", "tuple")):
                        value = "[]"
                    elif any(item in raw_type for item in ("map", "object")):
                        value = "{}"
                    else:
                        value = "easydep-plan-only-value-000000000000"
                    environment[f"TF_VAR_{variable_name}"] = value
        return environment

    @staticmethod
    def _validation_errors(validation: dict[str, Any]) -> list[str]:
        direct = validation.get("errors") or []
        if direct:
            return [str(error) for error in direct]
        errors = []
        for report in validation.get("reports") or []:
            if report.get("exitCode"):
                message = str(report.get("stderr") or report.get("stdout") or "")
                errors.append(message[-4000:])
        return errors or [str(validation.get("reason") or "Provider validation failed.")]

    @staticmethod
    def _with_binding_validation(
        validation: dict[str, Any],
        files: dict[str, str],
        *,
        application_port: int,
        mount_path: str | None,
        provider: str,
        expected_vm_spec: str | None,
        managed_group_required: bool,
        resource_plan: dict[str, Any],
    ) -> dict[str, Any]:
        if validation.get("status") == "failed":
            return validation
        report = validate_iac_bindings(
            files,
            application_port=application_port,
            mount_path=mount_path,
        )
        vm_report = validate_vm_selection_binding(
            files,
            provider=provider,
            expected_spec_name=expected_vm_spec,
        )
        managed_group_report = validate_managed_group_binding(
            files,
            provider=provider,
            required=managed_group_required,
        )
        resource_plan_report = validate_resource_plan_binding(
            files,
            resource_plan=resource_plan,
        )
        plan_report = validate_resource_plan_against_plan(
            resource_plan,
            validation.get("terraformPlan") or {},
        )
        authoritative_plan = bool(
            resource_plan.get("deploymentTopology")
            and resource_plan.get("providerProjectionPolicy")
        )
        plan_gate_passed = (
            plan_report["status"] == "passed"
            if authoritative_plan
            else plan_report["status"] != "failed"
        )
        if (
            report["status"] != "failed"
            and vm_report["status"] != "failed"
            and managed_group_report["status"] != "failed"
            and resource_plan_report["status"] != "failed"
            and plan_gate_passed
        ):
            return {
                **validation,
                "bindingReport": report,
                "vmSelectionBindingReport": vm_report,
                "managedGroupBindingReport": managed_group_report,
                "resourcePlanBindingReport": resource_plan_report,
                "resourcePlanTerraformPlanReport": plan_report,
            }
        diagnostics = [
            *report["diagnostics"],
            *vm_report["diagnostics"],
            *managed_group_report["diagnostics"],
            *resource_plan_report["diagnostics"],
            *plan_report["diagnostics"],
        ]
        if authoritative_plan and plan_report["status"] == "not-observed":
            diagnostics.append(
                {
                    "code": "BIND-RESOURCE-PLAN-JSON-UNOBSERVED",
                    "message": (
                        "Terraform Plan JSON is required before generated IaC can be promoted."
                    ),
                    "details": {"reason": plan_report.get("reason")},
                }
            )
        return {
            "status": "failed",
            "stage": "deploymentBinding",
            "errors": [item["message"] for item in diagnostics],
            "bindingReport": report,
            "vmSelectionBindingReport": vm_report,
            "managedGroupBindingReport": managed_group_report,
            "resourcePlanBindingReport": resource_plan_report,
            "resourcePlanTerraformPlanReport": plan_report,
            "_lockFileContent": validation.get("_lockFileContent", ""),
        }

    @staticmethod
    def _ensure_container_files(application: Path, application_port: int = 8080) -> list[str]:
        created: list[str] = []
        dockerfile = DOCKERFILE.format(application_port=application_port)
        for name, content in (("Dockerfile", dockerfile), (".dockerignore", DOCKERIGNORE)):
            target = application / name
            if target.is_file():
                continue
            target.write_text(content, encoding="utf-8")
            created.append(name)
        return created

    @staticmethod
    def _promote_infra_files(target: Path, files: dict[str, str], lock_content: str) -> list[str]:
        """검증된 VM delivery 소유 파일 집합을 이전 산출물과 섞이지 않게 교체한다."""
        target.parent.mkdir(parents=True, exist_ok=True)
        # tempfile.mkdtemp creates a protected ACL on Windows.  A normal mkdir under
        # the workspace inherits the sandbox/user ACL and remains readable after the
        # atomic promotion.
        for _ in range(10):
            staging = target.parent / f".easydep-infra-promotion-{secrets.token_hex(8)}"
            try:
                staging.mkdir()
                break
            except FileExistsError:
                continue
        else:  # pragma: no cover - cryptographic collision guard
            raise FileExistsError("Could not allocate an infrastructure staging directory")
        backup = staging.with_name(f"{staging.name}-previous")
        try:
            if target.is_dir():
                for child in target.iterdir():
                    owned = child.is_file() and (
                        SAFE_FILE.fullmatch(child.name) is not None
                        or child.name == ".terraform.lock.hcl"
                    )
                    if owned or child.name == ".terraform":
                        continue
                    destination = staging / child.name
                    if child.is_dir():
                        shutil.copytree(child, destination)
                    else:
                        shutil.copy2(child, destination)
            for name, content in files.items():
                (staging / name).write_text(content, encoding="utf-8")
            promoted = sorted(files)
            if lock_content:
                (staging / ".terraform.lock.hcl").write_text(lock_content, encoding="utf-8")
                promoted.append(".terraform.lock.hcl")
            if target.exists():
                target.rename(backup)
            try:
                staging.rename(target)
            except BaseException:
                if backup.exists() and not target.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
            return sorted(promoted)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def _validate_container_port(application: Path, application_port: int) -> None:
        dockerfile = application / "Dockerfile"
        content = dockerfile.read_text(encoding="utf-8", errors="replace")
        exposed = {int(value) for value in re.findall(r"(?im)^\s*EXPOSE\s+(\d+)", content)}
        if application_port not in exposed:
            raise BindingMismatchError(
                "BIND-PORT-001",
                f"Dockerfile does not expose the contracted application port {application_port}.",
            )

    @staticmethod
    def _dependency_input(cloud_design_result: dict[str, Any]) -> dict[str, Any]:
        """Expose only language-neutral machine fields to the English IaC agent."""
        intent = cloud_design_result.get("infra_intent") or {}
        if intent:
            capability_ids = sorted(
                {
                    capability_id
                    for realization in intent.get("capabilityRealizations") or []
                    for capability_id in realization.get("capabilityIds") or []
                }
            )
            bundle = query_knowledge(
                provider=str(intent.get("csp") or ""),
                anchors=list(intent.get("startResources") or []),
                capability_ids=capability_ids,
            )
            official = bundle["officialDependencies"]
            resource_ids = set(intent.get("startResources") or [])
            for item in official:
                resource_ids.update((item.get("from"), item.get("to")))
            resource_ids.discard(None)
            return {
                "csp": intent.get("csp"),
                "region": intent.get("region"),
                "coverage": cloud_design_result.get("dependency_coverage") or {},
                "startResources": intent.get("startResources") or [],
                "resources": [{"id": item} for item in sorted(resource_ids)],
                "edges": [
                    {
                        "from": item.get("from"),
                        "to": item.get("to"),
                        "semantics": item.get("semantics") or [],
                        "existenceDecision": item.get("existenceDecision"),
                        "necessityDecision": item.get("necessityDecision"),
                    }
                    for item in official
                ],
                "capabilityRealizations": bundle["capabilityRealizations"],
                "knowledgeSnapshot": bundle["snapshot"],
                "evidencePolicy": {
                    "legacyClaimsExcluded": True,
                    "candidateNecessityIsNotMandatory": True,
                },
            }
        plan = cloud_design_result.get("dependency_plan") or {}
        return {
            "csp": plan.get("csp"),
            "region": plan.get("region"),
            "coverage": cloud_design_result.get("dependency_coverage") or {},
            "resources": [
                {"id": item.get("id"), "provisioningStatus": item.get("provisioningStatus")}
                for item in plan.get("nodes") or []
            ],
            "edges": [
                {
                    "from": item.get("from"),
                    "to": item.get("to"),
                    "relation": item.get("relation"),
                }
                for item in plan.get("edges") or []
            ],
        }

    def generate(
        self,
        *,
        requirements_result: dict[str, Any],
        cloud_design_result: dict[str, Any],
        implementation_result: dict[str, Any],
        application_runtime_contract: dict[str, Any] | None = None,
        cloud_capability_contract: dict[str, Any] | None = None,
        deployment_binding_contract: dict[str, Any] | None = None,
        enable_repair_feedback: bool = True,
        enable_consistency_validator: bool = True,
        resource_constraints_text: str = "",
    ) -> dict[str, Any]:
        self.last_timing_events = []
        resource_spec = resolve_resource_spec(
            requirements_result.get("resource_spec") or {},
            resource_constraints_text,
        )
        deployment_needs = accepted_needs(requirements_result.get("deployment_needs") or {})
        persistent_storage_required = requires_persistent_storage(
            requirements_result.get("deployment_needs") or {}
        )
        topology_policy = (
            cloud_design_result.get("topology_policy")
            or (cloud_design_result.get("resource_plan") or {}).get("deploymentTopology")
            or {}
        )
        projection_policy = (
            cloud_design_result.get("provider_projection_policy")
            or (cloud_design_result.get("resource_plan") or {}).get("providerProjectionPolicy")
            or {}
        )
        app_contract = ApplicationRuntimeContract.model_validate(application_runtime_contract or {})
        resource_plan = (
            cloud_design_result.get("resource_plan")
            or cloud_design_result.get("deployment_diagram_model")
            or {}
        )
        if resource_plan.get("workloads") and application_runtime_contract is not None:
            resource_plan = bind_application_runtime(
                resource_plan,
                app_contract.model_dump(mode="json", by_alias=True),
            )
        unresolved_plan = list(resource_plan.get("unresolved") or [])
        if unresolved_plan:
            fields = sorted(
                str(item.get("field") or "unknown")
                for item in unresolved_plan
                if isinstance(item, dict)
            )
            raise BindingMismatchError(
                "RESOURCE-PLAN-UNRESOLVED",
                "ResourcePlan contains unresolved deployment decisions: "
                + ", ".join(fields or ["unknown"]),
            )
        vm_selection = select_vm_candidates(
            resource_spec,
            deployment_needs,
            projection_policy=projection_policy,
        )
        expected_vm_spec = (
            str((vm_selection.get("recommended") or {}).get("specName") or "") or None
        )
        provider = str(resource_spec.get("provider") or "").strip().lower()
        provider_region = str(resource_spec.get("region") or "").strip()
        self._expected_provider = provider
        cloud_contract = CloudCapabilityContract.model_validate(cloud_capability_contract or {})
        binding_contract = DeploymentBindingContract.model_validate(
            deployment_binding_contract or {}
        )
        application_port = int(contract_value(app_contract, "runtime.port", "port", 8080))
        if not 1 <= application_port <= 65535:
            raise BindingMismatchError(
                "BIND-PORT-001",
                f"Application port is outside the valid TCP range: {application_port}.",
            )
        mount_path = contract_value(cloud_contract, "cloud.storage.mount", "mountPath")
        persistence_owner = next(
            (
                decision.get("value")
                for decision in resource_plan.get("decisions") or []
                if decision.get("field") == "persistenceOwner"
            ),
            None,
        )
        owner_allocation: dict[str, Any] = next(
            (
                allocation
                for allocation in resource_plan.get("allocations") or []
                if allocation.get("workloadRef") == persistence_owner
            ),
            {},
        )
        application_mount_path = (
            mount_path
            if not persistence_owner
            or owner_allocation.get("computeRef") == resource_plan.get("computeNodeId")
            else None
        )
        prompt = json.dumps(
            {
                "resourceSpec": resource_spec,
                "deploymentNeeds": deployment_needs,
                "dependencyPlan": self._dependency_input(cloud_design_result),
                "resourcePlan": resource_plan,
                "vmSelection": vm_selection,
                "applicationPersistentStorageRequired": persistent_storage_required,
                "applicationRuntimeContract": app_contract.model_dump(mode="json", by_alias=True),
                "cloudCapabilityContract": cloud_contract.model_dump(mode="json", by_alias=True),
                "deploymentBindingContract": binding_contract.model_dump(
                    mode="json", by_alias=True
                ),
                "applicationPort": application_port,
                "applicationMountPath": application_mount_path,
                "persistenceOwner": persistence_owner,
                "persistenceBoundary": PERSISTENCE_INSTRUCTION,
                "deploymentTopology": topology_policy,
                "topologyBoundary": TOPOLOGY_INSTRUCTION,
                "providerCompatibility": PROVIDER_COMPATIBILITY.get(provider, {}),
                "providerBoundary": {
                    "allowedProviderSource": (PINNED_PROVIDERS.get(provider) or {}).get("source"),
                    "allowedProviderVersion": (PINNED_PROVIDERS.get(provider) or {}).get("version"),
                    "managedDeclarationFile": "easydep-provider.tf",
                    "policy": (
                        "Use only the selected CSP provider for resource and data blocks. "
                        "The system supplies required_providers in the managed declaration "
                        "file; do not generate that file or another required_providers block. "
                        "Do not introduce auxiliary providers. Prefer Terraform/OpenTofu "
                        "language built-ins such as templatefile() when they suffice."
                    ),
                },
            },
            ensure_ascii=False,
        )
        llm_calls = 1
        repair_events: list[dict[str, Any]] = []
        normalization_events: list[dict[str, Any]] = []

        def prepared_files(envelope: dict[str, Any], *, attempt: str) -> dict[str, str]:
            generated = self._files(envelope)
            normalized, events = self._normalize_generated_files(generated)
            normalization_events.extend({**event, "attempt": attempt} for event in events)
            return self._ensure_provider_contract(normalized, provider, provider_region)

        payload = json.loads(self._timed_invoke("iac.generate", prompt))
        if not isinstance(payload, dict):
            raise TypeError("IaC generator must return one JSON object")
        repaired = False
        try:
            payload = self._normalize_payload(payload)
            files = prepared_files(payload, attempt="initial")
        except (TypeError, ValueError) as error:
            if not enable_repair_feedback:
                raise
            repair_events.append({"stage": "outputEnvelope", "errors": [str(error)]})
            repair_prompt = json.dumps(
                {
                    "task": "repairTerraform",
                    "originalInput": json.loads(prompt),
                    "terraformEnvelope": payload,
                    "validationStage": "outputEnvelope",
                    "validationErrors": [str(error)],
                    "instruction": (
                        "Return the complete corrected terraformFiles envelope. "
                        "Use only safe flat file names. The only allowed extensions are "
                        ".tf, .tftpl, .tpl, and .sh."
                    ),
                },
                ensure_ascii=False,
            )
            repaired_payload = json.loads(self._timed_invoke("iac.repair", repair_prompt))
            llm_calls += 1
            if not isinstance(repaired_payload, dict):
                raise TypeError("IaC repair must return one JSON object")
            payload = self._normalize_payload(repaired_payload)
            files = prepared_files(payload, attempt="output-envelope-repair")
            repaired = True
        hcl_errors = self._timed_hcl_validation(files)
        runtime_bootstrap_errors = (
            [] if hcl_errors else self._runtime_bootstrap_errors(files, app_contract, provider)
        )
        errors = [*hcl_errors, *runtime_bootstrap_errors]
        validation = (
            {
                "status": "failed",
                "stage": ("hclPreflight" if hcl_errors else "runtimeBootstrap"),
                "errors": errors,
            }
            if errors
            else self._validate_provider_schema(files)
        )
        if enable_consistency_validator:
            validation = self._with_binding_validation(
                validation,
                files,
                application_port=application_port,
                mount_path=(str(application_mount_path) if application_mount_path else None),
                provider=provider,
                expected_vm_spec=expected_vm_spec,
                managed_group_required=(topology_policy.get("computeManagement") == "managedGroup"),
                resource_plan=resource_plan,
            )
        if not errors:
            self._record_provider_timings(validation, attempt="repair" if repaired else "initial")
        if validation.get("status") == "failed" and enable_repair_feedback and not repaired:
            repair_events.append(
                {
                    "stage": validation.get("stage") or "providerSchema",
                    "errors": self._validation_errors(validation),
                }
            )
            repair_prompt = json.dumps(
                {
                    "task": "repairTerraform",
                    "originalInput": json.loads(prompt),
                    "terraformFiles": files,
                    "validationStage": validation.get("stage") or "providerSchema",
                    "validationErrors": self._validation_errors(validation),
                    "instruction": (
                        "Return the complete corrected terraformFiles envelope. "
                        "Change only what is necessary to pass the reported validation stage."
                    ),
                },
                ensure_ascii=False,
            )
            repaired_payload = json.loads(self._timed_invoke("iac.repair", repair_prompt))
            llm_calls += 1
            if not isinstance(repaired_payload, dict):
                raise TypeError("IaC repair must return one JSON object")
            payload = self._normalize_payload(repaired_payload)
            files = prepared_files(payload, attempt="validation-repair")
            hcl_errors = self._timed_hcl_validation(files)
            runtime_bootstrap_errors = (
                [] if hcl_errors else self._runtime_bootstrap_errors(files, app_contract, provider)
            )
            errors = [*hcl_errors, *runtime_bootstrap_errors]
            if errors:
                raise ValueError(
                    "Generated Terraform failed static preflight after one repair: "
                    + "; ".join(errors)
                )
            validation = self._validate_provider_schema(files)
            if enable_consistency_validator:
                validation = self._with_binding_validation(
                    validation,
                    files,
                    application_port=application_port,
                    mount_path=(str(application_mount_path) if application_mount_path else None),
                    provider=provider,
                    expected_vm_spec=expected_vm_spec,
                    managed_group_required=(
                        topology_policy.get("computeManagement") == "managedGroup"
                    ),
                    resource_plan=resource_plan,
                )
            self._record_provider_timings(validation, attempt="repair")
            repaired = True
        if validation.get("status") == "failed" and enable_repair_feedback:
            stage = validation.get("stage") or "providerSchema"
            if stage == "deploymentBinding":
                report_diagnostics: list[dict[str, Any]] = []
                for report_name in (
                    "bindingReport",
                    "vmSelectionBindingReport",
                    "managedGroupBindingReport",
                    "resourcePlanBindingReport",
                    "resourcePlanTerraformPlanReport",
                ):
                    report = validation.get(report_name)
                    if isinstance(report, dict):
                        report_diagnostics.extend(report.get("diagnostics") or [])
                code = str(
                    (report_diagnostics[0] if report_diagnostics else {}).get("code")
                    or "CLOUD-PROJ-001"
                )
                raise BindingMismatchError(
                    code,
                    "Generated Terraform failed deployment binding validation after one repair: "
                    + "; ".join(self._validation_errors(validation)),
                )
            raise ValueError(
                f"Generated Terraform failed {stage} validation after one repair: "
                + "; ".join(self._validation_errors(validation))
            )
        lock_content = str(validation.pop("_lockFileContent", ""))
        target = self._target(implementation_result)
        promoted_files = self._promote_infra_files(target, files, lock_content)
        container_files = self._ensure_container_files(target.parent, application_port)
        if enable_consistency_validator:
            self._validate_container_port(target.parent, application_port)
        authoritative_plan = bool(
            resource_plan.get("deploymentTopology")
            and resource_plan.get("providerProjectionPolicy")
        )
        return {
            "status": "completed",
            "method": "same-llm-structured-iac-generation",
            "llmCalls": llm_calls,
            "timingEvents": self.last_timing_events,
            "cloudKbProvided": bool(cloud_design_result.get("kb_used")),
            "directory": str(target),
            "files": sorted(promoted_files),
            "containerFilesCreated": container_files,
            "preflight": {
                "status": ("passed" if validation.get("status") != "failed" else "failed-observed"),
                "repaired": repaired,
                "repairFeedbackEnabled": enable_repair_feedback,
                "consistencyValidatorEnabled": enable_consistency_validator,
                "providerValidation": validation,
            },
            "deploymentNotes": payload.get("deploymentNotes") or [],
            "vmSelection": vm_selection,
            "repairEvents": repair_events,
            "normalizationEvents": normalization_events,
            "resourcePlan": resource_plan,
            "resourcePlanDigest": (
                resource_plan_digest(resource_plan) if authoritative_plan else None
            ),
            "deploymentDiagramPuml": (
                deployment_bundle_runtime_puml(
                    {
                        "schemaVersion": "easydep-deployment-diagram/v1",
                        "mode": "single",
                        "logicalModel": {},
                        "resourceSpec": resource_spec,
                        "projections": [
                            {
                                "status": "completed",
                                "provider": provider,
                                "region": str(
                                    resource_plan.get("region") or resource_spec.get("region") or ""
                                ),
                                "topology": topology_policy,
                                "resourcePlan": resource_plan,
                            }
                        ],
                    }
                )
                if authoritative_plan
                else ""
            ),
        }
