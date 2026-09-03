from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.design.services.deployment_diagram import extractor as deployment_extractor
from app.implementation.generation.orchestrator import load_job
from app.implementation.planning.design_context import llm_config


def test_llm_connection_settings_have_no_code_fallback(monkeypatch) -> None:
    for name in ("API_KEY", "BASE_URL", "MODEL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    missing = {item["loc"][0] for item in error.value.errors()}
    assert {"api_key", "base_url", "model"} <= missing


def test_implementation_job_uses_only_root_environment_llm_settings(tmp_path: Path) -> None:
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps({
            "name": "probe",
            "workspaceRoot": str(tmp_path),
            "inputs": {},
            "outputRoot": "output",
            "agent": {
                "model": "stale/job-model",
                "baseUrl": "https://stale-job.invalid/v1",
            },
        }),
        encoding="utf-8",
    )

    spec = load_job(job)

    assert spec.agent_temperature == settings.implementation_agent_temperature
    assert spec.agent_max_output_tokens == settings.implementation_agent_max_output_tokens
    assert llm_config(spec)["model"] == settings.model
    assert llm_config(spec)["baseUrl"] == settings.base_url


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
