"""LLM 접근 + 구조화 출력 헬퍼.

NIM(OpenAI 호환) 엔드포인트를 langchain-openai의 ChatOpenAI로 감싼다.

구조화 출력(structured output) 처리:
  - 1차: `ChatOpenAI.with_structured_output(schema, method="json_schema")`.
    이는 OpenAI 네이티브 Structured Outputs(`response_format={"type":"json_schema",
    strict:true}`)를 호출하는 경로로, raw SDK의 `client.chat.completions.parse(...)`
    와 동일한 메커니즘을 langchain이 감싼 것이다. 응답의 `.parsed`를 꺼내 Pydantic
    인스턴스로 돌려준다.
  - 폴백: NIM에 서빙된 gpt-oss-120b는 간헐적으로 빈 `parsed`(content='')를 반환한다.
    이 경우 스키마(JSON Schema)를 프롬프트로 주고 원문 JSON을 직접 파싱한다.
"""
from __future__ import annotations

import json
from typing import TypeVar, cast

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from app.requirements.config import settings

T = TypeVar("T", bound=BaseModel)

# with_structured_output에 쓸 method. "json_schema"=네이티브 parse 경로(권장),
# "function_calling"=tool 호출 경로. NIM 모델 특성에 따라 교체 가능.
_STRUCTURED_METHOD = "json_schema"


# 프로세스 전역 클라이언트 캐시. NIM 첫 호출은 모델 콜드 스타트로 수 분 걸리므로(이후 호출은
# 2~3초), 클라이언트를 재사용해 연결 오버헤드를 줄이고 콜드 스타트를 프로세스당 1회로 한정한다.
_llm: ChatOpenAI | None = None


def build_llm() -> ChatOpenAI:
    """NIM(OpenAI 호환) 채팅 모델을 반환한다(프로세스당 1회 생성, 이후 재사용)."""
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=settings.model,
            base_url=settings.base_url,
            api_key=SecretStr(settings.api_key),
            temperature=settings.temperature,
            # 콜드 스타트(~5분)는 견디되, 진짜 멈춘 호출은 무한 대기하지 않도록 상한을 둔다.
            timeout=600,
            max_retries=2,
        )
    return _llm


def warmup_llm() -> float:
    """NIM 콜드 스타트를 미리 1회 지불한다(배치 시작 전 호출용). 소요 초를 반환."""
    import time
    from langchain_core.messages import HumanMessage

    t = time.time()
    try:
        build_llm().invoke([HumanMessage(content="ping")])
    except Exception as exc:  # noqa: BLE001 - 워밍업 실패는 치명적이지 않음
        print(f"[agent] LLM 워밍업 실패(무시): {exc}")
    return time.time() - t


def _message_text(content: str | list) -> str:
    """AIMessage.content(문자열 또는 파트 리스트)를 평문 문자열로 정규화한다."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            parts.append(str(part.get("text", "")))
    return "".join(parts)


def _extract_json(text: str) -> str:
    """모델 출력에서 첫 '{' ~ 마지막 '}' 구간을 JSON으로 잘라낸다(코드펜스/프롤로그 방어)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"응답에서 JSON을 찾지 못함: {text[:200]!r}")
    return text[start : end + 1]


def invoke_structured(schema: type[T], messages: list) -> T:
    """구조화 출력 호출. 네이티브 structured output 실패 시 JSON 모드로 폴백한다."""
    llm = build_llm()
    try:
        structured = llm.with_structured_output(schema, method=_STRUCTURED_METHOD)
        return cast(T, structured.invoke(messages))
    except Exception as exc:  # noqa: BLE001 - 폴백으로 흡수
        print(f"[agent] structured output 폴백(JSON 모드) 사용: {exc}")
        schema_json = json.dumps(schema.model_json_schema())
        instr = SystemMessage(
            content=(
                "Respond with ONLY a single JSON object that conforms to this "
                f"JSON Schema. No prose, no markdown fences.\n{schema_json}"
            )
        )
        raw = llm.invoke(list(messages) + [instr])
        return schema.model_validate_json(_extract_json(_message_text(raw.content)))
