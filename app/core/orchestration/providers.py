"""Concrete providers behind the stable orchestration step contracts."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from openai import OpenAI

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.core.orchestration.adapters.design import DesignAdapter
from app.core.orchestration.adapters.requirements import RequirementsAdapter
from app.core.orchestration.adapters.testing import TestingAdapter
from app.core.orchestration.adapters.vm_delivery import VmDeliveryAdapter
from app.core.orchestration.contracts import (
    Diagnostic,
    ProviderKind,
    RunMode,
    StepContext,
    StepResult,
    StepStatus,
)
from app.core.orchestration.process import run_process_tree
from app.core.orchestration.vm_selection import select_vm_candidates
from app.implementation.config import ImplementationSettings
from app.implementation.application.prototype import PrototypeClient


def _failure(step: str, provider: ProviderKind, error: Exception) -> StepResult:
    return StepResult(
        step=step,
        provider=provider,
        status=StepStatus.FAILED,
        diagnostics=[
            Diagnostic(code=type(error).__name__, message=str(error), severity="error")
        ],
    )


class MemberRequirementsProvider:
    step = "requirements.analysis"

    def __init__(self, adapter: RequirementsAdapter | None = None) -> None:
        self.adapter = adapter or RequirementsAdapter()

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        try:
            previous = payload.get("member_result") or {}
            if previous and context.response is not None:
                result = self.adapter.resume(
                    app_id=context.app_id,
                    thread_id=f"orchestration:{context.run_id}:requirements",
                    answer=context.response,
                )
            elif previous:
                result = previous
            else:
                result = self.adapter.start(
                    app_id=context.app_id,
                    thread_id=f"orchestration:{context.run_id}:requirements",
                    requirements=list(payload.get("requirements") or []),
                    constraints_text=str(payload.get("resource_constraints_text") or ""),
                )
            status = (
                StepStatus.COMPLETED
                if result.get("status") == "completed"
                else StepStatus.NEEDS_INPUT
            )
            if status == StepStatus.NEEDS_INPUT and context.mode == RunMode.BATCH:
                return StepResult(
                    step=self.step,
                    provider=ProviderKind.MEMBER,
                    status=StepStatus.FAILED,
                    output={"member_result": result},
                    diagnostics=[
                        Diagnostic(
                            code="BATCH_INPUT_REQUIRED",
                            message="Requirements analysis requires information absent from the batch case.",
                        )
                    ],
                )
            prompt = {
                key: result.get(key)
                for key in (
                    "feedback_prompt",
                    "questions",
                    "resource_questions",
                    "edit_stage",
                    "edit_targets",
                )
                if result.get(key) is not None
            }
            return StepResult(
                step=self.step,
                provider=ProviderKind.MEMBER,
                status=status,
                output={"member_result": result},
                prompt=prompt or None,
            )
        except Exception as error:  # noqa: BLE001 - provider failure is data
            return _failure(self.step, ProviderKind.MEMBER, error)


class MemberDesignProvider:
    step = "design.architecture"

    def __init__(self, adapter: DesignAdapter | None = None) -> None:
        self.adapter = adapter or DesignAdapter()

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        try:
            previous = payload.get("member_result") or {}
            if previous and context.response is not None:
                result = self.adapter.resume(
                    session_id=f"orchestration:{context.run_id}:design",
                    feedback=str(context.response),
                )
            elif previous:
                result = previous
            else:
                result = self.adapter.start(
                    session_id=f"orchestration:{context.run_id}:design",
                    requirements_result=dict(payload["requirements_result"]),
                )
            if context.mode == RunMode.BATCH:
                for _ in range(5):
                    if result.get("status") == "completed":
                        break
                    result = self.adapter.resume(
                        session_id=f"orchestration:{context.run_id}:design",
                        feedback="",
                    )
            status = (
                StepStatus.COMPLETED
                if result.get("status") == "completed"
                else StepStatus.NEEDS_INPUT
            )
            return StepResult(
                step=self.step,
                provider=ProviderKind.MEMBER,
                status=status,
                output={"member_result": result},
                prompt={
                    "stage": result.get("stage"),
                    "prompt": result.get("feedback_prompt"),
                }
                if status == StepStatus.NEEDS_INPUT
                else None,
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.MEMBER, error)


class BuiltinCloudDesignProvider:
    step = "design.cloud_enrichment"

    def __init__(self, adapter: CloudDesignAdapter | None = None) -> None:
        self.adapter = adapter or CloudDesignAdapter()

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            design = dict(payload["design_result"])
            artifacts = design.get("artifacts") or {}
            missing = [name for name in ("class_diagram", "api_spec") if not artifacts.get(name)]
            if missing:
                raise ValueError("Design is missing implementation inputs: " + ", ".join(missing))
            cloud = self.adapter.finalize(
                requirements_result=dict(payload["requirements_result"]),
                design_result=design,
                use_cloud_kb=bool(payload.get("use_cloud_kb", True)),
            )
            return StepResult(
                step=self.step,
                provider=ProviderKind.BUILTIN,
                status=StepStatus.COMPLETED,
                output={"cloud_design_result": cloud},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.BUILTIN, error)


class MemberScaffoldProvider:
    """Use the member generator only for the initial application skeleton."""

    step = "implementation.scaffold"

    def __init__(self, settings: ImplementationSettings | None = None) -> None:
        self.settings = settings or ImplementationSettings.from_env()
        self.client = PrototypeClient(self.settings)

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        try:
            design = payload["design_result"]
            artifacts = design.get("artifacts") or {}
            generator_input = {
                "class_diagram_puml": artifacts.get("class_diagram") or "",
                "sequence_diagram_puml": artifacts.get("sequence_diagram") or "",
                "api_spec": artifacts.get("api_spec") or {},
                "erd_puml": artifacts.get("erd") or "",
                "deployment_diagram_puml": payload.get("cloud_design_result", {}).get(
                    "deployment_diagram_puml", ""
                ),
                "resource_spec": payload.get("requirements_result", {}).get(
                    "resource_spec", {}
                ),
            }
            job = self.client.prepare_job(
                f"orchestration-{context.run_id}",
                context.app_id,
                generator_input,
                "com.easydep.generated",
                True,
            )
            job_config = json.loads(job.read_text(encoding="utf-8"))
            job_config.setdefault("verification", {})["compile"] = False
            job.write_text(
                json.dumps(job_config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            completed = run_process_tree(
                [
                    str(self.settings.python_executable),
                    "-B",
                    "-m",
                    "app.core.orchestration.scaffold_worker",
                    str(job),
                ],
                cwd=self.settings.repository_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Member scaffold failed: "
                    + (completed.stderr or completed.stdout)[-4000:]
                )
            generated = None
            for line in reversed(completed.stdout.splitlines()):
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and candidate.get("run_root"):
                    generated = candidate
                    break
            if generated is None:
                raise RuntimeError("Member scaffold returned no structured result")
            run_root = Path(str(generated["run_root"]))
            return StepResult(
                step=self.step,
                provider=ProviderKind.MEMBER,
                status=StepStatus.COMPLETED,
                output={
                    "job_path": str(job),
                    "run_root": str(run_root),
                    "member_plan": generated.get("member_plan") or {},
                },
                artifacts={"application": str(run_root / "application")},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.MEMBER, error)


SCAFFOLD_SYSTEM_PROMPT = """You create the initial production sources for a minimal Java 21
Spring Boot REST application. Use the supplied requirements, OpenAPI contract, and design.
Return one JSON object with `files`, mapping only paths below src/main to complete Java or
YAML source contents. Include a Spring Boot application entry point, /health, and the API
surface required by the OpenAPI contract. Keep the scaffold compilable; later providers will
add acceptance tests and complete business logic. Do not return build, test, Docker, or
infrastructure files. Return JSON only and keep all text in English."""


class LlmScaffoldProvider:
    """Temporary scaffold boundary used while the member generator is incomplete."""

    step = "implementation.scaffold"

    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
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
                {"role": "system", "content": SCAFFOLD_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or "{}"

    @staticmethod
    def _apply(application: Path, files: Any) -> list[str]:
        if not isinstance(files, dict) or not files:
            raise ValueError("Scaffold generation returned no files")
        written: list[str] = []
        root = application.resolve()
        for raw_name, raw_content in files.items():
            name = str(raw_name).replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe scaffold path: {name}")
            if tuple(path.parts[:2]) != ("src", "main") or path.suffix not in {
                ".java",
                ".kt",
                ".yaml",
                ".yml",
            }:
                raise ValueError(f"Scaffold provider may write src/main only: {name}")
            target = (application / Path(*path.parts)).resolve()
            if root not in target.parents:
                raise ValueError(f"Path escapes generated application: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(raw_content), encoding="utf-8")
            written.append(name)
        if not any(name.endswith((".java", ".kt")) for name in written):
            raise ValueError("Scaffold generation returned no Java or Kotlin sources")
        return sorted(written)

    @staticmethod
    def _write_build(application: Path, app_id: str) -> None:
        application.mkdir(parents=True, exist_ok=False)
        (application / "settings.gradle").write_text(
            f"rootProject.name = '{app_id.replace(chr(39), '') or 'easydep-app'}'\n",
            encoding="utf-8",
        )
        (application / "build.gradle").write_text(
            """plugins {
    id 'java'
    id 'org.springframework.boot' version '3.3.5'
    id 'io.spring.dependency-management' version '1.1.6'
}

group = 'com.easydep.generated'
version = '0.0.1-SNAPSHOT'

java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }

repositories { mavenCentral() }

dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'org.springframework.boot:spring-boot-starter-validation'
    testImplementation 'org.springframework.boot:spring-boot-starter-test'
}

tasks.named('test') { useJUnitPlatform() }
""",
            encoding="utf-8",
        )

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        try:
            run_root = Path(".easydep/orchestration/workspaces") / context.run_id
            application = run_root / "application"
            if run_root.exists():
                raise FileExistsError(f"Run workspace already exists: {run_root}")
            self._write_build(application, context.app_id)
            prompt = json.dumps(
                {
                    "requirements": payload.get("requirements_result") or {},
                    "design": payload.get("design_result") or {},
                },
                ensure_ascii=False,
            )
            response = json.loads(self._invoke(prompt))
            written = self._apply(application, response.get("files"))
            return StepResult(
                step=self.step,
                provider=ProviderKind.LLM,
                status=StepStatus.COMPLETED,
                output={"run_root": str(run_root.resolve()), "scaffold_files": written},
                artifacts={"application": str(application.resolve())},
                metrics={"llm_calls": 1},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.LLM, error)


LOGIC_SYSTEM_PROMPT = """You complete business logic in a generated Java application.
Use the supplied requirements, OpenAPI contract, design, and existing production sources.
Return one JSON object with `files`, mapping repository-relative production source paths to
complete file contents. Never edit tests, build scripts, Docker files, or infrastructure.
Do not return null/default stubs or UnsupportedOperationException. Implement concrete normal
paths and preserve public signatures. Return JSON only and keep all text in English."""

ACCEPTANCE_TEST_SYSTEM_PROMPT = """You write immutable acceptance-oriented tests for a
generated Java Spring Boot application. Use the requirements, OpenAPI contract, design, and
existing production source signatures. Return one JSON object with `files`, mapping only
repository-relative paths below src/test to complete JUnit 5 test contents. Cover /health and
at least one concrete normal business path with meaningful expected values. Do not modify
production code, build scripts, or infrastructure. Do not weaken assertions. Return JSON only
and keep all text in English."""


class LlmAcceptanceTestsProvider:
    step = "implementation.acceptance_tests"

    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
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
                {"role": "system", "content": ACCEPTANCE_TEST_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or "{}"

    @staticmethod
    def _apply(application: Path, files: Any) -> list[str]:
        if not isinstance(files, dict) or not files:
            raise ValueError("Acceptance test generation returned no files")
        written: list[str] = []
        root = application.resolve()
        for raw_name, raw_content in files.items():
            name = str(raw_name).replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or path.suffix not in {".java", ".kt"}:
                raise ValueError(f"Unsafe test source path: {name}")
            if tuple(path.parts[:2]) != ("src", "test"):
                raise ValueError(f"Acceptance provider may write tests only: {name}")
            target = (application / Path(*path.parts)).resolve()
            if root not in target.parents:
                raise ValueError(f"Path escapes generated application: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(raw_content), encoding="utf-8")
            written.append(name)
        return sorted(written)

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            application = Path(payload["run_root"]) / "application"
            prompt = json.dumps(
                {
                    "requirements": payload.get("requirements_result") or {},
                    "design": payload.get("design_result") or {},
                    "productionSources": LlmLogicProvider._sources(application),
                },
                ensure_ascii=False,
            )
            response = json.loads(self._invoke(prompt))
            written = self._apply(application, response.get("files"))
            return StepResult(
                step=self.step,
                provider=ProviderKind.LLM,
                status=StepStatus.COMPLETED,
                output={"acceptance_tests": written},
                metrics={"llm_calls": 1},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.LLM, error)


class LlmLogicProvider:
    step = "implementation.logic"

    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
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
                {"role": "system", "content": LOGIC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or "{}"

    @staticmethod
    def _sources(application: Path) -> dict[str, str]:
        sources: dict[str, str] = {}
        size = 0
        for path in sorted((application / "src" / "main").rglob("*")):
            if not path.is_file() or path.suffix not in {".java", ".kt", ".yaml", ".yml"}:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            size += len(content)
            if size > 120_000:
                raise ValueError("Generated production source exceeds the one-call LLM limit")
            sources[path.relative_to(application).as_posix()] = content
        if not sources:
            raise ValueError("Member scaffold produced no production sources")
        return sources

    @staticmethod
    def _apply(application: Path, files: Any) -> list[str]:
        if not isinstance(files, dict) or not files:
            raise ValueError("Logic completion returned no files")
        written: list[str] = []
        root = application.resolve()
        for raw_name, raw_content in files.items():
            name = str(raw_name).replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or path.suffix not in {
                ".java",
                ".kt",
                ".yaml",
                ".yml",
            }:
                raise ValueError(f"Unsafe production source path: {name}")
            if tuple(path.parts[:2]) != ("src", "main"):
                raise ValueError(f"LLM may edit production sources only: {name}")
            target = (application / Path(*path.parts)).resolve()
            if root not in target.parents:
                raise ValueError(f"Path escapes generated application: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(raw_content), encoding="utf-8")
            written.append(name)
        return sorted(written)

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            application = Path(payload["run_root"]) / "application"
            sources = self._sources(application)
            prompt = json.dumps(
                {
                    "requirements": payload.get("requirements_result") or {},
                    "design": payload.get("design_result") or {},
                    "sources": sources,
                    "immutableAcceptanceTests": {
                        path.relative_to(application).as_posix(): path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        for path in sorted((application / "src" / "test").rglob("*"))
                        if path.is_file() and path.suffix in {".java", ".kt"}
                    },
                },
                ensure_ascii=False,
            )
            response = json.loads(self._invoke(prompt))
            written = self._apply(application, response.get("files"))
            return StepResult(
                step=self.step,
                provider=ProviderKind.LLM,
                status=StepStatus.COMPLETED,
                output={"files": written, "run_root": payload["run_root"]},
                metrics={"llm_calls": 1},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.LLM, error)


class BuiltinVmSelectionProvider:
    step = "implementation.vm_selection"

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            requirements = payload["requirements_result"]
            selection = select_vm_candidates(
                requirements.get("resource_spec") or {},
                requirements.get("deployment_needs") or {},
            )
            return StepResult(
                step=self.step,
                provider=ProviderKind.BUILTIN,
                status=StepStatus.COMPLETED,
                output={"vm_selection": selection},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.BUILTIN, error)


class LlmVmDeliveryProvider:
    step = "implementation.vm_delivery"

    def __init__(self, adapter: VmDeliveryAdapter | None = None) -> None:
        self.adapter = adapter or VmDeliveryAdapter()

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            delivery = self.adapter.generate(
                requirements_result=payload["requirements_result"],
                cloud_design_result=payload["cloud_design_result"],
                implementation_result={"run_root": payload["run_root"]},
            )
            return StepResult(
                step=self.step,
                provider=ProviderKind.LLM,
                status=StepStatus.COMPLETED,
                output={"vm_delivery": delivery},
                metrics={"llm_calls": 1},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.LLM, error)


class BuiltinTestingProvider:
    step = "testing.application"

    def __init__(self, adapter: TestingAdapter | None = None) -> None:
        self.adapter = adapter or TestingAdapter()

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            result = self.adapter.run(
                implementation_result={"run_root": payload["run_root"]},
                case_id=str(payload.get("case_id") or "adhoc"),
            )
            status = StepStatus.COMPLETED if result.get("passed") else StepStatus.FAILED
            diagnostics = []
            if status == StepStatus.FAILED:
                diagnostics.append(
                    Diagnostic(code="APPLICATION_TESTS_FAILED", message="Generated application tests failed.")
                )
            return StepResult(
                step=self.step,
                provider=ProviderKind.BUILTIN,
                status=status,
                output={"testing_result": result},
                diagnostics=diagnostics,
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.BUILTIN, error)
