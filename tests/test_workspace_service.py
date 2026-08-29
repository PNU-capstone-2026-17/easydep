from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from app.db.models import Base
from app.design import progress as design_progress
from app.design.services.common.structured import record_llm_timing
from app.repositories import artifact_repository
from app.workspace import api as workspace_api
from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.live_preview import LivePreviewStore, live_previews
from app.workspace.service import WorkspaceService


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


def test_reconcile_implementation_command_closes_stale_running_command(monkeypatch) -> None:
    command = {
        "command_id": "command-1",
        "action": "approve_implementation",
        "status": "RUNNING",
        "payload": {"job_id": "job-1"},
    }
    completed_job = {"job_id": "job-1", "status": "COMPLETED"}
    updated = {**command, "status": "COMPLETED"}
    events: list[dict] = []
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(workspace_module.implementation_worker, "get", lambda _job_id: completed_job)
    monkeypatch.setattr(repository, "update_command", lambda *_args, **_kwargs: updated)
    monkeypatch.setattr(repository, "append_event", lambda *args, **kwargs: events.append(kwargs))
    monkeypatch.setattr(
        repository,
        "now",
        lambda: datetime.now(UTC).replace(tzinfo=None),  # noqa: PLW0108
    )

    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result["status"] == "COMPLETED"
    assert events[0]["metadata"]["status"] == "COMPLETED"


def test_reconcile_implementation_command_recovers_interrupted_command(monkeypatch) -> None:
    command = {
        "command_id": "command-1",
        "action": "approve_implementation",
        "status": "INTERRUPTED",
        "payload": {"job_id": "job-1"},
    }
    updated = {**command, "status": "COMPLETED"}
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {"job_id": "job-1", "status": "COMPLETED"},
    )
    monkeypatch.setattr(repository, "update_command", lambda *_args, **_kwargs: updated)
    monkeypatch.setattr(repository, "append_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        repository,
        "now",
        lambda: datetime.now(UTC).replace(tzinfo=None),  # noqa: PLW0108
    )

    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result["status"] == "COMPLETED"


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
            created_at=datetime(2026, 8, 19, 5, 30, 21),  # noqa: DTZ001
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


def test_design_operation_exposes_existing_llm_timing_events(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        repository,
        "append_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    def operation():
        record_llm_timing(
            "ClassInventory",
            status="cache_hit",
            metadata={
                "physicalRequest": False,
                "cacheStatus": "hit",
                "failureContentSha256": "safe-digest",
                "failureContentPrefix": "private response start",
                "failureContentSuffix": "private response end",
            },
        )
        return "done"

    WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="class_diagram",
        label="Generating the class diagram",
        operation=operation,
    )

    metrics = next(
        event
        for event in events
        if event["metadata"].get("progress_event") == "designLlmMetrics"
    )
    assert metrics["metadata"]["llm_timing_events"][0]["operation"] == (
        "ClassInventory"
    )
    assert metrics["metadata"]["llm_timing_events"][0]["cacheStatus"] == "hit"
    assert metrics["metadata"]["llm_timing_events"][0]["failureContentSha256"] == (
        "safe-digest"
    )
    assert "failureContentPrefix" not in metrics["metadata"]["llm_timing_events"][0]
    assert "failureContentSuffix" not in metrics["metadata"]["llm_timing_events"][0]


def test_design_operation_publishes_only_the_latest_class_preview(monkeypatch) -> None:
    events = []
    live_previews.clear()
    monkeypatch.setattr(
        repository,
        "append_event",
        lambda *args, **kwargs: events.append({"app_id": args[0], **kwargs}),
    )

    def operation():
        design_progress.emit_progress(
            "classDiagramSnapshotAccepted",
            puml="@startuml\nclass Course\n@enduml",
            phase="inventory",
            unit="inventory",
            completed=1,
            total=2,
        )
        design_progress.emit_progress(
            "classDiagramSnapshotAccepted",
            puml="@startuml\nclass Course {\n  + find()\n}\n@enduml",
            phase="operations",
            unit="UC1",
            completed=2,
            total=2,
        )
        return "done"

    assert WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="class_diagram",
        label="Generating the class diagram",
        operation=operation,
    ) == "done"

    preview = live_previews.get("app-1", "command-1", "class_diagram")
    assert preview is not None
    assert preview.revision == 2
    assert preview.unit == "UC1"
    preview_events = [
        event for event in events
        if event["metadata"].get("progress_event") == "classDiagramPreviewUpdated"
    ]
    assert [event["metadata"]["preview_revision"] for event in preview_events] == [1, 2]


def test_live_preview_store_isolates_commands_and_invalidates_cached_svg() -> None:
    store = LivePreviewStore()
    first = store.publish(
        app_id="app-1", command_id="command-1", stage="class_diagram",
        puml="@startuml\nclass A\n@enduml", phase="inventory",
    )
    store.cache_svg("app-1", "command-1", "class_diagram", first.revision, b"svg")
    second = store.publish(
        app_id="app-1", command_id="command-1", stage="class_diagram",
        puml="@startuml\nclass B\n@enduml", phase="operations",
    )
    store.publish(
        app_id="app-1", command_id="command-2", stage="class_diagram",
        puml="@startuml\nclass C\n@enduml", phase="inventory",
    )

    assert second.revision == 2
    assert second.image_svg is None
    assert store.get("app-1", "command-2", "class_diagram").revision == 1


def test_class_preview_endpoints_return_and_cache_the_latest_snapshot(monkeypatch) -> None:
    app_id = "11111111-1111-4111-8111-111111111111"
    live_previews.clear()
    preview = live_previews.publish(
        app_id=app_id,
        command_id="command-1",
        stage="class_diagram",
        puml="@startuml\nclass Course\n@enduml",
        phase="inventory",
        unit="inventory",
        completed=1,
        total=3,
    )
    monkeypatch.setattr(
        workspace_api.repository,
        "get_command",
        lambda command_id: {"command_id": command_id, "app_id": app_id},
    )
    renders = []
    monkeypatch.setattr(
        workspace_api,
        "render_plantuml",
        lambda puml, image_format: renders.append((puml, image_format)) or b"<svg />",
    )

    payload = workspace_api.get_class_diagram_preview(app_id, "command-1")
    first_image = workspace_api.get_class_diagram_preview_image(
        app_id, "command-1",
    )
    second_image = workspace_api.get_class_diagram_preview_image(
        app_id, "command-1",
    )

    assert payload == {
        "command_id": "command-1",
        "stage": "class_diagram",
        "revision": preview.revision,
        "phase": "inventory",
        "unit": "inventory",
        "completed": 1,
        "total": 3,
        "puml": "@startuml\nclass Course\n@enduml",
    }
    assert first_image.body == second_image.body == b"<svg />"
    assert renders == [("@startuml\nclass Course\n@enduml", "svg")]


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
    assert result["blocking_findings"][0]["message"] == "missing flow step"
    assert result["can_delegate_repair"] is True
    assert "before continuing" in result["message"]


def test_design_findings_with_a_generated_artifact_still_require_revision() -> None:
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
    assert result["requires_revision"] is True
    assert result["blocking_findings"][0]["message"] == "missing flow step"
    assert result["findings"] == ["missing flow step"]
    assert "before continuing" in result["message"]


def test_design_findings_cannot_be_waived_by_a_persisted_artifact(monkeypatch) -> None:
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

    assert result["requires_revision"] is True
    assert result["blocking_findings"][0]["message"] == "missing flow step"
    assert "before continuing" in result["message"]


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


def test_requirements_handoff_exposes_llm_repair_without_allowing_advance() -> None:
    service = WorkspaceService()
    try:
        result = service._requirements_result(
            {
                "status": "need_feedback",
                "phase": "requirements_handoff",
                "blocking_findings": [
                    {
                        "code": "requirements.specification",
                        "stage": "specs",
                        "target_ids": ["UC1"],
                        "message": "Specification has an unresolved finding.",
                        "severity": "error",
                        "repairable": True,
                    }
                ],
                "repair_state": {
                    "status": "ACTIVE",
                    "attempt_count": 3,
                    "accepted_count": 1,
                    "recent_attempts": [],
                },
            }
        )
    finally:
        service.shutdown()

    assert result["requires_revision"] is True
    assert result["can_delegate_repair"] is True
    assert result["repair_state"]["attempt_count"] == 3


def test_retry_requirements_accepts_only_a_failed_requirements_command(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda _command_id: {
            "command_id": "failed-requirements",
            "app_id": "app-1",
            "stage": "requirements",
            "status": "FAILED",
            "result": None,
        },
    )

    service = WorkspaceService()
    try:
        service._validate_action_reference(
            "app-1",
            "retry_requirements",
            {"action_id": "failed-requirements"},
        )
    finally:
        service.shutdown()


def test_delegated_repair_stalls_when_the_same_blockers_return() -> None:
    blocker = {
        "code": "requirements.specification",
        "stage": "specs",
        "target_ids": ["UC1"],
        "message": "Specification has an unresolved finding.",
        "severity": "error",
        "repairable": True,
    }
    previous = {
        "blocking_findings": [blocker],
        "repair_state": {
            "status": "ACTIVE",
            "attempt_count": 2,
            "accepted_count": 0,
            "recent_attempts": [],
            "tried_strategies": ["targeted_findings"],
            "rejected_candidate_digests": [],
        },
    }
    current = {
        "blocking_findings": [blocker],
        "repair_state": {
            "status": "ACTIVE",
            "attempt_count": 1,
            "accepted_count": 0,
            "recent_attempts": [],
        },
    }

    state = workspace_module._merge_delegated_repair_state(
        previous,
        current,
        strategy_key="delegate:requirements:specs:episode-3",
    )

    assert state["status"] == "STALLED"
    assert state["attempt_count"] == 4
    assert state["recent_attempts"][-1]["outcome"] == "repeated_candidate"
    assert "targeted_findings" in state["tried_strategies"]


def test_delegated_repair_keeps_running_after_blockers_make_progress() -> None:
    previous = {
        "blocking_findings": [
            {"code": "one", "stage": "specs", "message": "one"},
            {"code": "two", "stage": "specs", "message": "two"},
        ],
        "repair_state": {"status": "ACTIVE", "recent_attempts": []},
    }
    current = {
        "blocking_findings": [
            {"code": "two", "stage": "specs", "message": "two"},
        ],
        "repair_state": {"status": "ACTIVE", "recent_attempts": []},
    }

    state = workspace_module._merge_delegated_repair_state(
        previous,
        current,
        strategy_key="delegate:requirements:specs:episode-1",
    )

    assert state["status"] == "ACTIVE"
    assert state["accepted_count"] == 1
    assert state["recent_attempts"][-1]["outcome"] == "improved"


def test_failed_testing_is_an_actionable_repair_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_module,
        "get_testing_job",
        lambda _job_id: {
            "job_id": "testing-1",
            "status": "COMPLETED",
            "implementation_job_id": "implementation-1",
            "result": {
                "passed": False,
                "blocking_findings": [
                    {
                        "code": "testing.dynamic",
                        "stage": "testing",
                        "target_ids": [],
                        "message": "FR1 assertion failed",
                        "severity": "error",
                        "repairable": True,
                    }
                ],
                "repair_state": {
                    "status": "ACTIVE",
                    "attempt_count": 1,
                    "accepted_count": 0,
                    "recent_attempts": [],
                },
            },
        },
    )
    service = WorkspaceService()
    try:
        result = service._monitor_testing({"job_id": "testing-1"})
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["can_delegate_repair"] is True
    assert result["job"]["implementation_job_id"] == "implementation-1"


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


def test_implementation_progress_snapshot_exposes_generation_milestones() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "GENERATING",
                "progress": {
                    "status": "PREPARING_BUILD",
                    "message": "생성된 프로젝트를 준비하고 있습니다.",
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["prepare-job"]["status"] == "running"
    assert updates["validate-input"]["status"] == "completed"
    assert updates["generate-sources"]["status"] == "completed"
    assert updates["prepare-build"] == {
        "step": "prepare-build",
        "label": "빌드 환경 구성",
        "status": "running",
        "detail": "생성된 프로젝트를 준비하고 있습니다.",
    }


def test_implementation_progress_snapshot_exposes_workflow_phase_and_verification(
    tmp_path,
) -> None:
    run_root = tmp_path / "run-001"
    reports = run_root / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "persistence",
                "phases": [
                    {"phaseId": "control", "status": "SUCCEEDED"},
                    {"phaseId": "persistence", "status": "RUNNING"},
                ],
                "tasks": [
                    {"taskId": "implement-control", "phase": "control", "status": "SUCCEEDED"},
                    {"taskId": "create-entity", "phase": "persistence", "status": "RUNNING"},
                ],
                "currentActivity": {
                    "id": "verify-persistence",
                    "label": "Repository 빌드 및 Unit Test",
                    "status": "RUNNING",
                    "detail": "변경된 소스를 빌드하고 Unit Test를 실행하고 있습니다.",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"status": "RUNNING", "run_root": str(run_root)}
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "running"
    assert updates["sub-backend-persistence"]["status"] == "running"
    assert "activity-backend" not in updates


def test_implementation_progress_snapshot_prefers_terminal_job_failure(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "persistence",
                "phases": [{"phaseId": "persistence", "status": "RUNNING"}],
                "tasks": [
                    {
                        "taskId": "persistence-1",
                        "phase": "persistence",
                        "status": "SUCCEEDED",
                    }
                ],
                "currentActivity": {
                    "id": "verify-persistence",
                    "status": "RUNNING",
                },
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "FAILED",
                "run_root": str(run),
                "error": "npm ci timed out",
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "failed"
    assert updates["implementation-result"] == {
        "step": "implementation-result",
        "label": "구현 작업 실패",
        "status": "failed",
        "detail": "npm ci timed out",
    }
    assert progress["progress_status"] == "failed"


def test_implementation_progress_snapshot_nests_backend_tasks_and_hides_duplicate_activity(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "persistence",
                "phases": [
                    {"phaseId": "control", "status": "SUCCEEDED"},
                    {"phaseId": "persistence", "status": "RUNNING"},
                ],
                "tasks": [
                    {"taskId": "control-1", "phase": "control", "status": "SUCCEEDED"},
                    {"taskId": "persistence-1", "phase": "persistence", "status": "RUNNING"},
                ],
                "currentActivity": {"id": "persistence-1", "status": "RUNNING"},
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()

    progress = service._implementation_progress_snapshot(
        {"job_id": "job-1", "run_root": str(run), "status": "RUNNING"}
    )

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "running"
    assert updates["sub-backend-persistence"]["status"] == "running"
    assert "activity-backend" not in updates


def test_implementation_progress_snapshot_hides_aggregate_backend_activity_duplicate(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "persistence",
                "phases": [{"phaseId": "persistence", "status": "RUNNING"}],
                "tasks": [
                    {"taskId": "persistence-1", "phase": "persistence", "status": "RUNNING"}
                ],
                "currentActivity": {
                    "id": "verify-backend",
                    "status": "RUNNING",
                    "detail": "Running backend verification",
                },
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"run_root": str(run), "status": "RUNNING"}
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "running"
    assert "activity-backend" not in updates


def test_implementation_progress_snapshot_hides_between_phase_completion_audit(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "api-adapters",
                "phases": [
                    {"phaseId": "control", "status": "SUCCEEDED"},
                    {"phaseId": "api-adapters", "status": "RUNNING"},
                ],
                "tasks": [
                    {"taskId": "control-1", "phase": "control", "status": "SUCCEEDED"},
                    {"taskId": "api-adapter-1", "phase": "api-adapters", "status": "RUNNING"},
                ],
                "currentActivity": {
                    "id": "completion-audit",
                    "status": "RUNNING",
                    "detail": "Checking the completed Control phase",
                },
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"run_root": str(run), "status": "RUNNING"}
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "running"
    assert "activity-implementation" not in updates


def test_implementation_progress_snapshot_deduplicates_activity_label_suffix(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "api-adapters",
                "phases": [{"phaseId": "api-adapters", "status": "RUNNING"}],
                "tasks": [
                    {"taskId": "api-adapter-1", "phase": "api-adapters", "status": "RUNNING"}
                ],
                "currentActivity": {
                    "id": "release-verification",
                    "status": "RUNNING",
                    "detail": "Checking the implementation",
                },
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"run_root": str(run), "status": "RUNNING"}
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["activity-implementation"]["label"] == "Backend 구현 결과 확인"


def test_implementation_progress_snapshot_collapses_backend_tasks_by_phase(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "persistence",
                "phases": [{"phaseId": "persistence", "status": "RUNNING"}],
                "tasks": [
                    {"taskId": "entity-1", "phase": "persistence", "status": "SUCCEEDED"},
                    {"taskId": "repository-1", "phase": "persistence", "status": "RUNNING"},
                ],
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"run_root": str(run), "status": "RUNNING"}
        )
    finally:
        service.shutdown()

    subtasks = [item for item in progress["updates"] if item["step"].startswith("sub-backend-")]
    assert [item["step"] for item in subtasks] == ["sub-backend-persistence"]
    assert subtasks[0]["status"] == "running"


def test_implementation_progress_snapshot_hides_completed_repository_when_wiring_runs(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "wiring",
                "phases": [
                    {"phaseId": "persistence", "status": "SUCCEEDED"},
                    {"phaseId": "wiring", "status": "RUNNING"},
                ],
                "tasks": [
                    {"taskId": "repository-1", "phase": "persistence", "status": "SUCCEEDED"},
                    {"taskId": "wiring-1", "phase": "wiring", "status": "RUNNING"},
                ],
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"run_root": str(run), "status": "RUNNING"}
        )
    finally:
        service.shutdown()

    subtasks = [item for item in progress["updates"] if item["step"].startswith("sub-backend-")]
    assert [item["step"] for item in subtasks] == ["sub-backend-wiring"]


def test_implementation_progress_snapshot_keeps_backend_parent_after_backend_finishes(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "frontend",
                "phases": [
                    {"phaseId": "control", "status": "SUCCEEDED"},
                    {"phaseId": "persistence", "status": "SUCCEEDED"},
                    {"phaseId": "api-adapters", "status": "SUCCEEDED"},
                    {"phaseId": "boundary-adapters", "status": "SUCCEEDED"},
                    {"phaseId": "outbound-adapters", "status": "UNPLANNED"},
                    {"phaseId": "wiring", "status": "SUCCEEDED"},
                    {"phaseId": "frontend", "status": "RUNNING"},
                ],
                "tasks": [
                    {"taskId": "control-1", "phase": "control", "status": "SUCCEEDED"},
                    {"taskId": "frontend-1", "phase": "frontend", "status": "RUNNING"},
                ],
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"run_root": str(run), "status": "RUNNING"}
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "completed"
    assert not [item for item in progress["updates"] if item["step"].startswith("sub-backend-")]


def test_implementation_progress_snapshot_treats_completed_task_alias_as_terminal(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "frontend",
                "phases": [
                    {"phaseId": "persistence", "status": "COMPLETED"},
                    {"phaseId": "control", "status": "COMPLETED"},
                    {"phaseId": "api-adapters", "status": "COMPLETED"},
                    {"phaseId": "boundary-adapters", "status": "COMPLETED"},
                    {"phaseId": "outbound-adapters", "status": "UNPLANNED"},
                    {"phaseId": "wiring", "status": "COMPLETED"},
                    {"phaseId": "frontend", "status": "RUNNING"},
                ],
                "tasks": [
                    {"taskId": "persistence-1", "phase": "persistence", "status": "COMPLETED"},
                    {"taskId": "control-1", "phase": "control", "status": "COMPLETED"},
                ],
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot({"run_root": str(run), "status": "RUNNING"})
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "completed"
    assert not [item for item in progress["updates"] if item["step"].startswith("sub-backend-")]


def test_implementation_progress_snapshot_hides_pending_backend_tasks(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "persistence",
                "phases": [{"phaseId": "persistence", "status": "RUNNING"}],
                "tasks": [
                    {"taskId": "queued", "phase": "persistence", "status": "PENDING"},
                    {"taskId": "active", "phase": "persistence", "status": "RUNNING"},
                ],
            }
        ),
        encoding="utf-8",
    )
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"run_root": str(run), "status": "RUNNING"}
        )
    finally:
        service.shutdown()

    subtasks = [item for item in progress["updates"] if item["step"].startswith("sub-backend-")]
    assert [item["step"] for item in subtasks] == ["sub-backend-persistence"]


def test_implementation_progress_snapshot_keeps_preparation_parent_active_during_generation(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    reports = run / "reports"
    reports.mkdir(parents=True)
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "run_root": str(run),
                "status": "GENERATING_SOURCES",
                "progress": {"status": "GENERATING_SOURCES", "message": "Generating sources"},
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["prepare-job"]["status"] == "running"
    assert updates["generate-sources"]["status"] == "running"


def test_implementation_progress_snapshot_closes_release_verification_for_drained_ready_workflow(
    tmp_path,
) -> None:
    run_root = tmp_path / "run-001"
    reports = run_root / "reports"
    reports.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "READY",
                "currentPhase": "outbound-adapters",
                "phases": [
                    {"phaseId": "persistence", "status": "SUCCEEDED"},
                    {"phaseId": "outbound-adapters", "status": "UNPLANNED"},
                ],
                "tasks": [{"taskId": "task-1", "phase": "persistence", "status": "SUCCEEDED"}],
                "nextRunnableTasks": [],
                "blockingReason": None,
                "currentActivity": {
                    "id": "release-verification",
                    "label": "최종 릴리스 검증",
                    "status": "RUNNING",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"status": "COMPLETED", "run_root": str(run_root)}
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["release-verification"]["status"] == "completed"


def test_implementation_progress_snapshot_does_not_reopen_completed_file_activity(
    tmp_path,
) -> None:
    run_root = tmp_path / "run-001"
    reports = run_root / "reports"
    events = reports / "agent-executions"
    events.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "READY",
                "phases": [
                    {"phaseId": "persistence", "status": "SUCCEEDED"},
                    {"phaseId": "outbound-adapters", "status": "UNPLANNED"},
                ],
                "tasks": [{"taskId": "task-1", "phase": "persistence", "status": "SUCCEEDED"}],
                "nextRunnableTasks": [],
                "blockingReason": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (events / "task.events.jsonl").write_text(
        json.dumps(
            {"tool": "restricted_file_editor", "event": {"path": "application/src/Main.java"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {"status": "COMPLETED", "run_root": str(run_root)}
        )
    finally:
        service.shutdown()

    assert "current_file" not in progress
    assert progress["progress_status"] == "completed"


def test_rerun_implementation_creates_a_new_job(monkeypatch) -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def fake_create_job(app_id, request):
        calls.append(
            {
                "app_id": app_id,
                "base_package": request.base_package,
                "allow_assumptions": request.allow_assumptions,
            }
        )
        return {"job_id": "new-job", "app_id": app_id, "status": "QUEUED"}

    monkeypatch.setattr(workspace_module, "create_job", fake_create_job)
    monkeypatch.setattr(
        workspace_module.repository,
        "append_event",
        lambda *args, **kwargs: events.append(kwargs),
    )
    monkeypatch.setattr(
        workspace_module,
        "artifact_repository",
        SimpleNamespace(load_state=lambda _app_id: {"class_diagram_puml": "A", "api_spec": {"paths": {}}}),
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_monitor_implementation",
        lambda _self, job, command_id=None: {"job": job},
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
    assert events and events[0]["metadata"]["reset_implementation_timeline"] is True
