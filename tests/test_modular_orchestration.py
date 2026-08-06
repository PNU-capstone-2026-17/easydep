from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import app.core.orchestration.graph as graph_module
from app.core.orchestration.adapters.testing import TestingAdapter as _TestingAdapter
from app.core.orchestration.artifacts import persist_run_artifacts
from app.core.orchestration.contracts import (
    ProviderKind,
    RunMode,
    RunRequest,
    StepContext,
    StepResult,
    StepStatus,
)
from app.core.orchestration.graph import Orchestrator, build_orchestration_graph
from app.core.orchestration.providers import (
    LlmAcceptanceTestsProvider,
    LlmLogicProvider,
    LlmScaffoldProvider,
)
from app.core.orchestration.registry import ProviderRegistry
from app.core.orchestration.store import RunStore
from app.core.orchestration.worker_lock import exclusive_implementation_worker


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


def test_failed_selected_provider_stops_the_run(tmp_path, monkeypatch):
    failed = FakeProvider("requirements.analysis", {}, StepStatus.FAILED)
    result = _orchestrator(tmp_path, monkeypatch, _registry(tmp_path, failed)).start(
        RunRequest(requirements=["An application."])
    )

    assert result.status == StepStatus.FAILED
    assert result.stage.value == "requirements"
    assert "design" not in result.state


def test_unregistered_provider_is_rejected_instead_of_falling_back(tmp_path, monkeypatch):
    request = RunRequest(requirements=["An application."])
    request.providers.requirements_analysis = ProviderKind.LLM
    with pytest.raises(LookupError, match=r"requirements\.analysis"):
        _orchestrator(tmp_path, monkeypatch, _registry(tmp_path)).start(request)


def test_llm_logic_may_write_production_sources_only(tmp_path):
    application = tmp_path / "run" / "application"
    source = application / "src" / "main" / "java" / "Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Example {}", encoding="utf-8")
    provider = LlmLogicProvider(
        lambda _prompt: '{"files":{"src/main/java/Example.java":"class Example { int value() { return 1; } }"}}'
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


def test_llm_scaffold_rejects_non_production_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = LlmScaffoldProvider(
        lambda _prompt: '{"files":{"src/test/java/BadTest.java":"bad"}}'
    ).run(
        {"requirements_result": {}, "design_result": {}},
        StepContext(run_id="run", app_id="demo", mode=RunMode.BATCH),
    )

    assert result.status == StepStatus.FAILED


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


def test_implementation_worker_lock_rejects_overlap(tmp_path):
    lock = tmp_path / "worker.lock"

    with (
        exclusive_implementation_worker(lock),
        pytest.raises(RuntimeError, match="concurrent execution"),
        exclusive_implementation_worker(lock),
    ):
        pass


def test_member_scaffold_defers_compile_to_testing(tmp_path, monkeypatch):
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
    provider.client = type(
        "Client", (), {"prepare_job": lambda *_args, **_kwargs: job}
    )()
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

    assert result.status == StepStatus.COMPLETED
    assert json.loads(job.read_text(encoding="utf-8"))["verification"]["compile"] is False
