from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
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
TYPE_CLASS = "CLASS"
TYPE_SEQUENCE = "SEQUENCE"
TYPE_API_SPEC = "API_SPEC"
TYPE_ERD = "ERD"
TYPE_DEPLOYMENT = "DEPLOYMENT"

FORMAT_PUML = "PUML"
FORMAT_JSON = "JSON"

ORIGIN_GENERATED = "GENERATED"
ORIGIN_AUTO_FIXED = "AUTO_FIXED"
ORIGIN_FEEDBACK_REVISED = "FEEDBACK_REVISED"


class App(Base):
    """One cloud-native application development session, identified by a UUID."""

    __tablename__ = "apps"

    app_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_text: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)
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

    __table_args__ = (
        UniqueConstraint("artifact_id", "version_no", name="uq_versions_artifact_no"),
    )
