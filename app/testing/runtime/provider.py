"""동적 기능 테스트가 사용하는 공통 LLM 설정을 제공한다."""

from __future__ import annotations

from app.config import settings
from app.llm_connection import build_llm_connection


def configured_api_key() -> str:
    return build_llm_connection(settings).api_key


def configured_model() -> str:
    """Testing도 루트 `.env`의 공통 MODEL만 사용한다."""

    return build_llm_connection(settings).model


def configured_base_url() -> str:
    """동적 테스트 계획도 다른 개발 단계와 같은 endpoint를 사용한다."""

    return build_llm_connection(settings).base_url


def configured_headers() -> dict[str, str]:
    return build_llm_connection(settings).default_headers()
