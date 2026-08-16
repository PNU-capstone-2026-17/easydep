from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.models import App, DeploymentPreference, WorkspaceCommand, WorkspaceEvent
from app.db.session import session_scope

ACTIVE_STATUSES = {"QUEUED", "RUNNING"}
REQUIREMENTS_ARTIFACT_STAGES = {
    "refined_requirements",
    "capability_contract",
    "resource_intake",
    "usecase_spec",
    "usecase_diagram",
    "resource_spec",
}
DESIGN_ARTIFACT_STAGES = {
    "class_diagram",
    "sequence_diagram",
    "api_spec",
    "erd",
    "deployment_diagram",
}


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def workflow_stage(stage: str | None) -> str:
    if stage in REQUIREMENTS_ARTIFACT_STAGES:
        return "requirements"
    if stage in DESIGN_ARTIFACT_STAGES:
        return "design"
    if stage in {"requirements", "design", "implementation", "testing"}:
        return str(stage)
    return "requirements"


def command_dict(row: WorkspaceCommand) -> dict[str, Any]:
    return {
        "command_id": row.command_id,
        "app_id": row.app_id,
        "action": row.action,
        "stage": row.stage,
        "status": row.status,
        "payload": row.payload or {},
        "result": row.result,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def event_dict(row: WorkspaceEvent) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "app_id": row.app_id,
        "command_id": row.command_id,
        "stage": row.stage,
        "kind": row.kind,
        "actor": row.actor,
        "text": row.text,
        "metadata": row.event_data or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_command(
    command_id: str,
    app_id: str,
    action: str,
    stage: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with session_scope() as session:
        active = session.scalar(
            select(WorkspaceCommand).where(
                WorkspaceCommand.app_id == app_id,
                WorkspaceCommand.status.in_(ACTIVE_STATUSES),
            )
        )
        if active is not None:
            raise RuntimeError(f"An active workspace command already exists: {active.command_id}")
        row = WorkspaceCommand(
            command_id=command_id,
            app_id=app_id,
            action=action,
            stage=stage,
            status="QUEUED",
            payload=payload,
        )
        session.add(row)
        session.flush()
        return command_dict(row)


def get_command(command_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(WorkspaceCommand, command_id)
        return command_dict(row) if row is not None else None


def latest_command(
    app_id: str, *, exclude_command_id: str | None = None
) -> dict[str, Any] | None:
    with session_scope() as session:
        query = select(WorkspaceCommand).where(WorkspaceCommand.app_id == app_id)
        if exclude_command_id:
            query = query.where(WorkspaceCommand.command_id != exclude_command_id)
        row = session.scalar(query.order_by(WorkspaceCommand.created_at.desc()))
        return command_dict(row) if row is not None else None


def update_command(command_id: str, **changes: Any) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(WorkspaceCommand, command_id)
        if row is None:
            raise KeyError(command_id)
        for key, value in changes.items():
            setattr(row, key, value)
        session.flush()
        return command_dict(row)


def append_event(
    app_id: str,
    *,
    stage: str,
    kind: str,
    actor: str,
    text: str,
    command_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        row = WorkspaceEvent(
            app_id=app_id,
            command_id=command_id,
            stage=stage,
            kind=kind,
            actor=actor,
            text=text,
            event_data=metadata or {},
        )
        session.add(row)
        session.flush()
        return event_dict(row)


def list_events(app_id: str, *, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(WorkspaceEvent)
            .where(
                WorkspaceEvent.app_id == app_id,
                WorkspaceEvent.event_id > after,
            )
            .order_by(WorkspaceEvent.event_id)
            .limit(limit)
        ).all()
        return [event_dict(row) for row in rows]


def get_app_summary(app_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(App, app_id)
        if row is None:
            raise KeyError(app_id)
        return {
            "app_id": row.app_id,
            "current_stage": workflow_stage(row.current_stage),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def save_deployment_preferences(app_id: str, selection: dict[str, Any]) -> dict[str, Any]:
    """Upsert one versionless intake draft without touching an active command."""
    with session_scope() as session:
        if session.get(App, app_id) is None:
            raise KeyError(app_id)
        row = session.get(DeploymentPreference, app_id)
        if row is None:
            row = DeploymentPreference(app_id=app_id, selection=selection)
            session.add(row)
        else:
            row.selection = selection
        session.flush()
        return dict(row.selection or {})


def get_deployment_preferences(app_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(DeploymentPreference, app_id)
        return dict(row.selection or {}) if row is not None else None


def list_workspace_apps(limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(App).order_by(App.created_at.desc()).limit(limit)
        ).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            command = session.scalar(
                select(WorkspaceCommand)
                .where(WorkspaceCommand.app_id == row.app_id)
                .order_by(WorkspaceCommand.created_at.desc())
            )
            first_line = next(
                (line.strip() for line in (row.requirements_text or "").splitlines() if line.strip()),
                "",
            )
            result.append(
                {
                    "app_id": row.app_id,
                    "title": first_line[:72] or f"EasyDep app {row.app_id[:8]}",
                    "current_stage": (
                        command.stage
                        if command is not None
                        else workflow_stage(row.current_stage)
                    ),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "command": command_dict(command) if command is not None else None,
                }
            )
        return result


def interrupt_unfinished() -> int:
    changed = 0
    with session_scope() as session:
        rows = session.scalars(
            select(WorkspaceCommand).where(WorkspaceCommand.status.in_(ACTIVE_STATUSES))
        ).all()
        for row in rows:
            row.status = "INTERRUPTED"
            row.error = (
                "The server restarted and could not restore the in-flight command. "
                "Resume from a validated checkpoint."
            )
            row.completed_at = now()
            changed += 1
    return changed
