from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

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


def test_feedback_option_requires_confirmation_then_revises_requirements(
    monkeypatch,
) -> None:
    """A typed Design question cannot execute its Requirements revision directly."""

    target = RevisionTarget(
        ref="use_case_spec:UC1",
        kind="use_case_spec",
        element_id="UC1",
        owner="requirements",
        artifact_type="USECASE_SPEC",
        artifact_version_id=1,
        display_label="UC1",
    )
    design_entry = RevisionTarget(
        ref="class_diagram:OrderBoundary::approve()",
        kind="operation",
        element_id="operation-1",
        owner="design",
        artifact_type="CLASS",
        artifact_version_id=2,
        display_label="OrderBoundary::approve()",
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

        def design_entry_targets_for_requirements(self, _targets):
            return [design_entry]

    monkeypatch.setattr(workspace_module, "ProjectTools", Tools)
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)
    monkeypatch.setattr(
        WorkspaceService,
        "_attach_revision_execution",
        staticmethod(
            lambda _app_id, _plan, result: {
                **result,
                "revision_execution": {
                    "artifact_versions": {"USECASE_SPEC": 2}
                },
            }
        ),
    )

    requirements_plan = RevisionPlan(
        plan_digest="a" * 64,
        status="needs_confirmation",
        requested_targets=[target],
        authority_targets=[target],
        execution_mode="targeted_revision",
        explanation="Revise the use-case specification.",
        artifact_versions={"USECASE_SPEC": 1},
        trace_digest="b" * 64,
    )
    design_plan = RevisionPlan(
        plan_digest="c" * 64,
        status="needs_confirmation",
        requested_targets=[design_entry],
        authority_targets=[design_entry],
        execution_mode="targeted_revision",
        explanation="Revise the linked Boundary operation.",
        artifact_versions={"CLASS": 2},
        trace_digest="d" * 64,
    )
    monkeypatch.setattr(
        workspace_module,
        "plan_revision",
        lambda _tools, interpretation: (
            requirements_plan
            if interpretation.targets == [target.ref]
            else design_plan
        ),
    )
    revised: list[Any] = []
    monkeypatch.setattr(
        workspace_module,
        "revise_requirements_analysis",
        lambda edit, *_args, **_kw: (
            revised.append(edit)
            or {
                "status": "need_feedback",
                "phase": "specs",
                "use_case_specs": [{"use_case_id": "UC1"}],
            }
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
        assert revised == []
        assert commands[requirement["command_id"]]["status"] == "AWAITING_INPUT"
        assert commands["design-question"]["status"] == "COMPLETED"

        confirmation = service.submit(
            "app-1",
            action="confirm_change",
            payload={"action_id": requirement["command_id"]},
        )
        service._execute_command(confirmation["command_id"], confirmation)
        assert len(revised) == 1
        review = commands[confirmation["command_id"]]
        assert review["status"] == "AWAITING_INPUT"
        assert [item["action"] for item in review["result"]["actions"]] == [
            "message",
            "plan_downstream_revision",
        ]

        downstream = service.submit(
            "app-1",
            action="plan_downstream_revision",
            payload={"action_id": confirmation["command_id"]},
        )
        assert downstream["stage"] == "design"
        service._execute_command(downstream["command_id"], downstream)
        assert review["status"] == "COMPLETED"
        planned = commands[downstream["command_id"]]
        assert planned["status"] == "AWAITING_INPUT"
        assert planned["result"]["action"] == "confirm_change"
        assert [item["action"] for item in list(commands.values())[1:]] == [
            "message",
            "confirm_change",
            "plan_downstream_revision",
        ]
    finally:
        service.shutdown()
