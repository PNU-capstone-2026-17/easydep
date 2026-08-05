from __future__ import annotations

import json
from pathlib import Path

from app.core.orchestration.adapters.design import DesignAdapter
from app.core.orchestration.adapters.implementation import ImplementationAdapter
from app.core.orchestration.adapters.infrastructure import (
    InfrastructureRecommendationAdapter,
)
from app.core.orchestration.implementation_runner import (
    _apply_repair_directives,
    _prioritize_repair_owners,
)
from app.implementation.prototype_client import PrototypeExecutionError


def test_provisional_recommendation_is_explicitly_unmeasured():
    adapter = InfrastructureRecommendationAdapter(
        lambda _prompt: json.dumps(
            {"vmFamily": "general-purpose", "vmCount": 1, "confidence": "low"}
        )
    )

    result = adapter.recommend(
        requirements_result={"resource_spec": {"provider": "aws"}},
        cloud_design_result={"dependency_plan": {}, "deferred": ["price"]},
    )

    assert result["status"] == "provisional"
    assert result["method"] == "llm_prompt_only"
    assert result["measured"] is False


def test_orchestration_skips_and_restores_plantuml_jvm_check():
    from app.design.services.common import validation

    original = validation.check_plantuml_syntax
    with DesignAdapter._without_plantuml_jvm():
        assert validation.check_plantuml_syntax("invalid") == []
    assert validation.check_plantuml_syntax is original


def test_implementation_contract_maps_nested_design_artifacts():
    payload = ImplementationAdapter._design_payload(
        {"resource_spec": {"provider": "aws"}},
        {
            "artifacts": {
                "class_diagram": (
                    "A --> B\nclass User {\n  - email\n"
                    "  + authenticate(email,password)\n}"
                ),
                "sequence_diagram": "sequence puml",
                "api_spec": {"openapi": "3.0.0"},
                "erd": "erd puml",
                "deployment_diagram": "logical puml",
            }
        },
        {"deployment_diagram_puml": "cloud puml"},
        {"status": "provisional"},
    )

    assert payload["class_diagram_puml"] == (
        "' implementation relation: A --> B\nclass User {\n  - email: String\n"
        "  + authenticate(email: String, password: String)\n}"
    )
    assert payload["deployment_diagram_puml"] == "cloud puml"
    assert payload["resource_spec"]["provisionalRecommendation"]["status"] == "provisional"


def test_failed_worker_state_is_returned_for_checkpoint_and_artifacts(
    tmp_path: Path, monkeypatch
):
    job_path = tmp_path / "job.json"
    job_path.write_text("{}", encoding="utf-8")
    run_root = tmp_path / "run"
    report = run_root / "reports" / "workflow-state.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps({"status": "FAILED", "blockingReason": "JVM crashed"}),
        encoding="utf-8",
    )

    class FailedClient:
        def _call(self, _args):
            return {}

        def transmission_request(self, _run_root):
            return {"requestId": "request-1", "status": "AWAITING_APPROVAL"}

        def run_phase(self, *_args):
            raise PrototypeExecutionError("worker failed")

    adapter = ImplementationAdapter.__new__(ImplementationAdapter)
    adapter.client = FailedClient()
    monkeypatch.setattr(adapter, "_bridge_api_key", lambda: None)
    monkeypatch.setattr(adapter, "_configure_gradle_memory", lambda: None)
    monkeypatch.setattr(adapter, "_run_member_command", lambda _args: {})
    monkeypatch.setattr(
        adapter,
        "_run_member_workflow",
        lambda *_args: (_ for _ in ()).throw(PrototypeExecutionError("worker failed")),
    )

    result = adapter.resume(
        {"job_path": str(job_path), "run_root": str(run_root)}, approved=True
    )

    assert result["status"] == "failed"
    assert result["workflow"]["blockingReason"] == "JVM crashed"
    assert "worker failed" in result["execution_error"]


def test_repair_bridge_inserts_real_evidence_once(tmp_path: Path, monkeypatch):
    reports = tmp_path / "reports"
    task_dir = reports / "implementation-tasks"
    task_dir.mkdir(parents=True)
    prompt = task_dir / "entity.prompt.md"
    prompt.write_text("base prompt", encoding="utf-8")
    task = {
        "task_id": "entity",
        "task_type": "persistence-entities",
        "prompt_file": "reports/implementation-tasks/entity.prompt.md",
        "prompt_sha256": "old",
    }
    (task_dir / "entity.task.json").write_text(json.dumps(task), encoding="utf-8")
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    (reports / "repair-plan.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "ownerTaskIds": ["entity"],
                        "revalidationTaskIds": [],
                        "evidence": "real JPA evidence",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def broken_member(root):
        path = root / "reports/implementation-tasks/entity.prompt.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n\n## Orchestrated repair and revalidation directives\n"
            + "{entry['evidence']}",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "app.implementation.engine.repair_planner.apply_repair_directives",
        broken_member,
    )
    _apply_repair_directives(tmp_path)
    _apply_repair_directives(tmp_path)

    repaired = prompt.read_text(encoding="utf-8")
    assert repaired.count("real JPA evidence") == 1
    assert "{entry['evidence']}" not in repaired
    assert repaired.count("BCE foreign-key compatibility contract") == 1


def test_repair_owner_is_requested_before_failed_task(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "repair-plan.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "failedTaskId": "entities",
                        "ownerTaskIds": ["mapping"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def writer(_run_root, state):
        captured.update(state)
        return {"tasks": state["nextRunnableTasks"]}

    result = _prioritize_repair_owners(
        writer,
        tmp_path,
        {
            "nextRunnableTasks": ["entities", "mapping", "repositories"],
            "tasks": [{"taskId": "entities", "status": "FAILED"}],
        },
    )

    assert result["tasks"] == ["mapping"]
    assert captured["nextRunnableTasks"] == ["mapping"]
