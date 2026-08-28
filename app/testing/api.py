from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.implementation.application.jobs import JobNotFound
from app.implementation.application.jobs import worker as implementation_worker
from app.metrics import langsmith as langsmith_metrics
from app.testing.runtime.adapter import TestingAdapter
from app.testing.runtime.verification import run_verification_graph
from app.validation import RepairAttempt, RepairLedger, repair_makes_progress, stable_digest

router = APIRouter(prefix="/api/testing", tags=["testing"])

_testing_jobs: dict[str, dict[str, Any]] = {}
_testing_jobs_lock = threading.RLock()


class CreateTestingJobRequest(BaseModel):
    implementation_job_id: str
    repair_testing_job_id: str | None = None


def _job(job_id: str) -> dict[str, Any]:
    with _testing_jobs_lock:
        try:
            return dict(_testing_jobs[job_id])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Unknown testing job.") from error


def _update(job_id: str, **changes: Any) -> None:
    with _testing_jobs_lock:
        _testing_jobs[job_id].update(changes)


def _finding_keys(report: dict[str, Any]) -> tuple[str, ...]:
    findings: list[str] = []
    unit_passed = report.get("unitPassed")
    if unit_passed is None:
        unit_passed = (report.get("unitTests") or {}).get("passed", report.get("passed"))
    if not bool(unit_passed):
        findings.append("testing.unit-tests")
    verification = report.get("verification") or {}
    if verification.get("blockingReason"):
        findings.append(f"testing.dynamic:{verification['blockingReason']}")
    if not findings and not report.get("passed"):
        findings.append("testing.verification")
    return tuple(sorted(set(findings)))


def _repair_state(ledger: RepairLedger, *, passed: bool) -> dict[str, Any]:
    if passed:
        status = "COMPLETED"
    elif ledger.status == "WAITING_EXTERNAL":
        status = "WAITING_EXTERNAL"
    elif ledger.status == "STALLED":
        status = "STALLED"
    else:
        status = "ACTIVE"
    return {
        "status": status,
        "attempt_count": len(ledger.attempts),
        "accepted_count": sum(
            attempt.outcome in {"improved", "clean"} for attempt in ledger.attempts
        ),
        "recent_attempts": [
            attempt.model_dump(mode="json") for attempt in ledger.attempts[-5:]
        ],
        "tried_strategies": sorted(
            {attempt.strategy_key for attempt in ledger.attempts}
        ),
        "rejected_candidate_digests": sorted(
            {
                attempt.candidate_digest
                for attempt in ledger.attempts
                if attempt.candidate_digest
                and attempt.outcome not in {"improved", "clean"}
            }
        ),
        "finding_digest": stable_digest(
            ledger.attempts[-1].finding_keys_after if ledger.attempts else ()
        ),
        "stall_reason": ledger.stall_reason,
    }


def _run_test(job_id: str, app_id: str, run_root: Path) -> None:
    _update(job_id, status="RUNNING")
    job = _job(job_id)
    repair_history = job.get("repair_history") or {}
    previous_findings = tuple(job.get("previous_findings") or ())
    ledger = RepairLedger.model_validate(repair_history or {})
    try:
        # The web testing boundary owns its runner and does not invoke the
        # legacy orchestration graph — but it does run the same verification
        # stages: unit tests here, then static analysis and the dynamic checks
        # against a live instance built from the stored artifacts.
        with langsmith_metrics.trace_metadata({"app_id": app_id}):
            report = TestingAdapter().run(implementation_result={"run_root": str(run_root)})
            unit_passed = bool(report.get("passed"))
            report["unitPassed"] = unit_passed

            verification = run_verification_graph(
                run_id=job_id,
                app_id=app_id,
                manifests_dir=str(run_root / "application" / "k8s"),
                iac_dir=str(run_root / "application" / "terraform"),
                repair_history=ledger.model_dump(mode="json"),
            )
            report["verification"] = verification
            report["passed"] = unit_passed and verification["passed"]
            report["diagnostics"] = [
                *(report.get("diagnostics") or []),
                *verification["diagnostics"],
            ]
        findings = _finding_keys(report)
        dynamic = (verification.get("reports") or {}).get("dynamicFunctional") or {}
        candidate_digest = str(dynamic.get("candidateDigest") or stable_digest(report))
        if findings and not previous_findings:
            ledger.record(
                RepairAttempt(
                    stage="testing.dynamic-functional",
                    strategy_key="initial_generation",
                    input_digest=stable_digest({"job": job_id, "findings": findings}),
                    candidate_digest=candidate_digest,
                    finding_keys_after=findings,
                    outcome="no_improvement",
                    detail="Initial testing run established the repair baseline.",
                )
            )
        elif previous_findings:
            repeated = any(
                attempt.candidate_digest == candidate_digest
                for attempt in ledger.attempts
                if attempt.candidate_digest
            )
            improved = not repeated and repair_makes_progress(previous_findings, findings)
            ledger.record(
                RepairAttempt(
                    stage="testing.dynamic-functional",
                    strategy_key="regenerate_from_accumulated_failures",
                    input_digest=stable_digest(
                        {
                            "findings": previous_findings,
                            "history": ledger.prompt_context(),
                        }
                    ),
                    candidate_digest=candidate_digest,
                    finding_keys_before=previous_findings,
                    finding_keys_after=findings,
                    outcome=(
                        "repeated_candidate"
                        if repeated
                        else "clean"
                        if not findings
                        else "improved"
                        if improved
                        else "no_improvement"
                    ),
                )
            )
        if report["passed"]:
            ledger.status = "COMPLETED"
        elif previous_findings and ledger.attempts[-1].outcome in {
            "repeated_candidate",
            "no_improvement",
            "regressed",
        }:
            ledger.status = "STALLED"
            ledger.stall_reason = (
                "The regenerated test candidate did not improve the accumulated failures."
            )
        else:
            ledger.status = "ACTIVE"
        report["blocking_findings"] = [
            {
                "code": key.split(":", 1)[0],
                "stage": "testing",
                "target_ids": [],
                "message": key.split(":", 1)[-1].replace("testing.", ""),
                "severity": "error",
                "repairable": True,
            }
            for key in findings
        ]
        report["repair_state"] = _repair_state(ledger, passed=bool(report["passed"]))
        _update(
            job_id,
            status="COMPLETED",
            result=report,
            repair_history=ledger.model_dump(mode="json"),
        )
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

    repair_history: dict[str, Any] | None = None
    previous_findings: tuple[str, ...] = ()
    if request.repair_testing_job_id:
        previous = _job(request.repair_testing_job_id)
        if previous.get("app_id") != app_id or previous.get("implementation_job_id") != request.implementation_job_id:
            raise HTTPException(status_code=404, detail="Testing job does not belong to this implementation.")
        previous_result = previous.get("result") or {}
        if previous.get("status") != "COMPLETED" or previous_result.get("passed") is not False:
            raise HTTPException(status_code=409, detail="Only a completed failing testing job can be repaired.")
        repair_history = previous.get("repair_history") or {}
        previous_findings = _finding_keys(previous_result)

    job_id = uuid.uuid4().hex
    record = {
        "job_id": job_id,
        "app_id": app_id,
        "implementation_job_id": request.implementation_job_id,
        "status": "QUEUED",
        "result": None,
        "error": None,
        "repair_of_job_id": request.repair_testing_job_id,
        "repair_history": repair_history or RepairLedger().model_dump(mode="json"),
        "previous_findings": list(previous_findings),
    }
    with _testing_jobs_lock:
        _testing_jobs[job_id] = record
    threading.Thread(
        target=_run_test, args=(job_id, app_id, run_root), daemon=True
    ).start()
    return record


@router.get("/jobs/{job_id}")
def get_testing_job(job_id: str) -> dict:
    return _job(job_id)
