"""NIM 연동 설정 — .env를 로드하고 NVIDIA NIM용 Chat Completions 모델을 만든다.

핵심 제약:
- NIM은 OpenAI 호환이지만 Responses API를 지원하지 않는다. 따라서 Agents SDK 기본값(Responses)
  대신 OpenAIChatCompletionsModel + AsyncOpenAI 조합을 에이전트 model=에 명시적으로 넣는다.
- SDK는 기본적으로 OpenAI 플랫폼으로 trace를 전송하며 OPENAI_API_KEY가 없으면 경고가 난다.
  set_tracing_disabled(True)로 끈다.
"""

from __future__ import annotations

import os

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from dotenv import load_dotenv
from openai import AsyncOpenAI

# .env를 프로세스 환경으로 로드하고, OpenAI 플랫폼 trace 전송을 끈다(모듈 import 시 1회).
load_dotenv()
set_tracing_disabled(True)


def _require(name: str) -> str:
    """.env에서 필수 값을 읽고, 없으면 명확한 에러를 던진다."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f".env에 {name}가 없습니다. API_KEY / BASE_URL / MODEL을 확인하세요."
        )
    return value


def build_client() -> AsyncOpenAI:
    """NIM 엔드포인트를 가리키는 OpenAI 호환 비동기 클라이언트."""
    return AsyncOpenAI(api_key=_require("API_KEY"), base_url=_require("BASE_URL"))


def build_model() -> OpenAIChatCompletionsModel:
    """.env의 MODEL을 NIM에서 구동하는 Chat Completions 모델을 반환한다."""
    return OpenAIChatCompletionsModel(model=_require("MODEL"), openai_client=build_client())
