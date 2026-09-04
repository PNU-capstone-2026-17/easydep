from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.design.services.deployment_diagram import extractor as deployment_extractor
from app.implementation.generation.orchestrator import load_job
from app.implementation.planning.design_context import llm_config
from app.llm_connection import build_llm_connection, llm_subprocess_environment


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
    connection = build_llm_connection()
    assert llm_config(spec)["model"] == connection.model
    assert llm_config(spec)["baseUrl"] == connection.base_url


def test_cloudflare_connection_is_assembled_from_small_env_values() -> None:
    config = Settings(
        _env_file=None,
        api_key="fallback-key",
        base_url="https://fallback.invalid/v1",
        model="@cf/openai/gpt-oss-120b",
        cloudflare_account_id="account",
        cloudflare_api_token="cloudflare-token",  # noqa: S106 - 가짜 설정값
        cloudflare_ai_gateway_id="easydep",
    )

    connection = build_llm_connection(config)

    assert connection.provider == "cloudflare-ai-gateway"
    assert connection.api_key == "cloudflare-token"
    assert connection.base_url == (
        "https://api.cloudflare.com/client/v4/accounts/account/ai/v1"
    )
    assert connection.model == "@cf/openai/gpt-oss-120b"
    assert connection.default_headers() == {"cf-aig-gateway-id": "easydep"}
    assert connection.litellm_model() == "openai/@cf/openai/gpt-oss-120b"
    assert connection.display_name() == "Cloudflare AI Gateway"

    # 구현 runner는 중앙 함수가 만든 환경변수 묶음을 빠짐없이 받는다. 하위 프로세스도
    # 같은 provider, endpoint와 header를 복원해야 OpenHands의 후속 tool turn이 NVIDIA
    # 형식으로 잘못 전송되지 않는다.
    environment = llm_subprocess_environment(config)
    child = build_llm_connection(Settings(
        _env_file=None,
        api_key=environment["API_KEY"],
        base_url=environment["BASE_URL"],
        model=environment["MODEL"],
        cloudflare_account_id=environment["CLOUDFLARE_ACCOUNT_ID"],
        cloudflare_api_token=environment["CLOUDFLARE_API_TOKEN"],
        cloudflare_ai_gateway_id=environment["CLOUDFLARE_AI_GATEWAY_ID"],
    ))
    assert child == connection
    assert child.litellm_model() == "openai/@cf/openai/gpt-oss-120b"


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
