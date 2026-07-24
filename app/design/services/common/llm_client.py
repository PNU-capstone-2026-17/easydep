"""산출물 생성·수정이 공유하는 LLM 호출 배관.

특정 산출물에 묶이지 않는다 — 프롬프트는 각 산출물 서비스가 만들고, 여기서는
호출과 벽시계 타임아웃만 책임진다.
"""
from __future__ import annotations

import os
import queue
import threading


def call_artifact_llm(system_prompt: str, user_prompt: str) -> str:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()

    client = OpenAI(
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("API_KEY"),
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "0")),
    )

    response = run_with_wall_timeout(
        lambda: client.chat.completions.create(
            model=os.getenv(
                "ARTIFACT_MODEL",
                os.getenv(
                    "CLASS_EXTRACTOR_MODEL",
                    os.getenv("DESIGN_AGENT_MODEL", "openai/gpt-oss-120b"),
                ),
            ),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            seed=42,
        )
    )
    return response.choices[0].message.content.strip()


def run_with_wall_timeout(callable_obj):
    timeout_seconds = float(os.getenv("LLM_WALL_TIMEOUT_SECONDS", "150"))
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def target():
        try:
            result_queue.put((True, callable_obj()))
        except Exception as error:
            result_queue.put((False, error))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    try:
        ok, result = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as error:
        raise TimeoutError(
            f"LLM request timed out after {timeout_seconds:g} seconds."
        ) from error

    if ok:
        return result

    raise result
