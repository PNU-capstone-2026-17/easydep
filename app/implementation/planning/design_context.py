from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import settings

from ..domain.implementation_ir import (
    build_implementation_ir,
)
from ..domain.models import JobSpec
from ..generation.frontend_scaffold import frontend_page_names, operation_ids
from .frontend_contracts import GeneratedClientContracts


@dataclass(frozen=True)
class DesignClass:
    name: str
    stereotype: str
    block: str
    body: str


@dataclass(frozen=True)
class ImplementationTask:
    task_id: str
    control: str
    prompt_file: str
    context_file: str
    allowed_write_paths: list[str]
    immutable_paths: list[str]
    source_artifacts: dict[str, str]
    prompt_sha256: str
    llm: dict[str, object]
    task_type: str = "control"
    # ``allowed_write_paths`` is the complete editable scope.  A work unit can
    # therefore fix a related source file instead of handing the error to a
    # file owner.  ``required_output_paths`` keeps the smaller deterministic
    # completion contract used to decide whether the first implementation
    # request produced every required artifact.
    required_output_paths: list[str] | None = None

    def __post_init__(self) -> None:
        if self.required_output_paths is None:
            object.__setattr__(self, "required_output_paths", list(self.allowed_write_paths))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


CLASS_PATTERN = re.compile(
    r"(?ms)^\s*class\s+(?P<name>[A-Za-z_]\w*)\s+"
    r"<<(?P<stereotype>[^>]+)>>\s*\{(?P<body>.*?)\}"
)
RELATION_PATTERN = re.compile(
    r"(?m)^\s*(?P<left>[A-Za-z_]\w*)\s+[^\n:]+?\s+"
    r"(?P<right>[A-Za-z_]\w*)\s*(?::[^\n]*)?$"
)
STOP_WORDS = {
    "manager", "controller", "service", "get", "set", "is", "on", "log",
    "string", "boolean", "int", "float", "void", "message", "record",
}
def generate_persistence_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Plan one shared OpenHands work unit for the ERD persistence slice."""
    erd = _read(spec.inputs.get("erd"))
    if not erd:
        raise ValueError("ERD input is required to plan persistence tasks")
    ir = build_implementation_ir(spec, run_root)
    entity_names = list(ir.persistent_entities)
    if not entity_names:
        raise ValueError("ERD contains no BCE Entity aliases to persist")
    contracts = read_generated_java_contracts(
        run_root, spec.base_package, set(entity_names)
    )
    package_path = spec.base_package.replace(".", "/")
    repository_files = [
        f"application/src/main/java/{package_path}/persistence/repository/{name}Repository.java"
        for name in entity_names
    ]
    entity_files = [
        f"application/src/main/java/{package_path}/persistence/entity/{name}Entity.java"
        for name in entity_names
    ]
    required = [
        *entity_files,
        *repository_files,
        f"application/src/main/java/{package_path}/persistence/mapper/BcePersistenceMapper.java",
        "application/src/main/resources/db/migration/V1__initial_schema.sql",
        f"application/src/test/java/{package_path}/persistence/PersistenceContractTest.java",
    ]
    # A persistence error generally spans Entity, repository, mapper, and
    # migration.  These package scopes let one agent fix that vertical slice;
    # generated BCE/OpenAPI contracts remain outside the editable scope.
    editable_directories = [
        f"application/src/main/java/{package_path}/persistence",
        "application/src/main/resources/db/migration",
        f"application/src/test/java/{package_path}/persistence",
    ]
    editable = _work_unit_editable_paths(run_root, required, editable_directories)
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    task_id = "implement-shared-persistence"
    context = {
        "schemaVersion": "implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "persistence",
        "implementationIR": ir.to_dict(),
        "erd": erd,
        "bceEntities": entity_names,
        "generatedJavaContracts": contracts,
        "requiredOutputs": required,
    }
    context_path = output / "shared-persistence.context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (
        f"# Shared persistence implementation: {spec.name}\n\n"
        "Implement the ERD-backed persistence slice as one coherent unit. You may update "
        "related Entity, repository, mapper, migration, and focused test files when that "
        "resolves the same schema or compile failure. Generated BCE and OpenAPI sources are "
        "read-only.\n\n"
        "Rules:\n"
        "- Derive tables, columns, keys, nullability, and JPA relationships from the ERD.\n"
        "- Keep the Entity, Spring Data repository, mapper, migration, and tests consistent; "
        "do not hand a technical error to another file owner.\n"
        "- Preserve the exact generated contracts below and do not invent public API members.\n"
        "- Run the focused persistence compile/test command supplied by the runtime.\n"
        "- Do not leave TODO, FIXME, placeholder implementations, or speculative fallbacks.\n\n"
        "## ERD\n```plantuml\n"
        + erd
        + "\n```\n\n## Generated contracts\n```java\n"
        + contracts
        + "\n```\n\n## Editable directories\n"
        + "\n".join(f"- `{path}`" for path in editable_directories)
    ) + render_allowed_output_rules(required)
    prompt_path = output / "shared-persistence.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id=task_id,
        control="shared persistence",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=editable,
        required_output_paths=required,
        immutable_paths=[
            f"application/src/main/java/{package_path}/bce",
            f"application/src/main/java/{package_path}/api",
        ],
        source_artifacts={
            name: str(path) for name, path in spec.inputs.items()
            if name in {"bceClass", "erd", "erdBceModel"} and path.is_file()
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="persistence",
    )
    (output / "shared-persistence.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task]


def generate_api_adapter_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Plan the related backend use cases as one editable work unit.

    The public wrapper keeps its established import path while replacing the
    former one-controller-per-task plan.  Controls, inbound adapters, and
    outbound adapters now share a repair boundary; persistence remains a
    separate prior work unit.
    """
    package_path = spec.base_package.replace(".", "/")
    java_root = run_root / "application" / "src" / "main" / "java" / package_path
    ir = build_implementation_ir(spec, run_root)
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    required = [
        *[
            f"application/src/main/java/{package_path}/application/impl/{name}Service.java"
            for name in ir.controls
        ],
        *[
            f"application/src/main/java/{package_path}/adapter/in/web/{port.name}ApiController.java"
            for port in ir.api_ports
        ],
        *[
            f"application/src/main/java/{package_path}/adapter/in/boundary/{name}Adapter.java"
            for name in ir.boundaries
        ],
        *[
            (
                f"application/src/main/java/{package_path}/adapter/out/"
                f"{'persistence' if gateway.kind == 'persistence' else 'gateway'}/"
                f"{gateway.name if gateway.kind == 'persistence' else 'InMemory' + gateway.name}Adapter.java"
            )
            for gateway in ir.gateways
        ],
        f"application/src/test/java/{package_path}/application/impl/ApplicationUseCasesTest.java",
    ]
    editable_directories = [
        f"application/src/main/java/{package_path}/application/impl",
        f"application/src/main/java/{package_path}/adapter/in",
        f"application/src/main/java/{package_path}/adapter/out",
        f"application/src/test/java/{package_path}/application/impl",
        f"application/src/test/java/{package_path}/adapter",
    ]
    entity_sources = [
        f"application/src/main/java/{package_path}/bce/{name}.java"
        for name in ir.entities
    ]
    # Entity scaffolds preserve fields and operation signatures but deliberately
    # emit empty/default method bodies.  The former per-Entity task completed
    # those bodies; keep that implementation path inside this related backend
    # work unit without turning the generated public contract into a required
    # output again.
    editable = _work_unit_editable_paths(
        run_root, required, [*editable_directories, *entity_sources]
    )
    immutable_bce_paths = [
        path.relative_to(run_root).as_posix()
        for path in sorted((java_root / "bce").rglob("*.java"))
        if path.relative_to(run_root).as_posix() not in entity_sources
    ]
    generated_sources = [
        *sorted((java_root / "bce").rglob("*.java")),
        *sorted((java_root / "api").rglob("*.java")),
    ]
    contracts = render_source_contracts(run_root, generated_sources)
    task_id = "implement-application-use-cases"
    context = {
        "schemaVersion": "implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "use-case",
        "implementationIR": ir.to_dict(),
        "sequence": _read_json(spec.inputs.get("sequenceModel")),
        "erd": _read(spec.inputs.get("erd")),
        "openapi": _read(spec.inputs.get("openapi")),
        "generatedJavaContracts": contracts,
        "entityBodySources": entity_sources,
        "requiredOutputs": required,
    }
    deployment_context = _deployment_context(
        spec, {*(item.name for item in ir.components), *ir.controls}
    )
    if deployment_context:
        context["deployment"] = deployment_context
    context_path = output / "application-use-cases.context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (
        f"# Application use-case implementation: {spec.name}\n\n"
        "Implement the related Controls, web/boundary adapters, outbound adapters, and "
        "focused tests as one backend work unit. When an observed compile or HTTP failure "
        "crosses these directories, repair the related source in this same task rather than "
        "creating a file-owner handoff. Persistence entities, repositories, and generated "
        "OpenAPI contracts are read-only. BCE Entity sources below may have only their "
        "method bodies completed; preserve their generated public class, field, and method "
        "signatures exactly.\n\n"
        "Rules:\n"
        "- Implement only behavior established by the BCE, typed sequence, and OpenAPI contracts.\n"
        "- Use the ERD-derived repositories exposed by the completed persistence unit; do not "
        "invent alternative ports or in-memory domain state for persistent behavior.\n"
        "- Keep adapter request/response mapping and Control invocation consistent with the "
        "exact generated contracts.\n"
        "- Use the single ApplicationUseCasesTest as a small representative behavior suite; "
        "do not create one mechanical test file per class. Assert observable results rather "
        "than prompt wording or private helper calls.\n"
        "- Do not leave TODO, FIXME, placeholder implementations, or speculative fallbacks.\n\n"
        "## BCE Entity body sources (signatures are immutable)\n"
        + "\n".join(f"- `{path}`" for path in entity_sources)
        + "\n\n"
        "## Implementation IR\n```json\n"
        + _prompt_json(ir.to_dict())
        + "\n```\n\n## Typed sequence\n```json\n"
        + _prompt_json(context["sequence"])
        + "\n```\n\n## OpenAPI\n```yaml\n"
        + str(context["openapi"])
        + "\n```\n\n## Generated Java contracts\n```java\n"
        + contracts
        + "\n```\n"
        + _render_deployment_context(deployment_context)
        + "\n## Editable directories\n"
        + "\n".join(f"- `{path}`" for path in editable_directories)
    ) + render_allowed_output_rules(required)
    prompt_path = output / "application-use-cases.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id=task_id,
        control="application use cases",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=editable,
        required_output_paths=required,
        immutable_paths=[
            *immutable_bce_paths,
            f"application/src/main/java/{package_path}/api",
            f"application/src/main/java/{package_path}/persistence",
        ],
        source_artifacts={
            name: str(path) for name, path in spec.inputs.items()
            if name in {"bceClass", "sequenceModel", "erd", "openapi", "deploymentBundle"}
            and path.is_file()
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="use-case",
    )
    (output / "application-use-cases.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task]


def generate_wiring_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Plan the Spring bootstrap, bean graph, runtime properties, and context gate."""
    package_path = spec.base_package.replace(".", "/")
    ir = build_implementation_ir(spec, run_root)
    package_root = (
        run_root / "application" / "src" / "main" / "java" / package_path
    )
    write_spring_boot_entrypoint(
        run_root, spec.base_package, ir.application_class
    )
    source_paths: list[Path] = []
    for relative in ("application/impl", "adapter", "bce", "persistence/repository"):
        source_paths.extend(sorted((package_root / relative).rglob("*.java")))
    contracts = render_source_contracts(run_root, source_paths)
    required = [
        f"application/src/main/java/{package_path}/config/ApplicationConfiguration.java",
        "application/src/main/resources/application.yml",
        f"application/src/test/java/{package_path}/config/ApplicationContextTest.java",
        (
            "application/src/test/java/"
            f"{package_path}/integration/{ir.application_class.removesuffix('Application')}FlowTest.java"
        ),
    ]
    editable_directories = [
        f"application/src/main/java/{package_path}/config",
        f"application/src/test/java/{package_path}/config",
        f"application/src/test/java/{package_path}/integration",
    ]
    allowed = _work_unit_editable_paths(
        run_root,
        required,
        [*editable_directories, "application/src/main/resources/application.yml"],
    )
    task_id = "implement-application-wiring"
    deployment_context = _deployment_context(
        spec, {spec.name, ir.application_class, *(item.name for item in ir.components)}
    )
    representative = ir.e2e_scenarios[0] if ir.e2e_scenarios else None
    context = {
        "schemaVersion": "implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "wiring",
        "implementationIR": ir.to_dict(),
        "generatedJavaContracts": contracts,
        "applicationClass": ir.application_class,
        "openapi": _read(spec.inputs.get("openapi")),
        "semanticContract": (
            {
                "method": representative.method,
                "path": representative.path,
                "status": representative.status,
            }
            if representative is not None
            else {}
        ),
        "requiredOutputs": required,
    }
    if deployment_context:
        context["deployment"] = deployment_context
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    context_path = output / "application-wiring.context.json"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    prompt = render_wiring_prompt(spec, ir.application_class, contracts)
    prompt += (
        "\n\nImplement the single real HTTP flow test in the contracted integration path. "
        "It must start the wired application and exercise a documented successful OpenAPI "
        "operation; do not modify production source outside this work unit to make the test pass.\n"
        "The required HTTP path/status contract is:\n```json\n"
        + _prompt_json(context["semanticContract"])
        + "\n```"
    )
    prompt += _render_deployment_context(deployment_context)
    prompt += "\n\n## Editable directories\n" + "\n".join(
        f"- `{path}`" for path in editable_directories
    )
    prompt += render_allowed_output_rules(required)
    prompt_path = output / "application-wiring.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id=task_id,
        control="Spring application wiring",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=allowed,
        required_output_paths=required,
        immutable_paths=[
            f"application/src/main/java/{package_path}/bce",
            f"application/src/main/java/{package_path}/api",
            f"application/src/main/java/{package_path}/application",
            f"application/src/main/java/{package_path}/adapter",
            f"application/src/main/java/{package_path}/persistence",
            "application/src/main/resources/db/migration",
        ],
        source_artifacts={
            name: str(path) for name, path in spec.inputs.items()
            if name in {
                "bceClass", "sequence", "erd", "openapi", "deployment", "deploymentBundle", "cloud",
            }
            and path.is_file()
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="wiring",
    )
    (output / "application-wiring.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task]


def generate_frontend_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Plan the UI implementation from system-design artifacts and generated API client."""
    frontend = run_root / "application" / "frontend"
    generated = frontend / "src" / "generated"
    if not generated.is_dir():
        raise ValueError("OpenAPI Generator frontend client was not found")
    openapi = json.loads(_read(spec.inputs.get("openapi")))
    bce = _read(spec.inputs.get("bceClass"))
    classes = parse_design_classes(bce)
    bce_names = {item.name for item in classes}
    boundary_names = {
        item.name for item in classes if item.stereotype.casefold() == "boundary"
    }
    sequence_context = _project_sequence(
        _read_json(spec.inputs.get("sequenceModel")), boundary_names
    )
    pages = frontend_page_names(openapi)
    operations = operation_ids(openapi)
    client_contracts = GeneratedClientContracts.discover(generated)
    generated_contracts = client_contracts.render()

    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    contracts_path = run_root / "reports" / "frontend-generated-client-contracts.txt"
    contracts_path.write_text(generated_contracts, encoding="utf-8")
    required = [
        "application/frontend/src/App.tsx",
        "application/frontend/src/components/AppShell.tsx",
        *[f"application/frontend/src/pages/{name}.tsx" for name in pages],
        "application/frontend/src/styles.css",
    ]
    editable_directories = [
        "application/frontend/src/components",
        "application/frontend/src/pages",
    ]
    allowed = _work_unit_editable_paths(
        run_root,
        required,
        [
            "application/frontend/src/App.tsx",
            "application/frontend/src/styles.css",
            *editable_directories,
        ],
    )
    task_id = "implement-frontend-application"
    context = {
        "schemaVersion": "frontend-implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "frontend-implementation",
        "classDiagram": bce,
        "sequence": sequence_context,
        "openapi": openapi,
        "pages": pages,
        "operationIds": operations,
        "generatedTypescriptContracts": generated_contracts,
        "generatedImportRoot": client_contracts.import_root,
        "requiredOutputs": required,
    }
    deployment_context = _deployment_context(spec, {"frontend", *bce_names})
    if deployment_context:
        context["deployment"] = deployment_context
    context_path = output / "frontend-application.context.json"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page_list = "\n".join(f"- `{name}`" for name in pages)
    operation_list = "\n".join(f"- `{name}`" for name in operations)
    page_import_root = client_contracts.page_import_root
    prompt = f"""# Frontend implementation task: {spec.name}

Implement the React application on top of the immutable TypeScript client produced by
OpenAPI Generator. The system-design artifacts below are authoritative; do not invent API
operations or behavior outside those contracts.

Rules:
- The discovered OpenAPI Generator import root is exactly `{client_contracts.import_root}`. From a page below
  `src/pages`, import APIs from `{page_import_root}/apis`, models from
  `{page_import_root}/models`, and `Configuration` from `{page_import_root}/runtime`.
- Use only exports that exist below `{client_contracts.import_root}`; import generated API classes, models, and
  `Configuration` instead of hand-writing HTTP calls.
- Never call `fetch`, axios, XMLHttpRequest, or hard-code an endpoint path in an application file.
- Use `API_BASE_URL` from `src/config.ts` when constructing generated client configuration.
- Derive screens and user actions from BCE Boundary responsibilities and the typed sequence flow.
- Create an accessible responsive UI with explicit loading, empty, success, validation, and
  API-error states. Mutating operations must announce success with `role="status"` or an
  `aria-live` region. Every `aria-describedby` token must reference an existing element ID,
  and data tables must remain usable on narrow screens (for example, an overflow container).
  Keep domain state inside React components; do not add dependencies.
- `src/main.tsx` already provides the immutable `HashRouter` required for static hosting.
  `App.tsx` owns route declarations without creating another router, `AppShell.tsx` owns shared
  navigation/layout, and every contracted page must be reachable from the application.
- Preserve all generated client/model files and project configuration exactly.
- Create every contracted output and finish immediately. `npm run build` is the acceptance gate.
- Production source must contain no `TODO`, `FIXME`, or `PLACEHOLDER` markers, including in
  comments or strings. Do not leave demo-only identities, empty handlers, or speculative
  fallback branches described as placeholders; implement the contracted behavior or show a
  real loading, empty, validation, or API-error state instead.

## Contracted pages
{page_list}

## OpenAPI operations that the UI must expose where meaningful
{operation_list}

## BCE class design
```plantuml
{bce}
```

## Typed sequence context
```json
{_prompt_json(sequence_context)}
```

## OpenAPI contract
```json
{json.dumps(openapi, ensure_ascii=False, indent=2)}
```

## Exact OpenAPI Generator TypeScript contracts
```typescript
{generated_contracts}
```
{_render_deployment_context(deployment_context)}
"""
    prompt += "\n## Editable paths\n" + "\n".join(
        f"- `{path}`" for path in [
            "application/frontend/src/App.tsx",
            "application/frontend/src/styles.css",
            *editable_directories,
        ]
    )
    prompt += render_allowed_output_rules(required)
    prompt_path = output / "frontend-application.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id=task_id,
        control=f"{spec.name} frontend application",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=allowed,
        required_output_paths=required,
        immutable_paths=[
            "application/frontend/src/generated",
            "application/frontend/package.json",
            "application/frontend/tsconfig.json",
            "application/frontend/vite.config.ts",
            "application/frontend/src/config.ts",
            "application/frontend/src/main.tsx",
        ],
        source_artifacts={
            **{
                name: str(path)
                for name, path in spec.inputs.items()
                if name in {"bceClass", "sequenceModel", "openapi", "deploymentBundle"}
                and path.is_file()
            },
            "generatedClientContracts": str(contracts_path),
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="frontend-implementation",
    )
    (output / "frontend-application.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task]


def render_source_contracts(run_root: Path, paths: list[Path]) -> str:
    sections: list[str] = []
    for path in paths:
        if path.is_file():
            sections.append(
                f"// {path.relative_to(run_root).as_posix()}\n"
                + path.read_text(encoding="utf-8").strip()
            )
    return "\n\n".join(sections) or "// No Java contracts found"


def _render_deployment_context(deployment: object) -> str:
    """배포 정보는 구현 계약을 흐리지 않도록 있을 때만 짧은 JSON으로 붙인다."""
    return "" if not deployment else f"""
## Relevant runtime context
```json
{_prompt_json(deployment)}
```
"""


def _prompt_json(value: object) -> str:
    """구조는 그대로 두고 LLM 입력에 불필요한 화면용 들여쓰기만 없앤다."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_wiring_prompt(
    spec: JobSpec, application_class: str, contracts: str
) -> str:
    return f"""# Implementation task: Spring application wiring

The system has already created the complete `{application_class}` Spring Boot entry point.
Create the explicit production bean configuration, local runtime properties, and a context-load
integration test for that generated application.

Rules:
- Do not create or edit `{application_class}`; it is a deterministic framework bootstrap.
- Put explicit `@Bean` factory methods in `{spec.base_package}.config.ApplicationConfiguration` for every BCE Control service, stateful BCE Entity required directly by a service, concrete Boundary adapter, and concrete outbound Gateway adapter needed by the application graph.
- Use only the exact public constructors below. Do not add annotations to or edit generated/agent-produced classes.
- Generated Spring Data repositories are discovered by Spring Boot; do not instantiate repository proxies manually.
- Every generated persistence mapper under `{spec.base_package}.persistence.mapper` has no Spring stereotype. Declare one explicit `@Bean` for each mapper using its no-argument constructor.
- Do not add `@EnableJpaRepositories` exclusions, repository scan filters, or any workaround that removes a generated repository bean. If a repository contract is invalid, allow the context test to fail so the upstream repository task can be repaired.
- Existing `@RestController` API adapters are component-scanned; do not declare duplicate controller beans.
- Existing adapters annotated with `@Component`, `@Service`, `@Repository`, or `@RestController` are component-scanned. Do not also create an `@Bean` for any of those adapter classes; inject their port interface into the dependent Control instead. Each port must have exactly one candidate bean unless an explicit qualifier is part of the generated contract.
- Detect constructor cycles where a Boundary adapter delegates to a Control that itself consumes the Boundary. Break only that Control parameter with Spring `@Lazy`; never enable global circular references and never use field injection or `ApplicationContext.getBean`.
- It is acceptable to expose standalone UI adapters as beans even when no service currently consumes them.
- `ApplicationConfiguration` and every production file under `src/main/java` must use only real application beans. Never import, call, or create Mockito/JUnit mocks, spies, or test configuration there; test doubles belong only under `src/test/java`.
- Configure an H2 in-memory datasource and Flyway migration in `application.yml`. Do not store secrets and do not invent deployment/cloud settings that were not provided.
- The context test must use `@SpringBootTest`, assert that the application context loads, and
  dynamically cover every generated Control service, API controller, outbound Gateway adapter,
  and every generated Spring Data repository bean shown below. Do not select one domain-specific "main Control" and
  do not mock beans or disable Flyway/JPA.
- Do not leave TODO/FIXME markers, placeholder wording in production comments, duplicate bean definitions, or speculative configuration.
- Create every contracted output, then finish immediately.

## Exact existing Java contracts and constructors

```java
{contracts}
```
"""


def _llm_config(spec: JobSpec) -> dict[str, object]:
    return {
        "provider": "nvidia-nim",
        "model": spec.agent_model,
        "baseUrl": spec.agent_base_url,
        "temperature": spec.agent_temperature,
        "topP": spec.agent_top_p,
        "maxOutputTokens": spec.agent_max_output_tokens,
        "reasoningBudget": spec.agent_reasoning_budget,
        "reasoningEffort": settings.implementation_reasoning_effort,
        "repairReasoningEffort": settings.implementation_repair_reasoning_effort,
        "chatTemplateKwargs": {
            "enable_thinking": True,
            "force_nonempty_content": True,
        },
    }


def write_spring_boot_entrypoint(
    run_root: Path, base_package: str, application_class: str
) -> Path:
    """Write the framework bootstrap that has no domain-level implementation choice.

    The application name and Java package are fixed by the implementation job.
    A plain ``@SpringBootApplication`` entry point is therefore a complete,
    deterministic artifact; delegating it to the wiring conversation only adds
    token use and lets an agent accidentally alter repository discovery.
    """
    target = (
        run_root
        / "application"
        / "src"
        / "main"
        / "java"
        / Path(*base_package.split("."))
        / f"{application_class}.java"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"""package {base_package};

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class {application_class} {{
    public static void main(String[] args) {{
        SpringApplication.run({application_class}.class, args);
    }}
}}
""",
        encoding="utf-8",
    )
    return target


def render_allowed_output_rules(allowed: list[str]) -> str:
    return "\n\n## Contracted outputs\n\n" + "\n".join(
        f"- `{path}`" for path in allowed
    ) + "\n"


def parse_design_classes(source: str) -> list[DesignClass]:
    return [
        DesignClass(match.group("name"), match.group("stereotype"), match.group(0).strip(), match.group("body").strip())
        for match in CLASS_PATTERN.finditer(source)
    ]


def parse_relations(source: str) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for line in source.splitlines():
        if not re.search(r"(?:-->|\.\.>|\*--|o--)", line):
            continue
        names = re.findall(r"[A-Za-z_]\w*", line.split(":", 1)[0])
        if len(names) >= 2:
            result.append((names[0], names[-1], line.strip()))
    return result


def slice_erd(source: str, entity_names: set[str]) -> str:
    if not entity_names:
        return "' No directly related ERD entity"
    blocks: list[str] = []
    for name in sorted(entity_names):
        pattern = re.compile(rf'(?ms)^entity\s+"{re.escape(name)}"\s+as\s+{re.escape(name)}\s*\{{.*?^\}}')
        match = pattern.search(source)
        if match:
            blocks.append(match.group(0))
    for line in source.splitlines():
        if any(re.search(rf"\b{re.escape(name)}\b", line) for name in entity_names) and re.search(r"(?:\.\.|--)", line):
            blocks.append(line.strip())
    return "\n\n".join(dict.fromkeys(blocks)) if blocks else "' No directly related ERD entity"


def _project_sequence(
    model: dict[str, object], names: set[str]
) -> list[dict[str, object]]:
    """구현 대상이 참여한 typed 시퀀스만, 원래 호출 메타데이터와 함께 남긴다.

    PlantUML alias를 다시 해석하면 ``arguments``와 ``call_id`` 같은 실행 계약이
    사라진다. 따라서 모든 구현 task가 이 작은 projection을 공유해 원본 메시지
    dict를 그대로 전달한다.
    """
    diagrams = model.get("Diagrams", [model])
    if not isinstance(diagrams, list):
        return []
    selected: list[dict[str, object]] = []
    for diagram in diagrams:
        if not isinstance(diagram, dict):
            continue
        participants = [
            item for item in diagram.get("Participants", []) if isinstance(item, dict)
        ]
        aliases = {
            str(item.get("alias") or item.get("name") or "")
            for item in participants
            if str(item.get("source_class") or item.get("name") or "") in names
            or str(item.get("alias") or "") in names
        }
        messages = [
            item for item in diagram.get("Messages", [])
            if isinstance(item, dict)
            and {str(item.get("source") or ""), str(item.get("target") or "")} & aliases
        ]
        if not messages:
            continue
        involved = {
            str(message.get(side) or "")
            for message in messages
            for side in ("source", "target")
        }
        selected.append({
            "use_case_id": diagram.get("use_case_id", ""),
            "use_case_name": diagram.get("use_case_name", ""),
            "Participants": [
                item for item in participants
                if str(item.get("alias") or item.get("name") or "") in involved
            ],
            # arguments, call_id, reply_to, step_ids, fragments를 복사하지 않고 보존한다.
            "Messages": messages,
        })
    return selected


def _deployment_context(spec: JobSpec, names: set[str]) -> dict[str, object]:
    """구현 대상과 연결된 generatedApplication 실행 조건만 작게 전달한다.

    배포 bundle 전체에는 CSP 계획처럼 코드 task가 소비하지 않는 정보가 많다.
    sourceRefs가 대상 이름을 가리키는 workload를 우선 고르고, 단일 앱인 경우만
    안전하게 fallback하여 interface/configuration/storage/connection 계약을 남긴다.
    """
    graph = _read_json(spec.inputs.get("deploymentBundle")).get("workloadGraph")
    if not isinstance(graph, dict):
        return {}
    generated = [
        item for item in graph.get("workloads", [])
        if isinstance(item, dict)
        and str((item.get("artifact") or {}).get("kind") or "") == "generatedApplication"
    ]
    matched = [
        item for item in generated
        if any(
            name.lower() in json.dumps(item.get("sourceRefs") or [], ensure_ascii=False).lower()
            for name in names
        )
    ]
    workloads = matched or (generated if len(generated) == 1 else [])
    if not workloads:
        return {}
    workload_ids = {str(item.get("id") or "") for item in workloads}
    return {
        "workloads": [
            {
                "id": item.get("id"),
                "interfaces": list(item.get("interfaces") or []),
                # 설정값 자체(특히 secret)는 코드 task가 소비하지 않는다.
                "configuration": [
                    {
                        key: config.get(key)
                        for key in (
                            "id", "name", "kind", "projection", "connectionRef", "sensitive",
                        )
                        if config.get(key) is not None
                    }
                    | (
                        {"value": config.get("value")}
                        if config.get("kind") == "value"
                        and config.get("sensitive") is not True
                        and config.get("value") is not None
                        else {}
                    )
                    for config in item.get("configuration", [])
                    if isinstance(config, dict)
                ],
                "storage": list(item.get("storage") or []),
            }
            for item in workloads
        ],
        "connections": [
            connection for connection in graph.get("connections", [])
            if isinstance(connection, dict)
            and {str(connection.get("sourceRef") or ""), str(connection.get("targetRef") or "")}
            & workload_ids
        ],
    }


def split_words(value: str) -> list[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.findall(r"[a-z]+", expanded.lower())


def _read(path: Path | None) -> str:
    return path.read_text(encoding="utf-8") if path and path.is_file() else ""


def _read_json(path: Path | None) -> dict[str, object]:
    """선택 입력이 없거나 JSON object가 아니면 빈 설계로 처리한다."""
    try:
        value = json.loads(_read(path))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _work_unit_editable_paths(
    run_root: Path,
    required: list[str],
    scopes: list[str],
) -> list[str]:
    """Expand a package scope into the concrete editable files in this run.

    The runtime's restricted editor receives file paths, while planning should
    express a repair boundary as a package or directory.  Keep both meanings:
    required outputs are included even before they exist and all existing files
    below a scope are editable.  Generated contracts are never supplied here.
    """
    paths = {path.replace("\\", "/") for path in required}
    for scope in scopes:
        root = run_root / scope
        if root.is_file():
            paths.add(root.relative_to(run_root).as_posix())
        elif root.is_dir():
            paths.update(
                path.relative_to(run_root).as_posix()
                for path in root.rglob("*")
                if path.is_file()
            )
    return sorted(paths)


def _relative(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def read_generated_java_contracts(
    run_root: Path,
    base_package: str,
    names: set[str],
    api_model_names: set[str] | None = None,
    repository_names: set[str] | None = None,
) -> str:
    package_root = (
        run_root
        / "application"
        / "src"
        / "main"
        / "java"
        / Path(base_package.replace(".", "/"))
    )
    contracts = []
    bce_root = package_root / "bce"
    available_bce = {
        path.stem: path for path in bce_root.glob("*.java") if path.is_file()
    }
    # 선택한 Boundary/Control의 반환형이나 매개변수형도 구현에 필요한 계약이다.
    # 예를 들어 ``CloseResult`` record를 빼면 agent는 생성자 인자를 알 수 없어 수리
    # 단계마다 다른 값을 추측한다. 전체 BCE를 보내지는 않고, 실제 Java 선언에서 이름이
    # 참조된 type만 차례로 따라간다.
    selected = {name for name in names if name in available_bce}
    pending = sorted(selected)
    while pending:
        current = pending.pop(0)
        source = available_bce[current].read_text(encoding="utf-8").strip()
        for candidate in sorted(set(available_bce) - selected):
            if re.search(rf"\b{re.escape(candidate)}\b", source):
                selected.add(candidate)
                pending.append(candidate)
    for name in sorted(selected):
        path = available_bce[name]
        contracts.append(
            f"// bce/{name}.java\n{path.read_text(encoding='utf-8').strip()}"
        )
    for name in sorted(api_model_names or set()):
        path = package_root / "api" / "model" / f"{name}.java"
        if path.is_file():
            contracts.append(
                f"// api/model/{name}.java\n{path.read_text(encoding='utf-8').strip()}"
            )
    for name in sorted(repository_names or set()):
        path = package_root / "persistence" / "repository" / f"{name}.java"
        if path.is_file():
            contracts.append(
                f"// persistence/repository/{name}.java\n"
                f"{path.read_text(encoding='utf-8').strip()}"
            )
    return "\n\n".join(contracts) or "// No generated Java contracts found"


def referenced_openapi_model_names(openapi_context: str) -> set[str]:
    return set(
        re.findall(r"#/components/schemas/([A-Za-z_$][A-Za-z0-9_$]*)", openapi_context)
    )
