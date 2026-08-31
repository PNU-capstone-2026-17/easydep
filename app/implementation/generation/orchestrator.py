from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.config import settings

from ..agents.runtime import write_execution_plan
from ..domain.implementation_ir import (
    assess_bce_erd_entity_contract,
    parse_components,
    parse_openapi_operations,
    pascal_case,
    remove_readonly,
)
from ..domain.models import CommandEvidence, Diagnostic, JobSpec, RunManifest
from ..planning.design_context import (
    ImplementationTask,
    generate_api_adapter_tasks,
    generate_frontend_tasks,
    generate_persistence_tasks,
    generate_wiring_tasks,
)
from ..workflows.conformance import capture_generated_contracts
from .frontend import generate_frontend_project
from .java_scaffold import (
    JAVA_SCAFFOLDER_VERSION,
    JavaScaffoldInput,
    build_java_scaffold_trace,
    render_java_scaffold,
    render_openapi_controller_scaffold,
)

OPTIONAL_DESIGN_INPUTS = ("erd", "deployment", "cloud")
IMPLEMENTATION_PIPELINE_VERSION = "0.6.0-strict-release"
OPENAPI_GENERATOR_IMAGE = "openapitools/openapi-generator-cli:v7.24.0"
GRADLE_GENERATOR_IMAGE = "gradle:8.14.2-jdk21"
# A Docker bind mount can keep a directory handle open for a short time after
# the container process has exited on Windows.  Retrying only the documented
# sharing/access-denied errors keeps an immutable run promotion safe while
# allowing Docker Desktop to release that transient handle.
PROMOTION_RETRYABLE_WINERRORS = frozenset({5, 32, 33})
PROMOTION_MAX_ATTEMPTS = 12
PROMOTION_INITIAL_DELAY_SECONDS = 0.25
PROMOTION_MAX_DELAY_SECONDS = 2.0
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
GRADLE_COMMAND_TIMEOUT_SECONDS = 900
PROGRESS_SCHEMA = "easydep-implementation-progress/v1alpha1"
# Docker Desktop accepts a Windows host path as the bind-mount source, but all
# paths interpreted by the Linux container must be POSIX paths.  Mount every
# implementation input below one fixed container root so BCE, OpenAPI, and
# Gradle all use the same portable contract.
CONTAINER_WORKSPACE = PurePosixPath("/workspace")
def load_job(path: Path) -> JobSpec:
    job_path = path.resolve()
    data = json.loads(job_path.read_text(encoding="utf-8"))
    root = (job_path.parent / data.get("workspaceRoot", ".")).resolve()

    def resolve(value: str) -> Path:
        candidate = (root / value).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"Path escapes workspaceRoot: {value}")
        return candidate

    inputs = {name: resolve(value) for name, value in data.get("inputs", {}).items()}
    generation = data.get("generation", {})
    agent = data.get("agent", {})
    return JobSpec(
        job_type=str(data.get("jobType", "INITIAL_IMPLEMENTATION")),
        feedback=str(data.get("feedback", "")),
        name=data.get("name", job_path.stem),
        workspace_root=root,
        inputs=inputs,
        required_inputs=list(
            data.get("requiredInputs", ["bceClass", "sequence", "openapi"])
        ),
        base_package=generation.get("basePackage", "com.example.generated"),
        allow_assumptions=bool(generation.get("allowAssumptions", False)),
        verify_compile=bool(data.get("verification", {}).get("compile", True)),
        output_root=resolve(data.get("outputRoot", "generated/runs")),
        agent_mode=agent.get("mode", "plan-only"),
        agent_model=agent.get(
            "model", "nvidia_nim/openai/gpt-oss-120b"
        ),
        agent_base_url=agent.get(
            "baseUrl", "https://integrate.api.nvidia.com/v1"
        ),
        agent_temperature=float(
            agent.get("temperature", settings.implementation_agent_temperature)
        ),
        agent_top_p=float(agent.get("topP", 0.7)),
        agent_max_output_tokens=int(
            agent.get(
                "maxOutputTokens", settings.implementation_agent_max_output_tokens
            )
        ),
        agent_reasoning_budget=int(agent.get("reasoningBudget", 2048)),
        progress_path=(
            resolve(data["progressPath"])
            if isinstance(data.get("progressPath"), str)
            else None
        ),
        app_id=str(data["appId"]) if data.get("appId") else None,
    )


class _ManifestBuffer:
    """Per-thread sink so parallel generators never interleave manifest writes.

    A run directory is an immutable, reproducible checkpoint, so the evidence
    it records must not depend on which generator happened to finish first.
    """

    def __init__(self) -> None:
        self.commands: list[CommandEvidence] = []
        self.diagnostics: list[Diagnostic] = []
        self.tools: dict[str, dict[str, str]] = {}


class PrototypeOrchestrator:
    def __init__(self, spec: JobSpec):
        self.spec = spec
        self.manifest = RunManifest(job_name=spec.name, app_id=spec.app_id)
        self._sinks = threading.local()

    def _sink(self):
        """Return the buffer for this thread, or the manifest when sequential."""
        return getattr(self._sinks, "target", None) or self.manifest

    def _generate_sources(self, application: Path, java_root: Path) -> None:
        """Run the three independent generators concurrently.

        They write to disjoint trees -- BCE to src/main/java/<pkg>/bce, OpenAPI
        to src/main/java/<pkg>/api, and the frontend to application/frontend --
        so only the shared manifest needs isolating.
        """
        lanes = (
            (_ManifestBuffer(), lambda: self._generate_bce(java_root)),
            (_ManifestBuffer(), lambda: self._generate_openapi(application)),
            (_ManifestBuffer(), lambda: self._generate_frontend(application)),
        )

        def lane(buffer: _ManifestBuffer, call) -> None:
            self._sinks.target = buffer
            try:
                call()
            finally:
                self._sinks.target = None

        try:
            with ThreadPoolExecutor(
                max_workers=len(lanes), thread_name_prefix="easydep-generate"
            ) as pool:
                futures = [pool.submit(lane, buffer, call) for buffer, call in lanes]
                for future in futures:
                    future.result()
        finally:
            # Merge in declaration order, never completion order.  This runs even
            # when a lane fails: a failed run still has to report the evidence
            # from the generators that did execute.
            for buffer, _ in lanes:
                self.manifest.commands.extend(buffer.commands)
                self.manifest.diagnostics.extend(buffer.diagnostics)
                self.manifest.tools.update(buffer.tools)

    def run(self) -> Path:
        self._set_status("VALIDATING_INPUT", "입력 산출물을 검증하고 있습니다.")
        self._validate_inputs()
        self.manifest.input_hash = self._combined_input_hash()
        staging, final = self._select_run_paths()
        # A successful run for the exact input and generator fingerprint is an
        # immutable checkpoint. Reusing it is essential when a member runner
        # resumes an interrupted agent workflow; regenerating into the same
        # destination would correctly be rejected by _promote, but only after
        # spending time on every generator again.
        if final.exists():
            self._set_status("REUSING_GENERATED_RUN", "동일 입력의 생성 결과를 재사용하고 있습니다.")
            return final
        self._reset_target(staging)
        staging.mkdir(parents=True, exist_ok=True)

        if any(item.severity == "ERROR" for item in self.manifest.diagnostics):
            self._set_status("NEEDS_INPUT", "생성 전에 입력 보완이 필요합니다.")
            self._write_reports(staging)
            self._promote(staging, final)
            return final

        application = staging / "application"
        java_root = application / "src" / "main" / "java"
        java_root.mkdir(parents=True, exist_ok=True)

        try:
            if self.spec.job_type == "FEEDBACK_REVISION":
                self._set_status("PREPARING_FEEDBACK", "기존 산출물과 피드백을 준비하고 있습니다.")
                self._prepare_feedback_revision(staging)
                self._set_status("SUCCEEDED", "피드백 적용 준비가 완료되었습니다.")
                self.manifest.generated_files = sorted(
                    str(path.relative_to(staging)).replace("\\", "/")
                    for path in staging.rglob("*")
                    if path.is_file()
                )
                self._write_reports(staging)
                self._promote(staging, final)
                return final
            self._set_status(
                "GENERATING_SOURCES",
                "BCE·OpenAPI·Frontend 코드를 생성하고 있습니다.",
            )
            self._generate_sources(application, java_root)
            self._set_status("PREPARING_BUILD", "생성된 애플리케이션 프로젝트를 준비하고 있습니다.")
            self._write_gradle_project(application)
            self._write_application_entrypoint(java_root)
            self._write_runtime_configuration(application)
            # Capture before any OpenHands task runs.  These files are the
            # immutable BCE/OpenAPI source contract for the implementation.
            capture_generated_contracts(staging, self.spec.base_package)

            if self.spec.verify_compile:
                self._set_status("VERIFYING", "생성된 백엔드를 컴파일하고 있습니다.")
                self._compile(application)

            self._generate_openapi_controllers(application)
            self._set_status("PLANNING", "구현 작업과 의존 관계를 계획하고 있습니다.")
            self.manifest.implementation_tasks = []
            self.manifest.agent_execution = write_execution_plan(
                staging,
                self.manifest.implementation_tasks,
                self.spec.agent_mode,
                self.spec.agent_model,
                self.spec.agent_base_url,
            )

            self._set_status("SUCCEEDED", "초기 생성과 구현 계획 준비가 완료되었습니다.")
        except Exception as error:  # evidence is written before returning the failed run
            self._set_status("FAILED", "초기 생성 또는 검증에 실패했습니다.")
            self.manifest.diagnostics.append(
                Diagnostic("GENERATION_FAILED", "ERROR", str(error))
            )

        self.manifest.generated_files = sorted(
            str(path.relative_to(staging)).replace("\\", "/")
            for path in staging.rglob("*")
            if path.is_file()
        )
        self._write_reports(staging)
        self._promote(staging, final)
        return final

    def _select_run_paths(self) -> tuple[Path, Path]:
        """Return an immutable destination, retrying only previously failed runs.

        A failure report is useful evidence and must never be overwritten.  It
        must also not permanently prevent a corrected generator/runtime from
        retrying the same input hash.
        """
        base_name = f"run_{self.manifest.input_hash[:12]}"
        attempt = 0
        while True:
            run_name = base_name if attempt == 0 else f"{base_name}_retry_{attempt}"
            final = self.spec.output_root / run_name
            existing_manifest = final / "reports" / "run-manifest.json"
            if not existing_manifest.exists():
                if final.exists():
                    raise RuntimeError(f"Existing run is missing its manifest: {final}")
                return self.spec.output_root / f".{run_name}.staging", final
            existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
            if existing.get("input_hash") != self.manifest.input_hash:
                raise RuntimeError(f"Existing run has a conflicting input hash: {final}")
            if existing.get("status") != "FAILED":
                return self.spec.output_root / f".{run_name}.staging", final
            attempt += 1

    def _prepare_feedback_revision(self, staging: Path) -> None:
        snapshot_path = self.spec.inputs.get("baseSnapshot")
        if snapshot_path is None or not snapshot_path.is_file():
            raise ValueError("Feedback revision requires baseSnapshot")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        files = snapshot.get("files", {})
        if not isinstance(files, dict) or not files:
            raise ValueError("Feedback revision baseSnapshot is empty")

        allowed: list[str] = []
        for relative, content in sorted(files.items()):
            relative = str(relative).replace("\\", "/").strip("/")
            parts = Path(relative).parts
            if (
                not relative
                or any(part in {"", ".", ".."} for part in parts)
                or not relative.startswith("application/")
            ):
                raise ValueError(f"Invalid feedback snapshot path: {relative}")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
            allowed.append(relative)

        # A feedback revision starts from an already generated application.
        # Snapshot its BCE/OpenAPI contracts before OpenHands receives any
        # writable paths, just as an initial implementation run does.
        capture_generated_contracts(staging, self.spec.base_package)

        task_dir = staging / "reports" / "implementation-tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        immutable = [
            relative for relative in allowed
            if "/src/main/java/" in f"/{relative}"
            and any(token in f"/{relative}" for token in ("/bce/", "/api/"))
        ]
        editable = [relative for relative in allowed if relative not in immutable]
        context = {
            "schemaVersion": "implementation-feedback-context/v1alpha1",
            "taskId": "apply-source-feedback",
            "taskType": "control",
            "feedback": self.spec.feedback,
            "editableFiles": editable,
            "immutableFiles": immutable,
        }
        context_path = task_dir / "source-feedback.context.json"
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt = (
            "Apply the user's natural-language feedback to the existing application.\n\n"
            f"## User feedback\n{self.spec.feedback}\n\n"
            "## Rules\n"
            "- Make a minimal, incremental change; preserve unrelated behavior.\n"
            "- Modify only the explicitly allowed existing files.\n"
            "- Do not weaken, delete, or disable tests to obtain a passing build.\n"
            "- Add or strengthen assertions in an existing test file when behavior changes.\n"
            "- Generated API and BCE contracts are immutable. Do not edit them.\n"
            "- Finish only when compileJava and test pass.\n\n"
            "## Editable files\n"
            + "\n".join(f"- `{path}`" for path in editable)
        )
        prompt_path = task_dir / "source-feedback.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        task = ImplementationTask(
            task_id="apply-source-feedback",
            control="Natural-language source feedback",
            prompt_file=str(prompt_path.relative_to(staging)).replace("\\", "/"),
            context_file=str(context_path.relative_to(staging)).replace("\\", "/"),
            allowed_write_paths=editable,
            immutable_paths=immutable,
            source_artifacts={"baseSnapshot": str(snapshot_path)},
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            llm={
                "mode": self.spec.agent_mode,
                "model": self.spec.agent_model,
                "baseUrl": self.spec.agent_base_url,
            },
            task_type="control",
        )
        self.manifest.implementation_tasks = [task.to_dict()]
        self.manifest.agent_execution = write_execution_plan(
            staging,
            self.manifest.implementation_tasks,
            self.spec.agent_mode,
            self.spec.agent_model,
            self.spec.agent_base_url,
        )

    def _validate_inputs(self) -> None:
        for name in self.spec.required_inputs:
            path = self.spec.inputs.get(name)
            if path is None or not path.is_file():
                self.manifest.diagnostics.append(
                    Diagnostic("MISSING_REQUIRED_INPUT", "ERROR", f"Missing required input: {name}")
                )

        if self.spec.job_type != "FEEDBACK_REVISION":
            for name in OPTIONAL_DESIGN_INPUTS:
                path = self.spec.inputs.get(name)
                if path is None or not path.is_file():
                    self.manifest.diagnostics.append(
                        Diagnostic(
                            "MISSING_PROTOTYPE_INPUT",
                            "WARNING",
                            f"Prototype continues without optional input: {name}",
                        )
                    )

        for name, path in self.spec.inputs.items():
            if not path.is_file():
                continue
            digest = sha256_file(path)
            self.manifest.inputs[name] = {
                "path": str(path),
                "sha256": digest,
                "size": path.stat().st_size,
            }

        if self.spec.job_type != "FEEDBACK_REVISION":
            sequence = self.spec.inputs.get("sequence")
            if sequence and sequence.is_file() and not re.search(
                r"(?m)^\s*[A-Za-z_]\w*\s*(?:-|--)+>+\s*[A-Za-z_]\w*\s*:",
                sequence.read_text(encoding="utf-8"),
            ):
                self.manifest.diagnostics.append(
                    Diagnostic(
                        "SEQUENCE_HAS_NO_CALLS",
                        "ERROR",
                        "Sequence input contains no executable participant calls.",
                        str(sequence),
                    )
                )
            openapi = self.spec.inputs.get("openapi")
            if openapi and openapi.is_file():
                openapi_readable = True
                try:
                    operations = parse_openapi_operations(
                        openapi.read_text(encoding="utf-8")
                    )
                except (
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    AttributeError,
                    TypeError,
                ) as error:
                    self.manifest.diagnostics.append(
                        Diagnostic(
                            "OPENAPI_INVALID_DOCUMENT",
                            "ERROR",
                            f"OpenAPI input could not be read as a document: {error}",
                            str(openapi),
                        )
                    )
                    openapi_readable = False
                    operations = []
                if openapi_readable and not operations:
                    self.manifest.diagnostics.append(
                        Diagnostic(
                            "OPENAPI_NO_OPERATIONS",
                            "ERROR",
                            "OpenAPI paths must contain at least one HTTP operation before implementation can start.",
                            str(openapi),
                        )
                    )
                missing = [
                    f"{operation.method} {operation.path}"
                    for operation in operations
                    if not operation.operation_id
                ]
                for operation in missing:
                    self.manifest.diagnostics.append(
                        Diagnostic(
                            "OPENAPI_MISSING_OPERATION_ID",
                            "ERROR",
                            f"OpenAPI operation requires operationId: {operation}",
                            str(openapi),
                        )
                    )
            deployment = self.spec.inputs.get("deployment")
            cloud = self.spec.inputs.get("cloud")
            intent = self.spec.inputs.get("deploymentIntent")
            if (
                deployment
                and deployment.is_file()
                and not (cloud and cloud.is_file())
                and not (intent and intent.is_file())
            ):
                self.manifest.diagnostics.append(
                    Diagnostic(
                        "DEPLOYMENT_INTENT_SOURCE_MISSING",
                        "ERROR",
                        "Deployment diagram requires cloud or deploymentIntent input.",
                        str(deployment),
                    )
                )
            bce = self.spec.inputs.get("bceClass")
            erd = self.spec.inputs.get("erd")
            bce_entities = {
                item.name
                for item in (
                    parse_components(bce.read_text(encoding="utf-8"))
                    if bce and bce.is_file()
                    else []
                )
                if item.stereotype.lower() == "entity"
            }
            erd_source = erd.read_text(encoding="utf-8") if erd and erd.is_file() else ""
            contract = assess_bce_erd_entity_contract(erd_source, bce_entities)
            if bce_entities and not contract.erd_entities:
                self.manifest.diagnostics.append(
                    Diagnostic(
                        "ERD_REQUIRED_FOR_BCE_ENTITIES",
                        "ERROR",
                        "ERD input is required when BCE contains Entity components.",
                        str(bce),
                    )
                )
            elif contract.missing_bce_entities or contract.unexpected_erd_entities:
                unexpected_erd_entities = set(contract.unexpected_erd_entities)
                missing_erd_entities = set(contract.missing_bce_entities)
                if missing_erd_entities or unexpected_erd_entities:
                    self.manifest.diagnostics.append(
                        Diagnostic(
                            "BCE_ERD_ENTITY_MISMATCH",
                            "ERROR",
                            "BCE and ERD entity aliases must match (generated physical tables are allowed): "
                            f"BCE={sorted(bce_entities)}, ERD={sorted(contract.erd_entities)}, "
                            f"unmatched ERD={sorted(unexpected_erd_entities)}, "
                            f"missing ERD={sorted(missing_erd_entities)}",
                            str(erd),
                        )
                    )

    def _combined_input_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.spec.name.encode())
        digest.update(self.spec.job_type.encode())
        digest.update(self.spec.feedback.encode())
        digest.update(self.spec.base_package.encode())
        digest.update(str(self.spec.allow_assumptions).encode())
        digest.update(str(self.spec.verify_compile).encode())
        digest.update(self.spec.agent_model.encode())
        digest.update(self.spec.agent_base_url.encode())
        digest.update(str(self.spec.agent_temperature).encode())
        digest.update(str(self.spec.agent_top_p).encode())
        digest.update(str(self.spec.agent_max_output_tokens).encode())
        digest.update(str(self.spec.agent_reasoning_budget).encode())
        digest.update(JAVA_SCAFFOLDER_VERSION.encode())
        digest.update(OPENAPI_GENERATOR_IMAGE.encode())
        digest.update(IMPLEMENTATION_PIPELINE_VERSION.encode())
        # Bind a run to the actual planner/runtime implementation, not only a
        # manually maintained version label. Prompt or gate edits therefore
        # always receive a new immutable run ID.
        implementation_root = Path(__file__).resolve().parents[1]
        for source in sorted(implementation_root.rglob("*.py")):
            digest.update(source.relative_to(implementation_root).as_posix().encode())
            digest.update(sha256_file(source).encode())
        for name in sorted(self.manifest.inputs):
            digest.update(name.encode())
            digest.update(self.manifest.inputs[name]["sha256"].encode())
        return digest.hexdigest()

    def _container_path(self, path: Path) -> str:
        """Return a workspace-relative path as seen by generation containers."""
        root = self.spec.workspace_root.resolve()
        source = path.resolve()
        try:
            relative = source.relative_to(root)
        except ValueError as error:
            raise ValueError(
                f"Docker input/output must stay inside workspaceRoot: {source}"
            ) from error
        return (CONTAINER_WORKSPACE / relative.as_posix()).as_posix()

    def _workspace_volume(self) -> str:
        return f"{self.spec.workspace_root.resolve()}:{CONTAINER_WORKSPACE.as_posix()}"

    def _set_status(self, status: str, message: str) -> None:
        """Persist fine-grained progress for the parent web worker to poll."""
        self.manifest.status = status
        progress_path = getattr(self.spec, "progress_path", None)
        if progress_path is None:
            return
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": PROGRESS_SCHEMA,
            "status": status,
            "message": message,
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=progress_path.parent,
                prefix=f".{progress_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                json.dump(payload, handle, ensure_ascii=False)
            temporary = Path(temporary_name)
            for attempt in range(5):
                try:
                    os.replace(temporary, progress_path)
                    temporary_name = None
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def _generate_bce(self, java_root: Path) -> None:
        """구조화된 BCE JSON을 검증하고 Java source root 아래에 직접 쓴다."""
        def read_json(name: str) -> dict[str, object]:
            return json.loads(self.spec.inputs[name].read_text(encoding="utf-8"))

        erd_path = self.spec.inputs.get("erdBceModel")
        scaffold = JavaScaffoldInput.model_validate({
            "bceModel": read_json("bceModel"),
            "sequenceModel": read_json("sequenceModel"),
            "apiModel": read_json("apiModel"),
            "erdBceModel": (
                json.loads(erd_path.read_text(encoding="utf-8"))
                if erd_path and erd_path.is_file()
                else None
            ),
            "basePackage": self.spec.base_package,
            "javaVersion": 21,
            "applicationName": self.spec.name,
        })
        files = render_java_scaffold(scaffold)
        for relative, content in files.items():
            target = java_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        trace_path = java_root.parents[3] / "reports" / "java-scaffold-trace.json"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(
                build_java_scaffold_trace(scaffold, files),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._sink().tools["typed-java-scaffolder"] = {
            "version": JAVA_SCAFFOLDER_VERSION,
            "input": "BCEModel",
            "javaVersion": "21",
        }

    def _generate_openapi(self, application: Path) -> None:
        command = [
            "docker", "run", "--rm",
            "-v", self._workspace_volume(),
            "-w", CONTAINER_WORKSPACE.as_posix(),
            OPENAPI_GENERATOR_IMAGE, "generate",
            "-g", "spring",
            "-i", self._container_path(self.spec.inputs["openapi"]),
            "-o", self._container_path(application),
            "--additional-properties=" + ",".join(
                [
                    "library=spring-boot",
                    "interfaceOnly=true",
                    "skipDefaultInterface=true",
                    "useSpringBoot3=true",
                    "useBeanValidation=true",
                    "hideGenerationTimestamp=true",
                    f"basePackage={self.spec.base_package}",
                    f"apiPackage={self.spec.base_package}.api",
                    f"modelPackage={self.spec.base_package}.api.model",
                    f"artifactId={self.spec.name}",
                    "groupId=com.example",
                ]
            ),
            "--global-property", "apis,models",
        ]
        evidence = self._run_command(
            "openapi-generator", command, self.spec.workspace_root
        )
        missing_operation_ids: set[str] = set()
        for line in (evidence.stdout + evidence.stderr).splitlines():
            if "Empty operationId found" in line:
                match = re.search(r"path:\s*([A-Za-z]+\s+\S+)", line)
                if match:
                    method, path = match.group(1).split(maxsplit=1)
                    operation = f"{method.upper()} {path}"
                else:
                    operation = line.strip()
                missing_operation_ids.add(operation)
        for operation in sorted(missing_operation_ids):
            self._sink().diagnostics.append(
                Diagnostic(
                    "OPENAPI_MISSING_OPERATION_ID",
                    "WARNING",
                    f"OpenAPI operation has no operationId: {operation}",
                    str(self.spec.inputs["openapi"]),
                )
            )
        self._sink().tools["openapi-generator"] = {
            "image": OPENAPI_GENERATOR_IMAGE,
        }

    def _generate_openapi_controllers(self, application: Path) -> None:
        """OpenAPI interface의 확정 선언을 Controller 골격으로 한 번 옮긴다.

        OpenAPI Generator가 interface를 만든 직후 그 확정 선언을 사용한다. 선택적인 초기
        compile 뒤에 골격을 추가하므로 body sentinel은 OpenHands 작업 compile에서 검사된다.
        """
        package_path = Path(self.spec.base_package.replace(".", "/"))
        api_root = application / "src" / "main" / "java" / package_path / "api"
        controller_root = (
            application / "src" / "main" / "java" / package_path / "adapter" / "in" / "web"
        )
        generated = 0
        for interface_path in sorted(api_root.glob("*Api.java")):
            if interface_path.name == "ApiUtil.java":
                continue
            controller_name, source = render_openapi_controller_scaffold(
                interface_path.read_text(encoding="utf-8"), self.spec.base_package
            )
            target = controller_root / f"{controller_name}.java"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8", newline="\n")
            generated += 1
        self._sink().tools["openapi-controller-scaffolder"] = {
            "interfaces": generated,
            "source": "generated-openapi-interface",
        }

    def _generate_frontend(self, application: Path) -> None:
        openapi = json.loads(self.spec.inputs["openapi"].read_text(encoding="utf-8"))
        frontend = application / "frontend"
        generation = generate_frontend_project(
            workspace_root=self.spec.workspace_root,
            openapi_path=self.spec.inputs["openapi"],
            frontend_root=frontend,
            api_spec=openapi,
            application_name=self.spec.name,
            api_base_url=None,
            run_command=self._run_command,
        )
        self._sink().tools["easydep-frontend-generator"] = generation.tool_metadata()

    def _write_gradle_project(self, application: Path) -> None:
        build = """plugins {
    id 'org.springframework.boot' version '3.3.13'
    id 'io.spring.dependency-management' version '1.1.6'
    id 'java'
}

group = 'com.example'
version = '0.1.0-SNAPSHOT'

java {
    toolchain { languageVersion = JavaLanguageVersion.of(21) }
}

repositories { mavenCentral() }

dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'org.springframework.boot:spring-boot-starter-actuator'
    implementation 'org.springframework.boot:spring-boot-starter-validation'
    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
    implementation 'org.flywaydb:flyway-core'
    implementation 'org.springdoc:springdoc-openapi-starter-webmvc-ui:2.6.0'
    implementation 'com.google.code.findbugs:jsr305:3.0.2'
    implementation 'com.fasterxml.jackson.datatype:jackson-datatype-jsr310'
    implementation 'org.openapitools:jackson-databind-nullable:0.2.10'
    runtimeOnly 'com.h2database:h2'
    testImplementation 'org.springframework.boot:spring-boot-starter-test'
}

tasks.withType(Test).configureEach { useJUnitPlatform() }
"""
        (application / "build.gradle").write_text(build, encoding="utf-8")
        (application / "settings.gradle").write_text(
            f"rootProject.name = '{self.spec.name}'\n", encoding="utf-8"
        )

    def _write_application_entrypoint(self, java_root: Path) -> None:
        """Create the one Spring Boot entrypoint that the wiring task completes."""
        package = self.spec.base_package
        application_class = f"{pascal_case(self.spec.name)}Application"
        target = (
            java_root
            / Path(package.replace(".", "/"))
            / f"{application_class}.java"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"package {package};\n\n"
            "import org.springframework.boot.SpringApplication;\n"
            "import org.springframework.boot.autoconfigure.SpringBootApplication;\n\n"
            "@SpringBootApplication\n"
            f"public class {application_class} {{\n"
            "    public static void main(String[] args) {\n"
            f"        SpringApplication.run({application_class}.class, args);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )

    def _write_runtime_configuration(self, application: Path) -> None:
        resources = application / "src" / "main" / "resources"
        resources.mkdir(parents=True, exist_ok=True)
        (resources / "application.yml").write_text(
            "server:\n"
            "  port: 8000\n"
            "management:\n"
            "  endpoints:\n"
            "    web:\n"
            "      base-path: /\n"
            "      exposure:\n"
            "        include: health\n"
            "      path-mapping:\n"
            "        health: healthz\n"
            "  endpoint:\n"
            "    health:\n"
            "      probes:\n"
            "        enabled: true\n",
            encoding="utf-8",
        )

    def _compile(self, application: Path) -> None:
        # Do not mount the Windows-host Gradle cache into the container. Gradle's
        # FileAccessTimeJournal is lock-sensitive and can fail before compilation
        # with WinError 5/I/O errors when the host cache is shared by jobs. The
        # generator image keeps its own dependency layers; an isolated container
        # home trades cache reuse for a reliable pre-approval compile gate.
        self._run_command(
            "gradle-compile",
            [
                "docker", "run", "--rm",
                "-v", self._workspace_volume(),
                "-e", "GRADLE_USER_HOME=/tmp/easydep-gradle-home",
                "-w", self._container_path(application),
                GRADLE_GENERATOR_IMAGE,
                "gradle",
                "compileJava",
                "--no-daemon",
                "-Dorg.gradle.vfs.watch=false",
                "--build-cache",
            ],
            application,
            timeout_seconds=GRADLE_COMMAND_TIMEOUT_SECONDS,
        )
        # Deliberately `compileJava` only.  This pre-approval gate proves the
        # generated scaffold compiles; nothing consumes a jar yet.  `bootJar`
        # resolves the runtime classpath and copies every dependency into
        # BOOT-INF/lib through the bind mount, and the result is discarded:
        # agents/verification/build.py repackages after approval, the delivery
        # images build their own jar from source, and _persist_outputs skips
        # every `build/` path.
        local_gradle = application / ".gradle"
        if local_gradle.exists():
            shutil.rmtree(local_gradle)

    def _run_command(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> CommandEvidence:
        started = time.monotonic()
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        evidence = CommandEvidence(
            name=name,
            command=command,
            cwd=str(cwd),
            exit_code=result.returncode,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout=result.stdout[-20000:],
            stderr=result.stderr[-20000:],
        )
        self._sink().commands.append(evidence)
        if result.returncode != 0:
            raise RuntimeError(f"{name} failed with exit code {result.returncode}: {result.stderr[-1000:]}")
        return evidence

    def _write_reports(self, root: Path) -> None:
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "run-manifest.json").write_text(
            json.dumps(self.manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (reports / "input-diagnostics.json").write_text(
            json.dumps(
                [diagnostic.__dict__ for diagnostic in self.manifest.diagnostics],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with (reports / "traceability-matrix.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["source_artifact", "source_sha256", "generated_file"])
            for name, metadata in sorted(self.manifest.inputs.items()):
                for generated in self.manifest.generated_files:
                    if (name == "bceClass" and "/bce/" in generated) or (
                        name == "openapi" and ("/api/" in generated or "/api/model/" in generated)
                    ):
                        writer.writerow([name, metadata["sha256"], generated])
                for task in self.manifest.implementation_tasks:
                    if name in task["source_artifacts"]:
                        writer.writerow([name, metadata["sha256"], task["prompt_file"]])
                        writer.writerow([name, metadata["sha256"], task["context_file"]])

    def _reset_target(self, target: Path) -> None:
        target = target.resolve()
        output_root = self.spec.output_root.resolve()
        if output_root not in target.parents:
            raise ValueError(f"Refusing to reset path outside output root: {target}")
        if target.exists():
            shutil.rmtree(target, onerror=remove_readonly)

    def _promote(self, staging: Path, final: Path) -> None:
        self.spec.output_root.mkdir(parents=True, exist_ok=True)
        for attempt in range(PROMOTION_MAX_ATTEMPTS):
            if final.exists():
                raise RuntimeError(f"Refusing to overwrite immutable run: {final}")
            try:
                os.replace(staging, final)
                return
            except OSError as error:
                winerror = getattr(error, "winerror", None)
                if (
                    winerror not in PROMOTION_RETRYABLE_WINERRORS
                    or attempt == PROMOTION_MAX_ATTEMPTS - 1
                ):
                    raise
                delay = min(
                    PROMOTION_INITIAL_DELAY_SECONDS * (2 ** attempt),
                    PROMOTION_MAX_DELAY_SECONDS,
                )
                time.sleep(delay)


def plan_persistence_tasks(spec: JobSpec, run_root: Path) -> list[dict[str, object]]:
    """Add persistence tasks and deterministic dependencies to an existing run."""
    run_root = run_root.resolve()
    build = run_root / "application" / "build.gradle"
    if not build.is_file():
        raise ValueError(f"Run build.gradle was not found: {build}")
    source = build.read_text(encoding="utf-8")
    source = source.replace(
        "    implementation 'org.springframework.data:spring-data-commons'\n",
        "    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'\n"
        "    implementation 'org.flywaydb:flyway-core'\n",
    )
    if "spring-boot-starter-data-jpa" not in source:
        marker = "    implementation 'org.springframework.boot:spring-boot-starter-validation'\n"
        source = source.replace(
            marker,
            marker
            + "    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'\n"
            + "    implementation 'org.flywaydb:flyway-core'\n",
        )
    if "runtimeOnly 'com.h2database:h2'" not in source:
        source = source.replace(
            "    testImplementation 'org.springframework.boot:spring-boot-starter-test'\n",
            "    runtimeOnly 'com.h2database:h2'\n"
            "    testImplementation 'org.springframework.boot:spring-boot-starter-test'\n",
        )
    build.write_text(source, encoding="utf-8")

    tasks = generate_persistence_tasks(spec, run_root)
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = {
        item.get("task_id"): item
        for item in manifest.get("implementation_tasks", [])
    }
    # The persistence planner can change task boundaries when the ERD gains
    # relationships. Replace all of its previous tasks as a unit so an old
    # per-file entity task never overlaps a new relationship-group task.
    persistence_task_types = {
        "persistence-entities",
        "persistence-repositories",
        "persistence-mapping",
        "persistence-schema",
    }
    existing = {
        task_id: item for task_id, item in existing.items()
        if item.get("task_type") not in persistence_task_types
    }
    for task in tasks:
        existing[task.task_id] = task.to_dict()
    manifest["implementation_tasks"] = list(existing.values())
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task.to_dict() for task in tasks]


def plan_api_adapter_tasks(spec: JobSpec, run_root: Path) -> list[dict[str, object]]:
    """Add generated OpenAPI adapter tasks to an existing run manifest."""
    run_root = run_root.resolve()
    tasks = generate_api_adapter_tasks(spec, run_root)
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = {
        item.get("task_id"): item
        for item in manifest.get("implementation_tasks", [])
    }
    for task in tasks:
        existing[task.task_id] = task.to_dict()
    manifest["implementation_tasks"] = list(existing.values())
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task.to_dict() for task in tasks]


def plan_wiring_tasks(spec: JobSpec, run_root: Path) -> list[dict[str, object]]:
    """Add the Spring application wiring task to an existing run manifest."""
    run_root = run_root.resolve()
    tasks = generate_wiring_tasks(spec, run_root)
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = {
        item.get("task_id"): item
        for item in manifest.get("implementation_tasks", [])
    }
    for task in tasks:
        existing[task.task_id] = task.to_dict()
    manifest["implementation_tasks"] = list(existing.values())
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task.to_dict() for task in tasks]


def plan_frontend_tasks(spec: JobSpec, run_root: Path) -> list[dict[str, object]]:
    """Add the design-driven React implementation task to the run manifest."""
    run_root = run_root.resolve()
    tasks = generate_frontend_tasks(spec, run_root)
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = {
        item.get("task_id"): item
        for item in manifest.get("implementation_tasks", [])
        if item.get("task_type") != "frontend-implementation"
    }
    for task in tasks:
        existing[task.task_id] = task.to_dict()
    manifest["implementation_tasks"] = list(existing.values())
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task.to_dict() for task in tasks]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
