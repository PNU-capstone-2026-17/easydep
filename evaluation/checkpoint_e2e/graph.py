from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .catalog import (
    CHECKPOINTS,
    GOLD_ROOT,
    RUN_ROOT,
    case_definition,
    checkpoint_after,
    current_chain_path,
    current_stage_sample_path,
    digest,
    jsonable,
    load_gold,
    read_json,
    write_json,
)
from .evidence import semantic_signature, validate_state, write_outputs
from .transitions import run_transition


class HarnessState(TypedDict, total=False):
    case_id: str
    source_checkpoint: str
    target_checkpoint: str
    run_id: str
    job_dir: str
    source_state: dict[str, Any]
    expected_oracle: dict[str, Any]
    output_state: dict[str, Any]
    tasks: list[dict[str, Any]]
    render: dict[str, Any]
    validation: dict[str, Any]
    verdict: str
    error: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _job_name(source: str, target: str) -> str:
    return f"{CHECKPOINTS.index(target):02d}-{source}-to-{target}"


def _write_task(job_dir: Path, index: int, task: str, before: dict, delta: dict, elapsed: float) -> None:
    safe = task.replace(".", "-").replace("_", "-")
    root = job_dir / "tasks" / f"{index:02d}-{safe}"
    write_json(root / "input.json", before)
    write_json(root / "output.json", delta)
    write_json(
        root / "timing.json",
        {"task": task, "elapsedSeconds": round(elapsed, 6)},
    )


def build_harness_graph():
    builder = StateGraph(HarnessState)

    def load_source(state: HarnessState) -> dict[str, Any]:
        source, _source_oracle = load_gold(state["case_id"], state["source_checkpoint"])
        target = checkpoint_after(state["source_checkpoint"])
        _expected, oracle = load_gold(state["case_id"], target)
        job_dir = Path(state["job_dir"])
        write_json(job_dir / "input" / "snapshot.json", source)
        write_json(job_dir / "input" / "digest.json", {"sha256": digest(source)})
        return {
            "source_state": source,
            "target_checkpoint": target,
            "expected_oracle": oracle,
        }

    def execute(state: HarnessState) -> dict[str, Any]:
        job_dir = Path(state["job_dir"])
        tasks: list[dict[str, Any]] = []

        def record(task: str, before: dict, delta: dict, elapsed: float) -> None:
            index = len(tasks) + 1
            _write_task(job_dir, index, task, before, delta, elapsed)
            tasks.append(
                {"index": index, "task": task, "elapsedSeconds": round(elapsed, 6)}
            )

        try:
            target, output = run_transition(
                state["source_checkpoint"], dict(state["source_state"]), record
            )
        except Exception as error:
            return {"tasks": tasks, "error": f"{type(error).__name__}: {error}"}
        return {"target_checkpoint": target, "output_state": output, "tasks": tasks}

    def render(state: HarnessState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        try:
            rendered = write_outputs(
                state["target_checkpoint"],
                state["output_state"],
                Path(state["job_dir"]) / "output",
            )
            return {"render": rendered}
        except Exception as error:
            return {"error": f"{type(error).__name__}: {error}"}

    def validate(state: HarnessState) -> dict[str, Any]:
        job_dir = Path(state["job_dir"])
        if state.get("error"):
            report = {"status": "failed", "errors": [state["error"]], "warnings": []}
            comparison = {"matches": False, "expected": state.get("expected_oracle"), "actual": None}
            verdict = "failed"
        else:
            report = validate_state(state["target_checkpoint"], state["output_state"])
            actual = semantic_signature(state["target_checkpoint"], state["output_state"])
            expected = state["expected_oracle"].get("signature") or state["expected_oracle"]
            comparison = {
                "matches": actual == expected,
                "expected": expected,
                "actual": actual,
            }
            verdict = report["status"]
            if verdict == "passed" and not comparison["matches"]:
                verdict = "needs_review"
        write_json(job_dir / "validation" / "validators.json", report)
        write_json(job_dir / "validation" / "semantic-diff.json", comparison)
        write_json(job_dir / "validation" / "verdict.json", {"verdict": verdict})
        return {"validation": {"validators": report, "comparison": comparison}, "verdict": verdict}

    def finish(state: HarnessState) -> dict[str, Any]:
        manifest = {
            "schemaVersion": "easydep-checkpoint-e2e-run",
            "runId": state["run_id"],
            "caseId": state["case_id"],
            "sourceCheckpoint": state["source_checkpoint"],
            "targetCheckpoint": state.get("target_checkpoint"),
            "verdict": state.get("verdict", "failed"),
            "error": state.get("error"),
            "tasks": state.get("tasks") or [],
            "files": (state.get("render") or {}).get("files") or [],
            "completedAt": _now(),
        }
        write_json(Path(state["job_dir"]) / "manifest.json", manifest)
        return {}

    builder.add_node("load_gold_checkpoint", load_source)
    builder.add_node("execute_target_checkpoint", execute)
    builder.add_node("render_evidence", render)
    builder.add_node("validate_evidence", validate)
    builder.add_node("write_manifest", finish)
    builder.add_edge(START, "load_gold_checkpoint")
    builder.add_edge("load_gold_checkpoint", "execute_target_checkpoint")
    builder.add_edge("execute_target_checkpoint", "render_evidence")
    builder.add_edge("render_evidence", "validate_evidence")
    builder.add_edge("validate_evidence", "write_manifest")
    builder.add_edge("write_manifest", END)
    return builder.compile()


def _new_run_id(case_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{case_id}-{stamp}-{uuid.uuid4().hex[:8]}"


def _staging_path(destination: Path) -> Path:
    """Create a sibling location that can be published atomically by rename."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination.with_name(
        f".{destination.name}.staging-{uuid.uuid4().hex[:8]}"
    )


def _backup_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.previous-{uuid.uuid4().hex[:8]}")


def _publish_staging(staging: Path, destination: Path) -> None:
    """Replace only the requested current destination after staging completes.

    The old current tree is kept until the new one has been renamed into place.
    It is then removed as part of the explicit replacement; goldset locations
    are never passed to this helper by the experiment commands.
    """

    if not staging.is_dir():
        raise ValueError(f"Staging output is missing: {staging}")
    if not destination.exists():
        staging.rename(destination)
        return
    if not destination.is_dir():
        raise FileExistsError(f"Current destination is not a directory: {destination}")

    previous = _backup_path(destination)
    destination.rename(previous)
    try:
        staging.rename(destination)
    except Exception:
        previous.rename(destination)
        raise
    shutil.rmtree(previous)


def _prepare_staging(
    destination: Path, *, resume: bool, replace: bool
) -> Path:
    """Prepare a fresh staging tree without changing the published output."""

    if destination.exists():
        if not destination.is_dir():
            raise FileExistsError(f"Destination is not a directory: {destination}")
        if not (resume or replace):
            raise FileExistsError(
                f"Current experiment exists: {destination}; use --resume or --replace"
            )
    staging = _staging_path(destination)
    if resume and destination.exists():
        shutil.copytree(destination, staging)
    else:
        staging.mkdir()
    return staging


def _reject_gold_destination(destination: Path) -> None:
    """Experiments may observe gold, but must never publish into it."""

    try:
        destination.resolve().relative_to(GOLD_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("Experiment output cannot be written inside the goldset")


def run_one(
    case_id: str,
    source_checkpoint: str,
    *,
    output_root: Path = RUN_ROOT,
    run_id: str | None = None,
) -> dict[str, Any]:
    target = checkpoint_after(source_checkpoint)
    run_id = run_id or _new_run_id(case_id)
    root = output_root / run_id
    job_dir = root / "jobs" / _job_name(source_checkpoint, target)
    if job_dir.exists():
        raise FileExistsError(f"Job output already exists: {job_dir}")
    state: HarnessState = {
        "case_id": case_id,
        "source_checkpoint": source_checkpoint,
        "target_checkpoint": target,
        "run_id": run_id,
        "job_dir": str(job_dir),
    }
    build_harness_graph().invoke(state)
    manifest = read_json(job_dir / "manifest.json")
    summary = {
        "schemaVersion": "easydep-checkpoint-e2e-summary",
        "runId": run_id,
        "caseId": case_id,
        "jobs": [manifest],
    }
    write_json(root / "summary.json", summary)
    return {"root": str(root), **manifest}


def run_all(
    case_id: str,
    *,
    output_root: Path = RUN_ROOT,
    run_id: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    run_id = run_id or _new_run_id(case_id)
    root = output_root / run_id
    jobs: list[dict[str, Any]] = []
    for source in CHECKPOINTS[:-1]:
        target = checkpoint_after(source)
        job_dir = root / "jobs" / _job_name(source, target)
        manifest_path = job_dir / "manifest.json"
        if resume and manifest_path.is_file():
            manifest = read_json(manifest_path)
            if (
                manifest.get("caseId") == case_id
                and manifest.get("sourceCheckpoint") == source
                and manifest.get("targetCheckpoint") == target
            ):
                jobs.append(manifest)
                continue
        if job_dir.exists():
            raise FileExistsError(
                f"Incomplete or incompatible job output exists: {job_dir}"
            )
        run_one(case_id, source, output_root=output_root, run_id=run_id)
        jobs.append(read_json(manifest_path))
    summary = {
        "schemaVersion": "easydep-checkpoint-e2e-summary",
        "runId": run_id,
        "caseId": case_id,
        "verdict": (
            "failed"
            if any(item["verdict"] == "failed" for item in jobs)
            else "needs_review"
            if any(item["verdict"] == "needs_review" for item in jobs)
            else "passed"
        ),
        "jobs": jobs,
    }
    write_json(root / "summary.json", summary)
    return {"root": str(root), **summary}


def _sample_manifest(root: Path, source_checkpoint: str, index: int) -> dict[str, Any]:
    target = checkpoint_after(source_checkpoint)
    path = root / f"sample-{index:02d}" / "jobs" / _job_name(source_checkpoint, target)
    return read_json(path / "manifest.json")


def _stage_summary(
    case_id: str, source_checkpoint: str, samples: list[dict[str, Any]], *, status: str,
    error: str | None = None,
) -> dict[str, Any]:
    verdict_counts = {
        verdict: sum(
            str(sample.get("verdict", "failed")) == verdict for sample in samples
        )
        for verdict in ("passed", "needs_review", "failed")
    }
    if status != "complete" or verdict_counts["failed"]:
        verdict = "failed"
    elif verdict_counts["needs_review"]:
        verdict = "needs_review"
    else:
        verdict = "passed"
    return {
        "schemaVersion": "easydep-checkpoint-e2e-stage-samples", "caseId": case_id,
        "sourceCheckpoint": source_checkpoint, "targetCheckpoint": checkpoint_after(source_checkpoint),
        "status": status, "error": error, "verdict": verdict, "sampleCount": len(samples),
        "verdictCounts": verdict_counts,
        "samples": [{"id": f"sample-{index:02d}", "verdict": item.get("verdict", "failed")}
                    for index, item in enumerate(samples, start=1)],
    }


def _completed_stage(
    root: Path, case_id: str, source_checkpoint: str, sample_count: int
) -> dict[str, Any] | None:
    try:
        summary = read_json(root / "manifest.json")
    except (OSError, ValueError):
        return None
    target = checkpoint_after(source_checkpoint)
    if (
        summary.get("schemaVersion") != "easydep-checkpoint-e2e-stage-samples"
        or summary.get("status") != "complete"
        or summary.get("caseId") != case_id
        or summary.get("sourceCheckpoint") != source_checkpoint
        or summary.get("targetCheckpoint") != target
        or summary.get("sampleCount") != sample_count
    ):
        return None
    try:
        manifests = [_sample_manifest(root, source_checkpoint, index) for index in range(1, sample_count + 1)]
    except (OSError, ValueError):
        return None
    if any(
        item.get("caseId") != case_id
        or item.get("sourceCheckpoint") != source_checkpoint
        or item.get("targetCheckpoint") != target
        for item in manifests
    ):
        return None
    return summary


def run_stage_samples(
    case_id: str, source_checkpoint: str, *, destination: Path | None = None,
    samples: int = 3, resume: bool = False, replace: bool = False,
) -> dict[str, Any]:
    """Run fixed isolated gold-sourced samples at one stable stage path."""

    if samples < 1:
        raise ValueError("Stage sample count must be at least one")
    destination = destination or current_stage_sample_path(case_id, source_checkpoint)
    _reject_gold_destination(destination)
    if destination.exists() and resume:
        completed = _completed_stage(destination, case_id, source_checkpoint, samples)
        if completed is not None:
            return {"root": str(destination), **completed}
        raise FileExistsError("Stage output is incomplete or has a different identity; use --replace")

    staging = _prepare_staging(destination, resume=False, replace=replace)
    completed_samples: list[dict[str, Any]] = []
    try:
        for index in range(1, samples + 1):
            run_one(case_id, source_checkpoint, output_root=staging, run_id=f"sample-{index:02d}")
            manifest = _sample_manifest(staging, source_checkpoint, index)
            target = checkpoint_after(source_checkpoint)
            if (manifest.get("caseId"), manifest.get("sourceCheckpoint"), manifest.get("targetCheckpoint")) != (case_id, source_checkpoint, target):
                raise RuntimeError(f"Stage sample identity mismatch: sample-{index:02d}")
            completed_samples.append(manifest)
        summary = _stage_summary(case_id, source_checkpoint, completed_samples, status="complete")
    except Exception as error:
        summary = _stage_summary(case_id, source_checkpoint, completed_samples, status="failed", error=f"{type(error).__name__}: {error}")
        write_json(staging / "manifest.json", summary)
        _publish_staging(staging, destination)
        raise
    write_json(staging / "manifest.json", summary)
    _publish_staging(staging, destination)
    return {"root": str(destination), **summary}
def git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def generate_candidate(
    case_id: str,
    destination: Path | None = None,
    *,
    resume: bool = False,
    replace: bool = False,
    restart_from: str | None = None,
    through: str = CHECKPOINTS[-1],
) -> dict[str, Any]:
    """Generate a promotable candidate without ever modifying the goldset.

    The default destination is the stable current chain for the case.  A full
    candidate is directly eligible for the existing validation/promotion flow.
    """

    destination = destination or current_chain_path(case_id)
    _reject_gold_destination(destination)
    return _run_staged_chain(
        case_id,
        destination,
        resume=resume,
        replace=replace,
        restart_from=restart_from,
        through=through,
    )


def _truncate_candidate(destination: Path, case_id: str, restart_from: str) -> None:
    """Keep a verified prefix and remove stale downstream candidate evidence."""

    if restart_from not in CHECKPOINTS[:-1]:
        raise ValueError(f"Candidate restart checkpoint is invalid: {restart_from}")
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Candidate restart requires an existing manifest")
    manifest = read_json(manifest_path)
    if manifest.get("caseId") != case_id:
        raise ValueError("Candidate case does not match the requested case")
    entries = list(manifest.get("checkpoints") or [])
    ids = [item.get("id") for item in entries]
    keep_count = CHECKPOINTS.index(restart_from) + 1
    if ids[:keep_count] != list(CHECKPOINTS[:keep_count]):
        raise ValueError(f"Candidate restart checkpoint is absent: {restart_from}")

    for item in entries[:keep_count]:
        checkpoint = str(item["id"])
        value = read_json(destination / "snapshots" / checkpoint / "state.json")
        if digest(value) != item.get("sha256"):
            raise ValueError(f"Candidate checkpoint digest mismatch: {checkpoint}")

    for checkpoint in CHECKPOINTS[keep_count:]:
        snapshot = destination / "snapshots" / checkpoint
        oracle = destination / "oracles" / f"{checkpoint}.json"
        if snapshot.exists():
            shutil.rmtree(snapshot)
        if oracle.exists():
            oracle.unlink()
        stage = destination / "stages" / _job_name(
            CHECKPOINTS[CHECKPOINTS.index(checkpoint) - 1], checkpoint
        )
        if stage.exists():
            shutil.rmtree(stage)
    failures = destination / "failures"
    if failures.exists():
        shutil.rmtree(failures)
    manifest.update(status="in_progress", error=None, checkpoints=entries[:keep_count])
    write_json(manifest_path, manifest)


def _generate_chain_at(
    case_id: str,
    destination: Path,
    *,
    resume: bool,
    through: str,
) -> dict[str, Any]:
    if through not in CHECKPOINTS[1:]:
        raise ValueError(f"Candidate target is invalid: {through}")
    case = case_definition(case_id)
    checkpoints: list[dict[str, Any]] = []

    def write_evidence(
        checkpoint: str,
        value: dict[str, Any],
        validation: dict[str, Any],
        tasks: list[dict[str, Any]],
    ) -> None:
        index = CHECKPOINTS.index(checkpoint)
        source = CHECKPOINTS[index - 1]
        root = destination / "stages" / _job_name(source, checkpoint)
        write_json(root / "tasks.json", tasks)
        write_json(root / "validation.json", validation)
        write_json(root / "signature.json", semantic_signature(checkpoint, value))
        rendered = write_outputs(checkpoint, value, root / "output")
        write_json(
            root / "manifest.json",
            {
                "sourceCheckpoint": source,
                "targetCheckpoint": checkpoint,
                "validation": validation.get("status"),
                "tasks": len(tasks),
                "files": rendered.get("files") or [],
            },
        )

    def save(checkpoint: str, value: dict[str, Any]) -> None:
        from .catalog import oracle_path, snapshot_path

        cleaned = jsonable(value)
        write_json(snapshot_path(destination, checkpoint), cleaned)
        signature = semantic_signature(checkpoint, cleaned) if checkpoint != "input" else {"input": case_id}
        write_json(oracle_path(destination, checkpoint), {"signature": signature})
        checkpoints.append({"id": checkpoint, "sha256": digest(cleaned)})

    def write_manifest(status: str, error: str | None = None) -> dict[str, Any]:
        manifest = {
            "schemaVersion": "easydep-checkpoint-goldset",
            "caseId": case_id,
            "sourceCommit": git_revision(),
            "input": case["inputPath"],
            "status": status,
            "error": error,
            "checkpoints": checkpoints,
        }
        write_json(destination / "manifest.json", manifest)
        return manifest

    state = initial_candidate_state(case)
    start_index = 0
    if resume and (destination / "manifest.json").is_file():
        existing = read_json(destination / "manifest.json")
        if existing.get("caseId") != case_id:
            raise ValueError("Candidate case does not match the requested case")
        existing_ids = [item.get("id") for item in existing.get("checkpoints") or []]
        if existing_ids != list(CHECKPOINTS[: len(existing_ids)]):
            raise ValueError("Candidate checkpoints are not a valid prefix")
        for item in existing.get("checkpoints") or []:
            checkpoint = str(item["id"])
            value = read_json(destination / "snapshots" / checkpoint / "state.json")
            if digest(value) != item.get("sha256"):
                raise ValueError(f"Candidate checkpoint digest mismatch: {checkpoint}")
            checkpoints.append(item)
            state = value
            if checkpoint != "input" and not (
                destination
                / "stages"
                / _job_name(CHECKPOINTS[CHECKPOINTS.index(checkpoint) - 1], checkpoint)
                / "manifest.json"
            ).is_file():
                write_evidence(
                    checkpoint,
                    state,
                    validate_state(checkpoint, state),
                    [],
                )
        start_index = max(0, len(checkpoints) - 1)
        if existing.get("status") == "failed" and checkpoints:
            source = str(checkpoints[-1]["id"])
            if source != through:
                failed_target = checkpoint_after(source)
                failed_state = destination / "failures" / failed_target / "state.json"
                if failed_state.is_file():
                    state = read_json(failed_state)
        if len(checkpoints) > 1:
            latest = str(checkpoints[-1]["id"])
            if validate_state(latest, state).get("status") == "failed":
                # Retain the failed artifact in state so its stage extractor can
                # resume owned units, but replace its checkpoint evidence.
                checkpoints.pop()
                start_index = max(0, len(checkpoints) - 1)

    if not checkpoints:
        save("input", state)
        write_manifest("in_progress")
    target_index = CHECKPOINTS.index(through)
    for source in CHECKPOINTS[start_index:target_index]:
        target = checkpoint_after(source)
        records: list[dict[str, Any]] = []

        def record(task: str, _before: dict, _delta: dict, elapsed: float) -> None:
            records.append({"task": task, "elapsedSeconds": round(elapsed, 6)})

        try:
            _target, state = run_transition(source, state, record)
            validation = validate_state(target, state)
            if validation["status"] == "failed":
                # Invalid diagrams are still valuable visual evidence.  Keep the
                # same stage layout as successful checkpoints so a failed run
                # can be inspected without manually rendering its state.
                write_evidence(target, state, validation, records)
                write_json(
                    destination / "failures" / target / "state.json",
                    jsonable(state),
                )
                write_json(
                    destination / "failures" / target / "validation.json",
                    validation,
                )
                raise RuntimeError(
                    f"Candidate {target} is invalid: {validation['errors']}"
                )
            write_evidence(target, state, validation, records)
            save(target, state)
            write_manifest("in_progress")
        except Exception as error:
            write_manifest("failed", f"{type(error).__name__}: {error}")
            raise
    return write_manifest(
        "complete" if through == CHECKPOINTS[-1] else "in_progress"
    )


def _run_staged_chain(
    case_id: str,
    destination: Path,
    *,
    resume: bool,
    replace: bool,
    restart_from: str | None,
    through: str,
) -> dict[str, Any]:
    """Build beside current, publishing the final success or failure record."""

    resuming_current = resume and destination.exists()
    if restart_from is not None:
        if not resuming_current or replace:
            raise ValueError("--restart-from requires an existing candidate and --resume")
        if CHECKPOINTS.index(through) <= CHECKPOINTS.index(restart_from):
            raise ValueError("Candidate target must follow the restart checkpoint")
    staging = _prepare_staging(destination, resume=resume, replace=replace)
    try:
        if restart_from is not None:
            _truncate_candidate(staging, case_id, restart_from)
        manifest = _generate_chain_at(
            case_id,
            staging,
            resume=resuming_current,
            through=through,
        )
    except Exception:
        # Validation/transition failures have a manifest and often diagnostic
        # evidence.  Publish that useful state; only empty setup staging is
        # cleaned, so successful or failed runs never leave hidden siblings.
        if (staging / "manifest.json").is_file():
            _publish_staging(staging, destination)
        elif staging.exists():
            shutil.rmtree(staging)
        raise
    _publish_staging(staging, destination)
    return manifest


def initial_candidate_state(case: dict[str, Any]) -> dict[str, Any]:
    from .transitions import initial_state

    return initial_state(case)


def seed_candidate_prefix(
    case_id: str,
    source: Path,
    destination: Path,
    *,
    through: str = "erd",
) -> dict[str, Any]:
    """Rebase a verified functional-design prefix onto deployment-only case facts."""

    if destination.exists():
        raise FileExistsError(f"Candidate already exists: {destination}")
    if through not in CHECKPOINTS:
        raise ValueError(f"Unknown checkpoint: {through}")
    source_manifest = read_json(source / "manifest.json")
    source_entries = {
        str(item.get("id")): item for item in source_manifest.get("checkpoints") or []
    }
    case = case_definition(case_id)
    additions = initial_candidate_state(case)
    checkpoints: list[dict[str, Any]] = []
    for checkpoint in CHECKPOINTS[: CHECKPOINTS.index(through) + 1]:
        entry = source_entries.get(checkpoint)
        if entry is None:
            raise ValueError(f"Seed checkpoint is absent: {checkpoint}")
        state = read_json(source / "snapshots" / checkpoint / "state.json")
        if digest(state) != entry.get("sha256"):
            raise ValueError(f"Seed checkpoint digest mismatch: {checkpoint}")
        if list(state.get("raw_requirements") or []) != list(case["requirements"]):
            raise ValueError(f"Seed requirements differ at checkpoint: {checkpoint}")
        state["_case"] = additions["_case"]
        state["deployment_planning_facts"] = additions["deployment_planning_facts"]
        write_json(destination / "snapshots" / checkpoint / "state.json", state)
        signature = (
            semantic_signature(checkpoint, state)
            if checkpoint != "input"
            else {"input": case_id}
        )
        write_json(destination / "oracles" / f"{checkpoint}.json", {"signature": signature})
        checkpoints.append({"id": checkpoint, "sha256": digest(state)})
    manifest = {
        "schemaVersion": "easydep-checkpoint-goldset",
        "caseId": case_id,
        "sourceCommit": git_revision(),
        "input": case["inputPath"],
        "status": "in_progress",
        "error": None,
        "seed": {
            "path": str(source),
            "through": through,
            "rule": "deployment-only-facts-do-not-change-functional-design",
        },
        "checkpoints": checkpoints,
    }
    write_json(destination / "manifest.json", manifest)
    return manifest


def validate_candidate(path: Path) -> dict[str, Any]:
    manifest = read_json(path / "manifest.json")
    errors: list[str] = []
    schema_version = manifest.get("schemaVersion")
    if schema_version not in {None, "easydep-checkpoint-goldset"}:
        errors.append(f"candidate manifest is not promotable: {schema_version}")
    if manifest.get("status") not in {None, "complete"}:
        errors.append(f"candidate status is {manifest.get('status')}")
    ids = [item.get("id") for item in manifest.get("checkpoints") or []]
    if tuple(ids) != CHECKPOINTS:
        errors.append(f"checkpoint order differs: {ids}")
    from .catalog import oracle_path, snapshot_path

    for item in manifest.get("checkpoints") or []:
        checkpoint = str(item.get("id") or "")
        try:
            state = read_json(snapshot_path(path, checkpoint))
            oracle = read_json(oracle_path(path, checkpoint))
        except (OSError, ValueError) as error:
            errors.append(f"{checkpoint}: {error}")
            continue
        if digest(state) != item.get("sha256"):
            errors.append(f"{checkpoint}: digest mismatch")
        if checkpoint != "input":
            report = validate_state(checkpoint, state)
            errors.extend(f"{checkpoint}: {message}" for message in report["errors"])
            if oracle.get("signature") != semantic_signature(checkpoint, state):
                errors.append(f"{checkpoint}: oracle signature mismatch")
    return {"status": "failed" if errors else "passed", "errors": errors}


def promote_candidate(path: Path, case_id: str) -> Path:
    report = validate_candidate(path)
    if report["status"] != "passed":
        raise ValueError(f"Candidate validation failed: {report['errors']}")
    target = Path(case_definition(case_id)["goldPath"])
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}-staging-{uuid.uuid4().hex[:8]}")
    staging.mkdir()
    shutil.copy2(path / "manifest.json", staging / "manifest.json")
    shutil.copytree(path / "snapshots", staging / "snapshots")
    shutil.copytree(path / "oracles", staging / "oracles")
    if target.exists():
        backup = target.with_name(f".{target.name}-backup-{uuid.uuid4().hex[:8]}")
        target.rename(backup)
        try:
            staging.rename(target)
        except Exception:
            backup.rename(target)
            raise
        shutil.rmtree(backup)
    else:
        staging.rename(target)
    return target
