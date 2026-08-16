from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import app.core.orchestration.graph as graph_module
from app.core.orchestration.adapters.testing import TestingAdapter as _TestingAdapter
from app.core.orchestration.artifacts import persist_run_artifacts, restore_run_application
from app.core.orchestration.contracts import (
    ProviderKind,
    RunMode,
    RunRequest,
    StageName,
    StepContext,
    StepResult,
    StepStatus,
)
from app.core.orchestration.graph import Orchestrator, build_orchestration_graph
from app.core.orchestration.providers import (
    BuiltinCloudDesignProvider,
    LlmAcceptanceTestsProvider,
    LlmLogicProvider,
    LlmScaffoldProvider,
    MemberRequirementsProvider,
    _completion_options,
)
from app.core.orchestration.registry import ProviderRegistry
from app.core.orchestration.store import RunStore
from app.core.orchestration.worker_lock import (
    exclusive_implementation_worker,
    exclusive_run_execution,
)


class FakeProvider:
    def __init__(self, step: str, output: dict[str, Any], status=StepStatus.COMPLETED):
        self.step = step
        self.output = output
        self.status = status
        self.calls: list[tuple[dict[str, Any], StepContext]] = []

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        self.calls.append((payload, context))
        return StepResult(
            step=self.step,
            provider=ProviderKind.BUILTIN,
            status=self.status,
            output=self.output,
            prompt={"question": "answer"} if self.status == StepStatus.NEEDS_INPUT else None,
        )


class InteractiveRequirements(FakeProvider):
    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        self.calls.append((payload, context))
        if context.response is None:
            return StepResult(
                step=self.step,
                provider=ProviderKind.BUILTIN,
                status=StepStatus.NEEDS_INPUT,
                output={"member_result": {"status": "need_feedback"}},
                prompt={"question": "budget"},
            )
        return StepResult(
            step=self.step,
            provider=ProviderKind.BUILTIN,
            status=StepStatus.COMPLETED,
            output=self.output,
        )


class RequirementsRevisionScaffold(FakeProvider):
    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        self.calls.append((payload, context))
        requirements = payload["requirements_result"].get("active") or []
        if any("single zone" in item.lower() for item in requirements):
            return StepResult(
                step=self.step,
                provider=ProviderKind.BUILTIN,
                status=StepStatus.COMPLETED,
                output=self.output,
            )
        return StepResult(
            step=self.step,
            provider=ProviderKind.BUILTIN,
            status=StepStatus.NEEDS_INPUT,
            output={"pending_consistency_diagnostics": [{"code": "BIND-STATE-HA-001"}]},
            prompt={"kind": "app-cloud-consistency"},
        )


class FailOnceProvider(FakeProvider):
    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        self.calls.append((payload, context))
        status = StepStatus.FAILED if len(self.calls) == 1 else StepStatus.COMPLETED
        return StepResult(
            step=self.step,
            provider=ProviderKind.BUILTIN,
            status=status,
            output=self.output if status == StepStatus.COMPLETED else {},
            diagnostics=(
                []
                if status == StepStatus.COMPLETED
                else [{"code": "FIRST_ATTEMPT", "message": "first attempt failed"}]
            ),
        )


class DiagnosticFailOnceProvider(FakeProvider):
    def __init__(self, step: str, output: dict[str, Any], code: str):
        super().__init__(step, output)
        self.code = code

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        self.calls.append((payload, context))
        status = StepStatus.FAILED if len(self.calls) == 1 else StepStatus.COMPLETED
        return StepResult(
            step=self.step,
            provider=ProviderKind.BUILTIN,
            status=status,
            output=self.output if status == StepStatus.COMPLETED else {},
            diagnostics=(
                [{"code": self.code, "message": "routed failure"}]
                if status == StepStatus.FAILED
                else []
            ),
        )


class PartialDiagnosticFailureProvider(FakeProvider):
    def __init__(self, step: str, output: dict[str, Any], code: str):
        super().__init__(step, output)
        self.code = code

    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        self.calls.append((payload, context))
        return StepResult(
            step=self.step,
            provider=ProviderKind.BUILTIN,
            status=StepStatus.FAILED,
            output=self.output,
            diagnostics=[{"code": self.code, "message": "repairable partial output"}],
        )


class InterruptOnceProvider(FakeProvider):
    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult:
        self.calls.append((payload, context))
        if len(self.calls) == 1:
            raise KeyboardInterrupt("simulated worker termination")
        return StepResult(
            step=self.step,
            provider=ProviderKind.BUILTIN,
            status=StepStatus.COMPLETED,
            output=self.output,
        )


def _registry(tmp_path: Path, requirements: FakeProvider | None = None) -> ProviderRegistry:
    application = tmp_path / "member-run" / "application"
    application.mkdir(parents=True)
    requirements = requirements or FakeProvider(
        "requirements.analysis",
        {"member_result": {"status": "completed", "resource_spec": {}}},
    )
    return (
        ProviderRegistry()
        .register("requirements.analysis", ProviderKind.MEMBER, requirements)
        .register(
            "design.architecture",
            ProviderKind.MEMBER,
            FakeProvider(
                "design.architecture",
                {
                    "member_result": {
                        "status": "completed",
                        "artifacts": {"class_diagram": "class A", "api_spec": {}},
                    }
                },
            ),
        )
        .register(
            "design.cloud_enrichment",
            ProviderKind.BUILTIN,
            FakeProvider("design.cloud_enrichment", {"cloud_design_result": {}}),
        )
        .register(
            "implementation.scaffold",
            ProviderKind.MEMBER,
            FakeProvider("implementation.scaffold", {"run_root": str(application.parent)}),
        )
        .register(
            "implementation.acceptance_tests",
            ProviderKind.LLM,
            FakeProvider("implementation.acceptance_tests", {"acceptance_tests": ["test"]}),
        )
        .register(
            "implementation.logic",
            ProviderKind.LLM,
            FakeProvider("implementation.logic", {"files": []}),
        )
        .register(
            "implementation.vm_selection",
            ProviderKind.BUILTIN,
            FakeProvider("implementation.vm_selection", {"vm_selection": {"status": "deferred"}}),
        )
        .register(
            "implementation.vm_delivery",
            ProviderKind.LLM,
            FakeProvider("implementation.vm_delivery", {"vm_delivery": {"status": "completed"}}),
        )
        .register(
            "testing.application",
            ProviderKind.BUILTIN,
            FakeProvider("testing.application", {"testing_result": {"passed": True}}),
        )
    )


def _orchestrator(tmp_path: Path, monkeypatch, registry: ProviderRegistry) -> Orchestrator:
    monkeypatch.setattr(graph_module, "persist_run_artifacts", lambda *_args, **_kwargs: None)
    return Orchestrator(registry=registry, store=RunStore(tmp_path / "runs.sqlite3"))


def test_graph_has_only_the_four_product_stages(tmp_path):
    graph = build_orchestration_graph(_registry(tmp_path))
    assert set(graph.get_graph().nodes) == {
        "__start__",
        "requirements",
        "design",
        "implementation",
        "testing",
        "__end__",
    }


def test_batch_run_completes_all_four_stages(tmp_path, monkeypatch):
    result = _orchestrator(tmp_path, monkeypatch, _registry(tmp_path)).start(
        RunRequest(requirements=["The service converts units."], mode=RunMode.BATCH)
    )

    assert result.status == StepStatus.COMPLETED
    assert result.stage.value == "completed"
    assert set(result.state) >= {"requirements", "design", "implementation", "testing"}
    steps = [
        step
        for stage in ("requirements", "design", "implementation", "testing")
        for step in result.state[stage]["steps"]
    ]
    assert all(step["metrics"]["timing"]["elapsedSeconds"] >= 0 for step in steps)
    assert all(step["metrics"]["timing"]["startedAt"].endswith("+00:00") for step in steps)


@pytest.mark.parametrize(
    (
        "variant",
        "use_cloud_kb",
        "enable_repair_feedback",
        "enable_consistency_validator",
    ),
    [
        ("full", True, True, True),
        ("no-depkb", False, True, True),
        ("no-verification", True, False, True),
        ("no-consistency-validator", True, True, False),
    ],
)
def test_ablation_variant_changes_only_declared_treatment_payload(
    tmp_path,
    monkeypatch,
    variant,
    use_cloud_kb,
    enable_repair_feedback,
    enable_consistency_validator,
):
    registry = _registry(tmp_path)
    result = _orchestrator(tmp_path, monkeypatch, registry).start(
        RunRequest(requirements=["Deploy the service."], variant=variant, mode=RunMode.BATCH)
    )

    assert result.status == StepStatus.COMPLETED
    cloud_payload = registry.resolve("design.cloud_enrichment", ProviderKind.BUILTIN).calls[0][0]
    delivery_payload = registry.resolve("implementation.vm_delivery", ProviderKind.LLM).calls[0][0]
    assert cloud_payload["use_cloud_kb"] is use_cloud_kb
    assert cloud_payload["enable_repair_feedback"] is enable_repair_feedback
    assert delivery_payload["enable_repair_feedback"] is enable_repair_feedback
    assert delivery_payload["enable_consistency_validator"] is enable_consistency_validator


def test_interactive_run_resumes_without_implicit_provider_switch(tmp_path, monkeypatch):
    provider = InteractiveRequirements(
        "requirements.analysis",
        {"member_result": {"status": "completed", "resource_spec": {}}},
    )
    orchestrator = _orchestrator(tmp_path, monkeypatch, _registry(tmp_path, provider))
    first = orchestrator.start(RunRequest(requirements=["An application."], app_id="app"))
    second = orchestrator.resume(first.run_id, "USD 50 per month")

    assert first.status == StepStatus.NEEDS_INPUT
    assert first.prompt == {"question": "budget"}
    assert second.status == StepStatus.COMPLETED
    assert provider.calls[1][1].response == "USD 50 per month"


@pytest.mark.parametrize(
    "resolution", ["revise-state-requirement", "revise-availability-requirement"]
)
def test_explicit_requirement_revision_restarts_upstream_in_same_run(
    tmp_path, monkeypatch, resolution
):
    requirements = FakeProvider(
        "requirements.analysis",
        {"member_result": {"status": "completed", "active": []}},
    )
    registry = _registry(tmp_path, requirements)
    scaffold = RequirementsRevisionScaffold(
        "implementation.scaffold", {"run_root": str(tmp_path / "member-run")}
    )
    registry.register("implementation.scaffold", ProviderKind.MEMBER, scaffold)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    original = ["Keep local state and survive an availability-zone failure."]

    first = orchestrator.start(RunRequest(requirements=original, app_id="app"))
    # The fake requirements provider mirrors the current request for this routing test.
    requirements.output["member_result"]["active"] = [
        "The service may run in a single zone and keep local state."
    ]
    revised = ["The service may run in a single zone and keep local state."]
    second = orchestrator.resume(
        first.run_id,
        {
            "resolution": resolution,
            "revisedRequirements": revised,
        },
    )

    assert first.status == StepStatus.NEEDS_INPUT
    assert second.status == StepStatus.COMPLETED
    assert second.run_id == first.run_id
    assert second.state["request"]["requirements"] == revised
    assert second.state["requirementRevisionHistory"][0]["resolution"] == resolution
    assert second.state["requirementRevisionHistory"][0]["previousRequirements"] == original
    assert len(requirements.calls) == 2
    assert len(scaffold.calls) == 2


@pytest.mark.parametrize("revised", [None, [], [""], ["same"]])
def test_requirement_revision_rejects_missing_or_unchanged_full_contract(
    tmp_path, monkeypatch, revised
):
    requirements = FakeProvider(
        "requirements.analysis",
        {"member_result": {"status": "completed", "active": []}},
    )
    registry = _registry(tmp_path, requirements)
    registry.register(
        "implementation.scaffold",
        ProviderKind.MEMBER,
        RequirementsRevisionScaffold("implementation.scaffold", {}),
    )
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    first = orchestrator.start(RunRequest(requirements=["same"], app_id="app"))
    assert first.stage.value == "implementation"
    with pytest.raises(ValueError):
        orchestrator.resume(
            first.run_id,
            {
                "resolution": "revise-state-requirement",
                "revisedRequirements": revised,
            },
        )


def test_failed_selected_provider_stops_the_run(tmp_path, monkeypatch):
    failed = FakeProvider("requirements.analysis", {}, StepStatus.FAILED)
    result = _orchestrator(tmp_path, monkeypatch, _registry(tmp_path, failed)).start(
        RunRequest(requirements=["An application."])
    )

    assert result.status == StepStatus.FAILED
    assert result.stage.value == "requirements"
    assert "design" not in result.state


def test_retry_failed_design_reuses_completed_requirements(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    design = FailOnceProvider(
        "design.architecture",
        {
            "member_result": {
                "status": "completed",
                "artifacts": {"class_diagram": "class A", "api_spec": {}},
            }
        },
    )
    registry.register("design.architecture", ProviderKind.MEMBER, design)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)

    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))
    second = orchestrator.retry_failed(first.run_id, reason="retry timed out design")

    assert first.status == StepStatus.FAILED
    assert first.stage.value == "design"
    assert second.status == StepStatus.COMPLETED
    assert len(registry.resolve("requirements.analysis", ProviderKind.MEMBER).calls) == 1
    assert len(design.calls) == 2
    retry = second.state["retryHistory"][0]
    assert retry["stage"] == "design"
    assert retry["invalidatedSteps"] == []


def test_member_design_retries_pending_internal_node_without_restart():
    class PendingAdapter:
        def __init__(self):
            self.retried = []

        def has_pending(self, *, session_id):
            return True

        def retry_pending(self, *, session_id):
            self.retried.append(session_id)
            return {"status": "completed", "llm_timing_events": []}

        def start(self, **_kwargs):
            raise AssertionError("pending design must not restart")

        def timing_events(self, _session_id):
            return []

    adapter = PendingAdapter()
    provider = graph_module.MemberDesignProvider(adapter=adapter)

    result = provider.run(
        {"requirements_result": {"use_case_specs": [{}]}},
        StepContext(run_id="retry-design", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert adapter.retried == ["orchestration:retry-design:design"]


def test_member_sessions_are_isolated_by_requirement_revision():
    class RequirementsRecorder:
        last_telemetry: ClassVar[dict[str, Any]] = {}

        def __init__(self):
            self.thread_ids = []

        def start(self, *, thread_id, **_kwargs):
            self.thread_ids.append(thread_id)
            return {"status": "completed", "telemetry": {}}

    class DesignRecorder:
        def __init__(self):
            self.session_ids = []

        def has_pending(self, *, session_id):
            self.session_ids.append(session_id)
            return False

        def start(self, **_kwargs):
            return {"status": "completed"}

        def timing_events(self, _session_id):
            return []

    requirements = RequirementsRecorder()
    design = DesignRecorder()
    context = StepContext(
        run_id="same-run",
        app_id="app",
        mode=RunMode.INTERACTIVE,
        requirement_revision=2,
    )

    MemberRequirementsProvider(adapter=requirements).run({"requirements": ["revised"]}, context)
    graph_module.MemberDesignProvider(adapter=design).run({"requirements_result": {}}, context)

    assert requirements.thread_ids == ["orchestration:same-run:requirements:revision-2"]
    assert design.session_ids == ["orchestration:same-run:design:revision-2"]


def test_interrupted_design_resumes_same_run_after_completed_substep(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    architecture = registry.resolve("design.architecture", ProviderKind.MEMBER)
    cloud = InterruptOnceProvider("design.cloud_enrichment", {"cloud_design_result": {}})
    registry.register("design.cloud_enrichment", ProviderKind.BUILTIN, cloud)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    request = RunRequest(
        run_id="interrupted-design-run",
        requirements=["An application."],
        mode=RunMode.BATCH,
    )

    with pytest.raises(KeyboardInterrupt, match="simulated worker termination"):
        orchestrator.start(request)

    checkpoint = orchestrator.store.load(request.run_id)
    assert checkpoint["status"] == "running"
    assert checkpoint["current_stage"] == "design"
    assert checkpoint["requirements"]["steps"][-1]["status"] == "completed"
    assert checkpoint["design"]["steps"][-1]["step"] == "design.architecture"

    recovered = orchestrator.retry_failed(
        request.run_id, reason="worker process was confirmed stopped"
    )

    assert recovered.run_id == request.run_id
    assert recovered.status == StepStatus.COMPLETED
    assert len(architecture.calls) == 1
    assert len(cloud.calls) == 2
    assert recovered.state["retryHistory"][0]["stage"] == "design"


def test_retry_failed_testing_reuses_completed_implementation_steps(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    testing = FailOnceProvider("testing.application", {"testing_result": {"passed": True}})
    registry.register("testing.application", ProviderKind.BUILTIN, testing)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)

    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))
    second = orchestrator.retry_failed(first.run_id, reason="fixed runtime wiring")

    assert first.status == StepStatus.FAILED
    assert second.status == StepStatus.COMPLETED
    assert len(testing.calls) == 2
    assert [item["status"] for item in second.state["testing"]["steps"]] == [
        "failed",
        "completed",
    ]
    for step, kind in (
        ("implementation.scaffold", ProviderKind.MEMBER),
        ("implementation.acceptance_tests", ProviderKind.LLM),
        ("implementation.logic", ProviderKind.LLM),
        ("implementation.vm_selection", ProviderKind.BUILTIN),
        ("implementation.vm_delivery", ProviderKind.LLM),
    ):
        assert len(registry.resolve(step, kind).calls) == 1
    assert second.state["retryHistory"] == [
        {
            "attempt": 1,
            "stage": "testing",
            "reason": "fixed runtime wiring",
            "startedAt": second.state["retryHistory"][0]["startedAt"],
            "diagnosticCode": "FIRST_ATTEMPT",
            "repairOwner": None,
            "invalidatedSteps": [],
        }
    ]


def test_retry_routes_app_dependency_failure_to_logic_and_downstream_only(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("EASYDEP_EXPERIMENT_SESSION", "test-session")
    registry = _registry(tmp_path)
    testing = DiagnosticFailOnceProvider(
        "testing.application",
        {"testing_result": {"passed": True}},
        "APP-DEP-001",
    )
    registry.register("testing.application", ProviderKind.BUILTIN, testing)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)

    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))
    second = orchestrator.retry_failed(first.run_id, reason="repair dependency contract")

    assert first.status == StepStatus.FAILED
    assert second.status == StepStatus.COMPLETED
    assert len(registry.resolve("implementation.scaffold", ProviderKind.MEMBER).calls) == 1
    assert len(registry.resolve("implementation.acceptance_tests", ProviderKind.LLM).calls) == 1
    assert len(registry.resolve("implementation.logic", ProviderKind.LLM).calls) == 2
    assert len(registry.resolve("implementation.vm_selection", ProviderKind.BUILTIN).calls) == 2
    assert len(registry.resolve("implementation.vm_delivery", ProviderKind.LLM).calls) == 2
    retry = second.state["retryHistory"][0]
    assert retry["diagnosticCode"] == "APP-DEP-001"
    assert retry["repairOwner"] == "implementation.logic"
    assert retry["invalidatedSteps"] == [
        "implementation.logic",
        "implementation.vm_selection",
        "implementation.vm_delivery",
    ]
    events = capsys.readouterr().out
    assert '"event": "checkpointLoadStarted"' in events
    assert '"event": "checkpointLoadFinished"' in events
    assert '"event": "checkpointExecutionStarted"' in events
    assert '"event": "runPersistenceFinished"' in events


def test_retry_hands_verified_failed_scaffold_output_to_logic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = "partial-scaffold-run"
    workspace = tmp_path / ".easydep/orchestration/workspaces" / run_id
    application = workspace / "application"
    application.mkdir(parents=True)
    (application / "source.txt").write_text("partial", encoding="utf-8")
    registry = _registry(tmp_path)
    scaffold = PartialDiagnosticFailureProvider(
        "implementation.scaffold",
        {"run_root": str(workspace.resolve()), "scaffold_files": ["source.txt"]},
        "APP-DB-003",
    )
    registry.register("implementation.scaffold", ProviderKind.MEMBER, scaffold)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)

    first = orchestrator.start(
        RunRequest(run_id=run_id, requirements=["An application."], mode=RunMode.BATCH)
    )
    second = orchestrator.retry_failed(first.run_id, reason="repair database integration")

    assert first.status == StepStatus.FAILED
    assert second.status == StepStatus.COMPLETED
    assert len(scaffold.calls) == 1
    logic_calls = registry.resolve("implementation.logic", ProviderKind.LLM).calls
    assert len(logic_calls) == 1
    assert logic_calls[0][0]["run_root"] == str(workspace.resolve())
    assert logic_calls[0][0]["repair_feedback"][0]["code"] == "APP-DB-003"
    retry = second.state["retryHistory"][0]
    assert retry["repairOwner"] == "implementation.logic"
    assert retry["partialOutputHandoffs"] == [
        {
            "step": "implementation.scaffold",
            "fromStatus": "failed",
            "toOwner": "implementation.logic",
            "diagnosticCodes": ["APP-DB-003"],
            "workspaceVerified": True,
        }
    ]
    scaffold_step = second.state["implementation"]["steps"][0]
    assert scaffold_step["status"] == "completed"
    assert scaffold_step["metrics"]["repairHandoff"]["workspaceVerified"] is True


def test_retry_does_not_handoff_failed_scaffold_from_unexpected_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside" / "application"
    outside.mkdir(parents=True)
    registry = _registry(tmp_path)
    scaffold = PartialDiagnosticFailureProvider(
        "implementation.scaffold",
        {"run_root": str(outside.parent.resolve())},
        "APP-DB-003",
    )
    registry.register("implementation.scaffold", ProviderKind.MEMBER, scaffold)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    first = orchestrator.start(
        RunRequest(run_id="unsafe-partial", requirements=["An application."], mode=RunMode.BATCH)
    )

    second = orchestrator.retry_failed(first.run_id, reason="do not mix workspaces")

    assert second.status == StepStatus.FAILED
    assert len(scaffold.calls) == 2
    assert "partialOutputHandoffs" not in second.state["retryHistory"][0]


def test_partial_handoff_uses_only_the_latest_result_for_each_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = "repeated-partial"
    workspace = tmp_path / ".easydep/orchestration/workspaces" / run_id
    (workspace / "application").mkdir(parents=True)
    failures = [
        StepResult(
            step="implementation.scaffold",
            provider=ProviderKind.LLM,
            status=StepStatus.FAILED,
            output={"run_root": str(workspace.resolve()), "attempt": attempt},
            diagnostics=[{"code": "APP-DB-003", "message": "mismatch"}],
        ).model_dump(mode="json")
        for attempt in (1, 2, 3)
    ]
    state = {
        "run_id": run_id,
        "implementation": {"data": {}, "steps": failures},
    }

    invalidated, handoffs = graph_module._invalidate_implementation_from(
        state, "implementation.logic"
    )

    assert invalidated[0] == "implementation.logic"
    assert len(handoffs) == 1
    assert len(state["implementation"]["steps"]) == 1
    assert state["implementation"]["data"]["attempt"] == 3


def test_retry_restarts_scaffold_when_it_failed_before_creating_a_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = _registry(tmp_path)
    scaffold = FailOnceProvider(
        "implementation.scaffold",
        {"run_root": str(tmp_path / "member-run")},
    )
    registry.register("implementation.scaffold", ProviderKind.MEMBER, scaffold)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)

    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))
    stale_workspace = tmp_path / ".easydep/orchestration/workspaces" / first.run_id
    stale_workspace.mkdir(parents=True)
    (stale_workspace / "partial.txt").write_text("incomplete", encoding="utf-8")
    second = orchestrator.retry_failed(first.run_id, reason="repair scaffold policy")

    assert first.status == StepStatus.FAILED
    assert second.status == StepStatus.COMPLETED
    assert len(scaffold.calls) == 2
    assert len(registry.resolve("requirements.analysis", ProviderKind.MEMBER).calls) == 1
    assert len(registry.resolve("design.architecture", ProviderKind.MEMBER).calls) == 1
    assert not stale_workspace.exists()


def test_retry_reclassifies_preserved_generic_database_failure(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    testing = DiagnosticFailOnceProvider(
        "testing.application",
        {"testing_result": {"passed": True}},
        "APPLICATION_TESTS_FAILED",
    )
    registry.register("testing.application", ProviderKind.BUILTIN, testing)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))
    state = orchestrator.store.load(first.run_id)
    failed_step = state["testing"]["steps"][-1]
    failed_step["output"] = {
        "testing_result": {
            "unitTests": {
                "status": "failed",
                "stdout": "StrategySelectionException caused by ClassNotFoundException",
            }
        }
    }
    orchestrator.store.save(first.run_id, state)

    second = orchestrator.retry_failed(first.run_id, reason="reclassify stored output")

    assert second.state["retryHistory"][0]["diagnosticCode"] == "APP-DB-001"
    assert second.state["retryHistory"][0]["repairOwner"] == "implementation.logic"
    feedback = second.state["retryHistory"][0]["repairFeedback"]
    assert feedback[0]["code"] == "APP-DB-001"
    assert "StrategySelectionException" in feedback[0]["evidence"]
    logic_calls = registry.resolve("implementation.logic", ProviderKind.LLM).calls
    assert logic_calls[1][0]["repair_feedback"] == feedback


def test_operator_can_route_testing_failure_to_acceptance_tests(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    testing = DiagnosticFailOnceProvider(
        "testing.application",
        {"testing_result": {"passed": True}},
        "APPLICATION_TESTS_FAILED",
    )
    registry.register("testing.application", ProviderKind.BUILTIN, testing)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))

    second = orchestrator.retry_failed(
        first.run_id,
        reason="독립 실행은 성공했고 테스트 픽스처가 초기화된 DB를 삭제했다.",
        repair_owner="implementation.acceptance_tests",
    )

    retry = second.state["retryHistory"][0]
    assert retry["repairOwner"] == "implementation.acceptance_tests"
    assert retry["invalidatedSteps"] == [
        "implementation.acceptance_tests",
        "implementation.logic",
        "implementation.vm_selection",
        "implementation.vm_delivery",
    ]
    assert retry["repairFeedback"][0]["code"] == "OPERATOR-DIRECTED-REPAIR"
    assert "테스트 픽스처" in retry["repairFeedback"][0]["evidence"]
    assert len(registry.resolve("implementation.scaffold", ProviderKind.MEMBER).calls) == 1
    assert len(registry.resolve("implementation.acceptance_tests", ProviderKind.LLM).calls) == 2


def test_completed_run_can_reopen_only_with_explicit_external_repair_owner(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))

    with pytest.raises(ValueError, match="explicit repair owner"):
        orchestrator.retry_failed(first.run_id, reason="external verification failed")

    second = orchestrator.retry_failed(
        first.run_id,
        reason="An immutable external functional check failed.",
        repair_owner="implementation.logic",
    )

    assert first.status == StepStatus.COMPLETED
    assert second.status == StepStatus.COMPLETED
    retry = second.state["retryHistory"][0]
    assert retry["stage"] == "testing"
    assert retry["repairOwner"] == "implementation.logic"
    assert retry["invalidatedSteps"] == [
        "implementation.logic",
        "implementation.vm_selection",
        "implementation.vm_delivery",
    ]
    assert len(registry.resolve("implementation.scaffold", ProviderKind.MEMBER).calls) == 1
    assert len(registry.resolve("implementation.logic", ProviderKind.LLM).calls) == 2


def test_operator_repair_owner_is_bounded_to_implementation_steps(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    testing = DiagnosticFailOnceProvider(
        "testing.application",
        {"testing_result": {"passed": True}},
        "APPLICATION_TESTS_FAILED",
    )
    registry.register("testing.application", ProviderKind.BUILTIN, testing)
    orchestrator = _orchestrator(tmp_path, monkeypatch, registry)
    first = orchestrator.start(RunRequest(requirements=["An application."], mode=RunMode.BATCH))

    with pytest.raises(ValueError, match="Unknown implementation repair owner"):
        orchestrator.retry_failed(
            first.run_id,
            reason="invalid owner",
            repair_owner="requirements.analysis",
        )


def test_restore_run_application_rehydrates_only_expected_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checkpoint = tmp_path / "artifacts/runs/run-1/03-implementation/application"
    checkpoint.mkdir(parents=True)
    (checkpoint / "build.gradle").write_text("plugins {}", encoding="utf-8")
    manifest = {
        "runId": "run-1",
        "appId": "app",
        "completedStages": ["implementation"],
        "checkpointAttempt": 0,
        "applicationSha256": __import__(
            "app.core.orchestration.artifacts", fromlist=["_tree_sha256"]
        )._tree_sha256(checkpoint),
    }
    (checkpoint.parent.parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    run_root = tmp_path / ".easydep/orchestration/workspaces/run-1"

    restored = restore_run_application("run-1", run_root, root=tmp_path / "artifacts/runs")

    assert restored == run_root / "application"
    assert (restored / "build.gradle").read_text(encoding="utf-8") == "plugins {}"
    with pytest.raises(ValueError, match="unexpected workspace"):
        restore_run_application(
            "run-1", tmp_path / "outside/run-1", root=tmp_path / "artifacts/runs"
        )


def test_restore_rejects_checkpoint_with_mismatched_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checkpoint = tmp_path / "artifacts/runs/run-1/03-implementation/application"
    checkpoint.mkdir(parents=True)
    (checkpoint / "build.gradle").write_text("plugins {}", encoding="utf-8")
    (checkpoint.parent.parent / "manifest.json").write_text(
        json.dumps(
            {
                "runId": "different-run",
                "appId": "app",
                "completedStages": ["implementation"],
                "checkpointAttempt": 0,
                "applicationSha256": "invalid",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="checkpoint is absent"):
        restore_run_application(
            "run-1",
            tmp_path / ".easydep/orchestration/workspaces/run-1",
            root=tmp_path / "artifacts/runs",
            expected_app_id="app",
        )


def test_operator_restore_can_use_latest_existing_prior_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / ".easydep/orchestration/workspaces/source/application"
    workspace.mkdir(parents=True)
    (workspace / "build.gradle").write_text("plugins {}", encoding="utf-8")
    state = {
        "request": {"variant": "full", "case_id": "case"},
        "run_id": "run-1",
        "app_id": "app",
        "current_stage": "implementation",
        "status": "failed",
        "retryHistory": [{"attempt": 1}, {"attempt": 2}],
        "implementation": {"data": {"run_root": str(workspace.parent)}, "steps": []},
    }
    persist_run_artifacts("run-1", state, root=tmp_path / "artifacts/runs")

    restored = restore_run_application(
        "run-1",
        tmp_path / ".easydep/orchestration/workspaces/run-1",
        root=tmp_path / "artifacts/runs",
        checkpoint_attempt=3,
        allow_prior_checkpoint=True,
        expected_app_id="app",
    )

    assert (restored / "build.gradle").read_text(encoding="utf-8") == "plugins {}"


def test_persisted_checkpoint_digest_describes_filtered_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / ".easydep/orchestration/workspaces/source/application"
    workspace.mkdir(parents=True)
    (workspace / "build.gradle").write_text("plugins {}", encoding="utf-8")
    generated = workspace / "build/generated.txt"
    generated.parent.mkdir()
    generated.write_text("must not enter checkpoint", encoding="utf-8")
    state = {
        "request": {"variant": "full", "case_id": "case"},
        "run_id": "run-filtered",
        "app_id": "app",
        "current_stage": "implementation",
        "status": "failed",
        "implementation": {"data": {"run_root": str(workspace.parent)}, "steps": []},
    }

    persist_run_artifacts("run-filtered", state, root=tmp_path / "artifacts/runs")
    restored = restore_run_application(
        "run-filtered",
        tmp_path / ".easydep/orchestration/workspaces/run-filtered",
        root=tmp_path / "artifacts/runs",
        expected_app_id="app",
    )

    assert (restored / "build.gradle").is_file()
    assert not (restored / "build").exists()


def test_requirement_revision_artifacts_do_not_overwrite_original(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "artifacts/runs"
    original = {
        "request": {"requirements": ["original"], "variant": "full"},
        "run_id": "run-revision",
        "app_id": "app",
        "current_stage": "implementation",
        "status": "needs_input",
        "implementation": {"data": {}, "steps": []},
    }
    persist_run_artifacts("run-revision", original, root=root)
    revised = {
        **original,
        "request": {"requirements": ["revised"], "variant": "full"},
        "status": "completed",
        "requirementRevisionHistory": [{"revision": 1}],
    }
    persist_run_artifacts("run-revision", revised, root=root)

    original_manifest = json.loads(
        (root / "run-revision/manifest.json").read_text(encoding="utf-8")
    )
    revised_manifest = json.loads(
        (root / "run-revision/revisions/revision-1/manifest.json").read_text(encoding="utf-8")
    )
    assert original_manifest["requirementRevision"] == 0
    assert original_manifest["status"] == "needs_input"
    assert revised_manifest["requirementRevision"] == 1
    assert revised_manifest["status"] == "completed"


def test_retry_artifacts_preserve_original_and_write_numbered_attempt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / ".easydep/orchestration/workspaces/run-1/application"
    workspace.mkdir(parents=True)
    (workspace / "version.txt").write_text("original", encoding="utf-8")
    base_state = {
        "request": {"variant": "full", "case_id": "P2-azure"},
        "run_id": "run-1",
        "app_id": "app",
        "current_stage": "testing",
        "status": "failed",
        "implementation": {
            "data": {"run_root": str(workspace.parent)},
            "steps": [],
        },
    }
    root = tmp_path / "artifacts/runs"
    persist_run_artifacts("run-1", base_state, root=root)
    (workspace / "version.txt").write_text("repaired", encoding="utf-8")
    repair_state = {
        **base_state,
        "retryHistory": [
            {
                "attempt": 1,
                "stage": "testing",
                "reason": "runtime wiring",
                "startedAt": "2026-08-08T00:00:00+00:00",
            }
        ],
    }
    persist_run_artifacts("run-1", repair_state, root=root)

    original = root / "run-1/03-implementation/application/version.txt"
    repaired = root / "run-1/repairs/attempt-1/03-implementation/application/version.txt"
    assert original.read_text(encoding="utf-8") == "original"
    assert repaired.read_text(encoding="utf-8") == "repaired"
    manifest = json.loads(
        (root / "run-1/repairs/attempt-1/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["developmentRepair"] is True
    assert manifest["parentRunId"] == "run-1"


def test_run_artifacts_exclude_ephemeral_application_test_workspaces(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / ".easydep/orchestration/workspaces/run-1/application"
    transient = workspace / ".easydep-test-example"
    transient.mkdir(parents=True)
    (transient / "locked.tmp").write_text("temporary", encoding="utf-8")
    (workspace / "source.txt").write_text("preserve", encoding="utf-8")
    state = {
        "request": {"variant": "full", "case_id": "P2-gcp"},
        "run_id": "run-1",
        "app_id": "app",
        "current_stage": "testing",
        "status": "failed",
        "implementation": {
            "data": {"run_root": str(workspace.parent)},
            "steps": [],
        },
    }

    run = persist_run_artifacts("run-1", state, root=tmp_path / "artifacts/runs")

    copied = run / "03-implementation/application"
    assert (copied / "source.txt").is_file()
    assert not (copied / ".easydep-test-example").exists()


def test_unregistered_provider_is_rejected_instead_of_falling_back(tmp_path, monkeypatch):
    request = RunRequest(requirements=["An application."])
    request.providers.requirements_analysis = ProviderKind.LLM
    with pytest.raises(LookupError, match=r"requirements\.analysis"):
        _orchestrator(tmp_path, monkeypatch, _registry(tmp_path)).start(request)


def test_requirements_provider_separates_interactive_and_batch_graphs(monkeypatch):
    created: list[bool] = []

    class Adapter:
        def __init__(self, *, feedback_gates: bool):
            created.append(feedback_gates)

    monkeypatch.setattr("app.core.orchestration.providers.RequirementsAdapter", Adapter)
    provider = MemberRequirementsProvider()

    assert provider._adapter(RunMode.INTERACTIVE) is provider._adapter(RunMode.INTERACTIVE)
    assert provider._adapter(RunMode.BATCH) is provider._adapter(RunMode.BATCH)
    assert created == [True, False]


def test_requirements_provider_wraps_resource_answers_without_reclassifying():
    previous = {
        "resource_questions": [
            {"field": "provider"},
            {"field": "region"},
        ]
    }

    answer = MemberRequirementsProvider._resume_answer(
        previous, {"provider": "azure", "region": "koreacentral"}
    )

    assert answer.answers == {"provider": "azure", "region": "koreacentral"}


def test_llm_logic_may_write_production_sources_only(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src" / "main" / "java" / "Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    provider = LlmLogicProvider(
        lambda _prompt: (
            '{"files":{"src/main/java/Example.java":"class Example { int value() { return 1; } }"}}'
        )
    )

    result = provider.run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )
    rejected = LlmLogicProvider(
        lambda _prompt: '{"files":{"src/test/java/ExampleTest.java":"bad"}}'
    ).run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert "return 1" in source.read_text(encoding="utf-8")
    assert rejected.status == StepStatus.FAILED


def test_llm_logic_preserves_member_build_contract_and_merges_dependencies(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/Example.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import jakarta.persistence.Entity;\n@Entity class Example {}",
        encoding="utf-8",
    )
    build = application / "build.gradle"
    build.write_text(
        "plugins { id 'java' }\n\n"
        "dependencies {\n"
        "    implementation 'io.swagger.core.v3:swagger-annotations-jakarta:2.2.38'\n"
        "}\n",
        encoding="utf-8",
    )

    LlmScaffoldProvider._merge_build_dependencies(
        application,
        "app",
        dependencies=[("implementation", "org.springframework.boot:spring-boot-starter-data-jpa")],
    )
    merged = build.read_text(encoding="utf-8")

    assert "swagger-annotations-jakarta:2.2.38" in merged
    assert "spring-boot-starter-data-jpa" in merged
    assert merged.count("dependencies {") == 1


def test_implementation_llm_completion_limit_is_explicitly_configurable(monkeypatch):
    monkeypatch.delenv("LLM_MAX_COMPLETION_TOKENS", raising=False)
    assert _completion_options() == {}
    monkeypatch.setenv("LLM_MAX_COMPLETION_TOKENS", "8192")
    assert _completion_options() == {"max_completion_tokens": 8192}


def test_llm_logic_may_update_application_configuration(tmp_path):
    application = tmp_path / "run" / "application"
    configuration = application / "src" / "main" / "resources" / "application.yml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text("server: {}", encoding="utf-8")
    provider = LlmLogicProvider(
        lambda _prompt: '{"files":{"src/main/resources/application.yml":"server:\\n  port: 8080"}}'
    )

    result = provider.run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert "port: 8080" in configuration.read_text(encoding="utf-8")


def test_llm_logic_receives_structured_retry_feedback(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    prompts = []
    provider = LlmLogicProvider(lambda prompt: prompts.append(json.loads(prompt)) or '{"files":{}}')

    result = provider.run(
        {
            "run_root": str(application.parent),
            "requirements_result": {},
            "design_result": {},
            "repair_feedback": [{"code": "APP-DB-003", "message": "bad binding"}],
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert prompts[0]["repairFeedback"] == [{"code": "APP-DB-003", "message": "bad binding"}]


def test_llm_logic_projects_stable_products_and_only_acceptance_tests(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/Example.java"
    acceptance = application / "src/test/java/example/acceptance/ContractTest.java"
    unit = application / "src/test/java/example/ExampleTest.java"
    source.parent.mkdir(parents=True)
    acceptance.parent.mkdir(parents=True)
    unit.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("class Example {}", encoding="utf-8")
    acceptance.write_text("class ContractTest {}", encoding="utf-8")
    unit.write_text("class ExampleTest {}", encoding="utf-8")
    prompts = []
    provider = LlmLogicProvider(
        lambda prompt: prompts.append(json.loads(prompt)) or '{"files":{}}'
    )

    result = provider.run(
        {
            "run_root": str(application.parent),
            "requirements_result": {
                "requirements": ["observable behavior"],
                "deployment_needs": {"state": {"required": True}},
                "resource_spec": {"provider": "aws"},
                "telemetry": {"large": "must not be exported"},
            },
            "design_result": {
                "artifacts": {
                    "api_spec": {"openapi": "3.0.3"},
                    "erd": "@startuml\n@enduml",
                },
                "llm_timing_events": [{"raw": "must not be exported"}],
            },
            "repair_feedback": [
                {
                    "code": "APP-DB-003",
                    "message": "bad binding",
                    "locations": [f"src/main/java/F{index}.java" for index in range(20)],
                    "details": {"required": "postgresql"},
                }
            ],
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    prompt = prompts[0]
    assert result.status == StepStatus.COMPLETED
    assert set(prompt["requirements"]) == {
        "requirements",
        "deployment_needs",
        "resource_spec",
    }
    assert "telemetry" not in prompt["requirements"]
    assert set(prompt["design"]) == {"apiSpec", "erd"}
    assert prompt["repairFeedback"][0]["locationCount"] == 20
    assert len(prompt["repairFeedback"][0]["locations"]) == 12
    assert list(prompt["immutableAcceptanceTests"]) == [
        "src/test/java/example/acceptance/ContractTest.java"
    ]


def test_llm_logic_may_update_json_production_resources(tmp_path):
    application = tmp_path / "run" / "application"
    resource = application / "src/main/resources/products.json"
    resource.parent.mkdir(parents=True)
    resource.write_text("[]", encoding="utf-8")
    provider = LlmLogicProvider(
        lambda _prompt: '{"files":{"src/main/resources/products.json":"[{\\"id\\":1}]"}}'
    )

    result = provider.run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert json.loads(resource.read_text(encoding="utf-8")) == [{"id": 1}]


def test_llm_logic_distinguishes_explicit_noop_from_missing_files(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src" / "main" / "java" / "Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    payload = {
        "run_root": str(application.parent),
        "requirements_result": {},
        "design_result": {},
    }

    noop = LlmLogicProvider(lambda _prompt: '{"files":{}}').run(
        payload, StepContext(run_id="run", app_id="app", mode=RunMode.BATCH)
    )
    malformed = LlmLogicProvider(lambda _prompt: "{}").run(
        payload, StepContext(run_id="run", app_id="app", mode=RunMode.BATCH)
    )

    assert noop.status == StepStatus.COMPLETED
    assert noop.output["files"] == []
    assert noop.output["noChanges"] is True
    assert malformed.status == StepStatus.FAILED


def test_llm_logic_may_update_production_sql_resources(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    provider = LlmLogicProvider(
        lambda _prompt: json.dumps(
            {
                "files": {
                    "src/main/resources/db/migration/V1__init.sql": (
                        "create table example (id bigint primary key);"
                    )
                }
            }
        )
    )

    result = provider.run(
        {
            "run_root": str(application.parent),
            "requirements_result": {},
            "design_result": {},
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert (application / "src/main/resources/db/migration/V1__init.sql").is_file()


def test_acceptance_llm_may_write_tests_only(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src" / "main" / "java" / "Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    provider = LlmAcceptanceTestsProvider(
        lambda _prompt: '{"files":{"src/test/java/ExampleTest.java":"class ExampleTest {}"}}'
    )

    result = provider.run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )
    rejected = LlmAcceptanceTestsProvider(
        lambda _prompt: '{"files":{"src/main/java/Example.java":"bad"}}'
    ).run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert (application / "src/test/java/ExampleTest.java").is_file()
    assert rejected.status == StepStatus.FAILED


def test_acceptance_repair_receives_existing_tests_and_feedback(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/Example.java"
    existing = application / "src/test/java/ExistingTest.java"
    source.parent.mkdir(parents=True)
    existing.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    existing.write_text("class ExistingTest {}", encoding="utf-8")
    captured = {}

    def invoke(prompt):
        captured.update(json.loads(prompt))
        return json.dumps({"files": {"src/test/java/ExistingTest.java": "class ExistingTest {}"}})

    result = LlmAcceptanceTestsProvider(invoke).run(
        {
            "run_root": str(application.parent),
            "requirements_result": {"requirements": ["GET /health returns 200"]},
            "design_result": {},
            "repair_feedback": [{"code": "EXTERNAL", "evidence": "/health was 500"}],
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert "correcting tests" in captured["instruction"]
    assert captured["existingTests"] == {"src/test/java/ExistingTest.java": "class ExistingTest {}"}
    assert captured["repairFeedback"][0]["code"] == "EXTERNAL"


def test_acceptance_llm_may_write_declarative_test_resources_only(tmp_path):
    application = tmp_path / "run/application"
    source = application / "src/main/java/Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    accepted = LlmAcceptanceTestsProvider(
        lambda _prompt: json.dumps(
            {
                "files": {
                    "src/test/java/ExampleTest.java": "class ExampleTest {}",
                    "src/test/resources/fixture.sql": "delete from example;",
                }
            }
        )
    ).run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )
    rejected = LlmAcceptanceTestsProvider(
        lambda _prompt: json.dumps(
            {"files": {"src/test/resources/setup.ps1": "Remove-Item -Recurse C:\\\\"}}
        )
    ).run(
        {"run_root": str(application.parent), "requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert accepted.status == StepStatus.COMPLETED
    assert (application / "src/test/resources/fixture.sql").is_file()
    assert rejected.status == StepStatus.FAILED


def test_llm_scaffold_creates_a_bounded_spring_application(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = LlmScaffoldProvider(
        lambda _prompt: json.dumps(
            {
                "files": {
                    "src/main/java/com/example/Application.java": "class Application {}",
                }
            }
        )
    )

    result = provider.run(
        {"requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="demo", mode=RunMode.BATCH),
    )
    application = Path(result.output["run_root"]) / "application"

    assert result.status == StepStatus.COMPLETED
    assert result.provider == ProviderKind.LLM
    assert (application / "build.gradle").is_file()
    assert (application / "settings.gradle").is_file()
    assert (application / "src/main/java/com/example/Application.java").is_file()


def test_llm_scaffold_does_not_infer_app_database_from_cloud_storage_need(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = LlmScaffoldProvider(
        lambda _prompt: json.dumps(
            {"files": {"src/main/java/com/example/Application.java": "class Application {}"}}
        )
    )
    context = StepContext(run_id="persistent", app_id="demo", mode=RunMode.BATCH)

    result = provider.run(
        {
            "requirements_result": {
                "deployment_needs": {
                    "persistent_storage": {"required": True, "decision": "accepted"}
                }
            },
            "design_result": {},
        },
        context,
    )
    build = (Path(result.output["run_root"]) / "application/build.gradle").read_text(
        encoding="utf-8"
    )

    assert "spring-boot-starter-data-jpa" not in build
    assert "com.h2database:h2" not in build
    assert result.output["cloud_capability_contract"]["facts"][0]["kind"] == (
        "cloud.capability.persistent_storage"
    )


def test_llm_scaffold_does_not_add_unaccepted_persistence_dependencies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = LlmScaffoldProvider(
        lambda _prompt: json.dumps(
            {"files": {"src/main/java/com/example/Application.java": "class Application {}"}}
        )
    )

    result = provider.run(
        {
            "requirements_result": {
                "deployment_needs": {
                    "persistent_storage": {"required": True, "decision": "needsQuestion"}
                }
            },
            "design_result": {},
        },
        StepContext(run_id="question", app_id="demo", mode=RunMode.BATCH),
    )
    build = (Path(result.output["run_root"]) / "application/build.gradle").read_text(
        encoding="utf-8"
    )

    assert "spring-boot-starter-data-jpa" not in build
    assert "com.h2database:h2" not in build
    assert not (
        Path(result.output["run_root"]) / "application/src/main/resources/application.properties"
    ).exists()


def test_llm_scaffold_does_not_treat_mount_or_legacy_db_hint_as_app_implementation(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    provider = LlmScaffoldProvider(
        lambda _prompt: json.dumps(
            {"files": {"src/main/java/com/example/Application.java": "class Application {}"}}
        )
    )

    result = provider.run(
        {
            "requirements_result": {
                "deployment_needs": {
                    "persistent_storage": {
                        "required": True,
                        "decision": "needsQuestion",
                    },
                    "persistent_storage_mount": {
                        "required": True,
                        "decision": "accepted",
                    },
                    "embedded_database_support": {
                        "required": True,
                        "decision": "accepted",
                        "metadata": {"db_type": "SQLite"},
                    },
                }
            },
            "design_result": {},
        },
        StepContext(run_id="explicit-mount", app_id="demo", mode=RunMode.BATCH),
    )
    application = Path(result.output["run_root"]) / "application"

    build = (application / "build.gradle").read_text(encoding="utf-8")
    assert "spring-boot-starter-data-jpa" not in build
    assert "org.xerial:sqlite-jdbc" not in build
    assert "hibernate-community-dialects" not in build
    assert not (application / "src/main/resources/application.properties").exists()


def test_llm_scaffold_derives_build_dependencies_from_generated_app_artifacts(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    provider = LlmScaffoldProvider(
        lambda _prompt: json.dumps(
            {
                "files": {
                    "src/main/java/com/example/Note.java": (
                        "import jakarta.persistence.Entity;\n@Entity class Note {}"
                    ),
                    "src/main/resources/application.properties": (
                        "spring.datasource.url=${EASYDEP_DATASOURCE_URL:jdbc:sqlite::memory:}\n"
                        "spring.datasource.driver-class-name=org.sqlite.JDBC\n"
                        "spring.jpa.database-platform=org.hibernate.community.dialect.SQLiteDialect\n"
                    ),
                }
            }
        )
    )

    result = provider.run(
        {"requirements_result": {}, "design_result": {}},
        StepContext(run_id="artifact-contract", app_id="demo", mode=RunMode.BATCH),
    )
    build = (Path(result.output["run_root"]) / "application/build.gradle").read_text(
        encoding="utf-8"
    )

    assert result.status == StepStatus.COMPLETED
    assert "spring-boot-starter-data-jpa" in build
    assert "org.xerial:sqlite-jdbc" in build
    assert "hibernate-community-dialects" in build
    assert result.output["application_runtime_contract"]["facts"]


def test_llm_scaffold_returns_structured_database_diagnostic_and_can_retry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responses = iter(
        [
            {
                "files": {
                    "src/main/java/com/example/Application.java": "class Application {}",
                    "src/main/resources/application.properties": (
                        "spring.datasource.url=jdbc:sqlite::memory:\n"
                    ),
                },
                "runtimeContract": {
                    "facts": [
                        {
                            "id": "declared-db",
                            "kind": "runtime.database",
                            "attributes": {"engine": "h2"},
                        }
                    ]
                },
            },
            {
                "files": {
                    "src/main/java/com/example/Application.java": "class Application {}",
                    "src/main/resources/application.properties": (
                        "spring.datasource.url=jdbc:sqlite::memory:\n"
                    ),
                },
                "runtimeContract": {
                    "facts": [
                        {
                            "id": "declared-db",
                            "kind": "runtime.database",
                            "attributes": {"engine": "sqlite"},
                        }
                    ]
                },
            },
        ]
    )
    provider = LlmScaffoldProvider(lambda _prompt: json.dumps(next(responses)))
    context = StepContext(run_id="structured-retry", app_id="demo", mode=RunMode.BATCH)

    first = provider.run({"requirements_result": {}, "design_result": {}}, context)
    second = provider.run(
        {
            "requirements_result": {},
            "design_result": {},
            **first.output,
        },
        context,
    )

    assert first.status == StepStatus.FAILED
    assert first.diagnostics[0].code == "APP-DB-001"
    assert second.status == StepStatus.COMPLETED


def test_llm_scaffold_asks_before_resolving_managed_recovery_node_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prompts: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "files": {
                    "src/main/java/com/example/Application.java": "class Application {}",
                    "src/main/resources/application.properties": (
                        "spring.datasource.url=jdbc:sqlite:/srv/state/app.db\n"
                    ),
                }
            },
            {
                "files": {
                    "src/main/java/com/example/Application.java": "class Application {}",
                    "src/main/resources/application.properties": (
                        "spring.datasource.url=${EXTERNAL_DATABASE_URL}\n"
                    ),
                }
            },
        ]
    )

    def invoke(prompt: str) -> str:
        prompts.append(json.loads(prompt))
        return json.dumps(next(responses))

    provider = LlmScaffoldProvider(invoke)
    first = provider.run(
        {
            "requirements_result": {
                "deployment_needs": {
                    "availability_requirement": {
                        "decision": "accepted",
                        "metadata": {"high_availability": True},
                    }
                },
                "resource_spec": {
                    "computeProfile": "managedGroupOne",
                    "replicaCount": 1,
                    "publicIngress": "loadBalanced",
                },
            },
            "design_result": {},
        },
        StepContext(run_id="ha-state", app_id="demo", mode=RunMode.INTERACTIVE),
    )

    assert first.status == StepStatus.NEEDS_INPUT
    assert first.prompt["kind"] == "app-cloud-consistency"
    assert first.diagnostics[0].code == "BIND-STATE-GROUP-001"
    assert prompts[0]["consistencyResolution"] is None

    second = provider.run(
        {
            "requirements_result": {
                "deployment_needs": {
                    "availability_requirement": {
                        "decision": "accepted",
                        "metadata": {"high_availability": True},
                    }
                },
                "resource_spec": {
                    "computeProfile": "managedGroupOne",
                    "replicaCount": 1,
                    "publicIngress": "loadBalanced",
                },
            },
            "design_result": {},
            **first.output,
        },
        StepContext(
            run_id="ha-state",
            app_id="demo",
            mode=RunMode.INTERACTIVE,
            response={"resolution": "externalize-or-replicate-state"},
        ),
    )

    assert second.status == StepStatus.COMPLETED
    assert second.metrics["consistency_resolution"] == ("externalize-or-replicate-state")
    assert prompts[1]["existingSources"]
    assert prompts[1]["consistencyResolution"]["diagnostics"]


def test_requirement_owned_node_state_cannot_be_silently_externalized(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = 0

    def invoke(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "files": {
                    "src/main/java/example/Application.java": "class Application {}",
                }
            }
        )

    provider = LlmScaffoldProvider(invoke)
    payload = {
        "requirements_result": {
            "deployment_needs": {
                "open_state_constraint": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["R-state"],
                    "evidenceSpans": ["Keep state on the VM filesystem."],
                    "metadata": {
                        "applicationState": {
                            "durability": "persistent",
                            "accessScope": "node-filesystem",
                        }
                    },
                },
                "availability_requirement": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["R-ha"],
                    "metadata": {"high_availability": True},
                },
            },
            "resource_spec": {
                "computeProfile": "managedGroupOne",
                "replicaCount": 1,
                "publicIngress": "loadBalanced",
            },
        },
        "design_result": {},
    }

    first = provider.run(
        payload,
        StepContext(run_id="intent-owned", app_id="demo", mode=RunMode.INTERACTIVE),
    )
    second = provider.run(
        {**payload, **first.output},
        StepContext(
            run_id="intent-owned",
            app_id="demo",
            mode=RunMode.INTERACTIVE,
            response={"resolution": "externalize-or-replicate-state"},
        ),
    )

    alternatives = {
        item["id"] for question in first.prompt["questions"] for item in question["alternatives"]
    }
    assert first.status == StepStatus.NEEDS_INPUT
    assert alternatives == {
        "revise-state-requirement",
        "revise-compute-topology",
    }
    assert second.status == StepStatus.NEEDS_INPUT
    assert {item["resolution"] for item in second.prompt["upstreamRevisionResponses"]} == {
        "revise-state-requirement",
    }
    assert calls == 1


def test_consistency_resolution_replaces_stale_production_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    responses = iter(
        [
            {
                "files": {
                    "src/main/java/example/Application.java": "class Application {}",
                    "src/main/java/example/LocalStore.java": (
                        "import java.nio.file.Files; class LocalStore { void save(java.nio.file.Path p) "
                        "throws Exception { Files.newOutputStream(p); } }"
                    ),
                    "src/main/resources/application.properties": (
                        "state.path=${STATE_PATH:/srv/state/app.bin}"
                    ),
                }
            },
            {
                "files": {
                    "src/main/java/example/Application.java": "class Application {}",
                    "src/main/resources/application.properties": "state.url=${EXTERNAL_STATE_URL}",
                }
            },
        ]
    )
    provider = LlmScaffoldProvider(lambda _prompt: json.dumps(next(responses)))
    payload = {
        "requirements_result": {
            "deployment_needs": {
                "availability_requirement": {
                    "decision": "accepted",
                    "metadata": {"high_availability": True},
                }
            },
            "resource_spec": {
                "computeProfile": "managedGroupOne",
                "replicaCount": 1,
                "publicIngress": "loadBalanced",
            },
        },
        "design_result": {},
    }

    first = provider.run(
        payload,
        StepContext(run_id="stale-source", app_id="demo", mode=RunMode.INTERACTIVE),
    )
    second = provider.run(
        {**payload, **first.output},
        StepContext(
            run_id="stale-source",
            app_id="demo",
            mode=RunMode.INTERACTIVE,
            response={"resolution": "externalize-or-replicate-state"},
        ),
    )

    application = Path(second.output["run_root"]) / "application"
    assert first.status == StepStatus.NEEDS_INPUT
    assert second.status == StepStatus.COMPLETED
    assert not (application / "src/main/java/example/LocalStore.java").exists()


def test_failed_consistency_resolution_restores_the_checkpoint_application(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    local_files = {
        "src/main/java/com/example/Application.java": "class Application {}",
        "src/main/java/com/example/Store.java": (
            "import java.nio.file.Files; class Store { void save(java.nio.file.Path p) "
            "throws Exception { Files.newOutputStream(p); } }"
        ),
        "src/main/resources/application.properties": (
            "state.path=${STATE_PATH:/srv/state/app.bin}\n"
        ),
    }
    provider = LlmScaffoldProvider(lambda _prompt: json.dumps({"files": local_files}))
    requirements = {
        "deployment_needs": {
            "availability_requirement": {
                "decision": "accepted",
                "metadata": {"high_availability": True},
            }
        },
        "resource_spec": {
            "computeProfile": "managedGroupOne",
            "replicaCount": 1,
            "publicIngress": "loadBalanced",
        },
    }
    first = provider.run(
        {"requirements_result": requirements, "design_result": {}},
        StepContext(run_id="ha-restore", app_id="demo", mode=RunMode.INTERACTIVE),
    )
    application = Path(first.output["run_root"]) / "application"
    before = {
        path.relative_to(application).as_posix(): path.read_bytes()
        for path in application.rglob("*")
        if path.is_file()
    }

    second = provider.run(
        {
            "requirements_result": requirements,
            "design_result": {},
            **first.output,
        },
        StepContext(
            run_id="ha-restore",
            app_id="demo",
            mode=RunMode.INTERACTIVE,
            response={"resolution": "externalize-or-replicate-state"},
        ),
    )
    after = {
        path.relative_to(application).as_posix(): path.read_bytes()
        for path in application.rglob("*")
        if path.is_file()
    }

    assert second.status == StepStatus.NEEDS_INPUT
    assert after == before
    assert not list(application.parent.parent.glob(".easydep-consistency-repair-*"))


def test_batch_scaffold_fails_instead_of_waiting_for_a_consistency_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = LlmScaffoldProvider(
        lambda _prompt: json.dumps(
            {
                "files": {
                    "src/main/java/example/Store.java": (
                        "import java.nio.file.Files; class Store { void save(java.nio.file.Path p) "
                        "throws Exception { Files.newOutputStream(p); } }"
                    ),
                    "src/main/resources/application.properties": (
                        "state.path=${STATE_PATH:/srv/state/app.bin}\n"
                    ),
                }
            }
        )
    )

    result = provider.run(
        {
            "requirements_result": {
                "deployment_needs": {
                    "availability_requirement": {
                        "decision": "accepted",
                        "metadata": {"high_availability": True},
                    }
                },
                "resource_spec": {
                    "computeProfile": "managedGroupOne",
                    "replicaCount": 1,
                    "publicIngress": "loadBalanced",
                },
            },
            "design_result": {},
        },
        StepContext(run_id="ha-batch", app_id="demo", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.FAILED
    assert {item.code for item in result.diagnostics} == {
        "BIND-STATE-GROUP-001",
        "BATCH_INPUT_REQUIRED",
    }


def test_llm_scaffold_rejects_non_production_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = LlmScaffoldProvider(
        lambda _prompt: '{"files":{"src/test/java/BadTest.java":"bad"}}'
    ).run(
        {"requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="demo", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.FAILED


def test_no_consistency_validator_changes_only_diagnostic_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = {
        "files": {
            "src/main/java/com/example/Application.java": "class Application {}",
            "src/main/resources/application.properties": (
                "spring.datasource.url=jdbc:sqlite:/data/app.db\n"
            ),
        },
        "runtimeContract": {
            "facts": [
                {
                    "id": "declared-db",
                    "kind": "runtime.database",
                    "attributes": {"engine": "h2"},
                }
            ]
        },
    }
    provider = LlmScaffoldProvider(lambda _prompt: json.dumps(response))

    result = provider.run(
        {
            "requirements_result": {},
            "design_result": {},
            "enable_consistency_validator": False,
        },
        StepContext(run_id="no-validator", app_id="demo", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert result.metrics["consistency_validator_enabled"] is False


def test_llm_scaffold_accepts_json_production_resources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = LlmScaffoldProvider(
        lambda _prompt: json.dumps(
            {
                "files": {
                    "src/main/java/example/Application.java": "class Application {}",
                    "src/main/resources/products.json": '[{"id":1}]',
                }
            }
        )
    ).run(
        {"requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="demo", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert (
        tmp_path
        / ".easydep/orchestration/workspaces/run/application/src/main/resources/products.json"
    ).is_file()


def test_llm_prompts_forbid_unverified_framework_overrides():
    from app.core.orchestration.providers import (
        ACCEPTANCE_TEST_SYSTEM_PROMPT,
        LOGIC_SYSTEM_PROMPT,
        SCAFFOLD_SYSTEM_PROMPT,
    )

    assert "Spring Framework 6" in SCAFFOLD_SYSTEM_PROMPT
    assert "exact superclass or interface signature" in SCAFFOLD_SYSTEM_PROMPT
    assert "never add `@Override`" in LOGIC_SYSTEM_PROMPT
    assert "org.springframework.boot.test.web.server" in ACCEPTANCE_TEST_SYSTEM_PROMPT
    assert "legacy" in ACCEPTANCE_TEST_SYSTEM_PROMPT


def test_cloud_design_no_verification_observes_mismatch_without_repair():
    provider = BuiltinCloudDesignProvider(
        adapter=SimpleNamespace(finalize=lambda **_kwargs: {"kb_used": True}),
        revise_api=lambda *_args: pytest.fail("repair must not run"),
    )
    result = provider.run(
        {
            "requirements_result": {
                "requirements": [{"id": "R1", "description": "response field result"}]
            },
            "design_result": {
                "artifacts": {
                    "class_diagram": "@startuml\n@enduml",
                    "api_spec": {
                        "openapi": "3.0.3",
                        "paths": {"/value": {"get": {"responses": {"200": {}}}}},
                    },
                },
                "api_spec_model": {"endpoints": []},
            },
            "enable_repair_feedback": False,
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert result.metrics["repair_feedback_enabled"] is False
    assert result.metrics["api_traceability_repaired"] is False


def test_testing_rejects_a_repository_without_acceptance_tests(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)

    result = _TestingAdapter().run(implementation_result={"run_root": str(application.parent)})

    assert result["passed"] is False
    assert result["unitTests"]["status"] == "failed"
    assert result["unitTests"]["testFiles"] == []


def test_artifacts_store_stage_results_once(tmp_path):
    state = {
        "request": {"variant": "full", "case_id": "p1", "providers": {}},
        "app_id": "app",
        "current_stage": "completed",
        "status": "completed",
        "requirements": {"data": {"member_result": {}}, "steps": []},
        "design": {"data": {}, "steps": []},
        "implementation": {"data": {}, "steps": []},
        "testing": {"data": {}, "steps": []},
    }
    run = persist_run_artifacts("run-1", state, root=tmp_path)

    assert (run / "manifest.json").is_file()
    assert (run / "01-requirements" / "result.json").is_file()
    assert not (run / "result.json").exists()


def test_artifacts_rank_stage_and_overlapping_subtask_timings(tmp_path):
    state = {
        "request": {"variant": "full", "case_id": "p1", "providers": {}},
        "app_id": "app",
        "requirements": {
            "data": {},
            "steps": [
                {
                    "step": "requirements.analysis",
                    "provider": "member",
                    "status": "completed",
                    "metrics": {
                        "timing": {"elapsedSeconds": 10.0},
                        "llm_timing_events": [
                            {"operation": "a", "elapsedSeconds": 8.0, "status": "completed"},
                            {"operation": "b", "elapsedSeconds": 7.0, "status": "completed"},
                        ],
                    },
                }
            ],
        },
        "design": {
            "data": {},
            "steps": [
                {
                    "step": "design.architecture",
                    "provider": "member",
                    "status": "completed",
                    "metrics": {"timing": {"elapsedSeconds": 20.0}},
                }
            ],
        },
        "implementation": {"data": {}, "steps": []},
        "testing": {"data": {}, "steps": []},
    }

    run = persist_run_artifacts("run-timing", state, root=tmp_path)
    summary = json.loads((run / "timing-summary.json").read_text(encoding="utf-8"))

    assert [item["step"] for item in summary["stageRanking"]] == [
        "design.architecture",
        "requirements.analysis",
    ]
    assert [item["operation"] for item in summary["subtaskRanking"]] == ["a", "b"]
    assert summary["interpretation"]["subtaskDurationsMustNotBeSummedAsCriticalPath"] is True


def test_implementation_worker_lock_rejects_overlap(tmp_path):
    lock = tmp_path / "worker.lock"

    with (
        exclusive_implementation_worker(lock),
        pytest.raises(RuntimeError, match="concurrent execution"),
        exclusive_implementation_worker(lock),
    ):
        pass


def test_run_execution_lock_rejects_only_same_run(tmp_path):
    with exclusive_run_execution("run-a", tmp_path):
        with exclusive_run_execution("run-b", tmp_path):
            pass
        with (
            pytest.raises(RuntimeError, match="concurrent execution"),
            exclusive_run_execution("run-a", tmp_path),
        ):
            pass


def test_member_scaffold_without_explicit_transmission_approval_only_plans(tmp_path, monkeypatch):
    from app.core.orchestration.providers import MemberScaffoldProvider

    application = tmp_path / "generated" / "application"
    application.mkdir(parents=True)
    job = tmp_path / "job.json"
    job.write_text('{"verification":{"compile":true}}', encoding="utf-8")
    provider = object.__new__(MemberScaffoldProvider)
    provider.settings = type(
        "Settings",
        (),
        {
            "python_executable": Path("python"),
            "repository_root": tmp_path,
            "command_timeout_seconds": 60,
        },
    )()
    provider.client = type("Client", (), {"prepare_job": lambda *_args, **_kwargs: job})()
    monkeypatch.setattr(
        "app.core.orchestration.providers.run_process_tree",
        lambda *_args, **_kwargs: type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"run_root": str(application.parent), "member_plan": {}}),
                "stderr": "",
            },
        )(),
    )

    result = provider.run(
        {
            "requirements_result": {},
            "design_result": {"artifacts": {}},
            "cloud_design_result": {},
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.NEEDS_INPUT
    assert result.diagnostics[0].code == "MEMBER-APPROVAL-REQUIRED"
    assert json.loads(job.read_text(encoding="utf-8"))["verification"]["compile"] is False


def test_member_scaffold_runs_implemented_workflow_with_explicit_approval(tmp_path, monkeypatch):
    from app.core.orchestration.providers import MemberScaffoldProvider

    application = tmp_path / "generated" / "application"
    application.mkdir(parents=True)
    job = tmp_path / "job.json"
    job.write_text('{"verification":{"compile":false}}', encoding="utf-8")
    provider = object.__new__(MemberScaffoldProvider)
    provider.settings = type(
        "Settings",
        (),
        {
            "python_executable": Path("python"),
            "repository_root": tmp_path,
            "command_timeout_seconds": 60,
        },
    )()
    provider.client = type("Client", (), {"prepare_job": lambda *_args, **_kwargs: job})()
    commands = []

    def completed(command, **kwargs):
        commands.append(command)
        assert kwargs["env"]["LLM_API_KEY"] == "approved-key"
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "run_root": str(application.parent),
                        "member_plan": {"status": "COMPLETE"},
                        "member_workflow_status": "COMPLETE",
                    }
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setenv("EASYDEP_APPROVE_MEMBER_IMPLEMENTATION", "1")
    monkeypatch.setenv("API_KEY", "approved-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.setattr("app.core.orchestration.providers.run_process_tree", completed)

    result = provider.run(
        {
            "requirements_result": {},
            "design_result": {"artifacts": {}},
            "cloud_design_result": {},
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert commands[0][-1] == "--run-implemented-workflow"
    assert json.loads(job.read_text(encoding="utf-8"))["verification"]["compile"] is True
    assert result.output["member_workflow_status"] == "COMPLETE"


def test_member_scaffold_checkpoint_retry_requests_failed_cache_recovery(tmp_path, monkeypatch):
    from app.core.orchestration.providers import MemberScaffoldProvider

    application = tmp_path / "generated" / "application"
    application.mkdir(parents=True)
    job = tmp_path / "job.json"
    job.write_text('{"verification":{"compile":false}}', encoding="utf-8")
    provider = object.__new__(MemberScaffoldProvider)
    provider.settings = type(
        "Settings",
        (),
        {
            "python_executable": Path("python"),
            "repository_root": tmp_path,
            "command_timeout_seconds": 60,
        },
    )()
    provider.client = type("Client", (), {"prepare_job": lambda *_args, **_kwargs: job})()
    commands = []

    def completed(command, **_kwargs):
        commands.append(command)
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"run_root": str(application.parent), "member_plan": {}}),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("app.core.orchestration.providers.run_process_tree", completed)
    provider.run(
        {"requirements_result": {}, "design_result": {"artifacts": {}}},
        StepContext(
            run_id="run",
            app_id="app",
            mode=RunMode.BATCH,
            checkpoint_retry_attempt=1,
        ),
    )

    assert commands[0][-1] == "--retry-failed-generation"


def test_completed_member_workflow_skips_temporary_application_llms(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    payload = {
        "run_root": str(tmp_path / "run"),
        "member_workflow_status": "COMPLETE",
        "cloud_capability_contract": {},
        "deployment_binding_contract": {},
    }

    acceptance = LlmAcceptanceTestsProvider(
        lambda _prompt: pytest.fail("임시 acceptance LLM을 호출하면 안 됩니다")
    ).run(payload, StepContext(run_id="run", app_id="app", mode=RunMode.BATCH))
    logic = LlmLogicProvider(
        lambda _prompt: pytest.fail("임시 logic LLM을 호출하면 안 됩니다")
    ).run(payload, StepContext(run_id="run", app_id="app", mode=RunMode.BATCH))

    assert acceptance.status == StepStatus.COMPLETED
    assert acceptance.metrics["llm_calls"] == 0
    assert logic.status == StepStatus.COMPLETED
    assert logic.metrics["llm_calls"] == 0


def test_completed_member_workflow_uses_logic_llm_for_external_repair_feedback(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src/main/java/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    invoked = []

    def repair(prompt: str) -> str:
        invoked.append(json.loads(prompt))
        return json.dumps({"files": {}})

    result = LlmLogicProvider(repair).run(
        {
            "run_root": str(tmp_path / "run"),
            "member_workflow_status": "COMPLETE",
            "repair_feedback": [
                {
                    "code": "APP-BUSINESS-001",
                    "message": "The external business oracle observed an empty catalog.",
                    "details": {"failedPhase": "course-catalog"},
                }
            ],
            "cloud_capability_contract": {},
            "deployment_binding_contract": {},
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert result.metrics["llm_calls"] == 1
    assert invoked[0]["repairFeedback"][0]["code"] == "APP-BUSINESS-001"


def test_completed_member_workflow_checks_requirement_runtime_contract(tmp_path):
    application = tmp_path / "run" / "application"
    resources = application / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring:\n  datasource:\n    url: jdbc:h2:mem:generated\n"
        "    driver-class-name: org.h2.Driver\n",
        encoding="utf-8",
    )
    (application / "build.gradle").write_text(
        "dependencies { runtimeOnly 'com.h2database:h2' }",
        encoding="utf-8",
    )
    payload = {
        "run_root": str(tmp_path / "run"),
        "member_workflow_status": "COMPLETE",
        "requirements_result": {
            "deployment_needs": {
                "database": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["NFR-DB"],
                    "metadata": {
                        "databaseEngine": "PostgreSQL",
                        "deploymentMode": "separate container",
                        "embedded": False,
                    },
                }
            }
        },
    }

    logic = LlmLogicProvider(
        lambda _prompt: pytest.fail("completed member output must not call the logic LLM")
    ).run(payload, StepContext(run_id="run", app_id="app", mode=RunMode.BATCH))

    assert logic.status == StepStatus.FAILED
    assert {item.code for item in logic.diagnostics} == {
        "APP-DB-ENGINE-001",
        "APP-DB-MODE-001",
    }
    assert logic.output["run_root"] == str(tmp_path / "run")


def test_completed_member_workflow_closes_observed_database_dependencies_without_llm(tmp_path):
    application = tmp_path / "run" / "application"
    resources = application / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring:\n"
        "  datasource:\n"
        "    url: ${DATABASE_URL:jdbc:postgresql://state:5432/app}\n"
        "  flyway:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    build = application / "build.gradle"
    build.write_text(
        "plugins { id 'java' }\n\n"
        "dependencies {\n"
        "    implementation 'org.flywaydb:flyway-core'\n"
        "    runtimeOnly 'org.postgresql:postgresql'\n"
        "}\n",
        encoding="utf-8",
    )

    result = LlmLogicProvider(
        lambda _prompt: pytest.fail("dependency closure must not call the logic LLM")
    ).run(
        {
            "run_root": str(tmp_path / "run"),
            "member_workflow_status": "COMPLETE",
            "cloud_capability_contract": {},
            "deployment_binding_contract": {},
        },
        StepContext(run_id="run", app_id="app", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.COMPLETED
    assert "org.flywaydb:flyway-database-postgresql" in build.read_text(encoding="utf-8")
    assert result.metrics["llm_calls"] == 0


def test_retry_refreshes_legacy_generic_test_failure_with_file_ownership(tmp_path):
    application = tmp_path / "application"
    source = application / "src/main/java/example/Broken.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Broken {}", encoding="utf-8")
    state = {
        "implementation": {"data": {"scaffold_files": ["src/main/java/example/Broken.java"]}},
        "testing": {
            "steps": [
                {
                    "status": "failed",
                    "diagnostics": [
                        {
                            "code": "APPLICATION_TESTS_FAILED",
                            "message": "Generated application tests failed.",
                        }
                    ],
                    "output": {
                        "testing_result": {
                            "repository": str(application),
                            "unitTests": {
                                "status": "failed",
                                "stdout": "> Task :compileJava FAILED\nCompilation failed",
                                "stderr": f"{source}:3: error: invalid override",
                            },
                        }
                    },
                }
            ]
        },
    }

    assert graph_module._last_diagnostic_code(state, StageName.TESTING) == (
        "APP-COMPILE-SCAFFOLD-001"
    )
