from __future__ import annotations

import importlib.util
import sys

from app.config import settings
from app.llm_connection import build_llm_connection

MAX_PROVIDER_RETRIES = 3


def configured_api_key() -> str:
    return build_llm_connection().api_key


def configured_model() -> str:
    """루트 `.env`의 MODEL을 OpenHands/LiteLLM용 NIM 이름으로 바꾼다."""

    connection = build_llm_connection()
    model = connection.model
    if connection.provider == "cloudflare-ai-gateway":
        # OpenHands의 LiteLLM에는 adapter 이름이 앞에 필요하다. 실제 Cloudflare
        # 요청에는 뒤의 @cf/... 모델 ID가 전달된다.
        return model if model.startswith("openai/") else f"openai/{model}"
    return model if model.startswith("nvidia_nim/") else f"nvidia_nim/{model}"


def configured_base_url() -> str:
    return build_llm_connection().base_url


def configured_headers() -> dict[str, str]:
    return build_llm_connection().default_headers()


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
