"""구현 결과를 검사하고 실패 이력에 따라 다시 실행한다.

완료된 구현 작업의 고정 snapshot을 받아 여러 구성 요소를 함께 실행하는 통합 검사와 E2E를
수행한다. 단위 테스트, 작은 통합 테스트와 frontend build는 구현 에이전트가 각 작업 안에서
이미 수행하므로 여기서 반복하지 않는다. Testing은 고정 입력과 이전 체크포인트를 받아 전체
흐름 검사 결과만 반환한다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.artifact_trace import TraceRef
from app.artifact_trace_projection import (
    project_artifact_trace,
    projection_state_from_testing_contracts,
)
from app.db.models import TYPE_IAC_CODE, TYPE_SOURCE_CODE
from app.implementation.application.jobs import JobNotFound
from app.implementation.application.jobs import worker as implementation_worker
from app.metrics import langsmith as langsmith_metrics
from app.repositories.artifact_repository import load_file_snapshot
from app.testing.runtime.verification import run_verification_graph
from app.testing.schemas.testing_input import TestingInput
from app.testing.utils.artifact_source import (
    ArtifactSnapshotMismatch,
    ArtifactSourceUnavailable,
    capture_testing_input,
    materialized_testing_application,
)
from app.testing.utils.gates import aggregate_gate_report, gate_status
from app.validation import (
    RepairAttempt,
    RepairLedger,
    repair_makes_progress,
    stable_digest,
)

TestingProgress = Callable[[dict[str, Any]], None]


def _dynamic_target_ids(report: dict[str, Any]) -> list[str]:
    """첫 차단 실패의 case와 operation만 저장된 ID로 가리킨다."""
    finding = report.get("finding")
    finding = finding if isinstance(finding, dict) else {}
    operation_id = str(finding.get("operationId") or "").strip()
    digest = str(report.get("candidateDigest") or "").strip()
    case_id = str(report.get("caseId") or "").strip()
    return [
        *([f"api:{operation_id}"] if operation_id else []),
        *([f"test:{digest}:{case_id}"] if digest and case_id else []),
    ]


def _trace_hints(
    testing_input: TestingInput,
    dynamic: dict[str, Any],
    target_ids: list[str],
) -> tuple[list[str], list[str]]:
    """같은 Testing 입력의 계약·source RTM에서만 수리 힌트를 찾는다."""
    version_id = testing_input.artifact_version_ids.get(TYPE_SOURCE_CODE)
    if version_id is None:
        return [], []
    snapshot = load_file_snapshot(
        testing_input.app_id,
        TYPE_SOURCE_CODE,
        version_id=version_id,
    )
    metadata = snapshot.get("metadata") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot, dict) or snapshot.get("version_id") != version_id:
        return [], []
    implementation_rtm = (
        metadata.get("implementation_traceability")
        if isinstance(metadata, dict)
        else None
    )
    if not isinstance(implementation_rtm, dict):
        return [], []

    frozen_contracts = testing_input.contract_artifacts.model_dump(
        mode="json", exclude_none=True
    )
    source_contracts = (
        metadata.get("testing_contracts") if isinstance(metadata, dict) else None
    )
    # 새 snapshot에는 구현 시점 계약도 저장한다. 둘이 다르면 같은 파일 버전이라도
    # Testing 결과를 그 구현 trace에 붙일 근거가 없으므로 수리 범위를 제시하지 않는다.
    if isinstance(source_contracts, dict) and source_contracts != frozen_contracts:
        return [], []

    trace = project_artifact_trace(
        projection_state_from_testing_contracts(frozen_contracts),
        implementation_rtm=implementation_rtm,
        testing_result={"dynamic_functional_report": dynamic},
    )
    files: set[str] = set()
    related: set[str] = set()
    for value in target_ids:
        try:
            ref = TraceRef.parse(value)
        except ValueError:
            continue
        if ref not in trace.refs:
            continue
        related.update(
            item.format()
            for item in trace.upstream(ref)
            if item.kind in {"requirement", "use_case", "class", "operation", "api", "task"}
        )
        # UC처럼 넓은 중간 노드를 경유하면 sibling API와 모든 테스트까지 퍼질 수 있다.
        # 따라서 실패 API를 ``sourceRefs``로 직접 든 task와 그 파일만 수리 힌트로 쓴다.
        for task in trace.consumers(ref):
            if task.kind != "task":
                continue
            related.add(task.format())
            files.update(
                item.id for item in trace.consumers(task) if item.kind == "file"
            )
    return sorted(files), sorted(related)


def _finding_keys(report: dict[str, Any]) -> tuple[str, ...]:
    """형태가 다른 테스트 보고서를 비교 가능한 finding key 목록으로 정리한다."""
    findings: list[str] = []
    # 이전 결과를 읽을 때만 남아 있을 수 있는 항목이다. 새 Testing 실행은 구현 단계가
    # 이미 통과시킨 단위 테스트와 frontend build를 다시 실행하거나 판정하지 않는다.
    if "unitPassed" in report or "unitTests" in report:
        unit_passed = report.get("unitPassed")
        if unit_passed is None:
            unit_passed = (report.get("unitTests") or {}).get("passed")
        if not bool(unit_passed):
            findings.append("testing.unit-tests")
    frontend = report.get("frontendBuild") or {}
    if frontend and frontend.get("status") not in {"passed", "not_applicable"}:
        findings.append("testing.frontend-build")
    verification = report.get("verification") or {}
    for name, child in (verification.get("reports") or {}).items():
        if gate_status(child) in {"PASS", "NOT_APPLICABLE"}:
            continue
        validation_issues = (
            ((child or {}).get("validation") or {}).get("issues")
            if name == "dynamicFunctional"
            else None
        )
        reasons = validation_issues or [
            (child or {}).get("reason")
            or (child or {}).get("message")
            or verification.get("blockingReason")
            or f"{name} gate did not pass"
        ]
        # LLM이 다음 후보에서 무엇을 바꿔야 하는지 알 수 있도록 “검사 실패”라는
        # 요약 대신 실제 오류를 수리 이력에 각각 남긴다.
        findings.extend(f"testing.{name}:{reason}" for reason in map(str, reasons))
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
    run_id: str,
    testing_input: TestingInput,
    *,
    repair_history: dict[str, Any] | None = None,
    previous_findings: tuple[str, ...] = (),
    partial_result: dict[str, Any] | None = None,
    progress: TestingProgress | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """고정된 구현 snapshot으로 검사를 실행하고 결과와 수리 이력을 반환한다.

    복원한 파일의 digest를 확인한 뒤 정적 검사, 실행 통합 검사와 E2E를 수행한다. 구현
    단계에서 끝난 단위 테스트와 작은 통합 테스트는 이 서비스의 입력으로 믿고 반복하지 않는다.
    """
    ledger = RepairLedger.model_validate(repair_history or {})
    partial_result = dict(partial_result or {})
    raw_preserved_plan = partial_result.get("preservedCandidatePlan")
    preserved_test_plan = dict(raw_preserved_plan) if isinstance(raw_preserved_plan, dict) else None
    raw_case_results = partial_result.get("preservedCaseResults")
    preserved_case_results = (
        [dict(item) for item in raw_case_results if isinstance(item, dict)]
        if isinstance(raw_case_results, list)
        else []
    )

    def execute_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
        """복원한 한 snapshot 안에서 모든 검사를 끝낸다."""

        # 구현 작업이 고정한 파일 묶음을 새 임시 폴더에 한 번만 복원한다. 정적·IaC·동적
        # 검사는 아래 context가 끝날 때까지 이 폴더를 함께 사용한다.
        with (
            materialized_testing_application(testing_input) as run_root,
            langsmith_metrics.trace_metadata(
                {
                    "app_id": testing_input.app_id,
                    "implementation_job_id": testing_input.implementation_job_id,
                }
            ),
        ):
            if progress is not None:
                progress(
                    {
                        "current_node": "verification",
                        "result": (
                            {
                                "preservedCandidatePlan": preserved_test_plan,
                                "preservedCaseResults": preserved_case_results,
                            }
                            if preserved_test_plan
                            else {}
                        ),
                    }
                )

            verification = run_verification_graph(
                run_id=run_id,
                app_id=testing_input.app_id,
                application_dir=str(run_root / "application"),
                repair_history=ledger.model_dump(mode="json"),
                implementation_job_id=testing_input.implementation_job_id,
                testing_input=testing_input.model_dump(mode="json"),
                iac_expected=TYPE_IAC_CODE in testing_input.artifact_version_ids,
                # DEPLOYMENT_FILE에는 Dockerfile도 들어간다. 실제 package 필요 여부는
                # static node가 고정된 ResourcePlan과 복원 디렉터리를 보고 판단한다.
                deployment_package_expected=None,
                fixed_test_plan=preserved_test_plan,
                preserved_case_results=preserved_case_results,
            )
            aggregate = aggregate_gate_report({"verification": verification})
            report = {
                "status": "completed",
                "verification": verification,
                "passed": aggregate["passed"],
                "gateStatus": aggregate["status"],
                "gateCounts": aggregate["counts"],
                "diagnostics": list(verification["diagnostics"]),
                "testingInput": testing_input.model_dump(mode="json"),
            }
        findings = _finding_keys(report)
        dynamic = (verification.get("reports") or {}).get("dynamicFunctional") or {}
        plan_digest = str(dynamic.get("candidateDigest") or stable_digest(report))
        # 같은 plan을 새 구현물에 실행하는 것이 정상적인 회귀 흐름이다. plan digest만
        # 비교하면 실제 코드가 바뀌었어도 "같은 후보 반복"으로 오인하므로 둘을 함께 식별한다.
        execution_digest = stable_digest(
            {
                "plan": plan_digest,
                "implementation": testing_input.implementation_job_id,
            }
        )
        # 최초 실패는 이후 repair와 비교할 기준으로 기록한다. 재시도라면 결과 digest와
        # finding 집합을 이전 이력과 비교해 같은 후보 반복, 개선, 악화를 구분한다.
        if findings and not previous_findings:
            ledger.record(
                RepairAttempt(
                    stage="testing.dynamic-functional",
                    strategy_key="initial_generation",
                    input_digest=stable_digest({"run": run_id, "findings": findings}),
                    candidate_digest=execution_digest,
                    finding_keys_after=findings,
                    outcome="no_improvement",
                    detail="Initial testing run established the repair baseline.",
                )
            )
        elif previous_findings:
            repeated = any(
                attempt.candidate_digest == execution_digest
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
                    candidate_digest=execution_digest,
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
            ledger.stall_reason = ""
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
        dynamic_target_ids = _dynamic_target_ids(dynamic)
        file_hints, related_refs = _trace_hints(
            testing_input,
            dynamic,
            dynamic_target_ids,
        )
        dynamic_evidence = dynamic.get("finding")
        if not isinstance(dynamic_evidence, dict):
            dynamic_evidence = {}
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
                    "target_ids": dynamic_target_ids if is_dynamic else [],
                    "message": key.split(":", 1)[-1].replace("testing.", ""),
                    "severity": "error",
                    "repairable": inferred_class != "ENVIRONMENT_DEFECT",
                    "defect_class": inferred_class,
                    "repair_owner": inferred_owner,
                    "preserve_tests": preserve_tests if is_dynamic else True,
                    "candidate_digest": dynamic.get("candidateDigest") if is_dynamic else None,
                    "candidate_plan": dynamic.get("candidatePlan") if is_dynamic else None,
                    "file_hints": file_hints if is_dynamic else [],
                    "trace_refs": related_refs if is_dynamic else [],
                    "evidence": dict(dynamic_evidence) if is_dynamic else {},
                }
            )
        report["repair_state"] = _repair_state(ledger, passed=bool(report["passed"]))
        return report, ledger.model_dump(mode="json")

    return execute_snapshot()


def run_testing(
    app_id: str,
    implementation_job_id: str,
    *,
    run_id: str,
    previous_job: dict[str, Any] | None = None,
    preserve_test: bool = False,
    checkpoint: dict[str, Any] | None = None,
    progress: TestingProgress | None = None,
) -> dict[str, Any]:
    """Workspace command 안에서 Testing 한 회차를 실행한다.

    ``checkpoint``가 있으면 그 안의 고정 입력과 애플리케이션 검사 결과를 사용한다. 없으면
    구현 작업이 실제로 사용한 산출물을 한 번 고정한다. 이전 실패를 고치는 경우에는
    ``previous_job``의 수리 이력을 이어받으며, 구현 수리 뒤에는 ``preserve_test``로 기존
    기능 테스트 계획을 보존한다. 다만 구현물이 바뀌었다면 이전 통과 결과는 재사용하지 않고
    모든 사례를 다시 실행한다.
    """
    repair_history: dict[str, Any] = RepairLedger().model_dump(mode="json")
    previous_findings: tuple[str, ...] = ()
    partial_result: dict[str, Any] = {}

    if checkpoint is not None:
        # 재시작 뒤 최신 DB 값을 다시 조합하지 않는다. command가 시작할 때 저장한 입력이
        # 지금 실행할 구현 작업과 같은지 확인한 뒤 그대로 사용한다.
        testing_input = TestingInput.model_validate(checkpoint.get("testing_input") or {})
        if testing_input.app_id != app_id:
            raise ValueError("The Testing checkpoint does not belong to this app.")
        if testing_input.implementation_job_id != implementation_job_id:
            raise ValueError("The Testing checkpoint belongs to another implementation.")
        repair_history = dict(checkpoint.get("repair_history") or repair_history)
        previous_findings = tuple(checkpoint.get("previous_findings") or ())
        partial_result = dict(checkpoint.get("result") or {})
    else:
        try:
            implementation = implementation_worker.get_testing_input(implementation_job_id)
        except JobNotFound as error:
            raise ValueError("Unknown implementation job.") from error
        if implementation["app_id"] != app_id:
            raise ValueError("Implementation job does not belong to this app.")
        if implementation["status"] != "COMPLETED":
            raise ValueError("Implementation must be COMPLETED before testing can start.")
        try:
            testing_input = capture_testing_input(
                app_id,
                implementation_job_id,
                artifact_version_ids=implementation.get("artifact_version_ids"),
                contract_artifacts=implementation.get("contract_artifacts") or {},
            )
        except (
            ArtifactSourceUnavailable,
            ArtifactSnapshotMismatch,
            ValueError,
        ) as error:
            raise ValueError(f"Implementation artifacts are unavailable: {error}") from error

        if previous_job is not None:
            if previous_job.get("app_id") != app_id:
                raise ValueError("Previous Testing result does not belong to this app.")
            previous_result = previous_job.get("result") or {}
            if (
                previous_job.get("status") != "COMPLETED"
                or previous_result.get("passed") is not False
            ):
                raise ValueError("Only a completed failing Testing result can be repaired.")
            same_implementation = previous_job.get("implementation_job_id") == implementation_job_id
            repair_history = dict(previous_job.get("repair_history") or repair_history)
            previous_findings = _finding_keys(previous_result)
            previous_input = previous_job.get("testing_input")
            if previous_input is not None:
                fixed_previous_input = TestingInput.model_validate(previous_input)
                if same_implementation and fixed_previous_input != testing_input:
                    raise ValueError("A Testing repair must use the same implementation artifacts.")
                # 구현 수리는 새 파일 버전을 만드는 것이 정상이다. 이전 Testing 입력에
                # 계약이 실제로 기록돼 있었다면 그 계약만 유지됐는지 확인한다. 오래된 작업처럼
                # 계약이 비어 있으면 비교할 근거가 없으므로 새 구현의 고정 입력을 사용한다.
                previous_contracts = fixed_previous_input.contract_artifacts.model_dump(
                    mode="json", exclude_none=True
                )
                if (
                    not same_implementation
                    and previous_contracts
                    and fixed_previous_input.contract_artifacts != testing_input.contract_artifacts
                ):
                    raise ValueError(
                        "An implementation repair must preserve requirements and design contracts."
                    )
                if (
                    preserve_test
                    and fixed_previous_input.contract_artifacts != testing_input.contract_artifacts
                ):
                    raise ValueError(
                        "Preserved tests require the same requirements and design contracts."
                    )
            if preserve_test:
                dynamic = ((previous_result.get("verification") or {}).get("reports") or {}).get(
                    "dynamicFunctional"
                ) or {}
                preserved_plan = dynamic.get("candidatePlan")
                if not isinstance(preserved_plan, dict) or not preserved_plan:
                    raise ValueError("The previous Testing result has no executable test plan.")
                partial_result["preservedCandidatePlan"] = dict(preserved_plan)
                cases = dynamic.get("cases")
                # 구현 산출물 ID가 바뀌면 이전 통과도 회귀 검증 대상이다. 같은 구현을
                # 중단 지점부터 재개할 때만 이미 통과한 case를 건너뛴다.
                partial_result["preservedCaseResults"] = (
                    [
                        dict(item)
                        for item in cases
                        if isinstance(item, dict)
                        and str((item.get("result") or {}).get("gateStatus") or "").upper()
                        == "PASS"
                    ]
                    if same_implementation and isinstance(cases, list)
                    else []
                )

    def save_progress(state: dict[str, Any]) -> None:
        if progress is None:
            return
        progress(
            {
                "implementation_job_id": implementation_job_id,
                "testing_input": testing_input.model_dump(mode="json"),
                "current_node": state.get("current_node"),
                "result": dict(state.get("result") or {}),
                "repair_history": repair_history,
                "previous_findings": list(previous_findings),
            }
        )

    # 파일 복원이나 도구 실행 전에 고정 입력을 저장한다. 서버가 여기서 중단되어도 다음
    # 실행은 같은 산출물 ID와 계약 digest를 사용한다.
    save_progress({"current_node": "queued", "result": partial_result})
    report, completed_history = _run_test(
        run_id,
        testing_input,
        repair_history=repair_history,
        previous_findings=previous_findings,
        partial_result=partial_result,
        progress=save_progress,
    )
    return {
        "job_id": run_id,
        "app_id": app_id,
        "implementation_job_id": implementation_job_id,
        "status": "COMPLETED",
        "current_node": "completed",
        "testing_input": testing_input.model_dump(mode="json"),
        "result": report,
        "repair_history": completed_history,
    }
