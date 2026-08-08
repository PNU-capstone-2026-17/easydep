from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_IAC_CODE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
)
from app.repositories import artifact_repository
from app.repositories.artifact_repository import AppNotFound
from .schemas import (
    ApprovalRequest,
    CreateImplementationFeedbackJobRequest,
    CreateImplementationJobRequest,
    GenerateFrontendRequest,
)
from .frontend_scaffold import FrontendScaffoldError
from .frontend_generation import generate_frontend_project, write_openapi_input
from .worker import InvalidJobState, JobNotFound, worker


router = APIRouter(prefix="/api/implementation", tags=["implementation"])
FILE_ARTIFACT_TYPES = {
    TYPE_SOURCE_CODE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_TEST_CODE,
    TYPE_DEPLOYMENT_FILE,
    TYPE_IAC_CODE,
}


@router.post("/apps/{app_id}/frontend", status_code=201)
def generate_frontend(app_id: str, request: GenerateFrontendRequest) -> dict:
    """Generate and version the OpenAPI Generator React scaffold."""
    try:
        design = artifact_repository.load_state(app_id)
        api_spec = design.get("api_spec", {})
        worker.settings.work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="frontend-scaffold-", dir=worker.settings.work_root
        ) as directory:
            root = Path(directory)
            openapi_path = root / "openapi.json"
            frontend = root / "frontend"
            write_openapi_input(openapi_path, api_spec)

            def run_command(name: str, command: list[str], cwd: Path) -> object:
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=worker.settings.command_timeout_seconds,
                    check=False,
                )
                if result.returncode != 0:
                    output = result.stderr or result.stdout
                    raise FrontendScaffoldError(
                        f"{name} failed with exit code {result.returncode}: "
                        + output[-2000:]
                    )
                return result

            generation = generate_frontend_project(
                workspace_root=worker.settings.repository_root,
                openapi_path=openapi_path,
                frontend_root=frontend,
                api_spec=api_spec,
                application_name=request.application_name,
                api_base_url=request.api_base_url,
                run_command=run_command,
            )
            files = {
                path.relative_to(frontend).as_posix(): path.read_text(encoding="utf-8")
                for path in frontend.rglob("*")
                if path.is_file()
            }
        version_id = artifact_repository.save_file_snapshot(
            app_id,
            TYPE_FRONTEND_SOURCE_CODE,
            files,
            metadata=generation.artifact_metadata(request.application_name),
        )
        snapshot = artifact_repository.load_file_snapshot(
            app_id, TYPE_FRONTEND_SOURCE_CODE
        )
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    except (FrontendScaffoldError, OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    assert snapshot is not None
    return {
        "artifact_type": TYPE_FRONTEND_SOURCE_CODE,
        "version_id": version_id,
        "version_no": snapshot["version_no"],
        "metadata": snapshot["metadata"],
        "files": [
            {"path": path, "sha256": item["sha256"]}
            for path, item in snapshot["files"].items()
        ],
    }


@router.post("/apps/{app_id}/jobs", status_code=202)
def create_job(app_id: str, request: CreateImplementationJobRequest) -> dict:
    try:
        return worker.create_job(app_id, artifact_repository.load_state(app_id), request.base_package, request.allow_assumptions)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    except InvalidJobState as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/apps/{app_id}/feedback-jobs", status_code=202)
def create_feedback_job(
    app_id: str, request: CreateImplementationFeedbackJobRequest
) -> dict:
    try:
        return worker.create_feedback_job(
            app_id,
            artifact_repository.load_state(app_id),
            request.feedback,
            request.base_package,
            request.allow_assumptions,
        )
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    except InvalidJobState as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return worker.get(job_id)
    except JobNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown implementation job.") from error


@router.post("/jobs/{job_id}/approval", status_code=202)
def approve_job(job_id: str, request: ApprovalRequest) -> dict:
    try:
        return worker.approve(job_id, request.request_id, request.approved, request.approved_by, request.retry_failed, request.delegate_repair_approvals)
    except JobNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown implementation job.") from error
    except InvalidJobState as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/jobs/{job_id}/cancel", status_code=200)
def cancel_job(job_id: str) -> dict:
    try:
        return worker.cancel(job_id)
    except JobNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown implementation job.") from error
    except InvalidJobState as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/apps/{app_id}/artifacts/{artifact_type}")
def get_file_artifact(app_id: str, artifact_type: str) -> dict:
    if artifact_type not in FILE_ARTIFACT_TYPES:
        raise HTTPException(status_code=404, detail="Unknown implementation artifact type.")
    try:
        snapshot = artifact_repository.load_file_snapshot(app_id, artifact_type)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Artifact has not been generated.")
    snapshot["files"] = [{"path": path, "sha256": value["sha256"]} for path, value in snapshot["files"].items()]
    return snapshot


@router.get("/apps/{app_id}/artifacts/{artifact_type}/versions")
def list_file_artifact_versions(app_id: str, artifact_type: str) -> dict:
    if artifact_type not in FILE_ARTIFACT_TYPES:
        raise HTTPException(status_code=404, detail="Unknown implementation artifact type.")
    try:
        versions = artifact_repository.list_file_artifact_versions(app_id, artifact_type)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    return {"artifact_type": artifact_type, "versions": versions}


@router.get("/apps/{app_id}/artifacts/{artifact_type}/files/{file_path:path}")
def get_file(app_id: str, artifact_type: str, file_path: str) -> dict:
    if artifact_type not in FILE_ARTIFACT_TYPES:
        raise HTTPException(status_code=404, detail="Unknown implementation artifact type.")
    try:
        snapshot = artifact_repository.load_file_snapshot(app_id, artifact_type)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    item = (snapshot or {}).get("files", {}).get(file_path)
    if item is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return {"path": file_path, **item}
