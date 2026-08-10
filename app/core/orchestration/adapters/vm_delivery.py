"""Generate Docker-on-VM Terraform at the implementation boundary."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any

import hcl2

from app.core.cloudkb.depkb.knowledge_access import query_knowledge
from app.core.cloudkb.depkb.provider_cache import (
    PINNED_PROVIDERS,
    audit_provider_cache,
    provider_cache_environment,
    provider_mirror_configuration,
)
from app.core.orchestration.app_cloud_contracts import (
    ApplicationRuntimeContract,
    CloudCapabilityContract,
    DeploymentBindingContract,
    contract_value,
)
from app.core.orchestration.iac_binding_validation import (
    validate_iac_bindings,
    validate_vm_selection_binding,
)
from app.core.orchestration.process import run_process_tree
from app.core.orchestration.provider_target import resolve_resource_spec
from app.core.orchestration.vm_selection import select_vm_candidates
from app.requirements.capability_contract import (
    accepted_needs,
    requires_persistent_storage,
)

SYSTEM_PROMPT = """You generate deployable Terraform for one Docker application on Linux VMs.
Use only the supplied structured requirements and dependency plan. Support only AWS, Azure,
or GCP. Do not use Kubernetes, managed application platforms,
managed databases, VPNs, or serverless services. Provision every resource needed by the VM,
network, external HTTPS entry, optional persistent data disk, and optional load balancer.
Interpret dependency evidence conservatively: `existenceDecision=confirmed` proves that a
reference relation exists, while only `necessityDecision=confirmed` or `documented` supports a
necessity claim. Never turn `candidate` or `notAssessed` necessity into a universal mandatory
dependency. Components of a supplied capability realization are required for that selected
realization, without implying that they are universally required for every provider deployment.
Items in dependencyPlan.coverage.unmodeledAcceptedNeeds are requirements not modeled by the
dependency knowledge base. Do not claim that the dependency plan satisfies or evidences them;
use deploymentNeeds as their requirement source.
Opening firewall port 443 alone is not HTTPS: configure real TLS termination with an HTTPS/SSL
listener or a VM-side reverse proxy and certificate/domain variables.
Use cloud-init/user-data to install Docker, run the supplied container image on the requested
port, expose the health path, and mount persistent storage when required. Never embed
credentials. Prefer variables for image IDs, project/subscription identifiers, certificate
material, and container image. Return one JSON object with `terraformFiles`, a map from safe
flat file names to complete contents. It must contain at least one .tf file and may include
.tftpl, .tpl, or .sh files referenced by Terraform. Also return `deploymentNotes`, a short
array. Do not
return Markdown fences or any non-JSON text. All .tf files form one Terraform module: never
declare the same resource, variable, output, data source, or module block more than once.
Inside files consumed by Terraform `templatefile`, escape shell variable interpolation as
`$${NAME}` or avoid shell variables; `${NAME}` is reserved for a key explicitly supplied in
the templatefile vars map.
The exact envelope is {"terraformFiles":{"main.tf":"..."},"deploymentNotes":["..."]};
deploymentNotes must never be placed inside terraformFiles."""

VM_SELECTION_INSTRUCTION = """When vmSelection.status is `selected`, use its recommended
specName as the VM instance type/size. Declare it as a literal or as a Terraform variable with
that exact default so independent evaluation can verify the choice. When selection is deferred,
do not claim that an arbitrary VM size satisfies capacity or budget."""

PERSISTENCE_INSTRUCTION = """Create a separate application data disk only when
applicationPersistentStorageRequired is true. A provider's mandatory VM boot disk is not an
application data disk and should remain an inline boot-disk setting. Format and mount the data
disk on the guest, then bind that guest path into the Docker container at exactly
applicationMountPath. The guest source path may differ from the container target path. When the
guest initializes, format the disk only when no filesystem exists; bootstrap must be idempotent
and must never erase an already formatted persistent disk. Select the attached disk by a stable
provider device identity, never by taking the first enumerated block device, and use a bounded
wait for the attachment to become visible before formatting or mounting it. When the value is
false, do not create, attach, mount, or advertise a separate persistent disk."""

PROVIDER_COMPATIBILITY = {
    "azure": {
        "providerConstraint": "hashicorp/azurerm 5.0.1",
        "rules": [
            {
                "resourceType": "azurerm_network_interface",
                "rule": "Do not set network_security_group_id on the NIC resource.",
            },
            {
                "resourceType": "azurerm_network_interface_security_group_association",
                "rule": (
                    "Associate a NIC and NSG with network_interface_id and "
                    "network_security_group_id."
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
}

SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:tf|tftpl|tpl|sh)$")

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
        try:
            result = self._invoke(prompt)
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
            base_url=os.getenv("BASE_URL"),
            timeout=600,
            max_retries=0,
        ).chat.completions.create(
            model=os.getenv("MODEL", "openai/gpt-oss-120b"),
            temperature=float(os.getenv("TEMPERATURE", "0")),
            seed=int(os.getenv("SEED", "42")),
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
                            identity = (block_type, str(provider_type), str(instance_name))
                            if identity in declarations:
                                errors.append(f"duplicate {' '.join(identity)}")
                            declarations.add(identity)
            for block_type in ("variable", "output", "module"):
                for declaration in parsed.get(block_type, []):
                    for instance_name in declaration:
                        identity = (block_type, str(instance_name))
                        if identity in declarations:
                            errors.append(f"duplicate {' '.join(identity)}")
                        declarations.add(identity)
        return sorted(set(errors))

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
        files: dict[str, str], expected_provider: str | None
    ) -> dict[str, str]:
        """Add the experiment-owned pinned provider declaration only when absent."""
        if not expected_provider:
            return files
        contract = PINNED_PROVIDERS.get(expected_provider)
        if contract is None:
            return files
        for name, content in files.items():
            if not name.endswith(".tf"):
                continue
            parsed = hcl2.load(io.StringIO(content))
            if any(
                terraform.get("required_providers")
                for terraform in parsed.get("terraform", [])
            ):
                return files
        alias = contract["source"].split("/", 1)[1]
        managed_name = "easydep-provider.tf"
        if managed_name in files:
            raise ValueError(
                f"IaC generator used the system-managed file name: {managed_name}"
            )
        return {
            **files,
            managed_name: (
                "terraform {\n"
                "  required_providers {\n"
                f"    {alias} = {{\n"
                f'      source  = "{contract["source"]}"\n'
                f'      version = "= {contract["version"]}"\n'
                "    }\n"
                "  }\n"
                "}\n"
            ),
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
        cache = Path(
            os.getenv("EASYDEP_TOFU_PLUGIN_CACHE") or ".easydep/provider-plugin-cache"
        ).resolve()
        environment = provider_cache_environment(cache)
        contract_errors = self._provider_contract_errors(files, self._expected_provider)
        if contract_errors:
            return {
                "status": "failed",
                "stage": "providerContract",
                "errors": contract_errors,
                "reports": [],
            }
        cache_audit = audit_provider_cache(cache)
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
        validation_temp = cache.parent / "provider-validation-temp"
        validation_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="easydep-iac-validation-", dir=validation_temp
        ) as directory:
            root = Path(directory)
            for name, content in files.items():
                (root / name).write_text(content, encoding="utf-8")
            cli_config = root / "provider-mirror.tfrc"
            cli_config.write_text(provider_mirror_configuration(cache), encoding="utf-8")
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
            cache_audit = audit_provider_cache(cache)
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
            return {
                "status": "passed",
                "reports": reports,
                "pluginCache": str(cache),
                "pluginCachePolicy": cache_audit,
                "providerLock": {
                    "sha256": hashlib.sha256(lock_content.encode("utf-8")).hexdigest(),
                    "selections": selections,
                },
                "_lockFileContent": lock_content,
            }

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
        if report["status"] != "failed" and vm_report["status"] != "failed":
            return {
                **validation,
                "bindingReport": report,
                "vmSelectionBindingReport": vm_report,
            }
        diagnostics = [
            *report["diagnostics"],
            *vm_report["diagnostics"],
        ]
        return {
            "status": "failed",
            "stage": "deploymentBinding",
            "errors": [item["message"] for item in diagnostics],
            "bindingReport": report,
            "vmSelectionBindingReport": vm_report,
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
    def _promote_infra_files(
        target: Path, files: dict[str, str], lock_content: str
    ) -> list[str]:
        """검증된 VM delivery 소유 파일 집합을 이전 산출물과 섞이지 않게 교체한다."""
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=".easydep-infra-promotion-", dir=target.parent)
        )
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
                (staging / ".terraform.lock.hcl").write_text(
                    lock_content, encoding="utf-8"
                )
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
        vm_selection = select_vm_candidates(resource_spec, deployment_needs)
        expected_vm_spec = (
            str((vm_selection.get("recommended") or {}).get("specName") or "") or None
        )
        provider = str(resource_spec.get("provider") or "").strip().lower()
        self._expected_provider = provider
        app_contract = ApplicationRuntimeContract.model_validate(application_runtime_contract or {})
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
        prompt = json.dumps(
            {
                "resourceSpec": resource_spec,
                "deploymentNeeds": deployment_needs,
                "dependencyPlan": self._dependency_input(cloud_design_result),
                "vmSelection": vm_selection,
                "applicationPersistentStorageRequired": persistent_storage_required,
                "applicationRuntimeContract": app_contract.model_dump(mode="json", by_alias=True),
                "cloudCapabilityContract": cloud_contract.model_dump(mode="json", by_alias=True),
                "deploymentBindingContract": binding_contract.model_dump(
                    mode="json", by_alias=True
                ),
                "applicationPort": application_port,
                "applicationMountPath": mount_path,
                "persistenceBoundary": PERSISTENCE_INSTRUCTION,
                "providerCompatibility": PROVIDER_COMPATIBILITY.get(provider, {}),
                "providerBoundary": {
                    "allowedProviderSource": (PINNED_PROVIDERS.get(provider) or {}).get("source"),
                    "allowedProviderVersion": (PINNED_PROVIDERS.get(provider) or {}).get(
                        "version"
                    ),
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
        payload = json.loads(self._timed_invoke("iac.generate", prompt))
        if not isinstance(payload, dict):
            raise TypeError("IaC generator must return one JSON object")
        repaired = False
        try:
            payload = self._normalize_payload(payload)
            files = self._ensure_provider_contract(self._files(payload), provider)
        except (TypeError, ValueError) as error:
            if not enable_repair_feedback:
                raise
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
            files = self._ensure_provider_contract(self._files(payload), provider)
            repaired = True
        errors = self._timed_hcl_validation(files)
        validation = (
            {"status": "failed", "stage": "hclPreflight", "errors": errors}
            if errors
            else self._validate_provider_schema(files)
        )
        if enable_consistency_validator:
            validation = self._with_binding_validation(
                validation,
                files,
                application_port=application_port,
                mount_path=str(mount_path) if mount_path else None,
                provider=provider,
                expected_vm_spec=expected_vm_spec,
            )
        if not errors:
            self._record_provider_timings(validation, attempt="repair" if repaired else "initial")
        if validation.get("status") == "failed" and enable_repair_feedback and not repaired:
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
            files = self._ensure_provider_contract(self._files(payload), provider)
            errors = self._timed_hcl_validation(files)
            if errors:
                raise ValueError(
                    "Generated Terraform failed HCL preflight after one repair: "
                    + "; ".join(errors)
                )
            validation = self._validate_provider_schema(files)
            if enable_consistency_validator:
                validation = self._with_binding_validation(
                    validation,
                    files,
                    application_port=application_port,
                    mount_path=str(mount_path) if mount_path else None,
                    provider=provider,
                    expected_vm_spec=expected_vm_spec,
                )
            self._record_provider_timings(validation, attempt="repair")
            repaired = True
        if validation.get("status") == "failed" and enable_repair_feedback:
            stage = validation.get("stage") or "providerSchema"
            if stage == "deploymentBinding":
                report_diagnostics = (validation.get("bindingReport") or {}).get(
                    "diagnostics"
                ) or []
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
        }
