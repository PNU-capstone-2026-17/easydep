"""장기 LLM 호출과 독립된 endpoint 상태 probe를 예약한다."""

from __future__ import annotations

import json
import os
import threading
from time import perf_counter


def _emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def _probe(operation: str, timeout_seconds: float) -> None:
    from openai import OpenAI

    started = perf_counter()
    first_event: float | None = None
    status = "completed"
    error_type: str | None = None
    status_code: int | None = None
    _emit("llmStallProbeStarted", stalledOperation=operation)
    try:
        client = OpenAI(
            base_url=os.getenv("BASE_URL"),
            api_key=os.getenv("API_KEY"),
            timeout=timeout_seconds,
            max_retries=0,
        )
        stream = client.chat.completions.create(
            model=os.getenv("DESIGN_AGENT_MODEL", "openai/gpt-oss-120b"),
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
    raw_threshold = os.getenv("EASYDEP_LLM_STALL_PROBE_AFTER_SECONDS")
    if not raw_threshold:
        return completed
    threshold = float(raw_threshold)
    if threshold <= 0:
        return completed
    timeout_seconds = float(os.getenv("EASYDEP_LLM_STALL_PROBE_TIMEOUT_SECONDS", "60"))

    def watch() -> None:
        if not completed.wait(threshold):
            _probe(operation, timeout_seconds)

    threading.Thread(target=watch, daemon=True, name="easydep-llm-stall-probe").start()
    return completed
