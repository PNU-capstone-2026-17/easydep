"""Round-trip verification for the current MySQL schema and repository wiring.

The check writes one disposable app and checkpoint thread, verifies reads and
cascades, and removes every verification row before returning.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, text

from app.db.models import (
    TYPE_SOURCE_CODE,
    App,
    Artifact,
    ArtifactFile,
    ArtifactVersion,
    WorkspaceCommand,
    WorkspaceEvent,
)
from app.db.session import database_settings, init_db, session_scope
from app.repositories import artifact_repository
from app.requirements.orchestration.persistence import SqlCheckpointSaver
from app.workspace import repository as workspace_repository


def _checkpoint(checkpoint_id: str) -> dict:
    return {
        "v": 1,
        "id": checkpoint_id,
        "ts": "2026-08-31T00:00:00+00:00",
        "channel_values": {"verification": {"ok": True}},
        "channel_versions": {"verification": "1"},
        "versions_seen": {},
    }


def _row_counts() -> dict[str, int]:
    models = (
        App,
        Artifact,
        ArtifactVersion,
        ArtifactFile,
        WorkspaceCommand,
        WorkspaceEvent,
    )
    with session_scope() as session:
        return {
            model.__tablename__: int(
                session.scalar(select(func.count()).select_from(model)) or 0
            )
            for model in models
        }


def _verify_query_plans(
    *, app_id: str, artifact_id: int, version_id: int, thread_id: str
) -> dict[str, dict[str, str | None]]:
    """Confirm that each hot-path query exposes the intended composite index."""

    cases = {
        "latest_command": (
            "EXPLAIN SELECT * FROM workspace_commands "
            "WHERE app_id = :app_id ORDER BY created_at DESC LIMIT 1",
            {"app_id": app_id},
            "ix_workspace_commands_app_created",
        ),
        "event_stream": (
            "EXPLAIN SELECT * FROM workspace_events "
            "WHERE app_id = :app_id AND event_id > 0 "
            "ORDER BY event_id LIMIT 500",
            {"app_id": app_id},
            "ix_workspace_events_app_event",
        ),
        "artifact_version": (
            "EXPLAIN SELECT * FROM artifact_versions "
            "WHERE artifact_id = :artifact_id AND version_no = 2",
            {"artifact_id": artifact_id},
            "uq_versions_artifact_no",
        ),
        "artifact_files": (
            "EXPLAIN SELECT * FROM artifact_files "
            "WHERE artifact_version_id = :version_id ORDER BY file_path",
            {"version_id": version_id},
            "PRIMARY",
        ),
        "checkpoint_blob": (
            "EXPLAIN SELECT * FROM requirements_checkpoint_blobs "
            "WHERE thread_id = :thread_id AND checkpoint_ns = '' "
            "AND (channel, version) IN (('verification', '1'))",
            {"thread_id": thread_id},
            "PRIMARY",
        ),
    }
    plans: dict[str, dict[str, str | None]] = {}
    with session_scope() as session:
        for name, (statement, params, expected) in cases.items():
            rows = session.execute(text(statement), params).mappings().all()
            possible = {
                key
                for row in rows
                for key in str(row.get("possible_keys") or "").split(",")
                if key
            }
            selected = ",".join(
                str(row["key"]) for row in rows if row.get("key") is not None
            ) or None
            assert expected in possible, (name, expected, rows)
            plans[name] = {
                "expected": expected,
                "selected": selected,
            }
    return plans


def main() -> None:
    settings = database_settings()
    print(
        f"connecting to mysql://{settings['user']}@{settings['host']}:"
        f"{settings['port']}/{settings['name']}"
    )
    init_db()

    app_id = artifact_repository.create_app("MySQL schema verification")
    command_id = str(uuid.uuid4())
    saver = SqlCheckpointSaver()
    config = {"configurable": {"thread_id": app_id, "checkpoint_ns": ""}}

    try:
        workspace_repository.create_command(
            command_id,
            app_id,
            "verify_schema",
            "implementation",
            {"verification": True},
        )
        workspace_repository.append_event(
            app_id,
            command_id=command_id,
            stage="implementation",
            kind="verification",
            actor="system",
            text="schema round trip",
        )

        artifact_repository.save_file_snapshot(
            app_id,
            TYPE_SOURCE_CODE,
            {"README.md": "first", "readme.md": "case-sensitive sibling"},
        )
        artifact_repository.save_file_snapshot(
            app_id,
            TYPE_SOURCE_CODE,
            {"README.md": "second", "readme.md": "case-sensitive sibling"},
        )
        snapshot = artifact_repository.load_file_snapshot(app_id, TYPE_SOURCE_CODE)
        history = artifact_repository.list_file_artifact_versions(
            app_id, TYPE_SOURCE_CODE
        )
        assert snapshot is not None
        assert snapshot["version_no"] == 2
        assert snapshot["files"]["README.md"]["content"] == "second"
        assert set(snapshot["files"]) == {"README.md", "readme.md"}
        assert [item["file_count"] for item in history] == [2, 2]

        checkpoint_id = "00000000-0000-6000-8000-000000000001"
        saver.put(
            config,
            _checkpoint(checkpoint_id),
            {"source": "input", "step": 0},
            {"verification": "1"},
        )
        restored = saver.get_tuple(config)
        assert restored is not None
        assert restored.checkpoint["channel_values"]["verification"] == {"ok": True}

        with session_scope() as session:
            artifact_id = session.scalar(
                select(Artifact.id).where(
                    Artifact.app_id == app_id,
                    Artifact.artifact_type == TYPE_SOURCE_CODE,
                )
            )
        assert artifact_id is not None
        plans = _verify_query_plans(
            app_id=app_id,
            artifact_id=artifact_id,
            version_id=int(snapshot["version_id"]),
            thread_id=app_id,
        )

        counts = _row_counts()
        assert counts == {
            "apps": 1,
            "artifacts": 1,
            "artifact_versions": 2,
            "artifact_files": 4,
            "workspace_commands": 1,
            "workspace_events": 1,
        }
        print("round_trip_counts=", counts)
        print("query_plans=", plans)
    finally:
        saver.delete_thread(app_id)
        with session_scope() as session:
            session.execute(delete(App).where(App.app_id == app_id))

    final_counts = _row_counts()
    assert not any(final_counts.values()), final_counts
    print("cleanup_counts=", final_counts)
    print("OK: MySQL schema, repositories, checkpoints, and cascades verified")


if __name__ == "__main__":
    main()
