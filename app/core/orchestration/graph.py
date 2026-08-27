"""Four-stage LangGraph with explicit, replaceable substep providers."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import settings
from langgraph.graph import END, START, StateGraph

from app.core.orchestration.adapters.testing import TestingAdapter
from app.core.orchestration.artifacts import persist_run_artifacts, restore_run_application
from app.core.orchestration.contracts import (
    DesignOutput,
    ImplementationOutput,
    OrchestrationState,
    ProviderKind,
    RequirementsOutput,
    RunMode,
    RunRequest,
    RunResult,
    StageName,
    StepContext,
    StepResult,
    StepStatus,
    TestingOutput,
)
from app.core.orchestration.providers import (
    BuiltinCloudDesignProvider,
    BuiltinTestingProvider,
    BuiltinVmSelectionProvider,
    LlmAcceptanceTestsProvider,
    LlmLogicProvider,
    LlmScaffoldProvider,
    LlmVmDeliveryProvider,
    MemberDesignProvider,
    MemberRequirementsProvider,
    MemberScaffoldProvider,
)
from app.core.orchestration.registry import ProviderRegistry
from app.core.orchestration.repair_routing import DIAGNOSTIC_REPAIR_OWNER
from app.core.orchestration.store import RunStore
from app.core.orchestration.worker_lock import (
    exclusive_implementation_worker,
    exclusive_run_execution,
)
from app.orchestration.run_identity import make_run_id

IMPLEMENTATION_STEP_ORDER = (
    "implementation.scaffold",
    "implementation.acceptance_tests",
    "implementation.logic",
    "implementation.vm_selection",
    "implementation.vm_delivery",
)


def _last_diagnostic_code(state: dict[str, Any], stage: StageName) -> str | None:
    stage_output = state.get(stage.value) or {}
    for step in reversed(stage_output.get("steps") or []):
        if step.get("status") != StepStatus.FAILED.value:
            continue
        diagnostics = step.get("diagnostics") or []
        if diagnostics:
            code = str(diagnostics[-1].get("code") or "") or None
            if code == "APPLICATION_TESTS_FAILED":
                testing_result = (step.get("output") or {}).get("testing_result") or {}
                repository_text = str(testing_result.get("repository") or "")
                refreshed = TestingAdapter._diagnostics(
                    testing_result.get("unitTests") or {},
                    (state.get("implementation") or {}).get("data") or {},
                    Path(repository_text) if repository_text else None,
                )
                if refreshed:
                    return refreshed[-1]["code"]
            return code
    return None


def _bounded_retry_feedback(
    state: dict[str, Any], stage: StageName, diagnostic_code: str | None
) -> list[dict[str, str]]:
    """Keep only a bounded diagnostic excerpt for the owning repair step."""
    stage_output = state.get(stage.value) or {}
    for step in reversed(stage_output.get("steps") or []):
        if step.get("status") != StepStatus.FAILED.value:
            continue
        diagnostics = step.get("diagnostics") or []
        diagnostic = next(
            (item for item in diagnostics if item.get("code") == diagnostic_code),
            diagnostics[-1] if diagnostics else {},
        )
        testing_result = (step.get("output") or {}).get("testing_result") or {}
        unit_tests = testing_result.get("unitTests") or {}
        evidence = "\n".join(
            str(unit_tests.get(field) or "") for field in ("stderr", "stdout", "reason")
        )[-2000:]
        if not diagnostic and not evidence:
            return []
        return [
            {
                "code": str(diagnostic_code or diagnostic.get("code") or "UNKNOWN"),
                "message": str(diagnostic.get("message") or "Previous subtask failed."),
                "evidence": evidence,
            }
        ]
    return []


def _repair_handoff_step(
    state: dict[str, Any], step: dict[str, Any], owner: str
) -> dict[str, Any] | None:
    """Promote a verified partial workspace only for a later owning repair step."""
    step_name = str(step.get("step") or "")
    if step.get("status") != StepStatus.FAILED.value:
        return None
    if step_name not in IMPLEMENTATION_STEP_ORDER:
        return None
    if IMPLEMENTATION_STEP_ORDER.index(step_name) >= IMPLEMENTATION_STEP_ORDER.index(owner):
        return None
    output = step.get("output") or {}
    run_root = str(output.get("run_root") or "")
    if not run_root:
        return None
    expected = (Path(".easydep/orchestration/workspaces") / str(state["run_id"])).resolve()
    workspace = Path(run_root).resolve()
    if workspace != expected or not (workspace / "application").is_dir():
        return None
    diagnostic_codes = [
        str(item.get("code") or "UNKNOWN") for item in step.get("diagnostics") or []
    ]
    metrics = dict(step.get("metrics") or {})
    metrics["repairHandoff"] = {
        "fromStatus": StepStatus.FAILED.value,
        "toOwner": owner,
        "diagnosticCodes": diagnostic_codes,
        "workspaceVerified": True,
    }
    return {
        **step,
        "status": StepStatus.COMPLETED.value,
        "diagnostics": [],
        "metrics": metrics,
    }


def _invalidate_implementation_from(
    state: dict[str, Any], owner: str
) -> tuple[list[str], list[dict[str, Any]]]:
    owner_index = IMPLEMENTATION_STEP_ORDER.index(owner)
    implementation = state.get("implementation") or {}
    retained_steps: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    latest_by_step = {
        str(step.get("step") or ""): step
        for step in implementation.get("steps") or []
        if str(step.get("step") or "") in IMPLEMENTATION_STEP_ORDER
    }
    for step_name in IMPLEMENTATION_STEP_ORDER[:owner_index]:
        step = latest_by_step.get(step_name)
        if step is None:
            continue
        if step.get("status") == StepStatus.COMPLETED.value:
            retained_steps.append(step)
            continue
        handoff = _repair_handoff_step(state, step, owner)
        if handoff is not None:
            retained_steps.append(handoff)
            handoffs.append({"step": step_name, **handoff["metrics"]["repairHandoff"]})
    data: dict[str, Any] = {}
    for step in retained_steps:
        data.update(step.get("output") or {})
    state["implementation"] = {
        "schema_version": implementation.get("schema_version", "easydep-implementation/v1"),
        "data": data,
        "steps": retained_steps,
    }
    state["current_stage"] = StageName.IMPLEMENTATION.value
    return list(IMPLEMENTATION_STEP_ORDER[owner_index:]), handoffs


def _apply_explicit_requirements_revision(state: dict[str, Any], response: Any) -> bool:
    """사용자가 전체 활성 요구사항을 명시한 경우에만 상위 계약을 다시 연다."""
    if state.get("current_stage") != StageName.IMPLEMENTATION.value or not isinstance(
        response, dict
    ):
        return False
    if response.get("resolution") not in {
        "revise-availability-requirement",
        "revise-state-requirement",
    }:
        return False
    revised = response.get("revisedRequirements")
    if (
        not isinstance(revised, list)
        or not revised
        or not all(isinstance(item, str) and item.strip() for item in revised)
    ):
        raise ValueError(
            "revisedRequirements must contain the complete, non-empty active requirements"
        )

    request = RunRequest.model_validate(state["request"])
    previous = list(request.requirements)
    active = [item.strip() for item in revised]
    if active == previous:
        raise ValueError("revisedRequirements must differ from the active requirements")

    implementation = (state.get("implementation") or {}).get("data") or {}
    run_root = implementation.get("run_root")
    if run_root:
        workspace_root = Path(".easydep/orchestration/workspaces").resolve()
        workspace = Path(str(run_root)).resolve()
        if workspace.parent != workspace_root:
            raise ValueError(f"Unexpected implementation workspace: {workspace}")
        if workspace.is_dir():
            shutil.rmtree(workspace)

    history = list(state.get("requirementRevisionHistory") or [])
    history.append(
        {
            "revision": len(history) + 1,
            "reason": "app-cloud-consistency-user-decision",
            "resolution": response["resolution"],
            "previousRequirements": previous,
            "activeRequirements": active,
            "recordedAt": datetime.now(UTC).isoformat(),
        }
    )
    state.update(
        request=request.model_copy(update={"requirements": active}).model_dump(mode="json"),
        current_stage=StageName.REQUIREMENTS.value,
        status="running",
        response=None,
        error=None,
        requirementRevisionHistory=history,
    )
    for key in ("requirements", "design", "implementation", "testing"):
        state.pop(key, None)
    return True


def build_default_registry() -> ProviderRegistry:
    return (
        ProviderRegistry()
        .register("requirements.analysis", ProviderKind.MEMBER, MemberRequirementsProvider())
        .register("design.architecture", ProviderKind.MEMBER, MemberDesignProvider())
        .register("design.cloud_enrichment", ProviderKind.BUILTIN, BuiltinCloudDesignProvider())
        .register("implementation.scaffold", ProviderKind.MEMBER, MemberScaffoldProvider())
        .register("implementation.scaffold", ProviderKind.LLM, LlmScaffoldProvider())
        .register(
            "implementation.acceptance_tests",
            ProviderKind.LLM,
            LlmAcceptanceTestsProvider(),
        )
        .register("implementation.logic", ProviderKind.LLM, LlmLogicProvider())
        .register(
            "implementation.vm_selection",
            ProviderKind.BUILTIN,
            BuiltinVmSelectionProvider(),
        )
        .register("implementation.vm_delivery", ProviderKind.LLM, LlmVmDeliveryProvider())
        .register("testing.application", ProviderKind.BUILTIN, BuiltinTestingProvider())
    )


def _context(state: OrchestrationState) -> StepContext:
    request = RunRequest.model_validate(state["request"])
    return StepContext(
        run_id=state["run_id"],
        app_id=state["app_id"],
        mode=request.mode,
        response=state.get("response"),
        requirement_revision=len(state.get("requirementRevisionHistory") or []),
        checkpoint_retry_attempt=len(state.get("retryHistory") or []),
    )


def _step_state(
    *,
    state: OrchestrationState,
    stage: StageName,
    result: StepResult,
    output: dict[str, Any],
) -> dict[str, Any]:
    if result.status == StepStatus.COMPLETED:
        return output
    return {
        **output,
        "current_stage": stage.value,
        "status": result.status.value,
        "response": None,
        "error": "; ".join(item.message for item in result.diagnostics),
    }


def _timed_run(provider, payload: dict[str, Any], context: StepContext) -> StepResult:
    started_at = datetime.now(UTC)
    started = perf_counter()
    if settings.easydep_experiment_session:
        print(
            json.dumps(
                {
                    "event": "stepStarted",
                    "runId": context.run_id,
                    "step": provider.step,
                    "startedAt": started_at.isoformat(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    try:
        result = provider.run(payload, context)
    except BaseException as error:
        if settings.easydep_experiment_session:
            print(
                json.dumps(
                    {
                        "event": "stepRaised",
                        "runId": context.run_id,
                        "step": provider.step,
                        "errorType": type(error).__name__,
                        "elapsedSeconds": round(perf_counter() - started, 6),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        raise
    finished_at = datetime.now(UTC)
    if settings.easydep_experiment_session:
        print(
            json.dumps(
                {
                    "event": "stepFinished",
                    "runId": context.run_id,
                    "step": provider.step,
                    "status": result.status.value,
                    "finishedAt": finished_at.isoformat(),
                    "elapsedSeconds": round(perf_counter() - started, 6),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    result.metrics = {
        **result.metrics,
        "timing": {
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "elapsedSeconds": round(perf_counter() - started, 6),
        },
    }
    return result


def _progress(event: str, **fields: Any) -> None:
    if settings.easydep_experiment_session:
        print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def build_orchestration_graph(
    registry: ProviderRegistry,
    persist_checkpoint: Callable[[str, dict[str, Any]], None] | None = None,
):
    builder = StateGraph(OrchestrationState)

    def checkpoint(
        state: OrchestrationState,
        update: dict[str, Any],
        *,
        stage: StageName,
        step: str,
    ) -> None:
        if persist_checkpoint is None:
            return
        snapshot = {**dict(state), **update}
        snapshot.pop("response", None)
        started = perf_counter()
        persist_checkpoint(snapshot["run_id"], snapshot)
        _progress(
            "stageCheckpointSaved",
            runId=snapshot["run_id"],
            stage=stage.value,
            step=step,
            elapsedSeconds=round(perf_counter() - started, 6),
        )

    def requirements_stage(state: OrchestrationState) -> dict[str, Any]:
        request = RunRequest.model_validate(state["request"])
        previous = RequirementsOutput.model_validate(
            state.get("requirements") or {"data": {}, "steps": []}
        )
        provider = registry.resolve(
            "requirements.analysis", request.providers.requirements_analysis
        )
        result = _timed_run(
            provider,
            {
                "requirements": request.requirements,
                "resource_constraints_text": request.resource_constraints_text,
                "member_result": previous.data.get("member_result") or {},
            },
            _context(state),
        )
        completed = RequirementsOutput(
            data=result.output,
            steps=[*previous.steps, result],
        )
        update = _step_state(
            state=state,
            stage=StageName.REQUIREMENTS,
            result=result,
            output={"requirements": completed.model_dump(mode="json")},
        )
        if result.status == StepStatus.COMPLETED:
            update.update(current_stage=StageName.DESIGN.value, status="running", response=None)
        return update

    def design_stage(state: OrchestrationState) -> dict[str, Any]:
        request = RunRequest.model_validate(state["request"])
        requirements = RequirementsOutput.model_validate(state["requirements"])
        requirements_result = requirements.data["member_result"]
        previous = DesignOutput.model_validate(state.get("design") or {"data": {}, "steps": []})
        architecture_provider = registry.resolve(
            "design.architecture", request.providers.design_architecture
        )
        latest_by_step = {result.step: result for result in previous.steps}
        architecture = latest_by_step.get("design.architecture")
        steps = list(previous.steps)
        if architecture is None or architecture.status != StepStatus.COMPLETED:
            architecture = _timed_run(
                architecture_provider,
                {
                    "requirements_result": requirements_result,
                    "member_result": previous.data.get("design_result") or {},
                },
                _context(state),
            )
            steps.append(architecture)
        data = dict(previous.data)
        if architecture.output.get("member_result"):
            data["design_result"] = architecture.output["member_result"]
        output = DesignOutput(data=data, steps=steps)
        if architecture.status != StepStatus.COMPLETED:
            return _step_state(
                state=state,
                stage=StageName.DESIGN,
                result=architecture,
                output={"design": output.model_dump(mode="json")},
            )
        checkpoint(
            state,
            {
                "design": output.model_dump(mode="json"),
                "current_stage": StageName.DESIGN.value,
                "status": "running",
            },
            stage=StageName.DESIGN,
            step=architecture.step,
        )
        cloud_provider = registry.resolve(
            "design.cloud_enrichment", request.providers.design_cloud_enrichment
        )
        cloud = _timed_run(
            cloud_provider,
            {
                "requirements_result": requirements_result,
                "design_result": data["design_result"],
                "use_cloud_kb": request.variant not in {"no-cloud-kb", "no-depkb"},
                "enable_repair_feedback": request.variant != "no-verification",
            },
            StepContext(**_context(state).model_dump(exclude={"response"})),
        )
        steps.append(cloud)
        data.update(cloud.output)
        output = DesignOutput(data=data, steps=steps)
        update = _step_state(
            state=state,
            stage=StageName.DESIGN,
            result=cloud,
            output={"design": output.model_dump(mode="json")},
        )
        if cloud.status == StepStatus.COMPLETED:
            update.update(
                current_stage=StageName.IMPLEMENTATION.value,
                status="running",
                response=None,
            )
        return update

    def implementation_stage(state: OrchestrationState) -> dict[str, Any]:
        request = RunRequest.model_validate(state["request"])
        requirements = RequirementsOutput.model_validate(state["requirements"])
        design = DesignOutput.model_validate(state["design"])
        payload = {
            "requirements_result": requirements.data["member_result"],
            "design_result": design.data["design_result"],
            "cloud_design_result": design.data["cloud_design_result"],
            "resource_constraints_text": request.resource_constraints_text,
            "enable_repair_feedback": request.variant != "no-verification",
            "enable_consistency_validator": (request.variant != "no-consistency-validator"),
        }
        choices = (
            ("implementation.scaffold", request.providers.implementation_scaffold),
            (
                "implementation.acceptance_tests",
                request.providers.implementation_acceptance_tests,
            ),
            ("implementation.logic", request.providers.implementation_logic),
            (
                "implementation.vm_selection",
                request.providers.implementation_vm_selection,
            ),
            ("implementation.vm_delivery", request.providers.implementation_vm_delivery),
        )
        previous = ImplementationOutput.model_validate(
            state.get("implementation") or {"data": {}, "steps": []}
        )
        payload.update(previous.data)
        results: list[StepResult] = list(previous.steps)
        latest_by_step = {result.step: result for result in previous.steps}
        latest_retry = (state.get("retryHistory") or [{}])[-1]
        with exclusive_implementation_worker():
            for step, kind in choices:
                if latest_by_step.get(step) is not None and (
                    latest_by_step[step].status == StepStatus.COMPLETED
                ):
                    continue
                step_payload = dict(payload)
                previous_failure = latest_by_step.get(step)
                if (
                    previous_failure is not None
                    and previous_failure.status == StepStatus.FAILED
                    and payload["enable_repair_feedback"]
                ):
                    step_payload["repair_feedback"] = [
                        diagnostic.model_dump(mode="json")
                        for diagnostic in previous_failure.diagnostics
                    ]
                elif (
                    payload["enable_repair_feedback"]
                    and latest_retry.get("repairOwner") == step
                    and latest_retry.get("repairFeedback")
                ):
                    step_payload["repair_feedback"] = latest_retry["repairFeedback"]
                result = _timed_run(registry.resolve(step, kind), step_payload, _context(state))
                results.append(result)
                payload.update(result.output)
                if result.status != StepStatus.COMPLETED:
                    output = ImplementationOutput(data=payload, steps=results)
                    return _step_state(
                        state=state,
                        stage=StageName.IMPLEMENTATION,
                        result=result,
                        output={"implementation": output.model_dump(mode="json")},
                    )
                checkpoint(
                    state,
                    {
                        "implementation": ImplementationOutput(
                            data=payload, steps=results
                        ).model_dump(mode="json"),
                        "current_stage": StageName.IMPLEMENTATION.value,
                        "status": "running",
                    },
                    stage=StageName.IMPLEMENTATION,
                    step=result.step,
                )
        output = ImplementationOutput(data=payload, steps=results)
        return {
            "implementation": output.model_dump(mode="json"),
            "current_stage": StageName.TESTING.value,
            "status": "running",
            "response": None,
        }

    def testing_stage(state: OrchestrationState) -> dict[str, Any]:
        request = RunRequest.model_validate(state["request"])
        implementation = ImplementationOutput.model_validate(state["implementation"])
        testing_provider = registry.resolve(
            "testing.application", request.providers.testing_application
        )
        result = _timed_run(
            testing_provider,
            {**implementation.data, "case_id": request.case_id},
            _context(state),
        )
        previous = TestingOutput.model_validate(state.get("testing") or {"data": {}, "steps": []})
        output = TestingOutput(data=result.output, steps=[*previous.steps, result])
        if result.status == StepStatus.COMPLETED:
            return {
                "testing": output.model_dump(mode="json"),
                "current_stage": StageName.COMPLETED.value,
                "status": StepStatus.COMPLETED.value,
                "response": None,
            }
        return _step_state(
            state=state,
            stage=StageName.TESTING,
            result=result,
            output={"testing": output.model_dump(mode="json")},
        )

    def entry(state: OrchestrationState) -> str:
        stage = state.get("current_stage", StageName.REQUIREMENTS.value)
        return (
            stage
            if stage in {item.value for item in StageName if item != StageName.COMPLETED}
            else END
        )

    def advance(state: OrchestrationState) -> str:
        return state["current_stage"] if state.get("status") == "running" else END

    def with_checkpoint(stage: StageName, node):
        def wrapped(state: OrchestrationState) -> dict[str, Any]:
            update = node(state)
            checkpoint(state, update, stage=stage, step=f"{stage.value}.complete")
            return update

        return wrapped

    builder.add_node(
        StageName.REQUIREMENTS.value,
        with_checkpoint(StageName.REQUIREMENTS, requirements_stage),
    )
    builder.add_node(
        StageName.DESIGN.value,
        with_checkpoint(StageName.DESIGN, design_stage),
    )
    builder.add_node(
        StageName.IMPLEMENTATION.value,
        with_checkpoint(StageName.IMPLEMENTATION, implementation_stage),
    )
    builder.add_node(
        StageName.TESTING.value,
        with_checkpoint(StageName.TESTING, testing_stage),
    )
    builder.add_conditional_edges(START, entry)
    builder.add_conditional_edges(StageName.REQUIREMENTS.value, advance)
    builder.add_conditional_edges(StageName.DESIGN.value, advance)
    builder.add_conditional_edges(StageName.IMPLEMENTATION.value, advance)
    builder.add_edge(StageName.TESTING.value, END)
    return builder.compile()


class Orchestrator:
    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        store: RunStore | None = None,
    ) -> None:
        self.registry = registry or build_default_registry()
        self.store = store or RunStore()
        self.graph = build_orchestration_graph(self.registry, persist_checkpoint=self.store.save)

    def _finish(self, state: dict[str, Any]) -> RunResult:
        state.pop("response", None)
        persist_started = perf_counter()
        _progress("runPersistenceStarted", runId=state["run_id"])
        self.store.save(state["run_id"], state)
        persist_run_artifacts(state["run_id"], state)
        _progress(
            "runPersistenceFinished",
            runId=state["run_id"],
            elapsedSeconds=round(perf_counter() - persist_started, 6),
        )
        raw_status = state.get("status", StepStatus.FAILED.value)
        status = StepStatus(raw_status) if raw_status != "running" else StepStatus.FAILED
        prompt = None
        stage_value = state.get("current_stage", StageName.REQUIREMENTS.value)
        stage = StageName(stage_value)
        stage_output = state.get(stage_value) or {}
        steps = stage_output.get("steps") or []
        if steps and status == StepStatus.NEEDS_INPUT:
            prompt = steps[-1].get("prompt")
        return RunResult(
            run_id=state["run_id"],
            app_id=state["app_id"],
            stage=stage,
            status=status,
            prompt=prompt,
            state=state,
        )

    def start(self, request: RunRequest) -> RunResult:
        run_id = request.run_id or make_run_id("easydep", request.variant, request.case_id)
        with exclusive_run_execution(run_id):
            return self._start_unlocked(request, run_id)

    def _start_unlocked(self, request: RunRequest, run_id: str) -> RunResult:
        try:
            self.store.load(run_id)
        except KeyError:
            pass
        else:
            raise ValueError(f"Run already exists: {run_id}")
        state: OrchestrationState = {
            "request": request.model_copy(update={"run_id": run_id}).model_dump(mode="json"),
            "run_id": run_id,
            "app_id": request.app_id or uuid.uuid4().hex,
            "current_stage": StageName.REQUIREMENTS.value,
            "status": "running",
        }
        self.store.save(run_id, dict(state))
        return self._finish(dict(self.graph.invoke(state)))

    def resume(self, run_id: str, response: Any) -> RunResult:
        with exclusive_run_execution(run_id):
            return self._resume_unlocked(run_id, response)

    def _resume_unlocked(self, run_id: str, response: Any) -> RunResult:
        state = self.store.load(run_id)
        if state.get("status") != StepStatus.NEEDS_INPUT.value:
            raise ValueError(f"Run is not waiting for input: {run_id}")
        if not _apply_explicit_requirements_revision(state, response):
            state.update(status="running", response=response)
        self.store.save(run_id, dict(state))
        return self._finish(dict(self.graph.invoke(state)))

    def retry_failed(
        self, run_id: str, *, reason: str, repair_owner: str | None = None
    ) -> RunResult:
        with exclusive_run_execution(run_id):
            return self._retry_failed_unlocked(run_id, reason=reason, repair_owner=repair_owner)

    def _retry_failed_unlocked(
        self, run_id: str, *, reason: str, repair_owner: str | None = None
    ) -> RunResult:
        load_started = perf_counter()
        _progress("checkpointLoadStarted", runId=run_id)
        state = self.store.load(run_id)
        _progress(
            "checkpointLoadFinished",
            runId=run_id,
            elapsedSeconds=round(perf_counter() - load_started, 6),
        )
        prior_status = state.get("status")
        if prior_status not in {
            StepStatus.FAILED.value,
            StepStatus.COMPLETED.value,
            "running",
        }:
            raise ValueError(f"Run is neither failed nor interrupted: {run_id}")
        if prior_status == StepStatus.COMPLETED.value and repair_owner is None:
            raise ValueError("A completed run may be reopened only with an explicit repair owner")
        stage = (
            StageName.TESTING
            if prior_status == StepStatus.COMPLETED.value
            else StageName(state.get("current_stage", StageName.REQUIREMENTS.value))
        )
        if repair_owner is not None:
            if stage not in {StageName.IMPLEMENTATION, StageName.TESTING}:
                raise ValueError(
                    "Explicit repair owner is allowed only after implementation or testing failure"
                )
            if repair_owner not in IMPLEMENTATION_STEP_ORDER:
                raise ValueError(f"Unknown implementation repair owner: {repair_owner}")
        if stage in {StageName.IMPLEMENTATION, StageName.TESTING}:
            implementation_output = state.get("implementation") or {}
            implementation = implementation_output.get("data") or {}
            run_root = implementation.get("run_root")
            if not run_root:
                completed_steps = [
                    step
                    for step in implementation_output.get("steps") or []
                    if step.get("status") == StepStatus.COMPLETED.value
                ]
                if stage == StageName.TESTING or completed_steps:
                    raise ValueError("Failed run has no implementation workspace checkpoint")
                stale_workspace = (Path(".easydep/orchestration/workspaces") / run_id).resolve()
                expected_parent = Path(".easydep/orchestration/workspaces").resolve()
                if stale_workspace.parent != expected_parent:
                    raise ValueError(f"Unexpected implementation workspace: {stale_workspace}")
                if stale_workspace.is_dir():
                    shutil.rmtree(stale_workspace)
            else:
                workspace = Path(str(run_root))
            if run_root and not workspace.is_dir():
                restore_started = perf_counter()
                _progress("checkpointRestoreStarted", runId=run_id)
                restore_run_application(
                    run_id,
                    workspace,
                    checkpoint_attempt=len(state.get("retryHistory") or []),
                    requirement_revision=len(state.get("requirementRevisionHistory") or []),
                    allow_prior_checkpoint=repair_owner is not None,
                    expected_app_id=str(state.get("app_id") or "") or None,
                )
                _progress(
                    "checkpointRestoreFinished",
                    runId=run_id,
                    elapsedSeconds=round(perf_counter() - restore_started, 6),
                )
        diagnostic_code = _last_diagnostic_code(state, stage)
        inferred_repair_owner = DIAGNOSTIC_REPAIR_OWNER.get(diagnostic_code or "")
        selected_repair_owner = repair_owner or inferred_repair_owner
        repair_feedback = (
            _bounded_retry_feedback(state, stage, diagnostic_code) if selected_repair_owner else []
        )
        if repair_owner is not None:
            repair_feedback = [
                {
                    "code": "OPERATOR-DIRECTED-REPAIR",
                    "message": "The operator assigned the failed evidence to this subtask.",
                    "evidence": reason[-2000:],
                }
            ]
        invalidated_steps: list[str] = []
        partial_output_handoffs: list[dict[str, Any]] = []
        if selected_repair_owner:
            invalidated_steps, partial_output_handoffs = _invalidate_implementation_from(
                state, selected_repair_owner
            )
        history = list(state.get("retryHistory") or [])
        retry_record = {
            "attempt": len(history) + 1,
            "stage": stage.value,
            "reason": reason,
            "startedAt": datetime.now(UTC).isoformat(),
            "diagnosticCode": diagnostic_code,
            "repairOwner": selected_repair_owner,
            "invalidatedSteps": invalidated_steps,
        }
        if partial_output_handoffs:
            retry_record["partialOutputHandoffs"] = partial_output_handoffs
        if repair_feedback:
            retry_record["repairFeedback"] = repair_feedback
        history.append(retry_record)
        state.update(status="running", error=None, response=None, retryHistory=history)
        self.store.save(run_id, state)
        _progress("checkpointExecutionStarted", runId=run_id, stage=stage.value)
        return self._finish(dict(self.graph.invoke(state)))

    def prepare_failed_retry(self, run_id: str) -> Path:
        """Restore and return the latest failed application checkpoint without executing it."""
        with exclusive_run_execution(run_id):
            return self._prepare_failed_retry_unlocked(run_id)

    def _prepare_failed_retry_unlocked(self, run_id: str) -> Path:
        state = self.store.load(run_id)
        if state.get("status") != StepStatus.FAILED.value:
            raise ValueError(f"Run is not failed: {run_id}")
        implementation = (state.get("implementation") or {}).get("data") or {}
        run_root = implementation.get("run_root")
        if not run_root:
            raise ValueError("Failed run has no implementation workspace checkpoint")
        workspace = Path(str(run_root))
        application = workspace / "application"
        if application.is_dir():
            return application
        return restore_run_application(
            run_id,
            workspace,
            checkpoint_attempt=len(state.get("retryHistory") or []),
            requirement_revision=len(state.get("requirementRevisionHistory") or []),
            expected_app_id=str(state.get("app_id") or "") or None,
        )

    def get(self, run_id: str) -> RunResult:
        return self._finish(self.store.load(run_id))


_default: Orchestrator | None = None


def _orchestrator() -> Orchestrator:
    global _default
    if _default is None:
        _default = Orchestrator()
    return _default


def start_run(request: RunRequest | dict[str, Any]) -> RunResult:
    return _orchestrator().start(RunRequest.model_validate(request))


def resume_run(run_id: str, response: Any) -> RunResult:
    return _orchestrator().resume(run_id, response)


def retry_failed_run(run_id: str, *, reason: str, repair_owner: str | None = None) -> RunResult:
    return _orchestrator().retry_failed(run_id, reason=reason, repair_owner=repair_owner)


def get_run(run_id: str) -> RunResult:
    return _orchestrator().get(run_id)


def run_batch(request: RunRequest | dict[str, Any]) -> RunResult:
    parsed = RunRequest.model_validate(request)
    return _orchestrator().start(parsed.model_copy(update={"mode": RunMode.BATCH}))
