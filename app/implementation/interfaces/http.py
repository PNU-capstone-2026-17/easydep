from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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
)
from ..application.jobs import InvalidJobState, JobNotFound, worker


router = APIRouter(prefix="/api/implementation", tags=["implementation"])
FILE_ARTIFACT_TYPES = {
    TYPE_SOURCE_CODE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_TEST_CODE,
    TYPE_DEPLOYMENT_FILE,
    TYPE_IAC_CODE,
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


@router.get("/apps/{app_id}/download")
def download_implementation_artifacts(app_id: str) -> StreamingResponse:
    """Download the latest generated implementation file trees as one ZIP."""
    snapshots = []
    try:
        for artifact_type in sorted(FILE_ARTIFACT_TYPES):
            snapshot = artifact_repository.load_file_snapshot(app_id, artifact_type)
            if snapshot and snapshot.get("files"):
                snapshots.append(snapshot)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    if not snapshots:
        raise HTTPException(status_code=404, detail="Implementation artifacts are unavailable.")

    archive = io.BytesIO()
    manifest: dict[str, Any] = {"app_id": app_id, "artifacts": []}
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for snapshot in snapshots:
            artifact_type = str(snapshot["artifact_type"])
            files = snapshot.get("files") or {}
            artifact_entry = {
                "artifact_type": artifact_type,
                "version_no": snapshot.get("version_no"),
                "file_count": len(files),
            }
            manifest["artifacts"].append(artifact_entry)
            for path, item in files.items():
                relative = str(path).replace("\\", "/").lstrip("/")
                if not relative or relative == "." or ".." in relative.split("/"):
                    continue
                content = item.get("content", "") if isinstance(item, dict) else str(item)
                bundle.writestr(f"{artifact_type}/{relative}", content)
        bundle.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
    archive.seek(0)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="easydep-{app_id}-implementation.zip"'
            )
        },
    )


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
