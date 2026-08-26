from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import settings

from .frontend_contracts import GeneratedClientContracts
from ..domain.implementation_ir import (
    ApiPortIR,
    GatewayIR,
    build_implementation_ir,
    parse_erd_entities,
)
from ..generation.frontend_scaffold import frontend_page_names, operation_ids
from ..domain.models import JobSpec


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
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
STOP_WORDS = {
    "manager", "controller", "service", "get", "set", "is", "on", "log",
    "string", "boolean", "int", "float", "void", "message", "record",
}
HTTP_STATUS_ENUMS = {
    "BAD_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "UNPROCESSABLE_ENTITY": 422,
    "TOO_MANY_REQUESTS": 429,
    "INTERNAL_SERVER_ERROR": 500,
    "BAD_GATEWAY": 502,
    "SERVICE_UNAVAILABLE": 503,
    "GATEWAY_TIMEOUT": 504,
}


def generate_implementation_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    bce = _read(spec.inputs.get("bceClass"))
    sequence = _read(spec.inputs.get("sequence"))
    erd = _read(spec.inputs.get("erd"))
    openapi = _read(spec.inputs.get("openapi"))
    classes = parse_design_classes(bce)
    relations = parse_relations(bce)
    operations = parse_openapi_operations(openapi)
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)

    tasks: list[ImplementationTask] = []
    class_by_name = {item.name: item for item in classes}
    ir = build_implementation_ir(spec, run_root)
    repositories = {
        f"{entity}Repository"
        for entity in ir.persistent_entities
    }
    for control in sorted(item.name for item in classes if item.stereotype.lower() == "control"):
        neighbors = sorted(
            right if left == control else left
            for left, right, _ in relations
            if left == control or right == control
        )
        relevant_names = {control, *neighbors}
        bce_context = "\n\n".join(
            class_by_name[name].block for name in sorted(relevant_names) if name in class_by_name
        )
        relation_context = "\n".join(
            line for left, right, line in relations if control in {left, right}
        )
        sequence_context = slice_sequence(sequence, relevant_names)
        entity_names = {
            name for name in relevant_names
            if name in class_by_name and class_by_name[name].stereotype.lower() == "entity"
        }
        erd_context = slice_erd(erd, entity_names)
        openapi_context = select_openapi_operations(control, class_by_name[control].body, operations)
        api_model_names = referenced_openapi_model_names(openapi_context)
        generated_contracts = read_generated_java_contracts(
            run_root, spec.base_package, relevant_names, api_model_names
        )
        empty_contracts = find_empty_java_contracts(generated_contracts)

        task_slug = camel_to_kebab(control)
        relative_java = (
            "application/src/main/java/"
            + spec.base_package.replace(".", "/")
            + f"/application/impl/{control}Service.java"
        )
        relative_test = (
            "application/src/test/java/"
            + spec.base_package.replace(".", "/")
            + f"/application/impl/{control}ServiceTest.java"
        )
        context = {
            "schemaVersion": "implementation-context/v1alpha1",
            "taskId": f"implement-{task_slug}",
            "control": control,
            "neighbors": neighbors,
            "bce": bce_context + ("\n\n" + relation_context if relation_context else ""),
            "sequence": sequence_context,
            "erd": erd_context,
            "openapi": openapi_context,
            "generatedJavaContracts": generated_contracts,
            "emptyGeneratedContracts": empty_contracts,
            "repositories": sorted(
                repository for repository in repositories
                if repository.removesuffix("Repository") in entity_names
            ),
        }
        context_path = output / f"{task_slug}.context.json"
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        prompt = render_prompt(spec, context, [relative_java, relative_test])
        prompt_path = output / f"{task_slug}.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        task = ImplementationTask(
            task_id=f"implement-{task_slug}",
            control=control,
            prompt_file=_relative(run_root, prompt_path),
            context_file=_relative(run_root, context_path),
            allowed_write_paths=[relative_java, relative_test],
            immutable_paths=[
                "application/src/main/java/" + spec.base_package.replace(".", "/") + "/bce",
                "application/src/main/java/" + spec.base_package.replace(".", "/") + "/api",
            ],
            source_artifacts={
                name: str(path) for name, path in spec.inputs.items()
                if name in {"bceClass", "sequence", "erd", "openapi"} and path.is_file()
            },
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            llm={
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
            },
            task_type="control",
        )
        (output / f"{task_slug}.task.json").write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tasks.append(task)
    return tasks


def generate_persistence_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Plan bounded OpenHands tasks for the ERD persistence vertical slice."""
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
    entity_files = [
        f"application/src/main/java/{package_path}/persistence/entity/{name}Entity.java"
        for name in entity_names
    ]
    repository_files = [
        f"application/src/main/java/{package_path}/persistence/repository/{name}Repository.java"
        for name in entity_names
    ]
    # Keep one generated source file per agent task. A single LLM conversation
    # can exhaust its iteration budget while creating a large entity set,
    # leaving a partially generated workspace that fails the output gate.
    groups = [
        *[
            (
                f"implement-erd-persistence-entity-{camel_to_kebab(name)}",
                "persistence-entities",
                [path],
                render_persistence_entity_prompt(
                    spec,
                    erd,
                    read_generated_java_contracts(
                        run_root, spec.base_package, {name}
                    ),
                    [name],
                    [path],
                ),
            )
            for name, path in zip(entity_names, entity_files, strict=True)
        ],
        *[
            (
                f"implement-erd-persistence-repository-{camel_to_kebab(name)}",
                "persistence-repositories",
                [path],
                render_persistence_repository_prompt(spec, [name], [path]),
            )
            for name, path in zip(entity_names, repository_files, strict=True)
        ],
        (
            "implement-erd-persistence-mapping",
            "persistence-mapping",
            [
                f"application/src/main/java/{package_path}/persistence/mapper/BcePersistenceMapper.java",
                f"application/src/test/java/{package_path}/persistence/mapper/BcePersistenceMapperTest.java",
            ],
            render_persistence_mapping_prompt(spec, erd, contracts),
        ),
        (
            "implement-erd-persistence-schema",
            "persistence-schema",
            [
                "application/src/main/resources/db/migration/V1__initial_schema.sql",
                f"application/src/test/java/{package_path}/persistence/PersistenceSchemaTest.java",
            ],
            render_persistence_schema_prompt(spec, erd),
        ),
    ]
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    tasks: list[ImplementationTask] = []
    for task_id, task_type, allowed, prompt_body in groups:
        slug = task_id.removeprefix("implement-")
        context = {
            "schemaVersion": "implementation-context/v1alpha1",
            "taskId": task_id,
            "taskType": task_type,
            "erd": erd,
            "bceEntities": entity_names,
            "generatedJavaContracts": contracts,
        }
        context_path = output / f"{slug}.context.json"
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt = prompt_body + render_allowed_output_rules(allowed)
        prompt_path = output / f"{slug}.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        task = ImplementationTask(
            task_id=task_id,
            control="ERD persistence",
            prompt_file=_relative(run_root, prompt_path),
            context_file=_relative(run_root, context_path),
            allowed_write_paths=allowed,
            immutable_paths=[
                f"application/src/main/java/{package_path}/bce",
                f"application/src/main/java/{package_path}/api",
            ],
            source_artifacts={
                name: str(path) for name, path in spec.inputs.items()
                if name in {"bceClass", "erd"} and path.is_file()
            },
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            llm=_llm_config(spec),
            task_type=task_type,
        )
        (output / f"{slug}.task.json").write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tasks.append(task)
    return tasks


def generate_api_adapter_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Plan one bounded Spring web adapter task per generated OpenAPI interface."""
    package_path = spec.base_package.replace(".", "/")
    java_root = run_root / "application" / "src" / "main" / "java" / package_path
    ir = build_implementation_ir(spec, run_root)
    sequence = _read(spec.inputs.get("sequence"))
    control_bindings = _openapi_control_bindings(_read(spec.inputs.get("openapi")))
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    tasks: list[ImplementationTask] = []
    for api_port in ir.api_ports:
        stem = api_port.name
        api_interface = java_root / "api" / f"{stem}Api.java"
        api_sources = [api_interface]
        api_sources.extend(sorted((java_root / "api" / "model").glob("*.java")))
        # API-to-application mapping can involve a Control whose class name does
        # not match the resource noun. Inject all Control contracts and let the
        # exact operation signatures/sequence establish the mapping.
        bce_names = [*ir.controls, *ir.entities]
        bce_sources = [java_root / "bce" / f"{name}.java" for name in bce_names]
        exact_contracts = render_source_contracts(
            run_root, [*api_sources, *bce_sources]
        )
        sequence_context = slice_sequence(sequence, set(bce_names))
        kebab = camel_to_kebab(stem)
        task_id = f"implement-{kebab}-api-adapter"
        allowed = [
            f"application/src/main/java/{package_path}/adapter/in/web/{stem}ApiController.java",
            f"application/src/test/java/{package_path}/adapter/in/web/{stem}ApiControllerTest.java",
        ]
        context = {
            "schemaVersion": "implementation-context/v1alpha1",
            "taskId": task_id,
            "taskType": "api-adapter",
            "api": stem,
            "operations": [
                {
                    "method": operation.method,
                    "path": operation.path,
                    "operationId": operation.operation_id,
                    "responses": [response.status for response in operation.responses],
                    "controlBinding": control_bindings.get(operation.operation_id or ""),
                }
                for operation in api_port.operations
            ],
            "sequence": sequence_context,
            "generatedJavaContracts": exact_contracts,
        }
        context_path = output / f"{kebab}-api-adapter.context.json"
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt = render_api_adapter_prompt(
            spec,
            api_port,
            exact_contracts,
            sequence_context,
            {
                operation.operation_id: control_bindings[operation.operation_id]
                for operation in api_port.operations
                if operation.operation_id in control_bindings
            },
        ) + render_allowed_output_rules(allowed)
        prompt_path = output / f"{kebab}-api-adapter.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        task = ImplementationTask(
            task_id=task_id,
            control=f"{stem}Api",
            prompt_file=_relative(run_root, prompt_path),
            context_file=_relative(run_root, context_path),
            allowed_write_paths=allowed,
            immutable_paths=[
                f"application/src/main/java/{package_path}/bce",
                f"application/src/main/java/{package_path}/api",
                f"application/src/main/java/{package_path}/persistence",
            ],
            source_artifacts={
                name: str(path) for name, path in spec.inputs.items()
                if name in {"openapi", "sequence", "bceClass"} and path.is_file()
            },
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            llm=_llm_config(spec),
            task_type="api-adapter",
        )
        (output / f"{kebab}-api-adapter.task.json").write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tasks.append(task)
    return tasks


def generate_boundary_adapter_tasks(
    spec: JobSpec, run_root: Path
) -> list[ImplementationTask]:
    """Plan one bounded headless adapter task per BCE Boundary contract."""
    bce = _read(spec.inputs.get("bceClass"))
    sequence = _read(spec.inputs.get("sequence"))
    classes = parse_design_classes(bce)
    boundaries = sorted(
        (item for item in classes if item.stereotype.lower() == "boundary"),
        key=lambda item: item.name,
    )
    if not boundaries:
        raise ValueError("BCE input contains no <<Boundary>> contracts")

    package_path = spec.base_package.replace(".", "/")
    class_names = {item.name for item in classes}
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    tasks: list[ImplementationTask] = []
    for boundary in boundaries:
        sequence_context = slice_sequence(sequence, {boundary.name})
        peers = {
            name for name in class_names
            if name != boundary.name
            and re.search(rf"\b{re.escape(name)}\b", sequence_context)
        }
        contracts = read_generated_java_contracts(
            run_root, spec.base_package, {boundary.name, *peers}
        )
        slug = camel_to_kebab(boundary.name)
        task_id = f"implement-{slug}-boundary-adapter"
        allowed = [
            f"application/src/main/java/{package_path}/adapter/in/boundary/{boundary.name}Adapter.java",
            f"application/src/test/java/{package_path}/adapter/in/boundary/{boundary.name}AdapterTest.java",
        ]
        context = {
            "schemaVersion": "implementation-context/v1alpha1",
            "taskId": task_id,
            "taskType": "boundary-adapter",
            "boundary": boundary.name,
            "sequence": sequence_context,
            "generatedJavaContracts": contracts,
        }
        context_path = output / f"{slug}-boundary-adapter.context.json"
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt = render_boundary_adapter_prompt(
            spec, boundary.name, contracts, sequence_context
        ) + render_allowed_output_rules(allowed)
        prompt_path = output / f"{slug}-boundary-adapter.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        task = ImplementationTask(
            task_id=task_id,
            control=boundary.name,
            prompt_file=_relative(run_root, prompt_path),
            context_file=_relative(run_root, context_path),
            allowed_write_paths=allowed,
            immutable_paths=[
                f"application/src/main/java/{package_path}/bce",
                f"application/src/main/java/{package_path}/api",
                f"application/src/main/java/{package_path}/application",
                f"application/src/main/java/{package_path}/persistence",
                f"application/src/main/java/{package_path}/adapter/in/web",
            ],
            source_artifacts={
                name: str(path) for name, path in spec.inputs.items()
                if name in {"bceClass", "sequence"} and path.is_file()
            },
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            llm=_llm_config(spec),
            task_type="boundary-adapter",
        )
        (output / f"{slug}-boundary-adapter.task.json").write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tasks.append(task)
    return tasks


def generate_gateway_adapter_tasks(
    spec: JobSpec, run_root: Path
) -> list[ImplementationTask]:
    """Plan outbound adapters for generated BCE Gateway ports."""
    package_path = spec.base_package.replace(".", "/")
    package_root = run_root / "application" / "src" / "main" / "java" / package_path
    ir = build_implementation_ir(spec, run_root)
    specifications: list[tuple[str, GatewayIR, list[str], list[Path], str]] = []
    for gateway in ir.gateways:
        existing = next(
            (
                path for path in (package_root / "adapter" / "out").rglob("*.java")
                if path.stem in {f"{gateway.name}Adapter", f"InMemory{gateway.name}Adapter"}
            ),
            None,
        )
        adapter_name = (
            existing.stem
            if existing
            else (
                f"{gateway.name}Adapter"
                if gateway.kind == "persistence"
                else f"InMemory{gateway.name}Adapter"
            )
        )
        adapter_dir = (
            existing.parent.relative_to(package_root).as_posix()
            if existing
            else f"adapter/out/{'persistence' if gateway.kind == 'persistence' else 'gateway'}"
        )
        test_dir = adapter_dir
        allowed = [
            f"application/src/main/java/{package_path}/{adapter_dir}/{adapter_name}.java",
            f"application/src/test/java/{package_path}/{test_dir}/{adapter_name}Test.java",
        ]
        sources = [package_root / "bce" / f"{gateway.name}.java"]
        if gateway.kind == "persistence":
            for relative in ("bce", "persistence/entity", "persistence/repository", "persistence/mapper"):
                sources.extend(sorted((package_root / relative).glob("*.java")))
        else:
            sources.extend(sorted((package_root / "bce").glob("*.java")))
        task_id = f"implement-{camel_to_kebab(gateway.name)}-adapter"
        specifications.append(
            (
                task_id,
                gateway,
                allowed,
                list(dict.fromkeys(sources)),
                render_gateway_prompt(spec, gateway, adapter_name, adapter_dir),
            )
        )

    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    tasks: list[ImplementationTask] = []
    for task_id, gateway, allowed, source_paths, prompt_header in specifications:
        contracts = render_source_contracts(run_root, source_paths)
        context = {
            "schemaVersion": "implementation-context/v1alpha1",
            "taskId": task_id,
            "taskType": "gateway-adapter",
            "gateway": gateway.name,
            "gatewayKind": gateway.kind,
            "generatedJavaContracts": contracts,
            "erd": _read(spec.inputs.get("erd")),
            "sequence": _read(spec.inputs.get("sequence")),
        }
        slug = task_id.removeprefix("implement-")
        context_path = output / f"{slug}.context.json"
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt = prompt_header + f"\n\n## Exact generated contracts\n\n```java\n{contracts}\n```\n"
        prompt += render_allowed_output_rules(allowed)
        prompt_path = output / f"{slug}.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        task = ImplementationTask(
            task_id=task_id,
            control=gateway.name,
            prompt_file=_relative(run_root, prompt_path),
            context_file=_relative(run_root, context_path),
            allowed_write_paths=allowed,
            immutable_paths=[
                f"application/src/main/java/{package_path}/bce",
                f"application/src/main/java/{package_path}/application",
                f"application/src/main/java/{package_path}/persistence",
            ],
            source_artifacts={
                name: str(path) for name, path in spec.inputs.items()
                if name in {"bceClass", "sequence", "erd", "deployment", "cloud"}
                and path.is_file()
            },
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            llm=_llm_config(spec),
            task_type="gateway-adapter",
        )
        (output / f"{slug}.task.json").write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tasks.append(task)
    return tasks


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
    allowed = [
        f"application/src/main/java/{package_path}/config/ApplicationConfiguration.java",
        "application/src/main/resources/application.yml",
        f"application/src/test/java/{package_path}/config/ApplicationContextTest.java",
    ]
    task_id = "implement-application-wiring"
    context = {
        "schemaVersion": "implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "configuration",
        "generatedJavaContracts": contracts,
        "applicationClass": ir.application_class,
    }
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    context_path = output / "application-wiring.context.json"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    prompt = render_wiring_prompt(spec, ir.application_class, contracts)
    prompt += render_allowed_output_rules(allowed)
    prompt_path = output / "application-wiring.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id=task_id,
        control="Spring application wiring",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=allowed,
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
            if name in {"bceClass", "sequence", "erd", "openapi", "deployment", "cloud"}
            and path.is_file()
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="configuration",
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
    sequence = _read(spec.inputs.get("sequence"))
    pages = frontend_page_names(openapi)
    operations = operation_ids(openapi)
    client_contracts = GeneratedClientContracts.discover(generated)
    generated_contracts = client_contracts.render()

    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    contracts_path = run_root / "reports" / "frontend-generated-client-contracts.txt"
    contracts_path.write_text(generated_contracts, encoding="utf-8")
    allowed = [
        "application/frontend/src/App.tsx",
        "application/frontend/src/components/AppShell.tsx",
        *[f"application/frontend/src/pages/{name}.tsx" for name in pages],
        "application/frontend/src/styles.css",
    ]
    task_id = "implement-frontend-application"
    context = {
        "schemaVersion": "frontend-implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "frontend-implementation",
        "classDiagram": bce,
        "sequenceDiagram": sequence,
        "openapi": openapi,
        "pages": pages,
        "operationIds": operations,
        "generatedTypescriptContracts": generated_contracts,
        "generatedImportRoot": client_contracts.import_root,
    }
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
- Derive screens and user actions from BCE Boundary responsibilities and the sequence flow.
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

## Sequence design
```plantuml
{sequence}
```

## OpenAPI contract
```json
{json.dumps(openapi, ensure_ascii=False, indent=2)}
```

## Exact OpenAPI Generator TypeScript contracts
```typescript
{generated_contracts}
```
"""
    prompt += render_allowed_output_rules(allowed)
    prompt_path = output / "frontend-application.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id=task_id,
        control=f"{spec.name} frontend application",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=allowed,
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
                if name in {"bceClass", "sequence", "openapi"} and path.is_file()
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


def generate_e2e_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Plan a domain-neutral real HTTP flow test or report executable gaps."""
    ir = build_implementation_ir(spec, run_root)
    gaps = detect_e2e_design_gaps(spec, run_root)
    gap_report = {
        "schemaVersion": "implementation-design-gaps/v1alpha1",
        "phase": "end-to-end",
        "status": "NEEDS_INPUT" if gaps else "READY",
        "gaps": gaps,
    }
    gap_path = run_root / "reports" / "design-gaps" / "end-to-end-flow.json"
    gap_path.parent.mkdir(parents=True, exist_ok=True)
    gap_path.write_text(
        json.dumps(gap_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # An integration test is immutable with respect to production sources.  It
    # cannot repair an unresolved API/controller implementation, so scheduling
    # it would only waste repair attempts and obscure the production defect.
    if gaps:
        return []
    package_path = spec.base_package.replace(".", "/")
    package_root = run_root / "application" / "src" / "main" / "java" / package_path
    sources: list[Path] = []
    for relative in (
        "adapter/in/web",
        "adapter/in/boundary",
        "adapter/out",
        "persistence/repository",
        "persistence/entity",
        "api/model",
        "bce",
    ):
        sources.extend(sorted((package_root / relative).rglob("*.java")))
    sources = list(dict.fromkeys(sources))
    contracts = render_source_contracts(run_root, sources)
    sequence = _read(spec.inputs.get("sequence"))
    erd = _read(spec.inputs.get("erd"))
    openapi = _read(spec.inputs.get("openapi"))
    repositories = [
        path.stem for path in sorted((package_root / "persistence" / "repository").glob("*Repository.java"))
    ]
    gateway_adapters = []
    adapter_root = package_root / "adapter" / "out"
    for source in sorted(adapter_root.rglob("*.java")) if adapter_root.is_dir() else []:
        text = source.read_text(encoding="utf-8")
        if any(re.search(rf"\bimplements\s+{re.escape(item.name)}\b", text) for item in ir.gateways):
            gateway_adapters.append(source.stem)
    scenarios = [
        {"method": item.method, "path": item.path, "status": item.status, "label": item.label}
        for item in ir.e2e_scenarios
    ]
    semantic_contract = {
        "paths": sorted({item.path for item in ir.e2e_scenarios}),
        "statuses": sorted({item.status for item in ir.e2e_scenarios}),
        "repositories": repositories,
        "gatewayAdapters": gateway_adapters,
        "minimumTests": max(1, len(ir.e2e_scenarios)),
        "scenarios": scenarios,
    }
    flow_name = ir.application_class.removesuffix("Application") + "FlowTest"
    allowed = [
        f"application/src/test/java/{package_path}/integration/{flow_name}.java"
    ]
    context = {
        "schemaVersion": "implementation-context/v1alpha1",
        "taskId": "implement-end-to-end-flow",
        "taskType": "integration-test",
        "sequence": sequence,
        "erd": erd,
        "openapi": openapi,
        "generatedJavaContracts": contracts,
        "semanticContract": semantic_contract,
        "designGaps": gaps,
    }
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    context_path = output / "end-to-end-flow.context.json"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    prompt = render_e2e_prompt(
        spec, ir.application_name, semantic_contract, contracts, sequence, erd, openapi
    )
    prompt += render_allowed_output_rules(allowed)
    prompt_path = output / "end-to-end-flow.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id="implement-end-to-end-flow",
        control=f"{ir.application_name} end-to-end flow",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=allowed,
        immutable_paths=[f"application/src/main/java/{package_path}"],
        source_artifacts={
            name: str(path) for name, path in spec.inputs.items()
            if name in {"bceClass", "sequence", "erd", "openapi"} and path.is_file()
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="integration-test",
    )
    (output / "end-to-end-flow.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task]


def detect_e2e_design_gaps(spec: JobSpec, run_root: Path) -> list[dict[str, str]]:
    """Find domain-neutral executable-contract gaps before E2E generation."""
    package_path = spec.base_package.replace(".", "/")
    package_root = run_root / "application" / "src" / "main" / "java" / package_path
    test_root = run_root / "application" / "src" / "test" / "java" / package_path
    ir = build_implementation_ir(spec, run_root)
    gaps: list[dict[str, str]] = []

    for root in (package_root / "application" / "impl", package_root / "adapter"):
        for path in sorted(root.rglob("*.java")) if root.is_dir() else []:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code_line = re.sub(r"//.*$", "", line).strip()
                if re.search(r"\b(?:TODO|FIXME|UnsupportedOperationException)\b", code_line):
                    gaps.append({
                        "code": "UNRESOLVED_PRODUCTION_PATH",
                        "source": path.relative_to(run_root).as_posix(),
                        "evidence": f"line {number}: {line.strip()}",
                        "requiredAction": "Resolve the executable design contract before generating E2E evidence.",
                    })

    for control in ir.controls:
        service = package_root / "application" / "impl" / f"{control}Service.java"
        test = test_root / "application" / "impl" / f"{control}ServiceTest.java"
        if not service.is_file() or not test.is_file():
            gaps.append({
                "code": "CONTROL_IMPLEMENTATION_MISSING",
                "source": (package_root / "bce" / f"{control}.java").relative_to(run_root).as_posix(),
                "evidence": f"{control} has no verified service/test pair.",
                "requiredAction": "Run and verify the Control phase.",
            })

    for api in ir.api_ports:
        controller = package_root / "adapter" / "in" / "web" / f"{api.name}ApiController.java"
        test = test_root / "adapter" / "in" / "web" / f"{api.name}ApiControllerTest.java"
        if not controller.is_file() or not test.is_file():
            gaps.append({
                "code": "API_ADAPTER_MISSING",
                "source": api.interface_file,
                "evidence": f"{api.name}Api has no verified controller/test pair.",
                "requiredAction": "Run and verify the API adapter phase.",
            })

    expected_error_statuses = {
        scenario.status for scenario in ir.e2e_scenarios if scenario.status >= 400
    }
    web_adapter_root = package_root / "adapter" / "in" / "web"
    web_adapter_sources = (
        sorted(web_adapter_root.rglob("*.java")) if web_adapter_root.is_dir() else []
    )
    implemented_error_statuses = _implemented_http_statuses(web_adapter_sources)
    missing_error_statuses = sorted(expected_error_statuses - implemented_error_statuses)
    if missing_error_statuses:
        expected_text = ", ".join(str(status) for status in missing_error_statuses)
        implemented_text = ", ".join(
            str(status) for status in sorted(implemented_error_statuses) if status >= 400
        ) or "none"
        gaps.append({
            "code": "OPENAPI_ERROR_OUTCOME_UNIMPLEMENTED",
            "source": "OpenAPI/BCE",
            "evidence": (
                f"The sequence-selected OpenAPI error outcome(s) {expected_text} have no "
                f"executable web-adapter branch (implemented error statuses: {implemented_text})."
            ),
            "requiredAction": (
                "Model each error outcome in the BCE Control return/error contract and "
                "sequence alternative, then implement its explicit HTTP response mapping."
            ),
        })

    adapter_root = package_root / "adapter" / "out"
    adapter_sources = list(adapter_root.rglob("*.java")) if adapter_root.is_dir() else []
    for gateway in ir.gateways:
        implementation = next(
            (
                path for path in adapter_sources
                if re.search(
                    rf"\bimplements\s+{re.escape(gateway.name)}\b",
                    path.read_text(encoding="utf-8"),
                )
            ),
            None,
        )
        if implementation is None:
            gaps.append({
                "code": "GATEWAY_ADAPTER_MISSING",
                "source": (package_root / "bce" / f"{gateway.name}.java").relative_to(run_root).as_posix(),
                "evidence": f"{gateway.name} has no concrete outbound adapter.",
                "requiredAction": "Run and verify the outbound Gateway adapter phase.",
            })

    repository_root = package_root / "persistence" / "repository"
    if ir.entities and not any(repository_root.glob("*Repository.java")):
        gaps.append({
            "code": "PERSISTENCE_OUTPUT_MISSING",
            "source": "ERD",
            "evidence": "The design contains entities but no generated repositories.",
            "requiredAction": "Run and verify the ERD persistence phase.",
        })
    return gaps


def _implemented_http_statuses(sources: list[Path]) -> set[int]:
    """Return HTTP statuses with an executable Spring web-adapter mapping."""
    statuses: set[int] = set()
    for source in sources:
        text = source.read_text(encoding="utf-8")
        if re.search(r"\bResponseEntity\s*\.\s*ok\s*\(", text):
            statuses.add(200)
        if re.search(r"\bResponseEntity\s*\.\s*created\s*\(", text):
            statuses.add(201)
        if re.search(r"\bResponseEntity\s*\.\s*noContent\s*\(", text):
            statuses.add(204)
        if re.search(r"\bResponseEntity\s*\.\s*notFound\s*\(", text):
            statuses.add(404)
        statuses.update(
            int(value)
            for value in re.findall(
                r"\bResponseEntity\s*\.\s*status\s*\(\s*(\d{3})\s*\)",
                text,
            )
        )
        statuses.update(
            status for name, status in HTTP_STATUS_ENUMS.items()
            if re.search(rf"\bHttpStatus\s*\.\s*{name}\b", text)
        )
    return statuses


def render_source_contracts(run_root: Path, paths: list[Path]) -> str:
    sections: list[str] = []
    for path in paths:
        if path.is_file():
            sections.append(
                f"// {path.relative_to(run_root).as_posix()}\n"
                + path.read_text(encoding="utf-8").strip()
            )
    return "\n\n".join(sections) or "// No Java contracts found"


def _openapi_control_bindings(source: str) -> dict[str, dict[str, object]]:
    """Read reviewed API-to-Control mappings carried by the OpenAPI extension."""
    try:
        document = json.loads(source)
    except json.JSONDecodeError:
        return {}
    bindings: dict[str, dict[str, object]] = {}
    for path_item in document.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            binding = operation.get("x-easydep-control")
            if isinstance(operation_id, str) and isinstance(binding, dict):
                bindings[operation_id] = binding
    return bindings


def render_api_adapter_prompt(
    spec: JobSpec,
    api_port: ApiPortIR,
    contracts: str,
    sequence: str,
    control_bindings: dict[str | None, dict[str, object]] | None = None,
) -> str:
    operations = "\n".join(
        f"- {operation.method} {operation.path}: "
        + ", ".join(
            f"{response.status} {response.description}".strip()
            for response in operation.responses
        )
        for operation in api_port.operations
    ) or "- No parsed OpenAPI operations; implement only methods present in the exact interface."
    return f"""# Implementation task: {api_port.name} OpenAPI adapter

Implement `{api_port.name}Api` as a Spring REST controller and create a focused unit/contract test.

Rules:
- Use package `{spec.base_package}.adapter.in.web` and implement the exact generated `{api_port.name}Api` interface.
- Annotate the class with `@RestController`; do not duplicate or change generated request mappings.
- Use constructor injection for BCE application Control ports. Never instantiate a service directly.
- Preserve exact `ResponseEntity` generic types and documented HTTP status semantics.
- Never invent methods, access private DTO fields, use reflection, or edit generated contracts.
- Test every generated interface operation, response status/body mapping, and Control invocation. Instantiate the real controller and mock only its Control collaborators.
- Select Control operations only from the exact injected interfaces and sequence messages. Match
  request fields by name and compatible type; never fabricate domain values or silently drop a
  required input.
- HTTP request DTOs and BCE input types are separate generated contracts. Do not pass an API
  `Object` or API-model instance directly to a BCE method unless Java declares them assignable.
  Convert the request explicitly to the exact BCE parameter type using only public constructors,
  accessors, and fields present in both contracts. For a named empty request DTO whose BCE type
  has no state, create the BCE value with its public no-argument constructor; never cast an
  `Object` body or hide the mismatch with reflection.
- Follow the reviewed API-to-Control binding below when one is supplied. Its `arguments` map
  is the only permitted HTTP-to-Control value flow and its `outcomes` map is the required
  Control-result-to-HTTP-status mapping. Do not replace it with a resource-name guess.
- Map every documented OpenAPI response status below to an explicit, observable Control outcome.
  A null result must not be assigned an arbitrary status. If the generated contracts cannot
  represent a documented response, fail compilation rather than concealing the design gap.
- Map BCE return values into generated API DTOs field by field using exact public accessors.
- Unit tests must cover every documented status and verify exact Control arguments.
- Never leave placeholder, empty fallback, or speculative response comments in production code.
  If a Control cannot supply the documented response or error outcome, leave the contract
  uncompilable and report the design gap; do not fabricate a response.
- Create both contracted files, then finish immediately.

## OpenAPI response contract

{operations}

## Relevant sequence messages

```plantuml
{sequence}
```

## Reviewed API-to-Control bindings

```json
{json.dumps(control_bindings or {}, ensure_ascii=False, indent=2)}
```

## Exact generated API and BCE contracts

```java
{contracts}
```
"""


def render_boundary_adapter_prompt(
    spec: JobSpec, boundary: str, contracts: str, sequence: str
) -> str:
    no_sequence_delegation = sequence.strip().startswith("' No directly matched")
    delegation_rule = (
        "- No sequence message matches this Boundary. Do not import, inject, infer, "
        "or call any Control; implement only the Boundary's own state behavior.\n"
        if no_sequence_delegation
        else ""
    )
    return f"""# Implementation task: {boundary} BCE Boundary adapter

Implement a headless, state-backed prototype adapter for the exact `{boundary}` BCE Boundary
interface and create a focused unit test.

Rules:
- Use package `{spec.base_package}.adapter.in.boundary` and implement the exact generated `{boundary}` interface.
- Do not add REST mappings or edit generated BCE/API contracts. OpenAPI web adapters remain the HTTP boundary.
- Do not annotate the adapter as a Spring bean yet; production bean ownership and cycle-free wiring are decided by the wiring phase.
- Follow direction in the sequence: a Boundary-to-Control message delegates to that exact Control operation; a Control-to-Boundary message stores or exposes presentation/input state and must not call back into the Control.
- Retain values received by `on*`, `show*`, or equivalent methods. Return the last submitted/configured value from matching `get*`, `ask*`, or `prompt*` methods.
- When the interface has a return method but no submission method, accept the return value through a constructor or one explicit adapter-only submission method. Do not invent methods on BCE contracts.
- Reject only clearly invalid null input needed to preserve adapter state; do not invent business validation absent from the contracts.
- Expose only minimal read-only adapter accessors needed to observe display/error/portfolio state in tests.
- Do not leave TODO, FIXME, placeholder, or speculative import comments; all exact collaborator types are included below.
- Use constructor injection only when the sequence requires delegation to a generated Control port. Never instantiate a Control service directly.
{delegation_rule}- Never derive a collaborator or method name from the Boundary class name; use only exact sequence messages and contracts.
- Test every Boundary interface method, state transition, returned value, and required Control invocation. Use Mockito only for generated Control collaborators.
- Create both contracted files, then finish immediately.

## Relevant sequence messages

```plantuml
{sequence}
```

## Exact generated Boundary and collaborating contracts

```java
{contracts}
```
"""


def render_gateway_prompt(
    spec: JobSpec, gateway: GatewayIR, adapter_name: str, adapter_dir: str
) -> str:
    package = f"{spec.base_package}.{adapter_dir.replace('/', '.')}"
    if gateway.kind == "persistence":
        behavior = """
- Constructor-inject only the generated repositories and mapper required by the exact Gateway methods.
- Map BCE values to persistence entities, invoke the corresponding repository operation exactly
  once, and map repository results back to BCE values when the contract returns a value.
- Preserve every natural identifier and scalar field; never invent derived queries or fields.
- Tests may mock repositories but must use the real mapper and cover every Gateway operation.
"""
    else:
        behavior = """
- Implement a deterministic local adapter: keep configurable return values in thread-safe queues
  or maps keyed by the exact method inputs. Never perform real network access in the prototype.
- Expose minimal adapter-only enqueue/configure/reject methods for deterministic E2E tests. Name
  these seams after the exact Gateway operation or returned type rather than a domain guess.
- Preserve configured return values without fabricating fields. Explicitly model disconnected or
  rejected calls only when the exact contract exposes connection/failure behavior.
- Tests must cover every Gateway method, configured success values, rejection/failure, and state reset.
"""
    return f"""# Implementation task: {gateway.name} outbound adapter

Implement the exact BCE `{gateway.name}` Gateway as `{adapter_name}` and create a focused test.

Rules:
- Use package `{package}` and implement `{gateway.name}` exactly.
- Use only public operations present in the injected contracts; do not edit BCE, persistence, or API files.
{behavior}
- Do not leave TODO/FIXME/placeholders or conceal an unrepresentable design contract.
- Create both contracted files, then finish immediately.
"""


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


def render_e2e_prompt(
    spec: JobSpec,
    application_name: str,
    semantic_contract: dict[str, object],
    contracts: str,
    sequence: str,
    erd: str,
    openapi: str,
) -> str:
    scenarios = "\n".join(
        f"- {item['method']} {item['path']} -> HTTP {item['status']}: {item['label']}"
        for item in semantic_contract.get("scenarios", [])
    )
    repositories = ", ".join(semantic_contract.get("repositories", [])) or "none"
    gateways = ", ".join(semantic_contract.get("gatewayAdapters", [])) or "none"
    minimum_tests = semantic_contract.get("minimumTests", 1)
    return f"""# Implementation task: {application_name} end-to-end flow

Create one Spring Boot integration test that verifies the real application graph across HTTP/API
adapters, Control services, Boundary adapters, and persistence.

Rules:
- Use package `{spec.base_package}.integration` and `@SpringBootTest` with the real H2/Flyway configuration.
- The generated build is pinned to Spring Boot 3.3.13 and Java 21. Inspect its
  `build.gradle` before writing imports or APIs; do not copy framework examples from memory.
- Prefer `MockMvc` with `@AutoConfigureMockMvc` for the real HTTP contract so the test does not
  need a random embedded port. If `TestRestTemplate` with `RANDOM_PORT` is required, import
  `org.springframework.boot.test.web.server.LocalServerPort` (never the removed
  Spring Boot 2.x package `org.springframework.boot.web.server.LocalServerPort`).
- Spring Boot 3 uses Jakarta APIs. Use `jakarta.persistence`, `jakarta.validation`,
  `jakarta.servlet`, `jakarta.annotation`, and `jakarta.transaction`; never import their
  legacy `javax.*` counterparts in the generated test or test configuration.
- Do not add an ad-hoc dependency or downgrade the Spring Boot version to make a stale import compile.
- Do not mock application Controls, Boundary adapters, repositories, or the Spring context.
- Use the production application graph exactly as wired. Never declare `@TestConfiguration`,
  `@Bean`, `@MockBean`, `@MockitoBean`, `@Primary`, or enable bean-definition overriding.
- Autowire concrete Gateway adapters and at least one Spring Data repository listed in the semantic
  contract. Drive external outcomes only through their public deterministic seams; do not replace beans.
- Declare Gateway fields as their concrete adapter classes. Never use reflection or reduce them
  to only the Gateway interface when a configuration seam is required.
- Use `@DirtiesContext(classMode = BEFORE_EACH_TEST_METHOD)` when isolated state is needed.
  Never create duplicate application, Control, Boundary, Entity, or Gateway beans.
- Implement every scenario in the generated semantic contract below with at least
  {minimum_tests} independent `@Test` methods. Use real HTTP through `TestRestTemplate` or
  `MockMvc`; assert the exact response status and relevant response fields for each scenario.
- Each semantic-contract row is immutable: in that row's test, invoke exactly the listed HTTP
  method and path, then assert exactly its listed status. Do not infer a conventional status
  (for example, do not substitute `201 Created` for a documented `200 OK`) and do not append
  an undeclared path segment. A Java URL assembled from path variables is allowed only when it
  resolves exactly to the listed path template. A shared helper such as `performLogin()` is
  allowed when the calling `@Test` invokes that helper and the helper contains the exact HTTP
  request; do not omit the scenario because the request is factored into a helper.
- Assert repository-backed persistence for the exercised flow. A test that only checks in-memory
  UI state is invalid; unrelated repositories do not need to be injected into this single flow test.
- Assert observable HTTP responses, Boundary state, and repository state; do not call private methods or reproduce service logic inside the test.
- Before writing each scenario, inspect the generated API controller, Control, and concrete Boundary
  adapter used by that request.  Seed and call the exact identifiers and inputs that those contracts
  require; a repository seed is not visible to a Control that delegates to a Boundary adapter.
- Include every required path, query, header, and body parameter from the generated API signature.
  Do not omit a required query parameter merely because its value also appears elsewhere in the test.
- When a concrete stateful Boundary adapter exposes a public deterministic configuration or submission
  seam, use that existing seam to configure the exact response consumed by the real Control.  Do not
  assume a non-null return value, fabricate a second bean, or modify production sources.
- When a request causes persistence with a foreign-key reference, seed the referenced record using the
  exact identifier that the request passes through the Control.  Do not substitute a display name,
  username, or unrelated fixture key.
- For a generic/Object request body, send JSON compatible with the generated controller's conversion
  contract.  Do not assume Spring preserves a BCE input type placed inside `HttpEntity<Object>`.
- Do not weaken or disable Flyway/JPA and do not modify production sources.
- Do not leave TODO, disabled tests, unconditional success assertions, or tests that merely check context loading.
- Never accept multiple outcomes, omit a strict assertion, describe the test as simplified,
  or claim a required compiled adapter/repository is unavailable when it appears below.
- Create the contracted test file, then finish immediately.

## Machine-derived semantic contract

Required repositories: {repositories}
Required Gateway adapters: {gateways}

{scenarios}

## Sequence
```plantuml
{sequence}
```

## ERD
```plantuml
{erd}
```

## OpenAPI
```yaml
{openapi}
```

## Exact executable Java contracts
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


def render_persistence_entity_prompt(
    spec: JobSpec, erd: str, contracts: str, names: list[str], allowed: list[str]
) -> str:
    subject = ", ".join(f"`{name}Entity`" for name in names)
    return f"""# Implementation task: ERD persistence entity

Create the JPA persistence entity {subject} listed in the contracted output.

Rules:
- Use package `{spec.base_package}.persistence.entity` and Jakarta Persistence annotations.
- Derive every table, column, primary key, foreign key, nullability rule, and relationship from the
  injected ERD. Do not assume a fixed number of entities or relationships.
- Use a generated `Long id` only when the ERD declares a technical numeric key; preserve explicit
  natural-key Java types otherwise.
- Map every ERD scalar column with its exact snake_case column name. Rename a reserved identifier
  only when required by H2/SQL and use the same normalized name in the migration.
- Implement relationship ownership from the ERD cardinality and foreign-key direction. Add a
  bidirectional helper only when both navigation directions are represented in the contracts.
- A collection annotated with `@OneToMany(mappedBy = "property")` is valid only when the target
  entity declares that exact Java property as the owning `@ManyToOne` or `@OneToOne` association.
  Never use a scalar foreign-key column as the `mappedBy` target. If this entity cannot declare or
  safely coordinate that owning association, omit the inverse collection rather than emitting an
  invalid one-sided bidirectional mapping.
- An ERD comment of the form `easydep:erd-origin kind=multivalued` marks a generated 1NF
  collection table. It has no matching BCE Entity: map it through its annotated parent and field
  (for example with `@ElementCollection` and `@CollectionTable`) instead of inventing a domain
  entity for the table.
- Initialize collection relationships and provide a public no-arg constructor so the sibling persistence mapper can instantiate the entity. Add public constructors or accessors needed by the mapper and relationship helper methods that keep both sides consistent.
- Do not use Lombok, records, cascading remove, or eager collections. Do not edit BCE domain entities.
- Create the contracted file, then finish immediately.

## ERD

```plantuml
{erd}
```

## Immutable BCE contracts

```java
{contracts}
```
"""


def render_persistence_repository_prompt(
    spec: JobSpec, names: list[str], allowed: list[str]
) -> str:
    declarations = "\n".join(f"- {name}Entity -> {name}Repository" for name in names)
    return f"""# Implementation task: ERD persistence repositories

Create the Spring Data JPA repository for the listed persistence entity.

Rules:
- Use package `{spec.base_package}.persistence.repository`.
- Each public interface extends `JpaRepository<CorrespondingEntity, IdType>`, where
  `IdType` must exactly match the generated Entity's `@Id` field type. Do not default
  natural or UUID identifiers to `Long`.
- Import entities from `{spec.base_package}.persistence.entity`.
- Add a derived query only for a natural identifier explicitly present in the ERD/BCE contract and
  needed by a Gateway operation. A repository with no such requirement should contain no custom query.
- The parameter and return generic types of every derived query must exactly match the
  Java property type in the injected JPA entity contracts. A technical auto-increment
  key is `Long` only when the ERD/entity explicitly declares that key as numeric; natural
  String or UUID identifiers remain String or UUID throughout the repository API.
- Do not suppress invalid repositories through scanning exclusions; repository creation must succeed when the full Spring application context loads.
- Create the contracted file, then finish immediately.

{declarations}
"""


def render_persistence_mapping_prompt(spec: JobSpec, erd: str, contracts: str) -> str:
    return f"""# Implementation task: BCE/JPA persistence mapping

Create a stateless mapper and focused unit tests between immutable BCE domain entities and the JPA persistence entities.

Rules:
- Mapper package: `{spec.base_package}.persistence.mapper`.
- Map every scalar property exposed by the exact BCE accessors and persistence entity accessors.
- Map every BCE entity represented in the ERD, including collections and relationships, without
  infinite recursion. Relationship ownership must remain consistent with the ERD.
- Treat an ERD `easydep:erd-origin kind=multivalued` annotation as a collection persistence
  detail of its parent BCE entity, never as an undeclared BCE domain type.
- Never use reflection or assume an accessor absent from the contracts.
- Tests must cover scalar mapping, portfolio/holding mapping, and null handling. Do not mock value objects.
- Ensure date/time values and constructors match the exact parameter types declared in the generated contracts (e.g. java.time types vs domain models).
- Instantiate persistence entities only through a public constructor exposed by the injected entity contracts. The generated entities provide a public no-arg constructor for scalar/setter mapping; never assume package-private or protected access.
- Do not edit BCE or persistence entity files. Create both contracted files, then finish immediately.

## ERD

```plantuml
{erd}
```

## Exact BCE contracts

```java
{contracts}
```
"""


def render_persistence_schema_prompt(spec: JobSpec, erd: str) -> str:
    return f"""# Implementation task: ERD schema migration

Create the initial Flyway SQL migration. The runtime derives the accompanying
Flyway/H2 metadata smoke test deterministically from your declared tables.

Rules:
- Use lower snake_case table and column names matching the ERD.
- Keep ordinary table names unquoted so H2 and Hibernate resolve the same
  identifier; quote only a genuinely reserved identifier and use that exact
  quoted name consistently in JPA and every migration reference.
- Use the exact ERD column types. A generated identity primary key is BIGINT only when
  the ERD declares a numeric surrogate key; preserve VARCHAR/UUID natural primary keys
  and their foreign-key types. Use INTEGER for quantity, DOUBLE PRECISION for prices,
  BOOLEAN, and TIMESTAMP WITH TIME ZONE where declared.
- Declare every ERD foreign key and useful indexes for foreign-key columns and explicit natural identifiers.
- Match the exact `@Table`, `@Column`, and `@JoinColumn` names derived from the ERD.
- Avoid unquoted SQL/H2 reserved words as identifiers (such as `year`, `order`, `group`, `user`, `status`, `key`, `value`, `offset`, `limit`, `check`, `date`). When a column or table name is a reserved keyword, quote it (e.g. `"year"` or `\"year\"`) or use safe column names consistent with JPA entity `@Column(name = ...)`.
- Do not create or edit the schema test; it is generated after your migration so JDBC identifier-case behavior cannot make the task fail spuriously.
- Create the migration file, then finish immediately.

## ERD

```plantuml
{erd}
```
"""


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


def parse_openapi_operations(source: str) -> list[str]:
    lines = source.splitlines()
    operations: list[str] = []
    in_paths = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "paths:":
            in_paths = True
            index += 1
            continue
        if in_paths and line and not line.startswith(" "):
            break
        path_match = re.match(r"^  (/[^:]+):\s*$", line) if in_paths else None
        if not path_match:
            index += 1
            continue
        path_name = path_match.group(1)
        path_start = index
        index += 1
        while index < len(lines):
            if re.match(r"^  /[^:]+:\s*$", lines[index]) or (lines[index] and not lines[index].startswith(" ")):
                break
            method_match = re.match(r"^    ([a-z]+):\s*$", lines[index])
            if method_match and method_match.group(1) in HTTP_METHODS:
                method = method_match.group(1).upper()
                start = index
                index += 1
                while index < len(lines) and not re.match(r"^    [a-z]+:\s*$", lines[index]) and not re.match(r"^  /[^:]+:\s*$", lines[index]) and (not lines[index] or lines[index].startswith(" ")):
                    index += 1
                operations.append(f"# {method} {path_name}\n" + "\n".join(lines[start:index]))
                continue
            index += 1
        if index == path_start:
            index += 1
    return operations


def select_openapi_operations(control: str, body: str, operations: list[str]) -> str:
    method_names = re.findall(r"(?m)^\s*\+\s*([A-Za-z_]\w*)\s*\(", body)
    tokens = set(split_words(control))
    for method in method_names:
        tokens.update(split_words(method))
    tokens -= STOP_WORDS
    selected = [
        operation for operation in operations
        if tokens & set(re.findall(r"[a-z]+", operation.lower()))
    ]
    return "\n\n".join(selected) if selected else "# No directly matched OpenAPI operation"


def slice_sequence(source: str, names: set[str]) -> str:
    lines = source.splitlines()
    selected: list[str] = []
    active_blocks: list[str] = []
    emitted_blocks: set[str] = set()
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("alt "):
            active_blocks.append(stripped)
            continue
        if stripped.startswith("else ") and active_blocks:
            active_blocks[-1] = stripped
            continue
        if stripped == "end" and active_blocks:
            active_blocks.pop()
            continue
        if any(re.search(rf"\b{re.escape(name)}\b", raw) for name in names) and re.search(r"(?:->|-->)", raw):
            for block in active_blocks:
                if block not in emitted_blocks:
                    selected.append(f"' enclosing branch: {block}")
                    emitted_blocks.add(block)
            selected.append(stripped)
    return "\n".join(selected) if selected else "' No directly matched sequence messages"


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


def render_prompt(spec: JobSpec, context: dict[str, object], allowed: list[str]) -> str:
    repositories = list(context.get("repositories", []))
    repository_rule = (
        "- Use persistence only through these ERD-derived Spring Data repositories: "
        + ", ".join(f"`{name}`" for name in repositories)
        + ". Do not invent repository names or custom persistence fields.\n"
        if repositories
        else "- This Control has no related persistent Entity in the ERD. Do not import, name, or infer a repository or persistence adapter.\n"
    )
    control_rules = (
        "- Implement all public operations defined in the Control contract.\n"
        + repository_rule
        + "- Treat each repository's generic ID type and the corresponding Entity ID "
        "accessor type as authoritative. Preserve incoming BCE identifier types; "
        "never parse or coerce a String identifier to Long or another inferred type.\n"
        + "- Ensure clean domain logic with proper state transitions and no dummy fallbacks."
    )
    return f"""# Implementation task: {context['control']}

Implement the application behavior for `{context['control']}` using only the scoped contracts below.

Rules:
- Write only these files: {', '.join(f'`{path}`' for path in allowed)}.
- Treat `{spec.base_package}.bce`, `{spec.base_package}.api`, and `{spec.base_package}.api.model` as immutable generated contracts.
- Generated BCE Controls are application port interfaces. Implement the matching Control interface in the requested service and inject other Control/Boundary ports through its constructor.
- Produce valid Java syntax only. In particular, use `//` or `/* */` comments, never `#` comments.
- Use the exact Java signatures below. Do not invent a signature to work around a sequence conflict.
- Never call a method absent from the contracts or assign the result of a `void` method. Mockito tests must not use `when(...).thenReturn(...)` for `void` methods.
- Java has no import aliases. For duplicate simple names across API and BCE packages, use a fully qualified name; never invent a `Bce...` alias.
- Do not access private generated fields or use reflection to bypass a contract.
- Never assume conventional getters or setters; use only exact public accessors shown below.
- Some generated contracts may intentionally be empty placeholders because the design omitted
  their declaration. Those contracts are immutable: never infer fields, getters, setters, or
  constructors from a similarly named entity/API schema, and never add members to them.
- If an operation needs data that an empty contract cannot expose, record the limitation in the
  implementation plan/report and keep the generated code compilable; do not fabricate accessor
  calls in production code or Mockito tests.
- The writable parent directories are already created and write-tested. Do not browse directories; create the two requested files directly using the enforced absolute paths appended to this prompt.
- Do not invent public API operations or persistence fields. Do not leave TODO, FIXME, placeholders, or speculative comments; unresolved design gaps belong in the planner's structured report.
- Prefer constructor injection and keep orchestration in the application implementation, not generated DTO/entity files.
{control_rules}
- Do not attempt shell commands. After you finish editing, the orchestrator will run `gradle compileJava test --no-daemon` and reject changes that fail verification.
- After creating both files, finish immediately. If a correction is needed, view once and apply a small exact replacement; do not try to recreate an existing file.

## BCE context

```plantuml
{context['bce']}
```

## Sequence context

```plantuml
{context['sequence']}
```

## ERD context

```plantuml
{context['erd']}
```

## OpenAPI context

```yaml
{context['openapi']}
```

## Exact generated Java contracts

```java
{context['generatedJavaContracts']}
```

## Empty generated contracts (authoritative)

{', '.join(context.get('emptyGeneratedContracts', [])) or 'None'}

The list above is derived from the exact Java sources. An empty contract has no callable members.
Any test that calls a getter on one is invalid and will fail compilation.
"""


def split_words(value: str) -> list[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.findall(r"[a-z]+", expanded.lower())


def camel_to_kebab(value: str) -> str:
    return "-".join(split_words(value))


def _read(path: Path | None) -> str:
    return path.read_text(encoding="utf-8") if path and path.is_file() else ""


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
    for name in sorted(names):
        path = package_root / "bce" / f"{name}.java"
        if path.is_file():
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


def find_empty_java_contracts(contracts: str) -> list[str]:
    """Return generated contract names whose class body has no members.

    This deliberately operates on the exact source block passed to the implementation agent,
    rather than inferring a DTO shape from OpenAPI or a domain entity.  Empty BCE placeholders
    are valid immutable contracts and must not be given speculative accessors by the agent.
    """
    names: list[str] = []
    pattern = re.compile(
        r"(?ms)\bpublic\s+(?:final\s+)?class\s+"
        r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b[^\{]*\{\s*\}"
    )
    for match in pattern.finditer(contracts):
        names.append(match.group("name"))
    return sorted(dict.fromkeys(names))


def referenced_openapi_model_names(openapi_context: str) -> set[str]:
    return set(
        re.findall(r"#/components/schemas/([A-Za-z_$][A-Za-z0-9_$]*)", openapi_context)
    )
