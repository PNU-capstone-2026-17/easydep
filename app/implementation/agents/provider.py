from __future__ import annotations

import importlib.util
import sys

from app.config import settings
from app.llm_connection import LlmConnection, build_llm_connection

MAX_PROVIDER_RETRIES = 3
MAX_TOOL_PROTOCOL_RETRIES = 2


def openhands_connection() -> LlmConnection:
    """OpenHands도 다른 단계와 같은 중앙 연결 정보를 한 번만 읽는다.

    이 모듈은 OpenHands SDK가 요구하는 실행 보조 기능만 가진다. provider별 URL,
    모델 접두사, header 규칙은 ``app.llm_connection`` 밖으로 복사하지 않는다.
    """

    return build_llm_connection()


def configured_max_output_tokens(default: int) -> int:
    raw = settings.openhands_max_output_tokens
    return int(raw) if raw else default


def transient_provider_error(error: Exception) -> bool:
    """제공자와 무관하게 재시도할 수 있는 전송 실패를 구분한다."""
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


def retryable_tool_protocol_error(error: Exception) -> bool:
    """Recognize model-generated tool-call protocol failures, not arbitrary 400s."""

    text = f"{error.__class__.__name__}: {error}".lower()
    mentions_tool_call = any(
        marker in text for marker in ("tool call", "tool_call", "tool use")
    )
    rejects_protocol = any(
        marker in text
        for marker in (
            "validation failed",
            "invalid tool",
            "unknown tool",
            "unrecognized tool",
            "tool not found",
            "not a valid tool",
            "no tool named",
            "not in request.tools",
        )
    )
    return mentions_tool_call and rejects_protocol


def provider_retry_delay(retry_number: int) -> float:
    base = settings.openhands_provider_retry_base_seconds
    cap = settings.openhands_provider_retry_max_seconds
    return min(cap, base * (2 ** max(0, retry_number - 1)))


def openhands_compatibility(
    connection: LlmConnection | None = None,
) -> dict[str, object]:
    """실행 전 SDK와 공통 LLM 연결의 최소 준비 상태를 보고한다."""

    effective_connection = connection or openhands_connection()
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "pythonCompatible": sys.version_info >= (3, 12),
        "sdkInstalled": module_available("openhands.sdk"),
        "toolsInstalled": module_available("openhands.tools"),
        "apiKeyConfigured": bool(effective_connection.api_key),
    }


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False
