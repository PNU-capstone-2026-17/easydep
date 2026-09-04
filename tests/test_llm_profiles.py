"""후보 모델별 NIM 요청값이 한 설정 경계에서만 달라지는지 확인한다."""

import pytest

from app.llm_profiles import MIN_TEMPERATURE, candidate_model_ids, profile_for


@pytest.mark.parametrize("model", candidate_model_ids())
def test_screening_profiles_respect_common_bounds(model: str) -> None:
    profile = profile_for(model)

    assert profile.temperature >= MIN_TEMPERATURE
    assert profile.completion_limit(profile.max_tokens + 1) == profile.max_tokens
    assert profile.default_max_tokens <= profile.max_tokens


def test_profiles_translate_reasoning_without_sending_unsupported_values() -> None:
    assert profile_for("openai/gpt-oss-20b").resolve_reasoning("medium") == "medium"
    assert profile_for("@cf/openai/gpt-oss-120b").preserve_reasoning_on_tool_turn is True
    assert (
        profile_for("nvidia/nemotron-3-super-120b-a12b").resolve_reasoning("medium")
        == "high"
    )
    assert (
        profile_for("nvidia/nemotron-3.5-lightning-30b-a3b").resolve_reasoning(
            "medium"
        )
        is None
    )
    assert profile_for("moonshotai/kimi-k3").resolve_reasoning("medium") == "high"
    assert (
        profile_for("deepseek-ai/deepseek-v4-pro-0813").resolve_reasoning("medium")
        == "high"
    )
    assert profile_for("poolside/laguna-xs-2.1").resolve_reasoning("medium") is None


def test_only_models_that_need_provider_specific_body_receive_one() -> None:
    assert profile_for("openai/gpt-oss-20b").extra_body() is None
    assert profile_for("moonshotai/kimi-k3").extra_body() is None
    assert profile_for("deepseek-ai/deepseek-v4-pro-0813").extra_body() is None
    assert profile_for("poolside/laguna-xs-2.1").extra_body() is None
    assert profile_for("nvidia/nemotron-3-super-120b-a12b").extra_body() == {
        "reasoning_budget": 16384
    }
    assert profile_for("nvidia/nemotron-3.5-lightning-30b-a3b").extra_body() == {
        "chat_template_kwargs": {"enable_thinking": True},
        "reasoning_budget": 16384,
    }
