from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    FORMAT_JSON,
    FORMAT_PUML,
    ORIGIN_GENERATED,
    TYPE_API_SPEC,
    TYPE_CAPABILITY_CONTRACT,
    TYPE_CLASS,
    TYPE_DEPLOYMENT,
    TYPE_ERD,
    TYPE_REFINE_REQ,
    TYPE_RESOURCE_INTAKE,
    TYPE_RESOURCE_SPEC,
    TYPE_SEQUENCE,
    TYPE_USECASE_DIAGRAM,
    TYPE_USECASE_SPEC,
    App,
    Artifact,
    ArtifactFile,
    ArtifactVersion,
)
from app.db.session import session_scope
from app.design.schemas.architecture_state import ArchitectureState
from app.design.services.api_spec.openapi import build_openapi_from_model
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.deployment_diagram.bundle import (
    hydrate_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.erd.plantuml import generate_erd_from_bce_json
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.validation import rehydrated_check_state


class AppNotFound(Exception):
    """Raised when an app_id has no row in the apps table."""


# Every stage the workflow can persist, and how it maps onto ArchitectureState.
# state_key is what the web response and downstream stages read. A stage may also
# declare a source_key: the structured model that is the real source of truth and
# what actually gets stored, from which state_key is re-derived on load through the
# stage's `derive` function.
#
# All five design artifacts work this way. The LLM only ever produces and edits the
# structured model; the diagram or OpenAPI document is a pure projection of it. So
# feedback edits the model, the artifact is re-rendered deterministically, and the
# two cannot drift apart. The requirements-analysis stages have no such model —
# they are stored as they arrive.
STAGE_ARTIFACTS: dict[str, dict[str, Any]] = {
    "refined_requirements": {
        "artifact_type": TYPE_REFINE_REQ,
        "format": FORMAT_JSON,
        "state_key": "refined_requirements",
        "valid_key": None,
        "errors_key": None,
    },
    "capability_contract": {
        "artifact_type": TYPE_CAPABILITY_CONTRACT,
        "format": FORMAT_JSON,
        "state_key": "capability_contract",
        "valid_key": None,
        "errors_key": None,
    },
    "resource_intake": {
        "artifact_type": TYPE_RESOURCE_INTAKE,
        "format": FORMAT_JSON,
        "state_key": "resource_intake",
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
        # 결정론 규칙 검사 결과(`app/design/knowledge/`). 문법 검증과 다른 질문이라
        # 칸이 따로 있다 — 문법은 렌더러가 보장하고, 이건 아무도 보장하지 않는다.
        # 이 키가 없는 스테이지는 검사할 규칙이 아직 없다는 뜻이다(빈 결과가 아니라).
        "check_key": "class_diagram_check",
        # Stored as its BCE model; the PlantUML in state_key is derived from this.
        "source_key": "extracted_bce_classes",
        "source_format": FORMAT_JSON,
        "derive": generate_plantuml_from_bce_json,
    },
    "sequence_diagram": {
        "artifact_type": TYPE_SEQUENCE,
        "format": FORMAT_PUML,
        "state_key": "sequence_diagram_puml",
        "valid_key": "sequence_diagram_syntax_valid",
        "errors_key": "sequence_diagram_syntax_errors",
        "check_key": "sequence_diagram_check",
        # Stored as its interaction model; the PlantUML is derived from this.
        "source_key": "sequence_diagram_model",
        "source_format": FORMAT_JSON,
        "derive": generate_sequence_from_model,
    },
    "api_spec": {
        "artifact_type": TYPE_API_SPEC,
        "format": FORMAT_JSON,
        "state_key": "api_spec",
        "valid_key": "api_spec_syntax_valid",
        "errors_key": "api_spec_syntax_errors",
        "check_key": "api_spec_check",
        # Stored as its endpoint model; the OpenAPI document is assembled from this.
        "source_key": "api_spec_model",
        "source_format": FORMAT_JSON,
        "derive": build_openapi_from_model,
    },
    "erd": {
        "artifact_type": TYPE_ERD,
        "format": FORMAT_PUML,
        "state_key": "erd_puml",
        "valid_key": "erd_syntax_valid",
        "errors_key": "erd_syntax_errors",
        # Rule check over the BCE model and the data model mapped from it.
        "check_key": "erd_check",
        # Stored as its own BCE entity copy; the PlantUML in state_key is derived
        # from this, so ERD feedback edits the model, not the diagram text.
        "source_key": "erd_bce_classes",
        "source_format": FORMAT_JSON,
        "derive": generate_erd_from_bce_json,
    },
    "deployment_diagram": {
        "artifact_type": TYPE_DEPLOYMENT,
        "format": FORMAT_PUML,
        "state_key": "deployment_diagram_puml",
        "valid_key": "deployment_diagram_syntax_valid",
        "errors_key": "deployment_diagram_syntax_errors",
        # Store the editable logical model together with the deterministic provider
        # projection.  Both runtime and provisioning views are derived from this
        # one bundle, so refresh cannot fall back to the old logical-only picture.
        "source_key": "deployment_diagram_bundle",
        "source_format": FORMAT_JSON,
        "derive_state": lambda bundle: {
            "deployment_diagram_puml": deployment_bundle_runtime_puml(bundle),
            "deployment_diagram_provisioning_puml": (
                deployment_bundle_provisioning_puml(bundle)
            ),
        },
        "hydrate": hydrate_deployment_diagram_bundle,
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


def ensure_app_exists(app_id: str) -> None:
    """Raise AppNotFound unless the app row exists. One row read, nothing built.

    For callers that only need the 404 check. load_state() answers the same
    question, but on the way it reads every artifact and re-renders every diagram
    from its model — wasted work when the result is thrown away.
    """
    with session_scope() as session:
        _require_app(session, app_id)


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
                if config.get("hydrate"):
                    state.update(config["hydrate"](source_value))
                else:
                    state[source_key] = source_value
                if config.get("derive_state"):
                    state.update(config["derive_state"](source_value))
                else:
                    # PlantUML is a pure projection of the stored model.
                    state[config["state_key"]] = config["derive"](source_value)
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
        # Check reports are derived evidence, not a second source of truth.
        # Rebuild them from stored models so a page refresh cannot turn an
        # unresolved mismatch into a deceptively clean implementation hand-off.
        state.update(rehydrated_check_state(state))
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
        return _decode_content(
            version.content,
            config.get("source_format") or config["format"],
        )


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
