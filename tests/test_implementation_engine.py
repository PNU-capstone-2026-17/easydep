from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.implementation.agents.verification.build import (
    WorkspaceVerificationError,
    task_verification_command,
    verify_run_workspace,
)
from app.implementation.delivery.container import render_deployment
from app.implementation.delivery.terraform import render_iac
from app.implementation.domain.models import JobSpec
from app.implementation.workflows.conformance import (
    SourceDesignConformanceError,
    capture_generated_contracts,
    verify_source_design_conformance,
)
from app.implementation.workflows.coordinator import (
    plan_workflow,
    reconcile_workflow_state,
    run_workflow,
)
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


def test_work_unit_verification_runs_related_tests_directly_with_cache() -> None:
    """작업 검증은 관련 test 하나를 직접 실행해 중복 compile 단계를 줄인다."""
    assert task_verification_command(
        ["gradlew"],
        "use-case",
        ["application/src/test/java/com/example/OrderScenarioTest.java"],
    ) == ["gradlew", "test", "--tests", "*OrderScenarioTest", "--build-cache"]


@pytest.mark.parametrize(
    ("old_status", "result_prompt"),
    [("SUCCEEDED", "prompt-v1"), ("RUNNING", "prompt-before-replay")],
)
def test_resume_keeps_previous_success_after_shared_file_changes(
    tmp_path: Path, old_status: str, result_prompt: str
) -> None:
    """공유 파일 변경이나 중단된 재실행이 있어도 이전 성공 결과를 재사용한다."""
    reports = tmp_path / "reports"
    executions = reports / "agent-executions"
    executions.mkdir(parents=True)
    shared = tmp_path / "application/src/main/java/com/example/SharedAdapter.java"
    shared.parent.mkdir(parents=True)
    shared.write_text("class SharedAdapter { void laterChange() {} }", encoding="utf-8")
    task_id = "implement-first-use-case"
    relative = shared.relative_to(tmp_path).as_posix()
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": task_id,
                        "task_type": "use-case",
                        "prompt_sha256": "prompt-v1",
                        "required_output_paths": [relative],
                        "allowed_write_paths": [relative],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "taskId": task_id,
                        "status": old_status,
                        "attempts": 1,
                        "outputHashes": {relative: "hash-before-later-task"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (executions / f"{task_id}.result.json").write_text(
        json.dumps({"status": "SUCCEEDED", "promptSha256": result_prompt}),
        encoding="utf-8",
    )

    state = reconcile_workflow_state(tmp_path)

    assert state["tasks"][0]["status"] == "SUCCEEDED"
    assert state["tasks"][0]["attempts"] == 1


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
class CancelControl <<Control>> {}
class Order <<Entity>> { - id: UUID }
""",
        encoding="utf-8",
    )
    class_model_payload = typed_class_model_payload()
    class_model_payload["Classes"].extend([
        {"className": "Order", "stereotype": "Entity", "use_case_ids": ["UC1"],
         "identifier": ["id"], "fields": ["id : UUID"], "operations": []},
        {"className": "CancelControl", "stereotype": "Control", "use_case_ids": ["UC2"],
         "operations": []},
    ])
    class_model = design / "class-model.json"
    class_model.write_text(json.dumps(class_model_payload), encoding="utf-8")
    sequence_model = design / "sequence-model.json"
    sequence_model.write_text(json.dumps(typed_sequence_model_payload()), encoding="utf-8")
    sequence = design / "sequence.puml"
    sequence.write_text("OrderBoundary -> OrderControl : place(request)\n", encoding="utf-8")
    requirements = design / "requirements.json"
    requirements.write_text(json.dumps([
        {"id": "FR-ORDER", "use_case_ids": ["UC1"]},
        {"id": "FR-CANCEL", "use_case_ids": ["UC2"]},
    ]), encoding="utf-8")
    use_case_specs = design / "use-case-specs.json"
    use_case_specs.write_text(json.dumps([
        {"id": "UC1", "use_case_id": "UC1", "name": "Place order"},
        {"id": "UC2", "use_case_id": "UC2", "name": "Cancel order"},
    ]), encoding="utf-8")
    erd = design / "erd.puml"
    erd.write_text('entity "Order" as Order {\n  * id : UUID\n}\n', encoding="utf-8")
    openapi = design / "openapi.json"
    openapi.write_text(json.dumps({"openapi": "3.0.3", "paths": {
        "/orders": {"post": {"operationId": "placeOrder",
                                "responses": {"201": {"description": "Created"}}}},
        "/orders/{id}": {"delete": {"operationId": "cancelOrder",
                                       "responses": {"204": {"description": "Cancelled"}}}},
    }}), encoding="utf-8")
    api_model = design / "api-model.json"
    api_model.write_text(json.dumps({"Endpoints": [
        {"method": "POST", "path": "/orders", "operation_id": "placeOrder",
         "use_case_ids": ["UC1"], "control_binding": {"control": "OrderControl"}},
        {"method": "DELETE", "path": "/orders/{id}", "operation_id": "cancelOrder",
         "use_case_ids": ["UC2"], "control_binding": {"control": "CancelControl"}},
    ]}), encoding="utf-8")

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
    (package_root / "api/CancelApi.java").write_text(
        "package com.example.orders.api;\n"
        "public interface CancelApi { String PATH = \"/orders/{id}\"; "
        "void cancelOrder(); }\n",
        encoding="utf-8",
    )
    for name in ("OrderBoundary", "OrderControl", "CancelControl", "Order"):
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
    for name in ("OrdersApi", "CancelApi"):
        (generated / f"{name}.ts").write_text(
            f"export class {name} {{}}\n", encoding="utf-8"
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
            "apiModel": api_model,
            "refinedRequirements": requirements,
            "useCaseSpec": use_case_specs,
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

    use_cases = [task for task in tasks if task["task_type"] == "use-case"]
    wiring = next(task for task in tasks if task["task_type"] == "wiring")
    assert len(use_cases) >= 2
    assert set(wiring["use_case_ids"]) == {"UC1", "UC2"}
    contexts = [
        json.loads((run / task["context_file"]).read_text(encoding="utf-8"))
        for task in use_cases]
    expected_requirements = {"UC1": {"FR-ORDER"}, "UC2": {"FR-CANCEL"}}
    partitions = [set(context["useCaseIds"]) for context in contexts]
    assert set().union(*partitions) == set(expected_requirements)
    assert all(len(group) <= 3 for group in partitions)
    assert all(left.isdisjoint(right) for index, left in enumerate(partitions) for right in partitions[index + 1 :])
    for context in contexts:
        assert set(context["requirementIds"]) == set().union(
            *(expected_requirements[use_case_id] for use_case_id in context["useCaseIds"])
        )
    assert any(
        "application/src/main/java/com/example/orders/bce/Order.java"
        in set(task["allowed_write_paths"])
        for task in use_cases
    )
    generated_api = {
        "application/src/main/java/com/example/orders/api/OrdersApi.java",
        "application/src/main/java/com/example/orders/api/CancelApi.java",
    }
    assert all(
        not set(task["allowed_write_paths"]).intersection(generated_api)
        for task in use_cases
    )
    immutable_bce = {
        "application/src/main/java/com/example/orders/bce/OrderBoundary.java",
        "application/src/main/java/com/example/orders/bce/OrderControl.java",
        "application/src/main/java/com/example/orders/bce/CancelControl.java",
    }
    assert all(
        not set(task["allowed_write_paths"]).intersection(immutable_bce)
        for task in use_cases
    )


def test_scenario_failure_returns_to_automatic_repair_without_user_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패한 사용자 흐름은 입력 대기 대신 같은 작업의 repair checkpoint가 된다."""
    run = tmp_path / "run"
    flow_path = "application/src/test/java/com/example/OrderScenarioTest.java"
    flow = run / flow_path
    flow.parent.mkdir(parents=True)
    flow.write_text("class OrderScenarioTest {}", encoding="utf-8")
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [{
            "task_id": "implement-order-use-cases",
            "task_type": "use-case",
            "allowed_write_paths": [flow_path],
        }]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: {
            "status": "COMPLETE",
            "tasks": [{"taskId": "implement-order-use-cases", "status": "SUCCEEDED", "phase": "use-cases"}],
            "phases": [{"phaseId": "use-cases", "status": "SUCCEEDED"}],
            "nextRunnableTasks": [],
        },
    )

    def failed_scenario(_run_root: Path) -> dict[str, object]:
        raise WorkspaceVerificationError({
            "command": ["gradlew", "test", "--tests", "*OrderScenarioTest", "--build-cache"],
            "exitCode": 1,
            "stderr": f"{flow_path}: scenario assertion failed",
        })

    result = run_workflow(run, SimpleNamespace(app_id="app-1"), None, verifier=failed_scenario)

    assert result["status"] == "COMPLETE"
    assert result.get("blockingReason") is None
    assert result["repairPlan"] == "reports/repair-plan.json"
    repair = json.loads((reports / "repair-plan.json").read_text(encoding="utf-8"))
    assert repair["status"] == "ACTIVE"
    assert repair["entries"][-1]["ownerTaskIds"] == ["implement-order-use-cases"]


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


def test_entity_can_add_helpers_while_preserving_generated_public_signatures(
    tmp_path: Path,
) -> None:
    """Entity 구현용 메서드는 추가해도 설계가 정한 기존 호출 계약은 유지한다."""
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
        entity.read_text(encoding="utf-8").replace(
            "return value",
            'return value.trim(); } public String normalized() { return "ok"',
        ),
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
