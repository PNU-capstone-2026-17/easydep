from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.sequence_diagram.projection import project_sequence_model
from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.conversation.contracts import RevisionPlan, RevisionTarget
from app.workspace.conversation.feedback_envelope import (
    DecisionMeaning,
    DecisionPayload,
    DecisionPolicy,
    Question,
    QuestionOption,
)
from app.workspace.service import WorkspaceService
from tests.design_validation_fixtures import CLEAN, CLEAN_STATE


def test_feedback_option_revises_requirements_then_advances_design_to_sequence(
    monkeypatch,
) -> None:
    """A design Question crosses command boundaries; sequence remains a projection."""

    target = RevisionTarget(
        ref="use_case_spec:UC1",
        kind="use_case_spec",
        element_id="UC1",
        owner="requirements",
        artifact_type="USECASE_SPEC",
        artifact_version_id=1,
        display_label="UC1",
    )
    question = Question(
        question_id="design-gap",
        question_version=1,
        app_id="app-1",
        source_execution_id="design-run",
        detected_at={"stage": "design", "artifact_ref": "class_diagram:Order"},
        base_revisions=[{"artifact_type": "USECASE_SPEC", "version_id": 1}],
        trigger={"category": "specification_gap"},
        authority_candidates=[target],
        prompt="Choose the missing UC behavior.",
        decision_policy=DecisionPolicy(
            allowed_semantic_scopes=("behavior",), allowed_change_types=("modify",)
        ),
        options=[
            QuestionOption(
                option_id="approve",
                label="Add approval",
                decision_payload=DecisionPayload(
                    normalized_meaning=DecisionMeaning(
                        semantic_scope="behavior", requested_effect="Add approval."
                    ),
                    authoritative_target_refs=(target.ref,),
                ),
            )
        ],
    )
    commands: dict[str, dict[str, Any]] = {
        "design-question": {
            "command_id": "design-question",
            "app_id": "app-1",
            "action": "start_design",
            "stage": "design",
            "status": "AWAITING_INPUT",
            "payload": {},
            "result": {"feedback_question": question.model_dump(mode="json")},
        }
    }

    def create(command_id: str, app_id: str, action: str, stage: str, payload: dict[str, Any]):
        command = {
            "command_id": command_id,
            "app_id": app_id,
            "action": action,
            "stage": stage,
            "status": "QUEUED",
            "payload": dict(payload),
            "result": {},
        }
        commands[command_id] = command
        return command

    def update(command_id: str, **changes: Any):
        commands[command_id].update(changes)
        return commands[command_id]

    monkeypatch.setattr(repository, "create_command", create)
    monkeypatch.setattr(repository, "get_command", commands.get)
    monkeypatch.setattr(
        repository, "latest_command", lambda _app_id, **_kw: list(commands.values())[-1]
    )
    monkeypatch.setattr(repository, "update_command", update)
    monkeypatch.setattr(repository, "append_event", lambda *_args, **_kw: None)
    monkeypatch.setattr(repository, "now", lambda: datetime.now(UTC).replace(tzinfo=None))
    monkeypatch.setattr(
        workspace_module.artifact_repository, "ensure_app_exists", lambda _app: None
    )

    class Tools:
        def __init__(self, _app_id: str):
            pass

        def current_revision_target(self, _target: RevisionTarget) -> RevisionTarget:
            return target

        def validate_targets(self, _targets: list[dict[str, Any]]) -> dict[str, bool]:
            return {"valid": True}

    monkeypatch.setattr(workspace_module, "ProjectTools", Tools)
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)
    monkeypatch.setattr(
        WorkspaceService,
        "_attach_revision_execution",
        staticmethod(lambda _app_id, _plan, result: result),
    )

    plan = RevisionPlan(
        plan_digest="a" * 64,
        status="ready_local",
        requested_targets=[target],
        authority_targets=[target],
        execution_mode="targeted_revision",
        explanation="Revise the use-case specification.",
        artifact_versions={"USECASE_SPEC": 1},
        trace_digest="b" * 64,
    )
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: plan)
    revised: list[Any] = []
    monkeypatch.setattr(
        workspace_module,
        "revise_requirements_analysis",
        lambda edit, *_args, **_kw: (
            revised.append(edit) or {"status": "completed", "saved_stages": []}
        ),
    )
    monkeypatch.setattr(
        workspace_module,
        "analyze_requirements",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("initial analysis")),
    )

    service = WorkspaceService()
    service._executor.shutdown(wait=False, cancel_futures=True)
    service._executor = SimpleNamespace(
        submit=lambda *_args, **_kw: None,
        shutdown=lambda **_kw: None,
    )
    try:
        requirement = service.submit(
            "app-1",
            action="message",
            payload={
                "action_id": "design-question",
                "feedback_option_id": "approve",
                "text": "Add approval.",
            },
        )
        assert requirement["stage"] == "requirements"
        assert requirement["payload"]["feedback_decision"]["selected_option_id"] == "approve"
        service._execute_command(requirement["command_id"], requirement)
        assert len(revised) == 1
        assert commands["design-question"]["status"] == "COMPLETED"

        # Class review and sequence review are separate design commands.  The resume
        # stub invokes the real deterministic projection, never a sequence generator.
        session = {"stage": "class_diagram", "retryable": False, "active": True}
        monkeypatch.setattr(workspace_module, "session_status", lambda _app: dict(session))
        start_design = service.submit(
            "app-1", action="start_design", payload={"action_id": requirement["command_id"]}
        )
        assert start_design["stage"] == "design"
        monkeypatch.setattr(
            workspace_module,
            "start_design_session",
            lambda app_id: {"app_id": app_id, "status": "need_feedback", "stage": "class_diagram"},
        )
        service._execute_command(start_design["command_id"], start_design)
        assert commands[start_design["command_id"]]["status"] == "AWAITING_INPUT"
        projected: list[dict[str, Any]] = []

        def resume(app_id: str, text: str):
            assert app_id == "app-1" and text == ""
            assert session["stage"] == "class_diagram"
            model = project_sequence_model(
                build_scenario_index(CLEAN_STATE["usecase_spec"]), BCEModel.model_validate(CLEAN)
            ).model_dump(mode="json")
            projected.append(model)
            session["stage"] = "sequence_diagram"
            return {
                "app_id": app_id,
                "status": "need_feedback",
                "stage": "sequence_diagram",
                "sequence_diagram_model": model,
            }

        monkeypatch.setattr(workspace_module, "resume_design_session", resume)
        sequence = service.submit(
            "app-1", action="advance", payload={"action_id": start_design["command_id"]}
        )
        service._execute_command(sequence["command_id"], sequence)
        assert commands[sequence["command_id"]]["status"] == "AWAITING_INPUT"
        assert session["stage"] == "sequence_diagram"
        assert projected and projected[0]["Diagrams"]
        assert [item["action"] for item in list(commands.values())[1:]] == [
            "message",
            "start_design",
            "advance",
        ]
    finally:
        service.shutdown()
