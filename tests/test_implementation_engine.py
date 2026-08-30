from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.implementation.agents.verification.build import (
    api_adapter_contract_violations,
    boundary_adapter_contract_violations,
    persistence_entity_schema_violations,
    production_placeholder_markers,
    verify_run_workspace,
)
from app.implementation.agents.verification.e2e import e2e_contract_violations
from app.implementation.delivery.container import render_deployment
from app.implementation.delivery.terraform import render_iac
from app.implementation.domain.implementation_ir import build_implementation_ir
from app.implementation.generation.orchestrator import load_job
from app.implementation.planning.design_context import (
    generate_api_adapter_tasks,
    generate_boundary_adapter_tasks,
    generate_gateway_adapter_tasks,
    generate_implementation_tasks,
    generate_wiring_tasks,
)
from app.implementation.workflows.conformance import (
    SourceDesignConformanceError,
    capture_generated_contracts,
    verify_source_design_conformance,
)


def test_design_inputs_build_ir_and_required_adapter_tasks(tmp_path: Path) -> None:
    """설계 산출물이 구현 계획의 핵심 타입과 작업으로 연결되는지 확인한다."""
    (tmp_path / "bce.puml").write_text(
        """class OrderService <<Control>> {
  + createOrder(customerId: string): Order
}
class CheckoutScreen <<Boundary>> { + submit(customerId: string) }
class Order <<Entity>> { - orderId: string }
class OrderStoreGateway <<Gateway>> { + save(order: Order): Order }
class PaymentGateway <<Gateway>> { + charge(orderId: string): boolean }
""",
        encoding="utf-8",
    )
    (tmp_path / "sequence.puml").write_text(
        """CheckoutScreen -> OrderService : createOrder(customerId)
OrderService -> PaymentGateway : charge(orderId)
alt invalid order
OrderService --> CheckoutScreen : validation error
end
""",
        encoding="utf-8",
    )
    (tmp_path / "sequence-model.json").write_text(
        json.dumps(
            {
                "Diagrams": [{
                    "use_case_id": "UC_ORDER",
                    "use_case_name": "Create order",
                    "Participants": [
                        {
                            "name": "CheckoutScreen",
                            "alias": "screen",
                            "kind": "boundary",
                            "source_class": "CheckoutScreen",
                        },
                        {
                            "name": "OrderService",
                            "alias": "service",
                            "kind": "control",
                            "source_class": "OrderService",
                        },
                    ],
                    "Messages": [
                        {
                            "source": "screen",
                            "target": "service",
                            "type": "sync",
                            "arguments": [{"parameter": "customerId"}],
                            "call_id": "create-order::call:1",
                            "fragments": [{"type": "alt", "condition": "valid"}],
                        },
                        {
                            "source": "service",
                            "target": "screen",
                            "type": "return",
                            "reply_to": "create-order::call:1",
                            "fragments": [],
                        },
                    ],
                }]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "erd.puml").write_text(
        'entity "Order" as Order { * order_id : VARCHAR }',
        encoding="utf-8",
    )
    (tmp_path / "openapi.yaml").write_text(
        """openapi: 3.0.3
paths:
  /orders:
    post:
      operationId: createOrder
      responses:
        '201':
          description: Order created
        '422':
          description: Invalid order
""",
        encoding="utf-8",
    )
    (tmp_path / "deployment.json").write_text(
        json.dumps(
            {
                "workloadGraph": {
                    "workloads": [{
                        "id": "orders",
                        "artifact": {"kind": "generatedApplication"},
                        "interfaces": [{"id": "http", "port": 8080}],
                        "configuration": [{
                            "id": "payments-url",
                            "name": "PAYMENTS_URL",
                            "kind": "endpointBinding",
                        }],
                        "storage": [{"id": "orders-data", "mountPath": "/data"}],
                    }],
                    "connections": [],
                }
            }
        ),
        encoding="utf-8",
    )
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "name": "order-management",
                "workspaceRoot": ".",
                "inputs": {
                    "bceClass": "bce.puml",
                    "sequence": "sequence.puml",
                    "sequenceModel": "sequence-model.json",
                    "erd": "erd.puml",
                    "openapi": "openapi.yaml",
                    "deploymentBundle": "deployment.json",
                },
                "generation": {"basePackage": "com.example.orders"},
            }
        ),
        encoding="utf-8",
    )
    run = tmp_path / "run_order"
    java = run / "application/src/main/java/com/example/orders"
    (java / "api").mkdir(parents=True)
    (java / "api/OrdersApi.java").write_text(
        "package com.example.orders.api; "
        'interface OrdersApi { String PATH = "/orders"; void createOrder(); }',
        encoding="utf-8",
    )
    (java / "bce").mkdir(parents=True)
    for name in (
        "OrderService",
        "CheckoutScreen",
        "Order",
        "OrderStoreGateway",
        "PaymentGateway",
    ):
        (java / f"bce/{name}.java").write_text(
            f"package com.example.orders.bce; public interface {name} {{}}",
            encoding="utf-8",
        )
    (java / "bce/CheckoutScreen.java").write_text(
        "package com.example.orders.bce; "
        "public interface CheckoutScreen { CloseResult submit(String customerId); }",
        encoding="utf-8",
    )
    (java / "bce/CloseResult.java").write_text(
        "package com.example.orders.bce; public record CloseResult(String message) {}",
        encoding="utf-8",
    )
    (java / "bce/OrderService.java").write_text(
        "package com.example.orders.bce; "
        "public interface OrderService { OrderReceipt createOrder(String customerId); }",
        encoding="utf-8",
    )
    (java / "bce/OrderReceipt.java").write_text(
        "package com.example.orders.bce; public record OrderReceipt(String orderId) {}",
        encoding="utf-8",
    )

    spec = load_job(job)
    ir = build_implementation_ir(spec, run)

    assert ir.application_class == "OrderManagementApplication"
    assert [port.name for port in ir.api_ports] == ["Orders"]
    assert {gateway.name: gateway.kind for gateway in ir.gateways} == {
        "OrderStoreGateway": "persistence",
        "PaymentGateway": "external",
    }
    assert {scenario.status for scenario in ir.e2e_scenarios} == {201, 422}
    assert [task.task_id for task in generate_api_adapter_tasks(spec, run)] == [
        "implement-orders-api-adapter"
    ]
    assert {task.task_id for task in generate_gateway_adapter_tasks(spec, run)} == {
        "implement-order-store-gateway-adapter",
        "implement-payment-gateway-adapter",
    }
    control = next(
        task for task in generate_implementation_tasks(spec, run)
        if task.task_type == "control"
    )
    tasks = [control, *generate_boundary_adapter_tasks(spec, run), *generate_api_adapter_tasks(spec, run)]
    for task in tasks:
        context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
        diagram = next(item for item in context["sequence"] if item["use_case_id"] == "UC_ORDER")
        messages = diagram["Messages"]
        assert any(message.get("arguments") for message in messages)
        assert any(message.get("reply_to") for message in messages)
        assert any(message.get("fragments") for message in messages)
        projection = context["deployment"]
        assert {"workloads", "connections"} <= set(projection)
        assert {"interfaces", "configuration", "storage"} <= set(projection["workloads"][0])
        if task.task_type == "boundary-adapter":
            assert "record CloseResult(String message)" in context["generatedJavaContracts"]
        if task.task_type == "api-adapter":
            assert "record OrderReceipt(String orderId)" in context["generatedJavaContracts"]
    gateway = generate_gateway_adapter_tasks(spec, run)[0]
    wiring = generate_wiring_tasks(spec, run)[0]
    for task in (gateway, wiring):
        projection = json.loads((run / task.context_file).read_text(encoding="utf-8"))["deployment"]
        assert {"workloads", "connections"} <= set(projection)
        assert {"interfaces", "configuration", "storage"} <= set(projection["workloads"][0])
    assert (java / "OrderManagementApplication.java").is_file()


def test_final_workspace_verification_publishes_success_report(
    tmp_path: Path,
) -> None:
    """작업자가 생성한 소스를 최종 검증하고 공개 보고서를 남기는 흐름을 확인한다."""
    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")
    verification = {"exitCode": 0, "testResults": ""}
    short_workspace_root = tmp_path / "ascii-temp"

    with (
        patch(
            "app.implementation.agents.workspace.tempfile.gettempdir",
            return_value=str(short_workspace_root),
        ),
        patch(
            "app.implementation.agents.verification.build.verify_agent_workspace",
            return_value=verification,
        ),
    ):
        result = verify_run_workspace(run)

    report = json.loads(
        (run / "reports/final-verification.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "SUCCEEDED"
    assert report["verification"] == verification
    assert (
        short_workspace_root
        / "easydep-agent-workspaces"
        / "abcdef123456"
        / "final-verification"
        / "application/src/Main.java"
    ).is_file()


def test_adapter_gates_reject_missing_contract_behavior(
    tmp_path: Path,
) -> None:
    """API interface 누락과 Boundary의 빈 반환을 같은 adapter 검사에서 막는다."""
    relative = "application/src/main/java/com/example/StudentsApiController.java"
    controller = tmp_path / relative
    controller.parent.mkdir(parents=True)
    controller.write_text(
        "public class StudentsApiController {}",
        encoding="utf-8",
    )

    violations = api_adapter_contract_violations(tmp_path, [relative])
    assert violations == [
        f"{relative}: controller must implement generated StudentsApi"
    ]

    controller.write_text(
        "public class StudentsApiController implements StudentsApi {}",
        encoding="utf-8",
    )
    assert api_adapter_contract_violations(tmp_path, [relative]) == []

    boundary_relative = "application/src/main/java/com/example/OrderAdapter.java"
    boundary = tmp_path / boundary_relative
    boundary.write_text(
        "class OrderAdapter { String submit() { return null; } }",
        encoding="utf-8",
    )
    typed_sequence = [{
        "Participants": [
            {"alias": "boundary", "kind": "boundary"},
            {"alias": "control", "kind": "control"},
        ],
        "Messages": [{
            "source": "boundary", "target": "control", "type": "sync",
        }],
    }]
    violations = boundary_adapter_contract_violations(
        tmp_path, [boundary_relative], typed_sequence
    )
    assert any("return null" in violation for violation in violations)


def test_e2e_gate_accepts_complete_scenario_and_rejects_wrong_status(
    tmp_path: Path,
) -> None:
    """실제 HTTP 경로와 상태 코드를 확인하는 테스트만 E2E 계약으로 인정한다."""
    source = tmp_path / "CourseFlowTest.java"
    contract = {
        "paths": ["/courses"],
        "statuses": [201],
        "repositories": ["CourseRepository"],
        "minimumTests": 1,
    }
    source.write_text(
        """class CourseFlowTest {
TestRestTemplate http; CourseRepository courseRepository;
@Test void create() {
  use("/courses");
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
}
void use(String value) {}
}
""",
        encoding="utf-8",
    )
    assert e2e_contract_violations(source, contract) == []

    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "HttpStatus.CREATED", "HttpStatus.BAD_REQUEST"
        ),
        encoding="utf-8",
    )
    violations = e2e_contract_violations(source, contract)
    assert any("201" in violation for violation in violations)


def test_persistence_gate_accepts_all_columns_and_reports_missing_column(
    tmp_path: Path,
) -> None:
    """ERD에서 만든 DB 열이 JPA 엔티티에서 빠지는 오류를 빌드 전에 잡는다."""
    relative = (
        "application/src/main/java/com/example/persistence/entity/"
        "EnrollmentEntity.java"
    )
    migration = (
        tmp_path
        / "application/src/main/resources/db/migration/V1__initial_schema.sql"
    )
    entity = tmp_path / relative
    migration.parent.mkdir(parents=True)
    entity.parent.mkdir(parents=True)
    migration.write_text(
        """CREATE TABLE enrollment (
  enrollment_id VARCHAR(255) NOT NULL,
  student_id VARCHAR(255) NOT NULL,
  course_id VARCHAR(255) NOT NULL
);
""",
        encoding="utf-8",
    )
    entity.write_text(
        """@Entity
@Table(name = "enrollment")
class EnrollmentEntity {
  @Id @Column(name = "enrollment_id") private String enrollmentId;
  @Column(name = "student_id") private String studentId;
  @Column(name = "course_id") private String courseId;
}
""",
        encoding="utf-8",
    )
    assert persistence_entity_schema_violations(tmp_path, [relative]) == []

    entity.write_text(
        entity.read_text(encoding="utf-8").replace(
            '  @Column(name = "course_id") private String courseId;\n', ""
        ),
        encoding="utf-8",
    )
    violations = persistence_entity_schema_violations(tmp_path, [relative])
    assert len(violations) == 1
    assert "course_id" in violations[0]


def test_source_conformance_rejects_agent_changes_to_generated_contract(
    tmp_path: Path,
) -> None:
    """에이전트가 생성된 BCE 계약을 바꾸면 최종 검증에서 실패시킨다."""
    java = tmp_path / "application/src/main/java/com/example/demo"
    (java / "bce").mkdir(parents=True)
    (java / "application").mkdir()
    contract = java / "bce/CheckoutGateway.java"
    contract.write_text(
        "package com.example.demo.bce;\n"
        "public interface CheckoutGateway {\n"
        "    String charge(String purchaseId);\n"
        "}\n",
        encoding="utf-8",
    )
    (java / "application/CheckoutService.java").write_text(
        "package com.example.demo.application; "
        "class CheckoutServiceImpl implements CheckoutService { "
        "CheckoutGateway gateway; void run() { gateway.charge(\"order-1\"); } }",
        encoding="utf-8",
    )
    bce = tmp_path / "class.puml"
    bce.write_text(
        "class CheckoutService <<Control>> { + run() }\n"
        "class CheckoutGateway <<Gateway>> { + charge() }\n",
        encoding="utf-8",
    )
    sequence = tmp_path / "sequence.puml"
    sequence.write_text(
        "CheckoutService -> CheckoutGateway: charge()\n",
        encoding="utf-8",
    )
    spec = SimpleNamespace(
        base_package="com.example.demo",
        inputs={"bceClass": bce, "sequence": sequence},
    )
    capture_generated_contracts(tmp_path, "com.example.demo")

    assert verify_source_design_conformance(tmp_path, spec)["status"] == "PASSED"

    contract.write_text(
        contract.read_text(encoding="utf-8").replace(
            "String charge(String purchaseId)",
            "Integer charge(String purchaseId)",
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceDesignConformanceError):
        verify_source_design_conformance(tmp_path, spec)

    report = json.loads(
        (tmp_path / "reports/source-design-conformance.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "FAILED"
    assert "GENERATED_CONTRACT_STRUCTURE_CHANGED" in {
        item["code"] for item in report["violations"]
    }


def test_entity_body_can_change_without_changing_its_public_signature(
    tmp_path: Path,
) -> None:
    """Entity의 동작은 작성할 수 있지만 설계가 정한 호출 계약은 유지한다."""
    bce = tmp_path / "application/src/main/java/com/example/demo/bce"
    bce.mkdir(parents=True)
    entity = bce / "Order.java"
    entity.write_text(
        "public class Order { public String rename(String value) { return value; } }",
        encoding="utf-8",
    )
    class_model = tmp_path / "class.puml"
    class_model.write_text("class Order <<Entity>> { + rename(value: string): string }", encoding="utf-8")
    sequence = tmp_path / "sequence.puml"
    sequence.write_text("", encoding="utf-8")
    spec = SimpleNamespace(
        base_package="com.example.demo",
        inputs={"bceClass": class_model, "sequence": sequence},
    )
    capture_generated_contracts(tmp_path, spec.base_package)

    entity.write_text(
        entity.read_text(encoding="utf-8").replace("return value", "return value.trim()"),
        encoding="utf-8",
    )
    assert verify_source_design_conformance(tmp_path, spec)["status"] == "PASSED"

    entity.write_text(
        entity.read_text(encoding="utf-8").replace("String value", "Integer value"),
        encoding="utf-8",
    )
    with pytest.raises(SourceDesignConformanceError):
        verify_source_design_conformance(tmp_path, spec)


def test_entity_tasks_own_one_file_and_keep_typed_sequence_details(tmp_path: Path) -> None:
    """Entity 작업은 파일별로 나뉘고 구조화된 호출·반환 정보를 잃지 않는다."""
    inputs = {
        "bceClass": tmp_path / "bce.puml",
        "sequence": tmp_path / "sequence.puml",
        "erd": tmp_path / "erd.puml",
        "bceModel": tmp_path / "bce-model.json",
        "sequenceModel": tmp_path / "sequence-model.json",
        "erdBceModel": tmp_path / "erd-model.json",
    }
    inputs["bceClass"].write_text(
        "class Order <<Entity>> { + rename(title: string): void }\n"
        "class Customer <<Entity>> {}\n"
        "class OrderControl <<Control>> { + place(order: Order): Order }\n",
        encoding="utf-8",
    )
    inputs["sequence"].write_text("OrderControl -> Order : rename(title)\n", encoding="utf-8")
    inputs["erd"].write_text('entity "Order" as Order { * id : VARCHAR }', encoding="utf-8")
    inputs["bceModel"].write_text(json.dumps({"Classes": [
        {"className": "Order", "stereotype": "Entity",
         "fields": ["operation : OperationType"], "operations": []},
        {"className": "Customer", "stereotype": "Entity", "fields": [], "operations": []},
        {"className": "OrderControl", "stereotype": "Control", "fields": [], "operations": []},
    ], "DataTypes": [
        {"name": "OperationType", "kind": "enumeration", "values": ["CREATE", "UPDATE"]}
    ]}), encoding="utf-8")
    inputs["erdBceModel"].write_text(json.dumps({"Classes": [], "Relationships": []}), encoding="utf-8")
    inputs["sequenceModel"].write_text(json.dumps({"Diagrams": [{
        "use_case_id": "UC_ORDER",
        "Participants": [
            {"alias": "control", "source_class": "OrderControl"},
            {"alias": "order", "source_class": "Order"},
        ],
        "Messages": [
            {"source": "control", "target": "order", "type": "sync",
             "call_id": "call-1", "step_ids": ["UC_ORDER:main:1"],
             "arguments": [{"source_kind": "input", "source_ref": "#title"}]},
            {"source": "order", "target": "control", "type": "return",
             "reply_to": "call-1", "step_ids": ["UC_ORDER:main:1"]},
        ],
    }]}), encoding="utf-8")
    spec = SimpleNamespace(
        name="orders", base_package="com.example.orders", inputs=inputs,
        agent_model="test", agent_base_url="", agent_temperature=0.2,
        agent_top_p=1.0, agent_max_output_tokens=1, agent_reasoning_budget=1,
    )
    run = tmp_path / "run"
    bce = run / "application/src/main/java/com/example/orders/bce"
    bce.mkdir(parents=True)
    for name in ("Order", "Customer", "OrderControl"):
        (bce / f"{name}.java").write_text(f"public class {name} {{}}", encoding="utf-8")
    (bce / "OperationType.java").write_text(
        "public enum OperationType { CREATE, UPDATE }", encoding="utf-8"
    )

    tasks = generate_implementation_tasks(spec, run)
    entities = [task for task in tasks if task.task_type == "entity"]
    assert {task.control for task in entities} == {"Order", "Customer"}
    assert all(len(task.allowed_write_paths) == 1 for task in entities)
    assert not any(task.task_type == "scaffold-completion" for task in tasks)
    order = next(task for task in entities if task.control == "Order")
    context = json.loads((run / order.context_file).read_text(encoding="utf-8"))
    call, reply = context["sequence"][0]["messages"]
    assert call["arguments"][0]["source_kind"] == "input"
    assert reply["reply_to"] == call["call_id"]
    assert "public enum OperationType" in context["relatedJavaContracts"]


def test_placeholder_gate_checks_production_sources_only(tmp_path: Path) -> None:
    """실행 코드의 미구현 메서드는 막되 테스트용 예시는 배포 판단에서 제외한다."""
    main = "application/src/main/java/com/example/CoursesApiController.java"
    test = "application/src/test/java/com/example/CoursesApiControllerTest.java"
    source = (
        "class CoursesApiController { Object search() { "
        'throw new UnsupportedOperationException("not implemented"); } }'
    )
    for relative in (main, test):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    evidence = production_placeholder_markers(tmp_path, [main, test])

    assert len(evidence) == 1
    assert main in evidence[0]


def test_cloud_spec_renders_deployment_and_matching_iac(tmp_path: Path) -> None:
    """확정된 클라우드 명세가 배포 파일과 같은 공급자의 IaC로 이어지는지 확인한다."""
    cloud = tmp_path / "cloud.json"
    cloud.write_text(
        json.dumps(
            {
                "provider": "aws",
                "resources": [
                    {"type": "AWS::EC2::VPC", "name": "platform"},
                    {
                        "type": "AWS::EC2::Subnet",
                        "name": "private-a",
                        "availabilityZone": "ap-northeast-2a",
                        "dependsOn": ["platform"],
                    },
                    {
                        "type": "AWS::EC2::Subnet",
                        "name": "private-c",
                        "availabilityZone": "ap-northeast-2c",
                        "dependsOn": ["platform"],
                    },
                    {"type": "AWS::ECR::Repository", "name": "orders"},
                    {
                        "type": "AWS::EKS::Cluster",
                        "name": "orders-cluster",
                        "dependsOn": ["platform"],
                        "workloads": [{"name": "orders-api"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = SimpleNamespace(name="orders", inputs={"cloud": cloud})
    run = tmp_path / "run"

    with patch(
        "app.implementation.delivery.terraform.validate_terraform",
        return_value={"status": "SUCCEEDED"},
    ):
        deployment = render_deployment(run, spec)
        iac = render_iac(run, spec)

    assert deployment["intentSource"] == "implementation-agent-inference"
    assert deployment["kubernetesManifests"] is False
    assert iac["provider"] == "aws"
    assert iac["kubernetesManifests"] is False
    assert iac["sourceConformance"]["status"] == "SUCCEEDED"
    assert (run / "application/Dockerfile").is_file()
    assert (run / "application/terraform/main.tf").is_file()
    assert (run / "application/deployment-bundle/README.md").is_file()
    assert not (run / "application/k8s").exists()
    assert not (run / "application/deployment-bundle/application/k8s").exists()
