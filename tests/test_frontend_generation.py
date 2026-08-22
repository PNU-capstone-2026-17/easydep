from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.db.models import TYPE_FRONTEND_SOURCE_CODE
from app.implementation.config import ImplementationSettings
from app.implementation.agents.workspace import snapshot_files
from app.implementation.agents.verification.build import verify_frontend_workspace
from app.implementation.agents.verification.frontend import frontend_contract_violations
from app.implementation.planning.design_context import generate_frontend_tasks
from app.implementation.planning.frontend_contracts import (
    FrontendContractBudgetExceeded,
    GeneratedClientContracts,
)
from app.implementation.generation.frontend_scaffold import (
    FrontendScaffoldError,
    openapi_typescript_fetch_command,
    react_scaffold_files,
    resolve_api_base_url,
    validate_openapi,
)
from app.implementation.application.jobs import ImplementationWorker
from app.implementation.domain.models import CommandEvidence


OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Orders", "version": "1"},
    "paths": {
        "/orders/{orderId}": {
            "get": {
                "operationId": "getOrder",
                "summary": "Get an order",
                "tags": ["Orders"],
                "parameters": [
                    {
                        "name": "orderId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "expand",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/orders": {
            "post": {
                "operationId": "createOrder",
                "summary": "Create an order",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
    },
}


def test_builds_pinned_openapi_generator_typescript_fetch_command(tmp_path: Path) -> None:
    source = tmp_path / "openapi.json"
    target = tmp_path / "frontend/src/generated"
    command = openapi_typescript_fetch_command(tmp_path, source, target)

    assert "openapitools/openapi-generator-cli:v7.24.0" in command
    assert command[command.index("-g") + 1] == "typescript-fetch"
    assert command[command.index("-i") + 1] == "/workspace/openapi.json"
    assert command[command.index("-o") + 1] == "/workspace/frontend/src/generated"


def test_react_scaffold_contains_no_hardcoded_operation_implementation() -> None:
    files = react_scaffold_files("Order Console", "/service")

    assert {
        "package.json",
        "src/App.tsx",
        "src/config.ts",
        "src/vite-env.d.ts",
    } <= set(files)
    assert "getOrder" not in "\n".join(files.values())
    assert "OpenAPI Generator" in files["README.md"]
    assert "HashRouter" in files["src/main.tsx"]


def test_rejects_openapi_without_operations() -> None:
    with pytest.raises(FrontendScaffoldError, match="at least one operation"):
        validate_openapi({"openapi": "3.0.3", "paths": {}})


def test_resolves_api_base_url_from_openapi_server_without_inventing_prefix() -> None:
    with_server = {
        **OPENAPI,
        "servers": [
            {
                "url": "https://{tenant}.example.com/api/",
                "variables": {"tenant": {"default": "orders"}},
            }
        ],
    }

    assert resolve_api_base_url(with_server) == "https://orders.example.com/api"
    assert resolve_api_base_url(OPENAPI) == ""
    assert resolve_api_base_url(with_server, " https://override.example/v1/ ") == (
        "https://override.example/v1"
    )


def test_discovers_standard_openapi_generator_source_layout(tmp_path: Path) -> None:
    generated = tmp_path / "src/generated"
    api = generated / "src/apis/OrdersApi.ts"
    model = generated / "src/models/Order.ts"
    runtime = generated / "src/runtime.ts"
    for path in (api, model, runtime):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"export const {path.stem} = true;", encoding="utf-8")

    contracts = GeneratedClientContracts.discover(generated)

    assert contracts.import_root == "src/generated/src"
    assert contracts.page_import_root == "../generated/src"
    assert "// src/apis/OrdersApi.ts" in contracts.render()


def test_discovers_flat_generated_client_layout(tmp_path: Path) -> None:
    generated = tmp_path / "src/generated"
    api = generated / "apis/DefaultApi.ts"
    api.parent.mkdir(parents=True)
    api.write_text("export class DefaultApi {}", encoding="utf-8")

    contracts = GeneratedClientContracts.discover(generated)

    assert contracts.import_root == "src/generated"
    assert contracts.page_import_root == "../generated"


def test_rejects_generated_contracts_over_budget_without_partial_output(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "src/generated"
    source = generated / "src/apis/LargeApi.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const contract = '" + ("x" * 200) + "';", encoding="utf-8")
    contracts = GeneratedClientContracts.discover(generated)

    with pytest.raises(
        FrontendContractBudgetExceeded, match=r"\d+ > 100 characters"
    ):
        contracts.render(max_chars=100)


def test_compacts_generated_contracts_before_rejecting_budget(tmp_path: Path) -> None:
    generated = tmp_path / "src/generated"
    source = generated / "src/apis/OrdersApi.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "/** " + ("large documentation " * 40) + " */\n"
        "export class OrdersApi {\n"
        "  // generated operation\n"
        "  getOrder(): string { return 'ok'; }\n"
        "}\n",
        encoding="utf-8",
    )
    contracts = GeneratedClientContracts.discover(generated)

    rendered = contracts.render(max_chars=180)

    assert "generated operation" not in rendered
    assert "getOrder(): string" in rendered


def test_orchestrator_writes_frontend_below_generated_application(tmp_path: Path) -> None:
    from app.implementation.domain.models import JobSpec
    from app.implementation.generation.orchestrator import PrototypeOrchestrator

    openapi = tmp_path / "openapi.json"
    openapi.write_text(json.dumps(OPENAPI), encoding="utf-8")
    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="order-console",
        workspace_root=tmp_path,
        inputs={"openapi": openapi},
        required_inputs=[],
        base_package="com.example",
        allow_assumptions=True,
        verify_compile=False,
        output_root=tmp_path / "runs",
        puml2code_root=tmp_path,
        agent_mode="plan-only",
        agent_model="model",
        agent_base_url="http://localhost",
        agent_temperature=0.0,
        agent_top_p=1.0,
        agent_max_output_tokens=1000,
        agent_reasoning_budget=0,
    )
    application = tmp_path / "application"

    orchestrator = PrototypeOrchestrator(spec)
    commands: list[list[str]] = []

    def generated(_name: str, command: list[str], _cwd: Path):
        commands.append(command)
        if "-o" in command:
            target = application / "frontend/src/generated"
            target.mkdir(parents=True)
            (target / "index.ts").write_text(
                "export class DefaultApi {}", encoding="utf-8"
            )
        else:
            (_cwd / "package-lock.json").write_text("{}", encoding="utf-8")
        return None

    orchestrator._run_command = generated
    orchestrator._generate_frontend(application)

    assert (application / "frontend/src/App.tsx").is_file()
    assert (application / "frontend/src/generated/index.ts").is_file()
    assert (application / "frontend/package-lock.json").is_file()
    assert "typescript-fetch" in commands[0]
    # The committed lock template makes npm unnecessary on the happy path.
    assert len(commands) == 1
    lock = json.loads(
        (application / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    assert lock["name"] == "order-console"
    assert lock["packages"][""]["name"] == "order-console"
    assert lock["packages"][""]["dependencies"]["react"] == "18.3.1"
    assert orchestrator.manifest.tools["easydep-frontend-generator"]["generator"] == "typescript-fetch"


def test_backend_openapi_generation_uses_a_pinned_docker_image(tmp_path: Path) -> None:
    from app.implementation.domain.models import JobSpec
    from app.implementation.generation.orchestrator import (
        OPENAPI_GENERATOR_IMAGE,
        PrototypeOrchestrator,
    )

    openapi = tmp_path / "openapi.json"
    openapi.write_text(json.dumps(OPENAPI), encoding="utf-8")
    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="orders",
        workspace_root=tmp_path,
        inputs={"openapi": openapi},
        required_inputs=[],
        base_package="com.example",
        allow_assumptions=True,
        verify_compile=False,
        output_root=tmp_path / "runs",
        puml2code_root=tmp_path,
        agent_mode="plan-only",
        agent_model="model",
        agent_base_url="http://localhost",
        agent_temperature=0.0,
        agent_top_p=1.0,
        agent_max_output_tokens=1000,
        agent_reasoning_budget=0,
    )
    orchestrator = PrototypeOrchestrator(spec)
    commands: list[list[str]] = []

    def generated(name: str, command: list[str], cwd: Path) -> CommandEvidence:
        commands.append(command)
        return CommandEvidence(name, command, str(cwd), 0, 0, "", "")

    orchestrator._run_command = generated
    orchestrator._generate_openapi(tmp_path / "application")

    assert OPENAPI_GENERATOR_IMAGE == "openapitools/openapi-generator-cli:v7.24.0"
    assert OPENAPI_GENERATOR_IMAGE in commands[0]
    assert commands[0][commands[0].index("-v") + 1] == f"{tmp_path.resolve()}:/workspace"
    assert commands[0][commands[0].index("-w") + 1] == "/workspace"
    assert commands[0][commands[0].index("-i") + 1] == "/workspace/openapi.json"
    assert commands[0][commands[0].index("-o") + 1] == "/workspace/application"
    assert orchestrator.manifest.tools["openapi-generator"]["image"] == OPENAPI_GENERATOR_IMAGE


def test_frontend_agent_task_uses_only_system_design_and_generated_contracts(
    tmp_path: Path,
) -> None:
    from app.implementation.domain.models import JobSpec
    from app.implementation.workflows.coordinator import phase_for_task

    openapi = tmp_path / "openapi.json"
    bce = tmp_path / "class.puml"
    sequence = tmp_path / "sequence.puml"
    openapi.write_text(json.dumps(OPENAPI), encoding="utf-8")
    bce.write_text("class OrderScreen <<Boundary>> {}", encoding="utf-8")
    sequence.write_text("OrderScreen -> OrderController : getOrder()", encoding="utf-8")
    run = tmp_path / "run"
    generated = run / "application/frontend/src/generated/apis"
    generated.mkdir(parents=True)
    (generated / "DefaultApi.ts").write_text(
        "export class DefaultApi { getOrder(): Promise<void> { return Promise.resolve(); } }",
        encoding="utf-8",
    )
    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="orders",
        workspace_root=tmp_path,
        inputs={"bceClass": bce, "sequence": sequence, "openapi": openapi},
        required_inputs=[],
        base_package="com.example",
        allow_assumptions=True,
        verify_compile=False,
        output_root=tmp_path / "runs",
        puml2code_root=tmp_path,
        agent_mode="plan-only",
        agent_model="model",
        agent_base_url="http://localhost",
        agent_temperature=0.0,
        agent_top_p=1.0,
        agent_max_output_tokens=1000,
        agent_reasoning_budget=0,
    )

    task = generate_frontend_tasks(spec, run)[0]
    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    prompt = (run / task.prompt_file).read_text(encoding="utf-8")

    assert task.task_type == "frontend-implementation"
    assert phase_for_task(task.task_type) == "frontend"
    assert set(task.source_artifacts) == {
        "bceClass",
        "sequence",
        "openapi",
        "generatedClientContracts",
    }
    assert "requirements" not in context
    assert "OrderScreen" in prompt and "getOrder" in prompt
    assert "src/generated" in prompt
    assert context["generatedImportRoot"] == "src/generated"
    assert "../generated/apis" in prompt
    assert "application/frontend/src/pages/OrdersPage.tsx" in task.allowed_write_paths


def test_frontend_contract_rejects_direct_http_and_requires_generated_client(
    tmp_path: Path,
) -> None:
    source = tmp_path / "application/frontend/src/App.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export default function App(){ fetch('/orders'); return null; }", encoding="utf-8")

    violations = frontend_contract_violations(
        tmp_path, ["application/frontend/src/App.tsx"]
    )

    assert any("direct HTTP" in item for item in violations)
    assert any("OpenAPI Generator" in item for item in violations)


def test_frontend_contract_requires_accessible_success_and_responsive_table(
    tmp_path: Path,
) -> None:
    source = tmp_path / "application/frontend/src/App.tsx"
    styles = tmp_path / "application/frontend/src/styles.css"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import { OrdersApi } from './generated/src';"
        "export default function App(){return <form aria-describedby=\"form-error\">"
        "<table><tbody /></table></form>;}",
        encoding="utf-8",
    )
    styles.write_text(
        "table { width: 100%; } @media (max-width: 40rem) { table { width: 90%; } }",
        encoding="utf-8",
    )

    violations = frontend_contract_violations(
        tmp_path,
        [
            "application/frontend/src/App.tsx",
            "application/frontend/src/styles.css",
        ],
        requires_success_feedback=True,
    )

    assert any("success status" in item for item in violations)
    assert any("missing element id: form-error" in item for item in violations)
    assert any("responsive narrow-screen" in item for item in violations)


def test_frontend_verification_runs_install_then_production_build(
    monkeypatch, tmp_path: Path
) -> None:
    frontend = tmp_path / "application/frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    (frontend / "src").mkdir()
    (frontend / "src/main.tsx").write_text(
        "import { HashRouter } from 'react-router-dom'; const app=<HashRouter />;",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def completed(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(
        "app.implementation.agents.verification.build.subprocess.run", completed
    )

    result = verify_frontend_workspace(tmp_path)

    assert result["exitCode"] == 0
    assert commands[0][1] == "ci"
    assert commands[1][1:] == ["run", "build"]


def test_frontend_contract_checks_jsx_expression_aria_references(
    tmp_path: Path,
) -> None:
    source = tmp_path / "application/frontend/src/App.tsx"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import { OrdersApi } from './generated/src';"
        "export default function App(){const error=true;return "
        "<form aria-describedby={error ? 'form-error' : undefined} />;}",
        encoding="utf-8",
    )

    violations = frontend_contract_violations(
        tmp_path, ["application/frontend/src/App.tsx"]
    )

    assert any("missing element id: form-error" in item for item in violations)


def test_frontend_verification_requires_dependency_lock(tmp_path: Path) -> None:
    frontend = tmp_path / "application/frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="package-lock.json"):
        verify_frontend_workspace(tmp_path)


def test_frontend_verification_requires_static_hosting_router(tmp_path: Path) -> None:
    frontend = tmp_path / "application/frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    (frontend / "src/main.tsx").write_text(
        "import { BrowserRouter } from 'react-router-dom';", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="HashRouter"):
        verify_frontend_workspace(tmp_path)


def test_frontend_snapshot_ignores_build_metadata(tmp_path: Path) -> None:
    frontend = tmp_path / "application/frontend"
    frontend.mkdir(parents=True)
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    (frontend / "tsconfig.tsbuildinfo").write_text("metadata", encoding="utf-8")
    (frontend / "src.tsx").write_text("export {};", encoding="utf-8")

    snapshot = snapshot_files(tmp_path)

    assert "application/frontend/src.tsx" in snapshot
    assert "application/frontend/package-lock.json" not in snapshot
    assert "application/frontend/tsconfig.tsbuildinfo" not in snapshot


def test_completed_job_persists_frontend_as_its_own_file_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    frontend = run / "application/frontend/src"
    backend = run / "application/src/main/java/com/example"
    frontend.mkdir(parents=True)
    backend.mkdir(parents=True)
    (frontend / "App.tsx").write_text("export default function App() {}", encoding="utf-8")
    (backend / "Application.java").write_text("class Application {}", encoding="utf-8")
    persisted: dict[str, dict[str, str]] = {}

    def save_snapshot(
        _app_id: str, artifact_type: str, files: dict[str, str], **_kwargs: object
    ) -> int:
        persisted[artifact_type] = files
        return 1

    monkeypatch.setattr(
        "app.implementation.application.jobs.artifact_repository.save_file_snapshot",
        save_snapshot,
    )
    settings = ImplementationSettings(
        repository_root=tmp_path,
        work_root=tmp_path / "work",
        python_executable=Path(__file__),
        max_workers=1,
        model="model",
        base_url="http://localhost",
        command_timeout_seconds=60,
    )
    worker = ImplementationWorker(settings)
    try:
        record = {
            "run_root": str(run),
            "app_id": "app-1",
            "job_id": "job-1",
        }
        worker._persist_outputs(record)
    finally:
        worker.shutdown()

    assert persisted[TYPE_FRONTEND_SOURCE_CODE] == {
        "src/App.tsx": "export default function App() {}"
    }


def test_cli_run_to_completion_reuses_one_scoped_approval(
    monkeypatch, tmp_path: Path
) -> None:
    from app.implementation.domain.models import JobSpec
    from app.implementation.workflows.coordinator import run_workflow_to_completion

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "input_hash": "input-hash",
                "implementation_tasks": [
                    {"task_id": "backend"},
                    {"task_id": "implement-frontend-application"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (reports / "external-transmission-request.json").write_text(
        json.dumps({"requestId": "a" * 64}), encoding="utf-8"
    )
    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="test",
        workspace_root=tmp_path,
        inputs={},
        required_inputs=[],
        base_package="com.example",
        allow_assumptions=True,
        verify_compile=False,
        output_root=tmp_path,
        puml2code_root=tmp_path,
        agent_mode="plan-only",
        agent_model="model",
        agent_base_url="url",
        agent_temperature=0,
        agent_top_p=1,
        agent_max_output_tokens=1,
        agent_reasoning_budget=0,
    )
    approvals: list[dict[str, object]] = []
    states = iter([{"status": "READY"}, {"status": "COMPLETE"}])
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: {"status": "READY"},
    )

    def run(_root: Path, _spec: JobSpec, approval: Path, **_kwargs: object):
        approvals.append(json.loads(approval.read_text(encoding="utf-8")))
        return next(states)

    monkeypatch.setattr("app.implementation.workflows.coordinator.run_workflow", run)

    result = run_workflow_to_completion(
        tmp_path, spec, approved_by="CLI user"
    )

    assert result["status"] == "COMPLETE"
    assert len(approvals) == 2
    assert approvals[0] == approvals[1]
    assert approvals[0]["delegatedRepairApprovals"] is True
    assert approvals[0]["delegationScope"]["initialTaskIds"] == [
        "backend",
        "implement-frontend-application",
    ]
