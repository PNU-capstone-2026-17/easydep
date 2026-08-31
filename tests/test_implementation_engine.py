from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.implementation.agents.verification.build import (
    verify_run_workspace,
)
from app.implementation.agents.verification.e2e import e2e_contract_violations
from app.implementation.delivery.container import render_deployment
from app.implementation.delivery.terraform import render_iac
from app.implementation.domain.models import JobSpec
from app.implementation.workflows.conformance import (
    SourceDesignConformanceError,
    capture_generated_contracts,
    verify_source_design_conformance,
)
from app.implementation.workflows.coordinator import plan_workflow
from tests.class_design_fixtures import (
    typed_class_model_payload,
    typed_sequence_model_payload,
)


def test_final_workspace_verification_publishes_success_report(
    tmp_path: Path,
) -> None:
    """작업자가 생성한 소스를 최종 검증하고 공개 보고서를 남기는 흐름을 확인한다."""
    run = tmp_path / "generated" / "runs" / "run_abcdef1234567890"
    source = run / "application" / "src" / "Main.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Main {}", encoding="utf-8")
    verification = {"exitCode": 0, "testResults": ""}
    with (
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


def test_e2e_gate_accepts_complete_scenario_and_rejects_wrong_status(
    tmp_path: Path,
) -> None:
    """실제 HTTP 경로와 상태 코드를 확인하는 테스트만 E2E 계약으로 인정한다."""
    source = tmp_path / "CourseFlowTest.java"
    contract = {
        "method": "POST",
        "path": "/courses",
        "status": 201,
    }
    source.write_text(
        """@SpringBootTest class CourseFlowTest {
TestRestTemplate http;
@Test void create() {
  http.postForEntity("/courses", request, Object.class);
  assertThat(response.getStatusCode()).isEqualTo(HttpStatus.CREATED);
}
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

    source.write_text(
        """@SpringBootTest class CourseFlowTest {
MockMvc mvc;
@Test void search() throws Exception {
  mvc.perform(get("/courses")).andExpect(status().isOk());
}
}
""",
        encoding="utf-8",
    )
    assert e2e_contract_violations(
        source, {"method": "GET", "path": "/courses", "status": 200}
    ) == []


def test_planned_manifest_uses_work_units_and_scopes_repairs_to_contracts(
    tmp_path: Path,
) -> None:
    """공개 계획 결과가 작업 종류와 각 작업의 편집 경계를 보존한다."""
    design = tmp_path / "design"
    design.mkdir()
    bce = design / "class.puml"
    bce.write_text(
        """class OrderBoundary <<Boundary>> {
  + submit(request: OrderRequest): Receipt
}
class OrderControl <<Control>> {
  + place(request: OrderRequest): void
}
class Order <<Entity>> {
  - id: UUID
}
""",
        encoding="utf-8",
    )
    # Keep the accepted typed fixtures as the source shape for the planning job;
    # the persisted class model is not reinterpreted by the implementation planner.
    class_model_payload = typed_class_model_payload()
    class_model_payload["Classes"].append({
        "className": "Order",
        "stereotype": "Entity",
        "use_case_ids": ["UC1"],
        "identifier": ["id"],
        "fields": ["id : UUID"],
        "operations": [],
    })
    class_model = design / "class-model.json"
    class_model.write_text(
        json.dumps(class_model_payload), encoding="utf-8"
    )
    sequence_model = design / "sequence-model.json"
    sequence_model.write_text(
        json.dumps(typed_sequence_model_payload()), encoding="utf-8"
    )
    sequence = design / "sequence.puml"
    sequence.write_text(
        "OrderBoundary -> OrderControl : place(request)\n", encoding="utf-8"
    )
    erd = design / "erd.puml"
    erd.write_text(
        'entity "Order" as Order {\n  * id : UUID\n}\n', encoding="utf-8"
    )
    openapi = design / "openapi.json"
    openapi.write_text(
        json.dumps({
            "openapi": "3.0.3",
            "paths": {
                "/orders": {
                    "post": {
                        "operationId": "placeOrder",
                        "responses": {"201": {"description": "Created"}},
                    }
                }
            },
        }),
        encoding="utf-8",
    )

    run = tmp_path / "run"
    package_root = run / "application/src/main/java/com/example/orders"
    (package_root / "api").mkdir(parents=True)
    (package_root / "bce").mkdir(parents=True)
    (package_root / "api/OrdersApi.java").write_text(
        "package com.example.orders.api;\n"
        "public interface OrdersApi { String PATH = \"/orders\"; "
        "void placeOrder(); }\n",
        encoding="utf-8",
    )
    for name in ("OrderBoundary", "OrderControl", "Order"):
        (package_root / f"bce/{name}.java").write_text(
            f"package com.example.orders.bce; public interface {name} {{}}\n",
            encoding="utf-8",
        )
    (run / "application/build.gradle").parent.mkdir(parents=True, exist_ok=True)
    (run / "application/build.gradle").write_text(
        "dependencies {\n"
        "    implementation 'org.springframework.boot:spring-boot-starter-validation'\n"
        "    testImplementation 'org.springframework.boot:spring-boot-starter-test'\n"
        "}\n",
        encoding="utf-8",
    )
    generated = run / "application/frontend/src/generated/apis"
    generated.mkdir(parents=True)
    (generated / "OrdersApi.ts").write_text(
        "export class OrdersApi { placeOrder(): Promise<void> { "
        "return Promise.resolve(); } }\n",
        encoding="utf-8",
    )
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": []}), encoding="utf-8"
    )

    spec = JobSpec(
        job_type="INITIAL_IMPLEMENTATION",
        feedback="",
        name="orders",
        workspace_root=tmp_path,
        inputs={
            "bceClass": bce,
            "bceModel": class_model,
            "sequence": sequence,
            "sequenceModel": sequence_model,
            "erd": erd,
            "openapi": openapi,
        },
        required_inputs=[],
        base_package="com.example.orders",
        allow_assumptions=True,
        verify_compile=False,
        output_root=tmp_path / "generated-runs",
        agent_mode="plan-only",
        agent_model="model",
        agent_base_url="http://localhost",
        agent_temperature=0.0,
        agent_top_p=1.0,
        agent_max_output_tokens=1000,
        agent_reasoning_budget=0,
    )

    plan_workflow(run, spec)
    manifest = json.loads(
        (run / "reports/run-manifest.json").read_text(encoding="utf-8")
    )
    tasks = manifest["implementation_tasks"]
    task_types = {task["task_type"] for task in tasks}
    assert task_types == {
        "persistence", "use-case", "frontend-implementation", "wiring"
    }
    for task in tasks:
        assert set(task["required_output_paths"]) <= set(task["allowed_write_paths"])

    use_case = next(task for task in tasks if task["task_type"] == "use-case")
    editable = set(use_case["allowed_write_paths"])
    assert "application/src/main/java/com/example/orders/bce/Order.java" in editable
    assert not editable.intersection({
        "application/src/main/java/com/example/orders/bce/OrderBoundary.java",
        "application/src/main/java/com/example/orders/bce/OrderControl.java",
        "application/src/main/java/com/example/orders/api/OrdersApi.java",
    })


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
    assert "GENERATED_CONTRACT_CHANGED" in {
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
