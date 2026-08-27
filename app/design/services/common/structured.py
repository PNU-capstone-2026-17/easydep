"""구조화 출력 LLM 호출 — 모든 설계 산출물이 공유하는 단 하나의 LLM 배관.

**왜 구조화 출력만 쓰나.** 예전에는 산출물마다 방식이 갈렸다. 클래스·ERD는 LLM에게
Pydantic 스키마를 강제해 구조화된 모델을 받고 그것을 결정론적으로 렌더했지만, 시퀀스·
API·배포는 LLM이 PlantUML/JSON 텍스트를 직접 쓰고 그것을 파싱했다. 후자는 문법 오류가
날 수 있으니 validate→repair 루프가 필요했고, 피드백이 렌더된 텍스트를 편집하므로
"모델과 그림이 어긋나는" 상태가 원천적으로 가능했다.

지금은 다섯 산출물 모두 이 함수 하나를 거친다. LLM은 **언제나 스키마에 맞는 JSON만**
내놓고, 그림/명세는 그 모델에서 결정론적으로 렌더된다. 그래서 수리 루프가 사라지고,
피드백은 항상 모델을 편집하며, 모델과 산출물이 어긋날 수 없다.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.llm_stall_probe import start_stall_probe
from app.metrics import langsmith as langsmith_metrics


class StructuredLlmError(RuntimeError):
    """어느 구조화 산출물 호출이 실패했는지 보존하는 경계 오류."""


class _SchemaValidationFailure(Exception):
    """Carry a local validation error and its parsed JSON object to the one repair."""

    def __init__(self, error: ValidationError, parsed_input: Any | None) -> None:
        super().__init__(str(error))
        self.error = error
        self.parsed_input = parsed_input


def _failure_category(error: BaseException) -> str:
    """Classify transport failures without mistaking sandbox/network causes for 429s."""

    text = str(error).casefold()
    name = type(error).__name__.casefold()
    if "429" in text or "ratelimit" in name or "rate limit" in text:
        return "rate_limit"
    if "timeout" in name or "timed out" in text:
        return "timeout"
    if "connection" in name or "connection" in text or "dns" in text:
        return "connection"
    if isinstance(error, (ValidationError, _SchemaValidationFailure)):
        return "schema_validation"
    return "provider_or_runtime"


_TIMING_EVENTS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "design_llm_timing_events", default=None
)


@contextmanager
def capture_llm_timings():
    events: list[dict[str, Any]] = []
    token = _TIMING_EVENTS.set(events)
    try:
        yield events
    finally:
        _TIMING_EVENTS.reset(token)


def run_with_wall_timeout(
    callable_obj,
    *,
    operation: str = "structured-output",
    observation: dict[str, Any] | None = None,
):
    """Trace one design-model call while retaining the existing timeout contract."""

    recorded_observation = observation if observation is not None else {}
    with langsmith_metrics.trace_scope(
        f"easydep.design.llm.{operation}",
        run_type="llm",
        metadata={
            "agent": "design",
            "operation": operation,
            "ls_provider": "nvidia-nim",
            "ls_model_name": settings.model,
        },
    ) as trace:
        try:
            return _run_with_wall_timeout(
                callable_obj,
                operation=operation,
                observation=recorded_observation,
            )
        finally:
            if "inputTokens" in recorded_observation or "outputTokens" in recorded_observation:
                trace.set_usage(
                    input_tokens=int(recorded_observation.get("inputTokens") or 0),
                    output_tokens=int(recorded_observation.get("outputTokens") or 0),
                )


def _run_with_wall_timeout(
    callable_obj,
    *,
    operation: str = "structured-output",
    observation: dict[str, Any] | None = None,
):
    """벽시계 타임아웃. 클라이언트 타임아웃이 걸리지 않는 지연(연결 후 무응답 등)을 막는다."""
    recorded_observation = observation if observation is not None else {}
    timeout_seconds = settings.llm_wall_timeout_seconds
    started_at = datetime.now(UTC)
    started = perf_counter()
    status = "failed"
    error_type: str | None = None
    result_queue: queue.Queue = queue.Queue(maxsize=1)
    stall_probe = start_stall_probe(operation)
    if settings.easydep_experiment_session:
        print(json.dumps({
            "event": "llmOperationStarted",
            "operation": operation,
            "startedAt": started_at.isoformat(),
            "wallTimeoutSeconds": timeout_seconds,
        }, ensure_ascii=False), flush=True)

    def target():
        try:
            result_queue.put((True, callable_obj()))
        except Exception as error:  # noqa: BLE001 - 호출 스레드로 그대로 올린다
            result_queue.put((False, error))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    try:
        try:
            ok, result = result_queue.get(timeout=timeout_seconds)
        except queue.Empty as error:
            error_type = "WallTimeout"
            raise StructuredLlmError(
                f"{operation}: LLM request timed out after {timeout_seconds:g} seconds."
            ) from error
        if ok:
            status = "completed"
            return result
        error_type = type(result).__name__
        recorded_observation["failureCategory"] = _failure_category(result)
        raise StructuredLlmError(f"{operation}: {result}") from result
    finally:
        stall_probe.set()
        elapsed_seconds = round(perf_counter() - started, 6)
        if settings.easydep_experiment_session:
            print(json.dumps({
                "event": "llmOperationFinished",
                "operation": operation,
                "status": status,
                "errorType": error_type,
                "elapsedSeconds": elapsed_seconds,
            } | dict(observation or {}), ensure_ascii=False), flush=True)
        events = _TIMING_EVENTS.get()
        if events is not None:
            finished_at = datetime.now(UTC)
            events.append(
                {
                    "operation": operation,
                    "status": status,
                    "errorType": error_type,
                    "startedAt": started_at.isoformat(),
                    "finishedAt": finished_at.isoformat(),
                    "elapsedSeconds": elapsed_seconds,
                    "wallTimeoutSeconds": timeout_seconds,
                    "clientTimeoutSeconds": settings.llm_timeout_seconds,
                    "observationScope": "requestCompletionOnly",
                    "ttftSeconds": None,
                }
                | dict(observation or {})
            )


def _response_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        },
    }


def _reasoning_effort(reasoning_effort: str | None) -> str | None:
    """Validate an explicit per-call policy and gate it to GPT-OSS providers.

    NVIDIA NIM exposes ``reasoning_effort`` for GPT-OSS through its
    OpenAI-compatible endpoint.  Other configured models retain the same
    request shape they used before this policy was introduced.
    """
    configured = (reasoning_effort or settings.design_reasoning_effort).strip().lower()
    if configured not in {"low", "medium", "high"}:
        raise ValueError(f"unsupported reasoning effort: {configured}")
    if "gpt-oss" not in settings.model.lower():
        return None
    return configured


def _stream_structured(
    client,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    observation: dict[str, Any],
    *,
    reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
) -> BaseModel:
    """구조화 응답을 스트리밍으로 받아 진행 시간과 최종 스키마를 함께 검증한다."""
    started = perf_counter()
    previous_event: float | None = None
    first_event: float | None = None
    first_output: float | None = None
    first_content: float | None = None
    max_inter_event = 0.0
    event_count = 0
    content_parts: list[str] = []
    content_characters = 0
    reasoning_characters = 0
    finish_reasons: list[str] = []
    request: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature,
        "seed": settings.seed,
        "stream": True,
        # NVIDIA NIM documents this OpenAI-compatible option.  The final stream
        # chunk carries provider-reported usage, which is needed for exact
        # LangSmith token/cost totals rather than a local estimate.
        "stream_options": {"include_usage": True},
        "response_format": _response_format(schema),
    }
    completion_limit = (
        max_completion_tokens
        if max_completion_tokens is not None
        else settings.llm_max_completion_tokens
    )
    if completion_limit:
        request["max_completion_tokens"] = int(completion_limit)
    provider_reasoning_effort = _reasoning_effort(reasoning_effort)
    if provider_reasoning_effort:
        request["reasoning_effort"] = provider_reasoning_effort
    observation.update(
        schema=schema.__name__,
        provider="nvidia-nim",
        model=settings.model,
        reasoningEffort=provider_reasoning_effort,
        maxCompletionTokens=int(completion_limit) if completion_limit else None,
    )
    stream = client.chat.completions.create(
        **request,
    )
    observation["transport"] = "structuredStream"
    observation["responseEstablishedSeconds"] = round(perf_counter() - started, 6)
    for chunk in stream:
        _observe_stream_usage(observation, getattr(chunk, "usage", None))
        now = perf_counter()
        event_at = datetime.now(UTC).isoformat()
        event_count += 1
        if first_event is None:
            first_event = now - started
            observation["firstEventAt"] = event_at
        if previous_event is not None:
            max_inter_event = max(max_inter_event, now - previous_event)
        previous_event = now
        for choice in chunk.choices:
            content = choice.delta.content or ""
            reasoning = str(getattr(choice.delta, "reasoning_content", "") or "")
            if (content or reasoning) and first_output is None:
                first_output = now - started
            if content and first_content is None:
                first_content = now - started
            if content:
                content_parts.append(content)
                content_characters += len(content)
            reasoning_characters += len(reasoning)
            if choice.finish_reason:
                finish_reasons.append(str(choice.finish_reason))
        # Keep only aggregate progress.  This dict is shared with the wall-timeout
        # observer, so a timeout retains evidence from the last received chunk
        # without persisting prompts, reasoning, or response content.
        observation.update(
            firstEventSeconds=(
                round(first_event, 6) if first_event is not None else None
            ),
            lastEventAt=event_at,
            lastEventSeconds=round(now - started, 6),
            maxInterEventSeconds=round(max_inter_event, 6),
            eventCount=event_count,
            contentCharacters=content_characters,
            reasoningCharacters=reasoning_characters,
            finishReasonObserved=bool(finish_reasons),
            finishReasons=list(finish_reasons),
        )
    content_text = "".join(content_parts)
    observation.update(
        firstEventSeconds=round(first_event, 6) if first_event is not None else None,
        ttftSeconds=round(first_output, 6) if first_output is not None else None,
        firstContentSeconds=(
            round(first_content, 6) if first_content is not None else None
        ),
        maxInterEventSeconds=round(max_inter_event, 6),
        eventCount=event_count,
        contentCharacters=len(content_text),
        reasoningCharacters=reasoning_characters,
        finishReasonObserved=bool(finish_reasons),
        finishReasons=finish_reasons,
        responseSha256=hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
    )
    try:
        parsed_input = json.loads(content_text)
    except json.JSONDecodeError:
        parsed_input = None
    try:
        return schema.model_validate_json(content_text)
    except ValidationError as error:
        _record_failure_content(observation, content_text)
        raise _SchemaValidationFailure(error, parsed_input) from error
    except Exception:
        _record_failure_content(observation, content_text)
        raise


def _record_failure_content(observation: dict[str, Any], content_text: str) -> None:
    """Keep opt-in diagnostics identical for JSON and model validation failures."""
    # 실패 원문 전체와 reasoning은 보존하지 않는다. 실험에서 명시적으로 요청한
    # 제한 길이의 양 끝 표본과 전체 content 지문만 남겨 토큰 절단, 반복 출력,
    # 단순 문법 오류를 구분한다. 특정 schema나 사례에 의존하지 않는 공통 경계다.
    sample_limit = settings.llm_failure_response_sample_chars
    if settings.easydep_experiment_session and sample_limit > 0:
        bounded = min(sample_limit, 4096)
        observation.update(
            failureContentSha256=hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
            failureContentPrefix=content_text[:bounded],
            failureContentSuffix=content_text[-bounded:],
            failureContentSampleCharacters=bounded,
            failureContentSampleTruncated=len(content_text) > bounded * 2,
        )


def _observe_stream_usage(observation: dict[str, Any], usage: Any) -> None:
    """Copy provider-reported usage from the terminal SSE chunk if available."""

    if usage is None:
        return
    if isinstance(usage, dict):
        read = usage.get
    else:
        def read(name: str, default: Any = None) -> Any:
            return getattr(usage, name, default)
    prompt_tokens = read("prompt_tokens", read("input_tokens", None))
    completion_tokens = read("completion_tokens", read("output_tokens", None))
    total_tokens = read("total_tokens", None)
    if prompt_tokens is not None:
        observation["inputTokens"] = int(prompt_tokens)
    if completion_tokens is not None:
        observation["outputTokens"] = int(completion_tokens)
    if total_tokens is not None:
        observation["totalTokens"] = int(total_tokens)


def parse_structured(
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    *,
    reasoning_effort: str | None = None,
    repair_reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
    operation: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """LLM에게 schema를 강제해 구조화 결과를 받고 dict로 돌려준다.

    temperature/seed를 고정하는 것은 같은 입력이 같은 모델을 내도록 하기 위해서다 —
    산출물이 재현되지 않으면 피드백이 무엇을 고쳤는지 알 수 없다.
    """
    parsed = _parse_with_schema_repair(
        _structured_client(
            settings.base_url,
            settings.api_key,
            float(settings.llm_timeout_seconds),
            int(settings.llm_max_retries),
        ),
        messages,
        schema,
        reasoning_effort=reasoning_effort,
        repair_reasoning_effort=repair_reasoning_effort,
        max_completion_tokens=max_completion_tokens,
        operation=operation,
        metadata=metadata,
    )
    return parsed.model_dump()


@lru_cache(maxsize=4)
def _structured_client(
    base_url: str | None,
    api_key: str | None,
    timeout_seconds: float,
    max_retries: int,
):
    """Reuse one OpenAI-compatible client per configured provider tuple."""

    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout_seconds,
        max_retries=max_retries,
    )


def _parse_with_schema_repair(
    client,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    *,
    reasoning_effort: str | None = None,
    repair_reasoning_effort: str | None = None,
    max_completion_tokens: int | None = None,
    operation: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BaseModel:
    """Retry local schema validation once, retaining the declared effort policy.

    Repairs inherit the original call's effort.  A caller may explicitly opt
    into a different repair level (for example ``high``); no repair is silently
    escalated.
    """
    operation_name = operation or schema.__name__
    observation: dict[str, Any] = {
        "schemaRepairAttempt": 0,
        "taskKind": operation_name,
        **dict(metadata or {}),
    }
    try:
        return run_with_wall_timeout(
            lambda: _stream_structured(
                client,
                messages,
                schema,
                observation,
                reasoning_effort=reasoning_effort,
                **(
                    {"max_completion_tokens": max_completion_tokens}
                    if max_completion_tokens is not None
                    else {}
                ),
            ),
            operation=operation_name,
            observation=observation,
        )
    except StructuredLlmError as error:
        cause = error.__cause__
        parsed_input: Any | None = None
        if isinstance(cause, _SchemaValidationFailure):
            validation_error = cause.error
            parsed_input = cause.parsed_input
        elif isinstance(cause, ValidationError):
            validation_error = cause
        else:
            raise
        validation_errors = validation_error.errors(
            include_url=False,
            include_input=False,
            # Pydantic keeps the original exception object in ``ctx.error``
            # for value errors.  Repair prompts are JSON, so retaining that
            # object masks the validation failure with a serialization error.
            include_context=False,
        )

    repair_payload = _schema_repair_payload(validation_errors, parsed_input)
    repair_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                "The previous response failed local schema validation. Regenerate the "
                "entire object; do not return a patch and do not omit required values.\n\n"
                "[Schema repair context]\n"
                + json.dumps(repair_payload, ensure_ascii=False)
            ),
        },
    ]
    repair_observation: dict[str, Any] = {
        "schemaRepairAttempt": 1,
        "taskKind": operation_name,
        **dict(metadata or {}),
    }
    return run_with_wall_timeout(
        lambda: _stream_structured(
            client,
            repair_messages,
            schema,
            repair_observation,
            reasoning_effort=repair_reasoning_effort or reasoning_effort,
            **(
                {"max_completion_tokens": max_completion_tokens}
                if max_completion_tokens is not None
                else {}
            ),
        ),
        operation=f"{operation_name}:schema-repair",
        observation=repair_observation,
    )


_SCHEMA_REPAIR_PREVIOUS_INPUT_MAX_CHARS = 16_000


def _schema_repair_payload(
    validation_errors: list[dict[str, Any]], parsed_input: Any | None,
) -> dict[str, Any]:
    """Keep repair context JSON-safe and bounded without persisting model output."""
    payload: dict[str, Any] = {"validationErrors": validation_errors}
    if parsed_input is None:
        return payload
    encoded = json.dumps(parsed_input, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= _SCHEMA_REPAIR_PREVIOUS_INPUT_MAX_CHARS:
        payload["previousParsedInput"] = parsed_input
        return payload
    sample = _SCHEMA_REPAIR_PREVIOUS_INPUT_MAX_CHARS // 2
    payload["previousParsedInput"] = {
        "truncated": True,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "prefix": encoded[:sample],
        "suffix": encoded[-sample:],
    }
    return payload


def focus_note(targets: set[str] | None) -> str:
    """ "이 항목만 고쳐라"를 프롬프트에 얹는 문구. 대상이 없으면 빈 문자열.

    **이건 지시일 뿐이고 보장이 아니다.** 실제 보장은 코드가 한다 —
    `app/design/nodes/artifact.py`의 merge_model 이 비대상 항목에 대해서는 LLM 출력을
    아예 읽지 않는다. 이 문구는 대상이 잘 고쳐지도록 초점을 좁혀줄 뿐이다.
    """
    if not targets:
        return ""
    listed = ", ".join(sorted(targets))
    return (
        "\n\n[Scope]\n"
        f"Change ONLY these elements: {listed}.\n"
        "Return every other element exactly as given — same names, same fields, same "
        "order. Adding a new element is allowed when the change genuinely requires one."
    )


def revision_messages(
    system_prompt: str,
    context_label: str,
    context_text: str,
    model_label: str,
    current_model: dict[str, Any],
    feedback: str,
    targets: set[str] | None = None,
) -> list[dict[str, str]]:
    """피드백 수정 프롬프트의 공통 뼈대: 맥락 + 현재 모델 + 사용자 피드백 (+ 범위).

    다섯 산출물의 리바이저가 모두 같은 모양이므로 여기 한 번만 적는다.
    """
    import json

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"[{context_label}]\n{context_text}\n\n"
                f"[{model_label}]\n"
                f"{json.dumps(current_model, ensure_ascii=False, indent=2)}\n\n"
                f"[User Feedback]\n{feedback}" + focus_note(targets)
            ),
        },
    ]
