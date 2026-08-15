"""LLM settings used only when dynamic functional tests are generated."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class _TestingProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key: str | None = None
    nvidia_api_key: str | None = None
    nvidia_nim_api_key: str | None = None
    llm_api_key: str | None = None
    base_url: str | None = None
    openhands_model: str | None = None
    llm_model: str | None = None


settings = _TestingProviderSettings()


def configured_api_key() -> str | None:
    return (
        settings.api_key
        or settings.nvidia_api_key
        or settings.nvidia_nim_api_key
        or settings.llm_api_key
    )


def configured_model(default: str) -> str:
    return settings.openhands_model or settings.llm_model or default
