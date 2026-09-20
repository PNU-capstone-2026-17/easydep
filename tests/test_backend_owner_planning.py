from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.implementation.agents.verification.build import (
    WorkspaceVerificationError,
    verify_agent_workspace,
)
from app.implementation.domain.models import JobSpec
from app.implementation.planning.design_context import generate_backend_owner_tasks


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _spec_and_run(root: Path) -> tuple[JobSpec, Path]:
    run = root / "run"
    package = "com.example.orders"
    package_path = package.replace(".", "/")
    inputs = root / "inputs"
    inputs.mkdir(parents=True)

    _write_json(
        inputs / "bce.json",
        {
            "Classes": [
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC1"],
                    "operations": [
                        {
                            "operationId": "place-order",
                            "name": "place",
                            "parameters": [{"name": "id", "type": "String"}],
                            "returnType": "void",
                            "stepRefs": ["UC1:main:1"],
                        }
                    ],
                },
                {
                    "className": "Order",
                    "stereotype": "Entity",
                    "use_case_ids": ["UC1"],
                    "operations": [],
                },
            ],
            "DataTypes": [],
            "Relationships": [],
            "Collaborations": [],
        },
    )
    _write_json(inputs / "sequence.json", {"Diagrams": [], "MethodProposals": []})
    _write_json(
        inputs / "api.json",
        {
            "Endpoints": [
                {
                    "operation_id": "placeOrder",
                    "method": "post",
                    "path": "/orders",
                    "use_case_ids": ["UC1"],
                }
            ]
        },
    )
    _write_json(inputs / "requirements.json", [{"id": "REQ1", "text": "Place an order"}])
    _write_json(
        inputs / "use-cases.json",
        {"useCaseSpecs": [{"use_case_id": "UC1", "requirement_ids": ["REQ1"]}]},
    )

    bce_root = run / "application/src/main/java" / package_path / "bce"
    bce_root.mkdir(parents=True)
    (bce_root / "OrderControl.java").write_text("public interface OrderControl {}\n", encoding="utf-8")
    (bce_root / "Order.java").write_text("public class Order {}\n", encoding="utf-8")

    spec = JobSpec(
        job_type="implementation",
        feedback="",
        name="orders",
        workspace_root=root,
        inputs={
            "bceModel": inputs / "bce.json",
            "sequenceModel": inputs / "sequence.json",
            "apiModel": inputs / "api.json",
            "requirements": inputs / "requirements.json",
            "useCaseSpec": inputs / "use-cases.json",
        },
        required_inputs=[],
        base_package=package,
        allow_assumptions=False,
        verify_compile=True,
        output_root=root / "output",
        agent_mode="openhands",
        agent_temperature=0.2,
        agent_max_output_tokens=8192,
    )
    return spec, run


def _plan(root: Path, *, generated_operation_contracts: bool = False):
    spec, run = _spec_and_run(root)
    if generated_operation_contracts:
        contracts = run / "reports/generated-operation-contracts.json"
        contracts.parent.mkdir(parents=True)
        _write_json(contracts, {"schemaVersion": "generated-operation-contracts/v1"})
    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        return generate_backend_owner_tasks(spec, run), run


def test_backend_plan_persists_one_cohesive_owner_without_focused_test_contract(
    tmp_path: Path,
) -> None:
    tasks, run = _plan(tmp_path)

    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == "implement-backend-application"
    assert task.owner == "backend"
    assert task.task_type == "backend-implementation"
    assert task.required_test_paths == []
    assert {"use_case:UC1", "use_case_spec:UC1"} <= set(task.source_refs)
    assert all("/src/test/" not in path for path in task.required_output_paths or [])
    assert task.allowed_write_roots == [
        "application/src/main/java/com/example/orders",
        "application/src/main/resources",
    ]
    assert (run / "reports/implementation-tasks/implement-backend-application.task.json").is_file()

    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    assert context["taskId"] == task.task_id
    assert context["sourceIndexPath"].endswith(".source-index.json")
    source_index = json.loads((run / context["sourceIndexPath"]).read_text(encoding="utf-8"))
    assert source_index["methodContexts"]
    assert "generatedOperationContractsPath" not in source_index

    prompt = (run / task.prompt_file).read_text(encoding="utf-8")
    assert "JUnit" not in prompt
    assert "focused test" not in prompt
    assert "EASYDEP-IMPLEMENT" in prompt
    assert "batch-read" not in prompt
    assert "method-context" not in prompt
    assert "raw design input" not in prompt
    assert "run_task_check" in prompt
    assert context["requiredOutputPaths"] == task.required_output_paths
    assert context["verification"] == {
        "tool": "run_task_check",
        "policy": "run once after the edit batch; finish when it passes",
    }
    assert not {
        str(item["path"])
        for item in source_index["methodContexts"]
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }.intersection(context["readSourcePaths"])
    assert not set(context["designInputs"].values()).intersection(
        context["readSourcePaths"]
    )


def test_backend_owner_includes_an_existing_generated_operation_contract_sidecar(
    tmp_path: Path,
) -> None:
    tasks, run = _plan(tmp_path, generated_operation_contracts=True)

    task = tasks[0]
    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    source_index = json.loads((run / context["sourceIndexPath"]).read_text(encoding="utf-8"))
    sidecar = "reports/generated-operation-contracts.json"
    assert source_index["generatedOperationContractsPath"] == sidecar
    assert context["generatedOperationContractsPath"] == sidecar
    assert sidecar in context["readSourcePaths"]
    assert sidecar in (run / task.prompt_file).read_text(encoding="utf-8")


def test_backend_owner_includes_generated_bce_enum_declaration(tmp_path: Path) -> None:
    spec, run = _spec_and_run(tmp_path)
    bce_path = spec.inputs["bceModel"]
    bce_model = json.loads(bce_path.read_text(encoding="utf-8"))
    bce_model["DataTypes"] = [{"name": "OrderStatus", "kind": "enumeration", "values": ["NEW"]}]
    bce_path.write_text(json.dumps(bce_model), encoding="utf-8")
    enum_path = run / "application/src/main/java/com/example/orders/bce/OrderStatus.java"
    enum_path.write_text("public enum OrderStatus { NEW }\n", encoding="utf-8")

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)

    context = json.loads((run / tasks[0].context_file).read_text(encoding="utf-8"))
    assert "application/src/main/java/com/example/orders/bce/OrderStatus.java" in context[
        "readSourcePaths"
    ]


def test_backend_owner_requires_its_generated_completion_markers_to_be_absent(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    bce_model = json.loads(spec.inputs["bceModel"].read_text(encoding="utf-8"))
    bce_model["Classes"][1]["operations"] = [
        {
            "operationId": "confirm-order",
            "stableId": "confirm-order",
            "name": "confirm",
            "parameters": [],
            "returnType": "void",
        }
    ]
    _write_json(spec.inputs["bceModel"], bce_model)
    source_path = "application/src/main/java/com/example/orders/bce/Order.java"
    (run / source_path).write_text(
        "// EASYDEP-IMPLEMENT: complete confirm-order\\n",
        encoding="utf-8",
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        task = generate_backend_owner_tasks(spec, run)[0]

    assert task.verification_profile == {
        "requiredAbsentMarkers": [
            {
                "path": (
                    "application/src/main/java/com/example/orders/application/impl/"
                    "OrderControlService.java"
                ),
                "markers": [
                    "EASYDEP-IMPLEMENT: complete OrderControl::place(id:String)"
                ],
            },
            {
                "path": source_path,
                "markers": ["EASYDEP-IMPLEMENT: complete confirm-order"],
            }
        ]
    }
    with pytest.raises(WorkspaceVerificationError) as raised:
        verify_agent_workspace(
            run,
            task.task_type,
            task.allowed_write_paths,
            task.verification_profile,
        )
    assert raised.value.evidence["command"] == ["implementation-marker-contract"]
    assert raised.value.evidence["remainingMarkers"] == [
        {"path": source_path, "marker": "EASYDEP-IMPLEMENT: complete confirm-order"}
    ]


def test_backend_owner_plan_is_deterministic_across_equivalent_runs(tmp_path: Path) -> None:
    first, _run = _plan(tmp_path / "first")
    second, _run = _plan(tmp_path / "second")

    assert [task.task_id for task in first] == [task.task_id for task in second]
    assert [task.required_output_paths for task in first] == [
        task.required_output_paths for task in second
    ]
    assert [task.required_test_paths for task in first] == [[]]
    assert [task.required_test_paths for task in second] == [[]]
