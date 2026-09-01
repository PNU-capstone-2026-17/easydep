"""Database shape tests for keys, integrity constraints, and query indexes."""

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.migrations import migrate_db
from app.db.models import (
    App,
    Artifact,
    ArtifactFile,
    ArtifactVersion,
    Base,
    SchemaMigration,
    WorkspaceCommand,
    WorkspaceEvent,
)
from app.design.session_store import DesignCheckpoint
from app.requirements.orchestration.persistence import RequirementsCheckpoint


def _index_names(model: type) -> set[str]:
    return {str(index.name) for index in model.__table__.indexes}


def _constraint_names(model: type) -> set[str]:
    return {
        str(constraint.name)
        for constraint in model.__table__.constraints
        if constraint.name
    }


def test_artifact_tables_use_natural_snapshot_keys_and_integrity_checks() -> None:
    assert {column.name for column in ArtifactFile.__table__.primary_key} == {
        "artifact_version_id",
        "file_path",
    }
    assert "id" not in ArtifactFile.__table__.columns
    assert ArtifactFile.__table__.c.file_path.type.collation == "utf8mb4_0900_as_cs"
    assert ArtifactFile.__table__.c.sha256.type.collation == "ascii_bin"
    assert {
        "ck_artifact_files_path",
        "ck_artifact_files_sha256",
    } <= _constraint_names(ArtifactFile)
    assert "current_version_id" not in Artifact.__table__.columns
    assert "ck_artifacts_latest_version_nonnegative" in _constraint_names(Artifact)
    assert "ck_versions_version_positive" in _constraint_names(ArtifactVersion)


def test_workspace_indexes_match_global_and_app_scoped_queries() -> None:
    assert "ix_apps_created_at" in _index_names(App)
    assert {
        "ix_workspace_commands_app_created",
        "ix_workspace_commands_app_status",
        "ix_workspace_commands_status",
    } <= _index_names(WorkspaceCommand)
    assert {
        "ix_workspace_events_app_command",
        "ix_workspace_events_app_event",
    } <= _index_names(WorkspaceEvent)
    assert isinstance(WorkspaceEvent.__table__.c.event_id.type, BigInteger)
    assert "fk_workspace_events_command" in _constraint_names(WorkspaceEvent)


def test_checkpoint_global_list_has_a_checkpoint_id_index() -> None:
    assert "ix_requirements_checkpoints_checkpoint_id" in _index_names(
        RequirementsCheckpoint
    )
    assert "ix_design_checkpoints_checkpoint_id" in _index_names(DesignCheckpoint)


def test_schema_revision_table_is_registered_and_non_mysql_is_a_noop() -> None:
    assert SchemaMigration.__tablename__ == "schema_migrations"
    engine = create_engine("sqlite:///:memory:")
    try:
        migrate_db(engine)
    finally:
        engine.dispose()


def test_every_registered_table_and_index_compiles_for_mysql_8() -> None:
    dialect = mysql.dialect()
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect))
