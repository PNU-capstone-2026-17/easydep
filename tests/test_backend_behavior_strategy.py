from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.implementation.domain.implementation_ir import ComponentIR
from app.implementation.domain.models import JobSpec
from app.implementation.planning.design_context import (
    TaskSpec,
    _build_backend_behavior_tasks,
    _UseCaseBundle,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_fixture(
    root: Path,
    *,
    vocabulary: str = "Original",
) -> tuple[JobSpec, Path, Path, str, _UseCaseBundle, TaskSpec]:
    run = root / "run"
    output = run / "reports/implementation-tasks"
    package_path = "com/example/app"
    requirements = root / "requirements.json"
    use_cases_path = root / "use-cases.json"
    api_model = root / "api-model.json"
    bce_model = root / "bce-model.json"

    _write_json(
        requirements,
        [
            {
                "id": "REQ-A",
                "type": "functional",
                "text": f"The {vocabulary} first flow returns its result.",
            },
            {
                "id": "REQ-Z",
                "type": "non-functional",
                "text": "This requirement must stay outside unrelated capsules.",
            },
        ],
    )
    use_cases = [
        {
            "use_case_id": "UC-A",
            "name": f"{vocabulary} first flow",
            "requirement_ids": ["REQ-A"],
            "main_scenario": [
                {
                    "step_number": 1,
                    "sentence": f"Run the {vocabulary} primary branch.",
                }
            ],
            "extensions": [
                {
                    "label": "1a",
                    "condition": f"The {vocabulary} alternative applies.",
                    "outcome": f"The {vocabulary} alternative is observable.",
                    "handling_steps": [
                        {
                            "sub_step": "1a1",
                            "sentence": f"Run the {vocabulary} alternative branch.",
                        }
                    ],
                }
            ],
        },
        {
            "use_case_id": "UC-B",
            "name": f"{vocabulary} second flow",
            "main_scenario": [
                {"step_number": 1, "sentence": f"Run the {vocabulary} second flow."}
            ],
            "extensions": [],
        },
        {
            "use_case_id": "UC-C",
            "name": f"{vocabulary} independent flow",
            "main_scenario": [
                {
                    "step_number": 1,
                    "sentence": f"Run the {vocabulary} independent flow.",
                }
            ],
            "extensions": [],
        },
        {
            "use_case_id": "UC-Z",
            "name": f"{vocabulary} unrelated flow",
            "nfr_ids": ["REQ-Z"],
            "main_scenario": [
                {"step_number": 1, "sentence": "This must stay outside the capsule."}
            ],
            "extensions": [],
        },
    ]
    _write_json(use_cases_path, {"useCaseSpecs": use_cases})

    def endpoint(
        operation_id: str,
        use_case_ids: list[str],
        control: str,
        method: str,
        path: str,
    ) -> dict[str, object]:
        return {
            "operation_id": operation_id,
            "use_case_ids": use_case_ids,
            "method": "post",
            "path": path,
            "request_schema": f"{vocabulary}Request",
            "responses": [{"status": 200, "schema_name": f"{vocabulary}Result"}],
            "control_binding": {
                "control": control,
                "method": method,
                "arguments": [{"name": "value", "source": "$body.value"}],
                "outcomes": [{"status": 200, "outcome": "completed"}],
            },
        }

    endpoints = [
        endpoint("op-entry", ["UC-A"], f"{vocabulary}Entry", "enter", "/entry"),
        endpoint(
            "op-shared",
            ["UC-A", "UC-B"],
            f"{vocabulary}Shared",
            "share",
            "/shared",
        ),
        endpoint("op-exit", ["UC-B"], f"{vocabulary}Exit", "exit", "/exit"),
        endpoint("op-solo", ["UC-C"], f"{vocabulary}Solo", "run", "/solo"),
        endpoint("op-unrelated", ["UC-Z"], f"{vocabulary}Other", "ignore", "/other"),
    ]
    _write_json(api_model, {"Endpoints": endpoints})

    result_type = f"{vocabulary}Result"
    detail_type = f"{vocabulary}Detail"
    unrelated_type = f"{vocabulary}Other"
    bce_payload = {
        "Classes": [
            {
                "className": name,
                "stereotype": stereotype,
                "use_case_ids": use_cases,
                "fields": [],
            }
            for name, stereotype, use_cases in (
                (result_type, "Entity", ["UC-A", "UC-B"]),
                (f"{vocabulary}Entry", "Control", []),
                (f"{vocabulary}Nested", "Control", []),
                (detail_type, "Entity", []),
                (unrelated_type, "Entity", ["UC-Z"]),
            )
        ],
        "DataTypes": [],
        "Relationships": [
            {
                "source": result_type,
                "target": detail_type,
                "type": "Association",
            }
        ],
    }
    _write_json(bce_model, bce_payload)

    controller = (
        "application/src/main/java/"
        f"{package_path}/adapter/in/web/{vocabulary}ApiController.java"
    )
    interface = (
        f"application/src/main/java/{package_path}/api/{vocabulary}Api.java"
    )
    request_model = (
        f"application/src/main/java/{package_path}/api/model/{vocabulary}Request.java"
    )
    response_model = (
        f"application/src/main/java/{package_path}/api/model/{result_type}.java"
    )
    generated_contracts = {
        controller: (
            f"import com.example.app.api.{vocabulary}Api;\n"
            f"public class {vocabulary}ApiController implements {vocabulary}Api {{\n"
            '  @Operation(operationId = "op-entry")\n'
            '  @Operation(operationId = "op-shared")\n'
            '  @Operation(operationId = "op-exit")\n'
            '  @Operation(operationId = "op-solo")\n'
            '  @Operation(operationId = "op-unrelated")\n'
            "}\n"
        ),
        interface: f"public interface {vocabulary}Api {{}}\n",
        request_model: f"public class {vocabulary}Request {{}}\n",
        response_model: f"public class {result_type} {{}}\n",
        f"application/src/main/java/{package_path}/bce/{result_type}.java": (
            f"public class {result_type} {{}}\n"
        ),
        (
            f"application/src/main/java/{package_path}/persistence/entity/"
            f"{result_type}Entity.java"
        ): f"public class {result_type}Entity {{}}\n",
        (
            f"application/src/main/java/{package_path}/persistence/repository/"
            f"{result_type}Repository.java"
        ): f"public interface {result_type}Repository {{}}\n",
    }
    for relative, source in generated_contracts.items():
        target = run / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    method_root = output / "method-context"
    method_entries: list[dict[str, object]] = []

    def method_context(
        stable_id: str,
        use_case_id: str | None,
        operation_id: str | None,
        class_name: str,
        method: str,
        *,
        outgoing_target: str | None = None,
        implementation_marker: bool = True,
    ) -> None:
        source = (
            "application/src/main/java/"
            f"{package_path}/application/impl/{class_name}Service.java"
        )
        source_path = run / source
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            (
                f"class {class_name} {{ /* EASYDEP-IMPLEMENT:{stable_id} */ }}"
                if implementation_marker
                else f"interface {class_name} {{}}"
            ),
            encoding="utf-8",
        )
        refs = [f"operation:{class_name}::{method}()"]
        if use_case_id:
            refs.extend(
                [f"use_case:{use_case_id}", f"step:{use_case_id}:main:1"]
            )
        if operation_id:
            refs.append(f"api:{operation_id}")
        outgoing = []
        if outgoing_target:
            outgoing.append(
                {
                    "call_id": f"call:{stable_id}:nested",
                    "target": {
                        "stable_id": outgoing_target,
                        "class_name": f"{vocabulary}Nested",
                        "name": "continueFlow",
                    },
                    "arguments": [],
                    "fragments": [],
                }
            )
        context = {
            "schemaVersion": "implementation-method-context/v1alpha1",
            "method": {
                "stable_id": stable_id,
                "stereotype": "Control",
                "class_name": class_name,
                "operation_id": f"{class_name}::{method}()",
                "name": method,
                "parameters": [],
                "return_type": f"{vocabulary}Result",
            },
            "refs": refs,
            "sourcePaths": [source],
            "scenarioSteps": (
                [{"ref": f"{use_case_id}:main:1", "sentence": "Direct evidence."}]
                if use_case_id
                else []
            ),
            "apiOperations": (
                [value for value in endpoints if value["operation_id"] == operation_id]
                if operation_id
                else []
            ),
            "slices": [
                {
                    "use_case_ids": [use_case_id] if use_case_id else [],
                    "outgoing": outgoing,
                }
            ],
        }
        context_path = method_root / f"{stable_id}.json"
        _write_json(context_path, context)
        method_entries.append(
            {
                "stableId": stable_id,
                "path": context_path.relative_to(run).as_posix(),
                "refs": refs,
                "sourcePaths": [source],
            }
        )

    method_context(
        "method-entry",
        "UC-A",
        "op-entry",
        f"{vocabulary}Entry",
        "enter",
        outgoing_target="method-nested",
    )
    method_context(
        "method-shared", "UC-B", "op-shared", f"{vocabulary}Shared", "share"
    )
    method_context("method-solo", "UC-C", "op-solo", f"{vocabulary}Solo", "run")
    method_context(
        "method-unrelated",
        "UC-Z",
        "op-unrelated",
        f"{vocabulary}Other",
        "ignore",
    )
    # This context is reachable only by recursively following an outgoing call.
    # It has no exact UC/API membership and must not leak into UC-A's capsule.
    method_context(
        "method-nested", None, None, f"{vocabulary}Nested", "continueFlow"
    )
    # This method shares UC/API refs but has no implementation body owned by the task.
    method_context(
        "method-unbound",
        "UC-A",
        "op-entry",
        f"{vocabulary}Boundary",
        "notEndpointRoot",
        implementation_marker=False,
    )
    _write_json(
        output / "implement-backend-application.source-index.json",
        {"methodContexts": method_entries},
    )
    owner_context = output / "implement-backend-application.context.json"
    _write_json(owner_context, {"controllerPaths": [controller]})
    owner_test = (
        f"application/src/test/java/{package_path}/BackendApplicationTest.java"
    )
    owner_test_path = run / owner_test
    owner_test_path.parent.mkdir(parents=True, exist_ok=True)
    owner_test_path.write_text(
        "class BackendApplicationTest { /* EASYDEP-IMPLEMENT */ }\n",
        encoding="utf-8",
    )

    spec = JobSpec(
        job_type="implementation",
        feedback="",
        name=f"{vocabulary} application",
        workspace_root=root,
        inputs={
            "requirements": requirements,
            "useCaseSpec": use_cases_path,
            "apiModel": api_model,
            "bceModel": bce_model,
        },
        required_inputs=[],
        base_package="com.example.app",
        allow_assumptions=False,
        verify_compile=True,
        output_root=root / "output",
        agent_mode="openhands",
        agent_temperature=0.2,
        agent_max_output_tokens=8192,
    )
    owner = TaskSpec(
        task_id="implement-backend-application",
        control="backend",
        prompt_file="unused.prompt.md",
        context_file=owner_context.relative_to(run).as_posix(),
        allowed_write_paths=[],
        immutable_paths=[f"application/src/main/java/{package_path}/api"],
        source_artifacts={},
        prompt_sha256="owner-prompt",
        llm={},
        owner="backend",
        task_type="backend-implementation",
        required_test_paths=[owner_test],
        allowed_write_roots=[
            f"application/src/main/java/{package_path}",
            f"application/src/test/java/{package_path}",
        ],
    )
    bundle = _UseCaseBundle(
        ("UC-A", "UC-B", "UC-C", "UC-Z"),
        (ComponentIR(result_type, "Entity", ()),),
        (),
        tuple(endpoints),
    )
    return spec, run, output, package_path, bundle, owner


def _build_tasks(root: Path, *, vocabulary: str = "Original") -> list[TaskSpec]:
    arguments = _build_fixture(root, vocabulary=vocabulary)
    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        return _build_backend_behavior_tasks(*arguments)


def _task_context(run: Path, task: TaskSpec) -> dict[str, object]:
    return json.loads((run / task.context_file).read_text(encoding="utf-8"))


def test_exact_uc_api_components_form_deterministic_independent_tasks(
    tmp_path: Path,
) -> None:
    first = _build_tasks(tmp_path / "first")
    second = _build_tasks(tmp_path / "second")

    # UC-A and UC-B are connected by op-shared; UC-C and UC-Z remain separate.
    expected_components = {
        frozenset({"UC-A", "UC-B"}),
        frozenset({"UC-C"}),
        frozenset({"UC-Z"}),
    }
    assert {frozenset(task.use_case_ids) for task in first} == expected_components
    assert [task.task_id for task in first] == [task.task_id for task in second]
    assert [task.depends_on for task in first] == [[], [], []]
    # Reuse the existing backend phase and cumulative test verifier.
    assert all(task.task_type == "backend-implementation" for task in first)
    assert all(task.allowed_write_roots == [] for task in first)
    assert all(len(task.required_test_paths) == 1 for task in first)
    assert all(
        task.verification_profile["focusedTestPaths"] == task.required_test_paths
        for task in first
    )
    assert len({task.required_test_paths[0] for task in first}) == len(first)
    assert all(
        Path(task.required_test_paths[0]).name.startswith("Behavior")
        for task in first
    )

    run = tmp_path / "first/run"
    owner_test = "application/src/test/java/com/example/app/BackendApplicationTest.java"
    contexts = [_task_context(run, task) for task in first]
    assert all(context["requiredTestPath"] != owner_test for context in contexts)
    assert all(
        marker.get("path") != owner_test
        for context in contexts
        for marker in context["completionMarkers"]
    )
    assert all(
        marker.get("path") != owner_test
        for task in first
        for marker in task.verification_profile["requiredAbsentMarkers"]
    )
    operations_by_component = {
        frozenset(task.use_case_ids): set(
            _task_context(run, task)["apiOperationIds"]
        )
        for task in first
    }
    assert operations_by_component[frozenset({"UC-A", "UC-B"})] == {
        "op-entry",
        "op-shared",
        "op-exit",
    }
    assert operations_by_component[frozenset({"UC-C"})] == {"op-solo"}

    connected = next(
        task for task in first if set(task.use_case_ids) == {"UC-A", "UC-B"}
    )
    context = _task_context(run, connected)
    expected_read_only_contracts = {
        "application/src/main/java/com/example/app/bce/OriginalResult.java",
        (
            "application/src/main/java/com/example/app/persistence/entity/"
            "OriginalResultEntity.java"
        ),
        (
            "application/src/main/java/com/example/app/persistence/repository/"
            "OriginalResultRepository.java"
        ),
    }
    assert expected_read_only_contracts <= set(context["readSourcePaths"])
    assert expected_read_only_contracts.isdisjoint(connected.allowed_write_paths)
    assert {
        "application/src/main/java/com/example/app/adapter/in/web/OriginalApiController.java",
        "application/src/main/java/com/example/app/api/OriginalApi.java",
        "application/src/main/java/com/example/app/api/model/OriginalRequest.java",
        "application/src/main/java/com/example/app/api/model/OriginalResult.java",
        "reports/implementation-tasks/method-context/method-entry.json",
        "reports/implementation-tasks/method-context/method-shared.json",
    }.isdisjoint(context["readSourcePaths"])
    direct_methods = context["behaviorCapsule"]["directMethods"]
    assert {item["method"]["stable_id"] for item in direct_methods} == {
        "method-entry",
        "method-shared",
    }
    assert any(item["directCalls"] for item in direct_methods)
    prompt = (run / connected.prompt_file).read_text(encoding="utf-8")
    assert all(
        text in prompt
        for text in (
            "Group `completionMarkers` by unique path",
            "the endpoint with the same\n    HTTP method and path",
            "`method.stable_id` is the same",
            "focused test at `requiredTestPath`",
        )
    )
    assert "already passed this\n  Implementation subtask's semantic preflight" in prompt
    assert "inspect application source only as needed" in prompt
    assert "do not re-decide product meaning" in prompt


def test_shared_source_stays_bounded_and_rechecks_the_earlier_slice(
    tmp_path: Path,
) -> None:
    spec, run, output, package_path, bundle, owner = _build_fixture(tmp_path)
    source_index_path = output / "implement-backend-application.source-index.json"
    source_index = json.loads(source_index_path.read_text(encoding="utf-8"))
    entries = source_index["methodContexts"]
    entry = next(item for item in entries if item["stableId"] == "method-entry")
    solo = next(item for item in entries if item["stableId"] == "method-solo")
    shared_source = entry["sourcePaths"][0]
    solo_context_path = run / solo["path"]
    solo_context = json.loads(solo_context_path.read_text(encoding="utf-8"))
    solo_context["sourcePaths"] = [shared_source]
    solo_context_path.write_text(json.dumps(solo_context), encoding="utf-8")
    solo["sourcePaths"] = [shared_source]
    source_index_path.write_text(json.dumps(source_index), encoding="utf-8")
    shared_path = run / shared_source
    shared_path.write_text(
        shared_path.read_text(encoding="utf-8")
        + "\n/* EASYDEP-IMPLEMENT:method-solo */\n",
        encoding="utf-8",
    )
    owner.allowed_write_paths.append(shared_source)

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = _build_backend_behavior_tasks(
            spec, run, output, package_path, bundle, owner
        )

    assert {frozenset(task.use_case_ids) for task in tasks} == {
        frozenset({"UC-A", "UC-B"}),
        frozenset({"UC-C"}),
        frozenset({"UC-Z"}),
    }
    earlier = next(
        task for task in tasks if set(task.use_case_ids) == {"UC-A", "UC-B"}
    )
    later = next(task for task in tasks if task.use_case_ids == ["UC-C"])
    assert shared_source in earlier.allowed_write_paths
    assert shared_source in later.allowed_write_paths
    assert all(task.depends_on == [] for task in tasks)
    assert set(later.verification_profile["focusedTestPaths"]) == {
        earlier.required_test_paths[0],
        later.required_test_paths[0],
    }


def test_mechanical_vocabulary_rename_preserves_id_topology(
    tmp_path: Path,
) -> None:
    original = _build_tasks(tmp_path / "original", vocabulary="Original")
    renamed = _build_tasks(tmp_path / "renamed", vocabulary="Renamed")

    assert [task.task_id for task in renamed] == [task.task_id for task in original]
    assert [task.use_case_ids for task in renamed] == [
        task.use_case_ids for task in original
    ]
    assert [task.depends_on for task in renamed] == [
        task.depends_on for task in original
    ]
    assert [len(task.required_test_paths) for task in renamed] == [1, 1, 1]


def test_capsule_contains_only_exact_flow_endpoint_and_direct_method_contexts(
    tmp_path: Path,
) -> None:
    tasks = _build_tasks(tmp_path)
    run = tmp_path / "run"
    connected = next(task for task in tasks if set(task.use_case_ids) == {"UC-A", "UC-B"})
    context = _task_context(run, connected)
    capsule = context["behaviorCapsule"]

    assert {item["use_case_id"] for item in capsule["useCases"]} == {
        "UC-A",
        "UC-B",
    }
    uc_a = next(
        item for item in capsule["useCases"] if item["use_case_id"] == "UC-A"
    )
    assert uc_a["main_scenario"]
    assert uc_a["extensions"][0]["handling_steps"]
    assert {item["operation_id"] for item in capsule["endpoints"]} == {
        "op-entry",
        "op-shared",
        "op-exit",
    }
    assert capsule["requirements"] == [
        {
            "id": "REQ-A",
            "type": "functional",
            "text": "The Original first flow returns its result.",
        }
    ]
    assert all(item["control_binding"] for item in capsule["endpoints"])

    direct_ids = {
        item["method"]["stable_id"] for item in capsule["directMethods"]
    }
    assert direct_ids == {"method-entry", "method-shared"}
    evidence = capsule["designEvidence"]
    assert {item["className"] for item in evidence["Classes"]} == {
        "OriginalResult",
        "OriginalEntry",
        "OriginalNested",
        "OriginalDetail",
    }
    assert evidence["Relationships"] == [
        {
            "source": "OriginalResult",
            "target": "OriginalDetail",
            "type": "Association",
        }
    ]
    assert {
        "class_diagram:OriginalResult",
        "class_diagram:OriginalEntry",
        "class_diagram:OriginalNested",
        "class_diagram:OriginalDetail",
    } <= set(connected.source_refs)
    serialized = json.dumps(capsule, ensure_ascii=False)
    assert "UC-Z" not in serialized
    assert "OriginalOther" not in serialized
    assert "class_diagram:OriginalOther" not in connected.source_refs
    assert "method-unrelated" not in serialized
    assert "method-unbound" not in serialized
    # A direct call may name its immediate target, but that target's own
    # method-context must not be recursively promoted into this capsule.
    assert "method-nested" not in direct_ids
