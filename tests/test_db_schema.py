"""Database shape tests for the reset-and-recreate seven-table schema."""

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.models import (
    AgentCheckpoint,
    AgentCheckpointBlob,
    AgentCheckpointWrite,
    App,
    ArtifactFile,
    ArtifactVersion,
    Base,
    WorkspaceCommand,
)
from app.db.models import (
    TestingJob as DbTestingJob,
)


def _index_names(model: type) -> set[str]:
    return {str(index.name) for index in model.__table__.indexes}


def _constraint_names(model: type) -> set[str]:
    return {str(constraint.name) for constraint in model.__table__.constraints if constraint.name}


def test_only_eight_clear_persistence_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "apps",
        "artifact_versions",
        "artifact_files",
        "workspace_commands",
        "testing_jobs",
        "agent_checkpoints",
        "agent_checkpoint_blobs",
        "agent_checkpoint_writes",
    }


def test_artifact_versions_are_addressed_directly_by_app_type_and_number() -> None:
    assert "artifact_id" not in ArtifactVersion.__table__.columns
    assert {
        "app_id",
        "artifact_type",
        "version_no",
    } <= {column.name for column in ArtifactVersion.__table__.columns}
    assert "uq_artifact_versions_app_type_no" in _constraint_names(ArtifactVersion)
    assert "ck_versions_version_positive" in _constraint_names(ArtifactVersion)
    assert {column.name for column in ArtifactFile.__table__.primary_key} == {
        "artifact_version_id",
        "file_path",
    }
    assert ArtifactFile.__table__.c.file_path.type.collation == "utf8mb4_0900_as_cs"
    assert ArtifactFile.__table__.c.sha256.type.collation == "ascii_bin"


def test_app_holds_single_row_configuration_and_workspace_indexes() -> None:
    assert "deployment_preferences" in App.__table__.columns
    assert "requirements_gated" in App.__table__.columns
    assert "ix_apps_created_at" in _index_names(App)
    assert {
        "ix_workspace_commands_app_created",
        "ix_workspace_commands_app_status",
        "ix_workspace_commands_status",
    } <= _index_names(WorkspaceCommand)


def test_testing_jobs_keep_resume_fields_and_lookup_indexes() -> None:
    assert {
        "job_id",
        "app_id",
        "implementation_job_id",
        "status",
        "current_node",
        "testing_input",
        "result",
        "repair_history",
        "previous_findings",
    } <= {column.name for column in DbTestingJob.__table__.columns}
    assert {
        "ix_testing_jobs_app_created",
        "ix_testing_jobs_implementation",
        "ix_testing_jobs_status",
    } <= _index_names(DbTestingJob)


def test_agent_tables_share_a_graph_scoped_keyspace() -> None:
    assert next(column.name for column in AgentCheckpoint.__table__.primary_key) == "graph_type"
    assert next(column.name for column in AgentCheckpointBlob.__table__.primary_key) == (
        "graph_type"
    )
    assert next(column.name for column in AgentCheckpointWrite.__table__.primary_key) == (
        "graph_type"
    )
    assert "ix_agent_checkpoints_graph_checkpoint" in _index_names(AgentCheckpoint)


def test_every_registered_table_and_index_compiles_for_mysql_8() -> None:
    dialect = mysql.dialect()
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect))
