"""Write every stage output once under a single run directory."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from app.core.run_identity import identity_manifest

DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _valid_application_checkpoint(
    checkpoint: Path,
    run_id: str,
    app_id: str | None,
    expected_attempt: int,
    expected_revision: int,
) -> Path | None:
    source = checkpoint / "03-implementation" / "application"
    manifest_path = checkpoint / "manifest.json"
    if not source.is_dir() or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("runId") != run_id or "implementation" not in manifest.get(
        "completedStages", []
    ):
        return None
    if app_id is not None and manifest.get("appId") != app_id:
        return None
    if manifest.get("checkpointAttempt") != expected_attempt:
        return None
    if manifest.get("requirementRevision", 0) != expected_revision:
        return None
    expected = manifest.get("applicationSha256")
    if not expected or _tree_sha256(source) != expected:
        return None
    return source.resolve()


def _timing_summary(stage_values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for stage, value in stage_values.items():
        for step in value.get("steps") or []:
            metrics = step.get("metrics") or {}
            timing = metrics.get("timing") or {}
            elapsed = timing.get("elapsedSeconds")
            if isinstance(elapsed, (int, float)):
                stages.append(
                    {
                        "stage": stage,
                        "step": step.get("step"),
                        "status": step.get("status"),
                        "elapsedSeconds": elapsed,
                        "startedAt": timing.get("startedAt"),
                        "finishedAt": timing.get("finishedAt"),
                    }
                )
            for collection in ("llm_timing_events", "timing_events"):
                for event in metrics.get(collection) or []:
                    event_elapsed = event.get("elapsedSeconds")
                    if not isinstance(event_elapsed, (int, float)):
                        continue
                    events.append(
                        {
                            "stage": stage,
                            "step": step.get("step"),
                            "operation": event.get("operation"),
                            "attempt": event.get("attempt"),
                            "status": event.get("status"),
                            "elapsedSeconds": event_elapsed,
                            "startedAt": event.get("startedAt"),
                            "finishedAt": event.get("finishedAt"),
                        }
                    )
    return {
        "schemaVersion": "easydep-timing-summary/v1",
        "stageRanking": sorted(stages, key=lambda item: item["elapsedSeconds"], reverse=True),
        "subtaskRanking": sorted(events, key=lambda item: item["elapsedSeconds"], reverse=True),
        "interpretation": {
            "stageElapsedUsesWallClock": True,
            "subtaskElapsedMayOverlap": True,
            "subtaskDurationsMustNotBeSummedAsCriticalPath": True,
        },
    }


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


def _copy_application(run_root: str, target: Path) -> None:
    source = Path(run_root) / "application"
    if not source.is_dir():
        return
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "build",
            ".gradle",
            ".terraform",
            ".easydep-test-*",
            "hs_err_pid*.log",
            "replay_pid*.log",
        ),
    )


def restore_run_application(
    run_id: str,
    run_root: str | Path,
    *,
    root: Path = DEFAULT_ARTIFACT_ROOT,
    checkpoint_attempt: int = 0,
    requirement_revision: int = 0,
    allow_prior_checkpoint: bool = False,
    expected_app_id: str | None = None,
) -> Path:
    """Restore a cleaned run workspace from its immutable application checkpoint."""
    workspace_root = Path(".easydep/orchestration/workspaces").resolve()
    destination_root = Path(run_root).resolve()
    if destination_root.parent != workspace_root:
        raise ValueError(f"refusing to restore unexpected workspace: {destination_root}")
    revision_root = (
        root / run_id / "revisions" / f"revision-{requirement_revision}"
        if requirement_revision > 0
        else root / run_id
    )
    checkpoint = (
        revision_root / "repairs" / f"attempt-{checkpoint_attempt}"
        if checkpoint_attempt > 0
        else revision_root
    )
    source = _valid_application_checkpoint(
        checkpoint,
        run_id,
        expected_app_id,
        checkpoint_attempt,
        requirement_revision,
    )
    if source is None and allow_prior_checkpoint and checkpoint_attempt > 0:
        for attempt in range(checkpoint_attempt - 1, -1, -1):
            candidate_root = (
                revision_root / "repairs" / f"attempt-{attempt}"
                if attempt > 0
                else revision_root
            )
            candidate = _valid_application_checkpoint(
                candidate_root, run_id, expected_app_id, attempt, requirement_revision
            )
            if candidate is not None:
                source = candidate
                break
    if source is None:
        raise FileNotFoundError(f"application checkpoint is absent: {source}")
    destination = destination_root / "application"
    if destination_root.exists():
        raise FileExistsError(f"retry workspace already exists: {destination_root}")
    destination_root.mkdir(parents=True)
    shutil.copytree(source, destination)
    return destination


def persist_run_artifacts(
    run_id: str,
    state: dict[str, Any],
    *,
    root: Path = DEFAULT_ARTIFACT_ROOT,
) -> Path:
    base_run_dir = root / run_id
    retry_history = list(state.get("retryHistory") or [])
    revision_history = list(state.get("requirementRevisionHistory") or [])
    revision_root = (
        base_run_dir / "revisions" / f"revision-{len(revision_history)}"
        if revision_history
        else base_run_dir
    )
    run_dir = (
        revision_root / "repairs" / f"attempt-{len(retry_history)}"
        if retry_history
        else revision_root
    )
    request = state.get("request") or {}
    stage_values = {
        "requirements": state.get("requirements") or {},
        "design": state.get("design") or {},
        "implementation": state.get("implementation") or {},
        "testing": state.get("testing") or {},
    }
    completed = [name for name, value in stage_values.items() if value]
    manifest = identity_manifest(
        run_id,
        system="easydep",
        variant=str(request.get("variant") or "full"),
        case_id=str(request.get("case_id") or "adhoc"),
        purpose=str(request.get("purpose") or "normal"),
        completed_stages=completed,
    )
    manifest.update(
        {
            "appId": state.get("app_id"),
            "mode": request.get("mode"),
            "providers": request.get("providers") or {},
            "currentStage": state.get("current_stage"),
            "status": state.get("status"),
            "error": state.get("error") or None,
            "developmentRepair": bool(retry_history),
            "parentRunId": run_id if retry_history else None,
            "retryHistory": retry_history,
            "checkpointAttempt": len(retry_history),
            "requirementRevision": len(revision_history),
        }
    )
    directories = {
        "requirements": "01-requirements",
        "design": "02-design",
        "implementation": "03-implementation",
        "testing": "04-testing",
    }
    for name, value in stage_values.items():
        if value:
            _write(run_dir / directories[name] / "result.json", value)
    _write(run_dir / "timing-summary.json", _timing_summary(stage_values))

    requirements = (stage_values["requirements"].get("data") or {}).get("member_result", {})
    if requirements:
        _write(run_dir / "01-requirements" / "input.json", request.get("requirements") or [])

    design = stage_values["design"].get("data") or {}
    design_result = design.get("design_result") or {}
    design_artifacts = design_result.get("artifacts") or {}
    if design_result:
        for filename, value in (
            ("class.puml", design_artifacts.get("class_diagram") or ""),
            ("sequence.puml", design_artifacts.get("sequence_diagram") or ""),
            ("openapi.json", design_artifacts.get("api_spec") or {}),
            ("erd.puml", design_artifacts.get("erd") or ""),
            (
                "deployment.puml",
                (design.get("cloud_design_result") or {}).get("deployment_diagram_puml", ""),
            ),
        ):
            _write(run_dir / "02-design" / filename, value)

    implementation = stage_values["implementation"].get("data") or {}
    if implementation.get("run_root"):
        _copy_application(
            str(implementation["run_root"]),
            run_dir / "03-implementation" / "application",
        )
    persisted_application = run_dir / "03-implementation" / "application"
    if persisted_application.is_dir():
        # 복사에서 build/cache를 제외하므로 원본이 아니라 실제 불변 snapshot을 해시한다.
        manifest["applicationSha256"] = _tree_sha256(persisted_application)
    _write(run_dir / "manifest.json", manifest)
    return run_dir
