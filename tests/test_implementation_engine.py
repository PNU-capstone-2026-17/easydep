from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.implementation.agents import execute_openhands_task
from app.implementation.agents.runtime import create_openhands_conversation
from app.implementation.agents.task_check import (
    TaskCheckSession,
    consume_successful_task_check,
    run_task_check,
)
from app.implementation.agents.verification.build import (
    WorkspaceVerificationError,
    task_verification_command,
    verify_run_workspace,
    verify_use_case_scenarios,
)
from app.implementation.agents.workspace import (
    cleanup_agent_workspace,
    path_is_editable,
    prepare_agent_workspace,
)
from app.implementation.delivery.container import render_deployment
from app.implementation.delivery.terraform import render_iac
from app.implementation.domain.models import JobSpec
from app.implementation.workflows.completion import audit_run_completion
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
from app.implementation.workflows.repair import (
    apply_repair_directives,
    schedule_cross_phase_repair,
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

    report = json.loads((run / "reports/final-verification.json").read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert report["verification"] == verification


def test_one_scenario_method_can_cover_multiple_use_cases(tmp_path: Path) -> None:
    """한 흐름으로 여러 유스케이스를 검사한 테스트를 개수 부족으로 거절하지 않는다."""
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-use-cases-uc1-uc2-uc3",
                        "task_type": "use-case",
                        "use_case_ids": ["UC1", "UC2", "UC3"],
                        "required_test_paths": [
                            "application/src/test/java/com/example/UseCaseBundleTest.java"
                        ],
                    },
                    {
                        "task_id": "implement-application-wiring",
                        "task_type": "wiring",
                        "use_case_ids": ["UC1", "UC2", "UC3"],
                        "required_test_paths": [
                            "application/src/test/java/com/example/ApplicationFlowTest.java"
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    junit = tmp_path / "sandbox/application/build/test-results/test/TEST-flow.xml"
    junit.parent.mkdir(parents=True)
    junit.write_text(
        """<testsuite tests="2" failures="0" errors="0" skipped="0">
<testcase classname="com.example.UseCaseBundleTest" name="fullBundleFlow"/>
<testcase classname="com.example.ApplicationFlowTest" name="fullApplicationFlow"/>
</testsuite>""",
        encoding="utf-8",
    )

    result = verify_use_case_scenarios(tmp_path / "sandbox", run)

    assert result["status"] == "PASSED"
    assert result["coveredUseCaseIds"] == ["UC1", "UC2", "UC3"]
    assert [task["requiredPassedCases"] for task in result["tasks"]] == [1]


def test_work_unit_verification_runs_related_tests_directly_with_cache() -> None:
    """작업 검증은 관련 test 하나를 직접 실행해 중복 compile 단계를 줄인다."""
    assert task_verification_command(
        ["gradlew"],
        "use-case",
        ["application/src/test/java/com/example/OrderScenarioTest.java"],
    ) == ["gradlew", "test", "--tests", "*OrderScenarioTest", "--build-cache"]
    assert task_verification_command(["gradlew"]) == [
        "gradlew",
        "test",
        "--build-cache",
    ]


def test_agent_task_check_returns_real_focused_verification_result(
    tmp_path: Path,
) -> None:
    """코딩 에이전트의 검사 도구가 별도 명령 없이 기존 검증 결과를 돌려준다."""
    evidence = {
        "command": ["gradlew", "test", "--tests", "*OrderScenarioTest"],
        "exitCode": 1,
        "durationMs": 321,
        "stderr": "OrderService.java:42: incompatible types",
        "testResults": "OrderScenarioTest.placesOrder: assertion failed",
    }
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        side_effect=WorkspaceVerificationError(evidence),
    ) as verify:
        passed, output = run_task_check(
            tmp_path,
            "use-case",
            ["application/src/test/java/com/example/OrderScenarioTest.java"],
        )

    assert passed is False
    assert "TASK CHECK FAILED" in output
    assert "OrderService.java:42: incompatible types" in output
    assert "OrderScenarioTest.placesOrder: assertion failed" in output
    verify.assert_called_once_with(
        tmp_path,
        "use-case",
        ["application/src/test/java/com/example/OrderScenarioTest.java"],
    )


def test_agent_task_check_compacts_duplicate_framework_traces(tmp_path: Path) -> None:
    """같은 Spring trace가 여러 출력에 있어도 핵심 원인은 한 번만 전달한다."""
    root_cause = "Caused by: NoSuchBeanDefinitionException: OrderRepository"
    framework_trace = "\n".join(
        [root_cause, *[f"at org.springframework.example.Frame{i}" for i in range(500)]]
    )
    evidence = {
        "command": ["gradlew", "test"],
        "exitCode": 1,
        "testResults": framework_trace,
        "stderr": framework_trace,
    }
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        side_effect=WorkspaceVerificationError(evidence),
    ):
        passed, output = run_task_check(tmp_path, "use-case", [])

    assert passed is False
    assert output.count(root_cause) == 1
    assert len(output) < 8500


def test_agent_task_check_requires_a_source_change_before_retry(
    tmp_path: Path,
) -> None:
    """같은 실패 상태에서는 Gradle을 다시 돌리지 않고 먼저 수정을 요구한다."""
    source = tmp_path / "application/src/main/java/com/example/OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    evidence = {
        "command": ["gradlew", "compileJava"],
        "exitCode": 1,
        "stderr": "cannot find symbol",
    }
    session = TaskCheckSession(tmp_path, "use-case", [])
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        side_effect=WorkspaceVerificationError(evidence),
    ) as verify:
        first_passed, _ = session.run()
        second_passed, second_output = session.run()

    assert first_passed is False
    assert second_passed is False
    assert "source has not changed" in second_output
    verify.assert_called_once()


def test_successful_agent_check_is_reused_only_for_the_same_source(tmp_path: Path) -> None:
    """에이전트가 통과시킨 동일 검사를 대화 종료 직후 다시 실행하지 않는다."""
    source = tmp_path / "application/src/main/java/com/example/OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    evidence = {"command": ["gradlew", "test"], "exitCode": 0}
    paths = ["application/src/main/java/com/example/OrderService.java"]
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        return_value=evidence,
    ) as verify:
        passed, _ = run_task_check(tmp_path, "use-case", paths)
        reused = consume_successful_task_check(tmp_path, "use-case", paths)

    assert passed is True
    assert reused == {**evidence, "reusedFromTaskCheck": True}
    verify.assert_called_once()


def test_use_case_scope_allows_owned_package_but_protects_other_features() -> None:
    """기능 전용 package 안의 새 파일은 허용하고 다른 기능과 생성 계약은 보호한다."""
    roots = ["application/src/main/java/com/example/application"]
    immutable = [
        "application/src/main/java/com/example/api",
        "application/src/main/java/com/example/bce/OrderControl.java",
    ]

    assert path_is_editable(
        "application/src/main/java/com/example/application/OrderService.java",
        [],
        roots,
        immutable,
    )
    assert not path_is_editable(
        "application/src/main/java/com/example/persistence/OrderRepository.java",
        [],
        roots,
        immutable,
    )
    assert not path_is_editable(
        "application/src/main/java/com/example/api/OrdersApi.java",
        [],
        roots,
        immutable,
    )
    assert not path_is_editable(
        "application/src/main/java/com/example/bce/OrderControl.java",
        [],
        roots,
        immutable,
    )


def _write_minimal_agent_task(tmp_path: Path) -> tuple[Path, str, str, Path]:
    """Conversation 수리 테스트가 함께 쓰는 작은 구현 작업을 만든다."""
    run = tmp_path / "run_abcdef123456"
    task_id = "implement-order"
    source_path = "application/src/main/java/com/example/application/OrderService.java"
    source = run / source_path
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    reports = run / "reports"
    tasks = reports / "implementation-tasks"
    tasks.mkdir(parents=True)
    prompt = tasks / "order.prompt.md"
    context = tasks / "order.context.json"
    prompt.write_text("Implement the order use case.", encoding="utf-8")
    context.write_text("{}", encoding="utf-8")
    task = {
        "task_id": task_id,
        "task_type": "use-case",
        "prompt_file": prompt.relative_to(run).as_posix(),
        "context_file": context.relative_to(run).as_posix(),
        "prompt_sha256": "prompt-hash",
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
        "immutable_paths": [],
        "llm": {
            "model": "openai/gpt-oss-120b",
            "baseUrl": "http://localhost",
            "temperature": 0.2,
            "topP": 1.0,
            "maxOutputTokens": 1024,
            "reasoningBudget": 0,
            "reasoningEffort": "medium",
            "chatTemplateKwargs": {},
        },
    }
    (tasks / "order.task.json").write_text(json.dumps(task), encoding="utf-8")
    return run, task_id, source_path, source


def test_verification_failure_continues_the_same_openhands_conversation(
    tmp_path: Path,
) -> None:
    """일반적인 focused 검사 실패는 현재 대화에서 바로 고친다."""
    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)

    class FakeConversation:
        def __init__(self, sandbox: Path) -> None:
            self.sandbox = sandbox
            self.messages: list[str] = []
            self.run_count = 0
            self.close_count = 0

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            if self.run_count == 2:
                (self.sandbox / source_path).write_text(
                    "class OrderService { int repaired; }",
                    encoding="utf-8",
                )

        def close(self) -> None:
            self.close_count += 1

    created: list[FakeConversation] = []

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        conversation = FakeConversation(sandbox)
        created.append(conversation)
        return conversation, SimpleNamespace(_tools={})

    failure = WorkspaceVerificationError(
        {
            "command": ["gradlew", "compileJava"],
            "exitCode": 1,
            "stderr": f"{source_path}: cannot find symbol",
        }
    )
    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.configured_api_key",
            return_value="approved-key",
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ) as create,
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            side_effect=[failure, {"command": ["gradlew", "compileJava"], "exitCode": 0}],
        ),
    ):
        result = execute_openhands_task(run, task_id)

    assert result["status"] == "SUCCEEDED"
    assert create.call_count == 1
    assert len(created[0].messages) == 2
    assert created[0].run_count == 2
    assert created[0].close_count == 1
    assert "int repaired" in source.read_text(encoding="utf-8")


def test_exhausted_openhands_conversation_restarts_with_the_same_workspace(
    tmp_path: Path,
) -> None:
    """iteration 한도에 닿은 대화는 닫고 짧은 수리 대화를 새로 연다."""
    run, task_id, source_path, source = _write_minimal_agent_task(tmp_path)

    class FakeConversation:
        def __init__(self, sandbox: Path, number: int) -> None:
            self.sandbox = sandbox
            self.number = number
            self.messages: list[str] = []
            self.run_count = 0
            self.close_count = 0

        def send_message(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> None:
            self.run_count += 1
            if self.number == 1:
                raise RuntimeError("Agent reached maximum iterations limit (32).")
            (self.sandbox / source_path).write_text(
                "class OrderService { int repairedInFreshContext; }",
                encoding="utf-8",
            )

        def close(self) -> None:
            self.close_count += 1

    created: list[FakeConversation] = []

    def create_conversation(sandbox: Path, *_args, **_kwargs):
        conversation = FakeConversation(sandbox, len(created) + 1)
        created.append(conversation)
        return conversation, SimpleNamespace(_tools={})

    failure = WorkspaceVerificationError(
        {
            "command": ["gradlew", "compileJava"],
            "exitCode": 1,
            "stderr": f"{source_path}: cannot find symbol",
        }
    )
    with (
        patch(
            "app.implementation.agents.runtime.openhands_compatibility",
            return_value={
                "pythonCompatible": True,
                "sdkInstalled": True,
                "toolsInstalled": True,
                "apiKeyConfigured": True,
            },
        ),
        patch(
            "app.implementation.agents.runtime.configured_api_key",
            return_value="approved-key",
        ),
        patch(
            "app.implementation.agents.runtime.create_openhands_conversation",
            side_effect=create_conversation,
        ),
        patch(
            "app.implementation.agents.runtime.verify_agent_workspace",
            side_effect=[failure, {"command": ["gradlew", "compileJava"], "exitCode": 0}],
        ),
    ):
        result = execute_openhands_task(run, task_id)

    assert result["status"] == "SUCCEEDED"
    assert len(created) == 2
    assert [item.run_count for item in created] == [1, 1]
    assert [item.close_count for item in created] == [1, 1]
    assert "cannot find symbol" in created[1].messages[0]
    assert "repairedInFreshContext" in source.read_text(encoding="utf-8")


def test_openhands_conversation_enables_stuck_detection_and_condensation(
    tmp_path: Path,
) -> None:
    """공식 SDK의 반복 감지와 context condenser를 기본 실행에 연결한다."""
    source = tmp_path / "OrderService.java"
    source.write_text("class OrderService {}", encoding="utf-8")
    llm = {
        "model": "openai/gpt-oss-120b",
        "baseUrl": "http://localhost",
        "temperature": 0.2,
        "topP": 1.0,
        "maxOutputTokens": 1024,
        "reasoningBudget": 0,
        "chatTemplateKwargs": {},
    }

    conversation, agent = create_openhands_conversation(
        tmp_path,
        [str(source.resolve())],
        "approved-key",
        llm,
    )
    try:
        conversation.send_message("Initialize tools without running the model.")
        assert conversation.stuck_detector is not None
        assert agent.condenser.__class__.__name__ == "LLMSummarizingCondenser"
        assert "grep" in agent._tools
    finally:
        conversation.close()


def test_completion_audit_rejects_an_unfinished_controller_body(
    tmp_path: Path,
) -> None:
    """파일이 있어도 Controller 미완성 본문이 남으면 구현 완료로 보지 않는다."""
    run = tmp_path / "run"
    reports = run / "reports"
    controller = run / "application/src/main/java/example/OrdersApiController.java"
    context = reports / "implementation-tasks/orders.context.json"
    controller.parent.mkdir(parents=True)
    context.parent.mkdir(parents=True)
    controller.write_text(
        'throw new UnsupportedOperationException("EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/orders");',
        encoding="utf-8",
    )
    context.write_text(
        json.dumps(
            {"controllerPaths": ["application/src/main/java/example/OrdersApiController.java"]}
        ),
        encoding="utf-8",
    )
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-orders",
                        "task_type": "use-case",
                        "context_file": "reports/implementation-tasks/orders.context.json",
                        "required_output_paths": [
                            "application/src/main/java/example/OrdersApiController.java"
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = audit_run_completion(run)

    assert report["status"] == "INCOMPLETE"
    assert "Unimplemented Controller body remains" in report["backlog"][0]["evidence"][0]


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
                        "task_id": task_id,
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
    class_model_payload["Classes"].extend(
        [
            {
                "className": "Order",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "identifier": ["id"],
                "fields": ["id : UUID"],
                "operations": [],
            },
            {
                "className": "CancelControl",
                "stereotype": "Control",
                "use_case_ids": ["UC2"],
                "operations": [],
            },
        ]
    )
    class_model = design / "class-model.json"
    class_model.write_text(json.dumps(class_model_payload), encoding="utf-8")
    sequence_model = design / "sequence-model.json"
    sequence_model.write_text(json.dumps(typed_sequence_model_payload()), encoding="utf-8")
    sequence = design / "sequence.puml"
    sequence.write_text("OrderBoundary -> OrderControl : place(request)\n", encoding="utf-8")
    requirements = design / "requirements.json"
    requirements.write_text(
        json.dumps(
            [
                {
                    "id": "FR-ORDER",
                    "text": "The customer can place an order.",
                    "type": "FR",
                    "use_case_ids": ["UC1"],
                    "repair_history": {"marker": "INTERNAL-REPAIR-MARKER"},
                },
                {
                    "id": "FR-CANCEL",
                    "text": "The customer can cancel an order.",
                    "type": "FR",
                    "use_case_ids": ["UC2"],
                },
            ]
        ),
        encoding="utf-8",
    )
    use_case_specs = design / "use-case-specs.json"
    use_case_specs.write_text(
        json.dumps(
            [
                {
                    "id": "UC1",
                    "use_case_id": "UC1",
                    "name": "Place order",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "The customer places an order."}
                    ],
                    "repair_iters": 7,
                    "repair_history": {"marker": "INTERNAL-USE-CASE-REPAIR"},
                },
                {"id": "UC2", "use_case_id": "UC2", "name": "Cancel order"},
            ]
        ),
        encoding="utf-8",
    )
    erd = design / "erd.puml"
    erd.write_text('entity "Order" as Order {\n  * id : UUID\n}\n', encoding="utf-8")
    openapi = design / "openapi.json"
    openapi.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "paths": {
                    "/orders": {
                        "post": {
                            "operationId": "placeOrder",
                            "responses": {"201": {"description": "Created"}},
                        }
                    },
                    "/orders/{id}": {
                        "delete": {
                            "operationId": "cancelOrder",
                            "responses": {"204": {"description": "Cancelled"}},
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    api_model = design / "api-model.json"
    api_model.write_text(
        json.dumps(
            {
                "Endpoints": [
                    {
                        "method": "POST",
                        "path": "/orders",
                        "operation_id": "placeOrder",
                        "use_case_ids": ["UC1"],
                        "control_binding": {"control": "OrderControl"},
                    },
                    {
                        "method": "DELETE",
                        "path": "/orders/{id}",
                        "operation_id": "cancelOrder",
                        "use_case_ids": ["UC2"],
                        "control_binding": {"control": "CancelControl"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    run = tmp_path / "run"
    package_root = run / "application/src/main/java/com/example/orders"
    (package_root / "api").mkdir(parents=True)
    (package_root / "bce").mkdir(parents=True)
    (package_root / "api/OrdersApi.java").write_text(
        "package com.example.orders.api;\n"
        'public interface OrdersApi { String PATH = "/orders"; '
        "void placeOrder(); }\n",
        encoding="utf-8",
    )
    (package_root / "api/CancelApi.java").write_text(
        "package com.example.orders.api;\n"
        'public interface CancelApi { String PATH = "/orders/{id}"; '
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
        (generated / f"{name}.ts").write_text(f"export class {name} {{}}\n", encoding="utf-8")
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
            "erdBceModel": class_model,
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

    state = plan_workflow(run, spec)
    manifest = json.loads((run / "reports/run-manifest.json").read_text(encoding="utf-8"))
    tasks = manifest["implementation_tasks"]
    task_types = {task["task_type"] for task in tasks}
    assert task_types == {"use-case", "frontend-implementation", "wiring"}
    assert (
        run / "application/src/main/java/com/example/orders/persistence/entity/OrderEntity.java"
    ).is_file()
    assert (
        run
        / "application/src/main/java/com/example/orders/persistence/repository/OrderRepository.java"
    ).is_file()
    assert (run / "application/src/main/resources/db/migration/V1__initial_schema.sql").is_file()
    assert not any(
        "BcePersistenceMapper" in path.as_posix() for path in (run / "application").rglob("*.java")
    )
    for task in tasks:
        assert set(task["required_output_paths"]) <= set(task["allowed_write_paths"])

    use_cases = [task for task in tasks if task["task_type"] == "use-case"]
    wiring = next(task for task in tasks if task["task_type"] == "wiring")
    assert len(use_cases) == 2
    assert set(wiring["use_case_ids"]) == {"UC1", "UC2"}
    assert wiring["repair_only"] is True
    assert wiring["required_output_paths"] == []
    assert "implement-application-wiring" not in state["nextRunnableTasks"]
    expected_parallel_tasks = {
        task["task_id"]
        for task in tasks
        if task["task_type"] in {"use-case", "frontend-implementation"}
    }
    assert set(state["nextRunnableTasks"]) == expected_parallel_tasks
    contexts = [
        json.loads((run / task["context_file"]).read_text(encoding="utf-8")) for task in use_cases
    ]
    expected_requirements = {"UC1": {"FR-ORDER"}, "UC2": {"FR-CANCEL"}}
    partitions = [set(context["useCaseIds"]) for context in contexts]
    assert set().union(*partitions) == set(expected_requirements)
    assert partitions == [{"UC1"}, {"UC2"}]
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(partitions)
        for right in partitions[index + 1 :]
    )
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
        not set(task["allowed_write_paths"]).intersection(generated_api) for task in use_cases
    )
    immutable_bce = {
        "application/src/main/java/com/example/orders/bce/OrderBoundary.java",
        "application/src/main/java/com/example/orders/bce/OrderControl.java",
        "application/src/main/java/com/example/orders/bce/CancelControl.java",
    }
    assert all(
        not set(task["allowed_write_paths"]).intersection(immutable_bce) for task in use_cases
    )
    assert use_cases[0]["allowed_write_roots"]
    assert all(
        "application/src/main/java" not in task["allowed_write_roots"]
        for task in use_cases
    )
    uc1_task = next(task for task in use_cases if task["use_case_ids"] == ["UC1"])
    uc1_prompt = (run / uc1_task["prompt_file"]).read_text(encoding="utf-8")
    assert "The customer can place an order." in uc1_prompt
    assert '"call_id"' in uc1_prompt
    assert '"control_binding"' in uc1_prompt
    assert "INTERNAL-REPAIR-MARKER" not in uc1_prompt
    assert "INTERNAL-USE-CASE-REPAIR" not in uc1_prompt
    assert "Write the focused JUnit scenario first" not in uc1_prompt


def test_scenario_failure_returns_to_automatic_repair_without_user_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """최종 검사도 오류 파일을 원래 소유한 기능 작업으로 돌려보낸다."""
    run = tmp_path / "run"
    flow_path = "application/src/test/java/com/example/OrderScenarioTest.java"
    flow = run / flow_path
    flow.parent.mkdir(parents=True)
    flow.write_text("class OrderScenarioTest {}", encoding="utf-8")
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-order-use-cases",
                        "task_type": "use-case",
                        "allowed_write_paths": [flow_path],
                    },
                    {
                        "task_id": "implement-application-wiring",
                        "task_type": "wiring",
                        "allowed_write_paths": [flow_path],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: {
            "status": "COMPLETE",
            "tasks": [
                {"task_id": "implement-order-use-cases", "status": "SUCCEEDED", "phase": "use-cases"}
            ],
            "phases": [{"phaseId": "use-cases", "status": "SUCCEEDED"}],
            "nextRunnableTasks": [],
        },
    )

    def failed_scenario(_run_root: Path) -> dict[str, object]:
        raise WorkspaceVerificationError(
            {
                "command": ["gradlew", "test", "--tests", "*OrderScenarioTest", "--build-cache"],
                "exitCode": 1,
                "stderr": f"{flow_path}: scenario assertion failed",
            }
        )

    result = run_workflow(run, SimpleNamespace(app_id="app-1"), None, verifier=failed_scenario)

    assert result["status"] == "COMPLETE"
    assert result.get("blockingReason") is None
    assert result["repairPlan"] == "reports/repair-plan.json"
    repair = json.loads((reports / "repair-plan.json").read_text(encoding="utf-8"))
    assert repair["status"] == "ACTIVE"
    assert repair["entries"][-1]["ownerTaskIds"] == ["implement-order-use-cases"]
    assert repair["entries"][-1]["repairPaths"] == [flow_path]


def test_repair_uses_a_small_prompt_and_restores_the_accepted_source(
    tmp_path: Path,
) -> None:
    """자동 수리는 전체 초기 설명과 실패한 임시 코드를 다음 대화로 넘기지 않는다."""
    run = tmp_path / "run_repair_prompt"
    reports = run / "reports"
    task_dir = reports / "implementation-tasks"
    task_dir.mkdir(parents=True)
    source_path = "application/src/main/java/com/example/ApplicationConfiguration.java"
    source = run / source_path
    source.parent.mkdir(parents=True)
    source.write_text("class ApplicationConfiguration { /* accepted */ }", encoding="utf-8")
    prompt_path = task_dir / "wiring.md"
    initial_prompt = "INITIAL IMPLEMENTATION CONTEXT\n" + ("all requirements\n" * 100)
    prompt_path.write_text(initial_prompt, encoding="utf-8")
    task = {
        "task_id": "implement-application-wiring",
        "task_type": "wiring",
        "prompt_file": str(prompt_path.relative_to(run)).replace("\\", "/"),
        "allowed_write_paths": [source_path],
        "required_output_paths": [source_path],
        "immutable_paths": [],
    }
    (task_dir / "wiring.task.json").write_text(json.dumps(task), encoding="utf-8")
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )

    entry = schedule_cross_phase_repair(
        run,
        "verify-container-runtime",
        {
            "command": ["docker", "runtime-smoke"],
            "stderr": "frontend HTTP probe failed: HTTP 401 Unauthorized",
        },
        failed_task_type="wiring",
    )
    assert entry is not None
    execution_dir = reports / "agent-executions"
    execution_dir.mkdir()
    (execution_dir / "implement-application-wiring.result.json").write_text(
        json.dumps(
            {
                "repairHistory": {
                    "attempts": [
                        {
                            "strategy_key": "verification_correction",
                            "outcome": "no_improvement",
                            "candidate_digest": "candidate-401",
                            "detail": "SecurityConfiguration.java를 수정했지만 401이 계속됨",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repeated_entries = [entry]
    for _ in range(5):
        repeated = schedule_cross_phase_repair(
            run,
            "verify-container-runtime",
            {
                "command": ["docker", "runtime-smoke"],
                "stderr": "frontend HTTP probe failed: HTTP 401 Unauthorized",
            },
            failed_task_type="wiring",
        )
        assert repeated is not None
        repeated_entries.append(repeated)
    apply_repair_directives(run)

    stored_task = json.loads((task_dir / "wiring.task.json").read_text(encoding="utf-8"))
    repair_prompt = (run / stored_task["repair_prompt_file"]).read_text(encoding="utf-8")
    assert prompt_path.read_text(encoding="utf-8") == initial_prompt
    assert "INITIAL IMPLEMENTATION CONTEXT" not in repair_prompt
    assert "401 Unauthorized" in repair_prompt
    assert "SecurityConfiguration.java를 수정했지만 401이 계속됨" in repair_prompt
    assert "새 진단 가설 2" in repair_prompt
    assert len({item["strategy"] for item in repeated_entries}) == 6
    assert source_path in repair_prompt
    assert entry["acceptedSourceRoot"] == "application"
    assert entry["acceptedSourceDigest"]

    sandbox = prepare_agent_workspace(run, stored_task)
    (sandbox / source_path).write_text("class Broken {}", encoding="utf-8")
    extra = sandbox / "application/src/main/java/com/example/Unrelated.java"
    extra.write_text("class Unrelated {}", encoding="utf-8")
    restored = prepare_agent_workspace(run, stored_task, preserve_failed_edits=False)
    assert (restored / source_path).read_text(encoding="utf-8") == (
        "class ApplicationConfiguration { /* accepted */ }"
    )
    assert not extra.exists()
    cleanup_agent_workspace(restored)


def test_retried_release_failure_returns_to_wiring_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """완료 작업을 재검증하다 난 컨테이너 오류도 wiring 수리로 이어진다."""
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "implementation_tasks": [
                    {
                        "task_id": "implement-application-wiring",
                        "task_type": "wiring",
                        "allowed_write_paths": ["application/src/main/java"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    completed = {
        "status": "COMPLETE",
        "tasks": [
            {
                "task_id": "implement-application-wiring",
                "status": "SUCCEEDED",
                "phase": "wiring",
            }
        ],
        "phases": [{"phaseId": "wiring", "status": "SUCCEEDED"}],
        "nextRunnableTasks": [],
    }
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.plan_workflow",
        lambda *_args: dict(completed),
    )
    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.verify_source_design_conformance",
        lambda *_args: {"status": "PASSED"},
    )

    def failed_container(*_args: object) -> None:
        raise WorkspaceVerificationError(
            {
                "command": ["docker", "runtime-smoke"],
                "exitCode": 1,
                "stderr": "frontend HTTP probe failed: HTTP 401 Unauthorized",
            }
        )

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator._complete_release",
        failed_container,
    )

    result = run_workflow(
        run,
        SimpleNamespace(app_id="app-1"),
        None,
        verifier=lambda _run: {"status": "SUCCEEDED"},
        auditor=lambda _run: {"status": "COMPLETE"},
    )

    assert result.get("blockingReason") is None
    repair = json.loads((reports / "repair-plan.json").read_text(encoding="utf-8"))
    assert repair["entries"][-1]["ownerTaskIds"] == ["implement-application-wiring"]
    assert "401 Unauthorized" in repair["entries"][-1]["evidence"]


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
        'CheckoutGateway gateway; void run() { gateway.charge("order-1"); } }',
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
        (tmp_path / "reports/source-design-conformance.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "FAILED"
    assert "GENERATED_CONTRACT_CHANGED" in {item["code"] for item in report["violations"]}


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
    class_model.write_text(
        "class Order <<Entity>> { + rename(value: string): string }", encoding="utf-8"
    )
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
