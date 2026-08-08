from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .models import CommandEvidence, Diagnostic, JobSpec, RunManifest
from .agent_runtime import gradle_command, write_execution_plan
from .source_conformance import capture_generated_contracts
from .design_context import (
    ImplementationTask,
    generate_api_adapter_tasks,
    generate_boundary_adapter_tasks,
    generate_e2e_tasks,
    generate_frontend_tasks,
    generate_gateway_adapter_tasks,
    generate_implementation_tasks,
    generate_persistence_tasks,
    generate_wiring_tasks,
)
from .implementation_ir import pascal_case, remove_readonly
from ..frontend_scaffold import (
    openapi_typescript_fetch_command,
    write_react_scaffold,
)


OPTIONAL_DESIGN_INPUTS = ("sequence", "erd", "deployment", "cloud")
BCE_GENERATOR_VERSION = "0.2.0"
IMPLEMENTATION_PIPELINE_VERSION = "0.5.0-agent-frontend"
JAVA_BUILTIN_TYPES = {
    "boolean", "byte", "char", "double", "float", "int", "long", "short", "void",
    "Boolean", "Byte", "Character", "Double", "Float", "Integer", "Long", "Short",
    "String", "string", "DateTime", "OffsetDateTime", "List", "Map", "Set", "Object",
}


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
    tools = data.get("tools", {})
    agent = data.get("agent", {})
    return JobSpec(
        job_type=str(data.get("jobType", "INITIAL_IMPLEMENTATION")),
        feedback=str(data.get("feedback", "")),
        name=data.get("name", job_path.stem),
        workspace_root=root,
        inputs=inputs,
        required_inputs=list(data.get("requiredInputs", ["bceClass", "openapi"])),
        base_package=generation.get("basePackage", "com.example.generated"),
        allow_assumptions=bool(generation.get("allowAssumptions", False)),
        verify_compile=bool(data.get("verification", {}).get("compile", True)),
        output_root=resolve(data.get("outputRoot", "generated/runs")),
        puml2code_root=resolve(
            tools.get(
                "puml2codeRoot", "app/implementation/tools/puml2code-bce"
            )
        ),
        openapi_generator_jar=resolve(
            tools.get(
                "openapiGeneratorJar",
                "app/implementation/tools/openapi-generator/openapi-generator-cli-7.24.0.jar",
            )
        ),
        agent_mode=agent.get("mode", "plan-only"),
        agent_model=agent.get(
            "model", "nvidia_nim/openai/gpt-oss-120b"
        ),
        agent_base_url=agent.get(
            "baseUrl", "https://integrate.api.nvidia.com/v1"
        ),
        agent_temperature=float(agent.get("temperature", 0.6)),
        agent_top_p=float(agent.get("topP", 0.7)),
        agent_max_output_tokens=int(agent.get("maxOutputTokens", 4096)),
        agent_reasoning_budget=int(agent.get("reasoningBudget", 2048)),
    )


class PrototypeOrchestrator:
    def __init__(self, spec: JobSpec):
        self.spec = spec
        self.manifest = RunManifest(job_name=spec.name)

    def run(self) -> Path:
        self.manifest.status = "VALIDATING_INPUT"
        self._validate_inputs()
        self.manifest.input_hash = self._combined_input_hash()
        run_name = f"run_{self.manifest.input_hash[:12]}"
        staging = self.spec.output_root / f".{run_name}.staging"
        final = self.spec.output_root / run_name
        existing_manifest = final / "reports" / "run-manifest.json"
        if existing_manifest.is_file():
            existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
            if existing.get("input_hash") == self.manifest.input_hash:
                return final
            raise RuntimeError(f"Existing run has a conflicting input hash: {final}")
        self._reset_target(staging)
        staging.mkdir(parents=True, exist_ok=True)

        if any(item.severity == "ERROR" for item in self.manifest.diagnostics):
            self.manifest.status = "NEEDS_INPUT"
            self._write_reports(staging)
            self._promote(staging, final)
            return final

        application = staging / "application"
        java_root = application / "src" / "main" / "java"
        java_root.mkdir(parents=True, exist_ok=True)

        try:
            if self.spec.job_type == "FEEDBACK_REVISION":
                self._prepare_feedback_revision(staging)
                self.manifest.status = "SUCCEEDED"
                self.manifest.generated_files = sorted(
                    str(path.relative_to(staging)).replace("\\", "/")
                    for path in staging.rglob("*")
                    if path.is_file()
                )
                self._write_reports(staging)
                self._promote(staging, final)
                return final
            self.manifest.status = "GENERATING_CODE"
            self._generate_bce(java_root)
            self._generate_openapi(application)
            self._generate_frontend(application)
            self._write_gradle_project(application)
            self._write_application_entrypoint(java_root)
            self._write_runtime_configuration(application)
            self._write_missing_type_placeholders(java_root)
            # Capture before any OpenHands task runs.  These files are the
            # immutable BCE/OpenAPI source contract for the implementation.
            capture_generated_contracts(staging, self.spec.base_package)

            if self.spec.verify_compile:
                self.manifest.status = "VERIFYING"
                self._compile(application)

            tasks = generate_implementation_tasks(self.spec, staging)
            self.manifest.implementation_tasks = [task.to_dict() for task in tasks]
            self.manifest.agent_execution = write_execution_plan(
                staging,
                self.manifest.implementation_tasks,
                self.spec.agent_mode,
                self.spec.agent_model,
                self.spec.agent_base_url,
            )

            self.manifest.status = "SUCCEEDED"
        except Exception as error:  # evidence is written before returning the failed run
            self.manifest.status = "FAILED"
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

        required_tools = {} if self.spec.job_type == "FEEDBACK_REVISION" else {
            "puml2code": self.spec.puml2code_root / "bin" / "puml2code",
            "openapiGenerator": self.spec.openapi_generator_jar,
        }
        for name, path in required_tools.items():
            if not path.is_file():
                self.manifest.diagnostics.append(
                    Diagnostic("MISSING_TOOL", "ERROR", f"Missing tool {name}: {path}")
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
        digest.update(BCE_GENERATOR_VERSION.encode())
        digest.update(IMPLEMENTATION_PIPELINE_VERSION.encode())
        # Bind a run to the actual planner/runtime implementation, not only a
        # manually maintained version label. Prompt or gate edits therefore
        # always receive a new immutable run ID.
        for source in sorted(Path(__file__).parent.glob("*.py")):
            digest.update(source.name.encode())
            digest.update(sha256_file(source).encode())
        for tool in (
            self.spec.puml2code_root / "package.json",
            self.spec.openapi_generator_jar,
        ):
            if tool.is_file():
                digest.update(sha256_file(tool).encode())
        for name in sorted(self.manifest.inputs):
            digest.update(name.encode())
            digest.update(self.manifest.inputs[name]["sha256"].encode())
        return digest.hexdigest()

    def _generate_bce(self, java_root: Path) -> None:
        bce_package = f"{self.spec.base_package}.bce"
        workspace_root = str(self.spec.workspace_root)
        command = [
            "docker", "run", "--rm",
            "-v", f"{workspace_root}:{workspace_root}",
            "-w", workspace_root,
            "node:20",
            "node",
            str(self.spec.puml2code_root / "bin" / "puml2code"),
            "-i",
            str(self.spec.inputs["bceClass"]),
            "-l",
            "java",
            "-p",
            bce_package,
            "-o",
            str(java_root),
        ]
        self._run_command("puml2code-bce", command, self.spec.puml2code_root)
        self.manifest.tools["puml2code-bce"] = {
            "upstream": "https://github.com/jupe/puml2code",
            "forkVersion": BCE_GENERATOR_VERSION,
        }

    def _generate_openapi(self, application: Path) -> None:
        workspace_root = str(self.spec.workspace_root)
        command = [
            "docker", "run", "--rm",
            "-v", f"{workspace_root}:{workspace_root}",
            "openapitools/openapi-generator-cli", "generate",
            "-g", "spring",
            "-i", str(self.spec.inputs["openapi"]),
            "-o", str(application),
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
            self.manifest.diagnostics.append(
                Diagnostic(
                    "OPENAPI_MISSING_OPERATION_ID",
                    "WARNING",
                    f"OpenAPI operation has no operationId: {operation}",
                    str(self.spec.inputs["openapi"]),
                )
            )
        self.manifest.tools["openapi-generator"] = {
            "version": "docker-latest",
        }

    def _generate_frontend(self, application: Path) -> None:
        openapi = json.loads(self.spec.inputs["openapi"].read_text(encoding="utf-8"))
        frontend = application / "frontend"
        generated_client = frontend / "src" / "generated"
        command = openapi_typescript_fetch_command(
            self.spec.workspace_root,
            self.spec.inputs["openapi"],
            generated_client,
        )
        self._run_command(
            "openapi-generator-typescript-fetch",
            command,
            self.spec.workspace_root,
        )
        write_react_scaffold(
            frontend,
            openapi,
            application_name=self.spec.name,
            api_base_url="/api",
        )
        self.manifest.tools["easydep-frontend-generator"] = {
            "generator": "typescript-fetch",
            "version": "openapi-generator-cli/v7.14.0",
        }

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

    def _write_missing_type_placeholders(self, java_root: Path) -> None:
        source = self.spec.inputs["bceClass"].read_text(encoding="utf-8")
        missing = find_undefined_bce_types(source)
        if not missing:
            return
        if not self.spec.allow_assumptions:
            raise ValueError(f"Undefined BCE types require input: {', '.join(missing)}")

        package = f"{self.spec.base_package}.bce"
        target_dir = java_root / Path(package.replace(".", "/"))
        target_dir.mkdir(parents=True, exist_ok=True)
        for type_name in missing:
            (target_dir / f"{type_name}.java").write_text(
                f"package {package};\n\n"
                f"/** Assumed placeholder for an undefined BCE type. */\n"
                f"public final class {type_name} {{}}\n",
                encoding="utf-8",
            )
            assumption = f"Generated placeholder for undefined BCE type: {type_name}"
            self.manifest.assumptions.append(assumption)
            self.manifest.diagnostics.append(
                Diagnostic("BCE_UNDEFINED_TYPE_ASSUMED", "WARNING", assumption, str(self.spec.inputs["bceClass"]))
            )

    def _compile(self, application: Path) -> None:
        gradle_home = self.spec.output_root.parent.parent / ".cache" / "gradle"
        self._run_command(
            "gradle-compile",
            [
                "docker", "run", "--rm",
                "-v", f"{application}:{application}",
                "-v", f"{gradle_home}:/home/gradle/.gradle",
                "-w", str(application),
                "gradle:21-jdk",
                "gradle",
                "compileJava",
                "bootJar",
                "--no-daemon",
            ],
            application,
        )
        build_output = application / "build"
        if build_output.exists():
            shutil.rmtree(build_output)
        local_gradle = application / ".gradle"
        if local_gradle.exists():
            shutil.rmtree(local_gradle)

    def _run_command(self, name: str, command: list[str], cwd: Path) -> CommandEvidence:
        started = time.monotonic()
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
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
        self.manifest.commands.append(evidence)
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
        if final.exists():
            raise RuntimeError(f"Refusing to overwrite immutable run: {final}")
        os.replace(staging, final)


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


def plan_boundary_adapter_tasks(
    spec: JobSpec, run_root: Path
) -> list[dict[str, object]]:
    """Add BCE Boundary adapter tasks to an existing run manifest."""
    run_root = run_root.resolve()
    tasks = generate_boundary_adapter_tasks(spec, run_root)
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


def plan_gateway_adapter_tasks(
    spec: JobSpec, run_root: Path
) -> list[dict[str, object]]:
    """Add outbound Gateway adapter tasks to an existing run manifest."""
    run_root = run_root.resolve()
    tasks = generate_gateway_adapter_tasks(spec, run_root)
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


def plan_e2e_tasks(spec: JobSpec, run_root: Path) -> list[dict[str, object]]:
    """Add the E2E task, or persist a structured NEEDS_INPUT design-gap report."""
    run_root = run_root.resolve()
    tasks = generate_e2e_tasks(spec, run_root)
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["implementation_tasks"] = [
        item for item in manifest.get("implementation_tasks", [])
        if item.get("task_type") != "integration-test"
    ]
    if not tasks:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return []
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_undefined_bce_types(source: str) -> list[str]:
    """Find member types that have no class declaration in the BCE diagram.

    Notes and relationship labels deliberately remain outside the scan; they are
    natural-language data, not type declarations.
    """
    declarations = set(re.findall(r"(?m)^\s*class\s+([A-Za-z_]\w*)", source))
    member_lines: list[str] = []
    inside_class = False
    for line in source.splitlines():
        if re.match(r"^\s*class\s+[A-Za-z_]\w*.*\{\s*$", line):
            inside_class = True
            continue
        if inside_class and re.match(r"^\s*}\s*$", line):
            inside_class = False
            continue
        if inside_class and re.match(r"^\s*[+#~-]\s+", line):
            member_lines.append(line)

    type_fragments: list[str] = []
    for line in member_lines:
        type_fragments.extend(
            re.findall(r":\s*([A-Za-z_]\w*(?:\s*<[^>]+>)?(?:\[\])?)", line)
        )
    referenced: set[str] = set()
    for fragment in type_fragments:
        referenced.update(re.findall(r"[A-Za-z_]\w*", fragment))
    return sorted(referenced - declarations - JAVA_BUILTIN_TYPES)
