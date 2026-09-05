from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.design.services.deployment_diagram import extractor as deployment_extractor
from app.implementation.agents import runtime as openhands_runtime
from app.implementation.generation.orchestrator import load_job
from app.implementation.planning.design_context import llm_config
from app.llm_connection import build_llm_connection, llm_subprocess_environment

_PROVIDER_CASES = (
    pytest.param(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "openai/gpt-4o-mini",
        "openrouter/openai/gpt-4o-mini",
        id="openrouter",
    ),
    pytest.param(
        "nvidia_nim",
        "https://integrate.api.nvidia.com/v1",
        "openai/gpt-oss-20b",
        "nvidia_nim/openai/gpt-oss-20b",
        id="nvidia-nim",
    ),
    pytest.param(
        "cloudflare",
        "https://api.cloudflare.com/client/v4/accounts/account/ai/v1",
        "@cf/openai/gpt-oss-120b",
        "openai/@cf/openai/gpt-oss-120b",
        id="cloudflare",
    ),
    pytest.param(
        "openai_compatible",
        "https://llm.example.invalid/v1",
        "vendor/model",
        "openai/vendor/model",
        id="openai-compatible",
    ),
)


def _provider_settings(
    provider: str,
    base_url: str,
    model: str,
    *,
    cloudflare_values: bool = True,
) -> Settings:
    """테스트용 연결 설정을 만든다. 실제 비밀값은 사용하지 않는다."""

    values: dict[str, object] = {
        "_env_file": None,
        "llm_provider": provider,
        "api_key": "test-provider-secret",  # noqa: S105 - 네트워크에 쓰지 않는 가짜 값
        "base_url": base_url,
        "model": model,
        # 개발 PC의 실제 환경변수가 단위 테스트 설정에 섞이지 않게 명시적으로 비운다.
        "cloudflare_account_id": None,
        "cloudflare_api_token": None,
        "cloudflare_ai_gateway_id": None,
    }
    if provider == "cloudflare" and cloudflare_values:
        values.update(
            {
                "cloudflare_account_id": "account",
                "cloudflare_api_token": "test-cloudflare-secret",  # noqa: S105
                "cloudflare_ai_gateway_id": "easydep",
            }
        )
    return Settings(**values)


def test_llm_connection_settings_have_no_code_fallback(monkeypatch) -> None:
    for name in ("LLM_PROVIDER", "API_KEY", "BASE_URL", "MODEL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    missing = {item["loc"][0] for item in error.value.errors()}
    assert {"llm_provider", "api_key", "base_url", "model"} <= missing


def test_unknown_provider_and_litellm_prefixed_model_fail_before_request() -> None:
    common = {
        "_env_file": None,
        "api_key": "unused-test-key",
        "base_url": "https://llm.test.invalid/v1",
    }
    with pytest.raises(ValidationError):
        Settings(**common, llm_provider="unknown", model="vendor/model")
    with pytest.raises(ValueError, match="without a LiteLLM prefix"):
        build_llm_connection(
            Settings(
                **common,
                llm_provider="openrouter",
                model="openrouter/vendor/model",
            )
        )


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


@pytest.mark.parametrize(
    ("provider", "base_url", "model", "litellm_model"),
    _PROVIDER_CASES,
)
def test_provider_table_drives_sdk_and_openhands_model_names(
    provider: str,
    base_url: str,
    model: str,
    litellm_model: str,
) -> None:
    """직접 SDK 모델 ID와 OpenHands adapter ID가 한 연결에서 파생된다."""

    config = _provider_settings(provider, base_url, model)
    connection = build_llm_connection(config)

    assert connection.provider == provider
    assert connection.model == model
    assert connection.base_url == base_url
    assert connection.litellm_model() == litellm_model


@pytest.mark.parametrize(
    ("provider", "base_url", "model", "litellm_model"),
    _PROVIDER_CASES,
)
def test_subprocess_restores_the_same_provider_endpoint_and_model(
    provider: str,
    base_url: str,
    model: str,
    litellm_model: str,
) -> None:
    """하위 프로세스도 host와 같은 연결을 복원하며 adapter도 바꾸지 않는다."""

    config = _provider_settings(provider, base_url, model)
    environment = llm_subprocess_environment(config)
    expected_api_key = (
        "test-cloudflare-secret" if provider == "cloudflare" else "test-provider-secret"
    )

    assert environment["LLM_PROVIDER"] == provider
    assert environment["BASE_URL"] == base_url
    assert environment["MODEL"] == model
    assert environment["API_KEY"] == expected_api_key

    child_values: dict[str, object] = {
        "_env_file": None,
        "llm_provider": environment["LLM_PROVIDER"],
        "api_key": environment["API_KEY"],
        "base_url": environment["BASE_URL"],
        "model": environment["MODEL"],
    }
    if provider == "cloudflare":
        child_values.update(
            {
                "cloudflare_account_id": environment.get("CLOUDFLARE_ACCOUNT_ID"),
                "cloudflare_api_token": environment.get("CLOUDFLARE_API_TOKEN"),
                "cloudflare_ai_gateway_id": environment.get("CLOUDFLARE_AI_GATEWAY_ID"),
            }
        )
    child = build_llm_connection(Settings(**child_values))
    assert child.provider == provider
    assert child.base_url == base_url
    assert child.model == model
    assert child.litellm_model() == litellm_model


def test_explicit_provider_wins_over_stale_cloudflare_environment() -> None:
    """OpenRouter 선택은 남아 있는 Cloudflare 값에 의해 바뀌지 않는다."""

    config = _provider_settings(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "openai/gpt-4o-mini",
    ).model_copy(
        update={
            "cloudflare_account_id": "stale-account",
            "cloudflare_api_token": "stale-token",
            "cloudflare_ai_gateway_id": "stale-gateway",
        }
    )

    connection = build_llm_connection(config)

    assert connection.provider == "openrouter"
    assert connection.base_url == "https://openrouter.ai/api/v1"
    assert connection.litellm_model() == "openrouter/openai/gpt-4o-mini"


def test_cloudflare_accepts_a_final_url_but_rejects_partial_url_parts() -> None:
    direct = _provider_settings(
        "cloudflare",
        "https://gateway.example.invalid/v1",
        "@cf/openai/gpt-oss-120b",
        cloudflare_values=False,
    )
    assert build_llm_connection(direct).base_url == "https://gateway.example.invalid/v1"

    partial = direct.model_copy(update={"cloudflare_account_id": "account"})
    with pytest.raises(ValueError, match="CLOUDFLARE_API_TOKEN"):
        build_llm_connection(partial)


def test_openhands_execution_plan_keeps_connection_without_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """OpenHands 실행 계획에는 연결 식별자만 남기고 API key는 저장하지 않는다."""

    connection = build_llm_connection(
        _provider_settings(
            "openrouter",
            "https://openrouter.ai/api/v1",
            "openai/gpt-4o-mini",
        )
    )
    monkeypatch.setattr(
        openhands_runtime,
        "openhands_connection",
        lambda: connection,
    )
    monkeypatch.setattr(
        openhands_runtime,
        "openhands_compatibility",
        lambda *_args: {
            "pythonCompatible": True,
            "sdkInstalled": True,
            "toolsInstalled": True,
            "apiKeyConfigured": True,
        },
    )
    (tmp_path / "reports").mkdir()

    plan = openhands_runtime.write_execution_plan(
        tmp_path,
        [{"task_id": "implement-use-cases"}],
        "auto",
    )

    assert plan["llm"] == {
        "provider": "openrouter",
        "model": "openai/gpt-4o-mini",
        "baseUrl": "https://openrouter.ai/api/v1",
    }
    serialized = (tmp_path / "reports" / "agent-execution-plan.json").read_text(
        encoding="utf-8"
    )
    assert "test-provider-secret" not in serialized


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
