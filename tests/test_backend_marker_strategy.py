from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.implementation.agents.runtime import create_openhands_conversation
from app.implementation.agents.task_check import (
    consume_successful_task_check,
    has_successful_task_check,
    run_task_check,
)
from app.implementation.agents.verification.build import _verify_absent_markers
from app.implementation.planning.design_context import (
    TaskSpec,
    _build_backend_marker_tasks,
    _method_context_evidence,
    _render_adapter_contract,
    _UseCaseBundle,
)
from app.implementation.planning.method_projection import (
    MethodProjection,
    MethodRef,
    MethodSlice,
)
from app.llm_connection import LlmConnection


def test_glm_profile_bounds_openhands_turns_to_8k_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.implementation.agents.runtime.settings.openhands_max_output_tokens",
        None,
    )
    conversation, agent = create_openhands_conversation(
        tmp_path,
        LlmConnection(
            provider="cloudflare",
            api_key="validation-only-key",
            base_url="https://example.invalid/v1",
            model="@cf/zai-org/glm-5.3-flash",
            litellm_provider="openai",
        ),
        {"temperature": 0.9, "maxOutputTokens": 16384},
        task_type="backend-operation",
        verification_paths=[],
        editable_roots=[str(tmp_path.resolve())],
        native_owner_tools=True,
        owner_tool_mode="restricted",
        reasoning_effort="medium",
    )
    try:
        assert agent.llm.max_output_tokens == 8192
        assert agent.llm.temperature == 0.2
        assert agent.llm.reasoning_effort == "medium"
    finally:
        conversation.close()


def test_method_context_includes_referenced_extension_handling_step() -> None:
    method = MethodRef(
        class_name="TermManagementService",
        stereotype="Control",
        operation_id="TermManagementService::updateRegistrationPeriod()",
        stable_id="op_update_term",
        name="updateRegistrationPeriod",
        parameters=(),
        return_type="AcademicTerm",
    )
    projection = MethodProjection(
        method=method,
        slices=(
            MethodSlice(
                use_case_ids=("UC10",),
                incoming_call_id="call:update-term",
                incoming_source="AdminPortal",
                method=method,
                outgoing=(),
                return_type="AcademicTerm",
                step_refs=("UC10:extension:1a:1a2",),
                reasons=(),
            ),
        ),
        generation="generated",
        reasons=(),
    )

    evidence = _method_context_evidence(
        projection,
        requirements_by_id={},
        use_cases=[
            {
                "use_case_id": "UC10",
                "extensions": [
                    {
                        "label": "1a",
                        "condition": "The term already exists.",
                        "outcome": "The registration period is updated.",
                        "handling_steps": [
                            {
                                "sub_step": "1a2",
                                "sentence": "The system updates the registration period.",
                            }
                        ],
                    }
                ],
            }
        ],
        endpoints=[],
    )

    assert evidence["scenarioSteps"] == [
        {
            "ref": "UC10:extension:1a:1a2",
            "branchCondition": "The term already exists.",
            "outcome": "The registration period is updated.",
            "sub_step": "1a2",
            "sentence": "The system updates the registration period.",
        }
    ]


def test_backend_markers_form_one_sequential_compile_only_chain(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    output = run / "reports/implementation-tasks"
    method_context_root = output / "method-context"
    method_context_root.mkdir(parents=True)

    units = [
        (
            "op_domain",
            "Entity",
            "AcademicTerm",
            "updateRegistrationPeriod",
            "application/src/main/java/com/example/app/bce/AcademicTerm.java",
            "UC10:extension:1a:1a2",
        ),
        (
            "op_application",
            "Control",
            "TermManagementService",
            "createTerm",
            "application/src/main/java/com/example/app/application/impl/TermManagementServiceService.java",
            "UC10:main:2",
        ),
    ]
    method_entries = []
    for stable_id, stereotype, class_name, name, target, step_ref in units:
        target_path = run / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            f'class {class_name} {{ /* EASYDEP-IMPLEMENT:{stable_id} */ }}',
            encoding="utf-8",
        )
        context_path = method_context_root / f"{stable_id}.json"
        context_path.write_text(
            json.dumps(
                {
                    "method": {
                        "stable_id": stable_id,
                        "stereotype": stereotype,
                        "class_name": class_name,
                        "operation_id": f"{class_name}::{name}()",
                        "name": name,
                        "parameters": [],
                        "return_type": (
                            "AcademicTerm" if stable_id == "op_application" else "void"
                        ),
                    },
                    "refs": ["use_case:UC10", "requirement:RR12", f"step:{step_ref}"],
                    "sourcePaths": [target],
                    "scenarioSteps": [{"ref": step_ref, "sentence": "Perform the step."}],
                    "slices": [],
                }
            ),
            encoding="utf-8",
        )
        method_entries.append(
            {
                "stableId": stable_id,
                "path": context_path.relative_to(run).as_posix(),
                "refs": [
                    f"step:{step_ref}",
                    *(["api:manageTerm"] if stable_id == "op_application" else []),
                ],
            }
        )

    controller = (
        "application/src/main/java/com/example/app/adapter/in/web/"
        "AdminApiController.java"
    )
    generated_contracts = {
        "application/src/main/java/com/example/app/api/AdminApi.java": (
            "interface AdminApi { void manageTerm(); }"
        ),
        "application/src/main/java/com/example/app/api/model/ManageTermRequest.java": (
            "class ManageTermRequest { String getName() { return null; } }"
        ),
        "application/src/main/java/com/example/app/api/model/AcademicTerm.java": (
            "class AcademicTerm {}"
        ),
        "application/src/main/java/com/example/app/bce/TermManagementService.java": (
            "interface TermManagementService { AcademicTerm createTerm(String name); }"
        ),
        controller: (
            "import com.example.app.api.AdminApi;\n"
            "public class AdminApiController implements AdminApi { "
            '/* EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/admin/terms */ }'
        ),
    }
    for relative, content in generated_contracts.items():
        path = run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    endpoint = {
        "path": "/admin/terms",
        "method": "post",
        "operation_id": "manageTerm",
        "request_schema": "ManageTermRequest",
        "responses": [
            {"status": 201, "schema_name": "AcademicTerm"},
            {"status": 200, "schema_name": "AcademicTerm"},
            {"status": 400, "schema_name": ""},
        ],
        "use_case_ids": ["UC10"],
        "control_binding": {
            "control": "TermManagementService",
            "method": "createTerm",
            "arguments": [{"name": "name", "source": "$body.name"}],
            "outcomes": [
                {"status": 201, "outcome": "created"},
                {"status": 200, "outcome": "ok"},
                {"status": 400, "outcome": "validation_error"},
            ],
        },
    }
    (output / "implement-backend-application.source-index.json").write_text(
        json.dumps({"methodContexts": method_entries}),
        encoding="utf-8",
    )
    owner_context = output / "implement-backend-application.context.json"
    owner_context.write_text(
        json.dumps({"controllerPaths": [controller]}), encoding="utf-8"
    )
    owner = TaskSpec(
        task_id="implement-backend-application",
        control="backend",
        prompt_file="unused.prompt.md",
        context_file=owner_context.relative_to(run).as_posix(),
        allowed_write_paths=[],
        immutable_paths=["application/src/main/java/com/example/app/api"],
        source_artifacts={},
        prompt_sha256="owner-prompt",
        llm={},
        owner="backend",
        task_type="backend-implementation",
    )

    with patch(
        "app.implementation.planning.design_context.llm_config",
        return_value={
            "provider": "cloudflare",
            "model": "@cf/zai-org/glm-5.3-flash",
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "reasoningEffort": "medium",
        },
    ):
        tasks = _build_backend_marker_tasks(
            SimpleNamespace(name="terms", allow_assumptions=True),
            run,
            output,
            "com/example/app",
            _UseCaseBundle(("UC10",), (), (), (endpoint,)),
            owner,
        )

    assert [task.task_type for task in tasks] == ["backend-operation"] * 3
    assert [task.control.split()[1] for task in tasks] == [
        "DOMAIN_READY",
        "APPLICATION_READY",
        "ADAPTER_READY",
    ]
    assert tasks[0].depends_on == []
    assert tasks[1].depends_on == [tasks[0].task_id]
    assert tasks[2].depends_on == [tasks[1].task_id]
    assert tasks[2].task_id == "implement-backend-application"
    assert all(task.required_test_paths == [] for task in tasks)
    assert all(
        task.allowed_write_roots == ["application/src/main/java/com/example/app"]
        for task in tasks
    )
    for task in tasks:
        context = json.loads((run / task.context_file).read_text(encoding="utf-8"))
        prompt = (run / task.prompt_file).read_text(encoding="utf-8")
        assert context["parallelImplementation"] is False
        assert context["completionMarkers"]
        assert "does not own test authoring" in prompt
        assert "call `run_task_check` once" in prompt

    domain_prompt = (run / tasks[0].prompt_file).read_text(encoding="utf-8")
    application_prompt = (run / tasks[1].prompt_file).read_text(encoding="utf-8")
    assert "same-named state field" not in domain_prompt
    assert "UUID.randomUUID" not in application_prompt
    assert "AcademicTermRepository" not in application_prompt

    adapter = tasks[2]
    adapter_context = json.loads(
        (run / adapter.context_file).read_text(encoding="utf-8")
    )
    assert set(adapter_context["readSourcePaths"]) == {
        controller,
        "application/src/main/java/com/example/app/api/AdminApi.java",
        "application/src/main/java/com/example/app/api/model/ManageTermRequest.java",
        "application/src/main/java/com/example/app/api/model/AcademicTerm.java",
        "application/src/main/java/com/example/app/bce/TermManagementService.java",
    }
    adapter_prompt = (run / adapter.prompt_file).read_text(encoding="utf-8")
    assert "TermManagementService::createTerm" in adapter_prompt
    assert "name <- $body.name" in adapter_prompt
    assert "multiple success statuses (201, 200)" in adapter_prompt
    assert "return HTTP `201`" not in adapter_prompt
    assert "No exception or result binding is declared" in adapter_prompt
    assert "IllegalArgumentException" not in adapter_prompt
    assert "interaction_id" not in adapter_prompt


def test_adapter_success_uses_typed_http_status_not_method_or_outcome_name() -> None:
    lines = _render_adapter_contract(
        {
            "path": "/widgets/{widgetId}",
            "method": "delete",
            "operation_id": "frob",
            "responses": [{"status": 204, "schema_name": ""}],
            "control_binding": {
                "control": "ArbitraryControl",
                "method": "frob",
                "arguments": [
                    {"name": "opaque", "source": "$path.widgetId"},
                ],
                "outcomes": [
                    {"status": 204, "outcome": "any_label"},
                    {"status": 409, "outcome": "also_arbitrary"},
                ],
            },
        }
    )
    prompt = "\n".join(lines)

    assert "ArbitraryControl::frob" in prompt
    assert "opaque <- $path.widgetId" in prompt
    assert "return HTTP `204`" in prompt
    assert "HTTP `409` is labeled `also_arbitrary`" in prompt
    assert "do not infer one from this label" in prompt


def test_marker_finish_evidence_requires_absent_marker_and_unchanged_source(
    tmp_path: Path,
) -> None:
    relative = "application/src/main/java/com/example/AcademicTerm.java"
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    marker = "EASYDEP-IMPLEMENT:op_update_term"
    profile = {
        "requiredAbsentMarkers": [{"path": relative, "markers": [marker]}]
    }
    source.write_text(f"class AcademicTerm {{ /* {marker} */ }}", encoding="utf-8")

    remaining = _verify_absent_markers(tmp_path, profile)
    assert remaining is not None
    assert remaining["remainingMarkers"] == [{"path": relative, "marker": marker}]

    source.write_text("class AcademicTerm { int ready; }", encoding="utf-8")
    with patch(
        "app.implementation.agents.task_check.verify_agent_workspace",
        return_value={"command": ["gradlew", "compileJava"], "exitCode": 0},
    ):
        passed, _output = run_task_check(
            tmp_path,
            "backend-operation",
            [relative],
            profile,
        )

    assert passed is True
    assert has_successful_task_check(
        tmp_path,
        "backend-operation",
        [relative],
        profile,
    )
    source.write_text("class AcademicTerm { int changedAfterCheck; }", encoding="utf-8")
    assert not has_successful_task_check(
        tmp_path,
        "backend-operation",
        [relative],
        profile,
    )
    assert consume_successful_task_check(
        tmp_path,
        "backend-operation",
        [relative],
        profile,
    ) is None
