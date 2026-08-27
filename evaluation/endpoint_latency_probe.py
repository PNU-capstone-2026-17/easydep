"""비밀정보를 남기지 않고 OpenAI 호환 엔드포인트의 지연 구간을 분리 측정한다."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from app.design.services.api_spec.extractor import ApiSpecModel, api_spec_messages


def _schema_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ApiSpecModel",
            "strict": True,
            "schema": ApiSpecModel.model_json_schema(),
        },
    }


def _messages(kind: str, design_thread_id: str | None = None) -> list[dict[str, str]]:
    if kind == "simple":
        return [{"role": "user", "content": "Reply with exactly: pong"}]
    if design_thread_id:
        from app.orchestration.adapters.design import DesignAdapter
        from app.design.schemas.architecture_state import usecase_spec_text

        config = {"configurable": {"thread_id": design_thread_id}}
        state = dict(DesignAdapter().graph.get_state(config).values)
        if not state:
            raise ValueError(f"설계 체크포인트를 찾을 수 없습니다: {design_thread_id}")
        return api_spec_messages(
            usecase_spec_text(state),
            str(state.get("class_diagram_puml") or ""),
            str(state.get("sequence_diagram_puml") or ""),
        )
    return [
        {
            "role": "system",
            "content": (
                "Return a REST API model matching the supplied JSON schema. "
                "Do not include markdown or prose."
            ),
        },
        {
            "role": "user",
            "content": (
                "Design POST /notes accepting title and content and returning id, title, "
                "and content. Also design GET /health. Data persists across restarts."
            ),
        },
    ]


def probe(
    kind: str,
    *,
    transport: str,
    timeout_seconds: float,
    design_thread_id: str | None = None,
) -> dict[str, Any]:
    load_dotenv()
    client = OpenAI(
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("API_KEY"),
        timeout=timeout_seconds,
        max_retries=0,
    )
    messages = _messages(kind, design_thread_id)
    input_characters = sum(len(message["content"]) for message in messages)
    started_at = datetime.now(UTC)
    started = perf_counter()
    first_event: float | None = None
    first_text: float | None = None
    event_count = 0
    content_parts: list[str] = []
    reasoning_characters = 0
    finish_reasons: list[str] = []
    error: BaseException | None = None
    try:
        if transport == "parse":
            response = client.chat.completions.parse(
                model=os.getenv("MODEL", "openai/gpt-oss-120b"),
                messages=messages,
                temperature=0,
                seed=42,
                response_format=ApiSpecModel if kind == "api-spec-schema" else None,
            )
            response_established = perf_counter() - started
            first_event = response_established
            first_text = response_established
            event_count = 1
            content = response.choices[0].message.content or ""
            parsed = response.choices[0].message.parsed
            content_parts = [content or (parsed.model_dump_json() if parsed else "")]
            finish_reasons = [
                str(choice.finish_reason)
                for choice in response.choices
                if choice.finish_reason
            ]
            raise StopIteration
        stream = client.chat.completions.create(
            model=os.getenv("MODEL", "openai/gpt-oss-120b"),
            messages=messages,
            temperature=0,
            seed=42,
            stream=True,
            response_format=_schema_format() if kind == "api-spec-schema" else None,
            max_completion_tokens=4096 if kind == "api-spec-schema" else 64,
        )
        response_established = perf_counter() - started
        for chunk in stream:
            now = perf_counter()
            event_count += 1
            if first_event is None:
                first_event = now - started
            for choice in chunk.choices:
                delta = choice.delta
                content = delta.content or ""
                reasoning = str(getattr(delta, "reasoning_content", "") or "")
                if (content or reasoning) and first_text is None:
                    first_text = now - started
                if content:
                    content_parts.append(content)
                reasoning_characters += len(reasoning)
                if choice.finish_reason:
                    finish_reasons.append(str(choice.finish_reason))
    except StopIteration:
        pass
    except BaseException as exc:  # 계측 결과로 보존한 뒤 호출자에게는 종료코드로 알린다.
        error = exc
        response_established = locals().get("response_established")
    completed = perf_counter() - started
    content_text = "".join(content_parts)
    structured_valid: bool | None = None
    structured_error_type: str | None = None
    if kind == "api-spec-schema" and error is None:
        try:
            ApiSpecModel.model_validate_json(content_text)
            structured_valid = True
        except Exception as exc:  # 응답 본문은 남기지 않고 파싱 결과만 기록한다.
            structured_valid = False
            structured_error_type = type(exc).__name__
    return {
        "schemaVersion": "easydep-endpoint-latency/v1",
        "probe": kind,
        "transport": transport,
        "inputSource": "design-checkpoint" if design_thread_id else "probe-fixture",
        "inputCharacters": input_characters,
        "startedAt": started_at.isoformat(),
        "model": os.getenv("MODEL", "openai/gpt-oss-120b"),
        "timeoutSeconds": timeout_seconds,
        "responseEstablishedSeconds": (
            round(response_established, 6)
            if isinstance(response_established, float)
            else None
        ),
        "firstEventSeconds": round(first_event, 6) if first_event is not None else None,
        "firstTextSeconds": round(first_text, 6) if first_text is not None else None,
        "completedSeconds": round(completed, 6),
        "eventCount": event_count,
        "contentCharacters": len(content_text),
        "reasoningCharacters": reasoning_characters,
        "structuredValid": structured_valid,
        "structuredErrorType": structured_error_type,
        "finishReasons": finish_reasons,
        "status": "failed" if error is not None else "completed",
        "errorType": type(error).__name__ if error is not None else None,
        "error": str(error) if error is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 엔드포인트 지연 구간 측정")
    parser.add_argument("--probe", choices=("simple", "api-spec-schema"), required=True)
    parser.add_argument("--transport", choices=("stream", "parse"), default="stream")
    parser.add_argument("--design-thread-id")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = probe(
        args.probe,
        transport=args.transport,
        timeout_seconds=args.timeout_seconds,
        design_thread_id=args.design_thread_id,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
