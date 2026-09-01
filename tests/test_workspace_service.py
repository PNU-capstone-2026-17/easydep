from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    assert "workspace_commands" in Base.metadata.tables
    assert "workspace_events" not in Base.metadata.tables
    assert "deployment_preferences" not in Base.metadata.tables
    assert "deployment_preferences" in Base.metadata.tables["apps"].columns


def test_workspace_event_summary_omits_large_llm_contents() -> None:
    row = SimpleNamespace(
        event_id=7,
        app_id="app-1",
        command_id="command-1",
        stage="design",
        kind="progress",
        actor="system",
        text="Design LLM metrics recorded.",
        event_data={
            "progress_event": "designLlmMetrics",
            "analysis_step": "class_diagram",
            "llm_timing_events": [
                {"operation": "ClassInventory", "responseContent": "x" * 1_000_000},
                {"operation": "ClassRepair", "reasoningContent": "y" * 1_000_000},
            ],
        },
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )

    summary = repository.event_dict(row, include_llm_timings=False)

    assert summary["metadata"] == {
        "progress_event": "designLlmMetrics",
        "analysis_step": "class_diagram",
        "llm_timing_count": 2,
    }
    assert len(json.dumps(summary)) < 1000
    assert len(row.event_data["llm_timing_events"]) == 2


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


def test_reconcile_implementation_command_restores_progress_after_restart(monkeypatch) -> None:
    command = {
        "command_id": "command-1",
        "action": "approve_implementation",
        "status": "RUNNING",
        "payload": {"job_id": "job-1"},
    }
    events: list[dict] = []
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {"job_id": "job-1", "status": "RUNNING"},
    )
    monkeypatch.setattr(repository, "list_events", lambda _app_id: [])
    monkeypatch.setattr(repository, "append_event", lambda *args, **kwargs: events.append(kwargs))
    monkeypatch.setattr(
        WorkspaceService,
        "_implementation_progress_snapshot",
        staticmethod(
            lambda _job: {
                "progress_card_label": "구현 진행 상황",
                "updates": [
                    {
                        "step": "phase-backend",
                        "label": "Backend 구현",
                        "status": "running",
                        "detail": "Backend 구현을 진행하고 있습니다.",
                    }
                ],
            }
        ),
    )

    service = WorkspaceService()
    try:
        result = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert result == command
    assert events[0]["metadata"]["step"] == "phase-backend"
    assert events[0]["metadata"]["progress_status"] == "running"


@pytest.mark.parametrize(
    ("command_status", "job_status"),
    [("FAILED", "FAILED"), ("INTERRUPTED", "NEEDS_PLANNER")],
)
def test_reconcile_stopped_implementation_exposes_checkpoint_retry(
    monkeypatch,
    command_status: str,
    job_status: str,
) -> None:
    command = {
        "command_id": "command-1",
        "app_id": "app-1",
        "action": "approve_implementation",
        "stage": "implementation",
        "status": command_status,
        "payload": {"job_id": "job-1"},
        "result": None,
    }
    failed_job = {
        "job_id": "job-1",
        "app_id": "app-1",
        "status": job_status,
        "checkpoint_retryable": True,
    }
    monkeypatch.setattr(repository, "latest_command", lambda _app_id: command)
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: failed_job,
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_sync_implementation_progress",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "update_command",
        lambda _command_id, **changes: {**command, **changes},
    )

    service = WorkspaceService()
    try:
        reconciled = service.reconcile_implementation_command("app-1")
    finally:
        service.shutdown()

    assert reconciled is not None
    assert reconciled["status"] == "FAILED"
    assert reconciled["result"]["job_id"] == "job-1"
    assert reconciled["result"]["checkpoint_retryable"] is True


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
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
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
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
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


def test_legacy_handoff_checkpoint_backfills_and_routes_a_capability_choice(
    monkeypatch,
) -> None:
    captured = {}
    previous = {
        "command_id": "prior",
        "stage": "requirements",
        "status": "AWAITING_INPUT",
        "result": {
            "phase": "requirements_handoff",
            "blocking_findings": [
                {
                    "code": "requirements.capability-contract",
                    "repairable": False,
                }
            ],
        },
    }
    question = {
        "field": "capability:persistent_storage",
        "kind": "choice",
        "question": "Should data survive service restarts?",
        "choices": [{"value": "accepted", "label": "Yes"}],
    }
    monkeypatch.setattr(repository, "get_command", lambda *_args, **_kwargs: previous)
    monkeypatch.setattr(
        artifact_repository,
        "load_state",
        lambda _app_id: {"capability_contract": {"capabilities": []}},
    )
    monkeypatch.setattr(
        workspace_module,
        "capability_resource_questions",
        lambda _contract: [question],
    )

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
    service = WorkspaceService()
    try:
        presented = service.present_command("app-1", previous)
        service._stage_message(
            {
                "command_id": "reply",
                "app_id": "app-1",
                "stage": "requirements",
                "action": "message",
                "payload": {"text": "accepted", "action_id": "prior"},
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert presented["result"]["resource_question"] == question
    assert captured["request"].resource_answers == {
        "capability:persistent_storage": "accepted"
    }
    assert captured["request"].answer is None


def test_initial_workspace_request_accepts_provider_and_region_without_budget(
    monkeypatch,
) -> None:
    captured = {}

    def analyze(request):
        captured["request"] = request
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
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
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
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
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
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
        return {"status": "completed", "saved_stages": []}

    monkeypatch.setattr(workspace_module, "analyze_requirements", analyze)
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
        operation=lambda: {"status": "need_feedback", "validation": {}},
    )

    assert response == {"status": "need_feedback", "validation": {}}
    assert [event["metadata"]["progress_status"] for event in events] == [
        "running",
        "completed",
    ]
    assert events[-1]["metadata"]["progress_card_label"] == "Design generation"

    calls: list[str] = []
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": True,
            "active": True,
            "retryable": True,
            "stage": "sequence_diagram",
        },
    )
    monkeypatch.setattr(
        workspace_module,
        "start_design_session",
        lambda _app_id: calls.append("start") or {"status": "need_feedback"},
    )
    monkeypatch.setattr(
        workspace_module,
        "resume_design_session",
        lambda *_args: calls.append("resume") or {"status": "need_feedback"},
    )
    service = WorkspaceService()
    monkeypatch.setattr(service, "_design_result", lambda response: response)
    try:
        service._stage_message(
            {
                "command_id": "restart-command",
                "app_id": "app-1",
                "action": "start_design",
                "stage": "design",
                "payload": {"text": ""},
            },
            advance=True,
        )
    finally:
        service.shutdown()
    assert calls == ["start"]


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
                "responseContent": '{"Classes": []}',
                "reasoningContent": "empty inventory is enough",
                "schemaValidationErrors": [{"loc": ["Classes"], "type": "too_short"}],
            },
        )
        return {"status": "need_feedback", "validation": {}}

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
    assert metrics["metadata"]["llm_timing_events"][0]["responseContent"] == (
        '{"Classes": []}'
    )
    assert metrics["metadata"]["llm_timing_events"][0]["reasoningContent"] == (
        "empty inventory is enough"
    )
    assert metrics["metadata"]["llm_timing_events"][0]["schemaValidationErrors"] == [
        {"loc": ["Classes"], "type": "too_short"}
    ]


def test_design_operation_publishes_only_the_latest_class_preview(monkeypatch) -> None:
    events = []
    live_previews.clear()
    monkeypatch.setattr(
        "app.workspace.service.render_plantuml",
        lambda _puml, _image_format: b"<svg />",
    )
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
        return {"status": "need_feedback", "validation": {}}

    assert WorkspaceService._run_design_operation(
        {"app_id": "app-1", "command_id": "command-1"},
        stage="class_diagram",
        label="Generating the class diagram",
        operation=operation,
    ) == {"status": "need_feedback", "validation": {}}

    preview = live_previews.get("app-1", "command-1", "class_diagram")
    assert preview is not None
    assert preview.revision == 2
    assert preview.unit == "UC1"
    assert preview.image_svg == b"<svg />"
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
        operation=lambda: {
            "validation": {
                "sequence_diagram": {"findings": ["missing flow step"]}
            }
        },
    )

    assert response["validation"]["sequence_diagram"]["findings"] == [
        "missing flow step"
    ]
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


def test_requirements_handoff_exposes_capability_choices_instead_of_llm_repair() -> None:
    service = WorkspaceService()
    try:
        result = service._requirements_result(
            {
                "status": "need_feedback",
                "phase": "requirements_handoff",
                "blocking_findings": [
                    {
                        "code": "requirements.capability-contract",
                        "stage": "resources",
                        "target_ids": [],
                        "message": "capability contract needs answers for: persistent_storage",
                        "severity": "error",
                        "repairable": False,
                    }
                ],
                "repair_state": {
                    "status": "NEEDS_INPUT",
                    "attempt_count": 2,
                    "accepted_count": 2,
                    "recent_attempts": [],
                },
                "resource_questions": [
                    {
                        "field": "capability:persistent_storage",
                        "kind": "choice",
                        "question": "Should data survive service restarts?",
                        "choices": [
                            {"value": "accepted", "label": "Yes"},
                            {"value": "abstained", "label": "No"},
                        ],
                    }
                ],
            }
        )
    finally:
        service.shutdown()

    assert result["can_delegate_repair"] is False
    assert result["resource_question"]["kind"] == "choice"
    assert result["resource_question"]["choices"][0]["value"] == "accepted"
    assert "Should data survive service restarts?" in result["message"]


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


def test_exhausted_delegated_repair_becomes_an_explicit_manual_gate() -> None:
    result = {
        "message": "Delegate the repair to the LLM.",
        "can_delegate_repair": True,
        "blocking_findings": [
            {
                "code": "design.validation",
                "stage": "api_spec",
                "message": "The same API blocker remains.",
                "repairable": True,
            }
        ],
    }

    workspace_module._close_stalled_delegated_repair(result)

    assert result["can_delegate_repair"] is False
    assert result["blocking_findings"][0]["repairable"] is False
    assert "specific revision request" in result["message"]


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


def test_delegated_repair_stalls_when_it_introduces_more_blockers() -> None:
    previous = {
        "blocking_findings": [
            {"code": "one", "stage": "api_spec", "message": "one"},
        ],
        "repair_state": {"status": "ACTIVE", "recent_attempts": []},
    }
    current = {
        "blocking_findings": [
            {"code": "one", "stage": "api_spec", "message": "one"},
            {"code": "two", "stage": "api_spec", "message": "two"},
        ],
        "repair_state": {"status": "ACTIVE", "recent_attempts": []},
    }

    state = workspace_module._merge_delegated_repair_state(
        previous,
        current,
        strategy_key="delegate:design:api_spec:episode-1",
    )

    assert state["status"] == "STALLED"
    assert state["accepted_count"] == 0
    assert state["recent_attempts"][-1]["outcome"] == "regressed"
    assert state["rejected_candidate_digests"] == [state["finding_digest"]]


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


def test_implementation_progress_snapshot_uses_public_workflow_phases() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "RUNNING",
                "workflow": {
                    "status": "RUNNING",
                    "currentPhase": "persistence",
                    "phases": [
                        {"phaseId": "control", "status": "SUCCEEDED"},
                        {"phaseId": "persistence", "status": "RUNNING"},
                    ],
                    "tasks": [
                        {
                            "taskId": "create-entity",
                            "phase": "persistence",
                            "status": "RUNNING",
                        }
                    ],
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "running"
    assert updates["sub-backend-persistence"]["status"] == "running"


def test_implementation_progress_snapshot_reads_live_workflow_and_current_file(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    reports = run_root / "reports"
    event_dir = reports / "agent-executions"
    event_dir.mkdir(parents=True)
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "currentPhase": "use-cases",
                "phases": [
                    {"phaseId": "persistence", "status": "SUCCEEDED"},
                    {"phaseId": "use-cases", "status": "RUNNING"},
                ],
                "tasks": [
                    {
                        "task_id": "repository-1",
                        "phase": "persistence",
                        "status": "SUCCEEDED",
                    },
                    {
                        "task_id": "use-cases-1",
                        "phase": "use-cases",
                        "status": "RUNNING",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    journal_path = event_dir / "boundary.events.jsonl"
    journal_path.write_text(
        json.dumps(
            {
                "tool": "restricted_file_editor",
                "event": {
                    "action": {
                        "path": str(
                            tmp_path
                            / "agent-workspace"
                            / "application"
                            / "src"
                            / "main"
                            / "java"
                            / "BoundaryAdapter.java"
                        )
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (event_dir / "use-cases-1.result.json").write_text(
        json.dumps(
            {
                "taskId": "use-cases-1",
                "taskType": "use-case",
                "status": "SUCCEEDED",
                "changedFiles": ["application/src/main/java/BoundaryAdapter.java"],
                "verification": {"exitCode": 0},
                "repairHistory": {"attempts": []},
                "eventJournal": "reports/agent-executions/boundary.events.jsonl",
                "rawResponse": "Boundary adapter 구현을 완료했습니다.",
            }
        ),
        encoding="utf-8",
    )

    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "RUNNING",
                "run_root": str(run_root),
                "workflow": {"status": "READY", "tasks": []},
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "running"
    assert updates["sub-backend-use-cases"]["status"] == "running"
    assert updates["implementation-file"]["detail"] == "Editing BoundaryAdapter.java"
    assert progress["current_file"] == (
        "application/src/main/java/BoundaryAdapter.java"
    )
    assert progress["current_class"] == "BoundaryAdapter"
    assert progress["agent_results"] == [
        {
            "task_id": "use-cases-1",
            "task_type": "use-case",
            "status": "SUCCEEDED",
            "raw_response": "Boundary adapter 구현을 완료했습니다.",
            "changed_files": ["application/src/main/java/BoundaryAdapter.java"],
            "verification": {"exitCode": 0},
            "repair_history": {"attempts": []},
            "event_journal": "reports/agent-executions/boundary.events.jsonl",
        }
    ]


def test_implementation_progress_snapshot_marks_terminal_failure() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "FAILED",
                "error": "npm ci timed out",
                "workflow": {
                    "status": "RUNNING",
                    "currentPhase": "persistence",
                    "phases": [{"phaseId": "persistence", "status": "RUNNING"}],
                    "tasks": [],
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["phase-backend"]["status"] == "failed"
    assert updates["implementation-result"]["status"] == "failed"
    assert progress["progress_status"] == "failed"
    assert progress["progress_detail"] == "npm ci timed out"


def test_implementation_progress_snapshot_marks_completed_workflow() -> None:
    service = WorkspaceService()
    try:
        progress = service._implementation_progress_snapshot(
            {
                "status": "COMPLETED",
                "workflow": {
                    "status": "COMPLETE",
                    "currentPhase": "frontend",
                    "phases": [
                        {"phaseId": "control", "status": "SUCCEEDED"},
                        {"phaseId": "frontend", "status": "SUCCEEDED"},
                    ],
                    "tasks": [
                        {
                            "taskId": "control-1",
                            "phase": "control",
                            "status": "SUCCEEDED",
                        },
                        {
                            "taskId": "frontend-1",
                            "phase": "frontend",
                            "status": "SUCCEEDED",
                        },
                    ],
                },
            }
        )
    finally:
        service.shutdown()

    updates = {item["step"]: item for item in progress["updates"]}
    assert updates["release-verification"]["status"] == "completed"
    assert progress["progress_status"] == "completed"


def test_rerun_implementation_creates_a_new_job(monkeypatch) -> None:
    calls: list[dict] = []
    events: list[dict] = []
    command_updates: list[dict] = []

    def fake_create_job(app_id, _design, base_package, allow_assumptions):
        calls.append(
            {
                "app_id": app_id,
                "base_package": base_package,
                "allow_assumptions": allow_assumptions,
            }
        )
        return {"job_id": "new-job", "app_id": app_id, "status": "QUEUED"}

    monkeypatch.setattr(workspace_module.implementation_worker, "create_job", fake_create_job)
    monkeypatch.setattr(
        workspace_module.repository,
        "append_event",
        lambda *args, **kwargs: events.append(kwargs),
    )
    monkeypatch.setattr(
        workspace_module.repository,
        "update_command",
        lambda _command_id, **changes: command_updates.append(changes) or changes,
    )
    monkeypatch.setattr(
        workspace_module,
        "artifact_repository",
        SimpleNamespace(load_state=lambda _app_id: {"class_diagram_puml": "A", "api_spec": {"paths": {}}}),
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_repair_api_contract_before_implementation",
        lambda _self, _app_id, design: design,
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
    assert command_updates[0]["payload"]["job_id"] == "new-job"


def test_retry_implementation_resumes_the_failed_job_checkpoint(monkeypatch) -> None:
    calls: list[str] = []
    events: list[dict] = []
    job = {
        "job_id": "failed-job",
        "app_id": "app-1",
        "status": "QUEUED",
        "checkpoint_retryable": False,
    }
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "get",
        lambda _job_id: {**job, "status": "FAILED", "checkpoint_retryable": True},
    )
    monkeypatch.setattr(
        workspace_module.implementation_worker,
        "retry_failed",
        lambda job_id: calls.append(job_id) or job,
    )
    monkeypatch.setattr(
        workspace_module.repository,
        "append_event",
        lambda *args, **kwargs: events.append(kwargs),
    )
    monkeypatch.setattr(
        WorkspaceService,
        "_monitor_implementation",
        lambda _self, current, command_id=None: {
            "job": current,
            "command_id": command_id,
        },
    )

    service = WorkspaceService()
    try:
        result = service._dispatch(
            {
                "action": "retry_implementation",
                "app_id": "app-1",
                "command_id": "retry-command",
                "stage": "implementation",
                "payload": {
                    "action_id": "failed-command",
                    "job_id": "failed-job",
                },
            }
        )
    finally:
        service.shutdown()

    assert calls == ["failed-job"]
    assert result["job"]["job_id"] == "failed-job"
    assert result["command_id"] == "retry-command"
    assert events[0]["metadata"]["status"] == "CHECKPOINT_RETRY_STARTED"
