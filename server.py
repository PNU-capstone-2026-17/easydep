from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db.models import (
    ORIGIN_AUTO_FIXED,
    ORIGIN_FEEDBACK_REVISED,
    ORIGIN_GENERATED,
)
from app.db.session import init_db
from app.graphs.architecture_artifact_graph import architecture_artifact_graph
from app.nodes.class_diagram import (
    convert_to_class_diagram_code,
    extract_class_elements,
    validate_class_diagram_syntax,
)
from app.nodes.artifact_generation import (
    generate_api_spec,
    generate_deployment_diagram,
    generate_erd,
    generate_sequence_diagram,
)
from app.repositories import artifact_repository
from app.repositories.artifact_repository import AppNotFound, StageBusy
from app.schemas.architecture_state import ArchitectureState
from app.services.artifact_validation import (
    artifact_output_path,
    validate_api_spec,
    validate_puml_artifact,
    write_json_artifact,
)
from app.services.llm_artifacts import revise_json_with_llm, revise_puml_with_llm
from app.services.plantuml_class_diagram import (
    compile_plantuml_to_image,
    save_plantuml_file,
)


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
# Render cache only. Artifact text lives in MySQL; diagram images are
# re-rendered from that text on demand, so this directory is disposable.
RENDER_DIR = BASE_DIR / "outputs"
MAX_REVISION_ATTEMPTS = 3


STAGES = [
    "class_diagram",
    "sequence_diagram",
    "api_spec",
    "erd",
    "deployment_diagram",
]

PREREQUISITES = {
    "class_diagram": [],
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
    "class_diagram": {
        "code": "class_diagram_puml",
        "valid": "class_diagram_syntax_valid",
        "errors": "class_diagram_syntax_errors",
        "compile": "class_diagram_compile_result",
        "filename": "bce_class_diagram.puml",
        "label": "class diagram",
    },
    "sequence_diagram": {
        "code": "sequence_diagram_puml",
        "valid": "sequence_diagram_syntax_valid",
        "errors": "sequence_diagram_syntax_errors",
        "compile": "sequence_diagram_compile_result",
        "filename": "sequence_diagram.puml",
        "label": "sequence diagram",
    },
    "erd": {
        "code": "erd_puml",
        "valid": "erd_syntax_valid",
        "errors": "erd_syntax_errors",
        "compile": "erd_compile_result",
        "filename": "erd_diagram.puml",
        "label": "ERD",
    },
    "deployment_diagram": {
        "code": "deployment_diagram_puml",
        "valid": "deployment_diagram_syntax_valid",
        "errors": "deployment_diagram_syntax_errors",
        "compile": "deployment_diagram_compile_result",
        "filename": "deployment_diagram.puml",
        "label": "deployment diagram",
    },
}


class CreateAppRequest(BaseModel):
    scenario_text: str = ""


class StageRequest(BaseModel):
    scenario_text: str = ""
    plantuml_jar_path: str = "plantuml.jar"


class FeedbackRequest(StageRequest):
    feedback: str = ""


app = FastAPI(title="Architecture Artifact Tester")


@app.on_event("startup")
def startup() -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    _ = OpenAI
    init_db()


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/apps")
def create_app(request: CreateAppRequest) -> JSONResponse:
    """Issue an app id. Every later request works from this id alone."""
    app_id = artifact_repository.create_app(scenario_text=request.scenario_text)
    return JSONResponse(content={"app_id": app_id, **load_response(app_id)})


@app.get("/api/apps")
def list_apps() -> JSONResponse:
    return JSONResponse(content={"apps": artifact_repository.list_apps()})


@app.get("/api/apps/{app_id}")
def get_app(app_id: str) -> JSONResponse:
    validate_app_id(app_id)
    return JSONResponse(content={"app_id": app_id, **load_response(app_id)})


@app.post("/api/apps/{app_id}/stages/{stage}/generate")
def generate_stage(app_id: str, stage: str, request: StageRequest) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    state = prepare_state(app_id, request)
    ensure_prerequisites(stage, state)
    claim_stage(app_id, stage)

    try:
        if stage == "class_diagram":
            state["class_diagram_output_path"] = stage_output_path(app_id, stage)
            updated = generate_class_diagram_once(state)
        elif stage == "sequence_diagram":
            updated = merge_state(state, generate_sequence_diagram(state))
            updated = auto_fix_puml_stage(app_id, stage, updated, "")
        elif stage == "api_spec":
            updated = merge_state(state, generate_api_spec(state))
            updated = auto_fix_api_spec(app_id, updated, "")
        elif stage == "erd":
            updated = merge_state(state, generate_erd(state))
            updated = auto_fix_puml_stage(app_id, stage, updated, "")
        else:
            updated = merge_state(state, generate_deployment_diagram(state))
            updated = auto_fix_puml_stage(app_id, stage, updated, "")

        artifact_repository.save_stage(app_id, stage, updated, origin=ORIGIN_GENERATED)
    except Exception as error:
        artifact_repository.release_stage(app_id, stage, failed=True)
        raise HTTPException(
            status_code=502,
            detail=f"LLM/API generation failed: {error}",
        ) from error

    artifact_repository.release_stage(app_id, stage)
    return JSONResponse(content={"app_id": app_id, **to_web_response(updated)})


@app.post("/api/apps/{app_id}/stages/{stage}/feedback")
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
            updated = auto_fix_api_spec(app_id, state, request.feedback)
        else:
            updated = auto_fix_puml_stage(app_id, stage, state, request.feedback)

        artifact_repository.save_stage(
            app_id,
            stage,
            updated,
            origin=ORIGIN_FEEDBACK_REVISED if request.feedback else ORIGIN_AUTO_FIXED,
        )
    except Exception as error:
        artifact_repository.release_stage(app_id, stage, failed=True)
        raise HTTPException(
            status_code=502,
            detail=f"LLM/API feedback revision failed: {error}",
        ) from error

    artifact_repository.release_stage(app_id, stage)
    return JSONResponse(content={"app_id": app_id, **to_web_response(updated)})


@app.get("/api/apps/{app_id}/stages/{stage}/versions")
def list_stage_versions(app_id: str, stage: str) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app(app_id)
    return JSONResponse(
        content={"versions": artifact_repository.list_versions(app_id, stage)}
    )


@app.get("/api/apps/{app_id}/stages/{stage}/versions/{version_no}")
def get_stage_version(app_id: str, stage: str, version_no: int) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app(app_id)
    content = artifact_repository.get_version_content(app_id, stage, version_no)
    if content is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    return JSONResponse(content={"version_no": version_no, "content": content})


@app.get("/api/apps/{app_id}/stages/{stage}/image.{extension}")
def get_stage_image(app_id: str, stage: str, extension: str) -> FileResponse:
    """Render the stored PlantUML source to an image on demand.

    Images are not persisted in MySQL; they are rebuilt from the artifact text
    into a per-app cache directory.
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

    puml_path = Path(stage_output_path(app_id, stage))
    image_path = puml_path.with_suffix(f".{extension}")

    if not image_path.exists() or puml_path.read_text(encoding="utf-8") != puml_text:
        save_plantuml_file(puml_text, str(puml_path))
        compile_plantuml_to_image(str(puml_path), "plantuml.jar")

    if not image_path.exists():
        raise HTTPException(status_code=500, detail="Diagram rendering failed.")

    media_type = "image/svg+xml" if extension == "svg" else "image/png"
    return FileResponse(image_path, media_type=media_type)


@app.post("/api/apps/{app_id}/generate")
def generate_all(app_id: str, request: StageRequest) -> JSONResponse:
    validate_app_id(app_id)
    state = prepare_state(app_id, request)

    # The whole run writes every stage, so hold all five locks for its duration.
    claimed: list[str] = []
    try:
        for stage in STAGES:
            claim_stage(app_id, stage)
            claimed.append(stage)

        result = architecture_artifact_graph.invoke(state)
        for stage in STAGES:
            artifact_repository.save_stage(
                app_id,
                stage,
                result,
                origin=ORIGIN_GENERATED,
            )
    except HTTPException:
        release_stages(app_id, claimed, failed=False)
        raise
    except Exception as error:
        release_stages(app_id, claimed, failed=True)
        raise HTTPException(
            status_code=502,
            detail=f"LLM/API generation failed: {error}",
        ) from error

    release_stages(app_id, claimed, failed=False)
    return JSONResponse(content={"app_id": app_id, **to_web_response(result)})


def release_stages(app_id: str, stages: list[str], failed: bool) -> None:
    for stage in stages:
        artifact_repository.release_stage(app_id, stage, failed=failed)


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


def app_render_dir(app_id: str) -> Path:
    """Per-app render directory. Keeps concurrent users from colliding on
    shared output filenames."""
    directory = RENDER_DIR / app_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def stage_output_path(app_id: str, stage: str) -> str:
    return artifact_output_path(app_render_dir(app_id), PUML_FIELDS[stage]["filename"])


def prepare_state(app_id: str, request: StageRequest) -> ArchitectureState:
    state = require_app(app_id)
    if request.scenario_text:
        state["scenario_text"] = request.scenario_text
        artifact_repository.update_scenario(app_id, request.scenario_text)
    state["plantuml_jar_path"] = request.plantuml_jar_path or "plantuml.jar"
    return state


def load_response(app_id: str) -> dict[str, Any]:
    return to_web_response(require_app(app_id))


def generate_class_diagram_once(state: ArchitectureState) -> ArchitectureState:
    extracted = merge_state(state, extract_class_elements(state))
    converted = merge_state(extracted, convert_to_class_diagram_code(extracted))
    validated = merge_state(converted, validate_class_diagram_syntax(converted))
    validated["artifact_status"] = mark_status(
        validated,
        "class_diagram",
        "implemented",
    )
    return validated


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
    app_id: str,
    stage: str,
    state: ArchitectureState,
    feedback: str,
) -> ArchitectureState:
    fields = PUML_FIELDS[stage]
    code_key = fields["code"]
    valid_key = fields["valid"]
    errors_key = fields["errors"]
    compile_key = fields["compile"]
    output_path = stage_output_path(app_id, stage)
    plantuml_jar_path = state.get("plantuml_jar_path", "plantuml.jar")

    current = state.get(code_key, "")
    updated = dict(state)

    for attempt in range(MAX_REVISION_ATTEMPTS + 1):
        validation = validate_puml_artifact(current, output_path, plantuml_jar_path)
        updated[compile_key] = validation["compile_result"]
        updated[valid_key] = validation["syntax_valid"]
        updated[errors_key] = validation["syntax_errors"]
        updated[code_key] = current

        needs_revision = bool(feedback and attempt == 0) or (
            not validation["syntax_valid"] and attempt < MAX_REVISION_ATTEMPTS
        )
        if not needs_revision:
            break

        current = revise_puml_with_llm(
            artifact_name=fields["label"],
            current_puml=current,
            feedback=feedback,
            syntax_errors=validation["syntax_errors"],
            context=build_revision_context(updated),
        )

    save_plantuml_file(updated.get(code_key, ""), output_path)
    updated["artifact_status"] = mark_status(updated, stage, "implemented")
    return updated


def auto_fix_api_spec(
    app_id: str,
    state: ArchitectureState,
    feedback: str,
) -> ArchitectureState:
    current = state.get("api_spec", {})
    updated = dict(state)

    for attempt in range(MAX_REVISION_ATTEMPTS + 1):
        validation = validate_api_spec(current)
        updated["api_spec"] = current
        updated["api_spec_syntax_valid"] = validation["syntax_valid"]
        updated["api_spec_syntax_errors"] = validation["syntax_errors"]

        needs_revision = bool(feedback and attempt == 0) or (
            not validation["syntax_valid"] and attempt < MAX_REVISION_ATTEMPTS
        )
        if not needs_revision:
            break

        current = revise_json_with_llm(
            artifact_name="API specification",
            current_json=current,
            feedback=feedback,
            errors=validation["syntax_errors"],
            context=build_revision_context(updated),
        )

    write_json_artifact(
        updated.get("api_spec", {}),
        artifact_output_path(app_render_dir(app_id), "api_spec.json"),
    )
    updated["artifact_status"] = mark_status(updated, "api_spec", "implemented")
    return updated


def build_revision_context(state: ArchitectureState) -> str:
    return "\n\n".join(
        [
            "[Scenario]\n" + state.get("scenario_text", ""),
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
    return {
        "artifacts": {
            "class_diagram": result.get("class_diagram_puml", ""),
            "sequence_diagram": result.get("sequence_diagram_puml", ""),
            "api_spec": result.get("api_spec", {}),
            "erd": result.get("erd_puml", ""),
            "deployment_diagram": result.get("deployment_diagram_puml", ""),
        },
        "validation": {
            "class_diagram": {
                "valid": result.get("class_diagram_syntax_valid"),
                "errors": result.get("class_diagram_syntax_errors", []),
            },
            "sequence_diagram": {
                "valid": result.get("sequence_diagram_syntax_valid"),
                "errors": result.get("sequence_diagram_syntax_errors", []),
            },
            "api_spec": {
                "valid": result.get("api_spec_syntax_valid"),
                "errors": result.get("api_spec_syntax_errors", []),
            },
            "erd": {
                "valid": result.get("erd_syntax_valid"),
                "errors": result.get("erd_syntax_errors", []),
            },
            "deployment_diagram": {
                "valid": result.get("deployment_diagram_syntax_valid"),
                "errors": result.get("deployment_diagram_syntax_errors", []),
            },
        },
        "artifact_status": result.get("artifact_status", {}),
    }


RENDER_DIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
