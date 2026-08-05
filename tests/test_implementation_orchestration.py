from __future__ import annotations

import json
from pathlib import Path

from app.core.orchestration.adapters.design import DesignAdapter
from app.core.orchestration.adapters.implementation import ImplementationAdapter
from app.core.orchestration.adapters.infrastructure import (
    InfrastructureRecommendationAdapter,
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

    result = adapter.resume(
        {"job_path": str(job_path), "run_root": str(run_root)}, approved=True
    )

    assert result["status"] == "failed"
    assert result["workflow"]["blockingReason"] == "JVM crashed"
    assert "worker failed" in result["execution_error"]
