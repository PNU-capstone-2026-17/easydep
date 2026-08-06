"""Four-stage LangGraph with explicit, replaceable substep providers."""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.orchestration.artifacts import persist_run_artifacts
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
from app.core.orchestration.store import RunStore
from app.core.orchestration.worker_lock import exclusive_implementation_worker
from app.core.run_identity import make_run_id


def build_default_registry() -> ProviderRegistry:
    return (
        ProviderRegistry()
        .register("requirements.analysis", ProviderKind.MEMBER, MemberRequirementsProvider())
        .register("design.architecture", ProviderKind.MEMBER, MemberDesignProvider())
        .register(
            "design.cloud_enrichment", ProviderKind.BUILTIN, BuiltinCloudDesignProvider()
        )
        .register(
            "implementation.scaffold", ProviderKind.MEMBER, MemberScaffoldProvider()
        )
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


def build_orchestration_graph(registry: ProviderRegistry):
    builder = StateGraph(OrchestrationState)

    def requirements_stage(state: OrchestrationState) -> dict[str, Any]:
        request = RunRequest.model_validate(state["request"])
        previous = RequirementsOutput.model_validate(
            state.get("requirements") or {"data": {}, "steps": []}
        )
        provider = registry.resolve(
            "requirements.analysis", request.providers.requirements_analysis
        )
        result = provider.run(
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
        architecture = registry.resolve(
            "design.architecture", request.providers.design_architecture
        ).run(
            {
                "requirements_result": requirements_result,
                "member_result": previous.data.get("design_result") or {},
            },
            _context(state),
        )
        steps = [*previous.steps, architecture]
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
        cloud = registry.resolve(
            "design.cloud_enrichment", request.providers.design_cloud_enrichment
        ).run(
            {
                "requirements_result": requirements_result,
                "design_result": data["design_result"],
                "use_cloud_kb": request.variant != "no-cloud-kb",
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
        results: list[StepResult] = []
        with exclusive_implementation_worker():
            for step, kind in choices:
                result = registry.resolve(step, kind).run(payload, _context(state))
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
        result = registry.resolve(
            "testing.application", request.providers.testing_application
        ).run(
            {**implementation.data, "case_id": request.case_id},
            _context(state),
        )
        output = TestingOutput(data=result.output, steps=[result])
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
        return stage if stage in {item.value for item in StageName if item != StageName.COMPLETED} else END

    def advance(state: OrchestrationState) -> str:
        return state["current_stage"] if state.get("status") == "running" else END

    builder.add_node(StageName.REQUIREMENTS.value, requirements_stage)
    builder.add_node(StageName.DESIGN.value, design_stage)
    builder.add_node(StageName.IMPLEMENTATION.value, implementation_stage)
    builder.add_node(StageName.TESTING.value, testing_stage)
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
        self.graph = build_orchestration_graph(self.registry)

    def _finish(self, state: dict[str, Any]) -> RunResult:
        state.pop("response", None)
        self.store.save(state["run_id"], state)
        persist_run_artifacts(state["run_id"], state)
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
        state: OrchestrationState = {
            "request": request.model_copy(update={"run_id": run_id}).model_dump(mode="json"),
            "run_id": run_id,
            "app_id": request.app_id or uuid.uuid4().hex,
            "current_stage": StageName.REQUIREMENTS.value,
            "status": "running",
        }
        return self._finish(dict(self.graph.invoke(state)))

    def resume(self, run_id: str, response: Any) -> RunResult:
        state = self.store.load(run_id)
        if state.get("status") != StepStatus.NEEDS_INPUT.value:
            raise ValueError(f"Run is not waiting for input: {run_id}")
        state.update(status="running", response=response)
        return self._finish(dict(self.graph.invoke(state)))

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


def get_run(run_id: str) -> RunResult:
    return _orchestrator().get(run_id)


def run_batch(request: RunRequest | dict[str, Any]) -> RunResult:
    parsed = RunRequest.model_validate(request)
    return _orchestrator().start(parsed.model_copy(update={"mode": RunMode.BATCH}))
