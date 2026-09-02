"""구현 결과의 테스트 작업과 실패 이력 기반 재시도를 관리한다.

완료된 구현 작업의 로컬 workspace를 받아 unit test, 정적 검사와 실행 검증을 차례로
수행한다. 실패 후 다시 실행할 때는 이전 finding과 repair 이력을 넘겨 같은 결과를 반복했는지
판단한다. 작업과 고정 입력은 MySQL에 저장하므로 서버가 다시 시작되어도 조회하거나
마지막으로 끝난 검사 다음부터 재개할 수 있다.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.db.models import TYPE_IAC_CODE
from app.implementation.application.jobs import JobNotFound
from app.implementation.application.jobs import worker as implementation_worker
from app.metrics import langsmith as langsmith_metrics
from app.repositories.testing_job_repository import (
    TestingJobChanges,
    TestingJobRecord,
    insert_testing_job,
    load_testing_job,
    unfinished_testing_jobs,
    update_testing_job,
)
from app.testing.runtime.adapter import TestingAdapter
from app.testing.runtime.verification import run_verification_graph
from app.testing.schemas.testing_input import TestingInput
from app.testing.utils.artifact_source import (
    ArtifactSnapshotMismatch,
    ArtifactSourceUnavailable,
    capture_testing_input,
    materialized_testing_application,
)
from app.testing.utils.gates import aggregate_gate_report, gate_status
from app.validation import RepairAttempt, RepairLedger, repair_makes_progress, stable_digest

# DB가 작업 상태의 기준이다. 이 set은 같은 프로세스에서 startup 복구와 새 요청이 한 작업을
# 동시에 실행하지 않게 막는 실행 잠금일 뿐이며, 조회 가능한 상태를 보관하지 않는다.
_active_testing_jobs: set[str] = set()
_active_testing_jobs_lock = threading.RLock()


class CreateTestingJobRequest(BaseModel):
    """테스트할 구현 작업과 선택적인 이전 실패 작업 ID.

    ``repair_testing_job_id``는 테스트 코드 자체를 다시 만들 때 사용한다.
    ``preserve_testing_job_id``는 구현을 고친 뒤 이전에 실패를 발견한 테스트 코드를 그대로
    실행할 때 사용한다. 둘을 동시에 지정할 수는 없다.
    """

    implementation_job_id: str
    repair_testing_job_id: str | None = None
    preserve_testing_job_id: str | None = None


def _job(job_id: str) -> dict[str, Any]:
    """MySQL에서 테스트 작업을 읽고 JSON 응답으로 바꾼다."""
    record = load_testing_job(job_id)
    if record is None:
        raise ValueError("Unknown testing job.")
    return record.model_dump(mode="json")


def _update(job_id: str, **changes: Any) -> None:
    """허용된 작업 필드만 한 transaction으로 갱신한다."""
    try:
        update_testing_job(job_id, TestingJobChanges(**changes))
    except KeyError as error:
        raise ValueError("Unknown testing job.") from error


def _now() -> datetime:
    """MySQL ``DATETIME``에 저장할 UTC 시각을 timezone 정보 없이 반환한다."""
    return datetime.now(UTC).replace(tzinfo=None)


def _start_thread(
    job_id: str,
    testing_input: TestingInput,
    *,
    resume_node: str | None = None,
) -> bool:
    """한 process 안에서 같은 Testing 작업을 한 번만 실행한다."""
    with _active_testing_jobs_lock:
        if job_id in _active_testing_jobs:
            return False
        _active_testing_jobs.add(job_id)
    thread_kwargs = {"resume_node": resume_node} if resume_node else {}
    threading.Thread(
        target=_run_test,
        args=(job_id, testing_input),
        kwargs=thread_kwargs,
        daemon=True,
        name=f"easydep-testing-{job_id[:8]}",
    ).start()
    return True


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
    for name, child in (verification.get("reports") or {}).items():
        if gate_status(child) in {"PASS", "NOT_APPLICABLE"}:
            continue
        reason = str(
            (child or {}).get("reason")
            or (child or {}).get("message")
            or verification.get("blockingReason")
            or f"{name} gate did not pass"
        )
        findings.append(f"testing.{name}:{reason}")
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


def _run_test(
    job_id: str,
    testing_input: TestingInput,
    *,
    resume_node: str | None = None,
) -> None:
    """고정된 구현 snapshot으로 검사를 실행하고 중간·최종 결과를 저장한다.

    서버가 ``verification``에서 중단된 작업을 복구하면 이미 통과한 애플리케이션 테스트
    보고서를 재사용한다. 복원한 파일의 digest 검사는 다시 수행하므로, 저장 이후 파일이
    바뀐 작업을 잘못 이어서 실행하지 않는다.
    """
    _update(job_id, status="RUNNING", started_at=_now(), error=None)
    job = _job(job_id)
    repair_history = job.get("repair_history") or {}
    previous_findings = tuple(job.get("previous_findings") or ())
    ledger = RepairLedger.model_validate(repair_history or {})
    partial_result = job.get("result") or {}
    preserved_test_code = str(partial_result.get("preservedCandidateCode") or "")
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
            saved_application_report = partial_result.get("applicationReport")
            if resume_node == "verification" and isinstance(saved_application_report, dict):
                report = dict(saved_application_report)
            else:
                _update(job_id, current_node="application_tests")
                report = TestingAdapter().run(implementation_result={"run_root": str(run_root)})
                unit_status = (report.get("unitTests") or {}).get("status")
                unit_passed = (
                    bool(report.get("passed")) if unit_status is None else unit_status == "passed"
                )
                report["unitPassed"] = unit_passed
                _update(
                    job_id,
                    current_node="verification",
                    result={
                        "applicationReport": report,
                        **(
                            {"preservedCandidateCode": preserved_test_code}
                            if preserved_test_code
                            else {}
                        ),
                    },
                )
            static_passed = bool(report.get("passed"))

            verification = run_verification_graph(
                run_id=job_id,
                app_id=testing_input.app_id,
                application_dir=str(run_root / "application"),
                repair_history=ledger.model_dump(mode="json"),
                implementation_job_id=testing_input.implementation_job_id,
                run_dynamic=static_passed,
                testing_input=testing_input.model_dump(mode="json"),
                iac_expected=TYPE_IAC_CODE in testing_input.artifact_version_ids,
                # DEPLOYMENT_FILE에는 Dockerfile도 들어간다. 실제 package 필요 여부는
                # static node가 고정된 ResourcePlan과 복원 디렉터리를 보고 판단한다.
                deployment_package_expected=None,
                fixed_test_code=preserved_test_code or None,
            )
            report["verification"] = verification
            application_gate = {
                "gateStatus": (
                    "PASS"
                    if static_passed
                    else "INCONCLUSIVE"
                    if any(
                        str(item.get("defectClass") or "") == "ENVIRONMENT_DEFECT"
                        for item in report.get("diagnostics") or []
                        if isinstance(item, dict)
                    )
                    else "FAIL"
                )
            }
            aggregate = aggregate_gate_report(
                {
                    "applicationTests": application_gate,
                    "verification": verification,
                }
            )
            report["passed"] = aggregate["passed"]
            report["gateStatus"] = aggregate["status"]
            report["gateCounts"] = aggregate["counts"]
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
        else:
            # 횟수 제한은 두지 않는다. 개선되지 않은 시도도 이력에 남아 다음 LLM 호출이 같은
            # 후보와 전략을 피할 수 있게 하되, 자동 수리 자체를 STALLED로 닫지는 않는다.
            ledger.status = "ACTIVE"
            ledger.stall_reason = None
        dynamic_defect = dynamic.get("defect") or {}
        defect_class = (
            dynamic_defect.get("class")
            or dynamic_defect.get("defectClass")
            or dynamic.get("defectClass")
            or "SUT_DEFECT"
        )
        defect_route = {
            "TEST_DEFECT": "testing",
            "SUT_DEFECT": "implementation",
            "ENVIRONMENT_DEFECT": "environment",
            "UPSTREAM_AMBIGUITY": "requirements-or-design",
        }
        repair_owner = dynamic_defect.get("route") or defect_route.get(defect_class, "testing")
        preserve_tests = dynamic_defect.get("preserveTests", defect_class != "TEST_DEFECT")
        report["blocking_findings"] = []
        for key in findings:
            is_dynamic = key.startswith("testing.dynamicFunctional:")
            is_verification = key.startswith(("testing.static:", "testing.iac:"))
            inferred_class = defect_class if is_dynamic else "SUT_DEFECT"
            inferred_owner = repair_owner if is_dynamic else "implementation"
            if is_verification:
                child_name = key.split(".", 1)[1].split(":", 1)[0]
                child_report = (verification.get("reports") or {}).get(child_name) or {}
                if gate_status(child_report) == "INCONCLUSIVE":
                    inferred_class = "ENVIRONMENT_DEFECT"
                    inferred_owner = "environment"
            if key == "testing.unit-tests" and any(
                str(item.get("defectClass") or "") == "ENVIRONMENT_DEFECT"
                for item in report.get("diagnostics") or []
                if isinstance(item, dict)
            ):
                inferred_class = "ENVIRONMENT_DEFECT"
                inferred_owner = "environment"
            report["blocking_findings"].append(
                {
                    "code": key.split(":", 1)[0],
                    "stage": "testing",
                    "target_ids": [],
                    "message": key.split(":", 1)[-1].replace("testing.", ""),
                    "severity": "error",
                    "repairable": inferred_class != "ENVIRONMENT_DEFECT",
                    "defect_class": inferred_class,
                    "repair_owner": inferred_owner,
                    "preserve_tests": preserve_tests if is_dynamic else True,
                    "candidate_digest": dynamic.get("candidateDigest") if is_dynamic else None,
                    "candidate_code": dynamic.get("candidateCode") if is_dynamic else None,
                }
            )
        report["repair_state"] = _repair_state(ledger, passed=bool(report["passed"]))
        _update(
            job_id,
            status="COMPLETED",
            current_node="completed",
            result=report,
            repair_history=ledger.model_dump(mode="json"),
            completed_at=_now(),
        )
    except Exception as error:
        # 테스트 결과가 만들어지기 전에 runner 자체가 실패한 경우다. 오류가 지나치게 커져
        # 작업 조회 응답을 압도하지 않도록 마지막 4,000자만 보관한다.
        _update(
            job_id,
            status="FAILED",
            current_node="failed",
            error=str(error)[-4000:],
            completed_at=_now(),
        )
    finally:
        with _active_testing_jobs_lock:
            _active_testing_jobs.discard(job_id)


def create_testing_job(app_id: str, request: CreateTestingJobRequest) -> dict:
    """완료된 구현 작업을 검사하고 새 테스트 thread를 시작한다.

    ``repair_testing_job_id``가 있으면 같은 앱과 같은 구현 작업의 완료된 실패 결과인지
    확인한 뒤 finding과 repair 이력을 이어받는다. 성공한 작업이나 실행 중인 작업을 repair
    기준으로 쓰지 않는다.
    """
    if request.repair_testing_job_id and request.preserve_testing_job_id:
        raise ValueError("Choose either test regeneration or preserved-test verification.")

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
            # Worker exposes the frozen upstream contract under the canonical name
            # ``contract_artifacts``; TestingInput keeps one typed mapping.
            contract_artifacts=implementation.get("contract_artifacts") or {},
        )
    except (ArtifactSourceUnavailable, ArtifactSnapshotMismatch, ValueError) as error:
        raise ValueError(f"Implementation artifacts are unavailable: {error}") from error

    repair_history: dict[str, Any] | None = None
    previous_findings: tuple[str, ...] = ()
    preserved_test_code = ""
    previous_job_id = request.repair_testing_job_id or request.preserve_testing_job_id
    if previous_job_id:
        previous = _job(previous_job_id)
        if previous.get("app_id") != app_id:
            raise ValueError("Testing job does not belong to this app.")
        if (
            request.repair_testing_job_id
            and previous.get("implementation_job_id") != request.implementation_job_id
        ):
            raise ValueError("A regenerated test must use the same implementation.")
        previous_result = previous.get("result") or {}
        if previous.get("status") != "COMPLETED" or previous_result.get("passed") is not False:
            raise ValueError("Only a completed failing testing job can be repaired.")
        repair_history = previous.get("repair_history") or {}
        previous_findings = _finding_keys(previous_result)
        previous_input = previous.get("testing_input")
        if previous_input is not None:
            fixed_previous_input = TestingInput.model_validate(previous_input)
            if request.repair_testing_job_id and fixed_previous_input != testing_input:
                raise ValueError("A testing repair must use the same implementation artifacts.")
            if (
                request.preserve_testing_job_id
                and fixed_previous_input.contract_artifacts != testing_input.contract_artifacts
            ):
                raise ValueError(
                    "Preserved tests require the same requirements and design contracts."
                )
        if request.preserve_testing_job_id:
            dynamic = ((previous_result.get("verification") or {}).get("reports") or {}).get(
                "dynamicFunctional"
            ) or {}
            preserved_test_code = str(dynamic.get("candidateCode") or "").strip()
            if not preserved_test_code:
                raise ValueError("The previous Testing job has no executable test candidate.")

    # DB에 QUEUED 상태를 먼저 넣은 뒤 thread를 시작한다. 반대로 하면 빠른 thread가 아직
    # 없는 job_id를 갱신하려다 실패할 수 있다.
    job_id = uuid.uuid4().hex
    record = insert_testing_job(
        TestingJobRecord(
            job_id=job_id,
            app_id=app_id,
            implementation_job_id=request.implementation_job_id,
            status="QUEUED",
            result=(
                {"preservedCandidateCode": preserved_test_code} if preserved_test_code else None
            ),
            error=None,
            repair_of_job_id=previous_job_id,
            repair_history=repair_history or RepairLedger().model_dump(mode="json"),
            previous_findings=list(previous_findings),
            testing_input=testing_input.model_dump(mode="json"),
        )
    )
    _start_thread(job_id, testing_input)
    return record.model_dump(mode="json")


def get_testing_job(job_id: str) -> dict:
    """테스트 작업의 현재 상태와 완료된 보고서를 반환한다."""
    return _job(job_id)


def startup_testing_jobs() -> int:
    """서버 종료로 멈춘 Testing 작업을 저장된 지점에서 다시 시작한다.

    고정 입력의 Pydantic 검증에 실패한 작업은 잘못된 최신 산출물로 대체하지 않고 실패로
    닫는다. ``verification`` 직전까지 애플리케이션 테스트 결과가 저장된 작업만 그 결과를
    재사용하며, 그보다 앞에서 멈춘 작업은 애플리케이션 테스트부터 다시 실행한다.
    """
    resumed = 0
    for record in unfinished_testing_jobs():
        try:
            testing_input = TestingInput.model_validate(record.testing_input)
        except ValueError as error:
            _update(
                record.job_id,
                status="FAILED",
                current_node="failed",
                error=f"Stored Testing input is invalid: {error}"[-4000:],
                completed_at=_now(),
            )
            continue
        resume_node = (
            "verification"
            if record.current_node == "verification"
            and isinstance((record.result or {}).get("applicationReport"), dict)
            else None
        )
        _update(record.job_id, status="QUEUED", error=None)
        if _start_thread(record.job_id, testing_input, resume_node=resume_node):
            resumed += 1
    return resumed
