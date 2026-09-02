"""동적 기능 테스트가 사용하는 공통 LLM 설정을 제공한다."""

from __future__ import annotations

from app.config import settings


def configured_api_key() -> str | None:
    return (
        settings.api_key
        or settings.nvidia_api_key
        or settings.nvidia_nim_api_key
        or settings.llm_api_key
    )


def configured_model(default: str) -> str:
    # Testing도 요구사항·설계와 같은 모델을 쓴다. OpenHands 전용 model 이름에는
    # provider 접두사가 붙을 수 있으므로 raw OpenAI client에 넘기지 않는다.
    return settings.model or default
