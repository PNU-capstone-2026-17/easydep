from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.design.contracts.api_spec import ApiSpecModel
from app.design.schemas.class_model import BCEModel
from app.design.schemas.sequence_model import SequenceCollection
from app.implementation.domain.models import JobSpec
from app.implementation.generation.java_scaffold import (
    JavaScaffoldInput,
    render_java_scaffold,
)
from app.implementation.planning.design_context import (
    _backend_source_task_id,
    generate_backend_owner_tasks,
    generate_backend_unit_test_tasks,
)
from app.implementation.generation.operation_contracts import (
    build_generated_operation_contracts,
    write_generated_operation_contracts,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _spec_and_run(
    root: Path,
    *,
    extra_control: bool = False,
) -> tuple[JobSpec, Path]:
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
    if extra_control:
        bce_payload = json.loads((inputs / "bce.json").read_text(encoding="utf-8"))
        bce_payload["Classes"].insert(
            1,
            {
                "className": "AuditControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [],
            },
        )
        _write_json(inputs / "bce.json", bce_payload)
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
    if extra_control:
        (bce_root / "AuditControl.java").write_text(
            "public interface AuditControl {}\n",
            encoding="utf-8",
        )
    (bce_root / "Order.java").write_text("public class Order {}\n", encoding="utf-8")
    persistence_root = bce_root.parent / "persistence"
    (persistence_root / "entity").mkdir(parents=True)
    (persistence_root / "repository").mkdir(parents=True)
    (persistence_root / "entity" / "OrderEntity.java").write_text(
        "public class OrderEntity {}\n", encoding="utf-8"
    )
    (persistence_root / "repository" / "OrderRepository.java").write_text(
        "public interface OrderRepository {}\n", encoding="utf-8"
    )

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


def _plan(
    root: Path,
    *,
    generated_operation_contracts: bool = False,
    extra_control: bool = False,
    target_source: str | None = None,
):
    spec, run = _spec_and_run(root, extra_control=extra_control)
    if target_source is not None:
        target = (
            run
            / "application/src/main/java/com/example/orders/application/impl/"
            "OrderControlService.java"
        )
        target.parent.mkdir(parents=True)
        target.write_text(target_source, encoding="utf-8")
    if generated_operation_contracts:
        contracts = run / "reports/generated-operation-contracts.json"
        contracts.parent.mkdir(parents=True)
        _write_json(
            contracts,
            {
                "schemaVersion": "generated-operation-contracts/v1",
                "contracts": [
                    {
                        "operationId": "place-order",
                        "signature": "place(id: String): void",
                        "writableSource": (
                            "application/src/main/java/com/example/orders/application/impl/"
                            "OrderControlService.java"
                        ),
                        "interactionHints": [
                            {
                                "owner": "Order",
                                "method": "listAll",
                                "reason": "target_is_not_generated_spring_dependency",
                            }
                        ],
                        "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
                    },
                    {
                        "operationId": "other-operation",
                        "writableSource": (
                            "application/src/main/java/com/example/orders/bce/Order.java"
                        ),
                        "completionMarker": None,
                    },
                    "not-a-contract",
                ],
            },
        )
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
    assert task.task_id == (
        "implement-backend-com-example-orders-application-impl-ordercontrolservice"
    )
    assert task.owner == "backend"
    assert task.task_type == "backend-implementation"
    assert task.required_test_paths == []
    assert {"use_case:UC1", "use_case_spec:UC1"} <= set(task.source_refs)
    assert all("/src/test/" not in path for path in task.required_output_paths or [])
    assert task.allowed_write_paths == [
        "application/src/main/java/com/example/orders/application/impl/"
        "OrderControlService.java",
    ]
    assert task.allowed_write_roots == []
    assert (run / f"reports/implementation-tasks/{task.task_id}.task.json").is_file()

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
    assert "argument-free `run_task_check` once" in prompt
    assert "Generated class, sequence, RTM, collaborator, and wiring evidence may be incomplete" in prompt
    assert "A missing collaborator\n  or wiring entry alone is not an upstream gap" in prompt
    assert "conventional wiring and existing declared dependency APIs" in prompt
    assert "report_upstream_gap` with one supplied source reference" in prompt
    assert "first legal `file_editor` edit" in prompt
    assert "replace_source" not in prompt
    assert task.owner_tool_mode == "editor"
    assert context["requiredOutputPaths"] == task.required_output_paths
    assert context["verification"] == {
        "tool": "run_task_check",
        "policy": "the owner runs the focused check after an edit batch",
    }
    assert {
        "application/src/main/java/com/example/orders/persistence/entity/OrderEntity.java",
        "application/src/main/java/com/example/orders/persistence/repository/OrderRepository.java",
    } <= set(context["readSourcePaths"])
    method_context_paths = {
        str(item["path"])
        for item in source_index["methodContexts"]
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    assert method_context_paths.isdisjoint(context["readSourcePaths"])
    assert context["methodContextRoot"] not in context["readSourcePaths"]
    assert context["sourceIndexPath"] not in context["readSourcePaths"]
    assert not set(context["designInputs"].values()).intersection(
        context["readSourcePaths"]
    )


def test_marked_entity_owner_precedes_calling_service_and_its_unit(tmp_path: Path) -> None:
    spec, run = _spec_and_run(tmp_path)
    bce_path = spec.inputs["bceModel"]
    bce = json.loads(bce_path.read_text(encoding="utf-8"))
    bce["Classes"][1]["operations"] = [
        {"operationId": "order-save", "name": "save", "returnType": "Order"}
    ]
    _write_json(bce_path, bce)
    bce_model = BCEModel.model_validate(bce)
    files = render_java_scaffold(
        JavaScaffoldInput(
            bceModel=bce_model,
            basePackage=spec.base_package,
            applicationName="Orders",
        )
    )
    for relative_path, source in files.items():
        output = run / "application/src/main/java" / relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(source, encoding="utf-8")
    contracts = build_generated_operation_contracts(
        bce_model=bce_model,
        sequence_model=SequenceCollection.model_validate_json(
            spec.inputs["sequenceModel"].read_text(encoding="utf-8")
        ),
        api_model=ApiSpecModel.model_validate_json(
            spec.inputs["apiModel"].read_text(encoding="utf-8")
        ),
        base_package=spec.base_package,
    )
    write_generated_operation_contracts(run, contracts)
    service_source = (
        "application/src/main/java/com/example/orders/application/impl/"
        "OrderControlService.java"
    )
    entity_source = "application/src/main/java/com/example/orders/bce/Order.java"
    entity_contract = next(
        contract
        for contract in contracts.contracts
        if contract.writable_source == entity_source
    )
    assert entity_contract.completion_marker in (run / entity_source).read_text(
        encoding="utf-8"
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)
    by_source = {task.required_output_paths[0]: task for task in tasks}
    entity = by_source[entity_source]
    service = by_source[service_source]
    assert entity.depends_on == []
    assert entity.allowed_write_paths == [entity_source]
    assert entity.verification_profile["requiredAbsentMarkers"] == [
        {"path": entity_source, "markers": [entity_contract.completion_marker]}
    ]
    assert service.depends_on == [entity.task_id]
    units = generate_backend_unit_test_tasks(spec, run, tasks)
    service_unit = next(task for task in units if task.depends_on == [service.task_id])
    assert service_unit.depends_on == [service.task_id]


def test_backend_owner_includes_an_existing_generated_operation_contract_sidecar(
    tmp_path: Path,
) -> None:
    tasks, run = _plan(
        tmp_path,
        generated_operation_contracts=True,
        target_source=(
            "final class OrderControlService {\n"
            "  // EASYDEP-IMPLEMENT: complete place-order\n"
            "}\n"
        ),
    )

    task = tasks[0]
    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    source_index = json.loads((run / context["sourceIndexPath"]).read_text(encoding="utf-8"))
    sidecar = f"reports/implementation-tasks/{task.task_id}.operation-contracts.json"
    assert source_index["generatedOperationContractsPath"] == sidecar
    assert context["generatedOperationContractsPath"] == sidecar
    assert sidecar in context["readSourcePaths"]
    assert "reports/generated-operation-contracts.json" not in context["readSourcePaths"]
    assert json.loads((run / sidecar).read_text(encoding="utf-8")) == {
        "schemaVersion": "generated-operation-contracts/v1",
        "contracts": [
            {
                "operationId": "place-order",
                "signature": "place(id: String): void",
                "writableSource": (
                    "application/src/main/java/com/example/orders/application/impl/"
                    "OrderControlService.java"
                ),
                "interactionHints": [
                    {
                        "owner": "Order",
                        "method": "listAll",
                        "reason": "target_is_not_generated_spring_dependency",
                    }
                ],
                "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
            }
        ],
    }
    prompt = (run / task.prompt_file).read_text(encoding="utf-8")
    target_source = (run / task.allowed_write_paths[0]).read_text(encoding="utf-8")
    assert sidecar in prompt
    assert "## Implementation work packet" in prompt
    assert target_source.strip() in prompt
    assert "EASYDEP-IMPLEMENT" in prompt
    assert '"operationId": "place-order"' in prompt
    assert '"signature": "place(id: String): void"' in prompt
    assert '"writableSource"' not in prompt
    assert "authentication, build configuration, or migrations" in prompt
    assert "UnrelatedApi" not in prompt


def test_backend_owner_includes_only_imported_frozen_java_dependencies(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    java_root = run / "application/src/main/java/com/example/orders"
    sources = {
        "api/OrderApi.java": """
            package com.example.orders.api;
            import com.example.orders.api.model.PlaceOrderRequest;
            public interface OrderApi {
                void placeOrder(PlaceOrderRequest request);
            }
        """,
        "api/model/PlaceOrderRequest.java": """
            package com.example.orders.api.model;
            import com.example.orders.api.model.OrderItem;
            public record PlaceOrderRequest(OrderItem item) {}
        """,
        "api/model/OrderItem.java": """
            package com.example.orders.api.model;
            public record OrderItem(String id) {}
        """,
        "api/UnrelatedApi.java": """
            package com.example.orders.api;
            import com.example.orders.api.model.UnrelatedModel;
            public interface UnrelatedApi {
                UnrelatedModel load();
            }
        """,
        "api/model/UnrelatedModel.java": """
            package com.example.orders.api.model;
            public record UnrelatedModel(String value) {}
        """,
        "adapter/in/web/OrderApiController.java": """
            package com.example.orders.adapter.in.web;
            import com.example.orders.api.OrderApi;
            public final class OrderApiController implements OrderApi {
                // EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/orders
            }
        """,
    }
    for relative, source in sources.items():
        path = java_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        task = next(
            item
            for item in generate_backend_owner_tasks(spec, run)
            if item.required_output_paths
            == [
                "application/src/main/java/com/example/orders/adapter/in/web/"
                "OrderApiController.java"
            ]
        )

    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    readable = set(context["readSourcePaths"])
    assert {
        "application/src/main/java/com/example/orders/api/OrderApi.java",
        "application/src/main/java/com/example/orders/api/model/PlaceOrderRequest.java",
        "application/src/main/java/com/example/orders/api/model/OrderItem.java",
    } <= readable
    assert {
        "application/src/main/java/com/example/orders/api/UnrelatedApi.java",
        "application/src/main/java/com/example/orders/api/model/UnrelatedModel.java",
    }.isdisjoint(readable)


def test_backend_owner_expands_immutable_local_wildcard_packages_with_same_named_types(
    tmp_path: Path,
) -> None:
    target_source = """\
package com.example.orders.application.impl;
import com.example.orders.api.model.Course;
import com.example.orders.bce.*;
final class OrderControlService {
  private Course apiCourse;
  // EASYDEP-IMPLEMENT: complete place-order
}
"""
    spec, run = _spec_and_run(tmp_path)
    java_root = run / "application/src/main/java/com/example/orders"
    target = java_root / "application/impl/OrderControlService.java"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(target_source, encoding="utf-8")
    api_course = java_root / "api/model/Course.java"
    api_course.parent.mkdir(parents=True, exist_ok=True)
    api_course.write_text(
        "package com.example.orders.api.model;\npublic record Course(String id) {}\n",
        encoding="utf-8",
    )
    bce_course = java_root / "bce/Course.java"
    bce_course.write_text(
        "package com.example.orders.bce;\npublic record Course(String title) {}\n",
        encoding="utf-8",
    )
    bce_term = java_root / "bce/Term.java"
    bce_term.write_text(
        "package com.example.orders.bce;\npublic record Term(String name) {}\n",
        encoding="utf-8",
    )
    bce_order = java_root / "bce/Order.java"
    bce_order.write_text(
        "package com.example.orders.bce;\npublic class Order {}\n",
        encoding="utf-8",
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)
    task = tasks[0]
    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    readable = set(context["readSourcePaths"])
    assert {
        "application/src/main/java/com/example/orders/api/model/Course.java",
        "application/src/main/java/com/example/orders/bce/Course.java",
        "application/src/main/java/com/example/orders/bce/Term.java",
        "application/src/main/java/com/example/orders/bce/Order.java",
    } <= readable


def test_backend_owner_omits_marker_free_controller_without_empty_task(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    controller = (
        run
        / "application/src/main/java/com/example/orders/adapter/in/web/OrderApiController.java"
    )
    controller.parent.mkdir(parents=True, exist_ok=True)
    controller.write_text(
        "public final class OrderApiController {}\n",
        encoding="utf-8",
    )
    contracts = run / "reports/generated-operation-contracts.json"
    contracts.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        contracts,
        {
            "schemaVersion": "generated-operation-contracts/v1",
            "contracts": [
                {
                    "operationId": "place-order",
                    "writableSource": (
                        "application/src/main/java/com/example/orders/application/impl/"
                        "OrderControlService.java"
                    ),
                    "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
                }
            ],
        },
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)

    assert [task.required_output_paths for task in tasks] == [
        [
            "application/src/main/java/com/example/orders/application/impl/"
            "OrderControlService.java"
        ]
    ]
    assert all(task.required_output_paths for task in tasks)


def test_backend_owner_controller_retains_endpoint_contract_and_requires_body_marker(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    controller_path = (
        run
        / "application/src/main/java/com/example/orders/adapter/in/web/OrderApiController.java"
    )
    controller_path.parent.mkdir(parents=True, exist_ok=True)
    controller_marker = "EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/orders"
    controller_path.write_text(
        f'throw new UnsupportedOperationException("{controller_marker}");\n',
        encoding="utf-8",
    )
    api_path = run / "application/src/main/java/com/example/orders/api/OrderApi.java"
    api_path.parent.mkdir(parents=True, exist_ok=True)
    api_path.write_text("public interface OrderApi {}\n", encoding="utf-8")
    (run / "reports").mkdir(parents=True, exist_ok=True)
    _write_json(
        run / "reports/generated-operation-contracts.json",
        {
            "schemaVersion": "generated-operation-contracts/v1",
            "contracts": [
                {
                    "operationId": "place-order",
                    "writableSource": (
                        "application/src/main/java/com/example/orders/application/impl/"
                        "OrderControlService.java"
                    ),
                    "endpoints": [{"method": "POST", "path": "/orders"}],
                    "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
                },
                {
                    "operationId": "other-operation",
                    "writableSource": (
                        "application/src/main/java/com/example/orders/bce/Order.java"
                    ),
                    "endpoints": [{"method": "GET", "path": "/orders"}],
                },
            ],
        },
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        task = next(
            item
            for item in generate_backend_owner_tasks(spec, run)
            if item.required_output_paths == [
                "application/src/main/java/com/example/orders/adapter/in/web/"
                "OrderApiController.java"
            ]
        )

    sidecar = run / f"reports/implementation-tasks/{task.task_id}.operation-contracts.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["contracts"] == [
        {
            "operationId": "place-order",
            "writableSource": (
                "application/src/main/java/com/example/orders/application/impl/"
                "OrderControlService.java"
            ),
            "endpoints": [{"method": "POST", "path": "/orders"}],
            "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
        }
    ]
    assert {
        "path": "application/src/main/java/com/example/orders/adapter/in/web/"
        "OrderApiController.java",
        "markers": [controller_marker],
    } in task.verification_profile["requiredAbsentMarkers"]


def test_backend_owner_excludes_unrelated_generated_bce_enum_declaration(tmp_path: Path) -> None:
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
    assert "application/src/main/java/com/example/orders/bce/OrderStatus.java" not in context[
        "readSourcePaths"
    ]


def test_backend_owner_context_keeps_contract_dependencies_and_persistence_evidence(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    service_path = run / (
        "application/src/main/java/com/example/orders/application/impl/"
        "OrderControlService.java"
    )
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        """package com.example.orders.application.impl;
import com.example.orders.bce.Order;
import com.example.orders.persistence.repository.OrderRepository;
public class OrderControlService {}
""",
        encoding="utf-8",
    )
    repository_path = run / (
        "application/src/main/java/com/example/orders/persistence/repository/"
        "OrderRepository.java"
    )
    repository_path.parent.mkdir(parents=True, exist_ok=True)
    repository_path.write_text("interface OrderRepository {}\n", encoding="utf-8")
    unrelated = run / "application/src/main/java/com/example/orders/bce/Unrelated.java"
    unrelated.write_text("class Unrelated {}\n", encoding="utf-8")
    unrelated_repository = run / (
        "application/src/main/java/com/example/orders/persistence/repository/"
        "UnrelatedRepository.java"
    )
    unrelated_repository.write_text("interface UnrelatedRepository {}\n", encoding="utf-8")
    contracts = run / "reports/generated-operation-contracts.json"
    contracts.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        contracts,
        {
            "schemaVersion": "generated-operation-contracts/v1",
            "contracts": [
                {
                    "operationId": "place-order",
                    "source": "application/src/main/java/com/example/orders/bce/OrderControl.java",
                    "writableSource": (
                        "application/src/main/java/com/example/orders/application/impl/"
                        "OrderControlService.java"
                    ),
                    "constructorDependencies": [
                        "com.example.orders.persistence.repository.OrderRepository"
                    ],
                    "collaborators": [
                        {"ownerFqcn": "com.example.orders.bce.Order"}
                    ],
                    "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
                }
            ],
        },
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        task = generate_backend_owner_tasks(spec, run)[0]

    readable = set(
        json.loads((run / task.context_file).read_text(encoding="utf-8"))["readSourcePaths"]
    )
    assert service_path.relative_to(run).as_posix() in readable
    assert repository_path.relative_to(run).as_posix() in readable
    assert "application/src/main/java/com/example/orders/bce/Order.java" in readable
    assert "application/src/main/java/com/example/orders/bce/Unrelated.java" not in readable
    assert (
        "application/src/main/java/com/example/orders/persistence/repository/"
        "UnrelatedRepository.java"
    ) in readable
    assert task.use_case_ids == ["UC1"]
    assert task.requirement_ids == ["REQ1"]
    assert task.source_refs == sorted(set(task.source_refs))
    assert all("UC2" not in ref and "REQ2" not in ref for ref in task.source_refs)


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
    reports = run / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    _write_json(
        reports / "generated-operation-contracts.json",
        {
            "schemaVersion": "generated-operation-contracts/v1",
            "contracts": [
                {
                    "operationId": "place-order",
                    "writableSource": (
                        "application/src/main/java/com/example/orders/application/impl/"
                        "OrderControlService.java"
                    ),
                    "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
                },
                {
                    "operationId": "confirm-order",
                    "writableSource": source_path,
                    "completionMarker": None,
                },
            ],
        },
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)

    domain = next(item for item in tasks if item.owner == "backend" and not any(
        "/adapter/in/web/" in path for path in item.required_output_paths or []
    ))
    assert source_path not in (domain.required_output_paths or [])
    assert {
        "path": "application/src/main/java/com/example/orders/application/impl/OrderControlService.java",
        "markers": ["EASYDEP-IMPLEMENT: complete OrderControl::place(id:String)"],
    } in domain.verification_profile["requiredAbsentMarkers"]


def test_backend_owner_plan_is_deterministic_across_equivalent_runs(tmp_path: Path) -> None:
    first, _run = _plan(tmp_path / "first")
    second, _run = _plan(tmp_path / "second")

    assert [task.task_id for task in first] == [task.task_id for task in second]
    assert [task.required_output_paths for task in first] == [
        task.required_output_paths for task in second
    ]
    assert [task.required_test_paths for task in first] == [[]]
    assert [task.required_test_paths for task in second] == [[]]
    multi_source = [
        "application/src/main/java/com/example/orders/application/impl/OrderControlService.java",
        "application/src/main/java/com/example/orders/bce/Order.java",
    ]
    assert _backend_source_task_id(multi_source) == _backend_source_task_id(
        list(reversed(multi_source))
    )
    assert _backend_source_task_id(multi_source).startswith(
        "implement-backend-domain-core-"
    )
    assert len(_backend_source_task_id(multi_source)) <= 48


def test_backend_owner_splits_non_controller_sources_into_single_source_tasks(
    tmp_path: Path,
) -> None:
    tasks, _run = _plan(tmp_path, extra_control=True)

    assert len(tasks) == 2
    assert all(len(task.required_output_paths or []) == 1 for task in tasks)
    assert [task.required_output_paths for task in tasks] == [
        [
            "application/src/main/java/com/example/orders/application/impl/"
            "AuditControlService.java"
        ],
        [
            "application/src/main/java/com/example/orders/application/impl/"
            "OrderControlService.java"
        ],
    ]
    assert [task.task_id for task in tasks] == [
        "implement-backend-com-example-orders-application-impl-auditcontrolservice",
        "implement-backend-com-example-orders-application-impl-ordercontrolservice",
    ]


def test_backend_owner_groups_all_markers_for_one_source(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    bce_model = json.loads(spec.inputs["bceModel"].read_text(encoding="utf-8"))
    bce_model["Classes"][0]["operations"] = [
        {
            "operationId": "place-order",
            "stableId": "op_place",
            "name": "place",
            "parameters": [{"name": "id", "type": "String"}],
            "returnType": "void",
        },
        {
            "operationId": "create-order",
            "stableId": "op_create",
            "name": "create",
            "parameters": [],
            "returnType": "void",
        },
    ]
    _write_json(spec.inputs["bceModel"], bce_model)
    _write_json(
        spec.inputs["requirements"],
        [
            {"id": "REQ1", "text": "Place an order"},
            {"id": "REQ2", "text": "Create an order"},
        ],
    )
    _write_json(
        spec.inputs["useCaseSpec"],
        {
            "useCaseSpecs": [
                {
                    "use_case_id": "UC1",
                    "requirement_ids": ["REQ1"],
                    "main_scenario": [
                        {"step_number": 1, "text": "Place scenario only"}
                    ],
                },
                {
                    "use_case_id": "UC2",
                    "requirement_ids": ["REQ2"],
                    "main_scenario": [
                        {"step_number": 1, "text": "Create scenario only"}
                    ],
                },
            ]
        },
    )
    _write_json(
        spec.inputs["sequenceModel"],
        {
            "Diagrams": [
                {
                    "use_case_id": "UC1",
                    "use_case_name": "Place order",
                    "Participants": [
                            {"name": "Actor", "alias": "Actor", "kind": "actor", "participant_ref": "actor_uc1"},
                        {
                            "name": "OrderControl",
                            "alias": "OrderControl",
                            "kind": "control",
                            "source_class": "OrderControl",
                            "participant_ref": "class_order_control",
                        },
                    ],
                    "Messages": [
                        {
                            "source": "Actor",
                            "target": "OrderControl",
                            "label": "place(id:String)",
                            "type": "sync",
                            "use_case_ids": ["UC1"],
                            "step_ids": ["UC1:main:1"],
                                "call_id": "place::call:1",
                                "call_ref": "call_place",
                                "operation_ref": "op_place",
                            "arguments": [
                                {
                                    "parameter": "id",
                                    "type": "String",
                                    "source_kind": "input",
                                    "source_ref": "UC1:main:1#id",
                                }
                            ],
                        },
                        {
                            "source": "OrderControl",
                            "target": "Actor",
                            "label": "void",
                            "type": "return",
                            "use_case_ids": ["UC1"],
                            "step_ids": ["UC1:main:1"],
                                "reply_to": "place::call:1",
                                "call_ref": "call_place",
                                "operation_ref": "op_place",
                        },
                    ],
                },
                {
                    "use_case_id": "UC2",
                    "use_case_name": "Create order",
                    "Participants": [
                            {"name": "Actor", "alias": "Actor", "kind": "actor", "participant_ref": "actor_uc2"},
                        {
                            "name": "OrderControl",
                            "alias": "OrderControl",
                            "kind": "control",
                            "source_class": "OrderControl",
                            "participant_ref": "class_order_control",
                        },
                    ],
                    "Messages": [
                        {
                            "source": "Actor",
                            "target": "OrderControl",
                            "label": "create()",
                            "type": "sync",
                            "use_case_ids": ["UC2"],
                            "step_ids": ["UC2:main:1"],
                                "call_id": "create::call:1",
                                "call_ref": "call_create",
                                "operation_ref": "op_create",
                            "arguments": [],
                        },
                        {
                            "source": "OrderControl",
                            "target": "Actor",
                            "label": "void",
                            "type": "return",
                            "use_case_ids": ["UC2"],
                            "step_ids": ["UC2:main:1"],
                                "reply_to": "create::call:1",
                                "call_ref": "call_create",
                                "operation_ref": "op_create",
                        },
                    ],
                },
            ],
            "MethodProposals": [],
        },
    )
    source = (
        "application/src/main/java/com/example/orders/application/impl/"
        "OrderControlService.java"
    )
    source_path = run / source
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "// EASYDEP-IMPLEMENT: complete create-order\n"
        "// EASYDEP-IMPLEMENT: complete place-order\n",
        encoding="utf-8",
    )
    reports = run / "reports"
    reports.mkdir(parents=True)
    _write_json(
        reports / "generated-operation-contracts.json",
        {
            "schemaVersion": "generated-operation-contracts/v1",
            "contracts": [
                {
                    "operationId": "place-order",
                    "stableId": "op_place",
                    "writableSource": source,
                    "completionMarker": "EASYDEP-IMPLEMENT: complete place-order",
                },
                {
                    "operationId": "create-order",
                    "stableId": "op_create",
                    "writableSource": source,
                    "completionMarker": "EASYDEP-IMPLEMENT: complete create-order",
                },
            ],
        },
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)

    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == (
        "implement-backend-com-example-orders-application-impl-ordercontrolservice"
    )
    assert task.required_output_paths == [source]
    assert task.depends_on == []
    assert task.owner_tool_mode == "editor"
    expected_markers = [
        "EASYDEP-IMPLEMENT: complete op_create",
        "EASYDEP-IMPLEMENT: complete op_place",
    ]
    assert task.verification_profile["requiredAbsentMarkers"] == [
        {"path": source, "markers": expected_markers}
    ]
    sidecar = json.loads(
        (run / f"reports/implementation-tasks/{task.task_id}.operation-contracts.json").read_text(
            encoding="utf-8"
        )
    )
    assert [contract["operationId"] for contract in sidecar["contracts"]] == [
        "place-order",
        "create-order",
    ]
    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    assert context["dependsOn"] == []
    assert context["useCaseIds"] == ["UC1", "UC2"]
    assert context["requirementIds"] == ["REQ1", "REQ2"]
    assert {"requirement:REQ1", "requirement:REQ2"}.issubset(task.source_refs)
    source_index = json.loads(
        (run / context["sourceIndexPath"]).read_text(encoding="utf-8")
    )
    assert {item["stableId"] for item in source_index["methodContexts"]} == {
        "op_create",
        "op_place",
    }
    prompt = (run / task.prompt_file).read_text(encoding="utf-8")
    assert "Resolve every `EASYDEP-IMPLEMENT`" in prompt
    assert "Assigned operation behavior" not in prompt
    assert "### Owned operation behavior" in prompt
    assert "place-order" in prompt and "create-order" in prompt
    assert "UC1" in prompt and "UC2" in prompt
    assert "REQ1" in prompt and "REQ2" in prompt
    assert "Place scenario only" in prompt
    assert "Create scenario only" in prompt
    assert "designInputs" not in prompt
    assert "sourcePaths" not in prompt


def test_backend_owner_file_task_includes_only_its_method_behavior_capsule(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path, extra_control=True)
    bce_model = json.loads(spec.inputs["bceModel"].read_text(encoding="utf-8"))
    bce_model["Classes"][0]["operations"][0]["stepRefs"] = [
        "UC1:main:1",
        "UC1:extension:1:1:1",
    ]
    bce_model["Classes"][0]["operations"][0]["stableId"] = "op_place"
    bce_model["Classes"][1]["operations"] = [
        {
            "operationId": "audit-order",
            "stableId": "op_audit",
            "name": "audit",
            "parameters": [],
            "returnType": "void",
            "stepRefs": ["UC2:main:1"],
        }
    ]
    bce_model["Classes"][1]["use_case_ids"] = ["UC2"]
    _write_json(spec.inputs["bceModel"], bce_model)
    _write_json(
        spec.inputs["requirements"],
        [
            {"id": "REQ1", "text": "Place an order"},
            {"id": "REQ2", "text": "Audit an order"},
        ],
    )
    _write_json(
        spec.inputs["useCaseSpec"],
        {
            "useCaseSpecs": [
                {
                    "use_case_id": "UC1",
                    "requirement_ids": ["REQ1"],
                    "main_scenario": [
                        {"step_number": 1, "sentence": "Create the order"}
                    ],
                    "extensions": [
                        {
                            "branch_step": 1,
                            "condition": "Inventory is unavailable",
                            "outcome": "Reject the order",
                            "handling_steps": [
                                {"sub_step": 1, "sentence": "Report the shortage"}
                            ],
                        }
                    ],
                },
                {
                    "use_case_id": "UC2",
                    "requirement_ids": ["REQ2"],
                    "main_scenario": [
                        {"step_number": 1, "sentence": "Record the audit"}
                    ],
                },
            ]
        },
    )
    _write_json(
        spec.inputs["sequenceModel"],
        {
            "Diagrams": [
                {
                    "use_case_id": "UC1",
                    "Participants": [
                        {"name": "Actor", "alias": "Actor", "kind": "actor", "participant_ref": "actor_uc1"},
                        {"name": "OrderControl", "alias": "OrderControl", "kind": "control", "source_class": "OrderControl", "participant_ref": "class_order_control"},
                    ],
                    "Messages": [
                        {"source": "Actor", "target": "OrderControl", "label": "place(id:String)", "type": "sync", "use_case_ids": ["UC1"], "step_ids": ["UC1:main:1", "UC1:extension:1:1:1"], "call_id": "place::call:1", "call_ref": "call_place", "operation_ref": "op_place", "arguments": [{"parameter": "id", "type": "String", "source_kind": "input", "source_ref": "UC1:main:1#id"}]},
                        {"source": "OrderControl", "target": "Actor", "label": "void", "type": "return", "use_case_ids": ["UC1"], "step_ids": ["UC1:main:1", "UC1:extension:1:1:1"], "reply_to": "place::call:1", "call_ref": "call_place", "operation_ref": "op_place"},
                    ],
                },
                {
                    "use_case_id": "UC2",
                    "Participants": [
                        {"name": "Actor", "alias": "Actor", "kind": "actor", "participant_ref": "actor_uc2"},
                        {"name": "AuditControl", "alias": "AuditControl", "kind": "control", "source_class": "AuditControl", "participant_ref": "class_audit_control"},
                    ],
                    "Messages": [
                        {"source": "Actor", "target": "AuditControl", "label": "audit()", "type": "sync", "use_case_ids": ["UC2"], "step_ids": ["UC2:main:1"], "call_id": "audit::call:1", "call_ref": "call_audit", "operation_ref": "op_audit", "arguments": []},
                        {"source": "AuditControl", "target": "Actor", "label": "void", "type": "return", "use_case_ids": ["UC2"], "step_ids": ["UC2:main:1"], "reply_to": "audit::call:1", "call_ref": "call_audit", "operation_ref": "op_audit"},
                    ],
                },
            ],
            "MethodProposals": [],
        },
    )
    reports = run / "reports"
    reports.mkdir(parents=True)
    _write_json(
        reports / "generated-operation-contracts.json",
        {
            "schemaVersion": "generated-operation-contracts/v1",
            "contracts": [
                {
                    "operationId": "place-order",
                    "stableId": "op_place",
                    "writableSource": (
                        "application/src/main/java/com/example/orders/application/impl/"
                        "OrderControlService.java"
                    ),
                    "completionMarker": "EASYDEP-IMPLEMENT: complete op_place",
                }
            ],
        },
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)

    task = next(
        item
        for item in tasks
        if item.required_output_paths
        == [
            "application/src/main/java/com/example/orders/application/impl/"
            "OrderControlService.java"
        ]
    )
    context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
    source_index = json.loads(
        (run / context["sourceIndexPath"]).read_text(encoding="utf-8")
    )
    assert len(source_index["methodContexts"]) == 1
    prompt = (run / task.prompt_file).read_text(encoding="utf-8")
    assert "### Owned operation behavior" in prompt
    assert "Create the order" in prompt
    assert "Inventory is unavailable" in prompt
    assert "Reject the order" in prompt
    assert "Report the shortage" in prompt
    assert "Record the audit" not in prompt
    assert "REQ2" not in prompt
    assert len(prompt) < 12_000


def test_backend_owner_keeps_controller_only_marker_without_operation_contract(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    controller = (
        run
        / "application/src/main/java/com/example/orders/adapter/in/web/OrderApiController.java"
    )
    controller.parent.mkdir(parents=True, exist_ok=True)
    marker = "EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/orders"
    controller.write_text(f"// {marker}\n", encoding="utf-8")
    api_path = run / "application/src/main/java/com/example/orders/api/OrderApi.java"
    api_path.parent.mkdir(parents=True, exist_ok=True)
    api_path.write_text("public interface OrderApi {}\n", encoding="utf-8")
    reports = run / "reports"
    reports.mkdir(parents=True)
    _write_json(
        reports / "generated-operation-contracts.json",
        {"schemaVersion": "generated-operation-contracts/v1", "contracts": []},
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        tasks = generate_backend_owner_tasks(spec, run)

    assert len(tasks) == 1
    assert tasks[0].required_output_paths == [
        "application/src/main/java/com/example/orders/adapter/in/web/OrderApiController.java"
    ]
    assert tasks[0].verification_profile["requiredAbsentMarkers"] == [
        {
            "path": "application/src/main/java/com/example/orders/adapter/in/web/"
            "OrderApiController.java",
            "markers": [marker],
        }
    ]


def test_controller_task_scope_comes_from_its_owned_endpoint_contracts(tmp_path: Path) -> None:
    spec, run = _spec_and_run(tmp_path)
    spec.inputs["apiModel"].write_text(
        json.dumps(
            {
                "Endpoints": [
                    {
                        "operation_id": "admin-list",
                        "method": "get",
                        "path": "/admin/items",
                        "source_classes": ["OrderControl"],
                        "use_case_ids": ["UC2"],
                        "control_binding": {
                            "control": "OrderControl",
                            "method": "list",
                            "arguments": [],
                            "outcomes": [{"status": 200, "outcome": "listed"}],
                        },
                    },
                    {
                        "operation_id": "admin-create",
                        "method": "post",
                        "path": "/admin/items",
                        "source_classes": ["OrderControl"],
                        "use_case_ids": ["UC3"],
                        "control_binding": {
                            "control": "OrderControl",
                            "method": "create",
                            "arguments": [],
                            "outcomes": [{"status": 201, "outcome": "created"}],
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    spec.inputs["requirements"].write_text(
        json.dumps([{"id": f"RR{index}"} for index in range(1, 17)]),
        encoding="utf-8",
    )
    spec.inputs["useCaseSpec"].write_text(
        json.dumps(
            {
                "useCaseSpecs": [
                    {
                        "use_case_id": f"UC{index}",
                        "requirement_ids": [f"RR{index}"],
                    }
                    for index in range(1, 12)
                ]
            }
        ),
        encoding="utf-8",
    )
    api_path = run / "application/src/main/java/com/example/orders/api/OrderApi.java"
    api_path.parent.mkdir(parents=True, exist_ok=True)
    api_path.write_text(
        "interface OrderApi {\n"
        "  // GET /admin/items\n"
        "  // POST /admin/items\n"
        "}\n",
        encoding="utf-8",
    )
    controller = (
        run
        / "application/src/main/java/com/example/orders/adapter/in/web/OrderApiController.java"
    )
    controller.parent.mkdir(parents=True, exist_ok=True)
    controller.write_text(
        "// EASYDEP_CONTROLLER_BODY_REQUIRED:GET:/admin/items\n"
        "// EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/admin/items\n",
        encoding="utf-8",
    )
    contracts_path = run / "reports/generated-operation-contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        contracts_path,
        {
            "schemaVersion": "generated-operation-contracts/v1",
            "contracts": [
                {
                    "operationId": operation_id,
                    "writableSource": "application/src/main/java/com/example/orders/"
                    "application/impl/OrderControlService.java",
                    "endpoints": [{"method": method, "path": "/admin/items"}],
                    "completionMarker": f"EASYDEP-IMPLEMENT: complete {operation_id}",
                }
                for operation_id, method in (
                    ("admin-list", "GET"),
                    ("admin-create", "POST"),
                )
            ],
        },
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={"model": "test-model"},
    ):
        task = next(
            item
            for item in generate_backend_owner_tasks(spec, run)
            if item.required_output_paths
            == [
                "application/src/main/java/com/example/orders/adapter/in/web/"
                "OrderApiController.java"
            ]
        )

    assert task.use_case_ids == ["UC2", "UC3"]
    assert task.owner_tool_mode == "restricted"
    assert task.requirement_ids == ["RR2", "RR3"]
    assert task.source_refs == [
        "api:admin-create",
        "api:admin-list",
        "use_case:UC2",
        "use_case:UC3",
        "use_case_spec:UC2",
        "use_case_spec:UC3",
    ]
