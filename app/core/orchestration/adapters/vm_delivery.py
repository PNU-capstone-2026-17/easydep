"""Generate Docker-on-VM Terraform at the implementation boundary."""

from __future__ import annotations

import io
import json
import os
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import hcl2

from app.core.orchestration.vm_selection import select_vm_candidates

SYSTEM_PROMPT = """You generate deployable Terraform for one Docker application on Linux VMs.
Use only the supplied structured requirements and dependency plan. Support only AWS, Azure,
or GCP. Do not use Kubernetes, managed application platforms,
managed databases, VPNs, or serverless services. Provision every resource needed by the VM,
network, external HTTPS entry, optional persistent data disk, and optional load balancer.
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
The exact envelope is {"terraformFiles":{"main.tf":"..."},"deploymentNotes":["..."]};
deploymentNotes must never be placed inside terraformFiles."""

VM_SELECTION_INSTRUCTION = """When vmSelection.status is `selected`, use its recommended
specName as the VM instance type/size. Declare it as a literal or as a Terraform variable with
that exact default so independent evaluation can verify the choice. When selection is deferred,
do not claim that an arbitrary VM size satisfies capacity or budget."""

PERSISTENCE_INSTRUCTION = """Create a separate application data disk only when
applicationPersistentStorageRequired is true. A provider's mandatory VM boot disk is not an
application data disk and should remain an inline boot-disk setting. When the value is false,
do not create, attach, mount, or advertise a separate persistent disk."""

SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:tf|tftpl|tpl|sh)$")

DOCKERFILE = """FROM gradle:8.14.2-jdk21 AS build
WORKDIR /workspace
COPY . .
RUN gradle bootJar --no-daemon

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /workspace/build/libs/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
"""

DOCKERIGNORE = """.git
.gradle
build
infra/.terraform
"""


class VmDeliveryAdapter:
    """LLM boundary shared by full and cloud-KB-ablation experiment arms."""

    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

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
        if "deploymentNotes" in payload or not isinstance(nested_notes, list) or not all(
            isinstance(note, str) for note in nested_notes
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
            except (ValueError, TypeError) as error:
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
    def _ensure_container_files(application: Path) -> list[str]:
        created: list[str] = []
        for name, content in (("Dockerfile", DOCKERFILE), (".dockerignore", DOCKERIGNORE)):
            target = application / name
            if target.is_file():
                continue
            target.write_text(content, encoding="utf-8")
            created.append(name)
        return created

    @staticmethod
    def _dependency_input(cloud_design_result: dict[str, Any]) -> dict[str, Any]:
        """Expose only language-neutral machine fields to the English IaC agent."""
        intent = cloud_design_result.get("infra_intent") or {}
        if intent:
            plan = cloud_design_result.get("dependency_plan") or {}
            return {
                "csp": intent.get("csp"),
                "region": intent.get("region"),
                "startResources": intent.get("startResources") or [],
                "resources": [
                    {
                        "id": item.get("id"),
                        "provisioningStatus": item.get("provisioningStatus"),
                    }
                    for item in intent.get("resources") or []
                ],
                "edges": [
                    {
                        "from": item.get("from"),
                        "to": item.get("to"),
                        "relation": item.get("relation"),
                    }
                    for item in plan.get("edges") or []
                ],
                "createOrder": intent.get("createOrder") or [],
                "deleteBlockedWhileAttached": intent.get("deleteBlockedWhileAttached") or [],
                "detachRequiredBeforeDelete": intent.get("detachRequiredBeforeDelete") or [],
                "runtimeDependencies": [
                    {"from": item[0], "to": item[1], "signal": item[2]}
                    for item in intent.get("runtimeRequiredForSignal") or []
                    if isinstance(item, (list, tuple)) and len(item) >= 3
                ],
                "machineConstraints": [
                    {
                        "subject": item.get("subject"),
                        "object": item.get("object"),
                        "rule": item.get("machine"),
                    }
                    for item in intent.get("constraints") or []
                    if item.get("machine") is not None
                ],
            }
        plan = cloud_design_result.get("dependency_plan") or {}
        return {
            "csp": plan.get("csp"),
            "region": plan.get("region"),
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
    ) -> dict[str, Any]:
        resource_spec = requirements_result.get("resource_spec") or {}
        deployment_needs = requirements_result.get("deployment_needs") or {}
        persistent_need = deployment_needs.get("persistent_storage") or {}
        vm_selection = select_vm_candidates(resource_spec, deployment_needs)
        prompt = json.dumps(
            {
                "resourceSpec": resource_spec,
                "deploymentNeeds": deployment_needs,
                "dependencyPlan": self._dependency_input(cloud_design_result),
                "vmSelection": vm_selection,
                "applicationPersistentStorageRequired": (
                    persistent_need.get("required") is True
                ),
            },
            ensure_ascii=False,
        )
        payload = json.loads(self._invoke(prompt))
        if not isinstance(payload, dict):
            raise TypeError("IaC generator must return one JSON object")
        payload = self._normalize_payload(payload)
        files = self._files(payload)
        errors = self._validate_files(files)
        if errors:
            raise ValueError("Generated Terraform failed HCL preflight: " + "; ".join(errors))
        target = self._target(implementation_result)
        target.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (target / name).write_text(content, encoding="utf-8")
        container_files = self._ensure_container_files(target.parent)
        return {
            "status": "completed",
            "method": "same-llm-structured-iac-generation",
            "cloudKbProvided": bool(cloud_design_result.get("kb_used")),
            "directory": str(target),
            "files": sorted(files),
            "containerFilesCreated": container_files,
            "preflight": {"status": "passed", "repaired": False},
            "deploymentNotes": payload.get("deploymentNotes") or [],
            "vmSelection": vm_selection,
        }
