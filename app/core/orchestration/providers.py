"""Concrete providers behind the stable orchestration step contracts."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from openai import OpenAI

from app.config import settings

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.core.orchestration.adapters.design import DesignAdapter
from app.core.orchestration.adapters.requirements import RequirementsAdapter
from app.core.orchestration.adapters.testing import TestingAdapter
from app.core.orchestration.adapters.vm_delivery import BindingMismatchError, VmDeliveryAdapter
from app.core.orchestration.api_traceability import missing_explicit_fields
from app.core.orchestration.app_cloud_contracts import (
    CloudCapabilityContract,
    DeploymentBindingContract,
    application_intent_contract_from_requirements,
    cloud_contract_from_legacy,
    dependency_declarations,
    derive_deployment_bindings,
    infer_application_contract,
    merge_application_contracts,
    validate_application_consistency,
    validate_binding_consistency,
)
from app.core.orchestration.contracts import (
    Diagnostic,
    ProviderKind,
    RunMode,
    StepContext,
    StepResult,
    StepStatus,
)
from app.core.orchestration.linux_runner_transport import (
    configured_runner_image,
    runner_command,
    to_container_path,
    to_host_path,
)
from app.core.orchestration.process import run_process_tree
from app.core.orchestration.provider_target import resolve_resource_spec
from app.core.orchestration.vm_selection import select_vm_candidates
from app.implementation.application.prototype import PrototypeClient
from app.implementation.config import ImplementationSettings
from app.requirements.schemas import ResourceAnswer


def _failure(step: str, provider: ProviderKind, error: Exception) -> StepResult:
    return StepResult(
        step=step,
        provider=provider,
        status=StepStatus.FAILED,
        diagnostics=[Diagnostic(code=type(error).__name__, message=str(error), severity="error")],
    )


def _consistency_failure(
    step: str,
    provider: ProviderKind,
    diagnostics: list[Any],
    *,
    output: dict[str, Any] | None = None,
) -> StepResult:
    return StepResult(
        step=step,
        provider=provider,
        status=StepStatus.FAILED,
        output=output or {},
        diagnostics=[
            Diagnostic(code=item.code, message=item.message, severity="error")
            for item in diagnostics
        ],
    )


def _consistency_outcome(
    step: str,
    provider: ProviderKind,
    diagnostics: list[Any],
    *,
    output: dict[str, Any] | None = None,
) -> StepResult:
    """자동 수정할 수 없는 계약 질문은 실패와 구분해 사용자에게 돌려준다."""
    questions = [
        item for item in diagnostics
        if item.details.get("decision") == "needsUserInput"
    ]
    if len(questions) != len(diagnostics):
        return _consistency_failure(step, provider, diagnostics, output=output)
    serialized = [item.model_dump(mode="json") for item in questions]
    return StepResult(
        step=step,
        provider=provider,
        status=StepStatus.NEEDS_INPUT,
        output={**(output or {}), "pending_consistency_diagnostics": serialized},
        diagnostics=[
            Diagnostic(code=item.code, message=item.message, severity="warning")
            for item in questions
        ],
        prompt={
            "kind": "app-cloud-consistency",
            "questions": [item.details for item in questions],
        },
    )


class MemberRequirementsProvider:
    step = "requirements.analysis"

    def __init__(self, adapter: RequirementsAdapter | None = None) -> None:
        self.adapter = adapter
        self._mode_adapters: dict[RunMode, RequirementsAdapter] = {}

    def _adapter(self, mode: RunMode) -> RequirementsAdapter:
        """실행 모드에 맞는 정적 요구사항 그래프를 선택한다.

        대화형 실행은 질문/피드백 checkpoint가 필요하지만 배치 실험은 입력 대기로
        멈추면 안 된다. 주입된 어댑터는 테스트·대체 구현의 명시적 선택이므로 그대로 쓴다.
        """
        if self.adapter is not None:
            return self.adapter
        if mode not in self._mode_adapters:
            self._mode_adapters[mode] = RequirementsAdapter(
                feedback_gates=mode == RunMode.INTERACTIVE
            )
        return self._mode_adapters[mode]

    @staticmethod
    def _resume_answer(previous: dict[str, Any], response: Any) -> Any:
        """리소스 되묻기의 답을 요구사항 편집과 구별되는 계약으로 감싼다."""
        questions = list(previous.get("resource_questions") or [])
        if not questions or isinstance(response, ResourceAnswer):
            return response
        if isinstance(response, dict):
            values = response.get("answers", response)
            if isinstance(values, dict):
                return ResourceAnswer(
                    answers={str(key): str(value) for key, value in values.items()}
                )
        if len(questions) == 1:
            return ResourceAnswer(
                answers={str(questions[0]["field"]): str(response)}
            )
        return response

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        adapter = self._adapter(context.mode)
        revision_suffix = (
            f":revision-{context.requirement_revision}"
            if context.requirement_revision
            else ""
        )
        thread_id = f"orchestration:{context.run_id}:requirements{revision_suffix}"
        try:
            previous = payload.get("member_result") or {}
            if previous and context.response is not None:
                result = adapter.resume(
                    app_id=context.app_id,
                    thread_id=thread_id,
                    answer=self._resume_answer(previous, context.response),
                )
            elif previous:
                result = previous
            else:
                result = adapter.start(
                    app_id=context.app_id,
                    thread_id=thread_id,
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
                metrics={
                    "llm_calls": int((result.get("telemetry") or {}).get("llm_calls") or 0),
                    "llm_seconds": float((result.get("telemetry") or {}).get("llm_seconds") or 0),
                    "llm_timing_events": (result.get("telemetry") or {}).get("llm_timing_events")
                    or [],
                },
            )
        except Exception as error:  # noqa: BLE001 - provider failure is data
            failed = _failure(self.step, ProviderKind.MEMBER, error)
            telemetry_result = adapter.last_telemetry
            failed.metrics = {
                "llm_calls": int(telemetry_result.get("llm_calls") or 0),
                "llm_seconds": float(telemetry_result.get("llm_seconds") or 0),
                "llm_timing_events": telemetry_result.get("llm_timing_events") or [],
            }
            return failed


class MemberDesignProvider:
    step = "design.architecture"

    def __init__(self, adapter: DesignAdapter | None = None) -> None:
        self.adapter = adapter or DesignAdapter()

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        try:
            previous = payload.get("member_result") or {}
            revision_suffix = (
                f":revision-{context.requirement_revision}"
                if context.requirement_revision
                else ""
            )
            session_id = f"orchestration:{context.run_id}:design{revision_suffix}"
            if previous and context.response is not None:
                result = self.adapter.resume(
                    session_id=session_id,
                    feedback=str(context.response),
                )
            elif previous:
                result = previous
            elif self.adapter.has_pending(session_id=session_id):
                result = self.adapter.retry_pending(session_id=session_id)
            else:
                result = self.adapter.start(
                    session_id=session_id,
                    requirements_result=dict(payload["requirements_result"]),
                )
            if context.mode == RunMode.BATCH:
                for _ in range(5):
                    if result.get("status") == "completed":
                        break
                    result = self.adapter.resume(
                        session_id=session_id,
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
                metrics={
                    "llm_calls": len(result.get("llm_timing_events") or []),
                    "llm_timing_events": result.get("llm_timing_events") or [],
                },
                prompt={
                    "stage": result.get("stage"),
                    "prompt": result.get("feedback_prompt"),
                }
                if status == StepStatus.NEEDS_INPUT
                else None,
            )
        except Exception as error:  # noqa: BLE001
            failed = _failure(self.step, ProviderKind.MEMBER, error)
            events = self.adapter.timing_events(f"orchestration:{context.run_id}:design")
            failed.metrics = {
                "llm_calls": len(events),
                "llm_timing_events": events,
            }
            return failed


class BuiltinCloudDesignProvider:
    step = "design.cloud_enrichment"

    def __init__(
        self,
        adapter: CloudDesignAdapter | None = None,
        revise_api: Callable[..., dict[str, Any]] | None = None,
        render_api: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.adapter = adapter or CloudDesignAdapter()
        if revise_api is None:
            from app.design.services.api_spec.reviser import revise_api_spec_model

            revise_api = revise_api_spec_model
        if render_api is None:
            from app.design.services.api_spec.openapi import build_openapi_from_model

            render_api = build_openapi_from_model
        self._revise_api = revise_api
        self._render_api = render_api

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            design = dict(payload["design_result"])
            artifacts = design.get("artifacts") or {}
            missing = [name for name in ("class_diagram", "api_spec") if not artifacts.get(name)]
            if missing:
                raise ValueError("Design is missing implementation inputs: " + ", ".join(missing))
            field_mismatches = missing_explicit_fields(
                list(payload["requirements_result"].get("requirements") or []),
                dict(artifacts["api_spec"]),
            )
            llm_calls = 0
            repair_enabled = bool(payload.get("enable_repair_feedback", True))
            if field_mismatches and repair_enabled:
                details = ", ".join(
                    f"{item.requirement_id}:{item.direction}:{item.field}"
                    for item in field_mismatches
                )
                current_model = design.get("api_spec_model")
                if not isinstance(current_model, dict) or not current_model:
                    raise ValueError(
                        "OpenAPI omits explicitly required JSON field(s), but no "
                        "structured API model is available for one repair: " + details
                    )
                context_text = "\n\n".join(
                    (
                        "[Requirements]\n"
                        + json.dumps(
                            payload["requirements_result"].get("requirements") or [],
                            ensure_ascii=False,
                        ),
                        "[Class Diagram]\n" + str(artifacts.get("class_diagram") or ""),
                        "[Sequence Diagram]\n" + str(artifacts.get("sequence_diagram") or ""),
                    )
                )
                feedback = (
                    "The independent requirements-to-OpenAPI traceability gate found "
                    "these missing explicit JSON fields: " + details + ". Correct the "
                    "structured API model without changing unrelated requirements."
                )
                revised_model = self._revise_api(current_model, feedback, context_text, None)
                revised_api = self._render_api(revised_model)
                design["api_spec_model"] = revised_model
                design["artifacts"] = {**artifacts, "api_spec": revised_api}
                artifacts = design["artifacts"]
                llm_calls = 1
                remaining = missing_explicit_fields(
                    list(payload["requirements_result"].get("requirements") or []),
                    revised_api,
                )
                if remaining:
                    remaining_details = ", ".join(
                        f"{item.requirement_id}:{item.direction}:{item.field}" for item in remaining
                    )
                    raise ValueError(
                        "OpenAPI still omits explicitly required JSON field(s) after "
                        "one repair: " + remaining_details
                    )
            cloud = self.adapter.finalize(
                requirements_result=dict(payload["requirements_result"]),
                design_result=design,
                use_cloud_kb=bool(payload.get("use_cloud_kb", True)),
            )
            return StepResult(
                step=self.step,
                provider=ProviderKind.BUILTIN,
                status=StepStatus.COMPLETED,
                output={"design_result": design, "cloud_design_result": cloud},
                metrics={
                    "llm_calls": llm_calls,
                    "api_traceability_repaired": bool(llm_calls),
                    "repair_feedback_enabled": repair_enabled,
                    "api_traceability_mismatches_observed": len(field_mismatches),
                },
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
                "resource_spec": payload.get("requirements_result", {}).get("resource_spec", {}),
            }
            job = self.client.prepare_job(
                f"orchestration-{context.run_id}",
                context.app_id,
                generator_input,
                "com.easydep.generated",
                True,
            )
            job_config = json.loads(job.read_text(encoding="utf-8"))
            execute_member_workflow = settings.easydep_approve_member_implementation == "1"
            job_config.setdefault("verification", {})["compile"] = (
                execute_member_workflow
            )
            job.write_text(json.dumps(job_config, ensure_ascii=False, indent=2), encoding="utf-8")
            runner_image = configured_runner_image()
            worker_arguments = [str(job)]
            if execute_member_workflow:
                worker_arguments.append("--run-implemented-workflow")
            if context.checkpoint_retry_attempt > 0:
                worker_arguments.append("--retry-failed-generation")
            worker_environment = os.environ.copy()
            if os.name == "nt":
                hook_root = self.settings.repository_root / "app/core/orchestration/runtime_hooks"
                existing_pythonpath = worker_environment.get("PYTHONPATH")
                worker_environment["PYTHONPATH"] = os.pathsep.join(
                    part
                    for part in (
                        str(hook_root),
                        str(self.settings.repository_root),
                        existing_pythonpath,
                    )
                    if part
                )
                worker_environment["EASYDEP_DOCKER_WINDOWS_WORKSPACE"] = str(
                    self.settings.repository_root.resolve()
                )
            if worker_environment.get("API_KEY") and not any(
                worker_environment.get(name)
                for name in ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "LLM_API_KEY")
            ):
                worker_environment["LLM_API_KEY"] = worker_environment["API_KEY"]
            if runner_image:
                worker_arguments[0] = str(
                    to_container_path(job, self.settings.repository_root)
                )
                command = runner_command(
                    image=runner_image,
                    repository_root=self.settings.repository_root,
                    operation="worker",
                    arguments=worker_arguments,
                    environment=worker_environment,
                )
            else:
                command = [
                    str(self.settings.python_executable),
                    "-B",
                    "-m",
                    "app.core.orchestration.scaffold_worker",
                    *worker_arguments,
                ]
            completed = run_process_tree(
                command,
                cwd=self.settings.repository_root,
                env=worker_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Member scaffold failed: " + (completed.stderr or completed.stdout)[-4000:]
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
            generated_run_root = str(generated["run_root"])
            if runner_image:
                generated_run_root = to_host_path(
                    generated_run_root, self.settings.repository_root
                )
            run_root = Path(generated_run_root)
            output = {
                "job_path": str(job),
                "run_root": str(run_root),
                "member_plan": generated.get("member_plan") or {},
                "member_workflow_status": generated.get(
                    "member_workflow_status"
                ) or (generated.get("member_plan") or {}).get("status"),
                "member_workflow_executed": execute_member_workflow,
                "member_runner": (
                    {"kind": "linux-container", "image": runner_image}
                    if runner_image
                    else {"kind": "host"}
                ),
            }
            if not execute_member_workflow:
                return StepResult(
                    step=self.step,
                    provider=ProviderKind.MEMBER,
                    status=StepStatus.NEEDS_INPUT,
                    output=output,
                    artifacts={"application": str(run_root / "application")},
                    diagnostics=[
                        Diagnostic(
                            code="MEMBER-APPROVAL-REQUIRED",
                            message=(
                                "The planned OpenHands implementation workflow requires "
                                "explicit external-transmission approval."
                            ),
                        )
                    ],
                )
            return StepResult(
                step=self.step,
                provider=ProviderKind.MEMBER,
                status=StepStatus.COMPLETED,
                output=output,
                artifacts={"application": str(run_root / "application")},
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.MEMBER, error)


SCAFFOLD_SYSTEM_PROMPT = """You create the initial production sources for a minimal Java 21
Spring Boot REST application. Use the supplied requirements, OpenAPI contract, and design.
Return one JSON object with `files`, an optional `runtimeContract`, and an optional
`bindingContract`. `files` maps only paths
below src/main to complete Java or YAML source contents. `runtimeContract` uses
ApplicationRuntimeContract/v1 and may declare open `facts` with `id`, `kind`, and `attributes`;
do not infer a database engine from a cloud volume requirement. File-backed runtime paths must
be configurable through an environment placeholder declared as a runtime.environment fact.
Include a Spring Boot entry point, /health, and the API
surface required by the OpenAPI contract. Keep the scaffold compilable; later providers will
add acceptance tests and complete business logic. Do not return build, test, Docker, or
infrastructure files. The build uses Spring Boot 3.3 and Spring Framework 6: never annotate a
method with `@Override` unless its exact superclass or interface signature is present and
version-compatible. Prefer standalone `@ExceptionHandler` methods over guessing protected
framework override signatures. When `consistencyResolution` is present, `files` is a complete
replacement snapshot of src/main; omit obsolete sources rather than preserving the rejected
state mechanism. Return JSON only and keep all text in English."""

PRODUCTION_SOURCE_SUFFIXES = frozenset(
    {
        ".java",
        ".json",
        ".kt",
        ".properties",
        ".sql",
        ".yaml",
        ".yml",
    }
)


def _completion_options() -> dict[str, int]:
    value = settings.llm_max_completion_tokens
    return {"max_completion_tokens": int(value)} if value else {}


class LlmScaffoldProvider:
    """Temporary scaffold boundary used while the member generator is incomplete."""

    step = "implementation.scaffold"

    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
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
            **_completion_options(),
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
            if (
                tuple(path.parts[:2]) != ("src", "main")
                or path.suffix not in PRODUCTION_SOURCE_SUFFIXES
            ):
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
    def _write_build(
        application: Path,
        app_id: str,
        *,
        dependencies: list[tuple[str, str]] | None = None,
    ) -> None:
        (application / "settings.gradle").write_text(
            f"rootProject.name = '{app_id.replace(chr(39), '') or 'easydep-app'}'\n",
            encoding="utf-8",
        )
        dependency_lines = "".join(
            f"    {configuration} '{coordinate}'\n"
            for configuration, coordinate in sorted(set(dependencies or []))
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
"""
            + dependency_lines
            + """    testImplementation 'org.springframework.boot:spring-boot-starter-test'
}

tasks.named('test') { useJUnitPlatform() }
""",
            encoding="utf-8",
        )

    @staticmethod
    def _merge_build_dependencies(
        application: Path,
        app_id: str,
        dependencies: list[tuple[str, str]] | None = None,
    ) -> None:
        """기존 생성기의 빌드 계약을 보존하며 새 런타임 의존성만 보탠다."""
        build_path = application / "build.gradle"
        if not build_path.is_file():
            LlmScaffoldProvider._write_build(
                application,
                app_id,
                dependencies=dependencies,
            )
            return

        content = build_path.read_text(encoding="utf-8")
        missing = [
            (configuration, coordinate)
            for configuration, coordinate in sorted(set(dependencies or []))
            if coordinate not in content
        ]
        if not missing:
            return

        lines = content.splitlines(keepends=True)
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if re.match(r"^\s*dependencies\s*\{", line)
            ),
            None,
        )
        dependency_lines = [
            f"    {configuration} '{coordinate}'\n"
            for configuration, coordinate in missing
        ]
        if start is None:
            separator = "" if not content or content.endswith(("\n", "\r")) else "\n"
            build_path.write_text(
                content
                + separator
                + "\ndependencies {\n"
                + "".join(dependency_lines)
                + "}\n",
                encoding="utf-8",
            )
            return

        depth = 0
        end = None
        for index in range(start, len(lines)):
            depth += lines[index].count("{") - lines[index].count("}")
            if depth == 0:
                end = index
                break
        if end is None:
            raise ValueError("Existing Gradle dependencies block is not balanced")
        lines[end:end] = dependency_lines
        build_path.write_text("".join(lines), encoding="utf-8")

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        created_workspace = False
        resolution_backup: Path | None = None
        try:
            run_root = Path(".easydep/orchestration/workspaces") / context.run_id
            application = run_root / "application"
            retry_root = str(payload.get("run_root") or "")
            if run_root.exists() and Path(retry_root).resolve() != run_root.resolve():
                raise FileExistsError(f"Run workspace already exists: {run_root}")
            requirements_result = payload.get("requirements_result") or {}
            created_workspace = not run_root.exists()
            application.mkdir(parents=True, exist_ok=True)
            pending = list(payload.get("pending_consistency_diagnostics") or [])
            resolution = None
            if pending and context.response is not None:
                response = context.response
                if isinstance(response, dict):
                    resolution = str(response.get("resolution") or "")
                else:
                    resolution = str(response)
                allowed_resolutions = {
                    str(alternative.get("id") or "")
                    for item in pending
                    for alternative in (item.get("details") or {}).get(
                        "alternatives", []
                    )
                }
                if (
                    resolution not in allowed_resolutions
                    or resolution != "externalize-or-replicate-state"
                ):
                    return StepResult(
                        step=self.step,
                        provider=ProviderKind.LLM,
                        status=StepStatus.NEEDS_INPUT,
                        output={
                            "run_root": str(run_root.resolve()),
                            "pending_consistency_diagnostics": pending,
                        },
                        prompt={
                            "kind": "app-cloud-consistency",
                            "questions": [item.get("details") or {} for item in pending],
                            "upstreamRevisionResponses": [
                                {
                                    "resolution": item,
                                    "revisedRequirements": [
                                        "Provide the complete active requirements with the requested revisions."
                                    ],
                                }
                                for item in sorted(allowed_resolutions)
                                if item.startswith("revise-")
                                and item.endswith("-requirement")
                            ],
                            "note": (
                                "Changing an upstream requirement requires the user to provide the "
                                "complete revised active requirements. The implementation stage must "
                                "not relax a requirement-owned contract on its own."
                            ),
                        },
                    )
            existing_sources = {
                path.relative_to(application).as_posix(): path.read_text(
                    encoding="utf-8", errors="replace"
                )
                for path in sorted((application / "src" / "main").rglob("*"))
                if path.is_file() and path.suffix in PRODUCTION_SOURCE_SUFFIXES
            }
            if resolution:
                backup_root = Path(
                    tempfile.mkdtemp(
                        prefix=".easydep-consistency-repair-",
                        dir=run_root.parent,
                    )
                )
                resolution_backup = backup_root / "application"
                shutil.copytree(application, resolution_backup)
            prompt = json.dumps(
                {
                    "requirements": payload.get("requirements_result") or {},
                    "design": payload.get("design_result") or {},
                    "existingSources": existing_sources,
                    "consistencyResolution": (
                        {
                            "choice": resolution,
                            "instruction": (
                                "Remove the node-filesystem state dependency. Use an "
                                "external or explicitly replicated state mechanism that "
                                "can support the requested multi-zone deployment. Preserve "
                                "unrelated API behavior."
                            ),
                            "diagnostics": pending,
                        }
                        if resolution
                        else None
                    ),
                },
                ensure_ascii=False,
            )
            response = json.loads(self._invoke(prompt))
            if resolution:
                production_root = application / "src" / "main"
                if production_root.is_dir():
                    shutil.rmtree(production_root)
            written = self._apply(application, response.get("files"))
            declared_contract = response.get("runtimeContract")
            requirement_intent = application_intent_contract_from_requirements(
                requirements_result
            )
            contract = infer_application_contract(
                application,
                merge_application_contracts(
                    requirement_intent, declared_contract
                ).model_dump(mode="json", by_alias=True),
            )
            self._write_build(
                application,
                context.app_id,
                dependencies=dependency_declarations(contract),
            )
            validator_enabled = bool(payload.get("enable_consistency_validator", True))
            diagnostics = (
                validate_application_consistency(application, contract) if validator_enabled else []
            )
            if diagnostics:
                if resolution_backup is not None:
                    shutil.rmtree(application)
                    shutil.copytree(resolution_backup, application)
                    shutil.rmtree(resolution_backup.parent)
                    resolution_backup = None
                return _consistency_failure(
                    self.step,
                    ProviderKind.LLM,
                    diagnostics,
                    output={
                        "run_root": str(run_root.resolve()),
                        "application_runtime_contract": contract.model_dump(
                            mode="json", by_alias=True
                        ),
                    },
                )
            cloud_contract = cloud_contract_from_legacy(requirements_result)
            cloud_contract, binding_contract = derive_deployment_bindings(
                contract,
                cloud_contract,
                response.get("bindingContract"),
            )
            binding_diagnostics = (
                validate_binding_consistency(contract, cloud_contract, binding_contract)
                if validator_enabled
                else []
            )
            if binding_diagnostics:
                outcome = _consistency_outcome(
                    self.step,
                    ProviderKind.LLM,
                    binding_diagnostics,
                    output={
                        "run_root": str(run_root.resolve()),
                        "application_runtime_contract": contract.model_dump(
                            mode="json", by_alias=True
                        ),
                        "cloud_capability_contract": cloud_contract.model_dump(
                            mode="json", by_alias=True
                        ),
                        "deployment_binding_contract": binding_contract.model_dump(
                            mode="json", by_alias=True
                        ),
                    },
                )
                if resolution_backup is not None:
                    shutil.rmtree(application)
                    shutil.copytree(resolution_backup, application)
                    shutil.rmtree(resolution_backup.parent)
                    resolution_backup = None
                if context.mode == RunMode.BATCH:
                    outcome.status = StepStatus.FAILED
                    outcome.diagnostics.append(
                        Diagnostic(
                            code="BATCH_INPUT_REQUIRED",
                            message=(
                                "Application-cloud consistency requires a user decision "
                                "that is absent from the batch case."
                            ),
                        )
                    )
                return outcome
            if resolution_backup is not None:
                shutil.rmtree(resolution_backup.parent)
                resolution_backup = None
            return StepResult(
                step=self.step,
                provider=ProviderKind.LLM,
                status=StepStatus.COMPLETED,
                output={
                    "run_root": str(run_root.resolve()),
                    "scaffold_files": written,
                    "application_runtime_contract": contract.model_dump(mode="json", by_alias=True),
                    "cloud_capability_contract": cloud_contract.model_dump(
                        mode="json", by_alias=True
                    ),
                    "deployment_binding_contract": binding_contract.model_dump(
                        mode="json", by_alias=True
                    ),
                },
                artifacts={"application": str(application.resolve())},
                metrics={
                    "llm_calls": 1,
                    "consistency_validator_enabled": validator_enabled,
                    "consistency_resolution": resolution,
                },
            )
        except Exception as error:  # noqa: BLE001
            if resolution_backup is not None and resolution_backup.is_dir():
                if application.is_dir():
                    shutil.rmtree(application)
                shutil.copytree(resolution_backup, application)
                shutil.rmtree(resolution_backup.parent)
            if created_workspace and run_root.is_dir():
                shutil.rmtree(run_root)
            return _failure(self.step, ProviderKind.LLM, error)


LOGIC_SYSTEM_PROMPT = """You complete business logic in a generated Java application.
Use the supplied requirements, OpenAPI contract, design, and existing production sources.
Return one JSON object with `files`, mapping repository-relative production source paths to
complete file contents. Never edit tests, build scripts, Docker files, or infrastructure.
Do not return null/default stubs or UnsupportedOperationException. Implement concrete normal
paths and preserve public signatures. The build uses Spring Boot 3.3 and Spring Framework 6:
never add `@Override` unless the exact inherited signature is known from the existing source
contract. When an OpenAPI boundary exposes a request body as `Object` or `Map`, never cast it
directly to a generated DTO; perform explicit validated field extraction or use a configured
object mapper. When requirements define atomic state changes under concurrent requests, a
read-then-write precheck alone is insufficient; use transactional database constraints and an
appropriate locking or conflict-detection strategy. If the supplied production sources already
implement every required behavior, return
an explicit empty `files` object; the immutable tests and build will verify that no-op decision.
Return JSON only and keep all text in English."""

ACCEPTANCE_TEST_SYSTEM_PROMPT = """You write immutable acceptance-oriented tests for a
generated Java Spring Boot application. Use the requirements, OpenAPI contract, design, and
existing production source signatures. Return one JSON object with `files`, mapping only
repository-relative paths below src/test to complete JUnit 5 test contents. Cover /health and
at least one concrete normal business path with meaningful expected values. Do not modify
production code, build scripts, or infrastructure. Do not weaken assertions. Return JSON only
and keep all text in English. The generated build uses Spring Boot 3.3. For random-port tests,
import `LocalServerPort` only from `org.springframework.boot.test.web.server`; the legacy
`org.springframework.boot.web.server` package is not available. Never encode a known stub,
exception, or unimplemented response as the expected behavior when requirements define a
successful outcome. When repair feedback is supplied, reconcile every existing test that
contradicts the requirements while preserving unrelated meaningful assertions."""

TEST_RESOURCE_SUFFIXES = frozenset(
    {".csv", ".json", ".properties", ".sql", ".txt", ".yaml", ".yml"}
)


class LlmAcceptanceTestsProvider:
    step = "implementation.acceptance_tests"

    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
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
            **_completion_options(),
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
            test_source = tuple(path.parts[:3]) in {
                ("src", "test", "java"),
                ("src", "test", "kotlin"),
            } and path.suffix in {".java", ".kt"}
            test_resource = (
                tuple(path.parts[:3]) == ("src", "test", "resources")
                and path.suffix in TEST_RESOURCE_SUFFIXES
            )
            if path.is_absolute() or ".." in path.parts or not (test_source or test_resource):
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
            if payload.get("member_workflow_status") == "COMPLETE":
                return StepResult(
                    step=self.step,
                    provider=ProviderKind.LLM,
                    status=StepStatus.COMPLETED,
                    output={
                        "acceptance_tests": [],
                        "member_output_preserved": True,
                    },
                    metrics={"llm_calls": 0, "member_workflow_complete": True},
                )
            application = Path(payload["run_root"]) / "application"
            existing_tests = {
                path.relative_to(application).as_posix(): path.read_text(
                    encoding="utf-8", errors="replace"
                )
                for path in sorted((application / "src" / "test").rglob("*"))
                if path.is_file() and path.suffix in {".java", ".kt"}
            }
            prompt = json.dumps(
                {
                    "instruction": (
                        "Resolve repairFeedback by strengthening or correcting tests "
                        "that contradict the requirements. Do not preserve expectations "
                        "for known broken or unimplemented behavior."
                        if payload.get("repair_feedback")
                        else "Generate acceptance tests from the requirements."
                    ),
                    "requirements": payload.get("requirements_result") or {},
                    "design": payload.get("design_result") or {},
                    "productionSources": LlmLogicProvider._sources(application),
                    "existingTests": existing_tests,
                    "repairFeedback": payload.get("repair_feedback") or [],
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
    _REPAIR_LOCATION_LIMIT = 12

    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
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
            **_completion_options(),
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
            if not path.is_file() or path.suffix not in PRODUCTION_SOURCE_SUFFIXES:
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
    def _requirements_context(result: dict[str, Any]) -> dict[str, Any]:
        """Project the stable requirement products, excluding agent execution history."""
        return {
            key: result[key]
            for key in ("requirements", "deployment_needs", "resource_spec")
            if key in result
        }

    @staticmethod
    def _design_context(result: dict[str, Any]) -> dict[str, Any]:
        """Project application-facing design products rather than the whole design state."""
        artifacts = result.get("artifacts") or {}
        context = {
            "apiSpec": artifacts.get("api_spec") or result.get("api_spec_model"),
            "classDiagram": artifacts.get("class_diagram"),
            "sequenceDiagram": artifacts.get("sequence_diagram"),
            "erd": artifacts.get("erd"),
        }
        return {
            key: value for key, value in context.items() if value is not None and value != ""
        }

    @classmethod
    def _repair_feedback(cls, feedback: Any) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for raw_item in feedback if isinstance(feedback, list) else []:
            if not isinstance(raw_item, dict):
                continue
            item = {
                key: raw_item[key]
                for key in ("code", "message", "severity", "details")
                if key in raw_item
            }
            locations = [str(value) for value in raw_item.get("locations") or []]
            if locations:
                item["locations"] = locations[: cls._REPAIR_LOCATION_LIMIT]
                if len(locations) > cls._REPAIR_LOCATION_LIMIT:
                    item["locationCount"] = len(locations)
            compact.append(item)
        return compact

    @staticmethod
    def _acceptance_tests(application: Path) -> dict[str, str]:
        return {
            path.relative_to(application).as_posix(): path.read_text(
                encoding="utf-8", errors="replace"
            )
            for path in sorted((application / "src" / "test").rglob("*"))
            if path.is_file()
            and path.suffix in {".java", ".kt"}
            and "acceptance" in {part.lower() for part in path.parts}
        }

    @staticmethod
    def _close_build_dependencies(
        application: Path,
        contract: Any,
        app_id: str,
    ) -> tuple[Any, list[tuple[str, str]]]:
        declarations = dependency_declarations(contract)
        LlmScaffoldProvider._merge_build_dependencies(
            application,
            app_id,
            dependencies=declarations,
        )
        refreshed = infer_application_contract(
            application,
            contract.model_dump(mode="json", by_alias=True),
        )
        return refreshed, declarations

    @staticmethod
    def _apply(application: Path, files: Any) -> list[str]:
        if not isinstance(files, dict):
            raise TypeError("Logic completion must return a files object")
        written: list[str] = []
        root = application.resolve()
        for raw_name, raw_content in files.items():
            name = str(raw_name).replace("\\", "/")
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.suffix not in PRODUCTION_SOURCE_SUFFIXES
            ):
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
            if (
                payload.get("member_workflow_status") == "COMPLETE"
                and not payload.get("repair_feedback")
            ):
                application = Path(payload["run_root"]) / "application"
                requirement_intent = application_intent_contract_from_requirements(
                    payload.get("requirements_result") or {}
                )
                declared_contract = merge_application_contracts(
                    requirement_intent,
                    payload.get("application_runtime_contract"),
                )
                contract = infer_application_contract(
                    application,
                    declared_contract.model_dump(mode="json", by_alias=True),
                )
                contract, dependency_closure = self._close_build_dependencies(
                    application,
                    contract,
                    context.app_id,
                )
                validator_enabled = bool(payload.get("enable_consistency_validator", True))
                diagnostics = (
                    validate_application_consistency(application, contract)
                    if validator_enabled
                    else []
                )
                if diagnostics:
                    return _consistency_failure(
                        self.step,
                        ProviderKind.LLM,
                        diagnostics,
                        output={
                            "run_root": payload["run_root"],
                            "application_runtime_contract": contract.model_dump(
                                mode="json", by_alias=True
                            ),
                        },
                    )
                cloud_contract = CloudCapabilityContract.model_validate(
                    payload.get("cloud_capability_contract") or {}
                )
                binding_contract = DeploymentBindingContract.model_validate(
                    payload.get("deployment_binding_contract") or {}
                )
                cloud_contract, binding_contract = derive_deployment_bindings(
                    contract,
                    cloud_contract,
                    binding_contract.model_dump(mode="json", by_alias=True),
                )
                return StepResult(
                    step=self.step,
                    provider=ProviderKind.LLM,
                    status=StepStatus.COMPLETED,
                    output={
                        "files": [],
                        "member_output_preserved": True,
                        "application_runtime_contract": contract.model_dump(
                            mode="json", by_alias=True
                        ),
                        "cloud_capability_contract": cloud_contract.model_dump(
                            mode="json", by_alias=True
                        ),
                        "deployment_binding_contract": binding_contract.model_dump(
                            mode="json", by_alias=True
                        ),
                    },
                    metrics={
                        "llm_calls": 0,
                        "member_workflow_complete": True,
                        "dependency_closure": len(dependency_closure),
                    },
                )
            application = Path(payload["run_root"]) / "application"
            sources = self._sources(application)
            prompt_payload = {
                "instruction": (
                    "Resolve every supplied repairFeedback item in production files before "
                    "finishing. Do not edit immutable acceptance tests."
                    if payload.get("repair_feedback")
                    else "Implement the requested application logic."
                ),
                "requirements": self._requirements_context(
                    payload.get("requirements_result") or {}
                ),
                "design": self._design_context(payload.get("design_result") or {}),
                "sources": sources,
                "repairFeedback": self._repair_feedback(payload.get("repair_feedback")),
                "immutableAcceptanceTests": self._acceptance_tests(application),
            }
            prompt = json.dumps(prompt_payload, ensure_ascii=False)
            prompt_metrics = {
                "characters": len(prompt),
                "sourceFiles": len(sources),
                "acceptanceTestFiles": len(prompt_payload["immutableAcceptanceTests"]),
                "repairDiagnostics": len(prompt_payload["repairFeedback"]),
            }
            response = json.loads(self._invoke(prompt))
            if "files" not in response:
                raise ValueError("Logic completion omitted the files object")
            written = self._apply(application, response["files"])
            contract = infer_application_contract(
                application,
                payload.get("application_runtime_contract"),
            )
            contract, dependency_closure = self._close_build_dependencies(
                application,
                contract,
                context.app_id,
            )
            validator_enabled = bool(payload.get("enable_consistency_validator", True))
            diagnostics = (
                validate_application_consistency(application, contract) if validator_enabled else []
            )
            if diagnostics:
                return _consistency_failure(
                    self.step,
                    ProviderKind.LLM,
                    diagnostics,
                    output={"run_root": payload["run_root"], "promptMetrics": prompt_metrics},
                )
            cloud_contract = CloudCapabilityContract.model_validate(
                payload.get("cloud_capability_contract") or {}
            )
            binding_contract = DeploymentBindingContract.model_validate(
                payload.get("deployment_binding_contract") or {}
            )
            cloud_contract, binding_contract = derive_deployment_bindings(
                contract,
                cloud_contract,
                binding_contract.model_dump(mode="json", by_alias=True),
            )
            binding_diagnostics = (
                validate_binding_consistency(contract, cloud_contract, binding_contract)
                if validator_enabled
                else []
            )
            if binding_diagnostics:
                return _consistency_failure(self.step, ProviderKind.LLM, binding_diagnostics)
            return StepResult(
                step=self.step,
                provider=ProviderKind.LLM,
                status=StepStatus.COMPLETED,
                output={
                    "files": written,
                    "run_root": payload["run_root"],
                    "noChanges": not written,
                    "application_runtime_contract": contract.model_dump(mode="json", by_alias=True),
                    "cloud_capability_contract": cloud_contract.model_dump(
                        mode="json", by_alias=True
                    ),
                    "deployment_binding_contract": binding_contract.model_dump(
                        mode="json", by_alias=True
                    ),
                },
                metrics={
                    "llm_calls": 1,
                    "consistency_validator_enabled": validator_enabled,
                    "prompt": prompt_metrics,
                    "dependency_closure": len(dependency_closure),
                },
            )
        except Exception as error:  # noqa: BLE001
            return _failure(self.step, ProviderKind.LLM, error)


class BuiltinVmSelectionProvider:
    step = "implementation.vm_selection"

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            requirements = payload["requirements_result"]
            resource_spec = resolve_resource_spec(
                requirements.get("resource_spec") or {},
                str(payload.get("resource_constraints_text") or ""),
            )
            selection = select_vm_candidates(
                resource_spec,
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
                application_runtime_contract=payload.get("application_runtime_contract"),
                cloud_capability_contract=payload.get("cloud_capability_contract"),
                deployment_binding_contract=payload.get("deployment_binding_contract"),
                enable_repair_feedback=bool(payload.get("enable_repair_feedback", True)),
                enable_consistency_validator=bool(
                    payload.get("enable_consistency_validator", True)
                ),
                resource_constraints_text=str(payload.get("resource_constraints_text") or ""),
            )
            return StepResult(
                step=self.step,
                provider=ProviderKind.LLM,
                status=StepStatus.COMPLETED,
                output={"vm_delivery": delivery},
                metrics={
                    "llm_calls": int(delivery.get("llmCalls") or 1),
                    "timing_events": delivery.get("timingEvents") or [],
                    "consistency_validator_enabled": bool(
                        payload.get("enable_consistency_validator", True)
                    ),
                },
            )
        except Exception as error:  # noqa: BLE001
            failed = (
                StepResult(
                    step=self.step,
                    provider=ProviderKind.LLM,
                    status=StepStatus.FAILED,
                    diagnostics=[Diagnostic(code=error.code, message=str(error), severity="error")],
                )
                if isinstance(error, BindingMismatchError)
                else _failure(self.step, ProviderKind.LLM, error)
            )
            failed.metrics = {
                "llm_calls": sum(
                    event.get("operation") in {"iac.generate", "iac.repair"}
                    for event in self.adapter.last_timing_events
                ),
                "timing_events": self.adapter.last_timing_events,
                "consistency_validator_enabled": bool(
                    payload.get("enable_consistency_validator", True)
                ),
            }
            return failed


class BuiltinTestingProvider:
    step = "testing.application"

    def __init__(self, adapter: TestingAdapter | None = None) -> None:
        self.adapter = adapter or TestingAdapter()

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:  # noqa: ARG002
        try:
            result = self.adapter.run(
                implementation_result=payload,
                case_id=str(payload.get("case_id") or "adhoc"),
            )
            # Static analysis and the dynamic checks run through the same shared
            # verification pass the web testing API uses.  Its nodes read the
            # implementation agent's stored snapshots by app id; the directories
            # below are only the fallback for a run whose output was never
            # persisted, so they come from the workspace the adapter just tested.
            try:
                from app.testing.runtime.verification import run_verification_graph

                application = Path(str(payload.get("run_root") or "")) / "application"
                verification = run_verification_graph(
                    run_id=context.run_id,
                    app_id=context.app_id,
                    manifests_dir=str(application / "k8s"),
                    iac_dir=str(application / "terraform"),
                )
                result["verification"] = verification
            except Exception as graph_error:  # noqa: BLE001
                verification = None
                result["agent_testing_error"] = str(graph_error)

            unit_passed = bool(result.get("passed"))
            dynamic_passed = verification["passed"] if verification else False

            status = (
                StepStatus.COMPLETED
                if unit_passed and dynamic_passed
                else StepStatus.FAILED
            )

            diagnostics = []
            if not unit_passed:
                diagnostics.extend(
                    Diagnostic.model_validate(item)
                    for item in result.get("diagnostics")
                    or [
                        {
                            "code": "APPLICATION_TESTS_FAILED",
                            "message": "Generated application tests failed.",
                        }
                    ]
                )
            if verification is None:
                diagnostics.append(
                    Diagnostic.model_validate({
                        "code": "DYNAMIC_TESTS_CRASHED",
                        "message": f"Testing Agent Graph crashed: {result['agent_testing_error']}"
                    })
                )
            elif not dynamic_passed:
                diagnostics.append(
                    Diagnostic.model_validate({
                        "code": "DYNAMIC_TESTS_FAILED",
                        "message": f"Dynamic functional tests failed: {verification['blockingReason']}"
                    })
                )
            # Misconfiguration findings are reported but do not gate the step:
            # they describe the deployment artifacts, not whether the generated
            # application works. Without this they were computed and discarded.
            if verification:
                diagnostics.extend(
                    Diagnostic.model_validate(item)
                    for item in verification["diagnostics"]
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
