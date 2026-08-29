from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.metrics import langsmith as langsmith_metrics

from ..agents.runtime import execute_openhands_task
from ..agents.verification.build import (
    WorkspaceVerificationError,
    verify_run_workspace,
)
from ..agents.verification.release import verify_container_runtime
from ..delivery.kubernetes import render_deployment, render_local_container
from ..delivery.terraform import render_iac
from ..domain.implementation_ir import (
    assess_bce_erd_entity_contract,
    build_implementation_ir,
)
from ..domain.models import JobSpec
from ..generation.orchestrator import (
    plan_api_adapter_tasks,
    plan_boundary_adapter_tasks,
    plan_e2e_tasks,
    plan_frontend_tasks,
    plan_gateway_adapter_tasks,
    plan_persistence_tasks,
    plan_wiring_tasks,
)
from .completion import audit_run_completion
from .conformance import (
    SourceDesignConformanceError,
    restore_generated_contracts,
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
    # Persistence contracts are generated from the ERD before Control services
    # are implemented.  Repositories are implementation details, so they do
    # not need to appear in the BCE class diagram, but Control services must be
    # able to compile against the generated repository interfaces.
    ("persistence", (), {
        "persistence-entities",
        "persistence-repositories",
        "persistence-mapping",
        "persistence-schema",
    }),
    ("control", ("persistence",), {"control"}),
    ("api-adapters", ("control",), {"api-adapter"}),
    ("boundary-adapters", ("control",), {"boundary-adapter"}),
    ("outbound-adapters", ("control", "persistence"), {"gateway-adapter"}),
    ("wiring", ("persistence", "api-adapters", "boundary-adapters", "outbound-adapters"), {"configuration"}),
    ("frontend", ("api-adapters",), {"frontend-implementation"}),
    ("end-to-end", ("wiring", "frontend"), {"integration-test"}),
)

# Tasks in these phases are planned from immutable design/previous-phase
# contracts and own disjoint output files.  Persistence is handled separately:
# entities must land first, after which repositories, mapping, and schema are
# independent.
PARALLEL_PHASES = frozenset(
    {"control", "api-adapters", "boundary-adapters", "outbound-adapters"}
)
# Individual tasks already compile their owned production sources and run only
# their owned tests.  A full workspace build is valuable at integration seams,
# but repeating it after every independent phase dominated the end-to-end run.
FULL_VERIFICATION_PHASES = frozenset({"wiring", "end-to-end"})
PHASE_LABELS = {
    "control": "Control",
    "persistence": "Repository",
    "api-adapters": "API Adapter",
    "boundary-adapters": "Boundary Adapter",
    "outbound-adapters": "Outbound Adapter",
    "wiring": "Application Setup",
    "frontend": "Frontend",
    "end-to-end": "E2E Test",
}


def plan_workflow(run_root: Path, spec: JobSpec) -> dict[str, object]:
    """Idempotently plan implemented phases and persist a resumable checkpoint."""
    run_root = run_root.resolve()
    if spec.job_type == "FEEDBACK_REVISION":
        apply_repair_directives(run_root)
        return reconcile_workflow_state(run_root)
    ir = build_implementation_ir(spec, run_root)
    erd_path = spec.inputs.get("erd")
    erd_source = erd_path.read_text(encoding="utf-8") if erd_path and erd_path.is_file() else ""
    bce_entities = set(ir.entities)
    contract = assess_bce_erd_entity_contract(erd_source, bce_entities)
    if bce_entities and not contract.erd_entities:
        raise ValueError(
            "ERD input must contain Entity definitions matching the BCE Entity components"
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
        raise ValueError("BCE/ERD entity mismatch; " + "; ".join(details))
    needs_persistence = bool(bce_entities) or any(
        gateway.kind == "persistence" for gateway in ir.gateways
    )
    if needs_persistence:
        erd = erd_path
        if erd is None or not erd.is_file():
            raise ValueError(
                "ERD input is required because the BCE design contains persistent entities or Gateways"
            )
        plan_persistence_tasks(spec, run_root)
    plan_api_adapter_tasks(spec, run_root)
    if ir.boundaries:
        plan_boundary_adapter_tasks(spec, run_root)
    if ir.gateways:
        plan_gateway_adapter_tasks(spec, run_root)
    plan_wiring_tasks(spec, run_root)
    if (run_root / "application" / "frontend" / "src" / "generated").is_dir():
        plan_frontend_tasks(spec, run_root)
    # E2E gap detection is an audit of the completed application, not an
    # input gate for the phases that produce that application.  Running it
    # during the initial plan sees every controller/repository as missing and
    # incorrectly blocks the first persistence batch with NEEDS_INPUT.
    if _e2e_prerequisites_complete(run_root):
        plan_e2e_tasks(spec, run_root)
    else:
        _defer_e2e_planning(run_root)
    build_rtm_traceability_map(spec, run_root)
    apply_repair_directives(run_root)
    return reconcile_workflow_state(run_root)


def _e2e_prerequisites_complete(run_root: Path) -> bool:
    """Return whether every currently planned non-E2E task has landed outputs.

    E2E generation depends on the production and adapter phases.  Checking the
    manifest's allowed paths gives us a durable, process-independent
    checkpoint and also works when a workflow is resumed in a new process.
    """
    manifest = _read_json(run_root / "reports" / "run-manifest.json")
    tasks = [
        item for item in manifest.get("implementation_tasks", [])
        if item.get("task_type") != "integration-test"
    ]
    return all(
        (run_root / str(path)).is_file()
        for item in tasks
        for path in item.get("allowed_write_paths", [])
    )


def _defer_e2e_planning(run_root: Path) -> None:
    """Remove stale E2E tasks/gaps while their producer phases are pending."""
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    manifest["implementation_tasks"] = [
        item for item in manifest.get("implementation_tasks", [])
        if item.get("task_type") != "integration-test"
    ]
    _write_json_atomic(manifest_path, manifest)
    gap_path = run_root / "reports" / "design-gaps" / "end-to-end-flow.json"
    gap_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(
        gap_path,
        {
            "schemaVersion": "implementation-design-gaps/v1alpha1",
            "phase": "end-to-end",
            "status": "PENDING",
            "gaps": [],
            "reason": "Awaiting completion of prerequisite implementation phases.",
        },
    )


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
        output_hashes = _output_hashes(run_root, task.get("allowed_write_paths", []))
        complete_outputs = len(output_hashes) == len(task.get("allowed_write_paths", []))
        # A downstream task's prompt can legitimately change after an earlier
        # phase completes because its context embeds the newly generated
        # sources.  That must not replay an already successful task when the
        # task's own outputs have not changed.  Output hashes are the durable
        # checkpoint; the prompt hash remains relevant for failed attempts so
        # a repair prompt can be retried.
        same_output_checkpoint = old.get("outputHashes") == output_hashes
        result_matches = (
            result.get("status") == "SUCCEEDED"
            and complete_outputs
            and result.get("promptSha256", prompt_sha) == prompt_sha
        )
        repair_replay_required = (
            task_id in repaired_tasks
            and result.get("promptSha256") != prompt_sha
        )
        if old.get("status") == "RUNNING":
            status = (
                "SUCCEEDED"
                if result_matches and result.get("promptSha256") == prompt_sha
                else "INTERRUPTED"
            )
        elif (
            old.get("status") == "SUCCEEDED"
            and same_output_checkpoint
            and complete_outputs
            and not repair_replay_required
        ):
            status = "SUCCEEDED"
        elif result_matches and not old:
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
    design_gap_reports = {
        path.stem: _read_json(path)
        for path in (run_root / "reports" / "design-gaps").glob("*.json")
    } if (run_root / "reports" / "design-gaps").is_dir() else {}
    blocking_gap = next(
        (
            (name, report)
            for name, report in design_gap_reports.items()
            if report.get("status") == "NEEDS_INPUT"
        ),
        None,
    )
    blocking_details = (
        blocking_gap[1].get("gaps")
        if blocking_gap is not None and isinstance(blocking_gap[1].get("gaps"), list)
        else (
            blocking_gap[1].get("findings")
            if blocking_gap is not None and isinstance(blocking_gap[1].get("findings"), list)
            else []
        )
    )
    status = (
        "FAILED" if any(task["status"] == "FAILED" for task in pending)
        else ("READY" if pending else "NEEDS_PLANNER")
    )
    # A blocked E2E planner intentionally emits no runnable task and persists
    # its executable-contract gaps separately.  Empty optional phases (for
    # example, outbound adapters when no Gateway exists) can make ``current``
    # point at an earlier UNPLANNED phase, so gating this conversion on the
    # current phase incorrectly reported NEEDS_PLANNER instead of the required
    # user-actionable NEEDS_INPUT state.
    if blocking_gap is not None:
        status = "NEEDS_INPUT"
    state: dict[str, object] = {
        "schemaVersion": WORKFLOW_SCHEMA,
        "runId": run_root.name,
        "status": status,
        "currentPhase": current,
        "updatedAt": _now(),
        "phases": phases,
        "tasks": tasks,
        "nextRunnableTasks": (
            [] if status == "NEEDS_INPUT" else _next_runnable_tasks(tasks, phases)
        ),
        "blockingReason": (
            (
                "End-to-end generation is blocked by unresolved design contracts; "
                "see reports/design-gaps/end-to-end-flow.json."
                if status == "NEEDS_INPUT"
                else (None if pending else "Implemented phases are complete; the audit backlog requires the next planner.")
            )
        ),
        "blockingDetails": blocking_details if status == "NEEDS_INPUT" else [],
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
        state["currentActivity"] = {
            "id": "completion-audit",
            "label": "최종 구현 결과 확인",
            "status": "RUNNING",
            "detail": "생성된 소스를 빌드하고 테스트하고 있습니다.",
        }
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        try:
            verification = verifier(run_root)
            audit = auditor(run_root)
        except Exception as error:
            _record_workflow_failure(run_root, state, error)
            raise
        state = reconcile_workflow_state(run_root)
        if audit.get("status") == "COMPLETE":
            state["currentActivity"] = {
                "id": "release-verification",
                "label": "최종 릴리스 검증",
                "status": "RUNNING",
                "detail": "설계 정합성과 실행 가능 여부를 확인하고 있습니다.",
            }
            _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
            try:
                conformance = verify_source_design_conformance(run_root, spec)
            except SourceDesignConformanceError as error:
                return _handle_source_conformance_failure(run_root, spec, state, error)
            _complete_release(
                run_root, spec, state, audit, verification, conformance
            )
        elif state.get("status") != "NEEDS_INPUT":
            state["status"] = "NEEDS_PLANNER"
        state["verification"] = verification.get("status")
        state["audit"] = "reports/implementation-completion-audit.json"
        if state["status"] == "COMPLETE":
            state["blockingReason"] = None
            state["currentActivity"] = {
                "id": "release-verification",
                "label": "최종 릴리스 검증",
                "status": "SUCCEEDED",
            }
        elif state["status"] != "NEEDS_INPUT":
            state["blockingReason"] = (
                "No runnable planned task; implement the first unplanned audit backlog phase."
            )
            state.pop("currentActivity", None)
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        return state

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
        worker_limit = max(1, int(settings.implementation_task_parallelism))
        for task_batch in _phase_task_batches(phase_id, phase_tasks):
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
        full_phase_verification = (
            verifier is not verify_run_workspace
            or phase_id in FULL_VERIFICATION_PHASES
        )
        state["currentActivity"] = {
            "id": f"verify-{phase_id}",
            "label": (
                f"{PHASE_LABELS[phase_id]} 통합 빌드 및 Test"
                if full_phase_verification
                else f"{PHASE_LABELS[phase_id]} 작업 단위 검증 확인"
            ),
            "status": "RUNNING",
            "detail": (
                "전체 애플리케이션을 빌드하고 통합 테스트를 실행하고 있습니다."
                if full_phase_verification
                else "각 작업의 컴파일·소유 테스트가 완료되어 다음 통합 게이트에서 다시 검증합니다."
            ),
        }
        state["updatedAt"] = _now()
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        try:
            _verify_phase(run_root, phase_id, verifier)
        except Exception as error:
            _record_workflow_failure(run_root, state, error)
            raise
        state["currentActivity"] = {
            "id": f"audit-{phase_id}",
            "label": f"{PHASE_LABELS[phase_id]} 구현 결과 확인",
            "status": "RUNNING",
            "detail": "다음 구현 단계로 진행할 수 있는지 확인하고 있습니다.",
        }
        state["updatedAt"] = _now()
        _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        try:
            auditor(run_root)
        except Exception as error:
            _record_workflow_failure(run_root, state, error)
            raise
        next(
            phase for phase in state["phases"] if phase["phaseId"] == phase_id
        )["status"] = "SUCCEEDED"
        state.pop("currentActivity", None)

    # A completed phase can change the exact generated sources embedded in a
    # downstream prompt (notably Boundary outputs used by Spring wiring).
    # Re-plan before emitting the next approval request so its hash never
    # describes stale pre-phase context.
    final_state = plan_workflow(run_root, spec)
    final_state["currentActivity"] = {
        "id": "completion-audit",
        "label": "최종 구현 결과 확인",
        "status": "RUNNING",
        "detail": "전체 구현 결과를 빌드하고 테스트하고 있습니다.",
    }
    _write_json_atomic(run_root / "reports" / "workflow-state.json", final_state)
    try:
        audit = auditor(run_root)
    except Exception as error:
        _record_workflow_failure(run_root, final_state, error)
        raise
    if audit.get("status") == "COMPLETE":
        final_state["currentActivity"] = {
            "id": "release-verification",
            "label": "최종 릴리스 검증",
            "status": "RUNNING",
            "detail": "설계 정합성과 실행 가능 여부를 확인하고 있습니다.",
        }
        _write_json_atomic(run_root / "reports" / "workflow-state.json", final_state)
        try:
            verification = verifier(run_root)
        except Exception as error:
            _record_workflow_failure(run_root, final_state, error)
            raise
        try:
            conformance = verify_source_design_conformance(run_root, spec)
        except SourceDesignConformanceError as error:
            return _handle_source_conformance_failure(run_root, spec, final_state, error)
        _complete_release(
            run_root, spec, final_state, audit, verification, conformance
        )
    elif final_state.get("status") != "NEEDS_INPUT":
        final_state["status"] = (
            "READY" if final_state.get("nextRunnableTasks") else "NEEDS_PLANNER"
        )
    final_state["audit"] = "reports/implementation-completion-audit.json"
    if final_state["status"] == "COMPLETE":
        final_state["blockingReason"] = None
        final_state["currentActivity"] = {
            "id": "release-verification",
            "label": "최종 릴리스 검증",
            "status": "SUCCEEDED",
        }
    elif final_state["status"] == "READY":
        final_state["blockingReason"] = None
        final_state.pop("currentActivity", None)
    elif final_state["status"] != "NEEDS_INPUT":
        final_state["blockingReason"] = (
            "The audit contains backlog work without an implemented planner."
        )
        final_state.pop("currentActivity", None)
    _write_json_atomic(run_root / "reports" / "workflow-state.json", final_state)
    return final_state


def _phase_task_batches(
    phase_id: str, tasks: list[dict[str, object]]
) -> list[list[dict[str, object]]]:
    """Return dependency-safe batches while preserving manifest task order."""
    if len(tasks) < 2:
        return [tasks]
    if not _write_paths_are_disjoint(tasks):
        return [[task] for task in tasks]
    if phase_id in PARALLEL_PHASES:
        return [tasks]
    if phase_id == "persistence":
        entities = [
            task for task in tasks if task.get("taskType") == "persistence-entities"
        ]
        dependents = [task for task in tasks if task not in entities]
        # Entity tasks are file-disjoint and intentionally run concurrently;
        # repositories/mapping/schema remain behind the entity barrier.
        batches = [entities] if entities else []
        if dependents:
            batches.append(dependents)
        return batches
    return [[task] for task in tasks]


def _write_paths_are_disjoint(tasks: list[dict[str, object]]) -> bool:
    """Reject exact and ancestor/descendant output overlaps conservatively."""
    owned: list[tuple[str, ...]] = []
    for task in tasks:
        for value in task.get("allowedWritePaths", task.get("allowed_write_paths", [])):
            parts = tuple(Path(str(value).replace("\\", "/")).parts)
            if any(
                parts[: len(previous)] == previous
                or previous[: len(parts)] == parts
                for previous in owned
            ):
                return False
            owned.append(parts)
    return True


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

    if failures:
        failed_task, _ = failures[0]
        state["status"] = "FAILED"
        state["blockingReason"] = f"Task failed: {failed_task['taskId']}"
        state["updatedAt"] = _now()
        _write_json_atomic(state_path, state)
    return failures


def _verify_phase(
    run_root: Path,
    phase_id: str,
    verifier: Callable[[Path], dict[str, object]],
) -> dict[str, object]:
    if verifier is verify_run_workspace:
        if phase_id not in FULL_VERIFICATION_PHASES:
            result = {
                "status": "SKIPPED",
                "reason": (
                    "Task-scoped verification already completed; full workspace "
                    "verification runs at the next integration seam and final gate."
                ),
            }
            _write_json_atomic(
                run_root / "reports" / f"phase-{phase_id}-verification.json",
                result,
            )
            return result
        return verify_run_workspace(
            run_root,
            report_name=f"phase-{phase_id}-verification.json",
            verify_frontend=phase_id == "frontend",
        )
    return verifier(run_root)


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

    기본 실행에는 repair 횟수 상한이 없다. 각 repair 계획은 실패 지문과 사용한 전략을
    저장하며, 같은 실패에 같은 전략을 다시 쓰게 되면 계획 단계에서 ``STALLED``가 된다.
    ``max_cycles``는 테스트와 멤버 프로세스가 승인 한 주기만 실행할 때 쓰는 선택 사항이다.
    """
    run_root = run_root.resolve()
    state = plan_workflow(run_root, spec)
    request_path = run_root / "reports" / "external-transmission-request.json"
    if not request_path.is_file():
        if state.get("status") == "COMPLETE":
            return state
        raise PermissionError("No external transmission request is available to approve")
    request = _read_json(request_path)
    manifest = _read_json(run_root / "reports" / "run-manifest.json")
    approval_path = run_root / "reports" / "one-time-run-approval.json"
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

    cycle = 0
    while True:
        cycle += 1
        state = run_workflow(
            run_root,
            spec,
            approval_path,
            retry_failed=retry_failed,
        )
        status = str(state.get("status", ""))
        if status == "COMPLETE":
            state["oneTimeApproval"] = approval_path.relative_to(run_root).as_posix()
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


def _render_deployment_if_configured(
    run_root: Path, spec: JobSpec
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    intent = spec.inputs.get("deploymentIntent")
    cloud = spec.inputs.get("cloud")
    deployment = spec.inputs.get("deployment")
    deployment_bundle = spec.inputs.get("deploymentBundle")
    if (intent and intent.is_file()) or (cloud and cloud.is_file()):
        deployment_report = render_deployment(
            run_root, spec, include_kubernetes=False
        )
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
    if (cloud and cloud.is_file()) or (deployment_bundle and deployment_bundle.is_file()):
        iac_report = render_iac(run_root, spec, include_kubernetes=False)
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


def _handle_source_conformance_failure(
    run_root: Path,
    spec: JobSpec,
    state: dict[str, object],
    error: SourceDesignConformanceError,
) -> dict[str, object]:
    restored = restore_generated_contracts(run_root)
    # A generated-contract mutation needs no LLM repair: restore the exact
    # local baseline, then prove the restored workspace still builds and now
    # conforms before exposing it as a completed artifact.
    if restored:
        restored_verification = verify_run_workspace(run_root)
        restored_audit = audit_run_completion(run_root)
        try:
            restored_conformance = verify_source_design_conformance(run_root, spec)
        except SourceDesignConformanceError as restored_error:
            error = restored_error
        else:
            if restored_audit.get("status") == "COMPLETE":
                _complete_release(
                    run_root,
                    spec,
                    state,
                    restored_audit,
                    restored_verification,
                    restored_conformance,
                )
                state["restoredGeneratedContracts"] = restored
                state["blockingReason"] = None
                _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
                return state
    repair = schedule_source_conformance_repair(run_root, error.report)
    if repair is not None:
        repaired = plan_workflow(run_root, spec)
        repaired["status"] = "READY"
        repaired["repairPlan"] = "reports/repair-plan.json"
        repaired["sourceDesignConformance"] = "REPAIR_SCHEDULED"
        repaired["restoredGeneratedContracts"] = restored
        repaired["blockingReason"] = None
        _write_json_atomic(run_root / "reports" / "workflow-state.json", repaired)
        return repaired
    state["status"] = "FAILED"
    state["sourceDesignConformance"] = "FAILED"
    state["restoredGeneratedContracts"] = restored
    state["blockingReason"] = (
        "Generated source contracts or sequence calls diverge from the design; "
        "see reports/source-design-conformance.json."
    )
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
    except PermissionError as exact_error:
        if path is None or not path.is_file():
            raise
        approval = _read_json(path)
        approved_request_id = str(approval.get("requestId", ""))
        if approval.get("approved") is not True:
            raise exact_error
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
        raise exact_error


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
        current_ids.issubset(initial_ids) or current_ids.issubset(planned_ids)
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
    return _output_hashes(run_root, task.get("allowed_write_paths", []))


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
