from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.implementation.engine.orchestrator import (
    find_undefined_bce_types,
    load_job,
    plan_e2e_tasks,
)
from app.implementation.engine.agent_runtime import (
    EventJournal,
    changed_files,
    missing_required_outputs,
    openhands_compatibility,
    production_placeholder_markers,
    prepare_agent_workspace,
    read_allowed_sources,
    read_persistence_entity_contracts,
    read_gradle_test_failures,
    render_verification_feedback,
    select_repair_paths,
    snapshot_files,
    task_base_package,
    transient_provider_error,
    provider_retry_delay,
    verification_failure_hints,
    verify_run_workspace,
)
from app.implementation.engine.repair_planner import (
    apply_repair_directives,
    referenced_source_paths,
    schedule_cross_phase_repair,
)
from app.implementation.engine.design_context import (
    detect_e2e_design_gaps,
    generate_api_adapter_tasks,
    generate_boundary_adapter_tasks,
    generate_e2e_tasks,
    generate_gateway_adapter_tasks,
    generate_wiring_tasks,
    parse_design_classes,
    parse_openapi_operations,
    read_generated_java_contracts,
    referenced_openapi_model_names,
    render_api_adapter_prompt,
    slice_sequence,
)
from app.implementation.engine.completion_audit import audit_run_completion
from app.implementation.engine.quality_gates import e2e_contract_violations
from app.implementation.engine.deployment_renderer import (
    infer_intent,
    render_deployment,
    validate_intent,
)
from app.implementation.engine.iac_renderer import render_iac, validate_terraform
from app.implementation.engine.source_conformance import (
    SourceDesignConformanceError,
    capture_generated_contracts,
    restore_generated_contracts,
    verify_source_design_conformance,
)
from app.implementation.engine.implementation_ir import (
    ApiOperationIR,
    ApiPortIR,
    ApiResponseIR,
    build_implementation_ir,
    parse_openapi_operations as parse_ir_openapi_operations,
)
from app.implementation.engine.workflow import (
    reconcile_workflow_state,
    validate_approval,
    validate_workflow_approval,
    write_transmission_request,
)


class SourceDesignConformanceTest(unittest.TestCase):
    def _spec(self, root: Path, bce: Path, sequence: Path) -> SimpleNamespace:
        return SimpleNamespace(
            base_package="com.example.demo",
            inputs={"bceClass": bce, "sequence": sequence},
        )

    def test_preserves_generated_contracts_and_observable_sequence_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            java = run / "application/src/main/java/com/example/demo"
            (java / "bce").mkdir(parents=True)
            (java / "application").mkdir()
            contract = java / "bce/CheckoutGateway.java"
            contract.write_text(
                "package com.example.demo.bce;\n\n"
                "public interface CheckoutGateway {\n"
                "    String PAYMENT_KIND = \"card\";\n"
                "    String charge(String purchaseId);\n"
                "}\n",
                encoding="utf-8",
            )
            (java / "application/CheckoutService.java").write_text(
                "package com.example.demo.application; class CheckoutService implements CheckoutService { "
                "CheckoutGateway gateway; void run() { gateway.charge(); } }\n",
                encoding="utf-8",
            )
            bce = run / "class.puml"
            bce.write_text(
                "class CheckoutService <<Control>> {\n+  + run()\n}\n"
                "class CheckoutGateway <<Gateway>> {\n+  + charge()\n}\n",
                encoding="utf-8",
            )
            sequence = run / "sequence.puml"
            sequence.write_text("CheckoutService -> CheckoutGateway: charge()\n", encoding="utf-8")
            capture_generated_contracts(run, "com.example.demo")

            report = verify_source_design_conformance(run, self._spec(run, bce, sequence))

            self.assertEqual("PASSED", report["status"])
            self.assertTrue((run / "reports/source-design-conformance.json").is_file())

    def test_rejects_modified_contract_and_missing_sequence_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            java = run / "application/src/main/java/com/example/demo"
            (java / "bce").mkdir(parents=True)
            (java / "application").mkdir()
            contract = java / "bce/CheckoutGateway.java"
            contract.write_text(
                "package com.example.demo.bce;\n\n"
                "public interface CheckoutGateway {\n"
                "    String PAYMENT_KIND = \"card\";\n"
                "    String charge(String purchaseId);\n"
                "}\n",
                encoding="utf-8",
            )
            bce = run / "class.puml"
            bce.write_text(
                "class CheckoutService <<Control>> {}\nclass CheckoutGateway <<Gateway>> {}\n",
                encoding="utf-8",
            )
            sequence = run / "sequence.puml"
            sequence.write_text("CheckoutService -> CheckoutGateway: charge()\n", encoding="utf-8")
            capture_generated_contracts(run, "com.example.demo")
            contract.write_text(
                "package com.example.demo.bce;\n\n"
                "public interface CheckoutGateway {\n"
                "    Integer PAYMENT_KIND = 1;\n"
                "    Integer charge(String purchaseId);\n"
                "}\n",
                encoding="utf-8",
            )

            with self.assertRaises(SourceDesignConformanceError):
                verify_source_design_conformance(run, self._spec(run, bce, sequence))

            report = json.loads((run / "reports/source-design-conformance.json").read_text(encoding="utf-8"))
            self.assertEqual("FAILED", report["status"])
            self.assertEqual(
                {
                    "GENERATED_CONTRACT_MODIFIED",
                    "GENERATED_CONTRACT_STRUCTURE_CHANGED",
                    "SEQUENCE_CALL_NOT_IMPLEMENTED",
                },
                {item["code"] for item in report["violations"]},
            )
            changes = report["checks"]["generatedContracts"][0]["changes"]
            self.assertIn("PAYMENT_KIND: String -> Integer", changes["fields"]["modified"])
            self.assertIn("charge(String purchaseId): String -> Integer", changes["methods"]["modified"])
            restored = restore_generated_contracts(run)
            self.assertEqual(["application/src/main/java/com/example/demo/bce/CheckoutGateway.java"], restored)
            self.assertIn("String charge(String purchaseId);", contract.read_text(encoding="utf-8"))


class LoadJobTest(unittest.TestCase):
    def test_cross_phase_repair_replans_owner_and_downstream_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_repair"
            task_dir = run / "reports" / "implementation-tasks"
            execution_dir = run / "reports" / "agent-executions"
            task_dir.mkdir(parents=True)
            execution_dir.mkdir(parents=True)
            definitions = []
            for task_id, task_type, output in (
                (
                    "implement-repositories",
                    "persistence-repositories",
                    "application/src/main/java/example/OrderRepository.java",
                ),
                (
                    "implement-application-wiring",
                    "configuration",
                    "application/src/main/java/example/Application.java",
                ),
                (
                    "implement-end-to-end-flow",
                    "integration-test",
                    "application/src/test/java/example/ApplicationFlowTest.java",
                ),
            ):
                prompt = task_dir / f"{task_id}.prompt.md"
                prompt.write_text(f"base prompt for {task_id}", encoding="utf-8")
                task = {
                    "task_id": task_id,
                    "task_type": task_type,
                    "control": task_id,
                    "prompt_file": prompt.relative_to(run).as_posix(),
                    "context_file": "reports/context.json",
                    "prompt_sha256": task_id,
                    "source_artifacts": {},
                    "allowed_write_paths": [output],
                }
                (task_dir / f"{task_id}.task.json").write_text(
                    json.dumps(task), encoding="utf-8"
                )
                definitions.append(task)
            (run / "reports" / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": definitions}), encoding="utf-8"
            )

            repair = schedule_cross_phase_repair(
                run,
                "implement-application-wiring",
                {
                    "stderr": (
                        "C:\\work\\application\\src\\main\\java\\example\\"
                        "OrderRepository.java:12: error: cannot find symbol"
                    )
                },
            )
            self.assertEqual(["implement-repositories"], repair["ownerTaskIds"])
            self.assertEqual(
                ["implement-application-wiring", "implement-end-to-end-flow"],
                repair["revalidationTaskIds"],
            )

            apply_repair_directives(run)
            manifest = json.loads(
                (run / "reports" / "run-manifest.json").read_text(encoding="utf-8")
            )
            repository = manifest["implementation_tasks"][0]
            self.assertNotEqual("implement-repositories", repository["prompt_sha256"])
            self.assertIn("repairEvidence", repository["source_artifacts"])
            self.assertIn(
                "repair the failure in your owned files",
                (task_dir / "implement-repositories.prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "regenerate and revalidate after an upstream repair",
                (task_dir / "implement-end-to-end-flow.prompt.md").read_text(encoding="utf-8"),
            )

    def test_e2e_failure_without_source_path_selects_api_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports"
            reports.mkdir()
            tasks = [
                {
                    "task_id": "implement-orders-api-adapter",
                    "task_type": "api-adapter",
                    "control": "OrdersApi",
                    "allowed_write_paths": ["application/OrdersApiController.java"],
                },
                {
                    "task_id": "implement-end-to-end-flow",
                    "task_type": "integration-test",
                    "control": "flow",
                    "allowed_write_paths": ["application/ApplicationFlowTest.java"],
                },
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
            )
            repair = schedule_cross_phase_repair(
                run,
                "implement-end-to-end-flow",
                {"testResults": "expected HTTP 201 but was 500"},
            )
            self.assertEqual(
                ["implement-orders-api-adapter"], repair["ownerTaskIds"]
            )

    def test_provider_retry_helpers_are_bounded_and_classify_nim_errors(self) -> None:
        self.assertTrue(transient_provider_error(RuntimeError("429 rate limit")))
        self.assertTrue(transient_provider_error(TimeoutError("timed out")))
        self.assertFalse(transient_provider_error(ValueError("invalid model name")))
        with patch.dict(
            os.environ,
            {
                "OPENHANDS_PROVIDER_RETRY_BASE_SECONDS": "2",
                "OPENHANDS_PROVIDER_RETRY_MAX_SECONDS": "5",
            },
        ):
            self.assertEqual(2, provider_retry_delay(1))
            self.assertEqual(5, provider_retry_delay(4))

    def test_verification_source_paths_normalize_windows_paths(self) -> None:
        self.assertEqual(
            ["application/src/main/java/example/OrderRepository.java"],
            referenced_source_paths(
                {
                    "stderr": (
                        "C:\\work\\application\\src\\main\\java\\example\\"
                        "OrderRepository.java:42: error"
                    )
                }
            ),
        )

    def test_implementation_ir_parses_json_openapi(self) -> None:
        operations = parse_ir_openapi_operations(json.dumps({
            "openapi": "3.0.3",
            "paths": {
                "/sessions": {
                    "post": {
                        "operationId": "createSession",
                        "responses": {
                            "201": {"description": "Created"},
                            "401": {"description": "Unauthorized"},
                        },
                    }
                }
            },
        }))

        self.assertEqual(1, len(operations))
        self.assertEqual(("POST", "/sessions", "createSession"), (
            operations[0].method, operations[0].path, operations[0].operation_id
        ))
        self.assertEqual([201, 401], [item.status for item in operations[0].responses])

    def test_implementation_ir_and_planners_support_order_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bce.puml").write_text(
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
            (root / "sequence.puml").write_text(
                """CheckoutScreen -> OrderService : createOrder(customerId)
OrderService -> PaymentGateway : charge(orderId)
alt invalid order
OrderService --> CheckoutScreen : validation error
end
""",
                encoding="utf-8",
            )
            (root / "erd.puml").write_text(
                'entity "Order" as Order { * order_id : VARCHAR }', encoding="utf-8"
            )
            (root / "openapi.yaml").write_text(
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
            job = root / "job.json"
            job.write_text(
                json.dumps({
                    "name": "order-management",
                    "workspaceRoot": ".",
                    "inputs": {
                        "bceClass": "bce.puml",
                        "sequence": "sequence.puml",
                        "erd": "erd.puml",
                        "openapi": "openapi.yaml",
                    },
                    "generation": {"basePackage": "com.example.orders"},
                    "tools": {"puml2codeRoot": ".", "openapiGeneratorJar": "bce.puml"},
                }),
                encoding="utf-8",
            )
            package = run = root / "run_order"
            java = run / "application/src/main/java/com/example/orders"
            (java / "api").mkdir(parents=True)
            (java / "api/OrdersApi.java").write_text(
                'package com.example.orders.api; interface OrdersApi { String PATH = "/orders"; createOrder(); }',
                encoding="utf-8",
            )
            (java / "bce").mkdir(parents=True)
            for name in ("OrderService", "CheckoutScreen", "Order", "OrderStoreGateway", "PaymentGateway"):
                (java / f"bce/{name}.java").write_text(
                    f"package com.example.orders.bce; public interface {name} {{}}",
                    encoding="utf-8",
                )

            spec = load_job(job)
            ir = build_implementation_ir(spec, run)

            self.assertEqual("OrderManagementApplication", ir.application_class)
            self.assertEqual(["Orders"], [port.name for port in ir.api_ports])
            self.assertEqual(
                {"OrderStoreGateway": "persistence", "PaymentGateway": "external"},
                {gateway.name: gateway.kind for gateway in ir.gateways},
            )
            self.assertEqual({201, 422}, {scenario.status for scenario in ir.e2e_scenarios})
            self.assertEqual(
                ["implement-orders-api-adapter"],
                [task.task_id for task in generate_api_adapter_tasks(spec, run)],
            )
            gateway_tasks = generate_gateway_adapter_tasks(spec, run)
            self.assertEqual(
                {"implement-order-store-gateway-adapter", "implement-payment-gateway-adapter"},
                {task.task_id for task in gateway_tasks},
            )
            wiring = generate_wiring_tasks(spec, run)[0]
            self.assertIn("OrderManagementApplication.java", wiring.allowed_write_paths[0])

    def test_semantic_gate_requires_ir_status_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "OrderManagementFlowTest.java"
            path.write_text(
                """class OrderManagementFlowTest {
TestRestTemplate http; OrderRepository repository; InMemoryPaymentGatewayAdapter gateway;
@Test void created() { assertThat(response.status()).isEqualTo(201); use("/orders"); }
@Test void rejected() { assertThat(response.status()).isEqualTo(400); use("/orders/123"); }
void use(String value) {}
}""",
                encoding="utf-8",
            )
            contract = {
                "paths": ["/orders", "/orders/{orderId}"],
                "statuses": [201, 422],
                "repositories": ["OrderRepository"],
                "gatewayAdapters": ["InMemoryPaymentGatewayAdapter"],
                "minimumTests": 2,
            }

            violations = e2e_contract_violations(path, contract)

            self.assertTrue(any("422" in item for item in violations))
            self.assertFalse(any("OrderRepository" in item for item in violations))

    def test_e2e_semantic_gate_rejects_simplified_weak_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "StockPurchaseFlowTest.java"
            path.write_text(
                """class StockPurchaseFlowTest {
@Test void uiInteractionFlow() {
  // Simplified integration test: portfolio may be null; no strict assertion.
}
}""",
                encoding="utf-8",
            )

            violations = e2e_contract_violations(path)

            self.assertTrue(any("at least 4" in item for item in violations))
            self.assertTrue(any("weak dual-outcome" in item for item in violations))
            self.assertTrue(any("purchase persistence" in item for item in violations))

    def test_e2e_semantic_gate_accepts_real_http_and_persistence_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "StockPurchaseFlowTest.java"
            path.write_text(
                """class StockPurchaseFlowTest {
TestRestTemplate http; InMemoryTradingSiteGatewayAdapter gateway;
PurchaseRecordRepository purchases; HoldingRepository holdings;
@Test void success() { gateway.enqueueOutcome(null); use("completed", "/portfolio"); }
@Test void rejection() { gateway.rejectSite("bad"); }
@Test void delay() { use("delayed"); }
@Test void clarification() { use("missing_information", "clarification"); }
void use(String... value) {}
}""",
                encoding="utf-8",
            )

            self.assertEqual([], e2e_contract_violations(path))

    def test_purchases_adapter_prompt_requires_clarification_status_mapping(self) -> None:
        prompt = render_api_adapter_prompt(
            SimpleNamespace(base_package="com.example.demo"),
            ApiPortIR(
                "Orders",
                "application/src/main/java/com/example/demo/api/OrdersApi.java",
                (
                    ApiOperationIR(
                        "POST",
                        "/orders",
                        "createOrder",
                        (
                            ApiResponseIR(201, "Created"),
                            ApiResponseIR(422, "Invalid order"),
                        ),
                    ),
                ),
            ),
            "contracts",
            "sequence",
        )

        self.assertIn("POST /orders", prompt)
        self.assertIn("201 Created", prompt)
        self.assertIn("422 Invalid order", prompt)
        self.assertIn("every documented status", prompt)

    def test_production_placeholder_gate_ignores_tests_and_rejects_main_java(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = "application/src/main/java/com/example/Service.java"
            test = "application/src/test/java/com/example/ServiceTest.java"
            for relative in (main, test):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("class Example { // TODO finish\n}", encoding="utf-8")

            evidence = production_placeholder_markers(root, [main, test])

            self.assertEqual(1, len(evidence))
            self.assertIn(main, evidence[0])

    def test_workflow_checkpoint_recovers_results_and_interrupted_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_workflow"
            reports = run / "reports/agent-executions"
            reports.mkdir(parents=True)
            first_output = "application/src/First.java"
            second_output = "application/src/Second.java"
            first = run / first_output
            first.parent.mkdir(parents=True)
            first.write_text("class First {}", encoding="utf-8")
            tasks = [
                {
                    "task_id": "implement-first",
                    "task_type": "control",
                    "prompt_sha256": "prompt-first",
                    "allowed_write_paths": [first_output],
                    "source_artifacts": {},
                },
                {
                    "task_id": "implement-second",
                    "task_type": "api-adapter",
                    "prompt_sha256": "prompt-second",
                    "allowed_write_paths": [second_output],
                    "source_artifacts": {},
                },
            ]
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
            )
            (reports / "implement-first.result.json").write_text(
                json.dumps(
                    {
                        "taskId": "implement-first",
                        "status": "SUCCEEDED",
                        "promptSha256": "prompt-first",
                    }
                ),
                encoding="utf-8",
            )

            state = reconcile_workflow_state(run)
            self.assertEqual("SUCCEEDED", state["tasks"][0]["status"])
            self.assertEqual("PENDING", state["tasks"][1]["status"])
            self.assertEqual(["implement-second"], state["nextRunnableTasks"])

            state["tasks"][1]["status"] = "RUNNING"
            (run / "reports/workflow-state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            recovered = reconcile_workflow_state(run)
            self.assertEqual("INTERRUPTED", recovered["tasks"][1]["status"])

    def test_workflow_invalidates_succeeded_task_when_output_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_changed"
            reports = run / "reports/agent-executions"
            reports.mkdir(parents=True)
            relative = "application/src/Changed.java"
            output = run / relative
            output.parent.mkdir(parents=True)
            output.write_text("class Changed {}", encoding="utf-8")
            task = {
                "task_id": "implement-changed",
                "task_type": "control",
                "prompt_sha256": "same-prompt",
                "allowed_write_paths": [relative],
                "source_artifacts": {},
            }
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
            )
            (reports / "implement-changed.result.json").write_text(
                json.dumps(
                    {
                        "taskId": "implement-changed",
                        "status": "SUCCEEDED",
                        "promptSha256": "same-prompt",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                "SUCCEEDED", reconcile_workflow_state(run)["tasks"][0]["status"]
            )

            output.write_text("class Changed { int value; }", encoding="utf-8")
            changed = reconcile_workflow_state(run)
            self.assertEqual("PENDING", changed["tasks"][0]["status"])
            still_changed = reconcile_workflow_state(run)
            self.assertEqual("PENDING", still_changed["tasks"][0]["status"])

    def test_workflow_invalidates_failed_result_when_repair_prompt_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            reports = run / "reports" / "agent-executions"
            reports.mkdir(parents=True)
            output = "application/src/Repair.java"
            (run / output).parent.mkdir(parents=True)
            (run / output).write_text("class Repair {}", encoding="utf-8")
            (run / "reports" / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": "repair",
                                "task_type": "control",
                                "prompt_sha256": "new-repair-prompt",
                                "allowed_write_paths": [output],
                                "source_artifacts": {},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "repair.result.json").write_text(
                json.dumps(
                    {
                        "status": "FAILED",
                        "promptSha256": "old-failed-prompt",
                        "error": "compile failed",
                    }
                ),
                encoding="utf-8",
            )

            state = reconcile_workflow_state(run)

            self.assertEqual("PENDING", state["tasks"][0]["status"])
            self.assertEqual(["repair"], state["nextRunnableTasks"])

    def test_transmission_request_excludes_key_and_requires_matching_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_approval"
            reports = run / "reports"
            reports.mkdir(parents=True)
            (reports / "run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {
                                "task_id": "implement-one",
                                "task_type": "control",
                                "prompt_sha256": "abc",
                                "source_artifacts": {"bceClass": "design.puml"},
                                "allowed_write_paths": ["application/src/One.java"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "tasks": [{"taskId": "implement-one", "status": "PENDING"}]
            }
            request = write_transmission_request(run, state)
            self.assertIsNotNone(request)
            self.assertFalse(request["apiKeyIncluded"])
            self.assertNotIn("apiKey", request)

            approval = reports / "approval.json"
            approval.write_text(
                json.dumps(
                    {
                        "requestId": request["requestId"],
                        "approved": True,
                        "approvedAt": "2026-07-22T00:00:00Z",
                        "approvedBy": "test-user",
                    }
                ),
                encoding="utf-8",
            )
            accepted = validate_approval(approval, request["requestId"])
            self.assertTrue(accepted["approved"])
            with self.assertRaises(PermissionError):
                validate_approval(approval, "different-request")

    def test_workflow_approval_allows_remaining_subset_of_approved_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_subset"
            reports = run / "reports/agent-executions"
            reports.mkdir(parents=True)
            definitions = [
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "prompt_sha256": prompt,
                    "source_artifacts": {},
                    "allowed_write_paths": [f"application/{task_id}.java"],
                }
                for task_id, task_type, prompt in (
                    ("earlier-control", "control", "control-prompt"),
                    ("repair-repository", "persistence-repositories", "repo-prompt"),
                    ("repair-wiring", "configuration", "wiring-prompt"),
                )
            ]
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"implementation_tasks": definitions}), encoding="utf-8"
            )
            full_request = write_transmission_request(
                run,
                {
                    "tasks": [
                        {"taskId": "repair-repository", "status": "PENDING"},
                        {"taskId": "repair-wiring", "status": "PENDING"},
                    ]
                },
            )
            approval = run / "reports/approval.json"
            approval.write_text(
                json.dumps(
                    {
                        "requestId": full_request["requestId"],
                        "approved": True,
                        "approvedBy": "tester",
                    }
                ),
                encoding="utf-8",
            )
            (reports / "repair-repository.result.json").write_text(
                json.dumps(
                    {
                        "status": "SUCCEEDED",
                        "promptSha256": "repo-prompt",
                    }
                ),
                encoding="utf-8",
            )
            (reports / "earlier-control.result.json").write_text(
                json.dumps(
                    {
                        "status": "SUCCEEDED",
                        "promptSha256": "control-prompt",
                    }
                ),
                encoding="utf-8",
            )
            subset_state = {
                "tasks": [
                    {
                        "taskId": "earlier-control",
                        "phase": "control",
                        "status": "SUCCEEDED",
                        "attempts": 1,
                        "promptSha256": "control-prompt",
                        "resultFile": "reports/agent-executions/earlier-control.result.json",
                    },
                    {
                        "taskId": "repair-repository",
                        "phase": "repairs",
                        "status": "SUCCEEDED",
                        "attempts": 1,
                        "promptSha256": "repo-prompt",
                        "resultFile": "reports/agent-executions/repair-repository.result.json",
                    },
                    {
                        "taskId": "repair-wiring",
                        "phase": "repairs",
                        "status": "PENDING",
                        "attempts": 0,
                        "promptSha256": "wiring-prompt",
                    },
                ]
            }
            subset_request = write_transmission_request(run, subset_state)

            accepted = validate_workflow_approval(
                approval, subset_request, subset_state, run
            )

            self.assertEqual("APPROVED_SCOPE_SUBSET", accepted["authorization"])
            self.assertEqual(full_request["requestId"], accepted["approvedRequestId"])

    def test_workflow_approval_allows_delegated_repair_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_delegated"
            reports = run / "reports"
            reports.mkdir(parents=True)
            task = {
                "task_id": "repair-control", "task_type": "control", "prompt_sha256": "repair",
                "source_artifacts": {}, "allowed_write_paths": ["application/Repair.java"],
            }
            (reports / "run-manifest.json").write_text(
                json.dumps({"input_hash": "input-hash", "implementation_tasks": [task]}), encoding="utf-8"
            )
            (reports / "repair-plan.json").write_text(
                json.dumps({"entries": [{"revision": 1, "ownerTaskIds": ["repair-control"], "revalidationTaskIds": []}]}),
                encoding="utf-8",
            )
            state = {"tasks": [{"taskId": "repair-control", "status": "PENDING", "attempts": 1}]}
            request = write_transmission_request(run, state)
            approval = reports / "approval.json"
            approval.write_text(json.dumps({
                "requestId": "initial-request", "approved": True, "delegatedRepairApprovals": True,
                "delegationScope": {"runId": run.name, "inputHash": "input-hash", "initialTaskIds": [], "maxRepairRounds": 3, "maxTaskAttempts": 50},
            }), encoding="utf-8")

            accepted = validate_workflow_approval(approval, request, state, run)

            self.assertEqual("DELEGATED_RUN_SCOPE", accepted["authorization"])

    def test_transmission_request_is_limited_to_next_runnable_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_phase_scope"
            reports = run / "reports"
            reports.mkdir(parents=True)
            definitions = [
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "prompt_sha256": task_id,
                    "source_artifacts": {},
                    "allowed_write_paths": [f"application/{task_id}.java"],
                }
                for task_id, task_type in (
                    ("repository", "persistence-repositories"),
                    ("wiring", "configuration"),
                )
            ]
            (reports / "run-manifest.json").write_text(
                json.dumps({"implementation_tasks": definitions}), encoding="utf-8"
            )
            request = write_transmission_request(
                run,
                {
                    "nextRunnableTasks": ["repository"],
                    "tasks": [
                        {"taskId": "repository", "status": "PENDING"},
                        {"taskId": "wiring", "status": "PENDING"},
                    ],
                },
            )
            self.assertEqual(["repository"], [item["taskId"] for item in request["tasks"]])

    def test_completion_audit_builds_backlog_after_workspace_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_sample"
            java = run / "application/src/main/java/com/example/demo"
            test = run / "application/src/test/java/com/example/demo/application/impl"
            reports = run / "reports/implementation-tasks"
            for path in (java / "bce", java / "api/model", java / "application/impl", test, reports):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "bce/PurchaseRecord.java").write_text(
                "package com.example.demo.bce; class PurchaseRecord { void fail() { "
                'throw new UnsupportedOperationException("skeleton"); } }',
                encoding="utf-8",
            )
            (java / "application/impl/PurchaseService.java").write_text(
                "package com.example.demo.application.impl; class PurchaseService { // TODO map\n}",
                encoding="utf-8",
            )
            (test / "PurchaseServiceTest.java").write_text("class PurchaseServiceTest {}", encoding="utf-8")
            (reports / "purchase.context.json").write_text(
                json.dumps(
                    {
                        "bce": (
                            "class PurchaseScreen <<Boundary>> {}\n"
                            "class PurchaseController <<Control>> {}\n"
                            "class PurchaseRecord <<Entity>> {}"
                        )
                    }
                ),
                encoding="utf-8",
            )
            erd = run / "erd.puml"
            erd.write_text(
                'entity "PurchaseRecord" as PurchaseRecord {}', encoding="utf-8"
            )
            manifest = {
                "inputs": {
                    "bceClass": {"path": "C:/moved/missing.puml"},
                    "erd": {"path": str(erd)},
                },
                "diagnostics": [
                    {
                        "code": "MISSING_PROTOTYPE_INPUT",
                        "message": "Prototype continues without optional input: deployment",
                    }
                ],
            }
            (run / "reports/run-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            task_ids = {item["task_id"] for item in audit["backlog"]}
            self.assertEqual("INCOMPLETE", audit["status"])
            self.assertIn("replace-bce-runtime-skeletons", task_ids)
            self.assertIn("implement-erd-persistence", task_ids)
            self.assertIn("implement-purchases-api-adapter", task_ids)
            self.assertIn("implement-boundary-adapters", task_ids)
            self.assertTrue(
                (run / "reports/implementation-completion-audit.json").is_file()
            )

    def test_completion_audit_does_not_block_on_absent_runtime_skeleton_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_executable_contracts"
            java = run / "application/src/main/java/com/example/demo"
            reports = run / "reports/implementation-tasks"
            for path in (java / "bce", java / "api/model", reports):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "bce/PurchaseRecord.java").write_text(
                "package com.example.demo.bce; class PurchaseRecord {}",
                encoding="utf-8",
            )
            (reports / "purchase.context.json").write_text(
                json.dumps(
                    {
                        "bce": (
                            "class PurchaseScreen <<Boundary>> {}\n"
                            "class PurchaseController <<Control>> {}\n"
                            "class PurchaseRecord <<Entity>> {}"
                        )
                    }
                ),
                encoding="utf-8",
            )
            erd = run / "erd.puml"
            erd.write_text(
                'entity "PurchaseRecord" as PurchaseRecord {}', encoding="utf-8"
            )
            (run / "reports/run-manifest.json").write_text(
                json.dumps({
                    "inputs": {
                        "bceClass": {"path": "C:/moved/missing.puml"},
                        "erd": {"path": str(erd)},
                    }
                }),
                encoding="utf-8",
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "replace-bce-runtime-skeletons",
                {item["task_id"] for item in audit["backlog"]},
            )
            direct_tasks = {
                item["task_id"]: item for item in audit["backlog"]
            }
            self.assertEqual([], direct_tasks["implement-erd-persistence"]["blocked_by"])
            self.assertEqual([], direct_tasks["implement-purchases-api-adapter"]["blocked_by"])
            self.assertEqual([], direct_tasks["implement-boundary-adapters"]["blocked_by"])

    def test_completion_audit_accepts_api_adapter_only_with_controller_and_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_api_adapter"
            java = run / "application/src/main/java/com/example/demo"
            tests = run / "application/src/test/java/com/example/demo"
            reports = run / "reports/implementation-tasks"
            for path in (
                java / "api/model",
                java / "bce",
                java / "adapter/in/web",
                tests / "adapter/in/web",
                reports,
            ):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "adapter/in/web/PurchasesApiController.java").write_text(
                "class PurchasesApiController {}", encoding="utf-8"
            )
            (tests / "adapter/in/web/PurchasesApiControllerTest.java").write_text(
                "class PurchasesApiControllerTest {}", encoding="utf-8"
            )
            (reports / "empty.context.json").write_text(
                json.dumps({"bce": ""}), encoding="utf-8"
            )
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"inputs": {}, "diagnostics": []}), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "implement-purchases-api-adapter",
                {item["task_id"] for item in audit["backlog"]},
            )
            self.assertEqual(0, audit["summary"]["missingApiAdapters"])

    def test_completion_audit_accepts_boundary_only_with_adapter_and_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_boundary_adapter"
            java = run / "application/src/main/java/com/example/demo"
            tests = run / "application/src/test/java/com/example/demo"
            reports = run / "reports/implementation-tasks"
            for path in (
                java / "api/model",
                java / "bce",
                java / "adapter/in/boundary",
                tests / "adapter/in/boundary",
                reports,
            ):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "bce/PurchaseScreen.java").write_text(
                "package com.example.demo.bce; public interface PurchaseScreen {}",
                encoding="utf-8",
            )
            (java / "adapter/in/boundary/PurchaseScreenAdapter.java").write_text(
                "class PurchaseScreenAdapter {}", encoding="utf-8"
            )
            (tests / "adapter/in/boundary/PurchaseScreenAdapterTest.java").write_text(
                "class PurchaseScreenAdapterTest {}", encoding="utf-8"
            )
            (reports / "boundary.context.json").write_text(
                json.dumps({"bce": "class PurchaseScreen <<Boundary>> {}"}),
                encoding="utf-8",
            )
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"inputs": {}, "diagnostics": []}), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "implement-boundary-adapters",
                {item["task_id"] for item in audit["backlog"]},
            )
            self.assertEqual(0, audit["summary"]["missingBoundaryAdapters"])

    def test_boundary_planner_discovers_contracts_and_writes_bounded_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bce = root / "bce.puml"
            sequence = root / "sequence.puml"
            bce.write_text(
                """class BuyScreen <<Boundary>> {
  + display()
  + onPurchaseRequested()
}
class ErrorScreen <<Boundary>> {
  + showError(message: string)
}
class PurchaseController <<Control>> {
  + startPurchase()
}
""",
                encoding="utf-8",
            )
            sequence.write_text(
                """BuyScreen -> PurchaseController : startPurchase()
PurchaseController -> ErrorScreen : showError(\"failed\")
""",
                encoding="utf-8",
            )
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "bce.puml", "sequence": "sequence.puml"},
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            java = run / "application/src/main/java/com/example/demo/bce"
            java.mkdir(parents=True)
            (java / "BuyScreen.java").write_text(
                "package com.example.demo.bce; public interface BuyScreen { void display(); void onPurchaseRequested(); }",
                encoding="utf-8",
            )
            (java / "ErrorScreen.java").write_text(
                "package com.example.demo.bce; public interface ErrorScreen { void showError(String message); }",
                encoding="utf-8",
            )
            (java / "PurchaseController.java").write_text(
                "package com.example.demo.bce; public interface PurchaseController { void startPurchase(); }",
                encoding="utf-8",
            )

            tasks = generate_boundary_adapter_tasks(load_job(job), run)

            self.assertEqual(
                [
                    "implement-buy-screen-boundary-adapter",
                    "implement-error-screen-boundary-adapter",
                ],
                [task.task_id for task in tasks],
            )
            self.assertEqual("boundary-adapter", tasks[0].task_type)
            self.assertEqual(2, len(tasks[0].allowed_write_paths))
            prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            self.assertIn("public interface BuyScreen", prompt)
            self.assertIn("PurchaseController", prompt)
            self.assertIn("Do not annotate the adapter as a Spring bean", prompt)
            self.assertIn("Do not leave TODO", prompt)

    def test_wiring_planner_contracts_bootstrap_configuration_and_context_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bce = root / "bce.puml"
            sequence = root / "sequence.puml"
            bce.write_text(
                "class PurchaseController <<Control>> { + startPurchase() }",
                encoding="utf-8",
            )
            sequence.write_text(
                "BuyScreen -> PurchaseController : startPurchase()", encoding="utf-8"
            )
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "bce.puml", "sequence": "sequence.puml"},
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            service = run / "application/src/main/java/com/example/demo/application/impl/PurchaseControllerService.java"
            service.parent.mkdir(parents=True)
            service.write_text(
                "package com.example.demo.application.impl; public class PurchaseControllerService { public PurchaseControllerService() {} }",
                encoding="utf-8",
            )

            tasks = generate_wiring_tasks(load_job(job), run)

            self.assertEqual(["implement-application-wiring"], [task.task_id for task in tasks])
            self.assertEqual("configuration", tasks[0].task_type)
            self.assertEqual(4, len(tasks[0].allowed_write_paths))
            prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            self.assertIn("@SpringBootApplication", prompt)
            self.assertIn("Spring `@Lazy`", prompt)
            self.assertIn("Do not add `@EnableJpaRepositories` exclusions", prompt)
            self.assertIn("every generated Spring Data repository bean", prompt)
            self.assertIn("ApplicationContextTest.java", prompt)
            self.assertIn("PurchaseControllerService", prompt)

    def test_gateway_planner_creates_persistence_and_trading_adapter_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bce = root / "bce.puml"
            sequence = root / "sequence.puml"
            erd = root / "erd.puml"
            bce.write_text(
                """class StockPurchasePersistenceGateway <<Gateway>> {
  + savePurchase(record: PurchaseRecord): PurchaseRecord
}
class TradingSiteGateway <<Gateway>> {
  + connect(siteName: string): boolean
  + executePurchase(): PurchaseRecord
}
class PurchaseRecord <<Entity>> { - purchaseId: string }
""",
                encoding="utf-8",
            )
            sequence.write_text("TradingSiteGateway --> PurchaseController : record", encoding="utf-8")
            erd.write_text('entity "PurchaseRecord" as PurchaseRecord {}', encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "bce.puml", "sequence": "sequence.puml", "erd": "erd.puml"},
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {"puml2codeRoot": ".", "openapiGeneratorJar": "bce.puml"},
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            package = run / "application/src/main/java/com/example/demo"
            (package / "bce").mkdir(parents=True)
            (package / "persistence/repository").mkdir(parents=True)
            (package / "persistence/mapper").mkdir(parents=True)
            (package / "bce/StockPurchasePersistenceGateway.java").write_text(
                "public interface StockPurchasePersistenceGateway {}", encoding="utf-8"
            )
            (package / "bce/TradingSiteGateway.java").write_text(
                "public interface TradingSiteGateway {}", encoding="utf-8"
            )
            (package / "bce/PurchaseRecord.java").write_text(
                "public class PurchaseRecord {}", encoding="utf-8"
            )
            (package / "persistence/repository/PurchaseRecordRepository.java").write_text(
                "public interface PurchaseRecordRepository {}", encoding="utf-8"
            )
            (package / "persistence/mapper/BcePersistenceMapper.java").write_text(
                "public class BcePersistenceMapper {}", encoding="utf-8"
            )

            tasks = generate_gateway_adapter_tasks(load_job(job), run)

            self.assertEqual(
                [
                    "implement-stock-purchase-persistence-gateway-adapter",
                    "implement-trading-site-gateway-adapter",
                ],
                [task.task_id for task in tasks],
            )
            self.assertTrue(all(task.task_type == "gateway-adapter" for task in tasks))
            persistence_prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            trading_prompt = (run / tasks[1].prompt_file).read_text(encoding="utf-8")
            self.assertIn("corresponding repository operation exactly", persistence_prompt)
            self.assertIn("deterministic local adapter", trading_prompt)

    def test_e2e_planner_stops_when_bce_ports_cannot_carry_api_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bce.puml", "sequence.puml", "erd.puml", "openapi.yaml"):
                (root / name).write_text(name, encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {
                            "bceClass": "bce.puml",
                            "sequence": "sequence.puml",
                            "erd": "erd.puml",
                            "openapi": "openapi.yaml",
                        },
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            package = run / "application/src/main/java/com/example/demo"
            (package / "bce").mkdir(parents=True)
            (package / "api/model").mkdir(parents=True)
            (package / "application/impl").mkdir(parents=True)
            (package / "bce/StockPurchaseController.java").write_text(
                "public interface StockPurchaseController { void startPurchase(); void updatePortfolio(); void getSuggestion(); }",
                encoding="utf-8",
            )
            (package / "api/model/PurchaseRequest.java").write_text(
                "public class PurchaseRequest {}", encoding="utf-8"
            )
            (package / "api/model/SuggestionRequest.java").write_text(
                "public class SuggestionRequest {}", encoding="utf-8"
            )
            (package / "application/impl/StockPurchaseControllerService.java").write_text(
                "public class StockPurchaseControllerService { // TODO define real command mapping\n"
                " public PurchaseRecord startPurchase(String siteName) { return interceptResponse(); }\n}",
                encoding="utf-8",
            )
            (package / "application/impl/WebConnectionManagerService.java").write_text(
                'public class WebConnectionManagerService { String response = "ACK"; }',
                encoding="utf-8",
            )
            (package / "persistence/repository").mkdir(parents=True)
            (package / "persistence/repository/PurchaseRecordRepository.java").write_text(
                "public interface PurchaseRecordRepository {}", encoding="utf-8"
            )

            spec = load_job(job)
            self.assertEqual([], generate_e2e_tasks(spec, run))
            codes = {gap["code"] for gap in detect_e2e_design_gaps(spec, run)}
            self.assertEqual(
                {"UNRESOLVED_PRODUCTION_PATH"},
                codes,
            )
            report = json.loads(
                (run / "reports/design-gaps/end-to-end-flow.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("NEEDS_INPUT", report["status"])

            (run / "reports/run-manifest.json").write_text(
                json.dumps(
                    {
                        "implementation_tasks": [
                            {"task_id": "implement-existing", "task_type": "control"},
                            {
                                "task_id": "implement-end-to-end-flow",
                                "task_type": "integration-test",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual([], plan_e2e_tasks(spec, run))
            manifest = json.loads(
                (run / "reports/run-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                ["implement-existing"],
                [task["task_id"] for task in manifest["implementation_tasks"]],
            )

    def test_e2e_planner_emits_bounded_real_integration_test_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bce.puml", "sequence.puml", "erd.puml", "openapi.yaml"):
                (root / name).write_text(name, encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "workspaceRoot": ".",
                        "inputs": {
                            "bceClass": "bce.puml",
                            "sequence": "sequence.puml",
                            "erd": "erd.puml",
                            "openapi": "openapi.yaml",
                        },
                        "generation": {"basePackage": "com.example.demo"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "bce.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run = root / "run_sample"
            control = run / "application/src/main/java/com/example/demo/bce/StockPurchaseController.java"
            control.parent.mkdir(parents=True)
            control.write_text(
                "public interface StockPurchaseController { PurchaseRecord startPurchase(String siteName); Portfolio updatePortfolio(PurchaseRecord record); boolean submitSuggestion(String siteName); }",
                encoding="utf-8",
            )
            repository = (
                run
                / "application/src/main/java/com/example/demo/persistence/repository/PurchaseRecordRepository.java"
            )
            repository.parent.mkdir(parents=True)
            repository.write_text(
                "package com.example.demo.persistence.repository; public interface PurchaseRecordRepository {}",
                encoding="utf-8",
            )
            service = (
                run
                / "application/src/main/java/com/example/demo/application/impl/PersistenceConsumer.java"
            )
            service.parent.mkdir(parents=True)
            service.write_text(
                "package com.example.demo.application.impl; import com.example.demo.persistence.repository.PurchaseRecordRepository; class PersistenceConsumer { PurchaseRecordRepository repository; }",
                encoding="utf-8",
            )
            gateway = (
                run
                / "application/src/main/java/com/example/demo/adapter/out/trading/InMemoryTradingSiteGatewayAdapter.java"
            )
            gateway.parent.mkdir(parents=True)
            gateway.write_text(
                "package com.example.demo.adapter.out.trading; public class InMemoryTradingSiteGatewayAdapter { public void rejectSite(String siteName) {} }",
                encoding="utf-8",
            )

            tasks = generate_e2e_tasks(load_job(job), run)

            self.assertEqual(["implement-end-to-end-flow"], [task.task_id for task in tasks])
            self.assertEqual("integration-test", tasks[0].task_type)
            self.assertEqual(1, len(tasks[0].allowed_write_paths))
            prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
            self.assertIn("Do not mock application Controls", prompt)
            self.assertIn("Never declare `@TestConfiguration`", prompt)
            self.assertIn("InMemoryTradingSiteGatewayAdapter", prompt)
            self.assertIn("@DirtiesContext(classMode = BEFORE_EACH_TEST_METHOD)", prompt)
            self.assertIn("Never use reflection", prompt)
            self.assertIn("Machine-derived semantic contract", prompt)
            self.assertIn("package com.example.demo.persistence.repository", prompt)
            self.assertIn("package com.example.demo.adapter.out.trading", prompt)

    def test_deterministic_deployment_renderer_supports_multiple_workloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(
                json.dumps(
                    {
                        "resources": [
                            {"type": "Microsoft.ContainerRegistry/registries", "name": "demoacr"},
                            {"type": "Microsoft.KeyVault/vaults", "name": "demo-vault"},
                            {
                                "type": "Microsoft.ContainerService/managedClusters",
                                "networking": {
                                    "containerPort": 8000,
                                    "serviceExposure": "ClusterIP",
                                    "ingressProtocol": "HTTPS",
                                },
                                "workloads": [
                                    {
                                        "name": "orders-api",
                                        "replicas": {"min": 2, "max": 5},
                                        "probes": {
                                            "readiness": "/readyz",
                                            "liveness": "/livez",
                                        },
                                        "monitoring": {
                                            "metricsPath": "/actuator/prometheus"
                                        },
                                    },
                                    {
                                        "name": "orders-worker",
                                        "replicas": {"min": 1, "max": 1},
                                    },
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            spec = SimpleNamespace(name="orders", inputs={"cloud": cloud})
            report = render_deployment(root / "run", spec)

            files = set(report["renderedFiles"])
            self.assertIn("application/Dockerfile", files)
            self.assertIn("application/k8s/orders-api/deployment.yaml", files)
            self.assertIn("application/k8s/orders-api/service.yaml", files)
            self.assertIn("application/k8s/orders-api/ingress.yaml", files)
            self.assertIn("application/k8s/orders-api/hpa.yaml", files)
            self.assertIn("application/k8s/orders-api/pdb.yaml", files)
            self.assertIn("application/k8s/orders-api/network-policy.yaml", files)
            self.assertIn("application/k8s/orders-api/service-account.yaml", files)
            self.assertNotIn("application/k8s/orders-api/external-secret.yaml", files)
            self.assertIn("application/k8s/orders-api/service-monitor.yaml", files)
            self.assertIn("application/k8s/orders-worker/deployment.yaml", files)
            self.assertNotIn("application/k8s/orders-worker/service.yaml", files)
            self.assertEqual("deterministic", report["renderer"])
            self.assertEqual(
                "SUCCEEDED_WITH_WARNINGS", report["validation"]["status"]
            )
            self.assertTrue(report["sourceEvidence"]["cloudResourceSpecification"])
            self.assertEqual("implementation-agent-inference", report["intentSource"])
            self.assertEqual("SUCCEEDED", report["sourceConformance"]["status"])
            persisted_intent = json.loads(
                (root / "run/reports/deployment-intent.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(report["intent"], persisted_intent)
            service_source = (
                root / "run/application/k8s/orders-api/service.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("type: ClusterIP", service_source)
            deployment_source = (
                root / "run/application/k8s/orders-api/deployment.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("path: /readyz", deployment_source)
            self.assertIn("path: /livez", deployment_source)

    def test_deterministic_iac_renderer_matches_deployment_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({
                "provider": "azure",
                "resources": [
                    {"type": "Microsoft.ContainerRegistry/registries", "name": "demoacr"},
                    {"type": "Microsoft.ContainerService/managedClusters", "name": "demoaks", "workloads": [{"name": "orders-api"}]},
                    {"type": "Microsoft.KeyVault/vaults", "name": "demokv"},
                ],
            }), encoding="utf-8")
            run = root / "run"
            (run / "application/k8s/orders-api").mkdir(parents=True)
            (run / "reports").mkdir(parents=True)
            (run / "reports/deployment-intent.json").write_text(json.dumps({"workloads": [{"name": "orders-api"}]}), encoding="utf-8")

            report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))

            self.assertEqual("SUCCEEDED", report["sourceConformance"]["status"])
            source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
            self.assertIn('resource "azurerm_kubernetes_cluster"', source)
            self.assertIn('resource "azurerm_container_registry"', source)
            self.assertIn('resource "azurerm_key_vault"', source)

    def test_deterministic_iac_renderer_supports_aws_and_gcp(self) -> None:
        cases = (
            ("aws", [{"type": "AWS::ECR::Repository", "name": "orders"}, {"type": "AWS::EKS::Cluster", "name": "orders"}], ('resource "aws_ecr_repository"', 'resource "aws_eks_cluster"', 'resource "aws_iam_role_policy_attachment" "eks_ecr_pull"')),
            ("gcp", [{"type": "artifactregistry.googleapis.com/Repository", "name": "orders"}, {"type": "container.googleapis.com/Cluster", "name": "orders"}], ('resource "google_artifact_registry_repository"', 'resource "google_container_cluster"', 'resource "google_artifact_registry_repository_iam_member" "gke_artifact_pull"')),
        )
        for provider, resources, expected in cases:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cloud = root / "cloud.json"
                cloud.write_text(json.dumps({"provider": provider, "resources": resources}), encoding="utf-8")
                run = root / "run"
                (run / "application/k8s/orders-api").mkdir(parents=True)
                (run / "reports").mkdir(parents=True, exist_ok=True)
                (run / "reports/deployment-intent.json").write_text(json.dumps({"workloads": [{"name": "orders-api"}]}), encoding="utf-8")
                report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
                source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
                self.assertEqual("SUCCEEDED", report["sourceConformance"]["status"])
                self.assertEqual(provider, report["provider"])
                for marker in expected:
                    self.assertIn(marker, source)

    def test_iac_renderer_connects_networks_and_creates_cluster_nodes(self) -> None:
        cases = (
            (
                "aws",
                [{"type": "AWS::EC2::VPC", "name": "platform", "cidrBlock": "10.0.0.0/16"}, {"type": "AWS::EC2::Subnet", "name": "private-a", "cidrBlock": "10.0.1.0/24"}, {"type": "AWS::EKS::Cluster", "name": "platform"}],
                ("aws_vpc.platform.id", 'resource "aws_eks_node_group"'),
            ),
            (
                "gcp",
                [{"type": "compute.googleapis.com/Network", "name": "platform"}, {"type": "compute.googleapis.com/Subnetwork", "name": "private-a", "ipCidrRange": "10.0.1.0/24"}, {"type": "container.googleapis.com/Cluster", "name": "platform"}],
                ("network = google_compute_network.platform.id", 'resource "google_container_node_pool"'),
            ),
        )
        for provider, resources, expected in cases:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cloud = root / "cloud.json"
                cloud.write_text(json.dumps({"provider": provider, "resources": resources}), encoding="utf-8")
                run = root / "run"
                report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
                source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
                self.assertIn(report["sourceConformance"]["status"], {"SUCCEEDED_WITH_WARNINGS", "SUCCEEDED"})
                for marker in expected:
                    self.assertIn(marker, source)

    def test_iac_renderer_resolves_network_references_independent_of_resource_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "aws", "resources": [
                {"type": "AWS::EKS::Cluster", "name": "platform"},
                {"type": "AWS::EC2::Subnet", "name": "private-a"},
                {"type": "AWS::EC2::VPC", "name": "platform"},
            ]}), encoding="utf-8")
            run = root / "run"
            render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
            source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
            self.assertIn("vpc_id = aws_vpc.platform.id", source)
            self.assertIn("subnet_ids = [aws_subnet.private_a.id]", source)

    def test_iac_renderer_rejects_unknown_provider_resource_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "aws", "resources": [{"type": "AWS::S3::Bucket", "name": "assets"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not supported"):
                render_iac(root / "run", SimpleNamespace(inputs={"cloud": cloud}))

    def test_azure_iac_renderer_preserves_private_cluster_and_mysql_networking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "azure", "resources": [
                {"type": "Microsoft.Network/virtualNetworks", "name": "platform", "subnets": [{"name": "aks", "addressPrefix": "10.0.1.0/24"}, {"name": "mysql", "addressPrefix": "10.0.2.0/24", "delegations": ["Microsoft.DBforMySQL/flexibleServers"]}]},
                {"type": "Microsoft.Network/privateDnsZones", "name": "private.mysql.database.azure.com"},
                {"type": "Microsoft.ContainerService/managedClusters", "name": "platform", "nodePools": [{"name": "system", "vmSize": "Standard_D2s_v5", "count": 2, "enableAutoScaling": True, "minCount": 1, "maxCount": 3}], "networking": {"privateCluster": True, "subnet": "platform/aks"}},
                {"type": "Microsoft.DBforMySQL/flexibleServers", "name": "platform-db", "networking": {"publicNetworkAccess": "Disabled", "delegatedSubnet": "platform/mysql", "privateDnsZone": "private.mysql.database.azure.com"}},
            ]}), encoding="utf-8")
            run = root / "run"
            report = render_iac(run, SimpleNamespace(inputs={"cloud": cloud}))
            source = (run / "application/terraform/main.tf").read_text(encoding="utf-8")
            self.assertIn(report["sourceConformance"]["status"], {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"})
            for marker in ("private_cluster_enabled = true", "vnet_subnet_id = azurerm_subnet.platform_aks.id", "delegated_subnet_id = azurerm_subnet.platform_mysql.id", "private_dns_zone_id = azurerm_private_dns_zone.private_mysql_database_azure_com.id"):
                self.assertIn(marker, source)

    def test_infer_intent_uses_provider_specific_registry_images(self) -> None:
        cases = (
            ("aws", "AWS::EKS::Cluster", "AWS::ECR::Repository", ".dkr.ecr."),
            ("gcp", "container.googleapis.com/Cluster", "artifactregistry.googleapis.com/Repository", "-docker.pkg.dev/"),
        )
        for provider, cluster_type, registry_type, marker in cases:
            with self.subTest(provider=provider):
                intent = infer_intent("orders", {"provider": provider, "resources": [{"type": cluster_type, "workloads": [{"name": "orders-api"}]}, {"type": registry_type, "name": "orders"}]})
                self.assertIn(marker, intent["workloads"][0]["image"])

    def test_aws_cloud_spec_renders_deployment_then_iac(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(json.dumps({"provider": "aws", "resources": [
                {"type": "AWS::EC2::VPC", "name": "platform"},
                {"type": "AWS::EC2::Subnet", "name": "private-a"},
                {"type": "AWS::ECR::Repository", "name": "orders"},
                {"type": "AWS::EKS::Cluster", "name": "platform", "workloads": [{"name": "orders-api"}]},
            ]}), encoding="utf-8")
            spec = SimpleNamespace(name="orders", inputs={"cloud": cloud})
            run = root / "run"
            deployment = render_deployment(run, spec)
            iac = render_iac(run, spec)
            self.assertEqual("implementation-agent-inference", deployment["intentSource"])
            self.assertEqual("aws", iac["provider"])
            self.assertEqual("SUCCEEDED", iac["sourceConformance"]["status"])

    @patch("app.implementation.engine.iac_renderer.shutil.which", return_value=None)
    def test_terraform_validation_reports_when_binary_is_unavailable(self, _which: object) -> None:
        self.assertEqual("SKIPPED", validate_terraform(Path("missing")).get("status"))

    def test_deployment_intent_rejects_incompatible_job_capabilities(self) -> None:
        intent = {
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "demo",
            "workloads": [
                {
                    "name": "cleanup",
                    "kind": "Job",
                    "image": "example/cleanup:1",
                    "capabilities": {"service": True},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "Job/CronJob cannot enable"):
            validate_intent(intent)

    def test_deterministic_renderer_supports_stateful_job_and_cronjob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / "intent.json"
            intent.write_text(
                json.dumps(
                    {
                        "schemaVersion": "easydep-deployment-intent/v1alpha1",
                        "namespace": "platform",
                        "workloads": [
                            {
                                "name": "ledger",
                                "kind": "StatefulSet",
                                "image": "example/ledger:1",
                                "replicas": {"min": 2, "max": 4},
                                "storage": {
                                    "size": "20Gi",
                                    "accessModes": ["ReadWriteMany"],
                                },
                                "capabilities": {
                                    "service": True,
                                    "hpa": True,
                                    "pdb": True,
                                    "pvc": True,
                                    "serviceAccount": True,
                                },
                            },
                            {
                                "name": "migration",
                                "kind": "Job",
                                "image": "example/migration:1",
                                "capabilities": {
                                    "serviceAccount": True,
                                    "configMap": True,
                                    "externalSecret": True,
                                },
                                "externalSecret": {
                                    "storeName": "platform-secrets",
                                    "remoteKey": "migration/runtime",
                                },
                            },
                            {
                                "name": "cleanup",
                                "kind": "CronJob",
                                "image": "example/cleanup:1",
                                "schedule": "0 3 * * *",
                                "capabilities": {},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            spec = SimpleNamespace(
                name="platform",
                inputs={"deploymentIntent": intent},
            )
            report = render_deployment(root / "run", spec)
            files = set(report["renderedFiles"])
            self.assertIn("application/k8s/ledger/statefulset.yaml", files)
            self.assertIn("application/k8s/ledger/pvc.yaml", files)
            self.assertIn("application/k8s/migration/job.yaml", files)
            self.assertIn("application/k8s/cleanup/cronjob.yaml", files)
            job_source = (
                root / "run/application/k8s/migration/job.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("configMapRef", job_source)
            self.assertIn("secretRef", job_source)
            service_source = (
                root / "run/application/k8s/ledger/service.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("clusterIP: None", service_source)

    def test_renderer_removes_files_from_previous_managed_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent_path = root / "intent.json"
            intent = {
                "schemaVersion": "easydep-deployment-intent/v1alpha1",
                "namespace": "demo",
                "workloads": [
                    {
                        "name": "demo-api",
                        "kind": "Deployment",
                        "image": "example/demo:1",
                        "replicas": {"min": 1, "max": 2},
                        "capabilities": {"service": True, "hpa": True},
                    }
                ],
            }
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            spec = SimpleNamespace(
                name="demo", inputs={"deploymentIntent": intent_path}
            )
            render_deployment(root / "run", spec)
            hpa = root / "run/application/k8s/demo-api/hpa.yaml"
            self.assertTrue(hpa.is_file())

            intent["workloads"][0]["replicas"] = {"min": 1, "max": 1}
            intent["workloads"][0]["capabilities"]["hpa"] = False
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            report = render_deployment(root / "run", spec)
            self.assertFalse(hpa.exists())
            self.assertIn(
                "application/k8s/demo-api/hpa.yaml", report["removedFiles"]
            )

    def test_external_secret_requires_explicit_store_and_remote_key(self) -> None:
        intent = {
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "demo",
            "workloads": [
                {
                    "name": "demo-api",
                    "kind": "Deployment",
                    "image": "example/demo:1",
                    "capabilities": {"externalSecret": True},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "externalSecret"):
            validate_intent(intent)

    def test_intent_rejects_invalid_namespace_and_cron(self) -> None:
        intent = {
            "schemaVersion": "easydep-deployment-intent/v1alpha1",
            "namespace": "Invalid Namespace",
            "workloads": [
                {
                    "name": "cleanup",
                    "kind": "CronJob",
                    "image": "example/cleanup:1",
                    "schedule": "nightly",
                    "capabilities": {},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "namespace"):
            validate_intent(intent)

    def test_inference_uses_explicit_diagram_alias_for_exposure(self) -> None:
        cloud = {
            "resources": [
                {
                    "type": "Microsoft.ContainerService/managedClusters",
                    "networking": {"ingressProtocol": "HTTPS"},
                    "workloads": [
                        {
                            "name": "frontend",
                            "diagramAlias": "web",
                            "replicas": {"min": 1, "max": 1},
                        }
                    ],
                }
            ]
        }
        diagram = "@startuml\nactor User\nnode LB as lb\ncomponent Web as web\nlb --> web\n@enduml"
        intent = infer_intent("demo", cloud, diagram)
        capabilities = intent["workloads"][0]["capabilities"]
        self.assertTrue(capabilities["service"])
        self.assertTrue(capabilities["ingress"])

    def test_source_conformance_rejects_intent_that_conflicts_with_cloud_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = root / "cloud.json"
            cloud.write_text(
                json.dumps(
                    {
                        "resources": [
                            {
                                "type": "Microsoft.ContainerService/managedClusters",
                                "networking": {"containerPort": 8000},
                                "workloads": [
                                    {
                                        "name": "orders-api",
                                        "replicas": {"min": 2, "max": 2},
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            intent = root / "intent.json"
            intent.write_text(
                json.dumps(
                    {
                        "schemaVersion": "easydep-deployment-intent/v1alpha1",
                        "namespace": "orders",
                        "workloads": [
                            {
                                "name": "orders-api",
                                "kind": "Deployment",
                                "image": "example/orders-api:1",
                                "replicas": {"min": 1, "max": 1},
                                "capabilities": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            spec = SimpleNamespace(
                name="orders", inputs={"cloud": cloud, "deploymentIntent": intent}
            )
            with self.assertRaisesRegex(ValueError, "replicas.min"):
                render_deployment(root / "run", spec)
            report = json.loads(
                (root / "run/reports/deployment-render.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("FAILED", report["sourceConformance"]["status"])

    def test_completion_audit_accepts_wiring_only_with_all_four_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run_wiring"
            java = run / "application/src/main/java/com/example/demo"
            tests = run / "application/src/test/java/com/example/demo/config"
            resources = run / "application/src/main/resources"
            reports = run / "reports/implementation-tasks"
            for path in (java / "api/model", java / "bce", java / "config", tests, resources, reports):
                path.mkdir(parents=True, exist_ok=True)
            (java / "api/PurchasesApi.java").write_text(
                "package com.example.demo.api; public interface PurchasesApi {}",
                encoding="utf-8",
            )
            (java / "StockPurchaseApplication.java").write_text("class App {}", encoding="utf-8")
            (java / "config/ApplicationConfiguration.java").write_text("class Config {}", encoding="utf-8")
            (resources / "application.yml").write_text("spring: {}", encoding="utf-8")
            (tests / "ApplicationContextTest.java").write_text("class ContextTest {}", encoding="utf-8")
            (reports / "empty.context.json").write_text(json.dumps({"bce": ""}), encoding="utf-8")
            (run / "reports/run-manifest.json").write_text(
                json.dumps({"inputs": {}, "diagnostics": []}), encoding="utf-8"
            )

            audit = audit_run_completion(run)

            self.assertNotIn(
                "implement-application-wiring",
                {item["task_id"] for item in audit["backlog"]},
            )
            self.assertEqual(0, audit["summary"]["missingWiringOutputs"])

    def test_undefined_type_scan_ignores_notes_and_relationship_labels(self) -> None:
        source = """@startuml
class TimerManager <<Control>> {
  - timers: Map<string, TimerInfo>
  + start(record: PurchaseRecord): boolean
}
class PurchaseRecord <<Entity>> {}
note top of TimerManager : Control Manager creates Successful Purchase records
TimerManager --> PurchaseRecord : Manager creates record
@enduml
"""
        self.assertEqual(["TimerInfo"], find_undefined_bce_types(source))

    def test_rejects_input_outside_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "name": "unsafe",
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "../outside.puml"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "escapes workspaceRoot"):
                load_job(job)

    def test_loads_paths_relative_to_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "diagram.puml").write_text("@startuml\n@enduml\n", encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "name": "safe",
                        "workspaceRoot": ".",
                        "inputs": {"bceClass": "diagram.puml"},
                        "tools": {
                            "puml2codeRoot": ".",
                            "openapiGeneratorJar": "diagram.puml",
                        },
                    }
                ),
                encoding="utf-8",
            )
            spec = load_job(job)
            self.assertEqual(root.resolve(), spec.workspace_root)
            self.assertEqual((root / "diagram.puml").resolve(), spec.inputs["bceClass"])

    def test_extracts_control_and_scoped_sequence_messages(self) -> None:
        diagram = """class CheckoutController <<Control>> {
  + checkout()
}
class Cart <<Entity>> {}
"""
        classes = parse_design_classes(diagram)
        self.assertEqual(["CheckoutController", "Cart"], [item.name for item in classes])
        sequence = """alt valid
  UI -> CheckoutController : checkout()
  CheckoutController -> Cart : total()
else invalid
  UI -> ErrorScreen : show()
end
"""
        scoped = slice_sequence(sequence, {"CheckoutController", "Cart"})
        self.assertIn("enclosing branch: alt valid", scoped)
        self.assertNotIn("ErrorScreen", scoped)

    def test_parses_openapi_operations_without_yaml_dependency(self) -> None:
        source = """openapi: 3.0.3
paths:
  /orders:
    post:
      summary: Create order
  /orders/{id}:
    get:
      summary: Read order
components: {}
"""
        operations = parse_openapi_operations(source)
        self.assertEqual(2, len(operations))
        self.assertTrue(operations[0].startswith("# POST /orders"))
        self.assertTrue(operations[1].startswith("# GET /orders/{id}"))

    def test_openhands_absence_is_a_diagnostic_not_an_import_error(self) -> None:
        compatibility = openhands_compatibility()
        self.assertIn("sdkInstalled", compatibility)
        self.assertIsInstance(compatibility["sdkInstalled"], bool)

    def test_changed_files_detects_create_modify_and_delete(self) -> None:
        self.assertEqual(
            {"changed", "created", "deleted"},
            changed_files(
                {"same": "1", "changed": "1", "deleted": "1"},
                {"same": "1", "changed": "2", "created": "1"},
            ),
        )

    def test_missing_required_outputs_requires_every_contracted_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = "application/src/main/java/example/Service.java"
            test = "application/src/test/java/example/ServiceTest.java"
            target = root / implementation
            target.parent.mkdir(parents=True)
            target.write_text("class Service {}", encoding="utf-8")

            self.assertEqual(
                [test], missing_required_outputs(root, [implementation, test])
            )

    def test_snapshot_ignores_gradle_build_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "application/src/Main.java"
            build = root / "application/build/Main.class"
            gradle = root / "application/.gradle/file.lock"
            for path in (source, build, gradle):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")

            snapshot = snapshot_files(root)

            self.assertEqual(["application/src/Main.java"], list(snapshot))

    def test_verification_feedback_contains_compiler_error_and_contract_rule(self) -> None:
        feedback = render_verification_feedback(
            {"stdout": "", "stderr": "void cannot be converted to boolean"}
        )
        self.assertIn("void cannot be converted to boolean", feedback)
        self.assertIn("Generated contracts are authoritative", feedback)
        self.assertIn("use reflection", feedback)
        self.assertIn("Use create to replace", feedback)
        self.assertIn("remove the absent call", feedback)
        self.assertIn("void type not allowed here", feedback)
        self.assertIn("doAnswer", feedback)

    def test_repair_feedback_includes_current_allowlisted_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            first = "application/src/Service.java"
            second = "application/src/ServiceTest.java"
            for relative, content in ((first, "class Service {}"), (second, "class ServiceTest {}")):
                path = sandbox / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            sources = read_allowed_sources(sandbox, [first, second])
            feedback = render_verification_feedback(
                {"stderr": "compile failed"}, current_sources=sources
            )

            self.assertIn("class Service {}", feedback)
            self.assertIn("class ServiceTest {}", feedback)
            self.assertIn("Do not call view or str_replace", feedback)

    def test_reads_failed_gradle_test_xml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            result_dir = sandbox / "application/build/test-results/test"
            result_dir.mkdir(parents=True)
            (result_dir / "TEST-example.xml").write_text(
                '<testsuite><testcase><failure message="expected call"/></testcase></testsuite>',
                encoding="utf-8",
            )
            failures = read_gradle_test_failures(sandbox)
            self.assertIn("expected call", failures)
            self.assertNotIn("<testsuite>", failures)

    def test_compiler_repair_is_limited_to_named_allowed_file(self) -> None:
        main = "application/src/main/java/example/Service.java"
        test = "application/src/test/java/example/ServiceTest.java"
        evidence = {
            "stderr": (
                "C:\\workspace\\application\\src\\test\\java\\example"
                "\\ServiceTest.java:80: error: cannot find symbol"
            )
        }

        self.assertEqual([test], select_repair_paths(evidence, [main, test]))

    def test_runtime_failure_keeps_all_repair_targets(self) -> None:
        allowed = ["application/src/Main.java", "application/src/MainTest.java"]
        self.assertEqual(
            allowed,
            select_repair_paths(
                {
                    "testResults": (
                        "MainTest.failed(MainTest.java:42): expected true but was false"
                    )
                },
                allowed,
            ),
        )

    def test_runtime_failure_hints_explain_common_mockito_causes(self) -> None:
        hints = verification_failure_hints(
            "TooManyActualInvocations\nTooFewActualInvocations\n"
            "Wanted but not invoked\nvoid type not allowed here\n"
            "UnnecessaryStubbingException\nNotAMockException\n"
            "InvalidUseOfMatchersException: 2 matchers expected\n"
            "testStartPurchase_ConnectionFails_HandlesFailure(): Wanted but not invoked"
        )
        self.assertIn("exact argument", hints)
        self.assertIn("exact observed count", hints)
        self.assertIn("conflicting stubs", hints)
        self.assertIn("void mocks need no stub", hints)
        self.assertIn("delete every stubbing", hints)
        self.assertIn("real service", hints)
        self.assertIn("eq(30)", hints)
        self.assertIn("doThrow", hints)

    def test_event_journal_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "events.jsonl"
            journal = EventJournal(target)

            class FakeEvent:
                source = "agent"
                tool_name = "think"

                def model_dump(self, mode: str):
                    return {"mode": mode}

            journal(FakeEvent())
            record = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual("FakeEvent", record["type"])
            self.assertEqual({"think": 1}, journal.tool_counts)

    def test_agent_workspace_uses_short_path_and_creates_allowed_parents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "generated"
            run = generated / "runs" / "run_abcdef1234567890"
            (run / "application").mkdir(parents=True)
            relative = "application/src/main/java/example/impl/Service.java"
            temp_root = Path(directory) / "temp"
            with patch(
                "app.implementation.engine.agent_runtime.tempfile.gettempdir",
                return_value=str(temp_root),
            ):
                sandbox = prepare_agent_workspace(
                    run,
                    {"task_id": "implement-order-controller", "allowed_write_paths": [relative]},
                )
                self.assertEqual(
                    temp_root / "easydep-agent-workspaces" / "abcdef123456" / "order-controller",
                    sandbox,
                )
                self.assertTrue((sandbox / relative).parent.is_dir())
                readonly = sandbox / "application" / "readonly.java"
                readonly.write_text("class Readonly {}", encoding="utf-8")
                os.chmod(readonly, stat.S_IREAD)

                recreated = prepare_agent_workspace(
                    run,
                    {"task_id": "implement-order-controller", "allowed_write_paths": [relative]},
                )
            self.assertEqual(sandbox, recreated)
            self.assertFalse(readonly.exists())

    def test_final_verification_uses_ascii_short_workspace_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "generated" / "runs" / "run_abcdef1234567890"
            source = run / "application" / "src" / "Main.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Main {}", encoding="utf-8")
            temp_root = root / "ascii-temp"
            verification = {"exitCode": 0, "testResults": ""}

            with (
                patch(
                    "app.implementation.engine.agent_runtime.tempfile.gettempdir",
                    return_value=str(temp_root),
                ),
                patch(
                    "app.implementation.engine.agent_runtime.verify_agent_workspace",
                    return_value=verification,
                ),
            ):
                result = verify_run_workspace(run)

            self.assertEqual("SUCCEEDED", result["status"])
            self.assertEqual(verification, result["verification"])
            self.assertTrue(
                (
                    temp_root
                    / "easydep-agent-workspaces"
                    / "abcdef123456"
                    / "final-verification"
                    / "application/src/Main.java"
                ).is_file()
            )
            self.assertTrue((run / "reports/final-verification.json").is_file())

    def test_agent_workspace_uses_sibling_when_previous_workspace_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "generated"
            run = generated / "runs" / "run_abcdef1234567890"
            (run / "application").mkdir(parents=True)
            temp_root = Path(directory) / "temp"
            base = temp_root / "easydep-agent-workspaces" / "abcdef123456" / "order-controller"
            base.mkdir(parents=True)
            relative = "application/src/main/java/example/impl/Service.java"

            with (
                patch(
                    "app.implementation.engine.agent_runtime.tempfile.gettempdir",
                    return_value=str(temp_root),
                ),
                patch(
                    "app.implementation.engine.agent_runtime.shutil.rmtree",
                    side_effect=PermissionError("locked"),
                ),
            ):
                sandbox = prepare_agent_workspace(
                    run,
                    {"task_id": "implement-order-controller", "allowed_write_paths": [relative]},
                )

            self.assertEqual(base.with_name("order-controller-2"), sandbox)
            self.assertTrue((sandbox / relative).parent.is_dir())

    def test_reads_exact_generated_java_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            package = run / "application/src/main/java/com/example/bce"
            package.mkdir(parents=True)
            (package / "OrderController.java").write_text(
                "package com.example.bce; public class OrderController {}",
                encoding="utf-8",
            )
            api_package = run / "application/src/main/java/com/example/api/model"
            api_package.mkdir(parents=True)
            (api_package / "OrderController.java").write_text(
                "package com.example.api.model; public class OrderController {}",
                encoding="utf-8",
            )
            contracts = read_generated_java_contracts(
                run,
                "com.example",
                {"OrderController", "Missing"},
                {"OrderController"},
            )
            self.assertIn("// bce/OrderController.java", contracts)
            self.assertIn("// api/model/OrderController.java", contracts)
            self.assertNotIn("Missing.java", contracts)

    def test_reads_exact_persistence_entity_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            package = run / "application/src/main/java/com/example/demo/persistence/entity"
            package.mkdir(parents=True)
            (package / "HoldingEntity.java").write_text(
                "package com.example.demo.persistence.entity; public class HoldingEntity {}",
                encoding="utf-8",
            )

            contracts = read_persistence_entity_contracts(run, "com.example.demo")

            self.assertIn("HoldingEntity.java", contracts)
            self.assertIn("public class HoldingEntity", contracts)

    def test_extracts_referenced_openapi_models(self) -> None:
        context = """$ref: '#/components/schemas/PurchaseRequest'
$ref: '#/components/schemas/PurchaseRecord'"""
        self.assertEqual(
            {"PurchaseRequest", "PurchaseRecord"},
            referenced_openapi_model_names(context),
        )

    def test_derives_base_package_from_allowed_service_path(self) -> None:
        task = {
            "allowed_write_paths": [
                "application/src/main/java/com/example/demo/application/impl/Service.java"
            ]
        }
        self.assertEqual("com.example.demo", task_base_package(task))

    def test_derives_base_package_from_persistence_mapper_path(self) -> None:
        task = {
            "allowed_write_paths": [
                "application/src/main/java/com/example/demo/persistence/mapper/Mapper.java"
            ]
        }
        self.assertEqual("com.example.demo", task_base_package(task))

    def test_derives_base_package_when_first_output_is_a_resource(self) -> None:
        task = {
            "allowed_write_paths": [
                "application/src/main/resources/db/migration/V1__schema.sql",
                "application/src/test/java/com/example/demo/persistence/SchemaTest.java",
            ]
        }
        self.assertEqual("com.example.demo", task_base_package(task))


if __name__ == "__main__":
    unittest.main()
