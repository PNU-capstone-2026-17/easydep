"""LLM 접근 + 구조화 출력 헬퍼.

NIM(OpenAI 호환) 엔드포인트를 langchain-openai의 ChatOpenAI로 감싼다.

구조화 출력(structured output) 처리:
  - 1차: `ChatOpenAI.with_structured_output(schema, method="json_schema")`.
    이는 OpenAI 네이티브 Structured Outputs(`response_format={"type":"json_schema",
    strict:true}`)를 호출하는 경로로, raw SDK의 `client.chat.completions.parse(...)`
    와 동일한 메커니즘을 langchain이 감싼 것이다.
  - 폴백: NIM에 서빙된 gpt-oss-120b는 간헐적으로 빈 `parsed`(content='')를 반환한다.
    이 경우 스키마(JSON Schema)를 프롬프트로 주고 원문 JSON을 직접 파싱한다.

`include_raw=True`로 부르는 이유는 둘이다. 하나는 원본 메시지에 실린 토큰 사용량과
백엔드 지문을 읽기 위해서고(그게 없으면 이 파이프라인의 비용도 재현성도 잴 방법이
없다), 다른 하나는 파싱 실패가 예외가 아니라 `parsing_error` 값으로 돌아와 폴백
판단이 명시적이 되기 때문이다. 호출 1건은 telemetry가 지연·토큰·지문·폴백 여부와
함께 기록한다.

## 재현성

`temperature=0` + `seed` 고정으로 같은 입력에 같은 표본을 **요청**한다. 이건 보장이
아니다 — OpenAI 호환 API의 seed는 best-effort이고, 서버가 무시할 수도 있으며, 백엔드
구성이 바뀌면 같은 seed라도 결과가 달라진다. 그래서 응답의 `system_fingerprint`를
telemetry에 모은다. **"재현된다"의 근거는 우리가 보낸 seed가 아니라 서버가 돌려준
지문이다.** 한 실행 안에서 지문이 둘 이상이면 그 실행은 이미 섞여 있는 것이고,
실행끼리 비교할 때 지문이 다르면 출력 차이를 코드 변경 탓으로 돌릴 수 없다.
"""
from __future__ import annotations

import json
from typing import TypeVar, cast

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from app.requirements.common import telemetry
from app.requirements.config import settings

T = TypeVar("T", bound=BaseModel)

_log = telemetry.get_logger("llm")

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
            # 같은 입력에 같은 표본을 **요청**한다. 보장은 아니다 — 서버가 seed를
            # 무시할 수도 있고, 백엔드 구성이 바뀌면(system_fingerprint) 같은 seed라도
            # 결과가 달라진다. 그래서 지문을 telemetry에 남겨 사후에 확인할 수 있게 한다.
            # None이면 파라미터 자체를 안 보낸다.
            seed=settings.seed,
            # 콜드 스타트(~5분)는 견디되, 진짜 멈춘 호출은 무한 대기하지 않도록 상한을 둔다.
            timeout=600,
            max_retries=2,
        )
    return _llm


def reset_llm() -> None:
    """캐시된 클라이언트를 버린다.

    `build_llm()`이 프로세스당 1회만 만들기 때문에, settings의 모델·온도·seed를
    바꾼 뒤에는 이걸 불러야 반영된다. 안 부르면 설정은 바뀌었는데 호출은 옛 값으로
    나가고, 그건 로그만 봐서는 알 수 없는 종류의 어긋남이다.
    """
    global _llm
    _llm = None


def warmup_llm() -> float:
    """NIM 콜드 스타트를 미리 1회 지불한다(배치 시작 전 호출용). 소요 초를 반환."""
    import time

    from langchain_core.messages import HumanMessage

    t = time.time()
    try:
        build_llm().invoke([HumanMessage(content="ping")])
    except Exception as exc:  # noqa: BLE001 - 워밍업 실패는 치명적이지 않음
        # 워밍업이 실패해도 진행은 하지만, 그러면 첫 실제 호출이 콜드 스타트를 물게
        # 되므로 뒤따르는 지연은 모델이 느린 게 아니라 이것 때문이다.
        telemetry.record_degradation("llm.warmup", f"{type(exc).__name__}: {exc}")
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


def _native_structured(
    llm: ChatOpenAI, schema: type[T], messages: list, call: telemetry.LlmCall
) -> T | None:
    """네이티브 구조화 출력 경로. 못 얻으면 폴백 사유를 적고 None을 돌려준다."""
    try:
        structured = llm.with_structured_output(
            schema, method=_STRUCTURED_METHOD, include_raw=True
        )
        result = structured.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - 폴백으로 흡수, 사유는 기록한다
        call.mark_fallback(f"{type(exc).__name__}: {exc}")
        return None

    # include_raw=True 면 {"raw", "parsed", "parsing_error"} 를 돌려준다. 구버전
    # langchain이 파싱된 모델을 그대로 주면 그대로 쓴다.
    if not isinstance(result, dict):
        return cast(T, result)

    raw = result.get("raw")
    call.observe_usage(getattr(raw, "usage_metadata", None))
    call.observe_metadata(getattr(raw, "response_metadata", None))
    parsed = result.get("parsed")
    if parsed is not None:
        return cast(T, parsed)

    error = result.get("parsing_error")
    call.mark_fallback(f"parsed 없음: {error!r}" if error else "parsed 없음(빈 응답)")
    return None


def _json_mode(
    llm: ChatOpenAI, schema: type[T], messages: list, call: telemetry.LlmCall
) -> T:
    """스키마를 프롬프트로 주고 원문 JSON을 직접 파싱하는 폴백 경로."""
    schema_json = json.dumps(schema.model_json_schema())
    instr = SystemMessage(
        content=(
            "Respond with ONLY a single JSON object that conforms to this "
            f"JSON Schema. No prose, no markdown fences.\n{schema_json}"
        )
    )
    raw = llm.invoke(list(messages) + [instr])
    call.observe_usage(getattr(raw, "usage_metadata", None))
    call.observe_metadata(getattr(raw, "response_metadata", None))
    return schema.model_validate_json(_extract_json(_message_text(raw.content)))


def invoke_structured(schema: type[T], messages: list) -> T:
    """구조화 출력 호출. 네이티브 structured output 실패 시 JSON 모드로 폴백한다."""
    llm = build_llm()
    with telemetry.record_llm_call(f"structured:{schema.__name__}") as call:
        parsed = _native_structured(llm, schema, messages, call)
        if parsed is not None:
            return parsed
        return _json_mode(llm, schema, messages, call)
