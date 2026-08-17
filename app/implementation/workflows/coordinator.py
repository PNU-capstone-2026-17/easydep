from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..agents.runtime import execute_openhands_task
from ..agents.verification.build import (
    WorkspaceVerificationError,
    verify_run_workspace,
)
from ..agents.verification.release import verify_container_runtime
from ..delivery.kubernetes import render_deployment
from ..delivery.terraform import render_iac
from ..domain.implementation_ir import build_implementation_ir, parse_erd_entities
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
    repair_rounds,
    schedule_cross_phase_repair,
    schedule_source_conformance_repair,
)
from .traceability import build_rtm_traceability_map


WORKFLOW_SCHEMA = "implementation-workflow/v1alpha1"
TRANSMISSION_SCHEMA = "external-transmission-request/v1alpha1"
PHASES = (
    ("control", (), {"control"}),
    ("persistence", ("control",), {
        "persistence-entities",
        "persistence-repositories",
        "persistence-mapping",
        "persistence-schema",
    }),
    ("api-adapters", ("control",), {"api-adapter"}),
    ("boundary-adapters", ("control",), {"boundary-adapter"}),
    ("outbound-adapters", ("control", "persistence"), {"gateway-adapter"}),
    ("wiring", ("persistence", "api-adapters", "boundary-adapters", "outbound-adapters"), {"configuration"}),
    ("frontend", ("api-adapters",), {"frontend-implementation"}),
    ("end-to-end", ("wiring", "frontend"), {"integration-test"}),
)


def plan_workflow(run_root: Path, spec: JobSpec) -> dict[str, object]:
    """Idempotently plan implemented phases and persist a resumable checkpoint."""
    run_root = run_root.resolve()
    if spec.job_type == "FEEDBACK_REVISION":
        apply_repair_directives(run_root)
        return reconcile_workflow_state(run_root)
    ir = build_implementation_ir(spec, run_root)
    erd_path = spec.inputs.get("erd")
    erd_entities = (
        parse_erd_entities(erd_path.read_text(encoding="utf-8"))
        if erd_path is not None and erd_path.is_file()
        else set()
    )
    bce_entities = set(ir.entities)
    if bce_entities and not erd_entities:
        raise ValueError(
            "ERD input must contain Entity definitions matching the BCE Entity components"
        )
    if erd_entities != bce_entities:
        missing_in_erd = sorted(bce_entities - erd_entities)
        missing_in_bce = sorted(erd_entities - bce_entities)
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
    plan_e2e_tasks(spec, run_root)
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
        same_checkpoint = (
            old.get("promptSha256") == prompt_sha
            and old.get("outputHashes") == output_hashes
        )
        result_matches = (
            result.get("status") == "SUCCEEDED"
            and complete_outputs
            and result.get("promptSha256", prompt_sha) == prompt_sha
        )
        if old.get("status") == "RUNNING":
            status = (
                "SUCCEEDED"
                if result_matches and result.get("promptSha256") == prompt_sha
                else "INTERRUPTED"
            )
        elif old.get("status") == "SUCCEEDED" and same_checkpoint and complete_outputs:
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
    gap_path = run_root / "reports" / "design-gaps" / "end-to-end-flow.json"
    gap_report = _read_json(gap_path) if gap_path.is_file() else {}
    status = (
        "FAILED" if any(task["status"] == "FAILED" for task in pending)
        else ("READY" if pending else "NEEDS_PLANNER")
    )
    if (
        status == "NEEDS_PLANNER"
        and current == "end-to-end"
        and gap_report.get("status") == "NEEDS_INPUT"
    ):
        status = "NEEDS_INPUT"
    state: dict[str, object] = {
        "schemaVersion": WORKFLOW_SCHEMA,
        "runId": run_root.name,
        "status": status,
        "currentPhase": current,
        "updatedAt": _now(),
        "phases": phases,
        "tasks": tasks,
        "nextRunnableTasks": _next_runnable_tasks(tasks, phases),
        "blockingReason": (
            "End-to-end generation is blocked by unresolved design contracts; see reports/design-gaps/end-to-end-flow.json."
            if status == "NEEDS_INPUT"
            else (None if pending else "Implemented phases are complete; the audit backlog requires the next planner.")
        ),
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
        verification = verifier(run_root)
        audit = auditor(run_root)
        state = reconcile_workflow_state(run_root)
        if audit.get("status") == "COMPLETE":
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
        elif state["status"] != "NEEDS_INPUT":
            state["blockingReason"] = (
                "No runnable planned task; implement the first unplanned audit backlog phase."
            )
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
        for task in phase_tasks:
            task["status"] = "RUNNING"
            task["attempts"] = int(task.get("attempts", 0)) + 1
            state["updatedAt"] = _now()
            _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
            try:
                result = executor(run_root, str(task["taskId"]))
            except Exception as error:
                task["status"] = "FAILED"
                task["lastError"] = str(error)
                state["status"] = "FAILED"
                state["blockingReason"] = f"Task failed: {task['taskId']}"
                state["updatedAt"] = _now()
                _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
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
                raise
            task["status"] = "SUCCEEDED"
            task["resultFile"] = (
                f"reports/agent-executions/{task['taskId']}.result.json"
            )
            task["outputHashes"] = _task_output_hashes(run_root, str(task["taskId"]))
            task["lastError"] = None
            if result.get("status") != "SUCCEEDED":
                raise RuntimeError(f"Task returned non-success status: {task['taskId']}")
            state["updatedAt"] = _now()
            _write_json_atomic(run_root / "reports" / "workflow-state.json", state)
        _verify_phase(run_root, phase_id, verifier)
        auditor(run_root)
        next(
            phase for phase in state["phases"] if phase["phaseId"] == phase_id
        )["status"] = "SUCCEEDED"

    # A completed phase can change the exact generated sources embedded in a
    # downstream prompt (notably Boundary outputs used by Spring wiring).
    # Re-plan before emitting the next approval request so its hash never
    # describes stale pre-phase context.
    final_state = plan_workflow(run_root, spec)
    audit = auditor(run_root)
    if audit.get("status") == "COMPLETE":
        verification = verifier(run_root)
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
    elif final_state["status"] == "READY":
        final_state["blockingReason"] = None
    elif final_state["status"] != "NEEDS_INPUT":
        final_state["blockingReason"] = (
            "The audit contains backlog work without an implemented planner."
        )
    _write_json_atomic(run_root / "reports" / "workflow-state.json", final_state)
    return final_state


def _verify_phase(
    run_root: Path,
    phase_id: str,
    verifier: Callable[[Path], dict[str, object]],
) -> dict[str, object]:
    if verifier is verify_run_workspace:
        return verify_run_workspace(
            run_root, report_name=f"phase-{phase_id}-verification.json"
        )
    return verifier(run_root)


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
    max_cycles: int = 100,
) -> dict[str, object]:
    """Run every phase and bounded repair from one explicit transmission approval."""
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
            "maxRepairRounds": 3,
            "maxTaskAttempts": 50,
        },
    }
    _write_json_atomic(approval_path, approval)

    for _cycle in range(max_cycles):
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
    raise RuntimeError(f"Run-to-completion exceeded {max_cycles} workflow cycles")


def _render_deployment_if_configured(
    run_root: Path, spec: JobSpec
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    intent = spec.inputs.get("deploymentIntent")
    cloud = spec.inputs.get("cloud")
    deployment = spec.inputs.get("deployment")
    if (intent and intent.is_file()) or (cloud and cloud.is_file()):
        deployment_report = render_deployment(run_root, spec)
        iac_report = None
        if cloud and cloud.is_file():
            iac_report = render_iac(run_root, spec)
        return deployment_report, iac_report
    elif deployment and deployment.is_file():
        raise ValueError(
            "Deployment rendering requires deploymentIntent or a cloud resource "
            "specification"
        )
    return None, None


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
    planned_ids = {
        str(task_id)
        for entry in plan.get("entries", []) if isinstance(entry, dict)
        for task_id in [*entry.get("ownerTaskIds", []), *entry.get("revalidationTaskIds", [])]
    }
    current_ids = {str(item.get("taskId")) for item in request.get("tasks", [])}
    initial_ids = {str(task_id) for task_id in scope.get("initialTaskIds", [])}
    if not current_ids or not (current_ids.issubset(initial_ids) or current_ids.issubset(planned_ids)):
        return False
    attempts = sum(int(task.get("attempts", 0)) for task in state.get("tasks", []))
    return attempts < int(scope.get("maxTaskAttempts", 0)) and _repair_rounds(run_root) <= int(scope.get("maxRepairRounds", 0))


def _repair_rounds(run_root: Path) -> int:
    plan = _read_json(run_root / "reports" / "repair-plan.json") if (run_root / "reports" / "repair-plan.json").is_file() else {}
    return repair_rounds(plan)


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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
