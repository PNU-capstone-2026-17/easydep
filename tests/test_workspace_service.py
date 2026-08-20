from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from app.db.models import Base
from app.repositories import artifact_repository
from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.service import WorkspaceService

import json


class RejectingExecutor:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("cross-stage feedback must wait for confirmation")

    def shutdown(self, **_kwargs):
        return None


def test_workspace_tables_are_part_of_the_shared_database_schema() -> None:
    assert {
        "workspace_commands",
        "workspace_events",
        "deployment_preferences",
    } <= set(Base.metadata.tables)


def test_chat_event_timestamp_is_returned_as_explicit_korean_time() -> None:
    event = repository.event_dict(
        SimpleNamespace(
            event_id=1,
            app_id="app-1",
            command_id=None,
            stage="requirements",
            kind="message",
            actor="user",
            text="hello",
            event_data={},
            # MySQL DATETIME values are stored as naive UTC values.
            created_at=datetime(2026, 8, 19, 5, 30, 21),
        )
    )

    assert event["created_at"] == "2026-08-19T14:30:21+09:00"


def test_artifact_stage_is_normalized_to_the_user_visible_workflow_stage() -> None:
    assert repository.workflow_stage("resource_spec") == "requirements"
    assert repository.workflow_stage("capability_contract") == "requirements"
    assert repository.workflow_stage("resource_intake") == "requirements"
    assert repository.workflow_stage("deployment_diagram") == "design"
    assert repository.workflow_stage(None) == "requirements"


def test_cross_stage_feedback_waits_before_mutating_artifacts(monkeypatch) -> None:
    commands: dict[str, dict] = {}
    events: list[dict] = []

    monkeypatch.setattr(
        workspace_module.artifact_repository, "ensure_app_exists", lambda _app_id: None
    )

    def create_command(command_id, app_id, action, stage, payload):
        command = {
            "command_id": command_id,
            "app_id": app_id,
            "action": action,
            "stage": stage,
            "status": "QUEUED",
            "payload": payload,
            "result": None,
        }
        commands[command_id] = command
        return command.copy()

    def update_command(command_id, **changes):
        commands[command_id].update(changes)
        return commands[command_id].copy()

    def get_command(command_id):
        return commands.get(command_id)

    monkeypatch.setattr(repository, "create_command", create_command)
    monkeypatch.setattr(repository, "update_command", update_command)
    monkeypatch.setattr(repository, "get_command", get_command)
    monkeypatch.setattr(repository, "append_event", lambda *args, **kwargs: events.append(kwargs))

    service = WorkspaceService()
    service._executor.shutdown(wait=False, cancel_futures=True)
    service._executor = RejectingExecutor()
    command = service.submit(
        "app-1",
        action="message",
        stage="implementation",
        payload={
            "text": "ERD의 관계를 바꿔줘",
            "context": {"stage": "design", "artifact_stage": "erd"},
        },
    )

    assert commands[command["command_id"]]["status"] == "AWAITING_INPUT"
    assert commands[command["command_id"]]["result"]["action"] == "confirm_change"
    assert events[-1]["kind"] == "action_required"


def test_requirement_reply_uses_the_waiting_command_as_continuation(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda *_args, **_kwargs: {
            "command_id": "prior",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
        },
    )

    def analyze(request):
        captured["request"] = request
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"status": "completed", "saved_stages": []}
        )

    monkeypatch.setattr(workspace_module, "analyze_endpoint", analyze)
    service = WorkspaceService()
    try:
        result = service._stage_message(
            {
                "command_id": "reply",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {"text": "서울 리전입니다", "action_id": "prior"},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert captured["request"].answer == "서울 리전입니다"
    assert captured["request"].requirements is None
    assert result["message"] == "Requirements analysis completed."


def test_requirement_reply_answers_the_resource_question_without_reclassification(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda *_args, **_kwargs: {
            "command_id": "prior",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {
                "resource_question": {
                    "field": "monthlyBudgetUSD",
                    "kind": "missing",
                    "question": "What is the monthly budget?",
                }
            },
        },
    )

    def analyze(request):
        captured["request"] = request
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"status": "completed", "saved_stages": []}
        )

    monkeypatch.setattr(workspace_module, "analyze_endpoint", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "reply",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {"text": "100 USD", "action_id": "prior"},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert captured["request"].resource_answers == {"monthlyBudgetUSD": "100 USD"}
    assert captured["request"].answer is None


def test_initial_workspace_request_accepts_provider_and_region_without_budget(
    monkeypatch,
) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"status": "completed", "saved_stages": []}
        )

    monkeypatch.setattr(workspace_module, "analyze_endpoint", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "initial",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {
                    "text": "Students can register for a course.",
                    "provider": "aws",
                    "region": "ap-northeast-2",
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    constraints = captured["request"].cloud_constraints
    assert constraints.provider == "aws"
    assert constraints.region == "ap-northeast-2"
    assert constraints.monthly_budget_amount is None


def test_initial_workspace_request_can_start_before_cloud_selection(monkeypatch) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"status": "completed", "saved_stages": []}
        )

    monkeypatch.setattr(workspace_module, "analyze_endpoint", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "initial",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {"text": "Students can register for a course."},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    request = captured["request"]
    assert request.cloud_constraints is None
    assert request.requirements == ["Students can register for a course."]


def test_structured_deployment_preferences_resume_the_waiting_requirements_gate(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda command_id: {
            "command_id": command_id,
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {"resource_questions": [{"field": "provider"}]},
        },
    )

    def analyze(request):
        captured["request"] = request
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"status": "completed", "saved_stages": []}
        )

    monkeypatch.setattr(workspace_module, "analyze_endpoint", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "resume",
                "app_id": "app-1",
                "action": "apply_deployment_preferences",
                "stage": "requirements",
                "payload": {
                    "action_id": "prior",
                    "deployment_preferences": {
                        "targets": [
                            {
                                "provider": "aws",
                                "region": "ap-northeast-2",
                                "zones": ["ap-northeast-2a"],
                            },
                            {
                                "provider": "gcp",
                                "region": "asia-northeast3",
                                "zones": ["asia-northeast3-a"],
                            },
                        ]
                    },
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    preferences = captured["request"].deployment_preferences
    assert preferences is not None
    assert [target.provider for target in preferences.targets] == ["aws", "gcp"]
    assert captured["request"].answer is None


def test_saved_coordinates_do_not_answer_a_later_budget_question(monkeypatch) -> None:
    monkeypatch.setattr(
        repository,
        "get_deployment_preferences",
        lambda _app_id: {
            "targets": [
                {
                    "provider": "aws",
                    "region": "ap-northeast-2",
                    "zones": ["ap-northeast-2a"],
                }
            ]
        },
    )
    monkeypatch.setattr(
        repository,
        "latest_command",
        lambda _app_id: {
            "command_id": "budget-question",
            "stage": "requirements",
            "status": "AWAITING_INPUT",
            "result": {"resource_questions": [{"field": "monthlyBudgetUSD"}]},
        },
    )
    service = WorkspaceService()
    try:
        assert service.apply_saved_deployment_preferences("app-1") is None
    finally:
        service.shutdown()


def test_initial_workspace_request_forwards_structured_monthly_budget(monkeypatch) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"status": "completed", "saved_stages": []}
        )

    monkeypatch.setattr(workspace_module, "analyze_endpoint", analyze)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                "command_id": "initial",
                "app_id": "app-1",
                "stage": "requirements",
                "payload": {
                    "text": "Students can register for a course.",
                    "provider": "aws",
                    "region": "ap-northeast-2",
                    "monthly_budget_amount": 300000,
                    "monthly_budget_currency": "KRW",
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    constraints = captured["request"].cloud_constraints
    assert constraints.monthly_budget_amount == 300000
    assert constraints.monthly_budget_currency == "KRW"


def test_workspace_cloud_options_include_supported_default_regions() -> None:
    from app.workspace.api import cloud_options

    options = cloud_options()
    codes = {
        provider: {item["code"] for item in rows}
        for provider, rows in options["regions"].items()
    }
    assert "ap-northeast-2" in codes["aws"]
    assert "koreacentral" in codes["azure"]
    assert "asia-northeast3" in codes["gcp"]
    seoul = next(
        item for item in options["regions"]["aws"] if item["code"] == "ap-northeast-2"
    )
    assert isinstance(seoul["latitude"], float)
    assert isinstance(seoul["longitude"], float)
    assert seoul["zones"]


def test_requirements_progress_is_persisted_as_workspace_events(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    report = WorkspaceService._requirements_progress_reporter("app-1", "command-1")
    report("analysisStepStarted", {"step": "clarify"})
    report(
        "llmOperationFinished",
        {
            "operation": "structured:ClarifyOnlyResult",
            "status": "completed",
            "elapsedSeconds": 1.25,
        },
    )

    assert [event["kind"] for event in events] == ["progress", "progress"]
    assert events[0]["stage"] == "requirements"
    assert events[0]["command_id"] == "command-1"
    assert events[1]["metadata"]["elapsedSeconds"] == 1.25
    assert events[1]["metadata"]["analysis_step"] == "clarify"
    assert events[1]["metadata"]["progress_step_label"] == (
        "Refining ambiguous or compound requirements"
    )
    assert events[1]["metadata"]["progress_detail"] == (
        "AI requirement refinement completed in 1.2s"
    )


def test_design_operation_emits_a_named_progress_card(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    response = WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="sequence_diagram",
        label="Retrying the sequence diagram",
        operation=lambda: "done",
    )

    assert response == "done"
    assert [event["metadata"]["progress_status"] for event in events] == [
        "running",
        "completed",
    ]
    assert events[-1]["metadata"]["progress_card_label"] == "Design generation"


def test_design_operation_marks_a_generated_draft_as_needing_review(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    response = WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="sequence_diagram",
        label="Retrying the sequence diagram",
        operation=lambda: JSONResponse(
            content={
                "validation": {
                    "sequence_diagram": {"findings": ["missing flow step"]}
                }
            }
        ),
    )

    assert isinstance(response, JSONResponse)
    assert events[-1]["metadata"]["progress_status"] == "needs_review"
    assert "1 findings require revision" in events[-1]["metadata"]["progress_detail"]


def test_design_api_completed_status_finishes_the_workspace_stage() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "completed",
                "stage": None,
                "class_diagram_puml": "@startuml\n@enduml",
            }
        )
    finally:
        service.shutdown()

    assert result["message"] == "Design artifact generation completed."
    assert "awaiting_input" not in result


def test_design_feedback_status_remains_a_workspace_review_gate() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {"status": "need_feedback", "stage": "deployment_diagram"}
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["current_stage"] == "deployment_diagram"


def test_design_findings_without_an_artifact_require_revision() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "sequence_diagram",
                "validation": {
                    "sequence_diagram": {"findings": ["missing flow step"]}
                },
            }
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["requires_revision"] is True
    assert result["blocking_findings"] == ["missing flow step"]
    assert "before continuing" in result["message"]


def test_design_findings_with_a_generated_artifact_allow_continue() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "sequence_diagram",
                "artifacts": {"sequence_diagram": "@startuml\n@enduml"},
                "validation": {
                    "sequence_diagram": {"findings": ["missing flow step"]}
                },
            }
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["requires_revision"] is False
    assert result["blocking_findings"] == []
    assert result["findings"] == ["missing flow step"]
    assert "continued to the next stage" in result["message"]


def test_design_findings_use_persisted_artifact_when_response_omits_it(monkeypatch) -> None:
    monkeypatch.setattr(
        artifact_repository,
        "load_state",
        lambda _app_id: {"sequence_diagram_puml": "@startuml\n@enduml"},
    )
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "app_id": "app-1",
                "status": "need_feedback",
                "stage": "sequence_diagram",
                "validation": {
                    "sequence_diagram": {"findings": ["missing flow step"]}
                },
            }
        )
    finally:
        service.shutdown()

    assert result["requires_revision"] is False
    assert result["blocking_findings"] == []
    assert "continued to the next stage" in result["message"]


def test_retry_design_accepts_only_a_failed_design_command(monkeypatch) -> None:
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda _command_id: {
            "command_id": "failed-design",
            "app_id": "app-1",
            "stage": "design",
            "status": "FAILED",
            "result": None,
        },
    )

    service = WorkspaceService()
    try:
        service._validate_action_reference(
            "app-1", "retry_design", {"action_id": "failed-design"}
        )
    finally:
        service.shutdown()


def test_requirements_progress_tracks_only_active_use_case_spec_tasks(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    report = WorkspaceService._requirements_progress_reporter("app-1", "command-1")
    report("analysisStepStarted", {"step": "generate_specs"})
    report("specTaskStarted", {"useCaseId": "UC1", "useCaseName": "Browse courses"})
    report("specTaskStarted", {"useCaseId": "UC2", "useCaseName": "Enroll"})
    assert events[-1]["metadata"]["active_spec_tasks"] == [
        {"id": "UC1", "name": "Browse courses"},
        {"id": "UC2", "name": "Enroll"},
    ]

    report(
        "specTaskFinished",
        {"useCaseId": "UC1", "useCaseName": "Browse courses", "status": "completed"},
    )
    assert events[-1]["metadata"]["active_spec_tasks"] == [
        {"id": "UC2", "name": "Enroll"}
    ]

    report(
        "specTaskFinished",
        {"useCaseId": "UC2", "useCaseName": "Enroll", "status": "completed"},
    )
    assert events[-1]["metadata"]["active_spec_tasks"] == []


def test_requirements_progress_exposes_concurrent_analysis_steps(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    report = WorkspaceService._requirements_progress_reporter("app-1", "command-1")
    report("analysisStepStarted", {"step": "derive_deployment_needs"})
    report("analysisStepStarted", {"step": "extract_resource_constraints"})

    assert events[-1]["metadata"]["active_analysis_steps"] == [
        "derive_deployment_needs",
        "extract_resource_constraints",
    ]

    report(
        "analysisStepFinished",
        {
            "step": "derive_deployment_needs",
            "status": "completed",
            "elapsedSeconds": 1.0,
        },
    )
    assert events[-1]["metadata"]["active_analysis_steps"] == [
        "extract_resource_constraints"
    ]


def test_workspace_replaces_internal_feedback_prompt_with_english_ui_copy() -> None:
    service = WorkspaceService()
    try:
        result = service._requirements_result(
            {
                "status": "need_feedback",
                "phase": "requirements",
                "feedback_prompt": "내부 에이전트용 문구",
                "requirements": [{"id": "FR1", "type": "FR"}],
                "capability_contract": {"capabilities": [{"id": "public_ingress"}]},
                "resource_intake": {
                    "valid": False,
                    "questions": [{"field": "budget"}],
                },
                "resource_questions": [
                    {
                        "field": "monthlyBudgetUSD",
                        "kind": "missing",
                        "why": "A budget is required for cost filtering.",
                        "question": "What is the monthly budget?",
                        "choices": [],
                    }
                ],
            }
        )
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["kind"] == "question"
    assert result["message"] == (
        "I refined and classified 1 requirement (1 functional and 0 non-functional). "
        "What is the monthly budget?"
    )
    assert result["resource_question"]["field"] == "monthlyBudgetUSD"
    assert result["review_artifacts"] == ["Refined requirements"]
    assert "Before I continue" not in result["message"]
    assert "Waiting for deployment details." not in result["message"]
    assert not any("가" <= character <= "힣" for character in result["message"])


def test_implementation_progress_snapshot_extracts_active_file_and_class(tmp_path) -> None:
    run_root = tmp_path / "run-001"
    events_dir = run_root / "reports" / "agent-executions"
    events_dir.mkdir(parents=True)
    journal = events_dir / "task-1.attempt-001.events.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "tool": "restricted_file_editor",
                        "event": {
                            "command": "create",
                            "path": "/workspace/application/src/main/java/com/example/OrderController.java",
                        },
                    }
                ),
                json.dumps(
                    {
                        "tool": "finish",
                        "event": {"status": "completed"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "job_id": "job-1",
                "app_id": "app-1",
                "status": "RUNNING",
                "run_root": str(run_root),
            }
        )
    finally:
        service.shutdown()

    assert progress["current_file"].endswith("OrderController.java")
    assert progress["current_class"] == "OrderController"
    assert progress["progress_detail"] == "Editing OrderController.java"


def test_rerun_implementation_creates_a_new_job(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_create_job(app_id, design, base_package, allow_assumptions):
        calls.append(
            {
                "app_id": app_id,
                "design": design,
                "base_package": base_package,
                "allow_assumptions": allow_assumptions,
            }
        )
        return {"job_id": "new-job", "app_id": app_id, "status": "QUEUED"}

    monkeypatch.setattr(workspace_module, "create_job", fake_create_job)
    monkeypatch.setattr(
        workspace_module,
        "artifact_repository",
        SimpleNamespace(load_state=lambda _app_id: {"class_diagram_puml": "A", "api_spec": {"paths": {}}}),
    )

    service = WorkspaceService()
    try:
        result = service._dispatch(
            {
                "action": "rerun_implementation",
                "app_id": "app-1",
                "command_id": "cmd-1",
                "stage": "implementation",
                "payload": {"base_package": "com.example.app", "allow_assumptions": True},
            }
        )
    finally:
        service.shutdown()

    assert calls and calls[0]["app_id"] == "app-1"
    assert result["job"]["job_id"] == "new-job"
