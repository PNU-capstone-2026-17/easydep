"""Generate Docker-on-VM Terraform at the implementation boundary."""

from __future__ import annotations

import io
import re
import secrets
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import hcl2

from app.orchestration.app_cloud_contracts import (
    ApplicationRuntimeContract,
    contract_value,
)
from app.implementation.delivery.iac_renderer import render_open_tofu
from app.implementation.planning.provider_target import resolve_resource_spec
from app.implementation.planning.vm_selection import select_vm_candidates
from app.design.contracts.deployment import (
    RESOURCE_PLAN_SCHEMA,
    bind_runtime_contract,
    build_provider_resource_plan,
    deployment_bundle_runtime_puml,
)
from app.requirements.capability_contract import (
    accepted_needs,
)

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
    """Bind implementation observations and render the planned VM deployment."""

    def __init__(
        self,
        invoke: Callable[[str], str] | None = None,
        validate: Callable[[dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        # Retain constructor compatibility for orchestration wiring; neither callback
        # participates in deterministic ResourcePlan rendering.
        del invoke, validate
        self.last_timing_events: list[dict[str, Any]] = []
        self._expected_provider: str | None = None

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

    @staticmethod
    def _target(implementation_result: dict[str, Any]) -> Path:
        run_root = Path(str(implementation_result.get("run_root") or ""))
        application = run_root / "application"
        if not application.is_dir():
            raise ValueError(f"Generated application repository is absent: {application}")
        return application / "infra"

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
        del cloud_capability_contract, deployment_binding_contract, enable_repair_feedback
        self.last_timing_events = []
        diagram_bundle = dict(cloud_design_result.get("deployment_diagram_bundle") or {})
        if diagram_bundle.get("schemaVersion") != "easydep-deployment-diagram":
            raise BindingMismatchError(
                "DEPLOYMENT-DIAGRAM-MISSING",
                "Generate the WorkloadGraph deployment diagram before IaC generation.",
            )
        resource_spec = resolve_resource_spec(
            requirements_result.get("resource_spec") or {},
            resource_constraints_text,
        )
        deployment_needs = accepted_needs(requirements_result.get("deployment_needs") or {})
        projection_policy: dict[str, Any] = {}
        app_contract = ApplicationRuntimeContract.model_validate(application_runtime_contract or {})
        resource_plan = dict(cloud_design_result.get("resource_plan") or {})
        if resource_plan.get("schemaVersion") != RESOURCE_PLAN_SCHEMA:
            raise BindingMismatchError(
                "RESOURCE-PLAN-MISSING",
                "The deployment diagram must provide its deterministic ResourcePlan.",
            )
        graph = dict(cloud_design_result.get("workload_graph") or {})
        deployment_plan = dict(cloud_design_result.get("deployment_plan") or {})
        if not graph or not deployment_plan:
            raise BindingMismatchError(
                "RESOURCE-PLAN-SOURCES-MISSING",
                "ResourcePlan requires its WorkloadGraph and provider-neutral DeploymentPlan.",
            )
        if application_runtime_contract is not None:
            raw_contract = app_contract.model_dump(mode="json", by_alias=True)
            runtime_contracts = list((raw_contract.get("extensions") or {}).get("workloads") or [])
            if not runtime_contracts:
                generated = next(
                    (
                        item
                        for item in graph.get("workloads") or []
                        if (item.get("artifact") or {}).get("kind")
                        == "generatedApplication"
                    ),
                    None,
                )
                if generated is not None:
                    observed_interface = next(iter(generated.get("interfaces") or []), {})
                    observed: dict[str, Any] = {
                        "workloadId": generated.get("id"),
                        "interfaces": [],
                    }
                    image_digest = contract_value(
                        app_contract, "runtime.imageDigest", "imageDigest"
                    )
                    if image_digest:
                        observed["imageDigest"] = image_digest
                    if observed_interface:
                        observed["interfaces"].append(
                            {
                                "interfaceId": observed_interface.get("id"),
                                "port": contract_value(
                                    app_contract, "runtime.port", "port", 8080
                                ),
                                "healthPath": contract_value(
                                    app_contract,
                                    "runtime.healthPath",
                                    "healthPath",
                                ),
                            }
                        )
                    runtime_contracts = [observed]
            binding = bind_runtime_contract(graph, deployment_plan, runtime_contracts)
            if binding.get("status") != "bound":
                raise BindingMismatchError(
                    "RUNTIME-STRUCTURE-CHANGED",
                    "Implementation observations require deployment-diagram regeneration: "
                    + "; ".join(
                        str(item.get("reason") or "unknown")
                        for item in binding.get("issues") or []
                    ),
                )
            graph = dict(binding.get("workloadGraph") or {})
            deployment_plan = dict(binding.get("deploymentPlan") or {})
            resource_plan = build_provider_resource_plan(
                deployment_plan,
                graph,
                provider=str(resource_plan.get("provider") or ""),
                region=str(resource_plan.get("region") or ""),
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
        provider = str(resource_spec.get("provider") or "").strip().lower()
        self._expected_provider = provider
        application_port = int(contract_value(app_contract, "runtime.port", "port", 8080))
        if not 1 <= application_port <= 65535:
            raise BindingMismatchError(
                "BIND-PORT-001",
                f"Application port is outside the valid TCP range: {application_port}.",
            )
        files = render_open_tofu(resource_plan)
        hcl_errors = self._timed_hcl_validation(files)
        if hcl_errors:
            raise ValueError(
                "Deterministic ResourcePlan rendering failed HCL preflight: "
                + "; ".join(hcl_errors)
            )
        target = self._target(implementation_result)
        promoted_files = self._promote_infra_files(target, files, "")
        container_files = self._ensure_container_files(target.parent, application_port)
        if enable_consistency_validator:
            self._validate_container_port(target.parent, application_port)
        diagram = deployment_bundle_runtime_puml(
            {
                "schemaVersion": "easydep-deployment-diagram",
                "status": "completed",
                "mode": "single",
                "workloadGraph": graph,
                "projections": [
                    {
                        "status": "completed",
                        "provider": provider,
                        "region": resource_plan.get("region"),
                        "deploymentPlan": deployment_plan,
                        "resourcePlan": resource_plan,
                    }
                ],
            }
        )
        return {
            "status": "completed",
            "method": "deterministic-resource-plan",
            "llmCalls": 0,
            "timingEvents": self.last_timing_events,
            "cloudKbProvided": bool(cloud_design_result.get("kb_used")),
            "directory": str(target),
            "files": sorted(promoted_files),
            "containerFilesCreated": container_files,
            "preflight": {
                "status": "passed",
                "repaired": False,
                "repairFeedbackEnabled": False,
                "consistencyValidatorEnabled": enable_consistency_validator,
                "providerValidation": {
                    "status": "passed",
                    "stage": "deterministicHcl",
                },
            },
            "deploymentNotes": [
                "OpenTofu and Docker bootstrap were rendered deterministically from ResourcePlan."
            ],
            "vmSelection": vm_selection,
            "repairEvents": [],
            "normalizationEvents": [],
            "resourcePlan": resource_plan,
            "resourcePlanDigest": resource_plan.get("structureDigest"),
            "deploymentDiagramPuml": diagram,
        }
