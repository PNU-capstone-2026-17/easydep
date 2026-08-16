from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT, MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Artifact types. Kept as plain strings so later phases (source code, IaC,
# test results) can be added without a schema migration.
TYPE_REFINE_REQ = "REFINE_REQ"
TYPE_CAPABILITY_CONTRACT = "CAPABILITY_CONTRACT"
TYPE_RESOURCE_INTAKE = "RESOURCE_INTAKE"
TYPE_USECASE_SPEC = "USECASE_SPEC"
TYPE_USECASE_DIAGRAM = "USECASE_DIAGRAM"
TYPE_RESOURCE_SPEC = "RESOURCE_SPEC"

TYPE_CLASS = "CLASS"
TYPE_SEQUENCE = "SEQUENCE"
TYPE_API_SPEC = "API_SPEC"
TYPE_ERD = "ERD"
TYPE_DEPLOYMENT = "DEPLOYMENT"

# Implementation and test agents produce file trees.  Their version metadata
# lives in artifact_versions while the immutable files live in artifact_files.
TYPE_SOURCE_CODE = "SOURCE_CODE"
TYPE_FRONTEND_SOURCE_CODE = "FRONTEND_SOURCE_CODE"
TYPE_TEST_CODE = "TEST_CODE"
TYPE_DEPLOYMENT_FILE = "DEPLOYMENT_FILE"
TYPE_IAC_CODE = "IAC_CODE"

FORMAT_PUML = "PUML"
FORMAT_JSON = "JSON"

ORIGIN_GENERATED = "GENERATED"
ORIGIN_AUTO_FIXED = "AUTO_FIXED"
ORIGIN_FEEDBACK_REVISED = "FEEDBACK_REVISED"
# Supplied from outside this service (another agent, or entered by hand).
ORIGIN_IMPORTED = "IMPORTED"


class App(Base):
    """One cloud-native application development session, identified by a UUID."""

    __tablename__ = "apps"

    app_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # What the user typed. Kept because the refined requirements interpret it
    # and cannot be turned back into it, and because regenerating REFINE_REQ
    # after feedback needs the original wording.
    requirements_text: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)
    resource_constraints_text: Mapped[str | None] = mapped_column(
        MEDIUMTEXT, nullable=True
    )
    # 개발 진행 상태: the stage whose artifact was written most recently.
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6)
    )

    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="app",
        cascade="all, delete-orphan",
    )


class Artifact(Base):
    """The current artifact of one type for one app."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("apps.app_id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # The generation lock: NULL when free, otherwise when the lock was taken. A
    # generation that dies without releasing would lock the artifact forever, so
    # the claim is a lease that expires (see artifact_repository.claim_stage).
    generation_started_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), nullable=True
    )
    # Deliberately not a ForeignKey: artifacts and artifact_versions would
    # otherwise reference each other and neither could be inserted first.
    current_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latest_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    app: Mapped[App] = relationship(back_populates="artifacts")
    versions: Mapped[list[ArtifactVersion]] = relationship(
        back_populates="artifact",
        cascade="all, delete-orphan",
        order_by="ArtifactVersion.version_no",
    )

    __table_args__ = (
        UniqueConstraint("app_id", "artifact_type", name="uq_artifacts_app_type"),
    )


class ArtifactVersion(Base):
    """Every revision ever produced for an artifact."""

    __tablename__ = "artifact_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    artifact_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    syntax_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    syntax_errors: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ORIGIN_GENERATED
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6)
    )

    artifact: Mapped[Artifact] = relationship(back_populates="versions")
    files: Mapped[list[ArtifactFile]] = relationship(
        back_populates="artifact_version",
        cascade="all, delete-orphan",
        order_by="ArtifactFile.file_path",
    )

    __table_args__ = (
        UniqueConstraint("artifact_id", "version_no", name="uq_versions_artifact_no"),
    )


class ArtifactFile(Base):
    """One file in an immutable implementation artifact snapshot."""

    __tablename__ = "artifact_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    artifact_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("artifact_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    artifact_version: Mapped[ArtifactVersion] = relationship(back_populates="files")

    __table_args__ = (
        UniqueConstraint(
            "artifact_version_id",
            "file_path",
            name="uq_artifact_files_version_path",
        ),
    )


class DeploymentPreference(Base):
    """User-selected cloud alternatives collected while requirements are analyzed."""

    __tablename__ = "deployment_preferences"

    app_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("apps.app_id", ondelete="CASCADE"),
        primary_key=True,
    )
    selection: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=func.now(6),
        onupdate=func.now(6),
    )


class WorkspaceCommand(Base):
    """One user-visible command executed from the conversational workspace."""

    __tablename__ = "workspace_commands"

    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    app_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("apps.app_id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6)
    )
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    __table_args__ = (
        Index("ix_workspace_commands_app_created", "app_id", "created_at"),
        Index("ix_workspace_commands_app_status", "app_id", "status"),
    )


class WorkspaceEvent(Base):
    """An append-only, display-safe event in an application's workspace timeline."""

    __tablename__ = "workspace_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("apps.app_id", ondelete="CASCADE"), nullable=False
    )
    command_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    event_data: Mapped[Any] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6)
    )

    __table_args__ = (
        Index("ix_workspace_events_app_event", "app_id", "event_id"),
    )
