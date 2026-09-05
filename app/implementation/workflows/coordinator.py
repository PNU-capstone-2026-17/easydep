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
from ..agents.verification.build import WorkspaceVerificationError, verify_run_workspace
from ..delivery.container import render_local_container
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
from .repair import (
    apply_repair_directives,
    repair_task_ids,
    schedule_cross_phase_repair,
    schedule_source_conformance_repair,
)
from .traceability import build_rtm_traceability_map

WORKFLOW_SCHEMA = "implementation-workflow/v1alpha1"
PHASES = (
    ("persistence", (), {"persistence"}),
    # ``control`` remains only for the existing feedback-revision task; newly
    # planned implementation work always uses the broader ``use-case`` type.
    (
        "use-cases",
        ("persistence",),
        {
            "use-case",
            "control",
            # Testing에서 되돌아온 수리도 일반 구현 task와 같은 OpenHands 실행기를
            # 사용한다. 별도 phase를 만들지 않고 기존 피드백 작업이 속하던 이 phase에
            # 연결하여, 실패한 검사 종류별 run_task_check가 실제로 실행되게 한다.
            "testing-static",
            "testing-package",
            "testing-iac",
            "testing-dynamic-functional",
        },
    ),
    # generated OpenAPI client는 workflow planning 전에 이미 만들어진다. frontend source는
    # backend source와 경로도 겹치지 않으므로 두 작업은 persistence 준비 뒤 함께 실행한다.
    ("frontend", ("persistence",), {"frontend-implementation"}),
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
        raise ValueError("erdBceModel must contain Entity definitions matching bceModel")
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
        item.get("task_id"): item for item in previous.get("tasks", []) if isinstance(item, dict)
    }
    repaired_tasks = repair_task_ids(run_root)
    tasks: list[dict[str, object]] = []
    for task in manifest.get("implementation_tasks", []):
        task_id = str(task["task_id"])
        prompt_sha = str(task.get("prompt_sha256", ""))
        phase = phase_for_task(str(task.get("task_type", "control")))
        old = previous_tasks.get(task_id, {})
        result_path = run_root / "reports" / "agent-executions" / f"{task_id}.result.json"
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
            task_id in repaired_tasks and result.get("promptSha256") != prompt_sha
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
            old.get("status") == "SUCCEEDED" and complete_outputs and not repair_replay_required
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
                "task_id": task_id,
                "taskType": str(task.get("task_type", "control")),
                "phase": phase,
                # 같은 phase 안에서도 여러 유스케이스가 같은 Control이나 adapter 파일을
                # 고칠 수 있다. planner가 남긴 순서와 편집 범위를 실행 상태에도 보존해야
                # coordinator가 충돌하는 작업을 동시에 실행하지 않는다.
                "dependsOn": [
                    str(item) for item in task.get("depends_on", task.get("dependsOn", []))
                ],
                "allowedWritePaths": [str(item) for item in task.get("allowed_write_paths", [])],
                "allowedWriteRoots": [str(item) for item in task.get("allowed_write_roots", [])],
                "status": status,
                "promptSha256": prompt_sha,
                "outputHashes": output_hashes,
                "attempts": int(old.get("attempts", 0)),
                "resultFile": (
                    result_path.relative_to(run_root).as_posix() if result_path.is_file() else None
                ),
                "lastError": result.get("error") if result.get("status") == "FAILED" else None,
            }
        )

    phases = _phase_states(tasks)
    current = next(
        (
            phase["phaseId"]
            for phase in phases
            if phase["status"] in {"PENDING", "RUNNING", "FAILED"}
        ),
        next(
            (phase["phaseId"] for phase in phases if phase["status"] == "UNPLANNED"),
            None,
        ),
    )
    pending = [task for task in tasks if task["status"] != "SUCCEEDED"]
    status = (
        "FAILED"
        if any(task["status"] == "FAILED" for task in pending)
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
    }
    _write_json_atomic(state_path, state)
    return state


def run_workflow(
    run_root: Path,
    spec: JobSpec,
    *,
    retry_failed: bool = False,
    executor: Callable[[Path, str], dict[str, object]] = execute_openhands_task,
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
            retry_failed=retry_failed,
            executor=executor,
            auditor=auditor,
        )


def _run_workflow(
    run_root: Path,
    spec: JobSpec,
    *,
    retry_failed: bool = False,
    executor: Callable[[Path, str], dict[str, object]] = execute_openhands_task,
    auditor: Callable[[Path], dict[str, object]] = audit_run_completion,
) -> dict[str, object]:
    """Resume planned phases, checkpointing before and after every external task."""
    run_root = run_root.resolve()
    state = plan_workflow(run_root, spec)
    runnable = list(state.get("nextRunnableTasks", []))
    failed_runnable = [
        task_id
        for task_id in runnable
        if next(task for task in state["tasks"] if task["task_id"] == task_id)["status"] == "FAILED"
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
            auditor=auditor,
        )

    # 구현 단계 진입이 곧 실행 요청이다. 별도의 승인 파일 없이 현재 dependency가
    # 충족된 작업만 실행하고, 다음 묶음은 갱신된 workflow에서 이어서 고른다.
    authorized_task_ids = set(runnable)
    state["status"] = "RUNNING"
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)

    while True:
        runnable_phases: list[str] = []
        runnable_tasks: list[dict[str, object]] = []
        for phase_id, _dependencies, _types in PHASES:
            phase_tasks = [
                task
                for task in state["tasks"]
                if task["phase"] == phase_id
                and task["task_id"] in authorized_task_ids
                and (
                    task["status"] in {"PENDING", "INTERRUPTED"}
                    or (retry_failed and task["status"] == "FAILED")
                )
            ]
            if phase_tasks and _dependencies_succeeded(state, phase_id):
                runnable_phases.append(phase_id)
                runnable_tasks.extend(phase_tasks)
        if not runnable_tasks:
            break

        # backend와 frontend가 함께 실행될 때도 기존 UI가 이해하는 첫 phase를 대표값으로
        # 둔다. 개별 phase와 task의 RUNNING 상태는 아래 checkpoint에 그대로 기록된다.
        state["currentPhase"] = runnable_phases[0]
        state["currentPhases"] = runnable_phases
        worker_limit = max(1, int(settings.implementation_task_parallelism))
        # planner의 task dependency와 편집 경로 충돌을 한 번에 검사한다. frontend와 backend는
        # 경로가 분리되어 있으므로 같은 batch가 되고, 충돌하는 backend 작업은 계속 직렬화된다.
        for task_batch in _phase_task_batches("parallel", runnable_tasks):
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
                        run_root, str(task["task_id"]), error.evidence
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
        for phase_id in runnable_phases:
            next(phase for phase in state["phases"] if phase["phaseId"] == phase_id)["status"] = (
                "SUCCEEDED"
            )
        state["updatedAt"] = _now()
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
    state.pop("currentPhases", None)

    # 방금 끝난 작업의 결과와 수리 지시를 checkpoint에 반영한다.
    # wiring은 실패가 있을 때만 짧은 별도 수리 prompt를 받으므로 전체 source 계약을 여기서
    # 다시 직렬화하지 않는다.
    final_state = plan_workflow(run_root, spec)
    if final_state.get("nextRunnableTasks"):
        # Every work unit performs its own focused verification.  Do not scan
        # the incomplete application after each work unit; the final audit and
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
        auditor=auditor,
    )


def _finalize_workflow(
    run_root: Path,
    spec: JobSpec,
    state: dict[str, object],
    *,
    auditor: Callable[[Path], dict[str, object]],
) -> dict[str, object]:
    """작업 완결성과 설계 계약을 확인하고 Testing에 넘길 파일을 만든다.

    작업별 compile·관련 테스트는 각 코딩 에이전트가 이미 통과했다. 전체 Gradle 테스트,
    frontend build와 실제 container 실행은 저장된 동일 산출물을 사용하는 Testing 단계가 담당한다.
    단, 기존 코드를 고친 피드백 작업은 영향 범위가 여러 작업에 걸칠 수 있으므로 backend 테스트를
    한 번 다시 통과한 뒤 Testing으로 넘긴다.
    """
    state["status"] = "FINALIZING"
    state["blockingReason"] = None
    state["currentActivity"] = {
        "id": "completion-audit",
        "label": "최종 구현 결과 확인",
        "status": "RUNNING",
        "detail": "완료된 작업과 Testing에 전달할 산출물을 확인하고 있습니다.",
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
        state["blockingReason"] = "The audit contains work for which no implementation task exists."
        state.pop("currentActivity", None)
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        return state

    try:
        conformance = verify_source_design_conformance(run_root, spec)
    except SourceDesignConformanceError as error:
        repaired = _continue_after_conformance_failure(run_root, spec, error)
        if repaired is not None:
            return repaired
        _record_workflow_failure(run_root, state, error)
        raise

    if (
        spec.job_type == "FEEDBACK_REVISION"
        and not str(getattr(spec, "repair_task_type", "")).startswith("testing-")
    ):
        state["currentActivity"] = {
            "id": "feedback-regression",
            "label": "수정 후 단위 테스트",
            "status": "RUNNING",
            "detail": "수정된 코드가 기존 단위·작은 통합 테스트를 깨뜨리지 않았는지 확인합니다.",
        }
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        try:
            verify_run_workspace(
                run_root,
                "feedback-regression.json",
                verify_frontend=False,
                verify_end_to_end=False,
            )
        except WorkspaceVerificationError as error:
            repaired = _continue_after_feedback_regression_failure(run_root, spec, error)
            if repaired is not None:
                return repaired
            _record_workflow_failure(run_root, state, error)
            raise
        state["feedbackRegression"] = "reports/feedback-regression.json"

    _complete_implementation(run_root, spec, state, conformance)
    state["blockingReason"] = None
    state["currentActivity"] = {
        "id": "implementation-artifacts",
        "label": "구현 산출물 준비",
        "status": "SUCCEEDED",
        "detail": "정적·동적 검사는 다음 Testing 단계에서 실행합니다.",
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
    remaining = {str(task["task_id"]): task for task in tasks}
    batches: list[list[dict[str, object]]] = []
    completed_in_phase: set[str] = set()
    while remaining:
        ready = [
            task
            for task_id, task in remaining.items()
            if all(
                str(dependency) not in remaining or str(dependency) in completed_in_phase
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
            task_id = str(task["task_id"])
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
        result = executor(run_root, str(task["task_id"]))
        if result.get("status") != "SUCCEEDED":
            raise RuntimeError(f"Task returned non-success status: {task['task_id']}")
        return result

    def mark_started(task: dict[str, object]) -> None:
        task["status"] = "RUNNING"
        task["attempts"] = int(task.get("attempts", 0)) + 1
        state["updatedAt"] = _now()
        _write_json_atomic(state_path, state)

    failures: list[tuple[dict[str, object], Exception]] = []

    def record_completion(task: dict[str, object], future: Future[dict[str, object]]) -> None:
        try:
            future.result()
        except Exception as error:
            task["status"] = "FAILED"
            task["lastError"] = str(error)
            failures.append((task, error))
        else:
            task["status"] = "SUCCEEDED"
            task["resultFile"] = f"reports/agent-executions/{task['task_id']}.result.json"
            task["outputHashes"] = _task_output_hashes(run_root, str(task["task_id"]))
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
            active[pool.submit(langsmith_metrics.bind_context(run), task)] = task

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
        state["blockingReason"] = f"Task failed: {failed_task['task_id']}"
        state["updatedAt"] = _now()
        _write_json_atomic(state_path, state)
    return blocking_failures


def _record_workflow_failure(run_root: Path, state: dict[str, object], error: Exception) -> None:
    """완료 감사나 산출물 생성 실패를 workflow checkpoint에 기록한다."""
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


def _continue_after_incomplete_audit(
    run_root: Path,
    spec: JobSpec,
    audit: dict[str, object],
) -> dict[str, object] | None:
    """마지막 감사가 기존 task의 부족한 산출물을 찾으면 그 task부터 다시 실행한다.

    감사 결과에는 이미 담당 ``task_id``가 들어 있다. 새 planner나 사용자 선택을
    요구하지 않고, 해당 task에 감사 근거를 붙인 뒤 같은 실행에서 자동 수리한다.
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
    retry_failed: bool = False,
    max_cycles: int | None = None,
) -> dict[str, object]:
    """완료 또는 명확한 중단 상태까지 실행한다.

    기본 실행에는 repair 횟수 상한이 없다. 각 repair 계획은 실패 지문, 수정 전 코드와 사용한
    전략을 저장하며, 같은 결과가 반복되어도 이 이력을 다음 요청에 포함해 다른 수정을 시도한다.
    ``max_cycles``는 테스트가 실행 주기를 제한할 때 쓰는 선택 사항이다.
    """
    run_root = run_root.resolve()
    cycle = 0
    while True:
        cycle += 1
        state = plan_workflow(run_root, spec)
        if state.get("status") == "COMPLETE":
            return state
        state = run_workflow(
            run_root,
            spec,
            retry_failed=retry_failed,
        )
        status = str(state.get("status", ""))
        if status == "COMPLETE":
            _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
            return state
        if status in {"FAILED", "NEEDS_INPUT", "NEEDS_PLANNER"}:
            raise RuntimeError(
                f"Run-to-completion stopped in {status}: {state.get('blockingReason')}"
            )
        if max_cycles is not None and cycle >= max_cycles:
            raise RuntimeError(f"Run-to-completion exceeded {max_cycles} workflow cycles")


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
    selected_target = bundle.get("selectedTarget")
    if not isinstance(selected_target, dict):
        # A persisted v1 single-target bundle predates selectedTarget. It is
        # still deterministic; alternatives must be selected explicitly.
        if len(projections) != 1 or not isinstance(projections[0], dict):
            raise ValueError("Completed deployment bundle has no selected target")
        selected_target = {
            "provider": projections[0].get("provider"),
            "region": projections[0].get("region"),
        }
    selected_id = str(selected_target.get("id") or "")
    selected_matches = [
        projection
        for projection in projections
        if isinstance(projection, dict)
        and (
            str(projection["target"].get("id") or "") == selected_id
            if selected_id and isinstance(projection.get("target"), dict)
            else str(projection.get("provider") or "").lower()
            == str(selected_target.get("provider") or "").lower()
            and str(projection.get("region") or "") == str(selected_target.get("region") or "")
        )
    ]
    if len(selected_matches) != 1 or selected_matches[0].get("status") != "completed":
        raise ValueError("Selected deployment target has no completed projection")
    projection = selected_matches[0]
    deployment_plan = projection.get("deploymentPlan")
    if not isinstance(deployment_plan, dict):
        raise TypeError("Selected deployment projection has no deployment plan")
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
    rebound = [
        {
            **item,
            "deploymentPlan": current_plan,
            "deploymentPlanStructureDigest": current_plan.get("structureDigest"),
            "resourcePlan": resource_plan,
            "resourcePlanStructureDigest": current_digest,
            "issues": [],
        }
        if item is projection
        else item
        for item in projections
        if isinstance(item, dict)
    ]

    bound_bundle = {**bundle, "workloadGraph": current_graph, "projections": rebound}
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
    deployment = spec.inputs.get("deployment")
    deployment_bundle = spec.inputs.get("deploymentBundle")
    has_bundle = bool(deployment_bundle and deployment_bundle.is_file())
    if has_bundle:
        # observer가 실제 EXPOSE와 실행 사용자를 읽을 수 있도록 먼저 결정론적인
        # 로컬 Dockerfile을 만든다. 이후 IaC는 같은 파일과 bound bundle을 사용한다.
        render_local_container(run_root)
    bound_bundle = _bind_deployment_runtime(run_root, spec) if has_bundle else None
    if (
        deployment
        and deployment.is_file()
        and not (deployment_bundle and deployment_bundle.is_file())
    ):
        raise ValueError(
            "Deployment rendering requires deploymentIntent or a cloud resource specification"
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
    return deployment_report, iac_report


def _complete_implementation(
    run_root: Path,
    spec: JobSpec,
    state: dict[str, object],
    conformance: dict[str, object],
) -> None:
    """배포 입력을 코드로 만든 뒤 구현 산출물을 완료 상태로 바꾼다."""
    state["status"] = "COMPLETE"
    state["sourceDesignConformance"] = conformance.get("status")
    try:
        deployment, _iac = _render_deployment_if_configured(run_root, spec)
        if deployment is None:
            render_local_container(run_root)
        build_rtm_traceability_map(spec, run_root)
    except Exception as error:
        state["status"] = "FAILED"
        state["blockingReason"] = str(error)
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        raise
    state["testingRequired"] = True


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


def _continue_after_feedback_regression_failure(
    run_root: Path,
    spec: JobSpec,
    error: WorkspaceVerificationError,
) -> dict[str, object] | None:
    """피드백 수리 뒤 깨진 단위 테스트를 같은 OpenHands 작업으로 되돌린다."""
    repair = schedule_cross_phase_repair(
        run_root,
        "apply-source-feedback",
        error.evidence,
    )
    if repair is None:
        return None
    state = plan_workflow(run_root, spec)
    state["repairPlan"] = "reports/repair-plan.json"
    state["blockingReason"] = None
    state.pop("currentActivity", None)
    _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
    return state


def phase_for_task(task_type: str) -> str:
    for phase_id, _, types in PHASES:
        if task_type in types:
            return phase_id
    # 모르는 task를 조용히 건너뛰면 기존 파일이 있다는 이유만으로 workflow가 완료될
    # 수 있다. 새 task 종류를 추가할 때 실행 phase 연결도 함께 하도록 즉시 알린다.
    raise ValueError(f"Unknown implementation task type: {task_type}")


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
                "taskIds": [task["task_id"] for task in phase_tasks],
            }
        )
    return phases


def _next_runnable_tasks(
    tasks: list[dict[str, object]], phases: list[dict[str, object]]
) -> list[str]:
    phase_by_id = {phase["phaseId"]: phase for phase in phases}
    runnable: list[str] = []
    for phase_id, dependencies, _ in PHASES:
        candidates = [
            str(task["task_id"])
            for task in tasks
            if task["phase"] == phase_id and task["status"] in {"PENDING", "INTERRUPTED", "FAILED"}
        ]
        if candidates and all(
            phase_by_id[dependency]["status"] in {"SUCCEEDED", "UNPLANNED"}
            for dependency in dependencies
        ):
            runnable.extend(candidates)
    return runnable


def _dependencies_succeeded(state: dict[str, object], phase_id: str) -> bool:
    phase_by_id = {phase["phaseId"]: phase for phase in state["phases"]}
    dependencies = next(item[1] for item in PHASES if item[0] == phase_id)
    return all(phase_by_id[item]["status"] in {"SUCCEEDED", "UNPLANNED"} for item in dependencies)


def _task_output_hashes(run_root: Path, task_id: str) -> dict[str, str]:
    manifest = _read_json(run_root / "reports" / "run-manifest.json")
    task = next(
        item for item in manifest.get("implementation_tasks", []) if item.get("task_id") == task_id
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
