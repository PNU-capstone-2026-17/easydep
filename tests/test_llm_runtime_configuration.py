from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.design.services.deployment_diagram import extractor as deployment_extractor
from app.implementation.generation.orchestrator import load_job


def test_implementation_job_uses_gpt_oss_tuned_defaults(tmp_path: Path) -> None:
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps({
            "name": "probe",
            "workspaceRoot": str(tmp_path),
            "inputs": {},
            "outputRoot": "output",
            "agent": {"model": "nvidia_nim/openai/gpt-oss-120b"},
        }),
        encoding="utf-8",
    )

    spec = load_job(job)

    assert spec.agent_temperature == settings.implementation_agent_temperature
    assert spec.agent_max_output_tokens == settings.implementation_agent_max_output_tokens


def test_deployment_prompt_prefers_structured_models_over_rendered_duplicates(
    monkeypatch,
) -> None:
    captured: dict = {}

    def propose(structured_inputs, _proposal_call=None):
        captured.update(structured_inputs)
        return deployment_extractor.WorkloadGraph()

    monkeypatch.setattr(deployment_extractor, "propose_workload_graph", propose)
    deployment_extractor.extract_deployment_model(
        "scenario",
        "class puml",
        "sequence puml",
        {},
        "erd puml",
        class_model={"Classes": []},
        sequence_model={"Diagrams": []},
        erd_model={"Classes": []},
    )

    assert captured["classModel"] == {"Classes": []}
    assert captured["sequenceModel"] == {"Diagrams": []}
    assert captured["erdModel"] == {"Classes": []}
    assert "classDiagramPlantUML" not in captured
    assert "sequenceDiagramPlantUML" not in captured
    assert "erdPlantUML" not in captured
