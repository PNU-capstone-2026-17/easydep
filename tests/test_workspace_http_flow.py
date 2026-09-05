from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.workspace import api as workspace_api
from app.workspace.live_preview import live_previews

APP_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def client() -> Iterator[TestClient]:
    application = FastAPI()
    application.include_router(workspace_api.router)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_live_previews() -> Iterator[None]:
    live_previews.clear()
    yield
    live_previews.clear()


def test_frontend_can_create_read_and_advance_a_workspace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []

    def submit(
        app_id: str,
        *,
        action: str,
        payload: dict[str, Any],
        stage: str | None = None,
    ) -> dict[str, Any]:
        command = {
            "command_id": f"command-{len(commands) + 1}",
            "app_id": app_id,
            "action": action,
            "stage": stage or "design",
            "status": "QUEUED",
            "payload": payload,
            "result": None,
        }
        submitted.append({"app_id": app_id, "action": action, "payload": payload})
        commands.append(command)
        return command

    monkeypatch.setattr(
        workspace_api.artifact_repository, "create_app", lambda **_kwargs: APP_ID
    )
    monkeypatch.setattr(workspace_api.workspace_service, "submit", submit)
    monkeypatch.setattr(
        workspace_api.workspace_service,
        "reconcile_implementation_command",
        lambda _app_id: None,
    )
    monkeypatch.setattr(workspace_api, "require_app", lambda _app_id: {})
    monkeypatch.setattr(
        workspace_api,
        "to_web_response",
        lambda _state: {
            "artifacts": {"refined_requirements": {"items": []}},
            "artifact_status": {"refined_requirements": "ready"},
            "validation": {
                "refined_requirements": {
                    "valid": True,
                    "errors": [],
                    "findings": [],
                }
            },
        },
    )
    monkeypatch.setattr(
        workspace_api.repository,
        "get_app_summary",
        lambda _app_id: {"current_stage": "requirements"},
    )
    monkeypatch.setattr(
        workspace_api.repository,
        "latest_command",
        lambda _app_id: commands[-1] if commands else None,
    )
    monkeypatch.setattr(
        workspace_api.repository, "list_events", lambda _app_id, **_kwargs: []
    )
    monkeypatch.setattr(
        workspace_api.repository, "get_deployment_preferences", lambda _app_id: None
    )

    created = client.post(
        "/api/workspace/apps", json={"message": "수강 신청 서비스를 만들어 주세요."}
    )
    snapshot = client.get(f"/api/workspace/apps/{APP_ID}")
    advanced = client.post(
        f"/api/workspace/apps/{APP_ID}/commands",
        json={"action": "start_design", "action_id": "command-1"},
    )

    assert created.status_code == 202
    assert created.json()["app_id"] == APP_ID
    assert created.json()["command"]["status"] == "QUEUED"
    assert snapshot.status_code == 200
    assert snapshot.json()["current_stage"] == "requirements"
    assert snapshot.json()["artifacts"]["refined_requirements"] == {
        "available": True,
        "status": "ready",
        "validation": {
            "valid": True,
            "errors": [],
            "findings": [],
        },
    }
    assert advanced.status_code == 202
    assert advanced.json()["command"]["action"] == "start_design"
    assert submitted[1]["payload"]["action_id"] == "command-1"
    assert [call["action"] for call in submitted] == ["message", "start_design"]


def test_deployment_sizing_apply_checks_preview_and_completes_workspace_wait(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    def apply(app_id, target_id, selections, structure_digest):
        calls["apply"] = (app_id, target_id, selections, structure_digest)
        return {"status": "completed"}

    monkeypatch.setattr(workspace_api, "apply_deployment_sizing_session", apply)
    monkeypatch.setattr(
        workspace_api.workspace_service,
        "sync_deployment_configuration",
        lambda app_id, result: calls.update(complete=(app_id, result)),
    )

    response = client.put(
        f"/api/workspace/apps/{APP_ID}/deployment-sizing",
        json={
            "targetId": "aws:ap-northeast-2",
            "structureDigest": "preview-digest",
            "selections": [
                {
                    "computeUnitId": "compute-1",
                    "sku": "t3.small",
                    "replicaCount": 1,
                    "replicationConfirmed": False,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert calls["apply"][1:] == (
        "aws:ap-northeast-2",
        [
            {
                "computeUnitId": "compute-1",
                "sku": "t3.small",
                "replicaCount": 1,
                "replicationConfirmed": False,
            }
        ],
        "preview-digest",
    )
    assert calls["complete"] == (APP_ID, {"status": "completed"})


def test_llm_timing_details_are_loaded_one_page_at_a_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def get_page(app_id: str, event_id: int, *, offset: int, limit: int):
        calls.append(
            {"app_id": app_id, "event_id": event_id, "offset": offset, "limit": limit}
        )
        return {
            "event_id": event_id,
            "total": 1240,
            "offset": offset,
            "timings": [{"operation": "ClassInventory", "responseContent": "{}"}],
        }

    monkeypatch.setattr(workspace_api.repository, "get_event_llm_timings", get_page)

    response = client.get(
        f"/api/workspace/apps/{APP_ID}/events/3773/llm-timings?offset=20&limit=20"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1240
    assert response.json()["timings"][0]["operation"] == "ClassInventory"
    assert calls == [
        {"app_id": APP_ID, "event_id": 3773, "offset": 20, "limit": 20}
    ]


def test_repair_and_retry_commands_reach_the_workspace_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted: list[dict[str, Any]] = []

    def submit(
        app_id: str, *, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        submitted.append({"app_id": app_id, "action": action, "payload": payload})
        return {
            "command_id": f"command-{len(submitted)}",
            "app_id": app_id,
            "action": action,
            "stage": "design",
            "status": "QUEUED",
            "payload": payload,
            "result": None,
        }

    monkeypatch.setattr(workspace_api.workspace_service, "submit", submit)

    repair = client.post(
        f"/api/workspace/apps/{APP_ID}/commands",
        json={"action": "delegate_repair", "action_id": "review-command"},
    )
    retry = client.post(
        f"/api/workspace/apps/{APP_ID}/commands",
        json={"action": "retry_design", "action_id": "failed-command"},
    )

    assert repair.status_code == retry.status_code == 202
    assert [call["action"] for call in submitted] == ["delegate_repair", "retry_design"]
    assert submitted[0]["payload"]["action_id"] == "review-command"
    assert submitted[1]["payload"]["action_id"] == "failed-command"


def test_class_preview_is_readable_while_its_command_is_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_previews.publish(
        app_id=APP_ID,
        command_id="running-command",
        stage="class_diagram",
        puml="@startuml\nclass Course\n@enduml",
        phase="operations",
        unit="course-operations",
        completed=2,
        total=5,
    )
    monkeypatch.setattr(
        workspace_api.repository,
        "get_command",
        lambda _command_id: {
            "command_id": "running-command",
            "app_id": APP_ID,
            "stage": "design",
            "status": "RUNNING",
        },
    )

    response = client.get(
        f"/api/workspace/apps/{APP_ID}/commands/running-command/previews/class_diagram"
    )

    assert response.status_code == 200
    preview = response.json()
    assert preview["command_id"] == "running-command"
    assert preview["stage"] == "class_diagram"
    assert isinstance(preview["revision"], int)
    assert preview["completed"] == 2
    assert preview["total"] == 5
    assert preview["puml"].startswith("@startuml")
