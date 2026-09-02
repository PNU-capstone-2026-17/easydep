"""구현 결과의 테스트 작업과 실패 이력 기반 재시도를 관리한다.

완료된 구현 작업의 로컬 workspace를 받아 unit test, 정적 검사와 실행 검증을 차례로
수행한다. 실패 후 다시 실행할 때는 이전 finding과 repair 이력을 넘겨 같은 결과를 반복했는지
판단한다. 테스트 작업 registry는 현재 프로세스 메모리에 있으므로 서버 재시작 후에는
구현 작업에서 새 테스트를 시작해야 한다.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from pydantic import BaseModel

from app.implementation.application.jobs import JobNotFound
from app.implementation.application.jobs import worker as implementation_worker
from app.metrics import langsmith as langsmith_metrics
from app.testing.runtime.adapter import TestingAdapter
from app.testing.runtime.verification import run_verification_graph
from app.testing.schemas.testing_input import TestingInput
from app.testing.utils.artifact_source import (
    ArtifactSnapshotMismatch,
    ArtifactSourceUnavailable,
    capture_testing_input,
    materialized_testing_application,
)
from app.validation import RepairAttempt, RepairLedger, repair_makes_progress, stable_digest

_testing_jobs: dict[str, dict[str, Any]] = {}
# HTTP 조회 thread와 백그라운드 테스트 thread가 같은 dict를 사용하므로 모든 접근을
# RLock으로 감싼다. 읽을 때도 사본을 반환해 caller가 registry를 직접 바꾸지 못하게 한다.
_testing_jobs_lock = threading.RLock()


class CreateTestingJobRequest(BaseModel):
    """테스트할 구현 작업과 선택적인 이전 실패 작업 ID."""

    implementation_job_id: str
    repair_testing_job_id: str | None = None


def _job(job_id: str) -> dict[str, Any]:
    """테스트 작업 사본을 반환하고, 없으면 호출자에게 명확한 오류를 알린다."""
    with _testing_jobs_lock:
        try:
            return dict(_testing_jobs[job_id])
        except KeyError as error:
            raise ValueError("Unknown testing job.") from error


def _update(job_id: str, **changes: Any) -> None:
    """백그라운드 thread에서 테스트 작업의 일부 필드를 안전하게 갱신한다."""
    with _testing_jobs_lock:
        _testing_jobs[job_id].update(changes)


def _finding_keys(report: dict[str, Any]) -> tuple[str, ...]:
    """형태가 다른 테스트 보고서를 비교 가능한 finding key 목록으로 정리한다."""
    findings: list[str] = []
    unit_passed = report.get("unitPassed")
    if unit_passed is None:
        unit_passed = (report.get("unitTests") or {}).get("passed", report.get("passed"))
    if not bool(unit_passed):
        findings.append("testing.unit-tests")
    frontend = report.get("frontendBuild") or {}
    if frontend and frontend.get("status") not in {"passed", "not_applicable"}:
        findings.append("testing.frontend-build")
    verification = report.get("verification") or {}
    if verification.get("blockingReason"):
        findings.append(f"testing.dynamic:{verification['blockingReason']}")
    if not findings and not report.get("passed"):
        findings.append("testing.verification")
    return tuple(sorted(set(findings)))


def _repair_state(ledger: RepairLedger, *, passed: bool) -> dict[str, Any]:
    """내부 repair ledger를 프론트엔드가 표시할 수 있는 간단한 상태로 바꾼다."""
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
        # 전체 이력은 다음 repair 입력에 유지하지만 HTTP 응답에는 최근 다섯 건만 싣는다.
        # 작업을 오래 반복해도 화면 응답이 계속 커지지 않게 하기 위해서다.
        "recent_attempts": [attempt.model_dump(mode="json") for attempt in ledger.attempts[-5:]],
        "tried_strategies": sorted({attempt.strategy_key for attempt in ledger.attempts}),
        "rejected_candidate_digests": sorted(
            {
                attempt.candidate_digest
                for attempt in ledger.attempts
                if attempt.candidate_digest and attempt.outcome not in {"improved", "clean"}
            }
        ),
        "finding_digest": stable_digest(
            ledger.attempts[-1].finding_keys_after if ledger.attempts else ()
        ),
        "stall_reason": ledger.stall_reason,
    }


def _run_test(job_id: str, testing_input: TestingInput) -> None:
    """고정된 구현 snapshot으로 모든 검사를 실행하고 결과를 registry에 기록한다."""
    _update(job_id, status="RUNNING")
    job = _job(job_id)
    repair_history = job.get("repair_history") or {}
    previous_findings = tuple(job.get("previous_findings") or ())
    ledger = RepairLedger.model_validate(repair_history or {})
    try:
        # 구현 작업이 고정한 파일 묶음을 새 임시 폴더에 한 번만 복원한다. 단위·정적·IaC·
        # 동적 검사는 아래 context가 끝날 때까지 이 폴더를 함께 사용한다.
        with (
            materialized_testing_application(testing_input) as run_root,
            langsmith_metrics.trace_metadata(
                {
                    "app_id": testing_input.app_id,
                    "implementation_job_id": testing_input.implementation_job_id,
                }
            ),
        ):
            report = TestingAdapter().run(implementation_result={"run_root": str(run_root)})
            unit_status = (report.get("unitTests") or {}).get("status")
            unit_passed = (
                bool(report.get("passed"))
                if unit_status is None
                else unit_status == "passed"
            )
            report["unitPassed"] = unit_passed
            static_passed = bool(report.get("passed"))

            verification = run_verification_graph(
                run_id=job_id,
                app_id=testing_input.app_id,
                application_dir=str(run_root / "application"),
                repair_history=ledger.model_dump(mode="json"),
                implementation_job_id=testing_input.implementation_job_id,
                run_dynamic=static_passed,
            )
            report["verification"] = verification
            report["passed"] = static_passed and verification["passed"]
            report["diagnostics"] = [
                *(report.get("diagnostics") or []),
                *verification["diagnostics"],
            ]
            report["testingInput"] = testing_input.model_dump(mode="json")
        findings = _finding_keys(report)
        dynamic = (verification.get("reports") or {}).get("dynamicFunctional") or {}
        candidate_digest = str(dynamic.get("candidateDigest") or stable_digest(report))
        # 최초 실패는 이후 repair와 비교할 기준으로 기록한다. 재시도라면 결과 digest와
        # finding 집합을 이전 이력과 비교해 같은 후보 반복, 개선, 악화를 구분한다.
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
    except Exception as error:
        # 테스트 결과가 만들어지기 전에 runner 자체가 실패한 경우다. 오류가 지나치게 커져
        # 작업 조회 응답을 압도하지 않도록 마지막 4,000자만 보관한다.
        _update(job_id, status="FAILED", error=str(error)[-4000:])


def create_testing_job(app_id: str, request: CreateTestingJobRequest) -> dict:
    """완료된 구현 작업을 검사하고 새 테스트 thread를 시작한다.

    ``repair_testing_job_id``가 있으면 같은 앱과 같은 구현 작업의 완료된 실패 결과인지
    확인한 뒤 finding과 repair 이력을 이어받는다. 성공한 작업이나 실행 중인 작업을 repair
    기준으로 쓰지 않는다.
    """
    try:
        implementation = implementation_worker.get_testing_input(request.implementation_job_id)
    except JobNotFound as error:
        raise ValueError("Unknown implementation job.") from error

    if implementation["app_id"] != app_id:
        raise ValueError("Implementation job does not belong to this app.")
    if implementation["status"] != "COMPLETED":
        raise ValueError("Implementation must be COMPLETED before testing can start.")
    # 구현 작업이 기록한 파일 묶음 ID만 고정한다. 실제 파일은 백그라운드 thread에서 한
    # 번 복원하며 이후 검사들은 모두 같은 임시 애플리케이션 폴더를 사용한다.
    try:
        testing_input = capture_testing_input(
            app_id,
            request.implementation_job_id,
            artifact_version_ids=implementation.get("artifact_version_ids"),
        )
    except (ArtifactSourceUnavailable, ArtifactSnapshotMismatch, ValueError) as error:
        raise ValueError(f"Implementation artifacts are unavailable: {error}") from error

    repair_history: dict[str, Any] | None = None
    previous_findings: tuple[str, ...] = ()
    if request.repair_testing_job_id:
        previous = _job(request.repair_testing_job_id)
        if (
            previous.get("app_id") != app_id
            or previous.get("implementation_job_id") != request.implementation_job_id
        ):
            raise ValueError("Testing job does not belong to this implementation.")
        previous_result = previous.get("result") or {}
        if previous.get("status") != "COMPLETED" or previous_result.get("passed") is not False:
            raise ValueError("Only a completed failing testing job can be repaired.")
        repair_history = previous.get("repair_history") or {}
        previous_findings = _finding_keys(previous_result)
        previous_input = previous.get("testing_input")
        if (
            previous_input is not None
            and TestingInput.model_validate(previous_input) != testing_input
        ):
            raise ValueError("A testing repair must use the same implementation artifacts.")

    # registry에 QUEUED 상태를 먼저 넣은 뒤 thread를 시작한다. 반대로 하면 빠른 thread가
    # 아직 등록되지 않은 job_id를 갱신하려다 실패할 수 있다.
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
        "testing_input": testing_input.model_dump(mode="json"),
    }
    with _testing_jobs_lock:
        _testing_jobs[job_id] = record
    threading.Thread(
        target=langsmith_metrics.bind_context(_run_test),
        args=(job_id, testing_input),
        daemon=True,
    ).start()
    return record


def get_testing_job(job_id: str) -> dict:
    """테스트 작업의 현재 상태와 완료된 보고서를 반환한다."""
    return _job(job_id)
