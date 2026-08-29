from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.implementation.agents.verification.build import (
    api_adapter_contract_violations,
    persistence_entity_schema_violations,
    production_placeholder_markers,
    verify_run_workspace,
)
from app.implementation.agents.verification.e2e import e2e_contract_violations
from app.implementation.delivery.kubernetes import render_deployment
from app.implementation.delivery.terraform import render_iac
from app.implementation.domain.implementation_ir import build_implementation_ir
from app.implementation.generation.orchestrator import load_job
from app.implementation.planning.design_context import (
    generate_api_adapter_tasks,
    generate_gateway_adapter_tasks,
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
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "name": "order-management",
                "workspaceRoot": ".",
                "inputs": {
                    "bceClass": "bce.puml",
                    "sequence": "sequence.puml",
                    "erd": "erd.puml",
                    "openapi": "openapi.yaml",
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
    generate_wiring_tasks(spec, run)
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


def test_api_adapter_gate_accepts_contract_and_rejects_missing_interface(
    tmp_path: Path,
) -> None:
    """컴파일 가능한 빈 컨트롤러가 API 구현으로 통과하지 못하게 한다."""
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
    assert iac["provider"] == "aws"
    assert iac["sourceConformance"]["status"] == "SUCCEEDED"
    assert (run / "application/k8s/orders-api/deployment.yaml").is_file()
    assert (run / "application/terraform/main.tf").is_file()
    assert (run / "application/deployment-bundle/README.md").is_file()
