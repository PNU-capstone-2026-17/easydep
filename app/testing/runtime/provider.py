"""동적 기능 테스트가 사용하는 공통 LLM 설정을 제공한다."""

from __future__ import annotations

from app.config import settings


def configured_api_key() -> str:
    return settings.api_key


def configured_model() -> str:
    """Testing도 루트 `.env`의 공통 MODEL만 사용한다."""

    return settings.model
