"""Isolated entry point for the member-owned scaffold generator."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.implementation.application.prototype import PrototypeClient
from app.implementation.config import ImplementationSettings

APPROVAL_MISMATCH = "Approval does not match the current transmission request"
ONE_CYCLE_EXHAUSTED = "Run-to-completion exceeded 1 workflow cycles"
CHECKPOINT_RUN_ENV = "EASYDEP_MEMBER_CHECKPOINT_RUN"


class MemberPlannerExhausted(RuntimeError):
    pass


def _output_root(job_path: Path) -> tuple[dict[str, object], Path]:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    output_root = Path(str(job.get("outputRoot", "generated/runs")))
    if not output_root.is_absolute():
        workspace_root = Path(str(job.get("workspaceRoot", job_path.parent)))
        output_root = (workspace_root / output_root).resolve()
    return job, output_root.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _explicit_checkpoint(job_path: Path) -> Path | None:
    """명시된 run이 같은 입력 산출물의 멤버 작업 체크포인트인지 검증한다."""
    run_name = os.getenv(CHECKPOINT_RUN_ENV, "").strip()
    if not run_name:
        return None
    if Path(run_name).name != run_name or not run_name.startswith("run_"):
        raise ValueError(f"Invalid {CHECKPOINT_RUN_ENV}: {run_name}")

    job, output_root = _output_root(job_path)
    checkpoint = output_root / run_name
    manifest_path = checkpoint / "reports" / "run-manifest.json"
    state_path = checkpoint / "reports" / "workflow-state.json"
    if not manifest_path.is_file() or not state_path.is_file():
        raise FileNotFoundError(f"Incomplete member checkpoint: {checkpoint}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SUCCEEDED":
        raise ValueError(f"Checkpoint generation is not complete: {checkpoint}")
    if manifest.get("job_name") != job.get("name"):
        raise ValueError(f"Checkpoint job does not match the current job: {checkpoint}")

    workspace_root = Path(str(job.get("workspaceRoot", job_path.parent))).resolve()
    manifest_inputs = manifest.get("inputs") or {}
    for name, value in (job.get("inputs") or {}).items():
        source = Path(str(value))
        if not source.is_absolute():
            source = (workspace_root / source).resolve()
        expected = (manifest_inputs.get(name) or {}).get("sha256")
        if not source.is_file() or not expected or _sha256(source) != expected:
            raise ValueError(
                f"Checkpoint input does not match current artifact: {name}"
            )
    return checkpoint


def _preserve_failed_generation_cache(job_path: Path) -> list[str]:
    """명시적 재시도 전에 생성 자체가 완료되지 않은 캐시만 보존 격리한다.

    생성이 성공한 run에는 멤버 workflow의 task별 체크포인트가 들어 있다. 이 run까지
    격리하면 outer ``implementation.scaffold`` 재시도가 완료 task를 처음부터 다시
    실행하게 되므로 그대로 둔다.
    """
    _job, output_root = _output_root(job_path)
    if not output_root.is_dir():
        return []

    preserved: list[str] = []
    quarantine = output_root.parent / "failed-generation-cache"
    for manifest_path in sorted(output_root.glob("run_*/reports/run-manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if manifest.get("status") == "SUCCEEDED":
            continue
        quarantine.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        source = manifest_path.parent.parent
        target = quarantine / f"{source.name}-{stamp}"
        shutil.move(str(source), str(target))
        preserved.append(str(target))
    return preserved


def _run_member_workflow_with_current_approvals(
    run_root: Path,
    job: object,
    *,
    approved_by: str,
    max_approval_cycles: int = 32,
    max_attempts_per_task: int | None = None,
    retry_failed: bool = False,
) -> dict[str, object]:
    """새 transmission request마다 멤버의 공개 일괄 실행 경계를 다시 호출한다."""
    from app.implementation.workflows.coordinator import run_workflow_to_completion

    attempt_limit = max_attempts_per_task or max(
        1,
        int(os.getenv("IMPLEMENTATION_MAX_TASK_ATTEMPTS", "5")),
    )
    for _cycle in range(max_approval_cycles):
        if _task_attempt_limit_exceeded(run_root, attempt_limit):
            raise MemberPlannerExhausted(
                f"Member task exceeded {attempt_limit} attempts"
            )
        try:
            return run_workflow_to_completion(
                run_root,
                job,
                approved_by=approved_by,
                max_cycles=1,
                retry_failed=retry_failed,
            )
        except PermissionError as error:
            if str(error) != APPROVAL_MISMATCH:
                raise
            if _repair_revision_exceeds_delegation(run_root):
                raise RuntimeError(
                    "Member workflow exceeded its delegated repair-round limit"
                ) from error
        except RuntimeError as error:
            if str(error) != ONE_CYCLE_EXHAUSTED:
                raise
    raise RuntimeError(
        f"Member workflow exceeded {max_approval_cycles} transmission approval cycles"
    )


def _repair_revision_exceeds_delegation(run_root: Path) -> bool:
    reports = run_root / "reports"
    approval_path = reports / "one-time-run-approval.json"
    repair_path = reports / "repair-plan.json"
    if not approval_path.is_file() or not repair_path.is_file():
        return False
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    scope = approval.get("delegationScope") or {}
    limit = int(scope.get("maxRepairRounds", 0))
    plan = json.loads(repair_path.read_text(encoding="utf-8"))
    revision = max(
        (
            int(entry.get("revision", 0))
            for entry in plan.get("entries", [])
            if isinstance(entry, dict)
        ),
        default=0,
    )
    return revision > limit


def _task_attempt_limit_exceeded(run_root: Path, limit: int) -> bool:
    state_path = run_root / "reports" / "workflow-state.json"
    if not state_path.is_file():
        return False
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return any(
        int(task.get("attempts", 0)) >= limit
        and task.get("status") != "SUCCEEDED"
        for task in state.get("tasks", [])
        if isinstance(task, dict)
    )


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    flags = set(arguments[1:])
    allowed_flags = {"--run-implemented-workflow", "--retry-failed-generation"}
    if not arguments or len(flags) != len(arguments[1:]) or not flags <= allowed_flags:
        raise SystemExit(
            "usage: scaffold_worker <job.json> [--run-implemented-workflow] "
            "[--retry-failed-generation]"
        )
    client = PrototypeClient(ImplementationSettings.from_env())
    job_path = Path(arguments[0]).resolve()
    explicit_checkpoint = (
        _explicit_checkpoint(job_path)
        if "--retry-failed-generation" in flags
        else None
    )
    preserved_failed_cache = (
        _preserve_failed_generation_cache(job_path)
        if "--retry-failed-generation" in flags and explicit_checkpoint is None
        else []
    )
    if explicit_checkpoint is not None:
        from app.implementation.workflows.coordinator import workflow_status

        run_root = explicit_checkpoint
        workflow = workflow_status(run_root)
    else:
        run_root, workflow = client.generate_and_plan(job_path)
    if "--run-implemented-workflow" in flags:
        from app.implementation.generation.orchestrator import load_job
        from app.implementation.workflows.coordinator import workflow_status

        try:
            workflow = _run_member_workflow_with_current_approvals(
                run_root,
                load_job(job_path),
                approved_by="EasyDep orchestration explicit batch approval",
                retry_failed="--retry-failed-generation" in flags,
            )
        except MemberPlannerExhausted as error:
            workflow = workflow_status(run_root)
            workflow = {
                **workflow,
                "status": "NEEDS_PLANNER",
                "blockingReason": str(error),
            }
        except RuntimeError:
            # NEEDS_PLANNER means the member workflow exhausted its implemented
            # planners. Preserve its completed work so an explicitly configured
            # temporary provider can fill only this integration gap. Real task
            # failures and unresolved input must remain failures.
            workflow = workflow_status(run_root)
            if workflow.get("status") != "NEEDS_PLANNER":
                raise
    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "member_plan": workflow,
                "member_workflow_status": workflow.get("status"),
                "preserved_failed_generation_cache": preserved_failed_cache,
                "resumed_checkpoint": (
                    str(explicit_checkpoint) if explicit_checkpoint else None
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
