from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .quality_gates import e2e_contract_violations


@dataclass(frozen=True)
class BacklogTask:
    task_id: str
    category: str
    priority: str
    objective: str
    source_artifacts: list[str]
    expected_outputs: list[str]
    blocked_by: list[str]
    evidence: list[str]


def audit_run_completion(run_root: Path) -> dict[str, object]:
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    application = run_root / "application"
    java_root = application / "src" / "main" / "java"
    test_root = application / "src" / "test" / "java"
    base_package = _infer_base_package(java_root)
    package_path = Path(base_package.replace(".", "/"))
    package_root = java_root / package_path

    main_files = sorted(java_root.rglob("*.java"))
    test_files = sorted(test_root.rglob("*.java")) if test_root.is_dir() else []
    bce_files = sorted((package_root / "bce").glob("*.java"))
    api_files = sorted((package_root / "api").glob("*.java"))
    api_model_files = sorted((package_root / "api" / "model").glob("*.java"))
    service_files = sorted((package_root / "application" / "impl").glob("*.java"))

    skeletons = _scan_skeletons(bce_files, run_root)
    todo_evidence = _scan_lines(main_files, run_root, r"\bTODO\b")
    api_names = [path.stem for path in api_files if path.stem.endswith("Api")]
    missing_api_names = [
        name for name in api_names
        if not (
            package_root / "adapter" / "in" / "web" / f"{name}Controller.java"
        ).is_file()
        or not (
            test_root / package_path / "adapter" / "in" / "web" / f"{name}ControllerTest.java"
        ).is_file()
    ]
    boundary_names = _bce_names_with_stereotype(manifest, run_root, "Boundary")
    missing_boundary_names = [
        name for name in boundary_names
        if not (
            package_root / "adapter" / "in" / "boundary" / f"{name}Adapter.java"
        ).is_file()
        or not (
            test_root / package_path / "adapter" / "in" / "boundary" / f"{name}AdapterTest.java"
        ).is_file()
    ]
    gateway_names = _bce_names_with_stereotype(manifest, run_root, "Gateway")
    missing_gateway_names = [
        name for name in gateway_names
        if not _gateway_adapter_complete(package_root, test_root / package_path, name)
    ]
    bce_entity_names = _bce_names_with_stereotype(manifest, run_root, "Entity")
    erd_entity_names = _erd_entity_aliases(manifest)
    entity_names = [
        name for name in bce_entity_names if name in erd_entity_names
    ]
    control_names = _bce_names_with_stereotype(manifest, run_root, "Control")
    persistence_outputs = _expected_persistence_outputs(
        application, package_path, entity_names
    )
    missing_persistence_outputs = [
        path.relative_to(run_root).as_posix()
        for path in persistence_outputs if not path.is_file()
    ]
    wiring_outputs = _expected_wiring_outputs(application, package_path, manifest)
    missing_wiring_outputs = [
        path.relative_to(run_root).as_posix()
        for path in wiring_outputs if not path.is_file()
    ]
    e2e_relative = _task_output(
        manifest, "integration-test", suffix=".java"
    ) or f"application/src/test/java/{package_path.as_posix()}/integration/ApplicationFlowTest.java"
    e2e_output = run_root / e2e_relative
    e2e_contract_errors = e2e_contract_violations(
        e2e_output, _task_semantic_contract(manifest, run_root, "integration-test")
    )
    missing_e2e_output = bool(e2e_contract_errors)
    e2e_gap_path = run_root / "reports" / "design-gaps" / "end-to-end-flow.json"
    e2e_gap_report = (
        json.loads(e2e_gap_path.read_text(encoding="utf-8"))
        if e2e_gap_path.is_file() else {}
    )
    e2e_gaps = [
        gap for gap in e2e_gap_report.get("gaps", []) if isinstance(gap, dict)
    ]

    tasks = _build_backlog(
        base_package,
        missing_api_names,
        missing_boundary_names,
        missing_gateway_names,
        entity_names,
        control_names,
        skeletons,
        todo_evidence,
        manifest,
        missing_persistence_outputs,
        missing_wiring_outputs,
        missing_e2e_output,
        e2e_contract_errors,
        e2e_gaps,
    )
    critical = [task for task in tasks if task.priority == "CRITICAL"]
    high = [task for task in tasks if task.priority == "HIGH"]
    report: dict[str, object] = {
        "schemaVersion": "implementation-completion-audit/v1alpha1",
        "run": run_root.name,
        "status": "INCOMPLETE" if tasks else "COMPLETE",
        "summary": {
            "mainJavaFiles": len(main_files),
            "testJavaFiles": len(test_files),
            "bceContracts": len(bce_files),
            "openApiInterfaces": len(api_files),
            "openApiModels": len(api_model_files),
            "applicationServices": len(service_files),
            "unsupportedSkeletonMethods": len(skeletons),
            "todoMarkers": len(todo_evidence),
            "criticalTasks": len(critical),
            "highPriorityTasks": len(high),
            "totalBacklogTasks": len(tasks),
            "missingPersistenceOutputs": len(missing_persistence_outputs),
            "missingApiAdapters": len(missing_api_names),
            "missingBoundaryAdapters": len(missing_boundary_names),
            "missingGatewayAdapters": len(missing_gateway_names),
            "missingWiringOutputs": len(missing_wiring_outputs),
            "missingEndToEndOutputs": int(missing_e2e_output),
            "endToEndContractViolations": len(e2e_contract_errors),
            "unresolvedDesignGaps": len(e2e_gaps),
        },
        "completionCriteria": [
            "No production path delegates to a BCE method that throws UnsupportedOperationException.",
            "Every generated OpenAPI interface has a Spring adapter and contract test.",
            "ERD entities have persistence mappings, repositories, and migrations.",
            "Boundary ports have concrete adapters or are explicitly classified as external UI ports.",
            "Outbound Gateway ports have concrete adapters and tests.",
            "Application wiring and an end-to-end purchase flow test pass.",
            "The generated application compiles and its implementation and end-to-end tests pass.",
        ],
        "evidence": {
            "unsupportedSkeletons": skeletons,
            "todoMarkers": todo_evidence,
            "missingInputs": [
                item["message"]
                for item in manifest.get("diagnostics", [])
                if item.get("code") == "MISSING_PROTOTYPE_INPUT"
            ],
            "designGaps": e2e_gaps,
            "endToEndContractViolations": e2e_contract_errors,
        },
        "backlog": [asdict(task) for task in tasks],
    }
    reports = run_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "implementation-completion-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (reports / "implementation-backlog.json").write_text(
        json.dumps([asdict(task) for task in tasks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports / "implementation-completion-audit.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    return report


def _infer_base_package(java_root: Path) -> str:
    api = next(java_root.rglob("*Api.java"), None)
    if api is None:
        raise ValueError("Cannot infer base package: no generated OpenAPI interface found")
    source = api.read_text(encoding="utf-8")
    match = re.search(r"(?m)^package\s+([\w.]+)\.api;", source)
    if not match:
        raise ValueError(f"Cannot infer base package from {api}")
    return match.group(1)


def _scan_skeletons(paths: list[Path], root: Path) -> list[str]:
    evidence: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "UnsupportedOperationException" in line:
                evidence.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()}")
    return evidence


def _scan_lines(paths: list[Path], root: Path, pattern: str) -> list[str]:
    compiled = re.compile(pattern)
    evidence: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if compiled.search(line):
                evidence.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()}")
    return evidence


def _bce_names_with_stereotype(
    manifest: dict[str, object], run_root: Path, stereotype: str
) -> list[str]:
    inputs = manifest.get("inputs", {})
    bce = inputs.get("bceClass", {}) if isinstance(inputs, dict) else {}
    path = Path(str(bce.get("path", ""))) if isinstance(bce, dict) else Path()
    if path.is_file():
        source = path.read_text(encoding="utf-8")
    else:
        blocks: list[str] = []
        for context_path in sorted(
            (run_root / "reports" / "implementation-tasks").glob("*.context.json")
        ):
            context = json.loads(context_path.read_text(encoding="utf-8"))
            blocks.append(str(context.get("bce", "")))
        source = "\n".join(blocks)
    return sorted(
        match.group(1)
        for match in re.finditer(
            rf"(?m)^\s*class\s+([A-Za-z_]\w*)\s+<<{re.escape(stereotype)}>>",
            source,
            re.IGNORECASE,
        )
    )


def _erd_entity_aliases(manifest: dict[str, object]) -> set[str]:
    inputs = manifest.get("inputs", {})
    erd = inputs.get("erd", {}) if isinstance(inputs, dict) else {}
    path = Path(str(erd.get("path", ""))) if isinstance(erd, dict) else Path()
    if not path.is_file():
        return set()
    source = path.read_text(encoding="utf-8")
    return set(
        re.findall(r'(?m)^\s*entity\s+"[^"]+"\s+as\s+([A-Za-z_]\w*)', source)
    ) | set(re.findall(r"(?m)^\s*entity\s+([A-Za-z_]\w*)\s*\{", source))


def _build_backlog(
    base_package: str,
    api_names: list[str],
    missing_boundaries: list[str],
    missing_gateways: list[str],
    entities: list[str],
    controls: list[str],
    skeletons: list[str],
    todos: list[str],
    manifest: dict[str, object],
    missing_persistence_outputs: list[str],
    missing_wiring_outputs: list[str],
    missing_e2e_output: bool,
    e2e_contract_errors: list[str],
    e2e_gaps: list[dict[str, object]],
) -> list[BacklogTask]:
    package = base_package.replace(".", "/")
    tasks: list[BacklogTask] = []
    domain_contract_blockers = ["replace-bce-runtime-skeletons"] if skeletons else []
    if skeletons:
        tasks.append(
            BacklogTask(
                "replace-bce-runtime-skeletons",
                "domain-contracts",
                "CRITICAL",
                "Replace throwing BCE Control/Entity skeletons with executable ports and domain models, then rewire existing services.",
                ["bceClass", "erd", "sequence"],
                [
                    f"application/src/main/java/{package}/domain/**",
                    f"application/src/main/java/{package}/application/port/**",
                    f"application/src/test/java/{package}/architecture/**",
                ],
                [],
                skeletons[:20],
            )
        )
    if entities and missing_persistence_outputs:
        tasks.append(
            BacklogTask(
                "implement-erd-persistence",
                "persistence",
                "CRITICAL",
                "Implement ERD-backed persistence entities, repositories, mappings, and schema migration.",
                ["erd", "bceClass"],
                [
                    f"application/src/main/java/{package}/persistence/entity/**",
                    f"application/src/main/java/{package}/persistence/repository/**",
                    "application/src/main/resources/db/migration/V1__initial_schema.sql",
                ],
                domain_contract_blockers,
                [f"Missing persistence output: {path}" for path in missing_persistence_outputs],
            )
        )
    for api_name in api_names:
        stem = api_name.removesuffix("Api")
        tasks.append(
            BacklogTask(
                f"implement-{_kebab(stem)}-api-adapter",
                "api-adapter",
                "HIGH",
                f"Implement the Spring adapter for generated {api_name} and map API models to application commands/results.",
                ["openapi", "sequence"],
                [
                    f"application/src/main/java/{package}/adapter/in/web/{stem}ApiController.java",
                    f"application/src/test/java/{package}/adapter/in/web/{stem}ApiControllerTest.java",
                ],
                domain_contract_blockers,
                [f"Generated interface has no implementation: {api_name}"],
            )
        )
    if missing_boundaries:
        tasks.append(
            BacklogTask(
                "implement-boundary-adapters",
                "boundary-adapter",
                "HIGH",
                "Provide concrete adapters for BCE Boundary ports or classify each as an external client-owned UI port.",
                ["bceClass", "sequence"],
                [
                    f"application/src/main/java/{package}/adapter/in/boundary/**",
                    f"application/src/test/java/{package}/adapter/in/boundary/**",
                ],
                domain_contract_blockers,
                [
                    f"Boundary has no concrete adapter and test: {name}"
                    for name in missing_boundaries
                ],
            )
        )
    if missing_wiring_outputs:
        tasks.append(
            BacklogTask(
                "implement-application-wiring",
                "configuration",
                "HIGH",
                "Add Spring Boot entry point, dependency injection, configuration properties, and production bean wiring.",
                ["deployment", "cloud", "bceClass"],
                _task_outputs(manifest, "configuration") or [
                    f"application/src/main/java/{package}/Application.java",
                    f"application/src/main/java/{package}/config/ApplicationConfiguration.java",
                    "application/src/main/resources/application.yml",
                    f"application/src/test/java/{package}/config/ApplicationContextTest.java",
                ],
                (["implement-erd-persistence"] if missing_persistence_outputs else [])
                + [f"implement-{_kebab(name.removesuffix('Api'))}-api-adapter" for name in api_names]
                + (["implement-boundary-adapters"] if missing_boundaries else [])
                + (["implement-outbound-gateway-adapters"] if missing_gateways else []),
                [f"Missing wiring output: {path}" for path in missing_wiring_outputs],
            )
        )
    if missing_gateways:
        tasks.append(
            BacklogTask(
                "implement-outbound-gateway-adapters",
                "gateway-adapter",
                "HIGH",
                "Implement every generated outbound Gateway using persistence or external-system adapters.",
                ["bceClass", "erd", "sequence", "deployment", "cloud"],
                [f"application/src/main/java/{package}/adapter/out/**"],
                domain_contract_blockers,
                [f"Gateway has no concrete adapter: {name}" for name in missing_gateways],
            )
        )
    if missing_e2e_output:
        tasks.append(
            BacklogTask(
                "implement-end-to-end-flow",
                "integration-test",
                "HIGH",
                "Verify OpenAPI success and sequence-defined alternate flows across real adapters.",
                ["sequence", "openapi", "erd"],
                _task_outputs(manifest, "integration-test") or [
                    f"application/src/test/java/{package}/integration/ApplicationFlowTest.java"
                ],
                (["implement-application-wiring"] if missing_wiring_outputs else [])
                + (["implement-outbound-gateway-adapters"] if missing_gateways else [])
                + list(dict.fromkeys(
                    f"design-gap:{gap.get('code', 'UNKNOWN')}" for gap in e2e_gaps
                )),
                [
                    f"{gap.get('code', 'UNKNOWN')}: {gap.get('evidence', '')}"
                    for gap in e2e_gaps
                ] or e2e_contract_errors or todos[:20] or ["Application services contain unresolved design gaps."],
            )
        )
    return tasks


def _expected_wiring_outputs(
    application: Path, package_path: Path, manifest: dict[str, object]
) -> list[Path]:
    contracted = _task_outputs(manifest, "configuration")
    if contracted:
        run_root = application.parent
        return [run_root / relative for relative in contracted]
    main = application / "src" / "main"
    package_main = main / "java" / package_path
    test = application / "src" / "test" / "java" / package_path
    application_file = next(package_main.glob("*Application.java"), package_main / "Application.java")
    return [
        application_file,
        package_main / "config" / "ApplicationConfiguration.java",
        main / "resources" / "application.yml",
        test / "config" / "ApplicationContextTest.java",
    ]


def _gateway_adapter_complete(
    package_root: Path, package_test_root: Path, gateway_name: str
) -> bool:
    adapter_root = package_root / "adapter" / "out"
    if not adapter_root.is_dir():
        return False
    for source in adapter_root.rglob("*.java"):
        text = source.read_text(encoding="utf-8")
        if not re.search(rf"\bimplements\s+{re.escape(gateway_name)}\b", text):
            continue
        relative_parent = source.parent.relative_to(package_root)
        test = package_test_root / relative_parent / f"{source.stem}Test.java"
        if test.is_file():
            return True
    return False


def _task_outputs(manifest: dict[str, object], task_type: str) -> list[str]:
    for task in manifest.get("implementation_tasks", []):
        if isinstance(task, dict) and task.get("task_type") == task_type:
            return [str(path) for path in task.get("allowed_write_paths", [])]
    return []


def _task_output(
    manifest: dict[str, object], task_type: str, *, suffix: str
) -> str | None:
    return next(
        (path for path in _task_outputs(manifest, task_type) if path.endswith(suffix)),
        None,
    )


def _task_semantic_contract(
    manifest: dict[str, object], run_root: Path, task_type: str
) -> dict[str, object] | None:
    for task in manifest.get("implementation_tasks", []):
        if not isinstance(task, dict) or task.get("task_type") != task_type:
            continue
        context_file = task.get("context_file")
        if not context_file:
            return None
        path = run_root / str(context_file)
        if not path.is_file():
            return None
        context = json.loads(path.read_text(encoding="utf-8"))
        contract = context.get("semanticContract")
        return contract if isinstance(contract, dict) else None
    return None


def _expected_persistence_outputs(
    application: Path, package_path: Path, entities: list[str]
) -> list[Path]:
    main = application / "src" / "main"
    test = application / "src" / "test" / "java" / package_path
    package_main = main / "java" / package_path
    outputs: list[Path] = []
    outputs.extend(
        package_main / "persistence" / "entity" / f"{name}Entity.java"
        for name in entities
    )
    outputs.extend(
        package_main / "persistence" / "repository" / f"{name}Repository.java"
        for name in entities
    )
    outputs.extend(
        [
            package_main / "persistence" / "mapper" / "BcePersistenceMapper.java",
            test / "persistence" / "mapper" / "BcePersistenceMapperTest.java",
            main / "resources" / "db" / "migration" / "V1__initial_schema.sql",
            test / "persistence" / "PersistenceSchemaTest.java",
        ]
    )
    return outputs


def _kebab(value: str) -> str:
    return "-".join(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", value)).lower()


def _render_markdown(report: dict[str, object]) -> str:
    summary = report["summary"]
    lines = [
        "# Implementation completion audit",
        "",
        f"Status: **{report['status']}**",
        "",
        "## Summary",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in summary.items())
    lines.extend(["", "## Ordered backlog", ""])
    for index, task in enumerate(report["backlog"], 1):
        lines.extend(
            [
                f"### {index}. {task['task_id']} ({task['priority']})",
                "",
                task["objective"],
                "",
                f"Blocked by: {', '.join(task['blocked_by']) if task['blocked_by'] else 'none'}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"
