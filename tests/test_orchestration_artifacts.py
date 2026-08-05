from __future__ import annotations

import json
from pathlib import Path

from app.core.orchestration.artifacts import persist_run_artifacts


def test_persist_run_artifacts_exports_every_available_stage(tmp_path: Path):
    implementation_root = tmp_path / "implementation-run"
    source = implementation_root / "application" / "src" / "main" / "App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    build_file = implementation_root / "application" / "build" / "temporary.txt"
    build_file.parent.mkdir(parents=True)
    build_file.write_text("temporary", encoding="utf-8")
    report = implementation_root / "reports" / "workflow-state.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"status":"RUNNING"}', encoding="utf-8")

    run_dir = persist_run_artifacts(
        "run-1",
        {
            "app_id": "sample",
            "requirements": ["The system shall run."],
            "requirements_result": {"status": "completed"},
            "design_result": {
                "status": "completed",
                "artifacts": {
                    "class_diagram": "@startuml\n@enduml",
                    "sequence_diagram": "sequence",
                    "api_spec": {"openapi": "3.0.0"},
                    "erd": "erd",
                },
            },
            "cloud_design_result": {
                "logical_deployment_diagram_puml": "logical",
                "deployment_diagram_puml": "cloud",
            },
            "infrastructure_recommendation": {"status": "provisional"},
            "implementation_result": {
                "status": "needs_approval",
                "run_root": str(implementation_root),
            },
            "testing_result": {"status": "deferred"},
            "current_stage": "implementation",
            "status": "needs_input",
        },
        root=tmp_path / "artifacts",
    )

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["runId"] == "run-1"
    assert manifest["system"] == "easydep"
    assert manifest["variant"] == "full"
    assert manifest["caseId"] == "adhoc"
    assert manifest["purpose"] == "normal"
    assert manifest["completedStages"] == [
        "requirements",
        "design",
        "implementation",
        "testing",
    ]
    assert list(manifest["stages"]) == [
        "requirements",
        "design",
        "implementation",
        "testing",
    ]
    assert all(manifest["stages"].values())
    assert (run_dir / "01-requirements" / "result.json").is_file()
    assert (run_dir / "02-design" / "deployment-cloud.puml").read_text() == "cloud"
    assert (
        run_dir
        / "02-design/cloud-native/provisional-infrastructure-recommendation.json"
    ).is_file()
    assert (run_dir / "03-implementation" / "application/src/main/App.java").is_file()
    assert (run_dir / "03-implementation" / "reports/workflow-state.json").is_file()
    assert not (run_dir / "03-implementation" / "application/build").exists()
    assert (run_dir / "04-testing" / "result.json").is_file()
