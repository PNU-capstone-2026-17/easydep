from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.implementation.application.jobs import JobNotFound
from app.implementation.application.jobs import worker as implementation_worker
from app.testing.runtime.adapter import TestingAdapter

router = APIRouter(prefix="/api/testing", tags=["testing"])

_testing_jobs: dict[str, dict[str, Any]] = {}
_testing_jobs_lock = threading.RLock()


class CreateTestingJobRequest(BaseModel):
    implementation_job_id: str


def _job(job_id: str) -> dict[str, Any]:
    with _testing_jobs_lock:
        try:
            return dict(_testing_jobs[job_id])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Unknown testing job.") from error


def _update(job_id: str, **changes: Any) -> None:
    with _testing_jobs_lock:
        _testing_jobs[job_id].update(changes)


def _run_test(job_id: str, run_root: Path) -> None:
    _update(job_id, status="RUNNING")
    try:
        # The web testing boundary owns its runner and does not invoke the
        # legacy orchestration graph.
        report = TestingAdapter().run(implementation_result={"run_root": str(run_root)})
        _update(job_id, status="COMPLETED", result=report)
    except Exception as error:  # The job itself failed before a test report existed.
        _update(job_id, status="FAILED", error=str(error)[-4000:])


@router.post("/apps/{app_id}/jobs", status_code=202)
def create_testing_job(app_id: str, request: CreateTestingJobRequest) -> dict:
    try:
        implementation = implementation_worker.get_testing_input(request.implementation_job_id)
    except JobNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown implementation job.") from error

    if implementation["app_id"] != app_id:
        raise HTTPException(
            status_code=404,
            detail="Implementation job does not belong to this app.",
        )
    if implementation["status"] != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="Implementation must be COMPLETED before testing can start.",
        )
    run_root_value = implementation.get("run_root")
    if not run_root_value:
        raise HTTPException(status_code=409, detail="Implementation workspace is unavailable.")
    run_root = Path(str(run_root_value))
    if not run_root.is_dir():
        raise HTTPException(status_code=409, detail="Implementation workspace is unavailable.")

    job_id = uuid.uuid4().hex
    record = {
        "job_id": job_id,
        "app_id": app_id,
        "implementation_job_id": request.implementation_job_id,
        "status": "QUEUED",
        "result": None,
        "error": None,
    }
    with _testing_jobs_lock:
        _testing_jobs[job_id] = record
    threading.Thread(target=_run_test, args=(job_id, run_root), daemon=True).start()
    return record


@router.get("/jobs/{job_id}")
def get_testing_job(job_id: str) -> dict:
    return _job(job_id)
