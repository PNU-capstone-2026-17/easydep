from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.db.models import TYPE_FRONTEND_SOURCE_CODE
from app.implementation.agents.verification.build import verify_frontend_workspace
from app.implementation.agents.verification.frontend import (
    frontend_contract_violations,
    repair_frontend_accessibility_contract,
    repair_responsive_table_styles,
    reuse_frontend_build,
    run_frontend_verification,
    store_frontend_build,
)
from app.implementation.agents.workspace import snapshot_files
from app.implementation.application.jobs import ImplementationWorker
from app.implementation.config import ImplementationSettings
from app.implementation.domain.models import CommandEvidence
from app.implementation.generation.frontend import (
    repair_typescript_fetch_export_collisions,
)
from app.implementation.generation.frontend_scaffold import (
    FrontendScaffoldError,
    openapi_typescript_fetch_command,
    react_scaffold_files,
    resolve_api_base_url,
    validate_openapi,
)
from app.implementation.planning.design_context import generate_frontend_tasks
from app.implementation.planning.frontend_contracts import (
    FrontendContractBudgetExceeded,
    GeneratedClientContracts,
)

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


def test_repairs_typescript_fetch_inline_model_export_collisions(tmp_path: Path) -> None:
    generated = tmp_path / "src/generated"
    models = generated / "src/models"
    apis = generated / "src/apis"
    models.mkdir(parents=True)
    apis.mkdir(parents=True)
    (models / "DeleteCourseRequest.ts").write_text(
        "export interface DeleteCourseRequest { code: string; }\n",
        encoding="utf-8",
    )
    api = apis / "DefaultApi.ts"
    api.write_text(
        "export interface DeleteCourseRequest { code: string; }\n"
        "export function deleteCourse(request: DeleteCourseRequest) { return request; }\n",
        encoding="utf-8",
    )

    repaired = repair_typescript_fetch_export_collisions(generated)

    assert repaired == ["DefaultApi.ts: DeleteCourseRequest -> DeleteCourseRequestParams"]
    source = api.read_text(encoding="utf-8")
    assert "export interface DeleteCourseRequestParams" in source
    assert "request: DeleteCourseRequestParams" in source


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


def test_compact_contract_keeps_public_api_and_omits_generated_bodies(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "src/generated"
    api = generated / "src/apis/OrdersApi.ts"
    model = generated / "src/models/Order.ts"
    runtime = generated / "src/runtime.ts"
    api.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    api.write_text(
        "export interface OrdersApiInterface { getOrder(): Promise<Order>; }\n"
        "export class OrdersApi implements OrdersApiInterface {\n"
        "  async getOrder(): Promise<Order> { "
        + ("const internal = 'request';\n" * 100)
        + "return {} as Order; }\n}\n",
        encoding="utf-8",
    )
    model.write_text(
        "export interface Order { id: string; }\n"
        "export function OrderFromJSON(value: unknown): Order { "
        + ("const internal = value;\n" * 100)
        + "return internal as Order; }\n",
        encoding="utf-8",
    )
    runtime.write_text(
        "export interface ConfigurationParameters { basePath?: string; }\n"
        "export class Configuration { constructor(value: ConfigurationParameters = {}) {} }\n"
        "export class BaseAPI { "
        + ("request() {}\n" * 100)
        + "}\n",
        encoding="utf-8",
    )

    rendered = GeneratedClientContracts.discover(generated).render(max_chars=700)

    assert "getOrder(): Promise<Order>" in rendered
    assert "export interface Order { id: string; }" in rendered
    assert "export class Configuration" in rendered
    assert "const internal" not in rendered
    assert "export class BaseAPI" not in rendered


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
        agent_mode="plan-only",
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
            target = application / "frontend/src/generated/src"
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
    assert (application / "frontend/src/generated/index.ts").read_text(encoding="utf-8") == "export * from './src';\n"
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
        agent_mode="plan-only",
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
    assert orchestrator.manifest.tools["openapi-generator"] == {
        "kind": "docker-image",
        "version": "7.24.0",
    }


def test_frontend_agent_task_uses_only_system_design_and_generated_contracts(
    tmp_path: Path,
) -> None:
    from app.implementation.domain.models import JobSpec
    from app.implementation.workflows.coordinator import phase_for_task

    openapi = tmp_path / "openapi.json"
    bce = tmp_path / "class.puml"
    bce_model = tmp_path / "class-model.json"
    sequence = tmp_path / "sequence.puml"
    sequence_model = tmp_path / "sequence-model.json"
    openapi.write_text(json.dumps(OPENAPI), encoding="utf-8")
    bce.write_text("class OrderScreen <<Boundary>> {}", encoding="utf-8")
    bce_model.write_text(
        json.dumps(
            {
                "Classes": [
                    {
                        "className": "OrderScreen",
                        "stereotype": "Boundary",
                        "fields": [],
                        "operations": [],
                    }
                ],
                "DataTypes": [],
                "Relationships": [],
            }
        ),
        encoding="utf-8",
    )
    sequence.write_text("OrderScreen -> OrderController : getOrder()", encoding="utf-8")
    sequence_model.write_text(json.dumps({"Diagrams": [{
        "use_case_id": "UC_ORDER", "use_case_name": "Get order",
        "Participants": [
            {"alias": "screen", "kind": "boundary", "source_class": "OrderScreen"},
            {"alias": "control", "kind": "control", "source_class": "OrderController"},
        ],
        "Messages": [
            {"source": "screen", "target": "control", "type": "sync",
             "use_case_ids": ["UC_ORDER"], "call_id": "get-order::call:1",
             "arguments": [{"parameter": "orderId", "source_kind": "input", "source_ref": "UC_ORDER:main:1#orderId"}],
             "fragments": [{"id": "get-order:main", "type": "opt", "branch": "main", "condition": "requested"}]},
            {"source": "control", "target": "screen", "type": "return",
             "use_case_ids": ["UC_ORDER"], "reply_to": "get-order::call:1", "arguments": [], "fragments": []},
        ],
    }]}), encoding="utf-8")
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
        inputs={
            "bceClass": bce,
            "bceModel": bce_model,
            "sequence": sequence,
            "sequenceModel": sequence_model,
            "openapi": openapi,
        },
        required_inputs=[],
        base_package="com.example",
        allow_assumptions=True,
        verify_compile=False,
        output_root=tmp_path / "runs",
        agent_mode="plan-only",
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
        "bceModel",
        "sequenceModel",
        "openapi",
        "generatedClientContracts",
    }
    assert "requirements" not in context
    diagram = next(
        item for item in context["sequence"]
        if item.get("use_case_id") == "UC_ORDER"
    )
    messages = diagram["Messages"]
    assert any(message.get("arguments") for message in messages)
    assert any(message.get("reply_to") for message in messages)
    assert any(message.get("fragments") for message in messages)
    assert "deployment" not in context
    assert "OrderScreen" in prompt and "getOrder" in prompt
    assert "src/generated" in prompt
    assert context["generatedImportRoot"] == "src/generated"
    assert "../generated/apis" in prompt
    assert "TODO" in prompt and "PLACEHOLDER" in prompt
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


def test_frontend_responsive_table_repair_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "application/frontend/src/App.tsx"
    styles = tmp_path / "application/frontend/src/styles.css"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import { OrdersApi } from './generated/src'; export default function App(){return <table />;}",
        encoding="utf-8",
    )
    styles.write_text("body { margin: 0; }\n", encoding="utf-8")
    paths = [
        "application/frontend/src/App.tsx",
        "application/frontend/src/styles.css",
    ]

    assert repair_responsive_table_styles(tmp_path, paths) == [paths[1]]
    first = styles.read_text(encoding="utf-8")
    assert "overflow-x: auto" in first
    assert repair_responsive_table_styles(tmp_path, paths) == []
    assert styles.read_text(encoding="utf-8") == first


def test_frontend_accessibility_repair_removes_stale_markers_and_aria_references(
    tmp_path: Path,
) -> None:
    source = tmp_path / "application/frontend/src/pages/OverviewPage.tsx"
    source.parent.mkdir(parents=True)
    source.write_text(
        "// TODO: generated placeholder\n"
        "export default function App(){return <section aria-describedby=\"searchError\">ok</section>;}",
        encoding="utf-8",
    )

    changed = repair_frontend_accessibility_contract(
        tmp_path, ["application/frontend/src/pages/OverviewPage.tsx"]
    )

    assert changed == ["application/frontend/src/pages/OverviewPage.tsx"]
    repaired = source.read_text(encoding="utf-8")
    assert "TODO" not in repaired and "PLACEHOLDER" not in repaired
    assert "aria-describedby" not in repaired


def test_frontend_contract_does_not_treat_input_placeholder_as_marker(
    tmp_path: Path,
) -> None:
    source = tmp_path / "application/frontend/src/App.tsx"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import { OrdersApi } from './generated/src';"
        "export default function App(){return <input placeholder=\"Search courses\" />;}",
        encoding="utf-8",
    )

    violations = frontend_contract_violations(
        tmp_path, ["application/frontend/src/App.tsx"]
    )

    assert not any("implementation marker" in item for item in violations)


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
        "app.implementation.agents.verification.build.run_frontend_command", completed
    )

    result = verify_frontend_workspace(tmp_path)

    assert result["exitCode"] == 0
    assert commands[0][1] == "ci"
    assert "--prefer-offline" in commands[0]
    assert commands[1][1:] == ["run", "build"]


def test_frontend_repair_reuses_installed_dependencies(tmp_path: Path) -> None:
    """같은 sandbox에 설치 결과가 있으면 수리 build에서 npm ci를 반복하지 않는다."""
    frontend = tmp_path / "application/frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "node_modules").mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    (frontend / "node_modules/.package-lock.json").write_text("{}", encoding="utf-8")
    (frontend / "src/main.tsx").write_text(
        "import { HashRouter } from 'react-router-dom'; const app=<HashRouter />;",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def completed(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    result = run_frontend_verification(tmp_path, completed)

    assert result["exitCode"] == 0
    assert [command[1:] for command in commands] == [["run", "build"]]


def test_verified_frontend_bundle_is_reused_only_for_the_same_source(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    sandbox = tmp_path / "sandbox"
    for root in (run, sandbox):
        frontend = root / "application/frontend"
        (frontend / "src").mkdir(parents=True)
        (frontend / "package.json").write_text("{}", encoding="utf-8")
        (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
        (frontend / "src/App.tsx").write_text("export const App = 1;", encoding="utf-8")
    dist = sandbox / "application/frontend/dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>ready</main>", encoding="utf-8")

    stored = store_frontend_build(run, sandbox, {"exitCode": 0, "command": ["npm"]})

    assert stored is not None
    assert (run / "application/frontend/dist/index.html").is_file()
    assert reuse_frontend_build(run) == {
        "exitCode": 0,
        "command": ["npm"],
        "reusedFromFrontendTask": True,
    }
    (run / "application/frontend/src/App.tsx").write_text(
        "export const App = 2;", encoding="utf-8"
    )
    assert reuse_frontend_build(run) is None


def test_frontend_verification_keeps_timeout_diagnostics(tmp_path: Path) -> None:
    frontend = tmp_path / "application/frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    (frontend / "src").mkdir()
    (frontend / "src/main.tsx").write_text(
        "import { HashRouter } from 'react-router-dom'; const app=<HashRouter />;",
        encoding="utf-8",
    )

    def timeout(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(
            command, 300, output=b"fetching packages", stderr=b"registry stalled"
        )

    result = run_frontend_verification(tmp_path, timeout, timeout_seconds=300)

    assert result["exitCode"] == 1
    assert result["command"][1] == "ci"
    assert "Frontend command timed out after 300 seconds" in result["stderr"]
    assert "registry stalled" in result["stderr"]
    assert "fetching packages" in result["stdout"]


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

    with pytest.raises(RuntimeError, match=r"package-lock\.json"):
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
        json.dumps({"requestId": "a" * 64, "status": "AWAITING_APPROVAL"}),
        encoding="utf-8",
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
        agent_mode="plan-only",
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
