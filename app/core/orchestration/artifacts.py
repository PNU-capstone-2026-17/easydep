"""Write every stage output once under a single run directory."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.core.run_identity import identity_manifest

DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")


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
            "build", ".gradle", ".terraform", "hs_err_pid*.log", "replay_pid*.log"
        ),
    )


def persist_run_artifacts(
    run_id: str,
    state: dict[str, Any],
    *,
    root: Path = DEFAULT_ARTIFACT_ROOT,
) -> Path:
    run_dir = root / run_id
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
        }
    )
    _write(run_dir / "manifest.json", manifest)

    directories = {
        "requirements": "01-requirements",
        "design": "02-design",
        "implementation": "03-implementation",
        "testing": "04-testing",
    }
    for name, value in stage_values.items():
        if value:
            _write(run_dir / directories[name] / "result.json", value)

    requirements = (stage_values["requirements"].get("data") or {}).get(
        "member_result", {}
    )
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
                (design.get("cloud_design_result") or {}).get(
                    "deployment_diagram_puml", ""
                ),
            ),
        ):
            _write(run_dir / "02-design" / filename, value)

    implementation = stage_values["implementation"].get("data") or {}
    if implementation.get("run_root"):
        _copy_application(
            str(implementation["run_root"]),
            run_dir / "03-implementation" / "application",
        )
    return run_dir
