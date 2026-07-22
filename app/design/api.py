"""시스템 설계 에이전트의 서빙 레이어.

원래는 엔드포인트가 server.py 본문에 직접 있었지만, 요구사항 에이전트
(app/requirements/api.py)와 같은 방식으로 라우터로만 남기고 앱 생성은 server.py가
맡는다. 그래프·서비스(app.design.graphs, app.design.services)는 서빙 방식과
무관하게 재사용된다.

app_id(앱 컨테이너) 관리와 산출물 stage 생성이 모두 이 라우터에 모여 있다. 둘 다
같은 헬퍼(to_web_response, require_app 등)와 MySQL 저장소를 공유하므로 함께 둔다.
요구사항 분석 산출물(EXTERNAL_STAGES)은 여기서 만들지 않고, 저장·조회만 한다.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.db.models import (
    FORMAT_JSON,
    ORIGIN_AUTO_FIXED,
    ORIGIN_FEEDBACK_REVISED,
    ORIGIN_GENERATED,
    ORIGIN_IMPORTED,
)
from app.design.graphs.class_diagram_graph import class_diagram_graph
from app.design.nodes.artifact_generation import (
    generate_api_spec,
    generate_deployment_diagram,
    generate_erd,
    generate_sequence_diagram,
)
from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.common.plantuml import render_plantuml
from app.design.services.common.revision import revise_json_with_llm, revise_puml_with_llm
from app.design.services.common.validation import validate_api_spec, validate_puml_artifact
from app.repositories import artifact_repository
from app.repositories.artifact_repository import (
    STAGE_ARTIFACTS,
    AppNotFound,
    StageBusy,
)

router = APIRouter(tags=["design"])


def revision_attempt_limit() -> int:
    """How many times to re-ask the LLM after a validation failure.

    0 (the default) means keep going until the artifact validates. A cap can be
    set through MAX_REVISION_ATTEMPTS when a run must be bounded.
    """
    return int(os.getenv("MAX_REVISION_ATTEMPTS", "0"))


STAGES = list(STAGE_ARTIFACTS)

# Produced by the requirements analysis agent, which now runs in this same
# process but through its own endpoint (POST /api/requirements/analyze): the
# analysis is one interactive session that yields all of them together, not four
# independent one-shot generations. They can be stored and read here, and the
# content endpoint still accepts them, but this generate endpoint does not drive
# them.
EXTERNAL_STAGES = {
    "refined_requirements",
    "usecase_spec",
    "usecase_diagram",
    "resource_spec",
}

PREREQUISITES = {
    "refined_requirements": [],
    "usecase_spec": ["refined_requirements"],
    "usecase_diagram": ["usecase_spec"],
    "resource_spec": ["refined_requirements"],
    # Every design artifact is derived from the use case specification.
    "class_diagram": ["usecase_spec"],
    "sequence_diagram": ["class_diagram_puml"],
    "api_spec": ["class_diagram_puml", "sequence_diagram_puml"],
    "erd": ["class_diagram_puml", "sequence_diagram_puml", "api_spec"],
    "deployment_diagram": [
        "class_diagram_puml",
        "sequence_diagram_puml",
        "api_spec",
        "erd_puml",
    ],
}

PUML_FIELDS = {
    "usecase_diagram": {
        "code": "usecase_diagram_puml",
        "valid": "usecase_diagram_syntax_valid",
        "errors": "usecase_diagram_syntax_errors",
        "label": "use case diagram",
    },
    "class_diagram": {
        "code": "class_diagram_puml",
        "valid": "class_diagram_syntax_valid",
        "errors": "class_diagram_syntax_errors",
        "label": "class diagram",
    },
    "sequence_diagram": {
        "code": "sequence_diagram_puml",
        "valid": "sequence_diagram_syntax_valid",
        "errors": "sequence_diagram_syntax_errors",
        "label": "sequence diagram",
    },
    "erd": {
        "code": "erd_puml",
        "valid": "erd_syntax_valid",
        "errors": "erd_syntax_errors",
        "label": "ERD",
    },
    "deployment_diagram": {
        "code": "deployment_diagram_puml",
        "valid": "deployment_diagram_syntax_valid",
        "errors": "deployment_diagram_syntax_errors",
        "label": "deployment diagram",
    },
}


class CreateAppRequest(BaseModel):
    requirements_text: str = ""
    resource_constraints_text: str = ""


class StageRequest(BaseModel):
    pass


class ImportRequest(BaseModel):
    content: Any


class FeedbackRequest(StageRequest):
    feedback: str = ""


@router.post("/api/apps")
def create_app(request: CreateAppRequest) -> JSONResponse:
    """Issue an app id. Every later request works from this id alone."""
    app_id = artifact_repository.create_app(
        requirements_text=request.requirements_text,
        resource_constraints_text=request.resource_constraints_text,
    )
    return JSONResponse(content={"app_id": app_id, **load_response(app_id)})


@router.get("/api/apps")
def list_apps() -> JSONResponse:
    return JSONResponse(content={"apps": artifact_repository.list_apps()})


@router.get("/api/apps/{app_id}")
def get_app(app_id: str) -> JSONResponse:
    validate_app_id(app_id)
    return JSONResponse(content={"app_id": app_id, **load_response(app_id)})


@router.post("/api/apps/{app_id}/stages/{stage}/generate")
def generate_stage(app_id: str, stage: str, request: StageRequest) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    if stage in EXTERNAL_STAGES:
        raise HTTPException(
            status_code=501,
            detail={
                "message": "This artifact comes from the requirements analysis "
                "agent. Run POST /api/requirements/analyze with this app_id, or "
                "store it with the content endpoint.",
                "stage": stage,
            },
        )

    state = prepare_state(app_id, request)
    ensure_prerequisites(stage, state)
    claim_stage(app_id, stage)

    try:
        if stage == "class_diagram":
            updated = generate_class_diagram_once(state)
        elif stage == "sequence_diagram":
            updated = merge_state(state, generate_sequence_diagram(state))
            updated = auto_fix_puml_stage(stage, updated, "")
        elif stage == "api_spec":
            updated = merge_state(state, generate_api_spec(state))
            updated = auto_fix_api_spec(updated, "")
        elif stage == "erd":
            updated = merge_state(state, generate_erd(state))
            updated = auto_fix_puml_stage(stage, updated, "")
        else:
            updated = merge_state(state, generate_deployment_diagram(state))
            updated = auto_fix_puml_stage(stage, updated, "")

        artifact_repository.save_stage(app_id, stage, updated, origin=ORIGIN_GENERATED)
    except Exception as error:
        artifact_repository.release_stage(app_id, stage)
        raise HTTPException(
            status_code=502,
            detail=f"LLM/API generation failed: {error}",
        ) from error

    artifact_repository.release_stage(app_id, stage)
    return JSONResponse(content={"app_id": app_id, **to_web_response(updated)})


@router.post("/api/apps/{app_id}/stages/{stage}/feedback")
def apply_stage_feedback(
    app_id: str,
    stage: str,
    request: FeedbackRequest,
) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    state = prepare_state(app_id, request)
    ensure_prerequisites(stage, state)
    claim_stage(app_id, stage)

    try:
        if stage == "api_spec":
            updated = auto_fix_api_spec(state, request.feedback)
        else:
            updated = auto_fix_puml_stage(stage, state, request.feedback)

        artifact_repository.save_stage(
            app_id,
            stage,
            updated,
            origin=ORIGIN_FEEDBACK_REVISED if request.feedback else ORIGIN_AUTO_FIXED,
        )
    except Exception as error:
        artifact_repository.release_stage(app_id, stage)
        raise HTTPException(
            status_code=502,
            detail=f"LLM/API feedback revision failed: {error}",
        ) from error

    artifact_repository.release_stage(app_id, stage)
    return JSONResponse(content={"app_id": app_id, **to_web_response(updated)})


@router.post("/api/apps/{app_id}/stages/{stage}/content")
def import_stage_content(
    app_id: str,
    stage: str,
    request: ImportRequest,
) -> JSONResponse:
    """Store an artifact produced elsewhere.

    The requirements analysis agent runs outside this service, so this is how
    its output reaches the store. It is also how a use case specification can be
    supplied by hand while that agent is not connected yet.
    """
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app(app_id)

    config = STAGE_ARTIFACTS[stage]
    state: ArchitectureState = {config["state_key"]: request.content}
    if config["valid_key"] and stage in PUML_FIELDS:
        validation = validate_puml_artifact(request.content)
        state[config["valid_key"]] = validation["syntax_valid"]
        state[config["errors_key"]] = validation["syntax_errors"]

    version_id = artifact_repository.save_stage(
        app_id,
        stage,
        state,
        origin=ORIGIN_IMPORTED,
    )
    if version_id is None:
        raise HTTPException(status_code=400, detail="Content is empty.")

    return JSONResponse(content={"app_id": app_id, **load_response(app_id)})


@router.get("/api/apps/{app_id}/stages/{stage}/versions")
def list_stage_versions(app_id: str, stage: str) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app(app_id)
    return JSONResponse(
        content={"versions": artifact_repository.list_versions(app_id, stage)}
    )


@router.get("/api/apps/{app_id}/stages/{stage}/versions/{version_no}")
def get_stage_version(app_id: str, stage: str, version_no: int) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app(app_id)
    content = artifact_repository.get_version_content(app_id, stage, version_no)
    if content is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    return JSONResponse(content={"version_no": version_no, "content": content})


@router.get("/api/apps/{app_id}/stages/{stage}/image.{extension}")
def get_stage_image(app_id: str, stage: str, extension: str) -> Response:
    """Render the stored PlantUML source to an image on demand.

    Images are never stored; they are rebuilt from the artifact text in MySQL
    and streamed straight back, so there is no render directory to collide over
    or to go stale.
    """
    validate_app_id(app_id)
    validate_stage_name(stage)
    if extension not in ("png", "svg"):
        raise HTTPException(status_code=404, detail="Unsupported image format.")
    if stage not in PUML_FIELDS:
        raise HTTPException(status_code=404, detail="Stage has no diagram image.")

    state = require_app(app_id)
    puml_text = state.get(PUML_FIELDS[stage]["code"], "")
    if not puml_text:
        raise HTTPException(status_code=404, detail="Artifact has not been generated.")

    image = render_plantuml(puml_text, extension)
    if not image:
        raise HTTPException(status_code=500, detail="Diagram rendering failed.")

    media_type = "image/svg+xml" if extension == "svg" else "image/png"
    return Response(content=image, media_type=media_type)


def claim_stage(app_id: str, stage: str) -> None:
    """Take the generation lock, or tell the caller someone else has it."""
    try:
        artifact_repository.claim_stage(app_id, stage)
    except StageBusy as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This artifact is already being generated. "
                "Wait for the running request to finish.",
                "stage": stage,
            },
        ) from error


def validate_app_id(app_id: str) -> None:
    try:
        uuid.UUID(app_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid app id.") from error


def require_app(app_id: str) -> ArchitectureState:
    try:
        return artifact_repository.load_state(app_id)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error


def prepare_state(app_id: str, request: StageRequest) -> ArchitectureState:
    return require_app(app_id)


def load_response(app_id: str) -> dict[str, Any]:
    return to_web_response(require_app(app_id))


def generate_class_diagram_once(state: ArchitectureState) -> ArchitectureState:
    """Extraction, conversion, and the syntax repair loop, run as a LangGraph."""
    result = dict(class_diagram_graph.invoke(state))
    result["artifact_status"] = mark_status(result, "class_diagram", "implemented")
    return result


def validate_stage_name(stage: str) -> None:
    if stage not in STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown stage: {stage}")


def ensure_prerequisites(stage: str, state: ArchitectureState) -> None:
    missing = []
    for key in PREREQUISITES[stage]:
        value = state.get(key)
        if value in (None, "", {}):
            missing.append(key)

    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Previous artifacts must be generated first.",
                "missing": missing,
            },
        )


def auto_fix_puml_stage(
    stage: str,
    state: ArchitectureState,
    feedback: str,
) -> ArchitectureState:
    fields = PUML_FIELDS[stage]
    code_key = fields["code"]
    valid_key = fields["valid"]
    errors_key = fields["errors"]
    current = state.get(code_key, "")
    updated = dict(state)
    limit = revision_attempt_limit()
    attempt = 0

    while True:
        validation = validate_puml_artifact(current)
        updated[valid_key] = validation["syntax_valid"]
        updated[errors_key] = validation["syntax_errors"]
        updated[code_key] = current

        # User feedback is applied once; syntax errors keep the loop running.
        needs_revision = (feedback and attempt == 0) or not validation["syntax_valid"]
        if not needs_revision or (limit and attempt >= limit):
            break

        attempt += 1
        current = revise_puml_with_llm(
            artifact_name=fields["label"],
            current_puml=current,
            feedback=feedback,
            syntax_errors=validation["syntax_errors"],
            context=build_revision_context(updated),
        )

    updated["artifact_status"] = mark_status(updated, stage, "implemented")
    return updated


def auto_fix_api_spec(
    state: ArchitectureState,
    feedback: str,
) -> ArchitectureState:
    current = state.get("api_spec", {})
    updated = dict(state)
    limit = revision_attempt_limit()
    attempt = 0

    while True:
        validation = validate_api_spec(current)
        updated["api_spec"] = current
        updated["api_spec_syntax_valid"] = validation["syntax_valid"]
        updated["api_spec_syntax_errors"] = validation["syntax_errors"]

        needs_revision = (feedback and attempt == 0) or not validation["syntax_valid"]
        if not needs_revision or (limit and attempt >= limit):
            break

        attempt += 1
        current = revise_json_with_llm(
            artifact_name="API specification",
            current_json=current,
            feedback=feedback,
            errors=validation["syntax_errors"],
            context=build_revision_context(updated),
        )

    updated["artifact_status"] = mark_status(updated, "api_spec", "implemented")
    return updated


def build_revision_context(state: ArchitectureState) -> str:
    return "\n\n".join(
        [
            "[Use Case Specification]\n" + usecase_spec_text(state),
            "[Class Diagram]\n" + state.get("class_diagram_puml", ""),
            "[Sequence Diagram]\n" + state.get("sequence_diagram_puml", ""),
            "[API Spec]\n" + str(state.get("api_spec", {})),
            "[ERD]\n" + state.get("erd_puml", ""),
        ]
    )


def merge_state(
    state: ArchitectureState,
    updates: ArchitectureState,
) -> ArchitectureState:
    merged = dict(state)
    merged.update(updates)
    return merged


def mark_status(
    state: ArchitectureState,
    artifact_name: str,
    status: str,
) -> dict[str, str]:
    current = dict(state.get("artifact_status", {}))
    current[artifact_name] = status
    return current


def to_web_response(result: dict[str, Any]) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    validation: dict[str, Any] = {}

    for stage, config in STAGE_ARTIFACTS.items():
        empty: Any = {} if config["format"] == FORMAT_JSON else ""
        artifacts[stage] = result.get(config["state_key"], empty)
        validation[stage] = {
            "valid": result.get(config["valid_key"]) if config["valid_key"] else None,
            "errors": (
                result.get(config["errors_key"], []) if config["errors_key"] else []
            ),
        }

    return {
        "artifacts": artifacts,
        "validation": validation,
        "artifact_status": result.get("artifact_status", {}),
    }
