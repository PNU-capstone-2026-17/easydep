from __future__ import annotations

from fastapi import APIRouter, HTTPException
from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_IAC_CODE, TYPE_SOURCE_CODE, TYPE_TEST_CODE
from app.repositories import artifact_repository
from app.repositories.artifact_repository import AppNotFound
from .schemas import (
    ApprovalRequest,
    CreateImplementationFeedbackJobRequest,
    CreateImplementationJobRequest,
)
from .worker import InvalidJobState, JobNotFound, worker


router = APIRouter(prefix="/api/implementation", tags=["implementation"])
FILE_ARTIFACT_TYPES = {TYPE_SOURCE_CODE, TYPE_TEST_CODE, TYPE_DEPLOYMENT_FILE, TYPE_IAC_CODE}


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
