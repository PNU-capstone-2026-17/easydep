"""장기 LLM 호출과 독립된 endpoint 상태 probe를 예약한다."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any
from time import perf_counter

from app.config import settings


def _emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def _probe(operation: str, timeout_seconds: float) -> None:
    from openai import OpenAI
    from app.config import settings

    started = perf_counter()
    first_event: float | None = None
    status = "completed"
    error_type: str | None = None
    status_code: int | None = None
    _emit("llmStallProbeStarted", stalledOperation=operation)
    try:
        client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            max_retries=0,
            timeout=timeout_seconds,
        )
        stream = client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": "Reply with exactly: pong"}],
            temperature=0,
            seed=42,
            stream=True,
            max_completion_tokens=32,
        )
        for _chunk in stream:
            if first_event is None:
                first_event = perf_counter() - started
    except BaseException as error:  # 관찰 실패를 주 호출로 전파하지 않는다.
        status = "failed"
        error_type = type(error).__name__
        status_code = getattr(error, "status_code", None)
    elapsed = perf_counter() - started
    _emit(
        "llmStallProbeFinished",
        stalledOperation=operation,
        status=status,
        errorType=error_type,
        statusCode=status_code,
        rateLimited=status_code == 429 or error_type == "RateLimitError",
        endpointResponsive=status == "completed",
        firstEventSeconds=round(first_event, 6) if first_event is not None else None,
        elapsedSeconds=round(elapsed, 6),
    )


def start_stall_probe(operation: str) -> threading.Event:
    """설정된 지연을 넘기면 독립적인 짧은 probe를 한 번 실행한다."""
    completed = threading.Event()
    threshold_override = os.getenv("EASYDEP_LLM_STALL_PROBE_AFTER_SECONDS")
    threshold_value: str | float | None = (
        threshold_override
        if threshold_override is not None
        else settings.easydep_llm_stall_probe_after_seconds
    )
    if not threshold_value:
        return completed

    threshold = float(threshold_value)
    raw_timeout = os.getenv("EASYDEP_LLM_STALL_PROBE_TIMEOUT_SECONDS")
    timeout_seconds = (
        float(raw_timeout)
        if raw_timeout is not None
        else settings.easydep_llm_stall_probe_timeout_seconds
    )

    def watch() -> None:
        if not completed.wait(threshold):
            _probe(operation, timeout_seconds)

    threading.Thread(target=watch, daemon=True, name="easydep-llm-stall-probe").start()
    return completed
