from __future__ import annotations

from unittest.mock import patch

from app.design.service import (
    apply_deployment_topology_decision_session,
    resume_design_session,
)
from app.design.services.deployment_diagram.bundle import (
    hydrate_deployment_diagram_bundle,
)
from app.workspace import repository
from app.workspace import service as workspace_module
from app.workspace.service import WorkspaceService

APP_ID = "00000000-0000-4000-8000-000000000001"


def test_erd_approval_asks_only_before_ambiguous_database_runtime() -> None:
    state = {"refined_requirements": [{"id": "REQ-1"}]}
    with (
        patch("app.design.service.artifact_repository.ensure_app_exists"),
        patch("app.design.service.has_active_session", return_value=True),
        patch(
            "app.design.service.session_status",
            return_value={"active": True, "stage": "erd"},
        ),
        patch("app.design.service.artifact_repository.load_state", return_value=state),
        patch(
            "app.design.service.design_readiness_report",
            return_value={"findings": []},
        ),
        patch(
            "app.design.service.data_execution_mode_decision",
            return_value={
                "status": "needsInput",
                "sourceRefs": ["requirement:REQ-1"],
            },
        ),
        patch("app.design.service.resume_design") as resume,
    ):
        result = resume_design_session(APP_ID)

    assert result["stage"] == "deployment_diagram"
    assert result["resource_question"]["field"] == "dataExecutionMode"
    assert [
        item["value"] for item in result["resource_question"]["choices"]
    ] == ["postgresql-container", "embedded"]
    resume.assert_not_called()


def test_database_runtime_answer_is_a_planning_fact_then_resumes_deployment() -> None:
    synced: dict[str, object] = {}
    state = {
        "refined_requirements": [{"id": "REQ-1"}],
        "deployment_planning_facts": [
            {"id": "keep-me", "kind": "workloadContract"}
        ],
    }
    with (
        patch("app.design.service.artifact_repository.ensure_app_exists"),
        patch("app.design.service.has_active_session", return_value=True),
        patch(
            "app.design.service.session_status",
            return_value={"active": True, "stage": "erd"},
        ),
        patch("app.design.service.artifact_repository.load_state", return_value=state),
        patch(
            "app.design.service.data_execution_mode_decision",
            return_value={
                "status": "needsInput",
                "sourceRefs": ["requirement:REQ-1"],
            },
        ),
        patch(
            "app.design.service.sync_design_state",
            side_effect=lambda _app_id, values: synced.update(values),
        ),
        patch(
            "app.design.service.resume_design",
            return_value={"status": "need_feedback", "stage": "deployment_diagram"},
        ) as resume,
    ):
        result = apply_deployment_topology_decision_session(
            APP_ID, "postgresql-container"
        )

    facts = synced["deployment_planning_facts"]
    assert isinstance(facts, list)
    assert facts[0]["id"] == "keep-me"
    assert facts[1]["kind"] == "dataExecutionMode"
    assert facts[1]["value"] == "postgresql-container"
    assert result["stage"] == "deployment_diagram"
    resume.assert_called_once_with(APP_ID, "")


def test_hydration_restores_only_the_user_data_execution_fact() -> None:
    hydrated = hydrate_deployment_diagram_bundle(
        {
            "schemaVersion": "easydep-deployment-diagram",
            "selectedTarget": None,
            "projections": [],
            "workloadGraph": {},
            "planningFacts": {
                "facts": [
                    {
                        "id": "mode",
                        "kind": "dataExecutionMode",
                        "value": "embedded",
                        "authority": "explicit",
                        "status": "accepted",
                    },
                    {
                        "id": "derived",
                        "kind": "persistentDataRequirement",
                        "authority": "derived",
                        "status": "accepted",
                    },
                ]
            },
        }
    )

    assert hydrated["deployment_planning_facts"] == [
        {
            "id": "mode",
            "kind": "dataExecutionMode",
            "value": "embedded",
            "authority": "explicit",
            "status": "accepted",
        }
    ]


def test_workspace_routes_the_choice_without_treating_it_as_llm_feedback(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {
            "exists": True,
            "active": True,
            "retryable": False,
            "stage": "erd",
        },
    )
    monkeypatch.setattr(
        repository,
        "get_command",
        lambda _command_id: {
            "app_id": APP_ID,
            "stage": "design",
            "result": {"resource_question": {"field": "dataExecutionMode"}},
        },
    )
    monkeypatch.setattr(
        workspace_module,
        "apply_deployment_topology_decision_session",
        lambda app_id, value: calls.append((app_id, value))
        or {"status": "need_feedback", "stage": "deployment_diagram"},
    )
    service = WorkspaceService()
    monkeypatch.setattr(
        service,
        "_run_design_operation",
        lambda _command, *, stage, label, operation: operation(),
    )
    monkeypatch.setattr(service, "_design_result", lambda result: result)
    try:
        result = service._stage_message(
            {
                "command_id": "answer-command",
                "app_id": APP_ID,
                "action": "message",
                "stage": "design",
                "payload": {
                    "action_id": "question-command",
                    "text": "embedded",
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert calls == [(APP_ID, "embedded")]
    assert result["stage"] == "deployment_diagram"


def test_workspace_exposes_the_database_choice_as_a_normal_resource_question() -> None:
    service = WorkspaceService()
    question = {
        "field": "dataExecutionMode",
        "kind": "choice",
        "question": "How should the application run its database?",
        "choices": [{"value": "embedded", "label": "Embedded database"}],
    }
    try:
        result = service._design_result(
            {
                "status": "need_feedback",
                "stage": "deployment_diagram",
                "resource_question": question,
            }
        )
    finally:
        service.shutdown()

    assert result["resource_question"] == question
    assert result["resource_questions"] == [question]
    assert result.get("deployment_configuration_required") is None
