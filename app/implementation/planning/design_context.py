from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.artifact_trace import TraceRef
from app.artifact_trace_projection import project_artifact_trace
from app.config import settings
from app.design.schemas.class_model import BCEModel
from app.design.schemas.sequence_model import SequenceCollection
from app.llm_connection import build_llm_connection

from ..domain.implementation_ir import (
    ApiPortIR,
    ComponentIR,
    ImplementationIR,
    build_implementation_ir,
)
from ..domain.models import JobSpec
from ..generation.frontend_scaffold import frontend_page_names, operation_ids
from ..generation.java_scaffold import controller_body_marker
from .frontend_contracts import GeneratedClientContracts
from .method_projection import MethodProjection, MethodProjectionResult, project_method_calls


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    control: str
    prompt_file: str
    context_file: str
    allowed_write_paths: list[str]
    immutable_paths: list[str]
    source_artifacts: dict[str, str]
    prompt_sha256: str
    llm: dict[str, object]
    owner: str
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
    # task가 직접 소비하는 설계 주소다. RTM은 이 값을 다시 추측하지 않고 그대로 옮긴다.
    source_refs: list[str] = field(default_factory=list)
    # Spring 설정은 정상 경로에서 코드가 만든다. 이 작업은 최종 build나 HTTP 검사에서
    # 실제 연결 문제가 발견됐을 때만 OpenHands에게 넘기는 수리용 작업이다.
    repair_only: bool = False
    # 다른 기능 작업과 겹치지 않는 package만 새 파일 생성을 허용한다. 기존 계약 파일은
    # immutable_paths가 계속 보호한다.
    allowed_write_roots: list[str] = field(default_factory=list)
    # Testing feedback 작업만 사용한다. 실패 당시 고정한 OpenAPI·case 등을 같은
    # run_task_check에 넘겨 수리 전후 검사가 달라지지 않게 한다.
    verification_profile: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.required_output_paths is None:
            object.__setattr__(self, "required_output_paths", list(self.allowed_write_paths))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _UseCaseBundle:
    use_case_ids: tuple[str, ...]
    components: tuple[ComponentIR, ...]
    ports: tuple[ApiPortIR, ...]
    endpoints: tuple[dict[str, object], ...]


def generate_backend_owner_tasks(spec: JobSpec, run_root: Path) -> list[TaskSpec]:
    """전체 backend 구현을 맡는 단일 owner 작업을 만든다."""
    package_path = spec.base_package.replace(".", "/")
    java_root = run_root / "application" / "src" / "main" / "java" / package_path
    ir = build_implementation_ir(spec, run_root)
    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    endpoints = _api_model_endpoints(spec)
    _requirements, use_cases, _sources = _all_requirement_artifacts(spec)
    component_ids = _component_use_case_ids(spec)
    use_case_ids = {
        *_artifact_ids(use_cases),
        *(value for values in component_ids.values() for value in values),
        *(value for endpoint in endpoints for value in _use_case_ids(endpoint)),
    }
    bundle = _UseCaseBundle(
        tuple(sorted(use_case_ids, key=_use_case_sort_key)),
        tuple(item for item in ir.components if _is_work_component(item)),
        tuple(ir.api_ports),
        tuple(endpoints),
    )
    bce_paths = [
        path.relative_to(run_root).as_posix()
        for path in sorted((java_root / "bce").rglob("*.java"))
    ]
    task = _build_backend_owner_task(
        spec, run_root, ir, output, package_path, bce_paths, bundle
    )
    (output / f"{task.task_id}.task.json").write_text(
        json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [task]


def _is_work_component(component: ComponentIR) -> bool:
    return component.stereotype.casefold() in {
        "control",
        "boundary",
        "entity",
        "gateway",
    }


def _build_backend_owner_task(
    spec: JobSpec,
    run_root: Path,
    ir: ImplementationIR,
    output: Path,
    package_path: str,
    bce_paths: list[str],
    bundle: _UseCaseBundle,
) -> TaskSpec:
    """전체 backend 계약과 owner 범위를 하나의 작업으로 만든다."""
    label = ", ".join(bundle.use_case_ids) or "common"
    task_id = "implement-backend-application"
    test_path = (
        f"application/src/test/java/{package_path}/application/impl/BackendApplicationTest.java"
    )
    controls = [item.name for item in bundle.components if item.stereotype.casefold() == "control"]
    entities = [item.name for item in bundle.components if item.stereotype.casefold() == "entity"]
    gateway_kinds = {item.name: item.kind for item in ir.gateways}
    gateways = [item for item in bundle.components if item.name in gateway_kinds]
    required = sorted(
        {
            *(
                f"application/src/main/java/{package_path}/application/impl/{name}Service.java"
                for name in controls
            ),
            *(
                f"application/src/main/java/{package_path}/adapter/in/web/{port.name}ApiController.java"
                for port in bundle.ports
            ),
            *(
                _gateway_adapter_path(package_path, item.name, gateway_kinds[item.name])
                for item in gateways
            ),
            test_path,
        }
    )
    entity_sources = [
        f"application/src/main/java/{package_path}/bce/{name}.java" for name in entities
    ]
    # persistence 골격은 LLM 작업보다 먼저 생성되고 이후 작업이 수정하지 않는다. 관련
    # Entity와 Repository는 source index가 가리키는 정확한 파일에서 필요한 선언만 읽는다.
    persistence_sources = [
        path
        for name in entities
        for path in (
            f"application/src/main/java/{package_path}/persistence/entity/{name}Entity.java",
            f"application/src/main/java/{package_path}/persistence/repository/{name}Repository.java",
        )
        if (run_root / path).is_file()
    ]
    dependency_source_paths = [path for path in entity_sources if (run_root / path).is_file()]
    owner_roots = [
        f"application/src/main/java/{package_path}",
        f"application/src/test/java/{package_path}",
        "application/src/main/resources",
        "application/src/test/resources",
    ]
    owner_files = [
        path
        for path in (
            "application/build.gradle",
            "application/settings.gradle",
            "application/gradle.properties",
        )
        if (run_root / path).is_file()
    ]
    editable = _work_unit_editable_paths(
        run_root,
        required,
        [*owner_roots, *owner_files],
    )
    immutable_paths = [
        *(path for path in bce_paths if path not in entity_sources),
        f"application/src/main/java/{package_path}/api",
        f"application/src/main/java/{package_path}/persistence",
        "application/src/main/resources/db/migration",
    ]
    editable = _without_immutable_paths(editable, immutable_paths)
    requirements, use_cases, sources = _all_requirement_artifacts(spec)
    # HTTP Controller는 Boundary adapter를 거치지 않고 typed Control을 직접 호출한다.
    # Boundary가 참조하는 다른 기능 DTO까지 closure에 끌어오지 않고 이번 구현에 실제로
    # 쓰는 Control·Entity·Gateway 계약만 전달한다.
    component_names = {
        item.name for item in bundle.components if item.stereotype.casefold() != "boundary"
    }
    controller_paths = [run_root / path for path in required if "/adapter/in/web/" in path]
    scaffolds = render_source_contracts(run_root, controller_paths)
    dependency_source_paths.extend(
        path.relative_to(run_root).as_posix() for path in controller_paths if path.is_file()
    )
    controller_markers = sorted(
        {
            marker
            for endpoint in bundle.endpoints
            for marker in [
                controller_body_marker(
                    str(endpoint.get("method") or ""),
                    str(endpoint.get("path") or ""),
                )
            ]
            if endpoint.get("method") and endpoint.get("path")
            if marker in scaffolds
        }
    )
    design_inputs = _materialize_design_inputs(
        spec,
        run_root,
        {
            "bceClass",
            "bceModel",
            "sequence",
            "sequenceModel",
            "apiModel",
            "erdBceModel",
            "erdLogicalModel",
            "requirements",
            "useCaseSpec",
        },
    )
    method_projection = project_method_calls(
        bce_model=BCEModel.model_validate_json(
            spec.inputs["bceModel"].read_text(encoding="utf-8")
        ),
        sequence_model=SequenceCollection.model_validate_json(
            spec.inputs["sequenceModel"].read_text(encoding="utf-8")
        ),
    )
    method_contexts = _materialize_method_contexts(
        run_root,
        output,
        package_path,
        method_projection,
        requirements=requirements,
        use_cases=use_cases,
        endpoints=list(bundle.endpoints),
        design_inputs=design_inputs,
    )
    # The application tree is already copied into every agent sandbox. Keep only a small set of
    # starting paths in the index; do not copy or enumerate the whole Java tree as read sources.
    source_paths = sorted(
        dict.fromkeys(
            path
            for path in [
                *dependency_source_paths,
                *persistence_sources,
                *entity_sources,
                *[
                    path
                    for name in component_names
                    for path in bce_paths
                    if Path(path).stem == name
                ],
            ]
            if (run_root / path).is_file()
        )
    )
    source_index_path = output / f"{task_id}.source-index.json"
    source_index_path.write_text(
        json.dumps(
            {
                "schemaVersion": "implementation-source-index/v1alpha1",
                "taskId": task_id,
                "startingSourcePaths": source_paths,
                "designInputs": design_inputs,
                "methodContexts": method_contexts,
                "hintsOnly": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    context = {
        "schemaVersion": "implementation-context/v1alpha3",
        "taskId": task_id,
        "taskType": "backend-implementation",
        "owner": "backend",
        "dependsOn": [],
        "requirementIds": _artifact_ids(requirements),
        "useCaseIds": list(bundle.use_case_ids),
        "controllerPaths": [
            path.relative_to(run_root).as_posix() for path in controller_paths if path.is_file()
        ],
        "controllerBodyMarkers": controller_markers,
        "readSourcePaths": sorted(
            dict.fromkeys(
                [
                    _relative(run_root, source_index_path),
                    *(str(item["path"]) for item in method_contexts),
                    *design_inputs.values(),
                ]
            )
        ),
        "sourceIndexPath": _relative(run_root, source_index_path),
        "methodContextRoot": _relative(run_root, output / "method-context"),
        "designInputs": design_inputs,
    }
    deployment_context = _deployment_context(spec, component_names)
    if deployment_context:
        context["deployment"] = deployment_context
    context_path = output / f"{task_id}.context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (
        f"""# Backend application owner: {spec.name}

Complete the generated backend implementation and its JUnit scenarios. Start with the existing
Control service and test shells; their typed calls are already projected from the sequence model.

- Preserve frozen BCE/API declarations, persistence projections, repositories, and migrations.
- Resolve every `EASYDEP-IMPLEMENT` and named Controller marker with contracted behavior and
  meaningful assertions. Do not replace them with empty, demo, or always-passing behavior.
- Before repository-wide search, batch-read each marker's `Context` path and its listed source
  paths. The marker ID maps directly to `method-context/<ID>.json`.
- Use existing repositories for persistent behavior and constructor injection for Spring beans.
- Generated web Controllers already call their typed Control binding; do not duplicate HTTP or
  Boundary adapters.
- Read the smallest method context needed for a marker. Read a frozen design input only when the
  local context exposes a real contract gap.
- Source-index, method-context, and RTM references are navigation hints, never read or edit limits.
- Use English for source comments, tests, validation messages, documentation, and user-visible text.

## On-demand implementation context
- Source index: `{_relative(run_root, source_index_path)}`
- Method contexts: `{_relative(run_root, output / "method-context")}`
- Controller markers: {", ".join(controller_markers) or "none"}
"""
        "\n## Backend owner roots\n"
        + "\n".join(f"- `{root}`" for root in owner_roots)
        + render_allowed_output_rules(required)
    )
    prompt_path = output / f"{task_id}.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = TaskSpec(
        task_id=task_id,
        control=f"use cases {label}",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=editable,
        required_output_paths=required,
        immutable_paths=immutable_paths,
        source_artifacts=sources,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=llm_config(spec),
        owner="backend",
        task_type="backend-implementation",
        depends_on=[],
        requirement_ids=_artifact_ids(requirements),
        use_case_ids=list(bundle.use_case_ids),
        required_test_paths=[test_path],
        source_refs=[
            *_operation_source_refs(spec, set(bundle.use_case_ids)),
            *_workload_source_refs(deployment_context),
        ],
        allowed_write_roots=owner_roots,
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


def _api_model_endpoints(spec: JobSpec) -> list[dict[str, object]]:
    endpoints = _read_json(spec.inputs.get("apiModel")).get("Endpoints", [])
    return (
        [item for item in endpoints if isinstance(item, dict)]
        if isinstance(endpoints, list)
        else []
    )


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
        "bceModel",
        "sequenceModel",
        "apiModel",
        "erdBceModel",
        *requirement_sources,
        *use_case_sources,
    }
    return (
        requirements,
        use_cases,
        {name: str(path) for name, path in spec.inputs.items() if name in names and path.is_file()},
    )


def _job_artifact_items(
    spec: JobSpec, input_names: set[str], fields: tuple[str, ...]
) -> tuple[list[dict[str, object]], set[str]]:
    result: list[dict[str, object]] = []
    sources: set[str] = set()
    for name, path in spec.inputs.items():
        if re.sub(r"[^a-z]", "", name.casefold()) not in input_names:
            continue
        value = _read_json_value(path)
        candidates = (
            value
            if isinstance(value, list)
            else next(
                (
                    value.get(field, [])
                    for field in fields
                    if isinstance(value, dict) and field in value
                ),
                [],
            )
        )
        items = (
            [item for item in candidates if isinstance(item, dict)]
            if isinstance(candidates, list)
            else []
        )
        if items:
            result.extend(items)
            sources.add(name)
    return result, sources


def _artifact_ids(items: list[dict[str, object]]) -> list[str]:
    return sorted({str(item["id"]) for item in items if item.get("id")})


def generate_frontend_tasks(spec: JobSpec, run_root: Path) -> list[TaskSpec]:
    """typed 화면 흐름과 생성된 API client를 한 frontend 구현 작업으로 만든다."""
    frontend = run_root / "application" / "frontend"
    generated = frontend / "src" / "generated"
    if not generated.is_dir():
        raise ValueError("OpenAPI Generator frontend client was not found")
    openapi = json.loads(_read(spec.inputs.get("openapi")))
    bce_model = _read_json(spec.inputs.get("bceModel"))
    bce_classes = bce_model.get("Classes", [])
    classes = (
        [item for item in bce_classes if isinstance(item, dict)]
        if isinstance(bce_classes, list)
        else []
    )
    bce_names = {str(item["className"]) for item in classes if item.get("className")}
    pages = frontend_page_names(openapi)
    operations = operation_ids(openapi)
    client_contracts = GeneratedClientContracts.discover(generated)
    call_skeleton, projected_operations = client_contracts.render_call_skeleton(operations)
    call_skeleton_path = frontend / "src" / "api.ts"
    call_skeleton_path.write_text(call_skeleton, encoding="utf-8", newline="\n")

    output = run_root / "reports" / "implementation-tasks"
    output.mkdir(parents=True, exist_ok=True)
    design_inputs = _materialize_design_inputs(
        spec,
        run_root,
        {"bceClass", "bceModel", "sequence", "sequenceModel", "openapi"},
    )
    required = [
        "application/frontend/src/App.tsx",
        "application/frontend/src/api.ts",
        "application/frontend/src/components/AppShell.tsx",
        *[f"application/frontend/src/pages/{name}.tsx" for name in pages],
        "application/frontend/src/styles.css",
    ]
    allowed = _work_unit_editable_paths(
        run_root,
        required,
        ["application/frontend"],
    )
    allowed = _without_immutable_paths(
        allowed,
        ["application/frontend/src/generated"],
    )
    task_id = "implement-frontend-application"
    client_index = _frontend_contract_index(run_root, openapi, pages, client_contracts)
    client_index["callSkeletonPath"] = _relative(run_root, call_skeleton_path)
    client_index["projectedOperations"] = projected_operations
    client_index["unresolvedOperations"] = sorted(set(operations) - set(projected_operations))
    client_index_path = output / "frontend-generated-client-index.json"
    client_index_path.write_text(
        json.dumps(client_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    context = {
        "schemaVersion": "frontend-implementation-context/v1alpha2",
        "taskId": task_id,
        "taskType": "frontend-implementation",
        "owner": "frontend",
        "dependsOn": ["implement-backend-application"],
        "pages": pages,
        "operationIds": operations,
        "generatedImportRoot": client_contracts.import_root,
        "callSkeletonPath": _relative(run_root, call_skeleton_path),
        "clientIndexPath": _relative(run_root, client_index_path),
        "designInputs": design_inputs,
        "requiredOutputs": required,
        "readSourcePaths": sorted(
            dict.fromkeys(
                [
                    *design_inputs.values(),
                    _relative(run_root, client_index_path),
                ]
            )
        ),
    }
    deployment_context = _deployment_context(spec, {"frontend", *bce_names})
    if deployment_context:
        context["deployment"] = deployment_context
    context_path = output / "frontend-application.context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    page_list = "\n".join(f"- `{name}`" for name in pages)
    prompt = f"""# Frontend implementation task: {spec.name}

Complete the React application using the exact generated-client calls already wired in
`application/frontend/src/api.ts`.

- Preserve `{client_contracts.import_root}` and use `apiCalls`; never hand-write HTTP calls or paths.
- Resolve every `EASYDEP-IMPLEMENT` marker. Use the client index first, then read only the smallest
  relevant frozen design input if the local projection exposes a contract gap.
- Source-index and RTM references are navigation hints, never read or edit limits.
- Keep the existing `HashRouter`, make every contracted page reachable, and cover loading, empty,
  success, validation, and API-error states with accessible responsive UI.
- Leave no empty handler, demo fallback, TODO, FIXME, or placeholder, and add no dependency unless
  the existing build requires it.
- Use English for source comments, validation messages, documentation, and user-visible text.

## Contracted pages
{page_list}

## On-demand client context
- Exact call skeleton: `{_relative(run_root, call_skeleton_path)}`
- Client index: `{_relative(run_root, client_index_path)}`
"""
    prompt += "\n## Frontend owner root\n- `application/frontend`"
    prompt += render_allowed_output_rules(required)
    prompt_path = output / "frontend-application.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    task = TaskSpec(
        task_id=task_id,
        control=f"{spec.name} frontend application",
        prompt_file=_relative(run_root, prompt_path),
        context_file=_relative(run_root, context_path),
        allowed_write_paths=allowed,
        required_output_paths=required,
        immutable_paths=[
            "application/frontend/src/generated",
        ],
        source_artifacts={
            **{
                name: str(path)
                for name, path in spec.inputs.items()
                if name in {"bceModel", "sequenceModel", "openapi", "deploymentBundle"}
                and path.is_file()
            },
        },
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        llm=llm_config(spec),
        owner="frontend",
        task_type="frontend-implementation",
        depends_on=["implement-backend-application"],
        source_refs=[
            *(f"api:{operation_id}" for operation_id in operations),
            *_workload_source_refs(deployment_context),
        ],
        allowed_write_roots=["application/frontend"],
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


def llm_config(spec: JobSpec) -> dict[str, object]:
    """모든 구현 작업이 공유하는 OpenHands LLM 설정을 만든다."""

    connection = build_llm_connection()
    return {
        "provider": connection.provider,
        # Stored as execution evidence. Runtime selection still reads the same
        # required root .env settings and never falls back to this snapshot.
        "model": connection.model,
        "baseUrl": connection.base_url,
        "temperature": spec.agent_temperature,
        "maxOutputTokens": spec.agent_max_output_tokens,
        "reasoningEffort": settings.implementation_reasoning_effort,
    }




def render_allowed_output_rules(allowed: list[str]) -> str:
    return "\n\n## Contracted outputs\n\n" + "\n".join(f"- `{path}`" for path in allowed) + "\n"


def _deployment_context(spec: JobSpec, names: set[str]) -> dict[str, object]:
    """구현 대상과 연결된 generatedApplication 실행 조건만 작게 전달한다.

    배포 bundle 전체에는 CSP 계획처럼 코드 task가 소비하지 않는 정보가 많다.
    ArtifactTrace의 typed class→workload 경로가 정확히 있는 workload를 고르고, 연결이
    하나도 없을 때에는 단일 앱인 경우만 fallback하여 실행 계약을 남긴다.
    """
    bundle = _read_json(spec.inputs.get("deploymentBundle"))
    graph = bundle.get("workloadGraph")
    if not isinstance(graph, dict):
        return {}
    generated = [
        item
        for item in graph.get("workloads", [])
        if isinstance(item, dict)
        and str((item.get("artifact") or {}).get("kind") or "") == "generatedApplication"
    ]
    trace = project_artifact_trace(
        {
            "extracted_bce_classes": _read_json(spec.inputs.get("bceModel")),
            "sequence_diagram_model": _read_json(spec.inputs.get("sequenceModel")),
            "api_spec_model": _read_json(spec.inputs.get("apiModel")),
            "erd_bce_classes": _read_json(spec.inputs.get("erdBceModel")),
            "deployment_diagram_bundle": bundle,
        }
    )
    # 이름을 sourceRefs 문자열에서 찾지 않는다. class kind와 정확한 ID로 출발한 뒤
    # projection이 보존한 edge를 따라가 workload kind에 도착한 경우만 연결로 인정한다.
    matched_ids = {
        ref.id
        for name in names
        if name
        for ref in trace.downstream(TraceRef("class", name))
        if ref.kind == "workload"
    }
    matched = [item for item in generated if str(item.get("id") or "") in matched_ids]
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
                            "id",
                            "name",
                            "kind",
                            "projection",
                            "connectionRef",
                            "sensitive",
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
            connection
            for connection in graph.get("connections", [])
            if isinstance(connection, dict)
            and {str(connection.get("sourceRef") or ""), str(connection.get("targetRef") or "")}
            & workload_ids
        ],
    }


def _operation_source_refs(spec: JobSpec, use_case_ids: set[str]) -> list[str]:
    """명시된 UC ID가 정확히 연결된 API/BCE operation 주소만 만든다."""

    refs: set[str] = set()
    endpoints = _read_json(spec.inputs.get("apiModel")).get("Endpoints", [])
    if isinstance(endpoints, list):
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            endpoint_use_cases = set(_string_ids(endpoint.get("use_case_ids")))
            operation_id = endpoint.get("operation_id") or endpoint.get("operationId")
            if (
                endpoint_use_cases & use_case_ids
                and isinstance(operation_id, str)
                and operation_id
            ):
                refs.add(f"api:{operation_id}")

    classes = _read_json(spec.inputs.get("bceModel")).get("Classes", [])
    if not isinstance(classes, list):
        return sorted(refs)
    for class_item in classes:
        if not isinstance(class_item, dict):
            continue
        operations = class_item.get("operations", [])
        if not isinstance(operations, list):
            continue
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            step_use_cases = {
                step_ref.partition(":")[0]
                for step_ref in _string_ids(operation.get("stepRefs"))
                if ":" in step_ref
            }
            operation_id = operation.get("operationId")
            if (
                step_use_cases & use_case_ids
                and isinstance(operation_id, str)
                and operation_id
            ):
                refs.add(f"operation:{operation_id}")
    return sorted(refs)


def _workload_source_refs(deployment: dict[str, object]) -> list[str]:
    """exact projection이 고른 workload의 typed 주소만 task에 보존한다."""

    workloads = deployment.get("workloads", [])
    if not isinstance(workloads, list):
        return []
    return [
        f"workload:{item['id']}"
        for item in workloads
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    ]


def _string_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


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


def _materialize_design_inputs(
    spec: JobSpec,
    run_root: Path,
    names: set[str],
) -> dict[str, str]:
    """Copy frozen design inputs into the run for on-demand agent reads."""
    target_root = run_root / "reports" / "implementation-tasks" / "design-inputs"
    aliases = {
        "requirements": ("refinedRequirements", "requirements", "refined_requirements"),
        "useCaseSpec": ("useCaseSpec", "useCaseSpecs", "useCases", "use_case_specs"),
    }
    materialized: dict[str, str] = {}
    for requested in sorted(names):
        candidates = aliases.get(requested, (requested,))
        source = next((spec.inputs[name] for name in candidates if name in spec.inputs and spec.inputs[name].is_file()), None)
        if source is None:
            continue
        target = target_root / f"{requested}{source.suffix or '.json'}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
        materialized[requested] = _relative(run_root, target)
    return materialized


def _materialize_method_contexts(
    run_root: Path,
    output: Path,
    package_path: str,
    projection: MethodProjectionResult,
    *,
    requirements: list[dict[str, object]],
    use_cases: list[dict[str, object]],
    endpoints: list[dict[str, object]],
    design_inputs: dict[str, str],
) -> list[dict[str, object]]:
    """Write one small, on-demand context file per exact BCE operation."""

    context_root = output / "method-context"
    context_root.mkdir(parents=True, exist_ok=True)
    requirements_by_id = {
        str(value["id"]): value
        for value in requirements
        if isinstance(value.get("id"), str) and value["id"]
    }
    entries: list[dict[str, object]] = []
    for item in projection.methods:
        evidence = _method_context_evidence(
            item,
            requirements_by_id=requirements_by_id,
            use_cases=use_cases,
            endpoints=endpoints,
        )
        source_paths = {
            f"application/src/main/java/{package_path}/bce/{item.method.class_name}.java"
        }
        if item.method.stereotype == "Control":
            source_paths.add(
                "application/src/main/java/"
                f"{package_path}/application/impl/{item.method.class_name}Service.java"
            )
        for method_slice in item.slices:
            for call in method_slice.outgoing:
                if call.target is None:
                    continue
                source_paths.add(
                    f"application/src/main/java/{package_path}/bce/{call.target.class_name}.java"
                )
                if call.target.stereotype == "Control":
                    source_paths.add(
                        "application/src/main/java/"
                        f"{package_path}/application/impl/{call.target.class_name}Service.java"
                    )
        path = context_root / f"{item.method.stable_id}.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": "implementation-method-context/v1alpha1",
                    "method": asdict(item.method),
                    "generation": item.generation,
                    "reasons": list(item.reasons),
                    **evidence,
                    "designInputs": design_inputs,
                    "slices": [asdict(value) for value in item.slices],
                    "sourcePaths": sorted(
                        value for value in source_paths if (run_root / value).is_file()
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        entries.append(
            {
                "operationId": item.method.operation_id,
                "stableId": item.method.stable_id,
                "path": _relative(run_root, path),
                "refs": evidence["refs"],
                "sourcePaths": sorted(source_paths),
            }
        )
    return entries


def _method_context_evidence(
    projection: MethodProjection,
    *,
    requirements_by_id: dict[str, dict[str, object]],
    use_cases: list[dict[str, object]],
    endpoints: list[dict[str, object]],
) -> dict[str, object]:
    """Select only exact requirement, step, call, and API evidence for one method."""

    method = projection.method
    use_case_ids = {
        use_case_id
        for method_slice in projection.slices
        for use_case_id in method_slice.use_case_ids
    }
    step_refs = {
        step_ref
        for method_slice in projection.slices
        for step_ref in method_slice.step_refs
    }
    call_ids = {
        method_slice.incoming_call_id
        for method_slice in projection.slices
        if method_slice.incoming_call_id
    } | {
        call.call_id
        for method_slice in projection.slices
        for call in method_slice.outgoing
        if call.call_id
    }
    matching_use_cases = [
        value
        for value in use_cases
        if str(value.get("use_case_id") or value.get("id") or "") in use_case_ids
    ]
    requirement_ids = {
        str(requirement_id)
        for value in matching_use_cases
        for field in ("requirement_ids", "nfr_ids")
        for requirement_id in value.get(field, [])
        if isinstance(requirement_id, str) and requirement_id
    }
    scenario_steps = []
    for value in matching_use_cases:
        use_case_id = str(value.get("use_case_id") or value.get("id") or "")
        for step in value.get("main_scenario", []):
            if not isinstance(step, dict):
                continue
            step_ref = f"{use_case_id}:main:{step.get('step_number')}"
            if step_ref in step_refs:
                scenario_steps.append({"ref": step_ref, **step})

    api_operations = []
    for endpoint in endpoints:
        binding = endpoint.get("control_binding")
        if not isinstance(binding, dict) or (
            str(binding.get("control") or "") != method.class_name
            or str(binding.get("method") or "") != method.name
        ):
            continue
        api_operations.append(
            {
                key: endpoint[key]
                for key in ("operation_id", "method", "path", "control_binding")
                if endpoint.get(key) is not None
            }
        )

    refs = {
        f"operation:{method.operation_id}",
        *(f"requirement:{value}" for value in requirement_ids),
        *(f"use_case:{value}" for value in use_case_ids),
        *(f"step:{value}" for value in step_refs),
        *(f"call:{value}" for value in call_ids),
        *(
            f"api:{value['operation_id']}"
            for value in api_operations
            if isinstance(value.get("operation_id"), str)
        ),
    }
    return {
        "refs": sorted(refs),
        "requirements": [
            {
                key: requirements_by_id[requirement_id][key]
                for key in ("id", "type", "text")
                if requirements_by_id[requirement_id].get(key) is not None
            }
            for requirement_id in sorted(requirement_ids)
            if requirement_id in requirements_by_id
        ],
        "scenarioSteps": sorted(scenario_steps, key=lambda value: str(value["ref"])),
        "apiOperations": sorted(
            api_operations, key=lambda value: str(value.get("operation_id") or "")
        ),
    }


def _frontend_contract_index(
    run_root: Path,
    openapi: dict[str, object],
    pages: list[str],
    contracts: GeneratedClientContracts,
) -> dict[str, object]:
    """Build a compact page/operation/generated-source index."""
    files: list[dict[str, object]] = []
    for path in contracts.files:
        relative = path.relative_to(run_root).as_posix()
        generated_relative = path.relative_to(contracts.generated_root).as_posix()
        files.append(
            {
                "path": relative,
                "import": f"../generated/{generated_relative.removesuffix('.ts')}",
            }
        )

    operation_entries: list[dict[str, object]] = []
    paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
    methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    if isinstance(paths, dict):
        for path, path_item in sorted(paths.items()):
            if not isinstance(path_item, dict):
                continue
            for method, operation in sorted(path_item.items()):
                if method.casefold() not in methods or not isinstance(operation, dict):
                    continue
                operation_id = str(operation.get("operationId") or f"{method.upper()} {path}")
                tags = operation.get("tags") if isinstance(operation.get("tags"), list) else []
                tag = str(tags[0]) if tags else "Overview"
                page = "".join(
                    word[:1].upper() + word[1:]
                    for word in re.findall(r"[A-Za-z0-9]+", tag)
                ) or "Overview"
                page += "Page"
                operation_entries.append(
                    {
                        "page": page if page in pages else (pages[0] if pages else page),
                        "operationId": operation_id,
                        "method": method.upper(),
                        "path": path,
                        "sourceInventory": "generatedApiFiles",
                    }
                )
    api_files = [
        item
        for item in files
        if "/apis/" in f"/{item['path']}" or "/api/" in f"/{item['path']}"
    ]
    return {
        "schemaVersion": "frontend-client-index/v1alpha1",
        "importRoot": contracts.import_root,
        "files": files,
        "generatedApiFiles": api_files,
        "operations": operation_entries,
        "indexPath": "reports/implementation-tasks/frontend-generated-client-index.json",
        "hintsOnly": True,
    }


def _work_unit_editable_paths(
    run_root: Path,
    required: list[str],
    scopes: list[str],
) -> list[str]:
    """package 범위를 현재 실행에서 편집할 수 있는 실제 파일 목록으로 바꾼다.

    아직 만들어지지 않은 필수 파일은 그대로 포함하고, 지정한 디렉터리에 이미 있는 파일도
    함께 넣는다. 생성된 공개 계약은 호출자가 이 범위에 넘기지 않는다.
    """
    paths = {path.replace("\\", "/") for path in required}
    for scope in scopes:
        root = run_root / scope
        if root.is_file():
            paths.add(root.relative_to(run_root).as_posix())
        elif root.is_dir():
            paths.update(
                path.relative_to(run_root).as_posix() for path in root.rglob("*") if path.is_file()
            )
    return sorted(paths)


def _without_immutable_paths(paths: list[str], immutable: list[str]) -> list[str]:
    roots = [path.replace("\\", "/").rstrip("/") for path in immutable]
    return [
        path
        for path in paths
        if not any(path == root or path.startswith(root + "/") for root in roots)
    ]


def _relative(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")
