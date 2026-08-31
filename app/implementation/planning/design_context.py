from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import settings

from ..domain.implementation_ir import (
    ApiOperationIR,
    ApiPortIR,
    ComponentIR,
    ImplementationIR,
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
    # Use-case work units can share an adapter or an Entity body.  The
    # coordinator uses this explicit order instead of relying on task ids.
    depends_on: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    use_case_ids: list[str] = field(default_factory=list)
    required_test_paths: list[str] = field(default_factory=list)

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
    requirement_ids = _ids_for_components(spec, set(entity_names))
    requirements, use_cases, requirement_sources = _related_requirement_artifacts(
        spec, requirement_ids
    )
    context = {
        "schemaVersion": "implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "persistence",
        "implementationIR": ir.to_dict(),
        "erd": erd,
        "bceEntities": entity_names,
        "generatedJavaContracts": contracts,
        "requirements": requirements,
        "useCaseArtifacts": use_cases,
        "scenarios": _scenarios_for_use_cases(spec, requirement_ids),
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
        "## Related persistence requirements and scenarios\n```json\n"
        + _prompt_json({"requirements": requirements, "useCases": use_cases})
        + "\n```\n\n"
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
            if name in {"bceClass", "erd", "erdBceModel", *requirement_sources}
            and path.is_file()
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="persistence",
    )
    (output / "shared-persistence.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return [task]


@dataclass(frozen=True)
class _UseCaseBundle:
    use_case_ids: tuple[str, ...]
    components: tuple[ComponentIR, ...]
    ports: tuple[ApiPortIR, ...]
    endpoints: tuple[dict[str, object], ...]


def generate_api_adapter_tasks(spec: JobSpec, run_root: Path) -> list[ImplementationTask]:
    """Control/Entity를 공유하는 유스케이스를 최대 세 개씩 구현 단위로 묶는다."""
    package_path = spec.base_package.replace(".", "/")
    java_root = run_root / "application" / "src" / "main" / "java" / package_path
    ir = build_implementation_ir(spec, run_root)
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    endpoints = _api_model_endpoints(spec)
    component_ids = _component_use_case_ids(spec)
    bundles = _use_case_bundles(ir, component_ids, endpoints)
    bce_paths = [
        path.relative_to(run_root).as_posix()
        for path in sorted((java_root / "bce").rglob("*.java"))
    ]
    tasks: list[ImplementationTask] = []
    writers: dict[str, str] = {}
    for index, bundle in enumerate(bundles, start=1):
        task = _build_use_case_task(
            spec, run_root, ir, output, package_path, bce_paths, bundle, writers, index
        )
        tasks.append(task)
        writers.update(dict.fromkeys(task.allowed_write_paths, task.task_id))
    return tasks


def _use_case_bundles(
    ir: ImplementationIR,
    component_ids: dict[str, set[str]],
    endpoints: list[dict[str, object]],
) -> list[_UseCaseBundle]:
    """Control 연결을 우선하고, 작은 singleton만 Entity로 합친다."""
    for endpoint in endpoints:
        binding = endpoint.get("control_binding")
        control = binding.get("control") if isinstance(binding, dict) else None
        if isinstance(control, str) and control in ir.controls:
            component_ids.setdefault(control, set()).update(_use_case_ids(endpoint))

    all_ids: set[str] = set()
    for ids in component_ids.values():
        all_ids.update(ids)
    for endpoint in endpoints:
        all_ids.update(_use_case_ids(endpoint))
    links = {use_case_id: {use_case_id} for use_case_id in all_ids}
    for component in ir.components:
        if component.stereotype.casefold() != "control":
            continue
        related = component_ids.get(component.name, set())
        for use_case_id in related:
            links.setdefault(use_case_id, {use_case_id}).update(related)

    groups: list[tuple[str, ...]] = []
    pending = set(all_ids)
    while pending:
        root = min(pending, key=_use_case_sort_key)
        group: set[str] = set()
        frontier = [root]
        while frontier:
            use_case_id = frontier.pop()
            if use_case_id in group:
                continue
            group.add(use_case_id)
            frontier.extend(links.get(use_case_id, {use_case_id}) - group)
        pending.difference_update(group)
        ordered = sorted(group, key=_use_case_sort_key)
        groups.extend(
            tuple(ordered[offset:offset + 3])
            for offset in range(0, len(ordered), 3)
        )

    groups = _merge_entity_singletons(groups, ir, component_ids)

    bundles = [_bundle_for_ids(ir, component_ids, endpoints, group) for group in groups]
    assigned_components = {item.name for bundle in bundles for item in bundle.components}
    assigned_ports = {item.name for bundle in bundles for item in bundle.ports}
    common_components = tuple(
        item
        for item in ir.components
        if item.name not in assigned_components and _is_work_component(item)
    )
    common_ports = tuple(item for item in ir.api_ports if item.name not in assigned_ports)
    if common_components or common_ports:
        bundles.append(_UseCaseBundle((), common_components, common_ports, ()))
    planned = [
        bundle for bundle in bundles
        if bundle.ports or any(_is_work_component(item) for item in bundle.components)
    ]
    return sorted(
        planned,
        key=lambda bundle: _use_case_sort_key(bundle.use_case_ids[0])
        if bundle.use_case_ids
        else (10**9, "common"),
    )


def _merge_entity_singletons(
    groups: list[tuple[str, ...]],
    ir: ImplementationIR,
    component_ids: dict[str, set[str]],
) -> list[tuple[str, ...]]:
    """Control이 없는 작은 흐름만 같은 Entity 기준으로 최대 세 개까지 합친다."""
    merged_groups = list(groups)
    for entity in ir.components:
        if entity.stereotype.casefold() != "entity":
            continue
        related = component_ids.get(entity.name, set())
        indices = [
            index for index, group in enumerate(merged_groups)
            if len(group) == 1 and group[0] in related
        ]
        values = {item for index in indices for item in merged_groups[index]}
        if not 1 < len(values) <= 3:
            continue
        first = indices[0]
        merged_groups = [
            group for index, group in enumerate(merged_groups) if index not in indices
        ]
        merged_groups.insert(first, tuple(sorted(values, key=_use_case_sort_key)))
    return merged_groups


def _bundle_for_ids(
    ir: ImplementationIR,
    component_ids: dict[str, set[str]],
    endpoints: list[dict[str, object]],
    use_case_ids: tuple[str, ...],
) -> _UseCaseBundle:
    wanted = set(use_case_ids)
    selected_endpoints = tuple(
        endpoint for endpoint in endpoints if _use_case_ids(endpoint) & wanted
    )
    components = tuple(
        item for item in ir.components if component_ids.get(item.name, set()) & wanted
    )
    ports = tuple(
        port
        for port in ir.api_ports
        if any(
            _operation_matches_endpoint(operation, endpoint)
            for operation in port.operations
            for endpoint in selected_endpoints
        )
    )
    return _UseCaseBundle(use_case_ids, components, ports, selected_endpoints)


def _is_work_component(component: ComponentIR) -> bool:
    return component.stereotype.casefold() in {
        "control", "boundary", "entity", "gateway",
    }


def _build_use_case_task(
    spec: JobSpec,
    run_root: Path,
    ir: ImplementationIR,
    output: Path,
    package_path: str,
    bce_paths: list[str],
    bundle: _UseCaseBundle,
    writers: dict[str, str],
    index: int,
) -> ImplementationTask:
    """선택한 설계 slice와 필수 JUnit 클래스를 하나의 작업 계약으로 만든다."""
    label = ", ".join(bundle.use_case_ids) or "common"
    suffix = "-".join(item.casefold() for item in bundle.use_case_ids) or "common"
    task_id = f"implement-use-cases-{suffix}"
    test_path = (
        f"application/src/test/java/{package_path}/application/impl/"
        f"UseCaseBundle{index}Test.java"
    )
    controls = [
        item.name for item in bundle.components if item.stereotype.casefold() == "control"
    ]
    boundaries = [
        item.name for item in bundle.components if item.stereotype.casefold() == "boundary"
    ]
    entities = [
        item.name for item in bundle.components if item.stereotype.casefold() == "entity"
    ]
    gateway_kinds = {item.name: item.kind for item in ir.gateways}
    gateways = [item for item in bundle.components if item.name in gateway_kinds]
    required = sorted({
        *(
            f"application/src/main/java/{package_path}/application/impl/{name}Service.java"
            for name in controls
        ),
        *(
            f"application/src/main/java/{package_path}/adapter/in/web/{port.name}ApiController.java"
            for port in bundle.ports
        ),
        *(
            f"application/src/main/java/{package_path}/adapter/in/boundary/{name}Adapter.java"
            for name in boundaries
        ),
        *(
            _gateway_adapter_path(package_path, item.name, gateway_kinds[item.name])
            for item in gateways
        ),
        test_path,
    })
    entity_sources = [
        f"application/src/main/java/{package_path}/bce/{name}.java" for name in entities
    ]
    editable = _work_unit_editable_paths(run_root, required, entity_sources)
    depends_on = sorted({writers[path] for path in editable if path in writers})
    requirements, use_cases, sources = _related_requirement_artifacts(
        spec, bundle.use_case_ids
    )
    scenarios = _scenarios_for_use_cases(spec, bundle.use_case_ids)
    component_names = {item.name for item in bundle.components}
    contracts = read_generated_java_contracts(
        run_root,
        spec.base_package,
        component_names,
        _endpoint_model_names(bundle.endpoints),
    )
    controller_paths = [run_root / path for path in required if "/adapter/in/web/" in path]
    scaffolds = render_source_contracts(run_root, controller_paths)
    context = {
        "schemaVersion": "implementation-context/v1alpha2",
        "taskId": task_id,
        "taskType": "use-case",
        "dependsOn": depends_on,
        "requirementIds": _artifact_ids(requirements),
        "useCaseIds": list(bundle.use_case_ids),
        "requirements": requirements,
        "useCaseArtifacts": use_cases,
        "scenarios": scenarios,
        "apiEndpoints": list(bundle.endpoints),
        "implementationIR": ir.to_dict(),
        "generatedJavaContracts": contracts,
        "controllerScaffolds": scaffolds,
        "controllerBodyPaths": [
            path.relative_to(run_root).as_posix()
            for path in controller_paths
            if path.is_file()
        ],
        "entityBodySources": entity_sources,
        "requiredOutputs": required,
        "requiredTestPaths": [test_path],
    }
    deployment_context = _deployment_context(spec, component_names)
    if deployment_context:
        context["deployment"] = deployment_context
    context_path = output / f"{task_id}.context.json"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    prompt = f"""# Application use-case bundle: {label}

Implement only this design-backed bundle after persistence. Do not invent behavior outside
the supplied requirements, use-case scenarios, BCE, and OpenAPI contracts.

- BCE Entity sources may change only method bodies; preserve every public declaration.
- Generated API interfaces and Controller declarations are immutable; replace only a
  Controller body sentinel.
- Use the completed ERD repositories for persistent behavior; do not keep business state in
  an in-memory collection or invent another persistence port.
- Write the focused JUnit scenario first, then implement until it passes. Assert returned
  values and persisted state changes, including that rejected requests leave state unchanged.
- Leave no TODO, FIXME, or placeholder.

## Relevant requirements and scenarios
~~~json
{_prompt_json({"requirements": requirements, "useCases": use_cases, "scenarios": scenarios})}
~~~

## Exact generated Java contracts
~~~java
{contracts}
~~~

## Deterministic Controller scaffolds
~~~java
{scaffolds}
~~~
""" + _render_deployment_context(deployment_context) + render_allowed_output_rules(required)
    prompt_path = output / f"{task_id}.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = ImplementationTask(
        task_id=task_id,
        control=f"use cases {label}",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=editable,
        required_output_paths=required,
        immutable_paths=[
            *(path for path in bce_paths if path not in entity_sources),
            f"application/src/main/java/{package_path}/api",
        ],
        source_artifacts=sources,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="use-case",
        depends_on=depends_on,
        requirement_ids=_artifact_ids(requirements),
        use_case_ids=list(bundle.use_case_ids),
        required_test_paths=[test_path],
    )
    (output / f"{task_id}.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return task


def _component_use_case_ids(spec: JobSpec) -> dict[str, set[str]]:
    classes = _read_json(spec.inputs.get("bceModel")).get("Classes", [])
    if not isinstance(classes, list):
        return {}
    return {
        str(item["className"]): _use_case_ids(item)
        for item in classes
        if isinstance(item, dict) and item.get("className")
    }


def _ids_for_components(spec: JobSpec, names: set[str]) -> tuple[str, ...]:
    ids = set().union(*(
        use_case_ids
        for component, use_case_ids in _component_use_case_ids(spec).items()
        if component in names
    )) if names else set()
    return tuple(sorted(ids, key=_use_case_sort_key))


def _api_model_endpoints(spec: JobSpec) -> list[dict[str, object]]:
    endpoints = _read_json(spec.inputs.get("apiModel")).get("Endpoints", [])
    return [
        item for item in endpoints if isinstance(item, dict)
    ] if isinstance(endpoints, list) else []


def _operation_matches_endpoint(
    operation: ApiOperationIR, endpoint: dict[str, object]
) -> bool:
    return (
        operation.method.casefold() == str(endpoint.get("method") or "").casefold()
        and operation.path == str(endpoint.get("path") or "")
        and (
            not operation.operation_id
            or operation.operation_id == endpoint.get("operation_id")
        )
    )


def _endpoint_model_names(endpoints: tuple[dict[str, object], ...]) -> set[str]:
    names: set[str] = set()
    for endpoint in endpoints:
        request = str(endpoint.get("request_schema") or "")
        if request:
            names.add(request.rsplit("/", maxsplit=1)[-1])
        responses = endpoint.get("responses")
        if isinstance(responses, list):
            names.update(
                str(response.get("schema_name"))
                for response in responses
                if isinstance(response, dict) and response.get("schema_name")
            )
    return names


def _use_case_ids(item: dict[str, object]) -> set[str]:
    values = item.get("use_case_ids") or item.get("useCaseIds") or []
    result = {str(value) for value in values if str(value)} if isinstance(values, list) else set()
    for name in ("use_case_id", "useCaseId"):
        if item.get(name):
            result.add(str(item[name]))
    return result


def _use_case_sort_key(value: str) -> tuple[int, str]:
    match = re.search(r"(\d+)$", value)
    return (int(match.group(1)) if match else 10**9, value)


def _gateway_adapter_path(package_path: str, name: str, kind: str) -> str:
    directory = "persistence" if kind == "persistence" else "gateway"
    adapter = name if kind == "persistence" else f"InMemory{name}"
    return f"application/src/main/java/{package_path}/adapter/out/{directory}/{adapter}Adapter.java"


def _related_requirement_artifacts(
    spec: JobSpec, use_case_ids: tuple[str, ...]
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    wanted = set(use_case_ids)
    requirements, use_cases, sources = _all_requirement_artifacts(spec)
    selected_use_cases = [
        item for item in use_cases if _use_case_ids(item) & wanted
    ]
    requirement_ids = {
        str(value)
        for item in selected_use_cases
        for field in ("requirement_ids", "nfr_ids")
        for value in (item.get(field) if isinstance(item.get(field), list) else [])
        if str(value)
    }
    requirement_ids.update(_constraint_requirement_ids(spec, wanted))
    return (
        [
            item
            for item in requirements
            if str(item.get("id") or "") in requirement_ids
            or bool(_use_case_ids(item) & wanted)
        ],
        selected_use_cases,
        sources,
    )


def _constraint_requirement_ids(spec: JobSpec, use_case_ids: set[str]) -> set[str]:
    """추적표가 해당 UC 또는 전체 시스템 조건으로 표시한 요구사항 ID를 읽는다."""
    path = spec.inputs.get("useCaseSpec")
    traceability = _read_json(path).get("traceability")
    requirements = traceability.get("requirements") if isinstance(traceability, dict) else None
    if not isinstance(requirements, dict):
        return set()
    result: set[str] = set()
    for requirement_id, raw in requirements.items():
        if not isinstance(raw, dict) or raw.get("modeled_as_constraint") is not True:
            continue
        constrained = {
            str(item)
            for field in ("use_cases", "constrains_use_cases")
            for item in (raw.get(field) if isinstance(raw.get(field), list) else [])
        }
        if not constrained or constrained & use_case_ids:
            result.add(str(requirement_id))
    return result


def _all_requirement_artifacts(
    spec: JobSpec,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    requirements, requirement_sources = _job_artifact_items(
        spec,
        {"requirements", "refinedrequirements"},
        ("requirements", "refinedRequirements", "refined_requirements"),
    )
    use_cases, use_case_sources = _job_artifact_items(
        spec,
        {"usecases", "usecasespecs", "usecasespec"},
        ("useCases", "useCaseSpecs", "use_case_specs"),
    )
    names = {
        "bceClass", "bceModel", "sequenceModel", "apiModel", "erd", "openapi",
        *requirement_sources, *use_case_sources,
    }
    return requirements, use_cases, {
        name: str(path)
        for name, path in spec.inputs.items()
        if name in names and path.is_file()
    }


def _job_artifact_items(
    spec: JobSpec, input_names: set[str], fields: tuple[str, ...]
) -> tuple[list[dict[str, object]], set[str]]:
    result: list[dict[str, object]] = []
    sources: set[str] = set()
    for name, path in spec.inputs.items():
        if re.sub(r"[^a-z]", "", name.casefold()) not in input_names:
            continue
        value = _read_json_value(path)
        candidates = value if isinstance(value, list) else next(
            (
                value.get(field, [])
                for field in fields
                if isinstance(value, dict) and field in value
            ),
            [],
        )
        items = [
            item for item in candidates if isinstance(item, dict)
        ] if isinstance(candidates, list) else []
        if items:
            result.extend(items)
            sources.add(name)
    return result, sources


def _artifact_ids(items: list[dict[str, object]]) -> list[str]:
    return sorted({str(item["id"]) for item in items if item.get("id")})


def _scenarios_for_use_cases(
    spec: JobSpec, use_case_ids: tuple[str, ...]
) -> list[dict[str, object]]:
    diagrams = _read_json(spec.inputs.get("sequenceModel")).get("Diagrams", [])
    wanted = set(use_case_ids)
    return [
        item for item in diagrams
        if isinstance(item, dict) and item.get("use_case_id") in wanted
    ] if isinstance(diagrams, list) else []


def _all_scenarios(spec: JobSpec) -> list[dict[str, object]]:
    diagrams = _read_json(spec.inputs.get("sequenceModel")).get("Diagrams", [])
    return [item for item in diagrams if isinstance(item, dict)] if isinstance(diagrams, list) else []


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
    flow_test_path = (
        "application/src/test/java/"
        f"{package_path}/integration/"
        f"{ir.application_class.removesuffix('Application')}FlowTest.java"
    )
    required = [
        f"application/src/main/java/{package_path}/config/ApplicationConfiguration.java",
        "application/src/main/resources/application.yml",
        "application/src/test/resources/application-test.yml",
        f"application/src/test/java/{package_path}/config/ApplicationContextTest.java",
        flow_test_path,
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
    requirements, use_cases, requirement_sources = _all_requirement_artifacts(spec)
    context = {
        "schemaVersion": "implementation-context/v1alpha1",
        "taskId": task_id,
        "taskType": "wiring",
        "implementationIR": ir.to_dict(),
        "generatedJavaContracts": contracts,
        "applicationClass": ir.application_class,
        "requirements": requirements,
        "useCaseArtifacts": use_cases,
        "scenarios": _all_scenarios(spec),
        "e2eScenarios": [asdict(item) for item in ir.e2e_scenarios],
        "openapi": _read(spec.inputs.get("openapi")),
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
        "\n\nIn the single real HTTP FlowTest class, cover every planned use-case bundle. "
        "For each applicable scenario, assert the documented response and the required "
        "persisted result; also exercise the authorization and concurrency conditions stated "
        "in the supplied requirements. A single status-only endpoint check is insufficient."
    )
    prompt += "\n\n## All refined requirements and use-case scenarios\n```json\n" + _prompt_json({
        "requirements": requirements,
        "useCases": use_cases,
        "scenarios": context["scenarios"],
    }) + "\n```"
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
                *requirement_sources,
            }
            and path.is_file()
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=_llm_config(spec),
        task_type="wiring",
        # 최종 검증은 이 목록과 유스케이스 작업이 실제로 덮은 목록을 비교한다. 추적이
        # 빠진 유스케이스가 있어도 일부 테스트만 통과해 릴리스되는 일을 막는다.
        use_case_ids=_artifact_ids(use_cases),
        required_test_paths=[flow_test_path],
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
- Configure the production datasource in `application.yml` from `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, and `SPRING_DATASOURCE_PASSWORD`. Never store a credential in source and never silently fall back to an in-memory production database.
- Put the H2 in-memory datasource only in `src/test/resources/application-test.yml`, and activate that profile explicitly in tests.
- If the supplied requirements protect operations by identity or role, configure Spring Security with an environment-provided production identity boundary and local identities only in the test profile. The FlowTest must reject unauthenticated and wrong-role requests. If no authorization requirement exists, configure the filter chain to permit the documented API instead of accepting Spring Security's generated password behavior.
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
    value = _read_json_value(path)
    return value if isinstance(value, dict) else {}


def _read_json_value(path: Path | None) -> object:
    """요구사항 artifact처럼 최상위 list인 선택 입력도 보존한다."""
    try:
        return json.loads(_read(path))
    except json.JSONDecodeError:
        return {}


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
