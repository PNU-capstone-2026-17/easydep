from __future__ import annotations

import json
import hashlib
import os
import uuid
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    FORMAT_JSON,
    FORMAT_PUML,
    ORIGIN_GENERATED,
    TYPE_API_SPEC,
    TYPE_CLASS,
    TYPE_DEPLOYMENT,
    TYPE_ERD,
    TYPE_REFINE_REQ,
    TYPE_RESOURCE_SPEC,
    TYPE_SEQUENCE,
    TYPE_USECASE_DIAGRAM,
    TYPE_USECASE_SPEC,
    App,
    Artifact,
    ArtifactVersion,
    ArtifactFile,
)
from app.db.session import session_scope
from app.design.schemas.architecture_state import ArchitectureState
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json


class AppNotFound(Exception):
    """Raised when an app_id has no row in the apps table."""


class StageBusy(Exception):
    """Raised when another request is already generating this artifact."""


# How long a GENERATING claim stays valid. Generation runs several LLM calls
# plus up to three revision retries, so this is well above the worst observed
# runtime; it only exists so a crashed worker cannot lock an artifact forever.
def stage_lock_lease_seconds() -> int:
    return int(os.getenv("STAGE_LOCK_LEASE_SECONDS", "900"))


# Every stage the workflow can persist, and how it maps onto ArchitectureState.
# state_key is what the web response and downstream stages read. A stage may also
# declare a source_key: the structured model that is the real source of truth and
# what actually gets stored, from which state_key is re-derived on load. The class
# diagram works this way — its BCE model (extracted_bce_classes) is stored and the
# PlantUML is a pure projection of it, so feedback edits the model and the diagram
# is re-rendered deterministically instead of being rewritten (and never drifts).
STAGE_ARTIFACTS: dict[str, dict[str, Any]] = {
    "refined_requirements": {
        "artifact_type": TYPE_REFINE_REQ,
        "format": FORMAT_JSON,
        "state_key": "refined_requirements",
        "valid_key": None,
        "errors_key": None,
    },
    "usecase_spec": {
        "artifact_type": TYPE_USECASE_SPEC,
        "format": FORMAT_JSON,
        "state_key": "usecase_spec",
        "valid_key": None,
        "errors_key": None,
    },
    "usecase_diagram": {
        "artifact_type": TYPE_USECASE_DIAGRAM,
        "format": FORMAT_PUML,
        "state_key": "usecase_diagram_puml",
        "valid_key": "usecase_diagram_syntax_valid",
        "errors_key": "usecase_diagram_syntax_errors",
    },
    "resource_spec": {
        "artifact_type": TYPE_RESOURCE_SPEC,
        "format": FORMAT_JSON,
        "state_key": "resource_spec",
        "valid_key": None,
        "errors_key": None,
    },
    "class_diagram": {
        "artifact_type": TYPE_CLASS,
        "format": FORMAT_PUML,
        "state_key": "class_diagram_puml",
        "valid_key": "class_diagram_syntax_valid",
        "errors_key": "class_diagram_syntax_errors",
        # Stored as its BCE model; the PlantUML in state_key is derived from this.
        "source_key": "extracted_bce_classes",
        "source_format": FORMAT_JSON,
    },
    "sequence_diagram": {
        "artifact_type": TYPE_SEQUENCE,
        "format": FORMAT_PUML,
        "state_key": "sequence_diagram_puml",
        "valid_key": "sequence_diagram_syntax_valid",
        "errors_key": "sequence_diagram_syntax_errors",
    },
    "api_spec": {
        "artifact_type": TYPE_API_SPEC,
        "format": FORMAT_JSON,
        "state_key": "api_spec",
        "valid_key": "api_spec_syntax_valid",
        "errors_key": "api_spec_syntax_errors",
    },
    "erd": {
        "artifact_type": TYPE_ERD,
        "format": FORMAT_PUML,
        "state_key": "erd_puml",
        "valid_key": "erd_syntax_valid",
        "errors_key": "erd_syntax_errors",
    },
    "deployment_diagram": {
        "artifact_type": TYPE_DEPLOYMENT,
        "format": FORMAT_PUML,
        "state_key": "deployment_diagram_puml",
        "valid_key": "deployment_diagram_syntax_valid",
        "errors_key": "deployment_diagram_syntax_errors",
    },
}

STAGE_BY_ARTIFACT_TYPE = {
    config["artifact_type"]: stage for stage, config in STAGE_ARTIFACTS.items()
}


def create_app(requirements_text: str = "", resource_constraints_text: str = "") -> str:
    """Issue a new app id and store the inputs the workflow starts from."""
    app_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(
            App(
                app_id=app_id,
                requirements_text=requirements_text,
                resource_constraints_text=resource_constraints_text,
            )
        )
    return app_id


def list_apps(limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(App).order_by(App.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "app_id": row.app_id,
                "current_stage": row.current_stage,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]


def update_inputs(
    app_id: str,
    requirements_text: str | None = None,
    resource_constraints_text: str | None = None,
) -> None:
    with session_scope() as session:
        app = _require_app(session, app_id)
        if requirements_text is not None:
            app.requirements_text = requirements_text
        if resource_constraints_text is not None:
            app.resource_constraints_text = resource_constraints_text


def load_state(app_id: str) -> ArchitectureState:
    """Rebuild the workflow state for an app from its stored artifacts."""
    with session_scope() as session:
        app = _require_app(session, app_id)

        state: ArchitectureState = {
            "app_id": app_id,
            "requirements_text": app.requirements_text or "",
            "resource_constraints_text": app.resource_constraints_text or "",
        }
        artifact_status: dict[str, str] = {}

        artifacts = session.scalars(
            select(Artifact).where(Artifact.app_id == app_id)
        ).all()

        for artifact in artifacts:
            stage = STAGE_BY_ARTIFACT_TYPE.get(artifact.artifact_type)
            if stage is None or artifact.current_version_id is None:
                continue

            config = STAGE_ARTIFACTS[stage]
            version = session.get(ArtifactVersion, artifact.current_version_id)
            if version is None:
                continue

            source_key = config.get("source_key")
            if source_key:
                source_value = _decode_content(version.content, config["source_format"])
                state[source_key] = source_value
                # PlantUML is a pure projection of the stored model.
                state[config["state_key"]] = generate_plantuml_from_bce_json(
                    source_value
                )
            else:
                state[config["state_key"]] = _decode_content(
                    version.content,
                    config["format"],
                )
            if config["valid_key"]:
                state[config["valid_key"]] = version.syntax_valid
            if config["errors_key"]:
                state[config["errors_key"]] = version.syntax_errors or []

            artifact_status[stage] = "implemented"

        state["artifact_status"] = artifact_status
        return state


def save_stage(
    app_id: str,
    stage: str,
    state: ArchitectureState,
    origin: str = ORIGIN_GENERATED,
) -> int | None:
    """Persist a stage result as a new artifact version.

    Returns the new version id, or None when the stage produced no content.
    """
    with session_scope() as session:
        app = _require_app(session, app_id)
        version_id = _write_version(session, app_id, stage, state, origin)

        app.current_stage = stage
        return version_id


def claim_stage(app_id: str, stage: str) -> None:
    """Take the generation lock for one artifact.

    generation_started_at is the lock: NULL means free, a timestamp means held
    since then. Raises StageBusy when another request already holds it. The
    claim is a lease, so a lock left behind by a crashed worker can be taken
    over once stage_lock_lease_seconds has passed.
    """
    config = STAGE_ARTIFACTS[stage]
    with session_scope() as session:
        _require_app(session, app_id)
        artifact = _find_artifact(session, app_id, config["artifact_type"])

        if artifact is None:
            try:
                with session.begin_nested():
                    session.add(
                        Artifact(
                            app_id=app_id,
                            artifact_type=config["artifact_type"],
                            generation_started_at=func.now(6),
                        )
                    )
                return
            except IntegrityError:
                # A concurrent request created the row first; fall through and
                # contend for it through the conditional update below.
                artifact = _find_artifact(session, app_id, config["artifact_type"])
                if artifact is None:
                    raise StageBusy(stage) from None

        expiry = func.date_sub(
            func.now(6),
            text("INTERVAL :lease_seconds SECOND").bindparams(
                lease_seconds=stage_lock_lease_seconds()
            ),
        )
        # Single conditional UPDATE: whoever changes the row wins the lock.
        result = session.execute(
            update(Artifact)
            .where(
                Artifact.id == artifact.id,
                or_(
                    Artifact.generation_started_at.is_(None),
                    Artifact.generation_started_at < expiry,
                ),
            )
            .values(generation_started_at=func.now(6))
        )
        if result.rowcount != 1:
            raise StageBusy(stage)


def release_stage(app_id: str, stage: str) -> None:
    """Release the generation lock.

    A failed run leaves whatever version was already current in place, so there
    is nothing to roll back here.
    """
    config = STAGE_ARTIFACTS[stage]
    with session_scope() as session:
        artifact = _find_artifact(session, app_id, config["artifact_type"])
        if artifact is not None:
            artifact.generation_started_at = None


def list_versions(app_id: str, stage: str) -> list[dict[str, Any]]:
    """Revision history of one artifact, oldest first."""
    config = STAGE_ARTIFACTS[stage]
    with session_scope() as session:
        _require_app(session, app_id)
        artifact = _find_artifact(session, app_id, config["artifact_type"])
        if artifact is None:
            return []

        versions = session.scalars(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id)
            .order_by(ArtifactVersion.version_no)
        ).all()
        return [
            {
                "version_no": version.version_no,
                "origin": version.origin,
                "syntax_valid": version.syntax_valid,
                "syntax_errors": version.syntax_errors or [],
                "is_current": version.id == artifact.current_version_id,
                "created_at": version.created_at.isoformat(),
            }
            for version in versions
        ]


def get_version_content(app_id: str, stage: str, version_no: int) -> Any:
    """Content of one specific revision, or None when it does not exist."""
    config = STAGE_ARTIFACTS[stage]
    with session_scope() as session:
        _require_app(session, app_id)
        artifact = _find_artifact(session, app_id, config["artifact_type"])
        if artifact is None:
            return None

        version = session.scalars(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id == artifact.id,
                ArtifactVersion.version_no == version_no,
            )
        ).first()
        if version is None:
            return None
        return _decode_content(version.content, config["format"])


def save_file_snapshot(
    app_id: str,
    artifact_type: str,
    files: dict[str, str],
    *,
    origin: str = ORIGIN_GENERATED,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Save a whole file tree as one immutable artifact version."""
    if not files:
        raise ValueError("A file artifact snapshot cannot be empty")
    normalized = {_normalize_file_path(path): content for path, content in files.items()}
    with session_scope() as session:
        _require_app(session, app_id)
        artifact = session.scalars(
            select(Artifact)
            .where(Artifact.app_id == app_id, Artifact.artifact_type == artifact_type)
            .with_for_update()
        ).first()
        if artifact is None:
            artifact = Artifact(app_id=app_id, artifact_type=artifact_type)
            session.add(artifact)
            session.flush()

        version = ArtifactVersion(
            artifact_id=artifact.id,
            version_no=artifact.latest_version_no + 1,
            content=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            syntax_valid=True,
            origin=origin,
        )
        session.add(version)
        session.flush()
        for path, content in sorted(normalized.items()):
            session.add(
                ArtifactFile(
                    artifact_version_id=version.id,
                    file_path=path,
                    content=content,
                    sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                )
            )
        artifact.latest_version_no = version.version_no
        artifact.current_version_id = version.id
        return version.id


def load_file_snapshot(app_id: str, artifact_type: str) -> dict[str, Any] | None:
    """Load the current multi-file snapshot without mixing it into design state."""
    with session_scope() as session:
        _require_app(session, app_id)
        artifact = _find_artifact(session, app_id, artifact_type)
        if artifact is None or artifact.current_version_id is None:
            return None
        version = session.get(ArtifactVersion, artifact.current_version_id)
        if version is None:
            return None
        return {
            "artifact_type": artifact_type,
            "version_no": version.version_no,
            "metadata": _safe_json_object(version.content),
            "files": {
                item.file_path: {"content": item.content, "sha256": item.sha256}
                for item in version.files
            },
            "created_at": version.created_at.isoformat(),
        }


def list_file_artifact_versions(app_id: str, artifact_type: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        _require_app(session, app_id)
        artifact = _find_artifact(session, app_id, artifact_type)
        if artifact is None:
            return []
        versions = session.scalars(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id)
            .order_by(ArtifactVersion.version_no)
        ).all()
        return [
            {
                "version_no": version.version_no,
                "file_count": len(version.files),
                "is_current": version.id == artifact.current_version_id,
                "metadata": _safe_json_object(version.content),
                "created_at": version.created_at.isoformat(),
            }
            for version in versions
        ]


def _write_version(
    session: Session,
    app_id: str,
    stage: str,
    state: ArchitectureState,
    origin: str,
) -> int | None:
    config = STAGE_ARTIFACTS[stage]
    source_key = config.get("source_key")
    if source_key:
        # Persist the structured source of truth; state_key is derived from it.
        content = _encode_content(state.get(source_key), config["source_format"])
    else:
        content = _encode_content(state.get(config["state_key"]), config["format"])
    if not content.strip():
        return None

    artifact = _find_artifact(session, app_id, config["artifact_type"])
    if artifact is None:
        artifact = Artifact(app_id=app_id, artifact_type=config["artifact_type"])
        session.add(artifact)
        session.flush()

    version = ArtifactVersion(
        artifact_id=artifact.id,
        version_no=artifact.latest_version_no + 1,
        content=content,
        syntax_valid=(
            state.get(config["valid_key"]) if config["valid_key"] else None
        ),
        syntax_errors=(
            list(state.get(config["errors_key"], []) or [])
            if config["errors_key"]
            else None
        ),
        origin=origin,
    )
    session.add(version)
    session.flush()

    artifact.latest_version_no = version.version_no
    artifact.current_version_id = version.id
    return version.id


def _find_artifact(
    session: Session,
    app_id: str,
    artifact_type: str,
) -> Artifact | None:
    return session.scalars(
        select(Artifact).where(
            Artifact.app_id == app_id,
            Artifact.artifact_type == artifact_type,
        )
    ).first()


def _require_app(session: Session, app_id: str) -> App:
    app = session.get(App, app_id)
    if app is None:
        raise AppNotFound(app_id)
    return app


def _encode_content(value: Any, content_format: str) -> str:
    if value is None:
        return ""
    if content_format == FORMAT_JSON:
        if not value:
            return ""
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _decode_content(content: str, content_format: str) -> Any:
    if content_format != FORMAT_JSON:
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def _normalize_file_path(value: str) -> str:
    path = value.replace("\\", "/").strip("/")
    if not path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"Invalid artifact file path: {value}")
    return path


def _safe_json_object(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        return {}
