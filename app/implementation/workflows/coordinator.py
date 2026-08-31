from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.design.contracts import bind_runtime_contract, build_provider_resource_plan
from app.metrics import langsmith as langsmith_metrics

from ..agents.runtime import execute_openhands_task
from ..agents.verification.build import (
    WorkspaceVerificationError,
    verify_run_workspace,
)
from ..agents.verification.release import verify_container_runtime
from ..delivery.container import render_deployment, render_local_container
from ..delivery.terraform import render_iac
from ..domain.implementation_ir import (
    assess_bce_erd_entity_contract,
    build_implementation_ir,
)
from ..domain.models import JobSpec
from ..generation.orchestrator import (
    plan_api_adapter_tasks,
    plan_frontend_tasks,
    plan_persistence_tasks,
    plan_wiring_tasks,
)
from ..runtime.observations import observe_runtime_contract
from .completion import audit_run_completion
from .conformance import (
    SourceDesignConformanceError,
    verify_source_design_conformance,
)
from .release import write_release_manifest
from .repair import (
    apply_repair_directives,
    repair_task_ids,
    schedule_cross_phase_repair,
    schedule_source_conformance_repair,
)
from .traceability import build_rtm_traceability_map

WORKFLOW_SCHEMA = "implementation-workflow/v1alpha1"
TRANSMISSION_SCHEMA = "external-transmission-request/v1alpha1"
PHASES = (
    ("persistence", (), {"persistence"}),
    # ``control`` remains only for the existing feedback-revision task; newly
    # planned implementation work always uses the broader ``use-case`` type.
    ("use-cases", ("persistence",), {"use-case", "control"}),
    ("frontend", ("use-cases",), {"frontend-implementation"}),
    ("wiring", ("use-cases", "frontend"), {"wiring"}),
)

PHASE_LABELS = {
    "persistence": "공통 Persistence",
    "use-cases": "유스케이스 Backend",
    "wiring": "Application Setup",
    "frontend": "Frontend",
}

def plan_workflow(run_root: Path, spec: JobSpec) -> dict[str, object]:
    """Idempotently plan implemented phases and persist a resumable checkpoint."""
    run_root = run_root.resolve()
    if spec.job_type == "FEEDBACK_REVISION":
        apply_repair_directives(run_root)
        return reconcile_workflow_state(run_root)
    ir = build_implementation_ir(spec, run_root)
    erd_model_path = spec.inputs.get("erdBceModel")
    erd_model = _read_json(erd_model_path) if erd_model_path and erd_model_path.is_file() else {}
    bce_entities = set(ir.entities)
    contract = assess_bce_erd_entity_contract(erd_model, bce_entities)
    if bce_entities and not contract.erd_entities:
        raise ValueError(
            "erdBceModel must contain Entity definitions matching bceModel"
        )
    unexpected_erd_entities = set(contract.unexpected_erd_entities)
    missing_erd_entities = set(contract.missing_bce_entities)
    if missing_erd_entities or unexpected_erd_entities:
        missing_in_erd = sorted(missing_erd_entities)
        missing_in_bce = sorted(unexpected_erd_entities)
        details = []
        if missing_in_erd:
            details.append("missing in ERD: " + ", ".join(missing_in_erd))
        if missing_in_bce:
            details.append("missing in BCE: " + ", ".join(missing_in_bce))
        raise ValueError("bceModel/erdBceModel Entity mismatch; " + "; ".join(details))
    needs_persistence = bool(bce_entities) or any(
        gateway.kind == "persistence" for gateway in ir.gateways
    )
    if needs_persistence:
        if erd_model_path is None or not erd_model_path.is_file():
            raise ValueError(
                "erdBceModel is required because bceModel contains persistent Entity classes"
            )
        plan_persistence_tasks(spec, run_root)
    plan_api_adapter_tasks(spec, run_root)
    plan_wiring_tasks(spec, run_root)
    if (run_root / "application" / "frontend" / "src" / "generated").is_dir():
        plan_frontend_tasks(spec, run_root)
    build_rtm_traceability_map(spec, run_root)
    apply_repair_directives(run_root)
    return reconcile_workflow_state(run_root)


def reconcile_workflow_state(run_root: Path) -> dict[str, object]:
    run_root = run_root.resolve()
    manifest = _read_json(run_root / "reports" / "run-manifest.json")
    state_path = run_root / "reports" / "workflow-state.json"
    previous = _read_json(state_path) if state_path.is_file() else {}
    previous_tasks = {
        item.get("taskId"): item for item in previous.get("tasks", [])
        if isinstance(item, dict)
    }
    repaired_tasks = repair_task_ids(run_root)
    tasks: list[dict[str, object]] = []
    for task in manifest.get("implementation_tasks", []):
        task_id = str(task["task_id"])
        prompt_sha = str(task.get("prompt_sha256", ""))
        phase = phase_for_task(str(task.get("task_type", "control")))
        old = previous_tasks.get(task_id, {})
        result_path = (
            run_root / "reports" / "agent-executions" / f"{task_id}.result.json"
        )
        result = _read_json(result_path) if result_path.is_file() else {}
        required_outputs = task.get("required_output_paths", task.get("allowed_write_paths", []))
        output_hashes = _output_hashes(run_root, required_outputs)
        complete_outputs = len(output_hashes) == len(required_outputs)
        # 여러 유스케이스 작업이 Controller나 Boundary adapter를 순서대로 보완한다.
        # 따라서 뒤 작업이 공유 파일을 정상적으로 수정한 뒤에는 앞 작업의 output hash가
        # 달라지는 것이 자연스럽다. 이미 성공한 작업은 필요한 파일이 남아 있는지만
        # 확인하고 재사용한다. 최종 내용의 정확성은 마지막 scenario와 build가 검사한다.
        result_matches = (
            result.get("status") == "SUCCEEDED"
            and complete_outputs
            and result.get("promptSha256", prompt_sha) == prompt_sha
        )
        repair_replay_required = (
            task_id in repaired_tasks
            and result.get("promptSha256") != prompt_sha
        )
        repair_only = bool(task.get("repair_only", False))
        if repair_only and task_id not in repaired_tasks:
            # 정상 실행에서는 정형적인 Spring 설정을 generator와 각 기능 작업이 만든다.
            # 최종 검사가 실제 연결 오류를 찾았을 때만 repair plan이 이 작업을 깨운다.
            status = "SUCCEEDED"
        elif old.get("status") == "RUNNING":
            status = (
                "SUCCEEDED"
                if result.get("status") == "SUCCEEDED"
                and complete_outputs
                and not repair_replay_required
                else "INTERRUPTED"
            )
        elif (
            old.get("status") == "SUCCEEDED"
            and complete_outputs
            and not repair_replay_required
        ) or (result_matches and not old):
            status = "SUCCEEDED"
        elif (
            result.get("status") == "FAILED"
            and result.get("promptSha256", prompt_sha) == prompt_sha
        ):
            status = "FAILED"
        else:
            status = "PENDING"
        tasks.append(
            {
                "taskId": task_id,
                "taskType": str(task.get("task_type", "control")),
                "phase": phase,
                # 같은 phase 안에서도 여러 유스케이스가 같은 Control이나 adapter 파일을
                # 고칠 수 있다. planner가 남긴 순서와 편집 범위를 실행 상태에도 보존해야
                # coordinator가 충돌하는 작업을 동시에 실행하지 않는다.
                "dependsOn": [
                    str(item)
                    for item in task.get("depends_on", task.get("dependsOn", []))
                ],
                "allowedWritePaths": [
                    str(item) for item in task.get("allowed_write_paths", [])
                ],
                "allowedWriteRoots": [
                    str(item) for item in task.get("allowed_write_roots", [])
                ],
                "status": status,
                "promptSha256": prompt_sha,
                "outputHashes": output_hashes,
                "attempts": int(old.get("attempts", 0)),
                "resultFile": (
                    result_path.relative_to(run_root).as_posix()
                    if result_path.is_file() else None
                ),
                "lastError": result.get("error") if result.get("status") == "FAILED" else None,
            }
        )

    phases = _phase_states(tasks)
    current = next(
        (
            phase["phaseId"] for phase in phases
            if phase["status"] in {"PENDING", "RUNNING", "FAILED"}
        ),
        next(
            (phase["phaseId"] for phase in phases if phase["status"] == "UNPLANNED"),
            None,
        ),
    )
    pending = [task for task in tasks if task["status"] != "SUCCEEDED"]
    status = (
        "FAILED" if any(task["status"] == "FAILED" for task in pending)
        else ("READY" if pending else "READY_TO_FINALIZE")
    )
    state: dict[str, object] = {
        "schemaVersion": WORKFLOW_SCHEMA,
        "runId": run_root.name,
        "status": status,
        "currentPhase": current,
        "updatedAt": _now(),
        "phases": phases,
        "tasks": tasks,
        "nextRunnableTasks": _next_runnable_tasks(tasks, phases),
        "blockingReason": None,
        "blockingDetails": [],
        "approval": previous.get("approval"),
    }
    _write_json_atomic(state_path, state)
    request = write_transmission_request(run_root, state)
    state["transmissionRequest"] = request[
        "requestFile"
    ] if request else None
    _write_json_atomic(state_path, state)
    return state


def run_workflow(
    run_root: Path,
    spec: JobSpec,
    approval_path: Path | None,
    *,
    retry_failed: bool = False,
    executor: Callable[[Path, str], dict[str, object]] = execute_openhands_task,
    verifier: Callable[[Path], dict[str, object]] = verify_run_workspace,
    auditor: Callable[[Path], dict[str, object]] = audit_run_completion,
) -> dict[str, object]:
    """Trace the implementation workflow independently of its caller process."""

    with langsmith_metrics.trace_scope(
        "easydep.implementation.workflow",
        metadata={
            "agent": "implementation",
            "operation": "workflow",
            "run_id": run_root.name,
            "app_id": spec.app_id,
        },
    ):
        return _run_workflow(
            run_root,
            spec,
            approval_path,
            retry_failed=retry_failed,
            executor=executor,
            verifier=verifier,
            auditor=auditor,
        )


def _run_workflow(
    run_root: Path,
    spec: JobSpec,
    approval_path: Path | None,
    *,
    retry_failed: bool = False,
    executor: Callable[[Path, str], dict[str, object]] = execute_openhands_task,
    verifier: Callable[[Path], dict[str, object]] = verify_run_workspace,
    auditor: Callable[[Path], dict[str, object]] = audit_run_completion,
) -> dict[str, object]:
    """Resume planned phases, checkpointing before and after every external task."""
    run_root = run_root.resolve()
    state = plan_workflow(run_root, spec)
    runnable = list(state.get("nextRunnableTasks", []))
    failed_runnable = [
        task_id for task_id in runnable
        if next(task for task in state["tasks"] if task["taskId"] == task_id)["status"] == "FAILED"
    ]
    if failed_runnable and not retry_failed:
        raise RuntimeError(
            "Workflow has failed tasks; inspect evidence and use --retry-failed: "
            + ", ".join(failed_runnable)
        )
    if not runnable:
        return _finalize_workflow(
            run_root,
            spec,
            state,
            verifier=verifier,
            auditor=auditor,
        )

    request = _read_json(
        run_root / "reports" / "external-transmission-request.json"
    )
    approval = validate_workflow_approval(
        approval_path, request, state, run_root
    )
    approval["authorizedTaskIds"] = sorted(
        str(item["taskId"]) for item in request.get("tasks", [])
    )
    authorized_task_ids = set(approval["authorizedTaskIds"])
    state["approval"] = approval
    state["status"] = "RUNNING"
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)

    phase_order = [item[0] for item in PHASES]
    for phase_id in phase_order:
        phase_tasks = [
            task for task in state["tasks"]
            if task["phase"] == phase_id
            and task["taskId"] in authorized_task_ids
            and (
                task["status"] in {"PENDING", "INTERRUPTED"}
                or (retry_failed and task["status"] == "FAILED")
            )
        ]
        if not phase_tasks:
            continue
        if not _dependencies_succeeded(state, phase_id):
            continue
        state["currentPhase"] = phase_id
        # planner가 같은 source와 package를 공유하는 작업을 한 묶음으로 만들고, 아래 batch
        # 검사도 편집 범위가 겹치는 작업을 분리한다. 따라서 독립 기능은 설정된 범위 안에서
        # 병렬 실행해도 같은 파일을 덮어쓰지 않는다.
        worker_limit = max(1, int(settings.implementation_task_parallelism))
        for task_batch in _phase_task_batches(
            phase_id,
            phase_tasks,
        ):
            failures = _execute_task_batch(
                run_root,
                state,
                task_batch,
                executor,
                max_workers=worker_limit,
            )
            if failures:
                task, error = failures[0]
                if isinstance(error, WorkspaceVerificationError):
                    repair = schedule_cross_phase_repair(
                        run_root, str(task["taskId"]), error.evidence
                    )
                    if repair is not None:
                        repaired_state = plan_workflow(run_root, spec)
                        repaired_state["repairPlan"] = "reports/repair-plan.json"
                        repaired_state["blockingReason"] = None
                        _write_json_atomic(
                            run_root / "reports" / "workflow-state.json",
                            repaired_state,
                        )
                        return repaired_state
                raise error
        next(
            phase for phase in state["phases"] if phase["phaseId"] == phase_id
        )["status"] = "SUCCEEDED"
        state["updatedAt"] = _now()
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)

    # A completed work unit can change source contracts embedded in the
    # downstream wiring prompt.
    # Re-plan before emitting the next approval request so its hash never
    # describes stale pre-phase context.
    final_state = plan_workflow(run_root, spec)
    if final_state.get("nextRunnableTasks"):
        # Every work unit performs its own focused verification.  Do not scan
        # the incomplete application after each approval; the final audit and
        # full workspace build run only when no work unit remains.
        final_state["status"] = "READY"
        final_state["blockingReason"] = None
        final_state.pop("currentActivity", None)
        _write_json_atomic(run_root / "reports" / "workflow-state.json", final_state)
        return final_state
    return _finalize_workflow(
        run_root,
        spec,
        final_state,
        verifier=verifier,
        auditor=auditor,
    )


def _finalize_workflow(
    run_root: Path,
    spec: JobSpec,
    state: dict[str, object],
    *,
    verifier: Callable[[Path], dict[str, object]],
    auditor: Callable[[Path], dict[str, object]],
) -> dict[str, object]:
    """모든 작업이 끝난 실행을 한 경로에서 검사하고 release한다."""
    state["status"] = "VERIFYING"
    state["blockingReason"] = None
    state["currentActivity"] = {
        "id": "completion-audit",
        "label": "최종 구현 결과 확인",
        "status": "RUNNING",
        "detail": "전체 구현 결과를 확인하고 빌드·테스트하고 있습니다.",
    }
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)

    try:
        audit = auditor(run_root)
    except Exception as error:
        _record_workflow_failure(run_root, state, error)
        raise
    state["audit"] = "reports/implementation-completion-audit.json"
    if audit.get("status") != "COMPLETE":
        repaired = _continue_after_incomplete_audit(run_root, spec, audit)
        if repaired is not None:
            return repaired
        state["status"] = "NEEDS_PLANNER"
        state["blockingReason"] = (
            "The audit contains work for which no implementation task exists."
        )
        state.pop("currentActivity", None)
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        return state

    state["currentActivity"] = {
        "id": "release-verification",
        "label": "최종 릴리스 검증",
        "status": "RUNNING",
        "detail": "설계 계약, 전체 test와 실제 container 응답을 확인하고 있습니다.",
    }
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
    try:
        verification = verifier(run_root)
    except Exception as error:
        repaired = _continue_after_verification_failure(
            run_root,
            spec,
            failure_id="verify-final-workspace",
            failed_task_type="wiring",
            error=error,
        )
        if repaired is not None:
            return repaired
        _record_workflow_failure(run_root, state, error)
        raise
    try:
        conformance = verify_source_design_conformance(run_root, spec)
    except SourceDesignConformanceError as error:
        repaired = _continue_after_conformance_failure(run_root, spec, error)
        if repaired is not None:
            return repaired
        _record_workflow_failure(run_root, state, error)
        raise
    try:
        _complete_release(
            run_root, spec, state, audit, verification, conformance
        )
    except WorkspaceVerificationError as error:
        repaired = _continue_after_verification_failure(
            run_root,
            spec,
            failure_id="verify-container-runtime",
            failed_task_type="wiring",
            error=error,
        )
        if repaired is not None:
            return repaired
        _record_workflow_failure(run_root, state, error)
        raise

    state["verification"] = verification.get("status")
    state["blockingReason"] = None
    state["currentActivity"] = {
        "id": "release-verification",
        "label": "최종 릴리스 검증",
        "status": "SUCCEEDED",
    }
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
    return state


def _phase_task_batches(
    phase_id: str,
    tasks: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    """같은 phase의 독립 작업만 한 batch로 묶는다.

    요구사항 묶음은 가능한 한 병렬 실행하지만, planner가 명시한 선행 작업이나 편집 파일이
    겹치면 순서대로 실행한다. 별도의 스케줄러를 만들지 않고 작은 위상 정렬과 경로 충돌
    검사만 사용하므로 실행 규칙이 manifest에서 바로 보인다.
    """
    del phase_id
    remaining = {str(task["taskId"]): task for task in tasks}
    batches: list[list[dict[str, object]]] = []
    completed_in_phase: set[str] = set()
    while remaining:
        ready = [
            task
            for task_id, task in remaining.items()
            if all(
                str(dependency) not in remaining
                or str(dependency) in completed_in_phase
                for dependency in task.get("dependsOn", [])
            )
        ]
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise ValueError(f"Implementation task dependency cycle: {cycle}")

        batch: list[dict[str, object]] = []
        occupied: set[str] = set()
        for task in ready:
            paths = {
                str(path).replace("\\", "/")
                for path in [
                    *task.get("allowedWritePaths", []),
                    *task.get("allowedWriteRoots", []),
                ]
            }
            if batch and _write_scopes_overlap(occupied, paths):
                continue
            batch.append(task)
            occupied.update(paths)
        batches.append(batch)
        for task in batch:
            task_id = str(task["taskId"])
            completed_in_phase.add(task_id)
            remaining.pop(task_id, None)
    return batches


def _write_scopes_overlap(left: set[str], right: set[str]) -> bool:
    """파일 경로나 디렉터리 범위가 같은 source를 가리키는지 확인한다."""
    return any(
        a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")
        for a in left
        for b in right
    )


def _execute_task_batch(
    run_root: Path,
    state: dict[str, object],
    tasks: list[dict[str, object]],
    executor: Callable[[Path, str], dict[str, object]],
    *,
    max_workers: int,
) -> list[tuple[dict[str, object], Exception]]:
    """Execute a safe batch concurrently and checkpoint results deterministically."""
    state_path = run_root / "reports" / "workflow-state.json"
    workers = min(max(1, max_workers), len(tasks))
    for task in tasks:
        # A task is marked RUNNING only immediately before it is submitted to
        # an available worker.  In particular, do not increment attempts for
        # queued tasks: a pending task has not started yet.
        task["status"] = "PENDING"
    state["updatedAt"] = _now()
    _write_json_atomic(state_path, state)

    def run(task: dict[str, object]) -> dict[str, object]:
        result = executor(run_root, str(task["taskId"]))
        if result.get("status") != "SUCCEEDED":
            raise RuntimeError(
                f"Task returned non-success status: {task['taskId']}"
            )
        return result

    def mark_started(task: dict[str, object]) -> None:
        task["status"] = "RUNNING"
        task["attempts"] = int(task.get("attempts", 0)) + 1
        state["updatedAt"] = _now()
        _write_json_atomic(state_path, state)

    failures: list[tuple[dict[str, object], Exception]] = []

    def record_completion(
        task: dict[str, object], future: Future[dict[str, object]]
    ) -> None:
        try:
            future.result()
        except Exception as error:
            task["status"] = "FAILED"
            task["lastError"] = str(error)
            failures.append((task, error))
        else:
            task["status"] = "SUCCEEDED"
            task["resultFile"] = (
                f"reports/agent-executions/{task['taskId']}.result.json"
            )
            task["outputHashes"] = _task_output_hashes(
                run_root, str(task["taskId"])
            )
            task["lastError"] = None
        state["updatedAt"] = _now()
        _write_json_atomic(state_path, state)

    if workers == 1:
        # Preserve the original calling-thread behavior when parallelism is
        # disabled or a dependency/overlap reduced this to a singleton batch.
        for task in tasks:
            mark_started(task)
            future: Future[dict[str, object]] = Future()
            try:
                future.set_result(run(task))
            except Exception as error:
                future.set_exception(error)
            record_completion(task, future)
    else:
        next_index = 0
        active: dict[Future[dict[str, object]], dict[str, object]] = {}

        def submit_next(pool: ThreadPoolExecutor) -> None:
            nonlocal next_index
            if next_index >= len(tasks):
                return
            task = tasks[next_index]
            next_index += 1
            mark_started(task)
            active[pool.submit(run, task)] = task

        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="easydep-implementation-task"
        ) as pool:
            # Keep no more than ``workers`` futures submitted at once.  This
            # makes the durable PENDING/RUNNING state reflect actual execution
            # instead of the executor's private unbounded queue.
            for _ in range(workers):
                submit_next(pool)
            while active:
                completed, _ = wait(active, return_when=FIRST_COMPLETED)
                # Completion order is nondeterministic; process a group in
                # manifest order so the checkpoint and selected error remain
                # stable across runs.
                done_tasks = sorted(
                    ((future, active[future]) for future in completed),
                    key=lambda item: tasks.index(item[1]),
                )
                for future, task in done_tasks:
                    active.pop(future, None)
                    record_completion(task, future)
                # Refill only after every completed task in this checkpoint
                # group has transitioned out of RUNNING.  Otherwise a fast
                # completion group could briefly show a finished task and its
                # replacement as RUNNING at the same time in the UI.
                for _ in done_tasks:
                    submit_next(pool)

    blocking_failures = failures
    if blocking_failures:
        failed_task, _ = blocking_failures[0]
        state["status"] = "FAILED"
        state["blockingReason"] = f"Task failed: {failed_task['taskId']}"
        state["updatedAt"] = _now()
        _write_json_atomic(state_path, state)
    return blocking_failures


def _record_workflow_failure(
    run_root: Path, state: dict[str, object], error: Exception
) -> None:
    """Persist a verifier/auditor failure before propagating it to the job."""
    activity = state.get("currentActivity")
    failed_activity = dict(activity) if isinstance(activity, dict) else {}
    activity_id = str(failed_activity.get("id") or "")
    phase_id = activity_id.removeprefix("verify-").removeprefix("audit-")
    if phase_id in PHASE_LABELS:
        for phase in state.get("phases", []):
            if isinstance(phase, dict) and phase.get("phaseId") == phase_id:
                phase["status"] = "FAILED"
                break

    detail = (str(error).strip() or type(error).__name__)[-1000:]
    label = str(failed_activity.get("label") or "구현 결과 확인")
    failed_activity.update(
        {
            "id": activity_id or "workflow-verification",
            "label": label,
            "status": "FAILED",
            "detail": f"{label} 실패: {detail}",
        }
    )
    state["currentActivity"] = failed_activity
    state["status"] = "FAILED"
    state["blockingReason"] = failed_activity["detail"]
    state["updatedAt"] = _now()
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)


def _continue_after_verification_failure(
    run_root: Path,
    spec: JobSpec,
    *,
    failure_id: str,
    failed_task_type: str,
    error: Exception,
) -> dict[str, object] | None:
    """통합 compile/test 실패를 wiring 통합 수리 작업으로 되돌린다.

    작업 안에서 난 오류는 OpenHands가 바로 수리하지만, phase 또는 최종 검증은 여러 작업을
    함께 빌드하므로 예전에는 Job 전체가 즉시 실패했다. 검증기가 남긴 구조화된 근거가 있을
    때만 wiring 작업에 넘기고, 원인을 찾지 못한 예외는 원래대로 호출자에게 전달한다.
    """

    if not isinstance(error, WorkspaceVerificationError):
        return None
    repair = schedule_cross_phase_repair(
        run_root,
        failure_id,
        error.evidence,
        failed_task_type=failed_task_type,
    )
    if repair is None:
        return None
    state = plan_workflow(run_root, spec)
    state["repairPlan"] = "reports/repair-plan.json"
    state["blockingReason"] = None
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
    return state


def _continue_after_incomplete_audit(
    run_root: Path,
    spec: JobSpec,
    audit: dict[str, object],
) -> dict[str, object] | None:
    """마지막 감사가 기존 task의 부족한 산출물을 찾으면 그 task부터 다시 실행한다.

    감사 결과에는 이미 담당 ``task_id``가 들어 있다. 새 planner나 사용자 선택을
    요구하지 않고, 해당 task에 감사 근거를 붙인 뒤 기존 승인 범위에서 자동 수리한다.
    아직 구현 task가 없는 새 종류의 작업만 기존 ``NEEDS_PLANNER`` 상태로 남는다.
    """

    backlog = audit.get("backlog")
    if not isinstance(backlog, list):
        return None
    for item in backlog:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        if not task_id:
            continue
        repair = schedule_cross_phase_repair(
            run_root,
            task_id,
            {
                "command": ["completion-audit"],
                "exitCode": 1,
                "stderr": json.dumps(item, ensure_ascii=False, indent=2),
            },
        )
        if repair is None:
            continue
        state = plan_workflow(run_root, spec)
        state["repairPlan"] = "reports/repair-plan.json"
        state["blockingReason"] = None
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        return state
    return None


def workflow_status(run_root: Path) -> dict[str, object]:
    path = run_root.resolve() / "reports" / "workflow-state.json"
    if not path.is_file():
        raise ValueError("Workflow has not been planned for this run")
    return _read_json(path)


def run_workflow_to_completion(
    run_root: Path,
    spec: JobSpec,
    *,
    approved_by: str,
    retry_failed: bool = False,
    max_cycles: int | None = None,
) -> dict[str, object]:
    """한 번의 위임 승인으로 완료 또는 명확한 중단 상태까지 실행한다.

    기본 실행에는 repair 횟수 상한이 없다. 각 repair 계획은 실패 지문, 수정 전 코드와 사용한
    전략을 저장하며, 같은 결과가 반복되어도 이 이력을 다음 요청에 포함해 다른 수정을 시도한다.
    ``max_cycles``는 테스트와 멤버 프로세스가 승인 한 주기만 실행할 때 쓰는 선택 사항이다.
    """
    run_root = run_root.resolve()
    request_path = run_root / "reports" / "external-transmission-request.json"
    approval_path = run_root / "reports" / "one-time-run-approval.json"
    cycle = 0
    while True:
        cycle += 1
        state = plan_workflow(run_root, spec)
        if state.get("status") == "COMPLETE":
            return state
        request = _read_json(request_path) if request_path.is_file() else {}
        execution_approval: Path | None = None
        if request.get("status") == "AWAITING_APPROVAL":
            manifest = _read_json(run_root / "reports" / "run-manifest.json")
            approval = {
                "requestId": request["requestId"],
                "approved": True,
                "approvedAt": _now(),
                "approvedBy": approved_by,
                "delegatedRepairApprovals": True,
                "delegationScope": {
                    "runId": run_root.name,
                    "inputHash": manifest.get("input_hash"),
                    "initialTaskIds": sorted(
                        str(task["task_id"])
                        for task in manifest.get("implementation_tasks", [])
                    ),
                },
            }
            _write_json_atomic(approval_path, approval)
            execution_approval = approval_path
        elif state.get("status") != "READY_TO_FINALIZE":
            raise PermissionError(
                "No external transmission request is available to approve"
            )
        state = run_workflow(
            run_root,
            spec,
            execution_approval,
            retry_failed=retry_failed,
        )
        status = str(state.get("status", ""))
        if status == "COMPLETE":
            if approval_path.is_file():
                state["oneTimeApproval"] = approval_path.relative_to(
                    run_root
                ).as_posix()
            _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
            return state
        if status in {"FAILED", "NEEDS_INPUT", "NEEDS_PLANNER"}:
            raise RuntimeError(
                f"Run-to-completion stopped in {status}: {state.get('blockingReason')}"
            )
        if max_cycles is not None and cycle >= max_cycles:
            raise RuntimeError(
                f"Run-to-completion exceeded {max_cycles} workflow cycles"
            )


def _bind_deployment_runtime(run_root: Path, spec: JobSpec) -> Path | None:
    """완료된 배포 설계에 생성 앱의 실제 실행값을 넣어 새 bundle을 만든다.

    원래 설계 파일은 입력 snapshot이므로 수정하지 않는다. 배포 설계가 아직 질문을
    남긴 상태라면 로컬 Docker 검증만 계속하고, 완료된 설계에서 실행 계약이 다르면
    IaC를 만들지 않고 구현 오류로 보고한다.
    """
    source = spec.inputs.get("deploymentBundle")
    if source is None or not source.is_file():
        return None
    try:
        bundle = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Deployment bundle could not be read: {error}") from error
    if not isinstance(bundle, dict) or bundle.get("schemaVersion") != "easydep-deployment-diagram":
        raise ValueError("Implementation requires a valid deployment diagram bundle")

    reports = run_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / "deployment-runtime.json"
    observed = observe_runtime_contract(bundle, run_root / "application")
    if bundle.get("status") != "completed":
        _write_json_atomic(
            report_path,
            {
                "schemaVersion": "easydep-implementation-runtime/v1alpha1",
                "status": "NOT_APPLICABLE",
                "reason": "Deployment design still needs input; local container verification continues.",
                "runtimeContracts": observed,
            },
        )
        return None

    graph = bundle.get("workloadGraph")
    projections = bundle.get("projections")
    if not isinstance(graph, dict) or not isinstance(projections, list) or not projections:
        raise ValueError("Completed deployment bundle has no workload graph or projections")
    rebound: list[dict[str, object]] = []
    bound_graph: dict[str, object] | None = None
    for projection in projections:
        if not isinstance(projection, dict) or projection.get("status") != "completed":
            raise ValueError("Completed deployment bundle contains an incomplete projection")
        deployment_plan = projection.get("deploymentPlan")
        if not isinstance(deployment_plan, dict):
            raise TypeError("Deployment projection has no deployment plan")
        binding = bind_runtime_contract(graph, deployment_plan, observed)
        if binding.get("status") != "bound":
            issues = binding.get("issues") or []
            _write_json_atomic(
                report_path,
                {
                    "schemaVersion": "easydep-implementation-runtime/v1alpha1",
                    "status": "FAILED",
                    "runtimeContracts": observed,
                    "issues": issues,
                },
            )
            reasons = [str(item.get("reason") or item) for item in issues if isinstance(item, dict)]
            raise RuntimeError(
                "Generated application does not satisfy the deployment runtime contract: "
                + "; ".join(reasons or ["unknown runtime mismatch"])
            )
        current_graph = binding.get("workloadGraph")
        current_plan = binding.get("deploymentPlan")
        if not isinstance(current_graph, dict) or not isinstance(current_plan, dict):
            raise TypeError("Runtime binding returned no bound graph or deployment plan")
        resource_plan = build_provider_resource_plan(
            current_plan,
            current_graph,
            provider=str(projection.get("provider") or ""),
            region=str(projection.get("region") or ""),
        )
        previous_digest = str(projection.get("resourcePlanStructureDigest") or "")
        current_digest = str(resource_plan.get("structureDigest") or "")
        if previous_digest and previous_digest != current_digest:
            raise RuntimeError("Runtime binding changed the ResourcePlan structure")
        rebound.append(
            {
                **projection,
                "deploymentPlan": current_plan,
                "deploymentPlanStructureDigest": current_plan.get("structureDigest"),
                "resourcePlan": resource_plan,
                "resourcePlanStructureDigest": current_digest,
                "issues": [],
            }
        )
        bound_graph = current_graph

    bound_bundle = {**bundle, "workloadGraph": bound_graph or graph, "projections": rebound}
    target = reports / "runtime-bound-deployment-bundle.json"
    _write_json_atomic(target, bound_bundle)
    _write_json_atomic(
        report_path,
        {
            "schemaVersion": "easydep-implementation-runtime/v1alpha1",
            "status": "BOUND",
            "runtimeContracts": observed,
            "boundBundle": target.relative_to(run_root).as_posix(),
        },
    )
    return target


def _render_deployment_if_configured(
    run_root: Path, spec: JobSpec
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    intent = spec.inputs.get("deploymentIntent")
    cloud = spec.inputs.get("cloud")
    deployment = spec.inputs.get("deployment")
    deployment_bundle = spec.inputs.get("deploymentBundle")
    has_bundle = bool(deployment_bundle and deployment_bundle.is_file())
    if has_bundle:
        # observer가 실제 EXPOSE와 실행 사용자를 읽을 수 있도록 먼저 결정론적인
        # 로컬 Dockerfile을 만든다. 이후 IaC는 같은 파일과 bound bundle을 사용한다.
        render_local_container(run_root)
    bound_bundle = _bind_deployment_runtime(run_root, spec) if has_bundle else None
    # 현재 제품 경로는 Docker-on-VM ResourcePlan이다. 구조화된 bundle이 있으면
    # 예전 Kubernetes cloud inference를 함께 실행하지 않는다.
    if not has_bundle and ((intent and intent.is_file()) or (cloud and cloud.is_file())):
        deployment_report = render_deployment(run_root, spec)
    elif deployment and deployment.is_file() and not (
        deployment_bundle and deployment_bundle.is_file()
    ):
        raise ValueError(
            "Deployment rendering requires deploymentIntent or a cloud resource "
            "specification"
        )
    else:
        deployment_report = None
    iac_report = None
    if bound_bundle is not None:
        iac_spec = replace(
            spec,
            inputs={**spec.inputs, "deploymentBundle": bound_bundle},
        )
        iac_report = render_iac(run_root, iac_spec)
    elif not has_bundle and cloud and cloud.is_file():
        iac_report = render_iac(run_root, spec)
    return deployment_report, iac_report


def _complete_release(
    run_root: Path,
    spec: JobSpec,
    state: dict[str, object],
    audit: dict[str, object],
    verification: dict[str, object],
    conformance: dict[str, object],
) -> None:
    state["status"] = "COMPLETE"
    state["verification"] = verification.get("status")
    state["sourceDesignConformance"] = conformance.get("status")
    try:
        deployment, iac = _render_deployment_if_configured(run_root, spec)
        if deployment is None:
            render_local_container(run_root)
        traceability = build_rtm_traceability_map(spec, run_root)
        container_smoke = verify_container_runtime(run_root)
        release = write_release_manifest(
            run_root,
            workflow=state,
            audit=audit,
            verification=verification,
            conformance=conformance,
            traceability=traceability,
            deployment=deployment,
            iac=iac,
            container_smoke=container_smoke,
        )
        if release["status"] != "RELEASABLE":
            raise RuntimeError(
                "Release verification failed: "
                + ", ".join(str(item) for item in release["failedChecks"])
            )
    except Exception as error:
        state["status"] = "FAILED"
        state["blockingReason"] = str(error)
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        raise
    state["releaseManifest"] = "reports/release-manifest.json"


def _continue_after_conformance_failure(
    run_root: Path,
    spec: JobSpec,
    error: SourceDesignConformanceError,
) -> dict[str, object] | None:
    """공개 계약 오류를 사용자 버튼 대신 기존 기능 작업의 수리로 되돌린다."""
    repair = schedule_source_conformance_repair(run_root, error.report)
    if repair is None:
        return None
    state = plan_workflow(run_root, spec)
    state["repairPlan"] = "reports/repair-plan.json"
    state["blockingReason"] = None
    state.pop("currentActivity", None)
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
    return state


def write_transmission_request(
    run_root: Path, state: dict[str, object]
) -> dict[str, object] | None:
    next_runnable = state.get("nextRunnableTasks")
    if next_runnable is not None:
        pending_ids = sorted(str(task_id) for task_id in next_runnable)
    else:
        pending_ids = sorted(
            str(task["taskId"]) for task in state["tasks"]
            if task["status"] in {"PENDING", "INTERRUPTED", "FAILED"}
        )
    if not pending_ids:
        request_path = run_root / "reports" / "external-transmission-request.json"
        if request_path.is_file():
            previous = _read_json(request_path)
            if previous.get("status") == "AWAITING_APPROVAL":
                previous["status"] = "CLOSED"
                previous["closedAt"] = _now()
                _write_json_atomic(request_path, previous)
        return None
    manifest = _read_json(run_root / "reports" / "run-manifest.json")
    task_by_id = {
        item["task_id"]: item for item in manifest.get("implementation_tasks", [])
    }
    payload = _transmission_payload(task_by_id, pending_ids)
    request_id = _transmission_request_id(payload)
    request = {
        "schemaVersion": TRANSMISSION_SCHEMA,
        "requestId": request_id,
        "status": "AWAITING_APPROVAL",
        "createdAt": _now(),
        "provider": "NVIDIA NIM",
        "notice": (
            "The listed design context, generated contracts, source artifacts, "
            "verification diagnostics, and repair sources may leave the local machine."
        ),
        "apiKeyIncluded": False,
        "tasks": payload,
        "requestFile": "reports/external-transmission-request.json",
    }
    _write_json_atomic(
        run_root / "reports" / "external-transmission-request.json", request
    )
    return request


def validate_approval(path: Path | None, request_id: str) -> dict[str, object]:
    if path is None or not path.is_file():
        raise PermissionError(
            "External transmission approval is required; provide an approval JSON file"
        )
    approval = _read_json(path)
    if approval.get("requestId") != request_id or approval.get("approved") is not True:
        raise PermissionError("Approval does not match the current transmission request")
    result = {
        "requestId": request_id,
        "approved": True,
        "approvedAt": approval.get("approvedAt"),
        "approvedBy": approval.get("approvedBy"),
    }
    if approval.get("delegatedRepairApprovals") is True:
        result["delegatedRepairApprovals"] = True
        result["delegationScope"] = approval.get("delegationScope", {})
    return result


def validate_workflow_approval(
    path: Path | None,
    request: dict[str, object],
    state: dict[str, object],
    run_root: Path,
) -> dict[str, object]:
    """Accept an exact approval or a remaining subset of an already approved run scope."""
    try:
        return validate_approval(path, str(request["requestId"]))
    except PermissionError:
        if path is None or not path.is_file():
            raise
        approval = _read_json(path)
        approved_request_id = str(approval.get("requestId", ""))
        if approval.get("approved") is not True:
            raise
        if _valid_delegated_execution_approval(approval, request, state, run_root):
            return {
                "requestId": str(request["requestId"]),
                "approvedRequestId": str(approval.get("requestId")),
                "approved": True,
                "authorization": "DELEGATED_RUN_SCOPE",
                "delegatedRepairApprovals": True,
                "approvedAt": approval.get("approvedAt"),
                "approvedBy": approval.get("approvedBy"),
            }
        current_ids = {str(item["taskId"]) for item in request.get("tasks", [])}
        candidate_ids = set(current_ids)
        current_phases = {
            task.get("phase")
            for task in state.get("tasks", [])
            if str(task.get("taskId")) in current_ids
        }
        for task in state.get("tasks", []):
            # An approval covers the originally requested phase, not every task
            # that happened to succeed earlier in the run. Including earlier
            # phases changes the reconstructed request ID and wrongly rejects a
            # retry of an unchanged remaining task.
            if task.get("phase") not in current_phases:
                continue
            if task.get("status") != "SUCCEEDED" or int(task.get("attempts", 0)) < 1:
                continue
            result_file = task.get("resultFile")
            if not result_file or not (run_root / str(result_file)).is_file():
                continue
            result = _read_json(run_root / str(result_file))
            if result.get("promptSha256") == task.get("promptSha256"):
                candidate_ids.add(str(task["taskId"]))
        manifest = _read_json(run_root / "reports" / "run-manifest.json")
        task_by_id = {
            item["task_id"]: item for item in manifest.get("implementation_tasks", [])
        }
        payload = _transmission_payload(task_by_id, sorted(candidate_ids))
        if (
            current_ids
            and current_ids.issubset(candidate_ids)
            and _transmission_request_id(payload) == approved_request_id
        ):
            return {
                "requestId": str(request["requestId"]),
                "approvedRequestId": approved_request_id,
                "approved": True,
                "authorization": "APPROVED_SCOPE_SUBSET",
                "approvedAt": approval.get("approvedAt"),
                "approvedBy": approval.get("approvedBy"),
            }
        raise


def _valid_delegated_execution_approval(
    approval: dict[str, object], request: dict[str, object], state: dict[str, object], run_root: Path
) -> bool:
    if approval.get("delegatedRepairApprovals") is not True:
        return False
    scope = approval.get("delegationScope")
    if not isinstance(scope, dict) or scope.get("runId") != run_root.name:
        return False
    manifest = _read_json(run_root / "reports" / "run-manifest.json")
    if scope.get("inputHash") != manifest.get("input_hash"):
        return False
    plan_path = run_root / "reports" / "repair-plan.json"
    plan = _read_json(plan_path) if plan_path.is_file() else {}
    if plan.get("status") == "STALLED":
        return False
    planned_ids = {
        str(task_id)
        for entry in plan.get("entries", []) if isinstance(entry, dict)
        for task_id in [*entry.get("ownerTaskIds", []), *entry.get("revalidationTaskIds", [])]
    }
    current_ids = {str(item.get("taskId")) for item in request.get("tasks", [])}
    initial_ids = {str(task_id) for task_id in scope.get("initialTaskIds", [])}
    return bool(current_ids) and (
        current_ids.issubset(initial_ids)
        or current_ids.issubset(planned_ids)
    )


def phase_for_task(task_type: str) -> str:
    for phase_id, _, types in PHASES:
        if task_type in types:
            return phase_id
    return "unclassified"


def _transmission_payload(
    task_by_id: dict[str, dict[str, object]], task_ids: list[str]
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for task_id in sorted(task_ids):
        task = task_by_id[task_id]
        sources = task.get("source_artifacts", {})
        source_hashes = {
            str(name): _file_sha256(Path(str(path)))
            for name, path in sources.items()
        } if isinstance(sources, dict) else {}
        payload.append({
            "taskId": task_id,
            "taskType": task.get("task_type"),
            "promptSha256": task.get("prompt_sha256"),
            "sourceArtifacts": sources,
            "sourceArtifactHashes": source_hashes,
            "allowedWritePaths": task.get("allowed_write_paths", []),
            "requiredOutputPaths": task.get(
                "required_output_paths", task.get("allowed_write_paths", [])
            ),
        })
    return payload


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _transmission_request_id(payload: list[dict[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _phase_states(tasks: list[dict[str, object]]) -> list[dict[str, object]]:
    phases: list[dict[str, object]] = []
    for phase_id, dependencies, _ in PHASES:
        phase_tasks = [task for task in tasks if task["phase"] == phase_id]
        if not phase_tasks:
            status = "UNPLANNED"
        elif all(task["status"] == "SUCCEEDED" for task in phase_tasks):
            status = "SUCCEEDED"
        elif any(task["status"] == "RUNNING" for task in phase_tasks):
            status = "RUNNING"
        elif any(task["status"] == "FAILED" for task in phase_tasks):
            status = "FAILED"
        else:
            status = "PENDING"
        phases.append(
            {
                "phaseId": phase_id,
                "dependsOn": list(dependencies),
                "status": status,
                "taskIds": [task["taskId"] for task in phase_tasks],
            }
        )
    return phases


def _next_runnable_tasks(
    tasks: list[dict[str, object]], phases: list[dict[str, object]]
) -> list[str]:
    phase_by_id = {phase["phaseId"]: phase for phase in phases}
    for phase_id, dependencies, _ in PHASES:
        candidates = [
            str(task["taskId"]) for task in tasks
            if task["phase"] == phase_id
            and task["status"] in {"PENDING", "INTERRUPTED", "FAILED"}
        ]
        if candidates and all(
            phase_by_id[dependency]["status"] in {"SUCCEEDED", "UNPLANNED"}
            for dependency in dependencies
        ):
            return candidates
    return []


def _dependencies_succeeded(state: dict[str, object], phase_id: str) -> bool:
    phase_by_id = {phase["phaseId"]: phase for phase in state["phases"]}
    dependencies = next(item[1] for item in PHASES if item[0] == phase_id)
    return all(
        phase_by_id[item]["status"] in {"SUCCEEDED", "UNPLANNED"}
        for item in dependencies
    )


def _task_output_hashes(run_root: Path, task_id: str) -> dict[str, str]:
    manifest = _read_json(run_root / "reports" / "run-manifest.json")
    task = next(
        item for item in manifest.get("implementation_tasks", [])
        if item.get("task_id") == task_id
    )
    return _output_hashes(
        run_root,
        task.get("required_output_paths", task.get("allowed_write_paths", [])),
    )


def _output_hashes(run_root: Path, relative_paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in relative_paths:
        path = run_root / relative
        if path.is_file():
            hashes[str(relative)] = _sha256(path)
    return hashes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        # A shared ``workflow-state.json.tmp`` collides when a retry or a
        # parallel worker persists state at the same time.  Windows also
        # briefly rejects replace while an antivirus/indexer has the target
        # open, so use a unique file and retry only that transient operation.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
        temporary = Path(temporary_name)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                temporary_name = None
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _now() -> str:
    return datetime.now(UTC).isoformat()
