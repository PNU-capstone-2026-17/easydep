from __future__ import annotations

import importlib.util
import os
import sys

from app.config import settings


MAX_PROVIDER_RETRIES = 3


def configured_api_key() -> str | None:
    return (
        settings.api_key
        or settings.nvidia_api_key
        or settings.nvidia_nim_api_key
        or settings.llm_api_key
        or windows_user_environment("NVIDIA_API_KEY")
        or windows_user_environment("NVIDIA_NIM_API_KEY")
    )


def configured_model(default: str) -> str:
    """공통 MODEL을 OpenHands/LiteLLM이 이해하는 NIM 이름으로 바꾼다."""

    model = settings.model or default
    return model if model.startswith("nvidia_nim/") else f"nvidia_nim/{model}"


def configured_max_output_tokens(default: int) -> int:
    raw = settings.openhands_max_output_tokens
    return int(raw) if raw else default


def transient_provider_error(error: Exception) -> bool:
    """Recognize retryable NIM/OpenAI-compatible transport failures."""
    text = f"{error.__class__.__name__}: {error}".lower()
    return any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "too many requests",
            "timeout",
            "timed out",
            "connection error",
            "temporarily unavailable",
            "service unavailable",
            "overloaded",
            "bad gateway",
            "gateway timeout",
            "502",
            "503",
            "504",
        )
    )


def provider_retry_delay(retry_number: int) -> float:
    base = settings.openhands_provider_retry_base_seconds
    cap = settings.openhands_provider_retry_max_seconds
    return min(cap, base * (2 ** max(0, retry_number - 1)))


def windows_user_environment(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value if isinstance(value, str) and value else None
    except (FileNotFoundError, OSError):
        return None


def openhands_compatibility() -> dict[str, object]:
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "pythonCompatible": sys.version_info >= (3, 12),
        "sdkInstalled": module_available("openhands.sdk"),
        "toolsInstalled": module_available("openhands.tools"),
        "apiKeyConfigured": bool(configured_api_key()),
    }


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False
