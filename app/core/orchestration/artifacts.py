"""Persist user-visible orchestration outputs under the repository artifacts tree."""

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
        return
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _copy_implementation(run_root: Path, target: Path) -> None:
    """Copy durable implementation outputs, excluding disposable build state."""
    if not run_root.is_dir():
        return
    for name in ("application", "reports"):
        source = run_root / name
        if source.is_dir():
            shutil.copytree(
                source,
                target / name,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    "build", ".gradle", "hs_err_pid*.log", "replay_pid*.log"
                ),
            )
    for name in ("implementation-manifest.json", "workflow-state.json"):
        source = run_root / name
        if source.is_file():
            shutil.copy2(source, target / name)


def persist_run_artifacts(
    run_id: str,
    state: dict[str, Any],
    *,
    root: Path = DEFAULT_ARTIFACT_ROOT,
) -> Path:
    """Materialize every available stage output in one run-scoped directory."""
    run_dir = root / run_id
    requirements = state.get("requirements_result") or {}
    design = state.get("design_result") or {}
    cloud = state.get("cloud_design_result") or {}
    infrastructure = state.get("infrastructure_recommendation") or {}
    implementation = state.get("implementation_result") or {}
    testing = state.get("testing_result") or {}

    # Migrate the short-lived five-directory layout. Infrastructure is design
    # enrichment, not a user-visible workflow stage.
    legacy_implementation = run_dir / "04-implementation"
    implementation_stage = run_dir / "03-implementation"
    if legacy_implementation.is_dir():
        shutil.copytree(legacy_implementation, implementation_stage, dirs_exist_ok=True)
        shutil.rmtree(legacy_implementation)
    legacy_infrastructure = run_dir / "03-infrastructure"
    if legacy_infrastructure.is_dir():
        shutil.rmtree(legacy_infrastructure)

    completed_stages = [
        name
        for name, available in (
            ("requirements", bool(requirements)),
            ("design", bool(design or cloud)),
            ("implementation", bool(implementation)),
            ("testing", bool(testing)),
        )
        if available
    ]
    manifest = identity_manifest(
        run_id,
        system="easydep",
        variant=str(state.get("run_variant") or "full"),
        case_id=str(state.get("case_id") or "adhoc"),
        purpose=str(state.get("run_purpose") or "normal"),
        completed_stages=completed_stages,
    )
    manifest.update(
        {
            "appId": state.get("app_id", ""),
            "currentStage": state.get("current_stage", ""),
            "status": state.get("status", ""),
            "stages": {
                "requirements": bool(requirements),
                "design": bool(design or cloud),
                "implementation": bool(implementation),
                "testing": bool(testing),
            },
        }
    )
    _write(run_dir / "manifest.json", manifest)

    if requirements:
        stage = run_dir / "01-requirements"
        _write(stage / "result.json", requirements)
        _write(stage / "input-requirements.json", state.get("requirements") or [])
        _write(
            stage / "resource-constraints.txt",
            str(state.get("resource_constraints_text") or ""),
        )

    if design or cloud:
        stage = run_dir / "02-design"
        artifacts = design.get("artifacts") or {}
        _write(stage / "result.json", design)
        _write(stage / "cloud-design.json", cloud)
        _write(stage / "class.puml", artifacts.get("class_diagram") or "")
        _write(stage / "sequence.puml", artifacts.get("sequence_diagram") or "")
        _write(stage / "openapi.json", artifacts.get("api_spec") or {})
        _write(stage / "erd.puml", artifacts.get("erd") or "")
        _write(
            stage / "deployment-logical.puml",
            cloud.get("logical_deployment_diagram_puml")
            or design.get("logical_deployment_diagram_puml")
            or "",
        )
        _write(
            stage / "deployment-cloud.puml",
            cloud.get("deployment_diagram_puml")
            or design.get("deployment_diagram_puml")
            or "",
        )

    if infrastructure:
        _write(
            run_dir
            / "02-design"
            / "cloud-native"
            / "provisional-infrastructure-recommendation.json",
            infrastructure,
        )

    if implementation:
        stage = implementation_stage
        _write(stage / "result.json", implementation)
        run_root = Path(str(implementation.get("run_root") or ""))
        if str(run_root) not in {"", "."}:
            _copy_implementation(run_root, stage)

    if testing:
        _write(run_dir / "04-testing" / "result.json", testing)

    return run_dir
