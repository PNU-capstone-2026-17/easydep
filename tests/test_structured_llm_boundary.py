from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from pydantic import BaseModel, model_validator

import app.design.services.common.structured as structured
from app.design.services.common.structured import (
    StructuredLlmError,
    bind_context,
    capture_llm_timings,
    parse_with_schema_repair,
    record_llm_timing,
    run_with_wall_timeout,
    schema_repair_payload,
    stream_structured_response,
)


def test_local_schema_failure_gets_one_bounded_full_object_retry(monkeypatch):
    class Result(BaseModel):
        answer: str

    calls: list[list[dict[str, str]]] = []
    repair_payloads: list[dict[str, Any]] = []

    def stream(_client, messages, schema, _observation, **_kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return schema.model_validate({})
        return schema(answer="ok")

    def capture_payload(validation_errors, parsed_input):
        payload = schema_repair_payload(validation_errors, parsed_input)
        repair_payloads.append(payload)
        return payload

    monkeypatch.setattr(structured, "stream_structured_response", stream)
    monkeypatch.setattr(structured, "schema_repair_payload", capture_payload)

    parsed = parse_with_schema_repair(
        object(), [{"role": "user", "content": "generate"}], Result
    )

    assert parsed.answer == "ok"
    assert len(calls) == 2
    assert len(repair_payloads) == 1
    assert list(repair_payloads[0]["validationErrors"][0]["loc"]) == ["answer"]


def test_value_error_context_is_serialized_for_schema_repair(monkeypatch):
    class Result(BaseModel):
        answer: str

        @model_validator(mode="after")
        def answer_is_not_empty(self):
            if not self.answer.strip():
                raise ValueError("answer must not be empty")
            return self

    calls: list[list[dict[str, str]]] = []
    repair_payloads: list[dict[str, Any]] = []

    def stream(_client, messages, schema, _observation, **_kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return schema.model_validate({"answer": ""})
        return schema(answer="ok")

    def capture_payload(validation_errors, parsed_input):
        payload = schema_repair_payload(validation_errors, parsed_input)
        repair_payloads.append(payload)
        return payload

    monkeypatch.setattr(structured, "stream_structured_response", stream)
    monkeypatch.setattr(structured, "schema_repair_payload", capture_payload)

    parsed = parse_with_schema_repair(
        object(), [{"role": "user", "content": "generate"}], Result
    )

    assert parsed.answer == "ok"
    assert len(calls) == 2
    assert "answer must not be empty" in repair_payloads[0]["validationErrors"][0]["msg"]


def test_schema_repair_carries_a_bounded_prior_model_level_input(monkeypatch):
    class Result(BaseModel):
        source: str
        target: str

        @model_validator(mode="after")
        def endpoints_must_differ(self):
            if self.source == self.target:
                raise ValueError("endpoints must differ")
            return self

    invalid = {"source": "same", "target": "same", "padding": "x" * 128}
    repair_payloads = []
    original_payload = schema_repair_payload

    def capture_payload(validation_errors, parsed_input):
        payload = original_payload(validation_errors, parsed_input)
        repair_payloads.append(payload)
        return payload

    class Completions:
        def __init__(self):
            self.contents = iter((json.dumps(invalid), '{"source":"left","target":"right"}'))

        def create(self, **_kwargs):
            choice = type("Choice", (), {
                "delta": type("Delta", (), {
                    "content": next(self.contents), "reasoning_content": ""
                })(),
                "finish_reason": "stop",
            })()
            return [type("Chunk", (), {"choices": [choice]})()]

    completions = Completions()
    client = type("Client", (), {
        "chat": type("Chat", (), {"completions": completions})()
    })()

    monkeypatch.setattr(structured, "SCHEMA_REPAIR_PREVIOUS_INPUT_MAX_CHARS", 32)
    monkeypatch.setattr(structured, "schema_repair_payload", capture_payload)

    parsed = parse_with_schema_repair(
        client, [{"role": "user", "content": "generate"}], Result
    )

    previous = repair_payloads[0]["previousParsedInput"]
    assert parsed.source == "left"
    assert len(repair_payloads) == 1
    assert previous["truncated"] is True
    assert len(previous["sha256"]) == 64


def test_schema_repair_keeps_or_explicitly_overrides_call_reasoning_effort(monkeypatch):
    class Result(BaseModel):
        answer: str

    efforts: list[str] = []

    def stream(_client, _messages, schema, _observation, *, reasoning_effort):
        efforts.append(reasoning_effort)
        if len(efforts) == 1:
            return schema.model_validate({})
        return schema(answer="ok")

    monkeypatch.setattr(
        "app.design.services.common.structured.stream_structured_response", stream
    )

    parsed = parse_with_schema_repair(
        object(),
        [{"role": "user", "content": "generate"}],
        Result,
        reasoning_effort="low",
        repair_reasoning_effort="high",
    )

    assert parsed.answer == "ok"
    assert efforts == ["low", "high"]


def test_non_validation_failure_is_not_retried(monkeypatch):
    class Result(BaseModel):
        answer: str

    calls = 0

    def stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("endpoint stalled")

    monkeypatch.setattr(structured, "stream_structured_response", stream)

    with pytest.raises(StructuredLlmError, match="endpoint stalled"):
        parse_with_schema_repair(
            object(), [{"role": "user", "content": "generate"}], Result
        )
    assert calls == 1


def test_structured_llm_error_names_the_output_schema():
    def fail():
        raise TimeoutError("Request timed out.")

    with (
        capture_llm_timings() as events,
        pytest.raises(StructuredLlmError, match="ApiSpecModel: Request timed out"),
    ):
        run_with_wall_timeout(fail, operation="ApiSpecModel")

    assert events[0]["operation"] == "ApiSpecModel"
    assert events[0]["status"] == "failed"
    assert events[0]["errorType"] == "TimeoutError"
    assert events[0]["failureCategory"] == "timeout"
    assert events[0]["elapsedSeconds"] >= 0


def test_streaming_structured_output_records_progress_and_validates_schema(monkeypatch):
    class Result(BaseModel):
        answer: str

    class Delta:
        def __init__(self, content="", reasoning=""):
            self.content = content
            self.reasoning_content = reasoning

    class Choice:
        def __init__(self, content="", reasoning="", finish_reason=None):
            self.delta = Delta(content, reasoning)
            self.finish_reason = finish_reason

    class Chunk:
        def __init__(self, choice):
            self.choices = [choice]

    chunks = [
        Chunk(Choice(reasoning="thinking")),
        Chunk(Choice(content='{"answer":')),
        Chunk(Choice(content='"ok"}', finish_reason="stop")),
    ]
    completions = type("Completions", (), {"create": lambda *_args, **_kwargs: chunks})()
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    observation = {}
    from app.config import settings
    monkeypatch.setattr(settings, "llm_capture_response_content", True)

    parsed = stream_structured_response(
        client, [{"role": "user", "content": "x"}], Result, observation
    )

    assert parsed.answer == "ok"
    assert observation["transport"] == "structuredStream"
    assert observation["eventCount"] == 3
    assert observation["reasoningCharacters"] == len("thinking")
    assert observation["contentCharacters"] == len('{"answer":"ok"}')
    assert observation["ttftSeconds"] is not None
    assert observation["firstContentSeconds"] is not None
    assert observation["finishReasons"] == ["stop"]
    assert observation["responseContent"] == '{"answer":"ok"}'
    assert observation["reasoningContent"] == "thinking"


def test_streaming_structured_output_accepts_an_explicit_completion_limit(monkeypatch):
    class Result(BaseModel):
        answer: str

    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            choice = type("Choice", (), {
                "delta": type("Delta", (), {
                    "content": '{"answer":"ok"}', "reasoning_content": ""
                })(),
                "finish_reason": "stop",
            })()
            return [type("Chunk", (), {"choices": [choice]})()]

    client = type("Client", (), {
        "chat": type("Chat", (), {"completions": Completions()})()
    })()
    from app.config import settings
    monkeypatch.setattr(settings, "llm_max_completion_tokens", 8192)

    parsed = stream_structured_response(
        client,
        [{"role": "user", "content": "x"}],
        Result,
        {},
        max_completion_tokens=1024,
    )

    assert parsed.answer == "ok"
    assert captured["max_completion_tokens"] == 1024
    assert captured["reasoning_effort"] == "medium"
    assert captured["temperature"] == settings.temperature
    assert captured["seed"] == settings.seed


def test_streaming_structured_output_uses_explicit_effort_and_omits_it_for_non_gpt_oss(
    monkeypatch,
):
    class Result(BaseModel):
        answer: str

    requests: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            requests.append(kwargs)
            choice = type("Choice", (), {
                "delta": type("Delta", (), {
                    "content": '{"answer":"ok"}', "reasoning_content": ""
                })(),
                "finish_reason": "stop",
            })()
            return [type("Chunk", (), {"choices": [choice]})()]

    client = type("Client", (), {
        "chat": type("Chat", (), {"completions": Completions()})()
    })()
    from app.config import settings

    monkeypatch.setattr(settings, "model", "openai/gpt-oss-120b")
    stream_structured_response(
        client, [{"role": "user", "content": "x"}], Result, {}, reasoning_effort="low"
    )
    monkeypatch.setattr(settings, "model", "meta/llama-3.1-8b-instruct")
    stream_structured_response(
        client, [{"role": "user", "content": "x"}], Result, {}, reasoning_effort="high"
    )

    assert requests[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in requests[1]


def test_streaming_structured_output_rejects_unknown_reasoning_effort():
    class Result(BaseModel):
        answer: str

    with pytest.raises(ValueError, match="unsupported reasoning effort"):
        stream_structured_response(
            object(),
            [{"role": "user", "content": "x"}],
            Result,
            {},
            reasoning_effort="maximum",
        )


def test_timeout_retains_incremental_stream_progress_without_content(
    monkeypatch, capsys
):
    class Result(BaseModel):
        answer: str

    class Choice:
        delta = type("Delta", (), {
            "content": '{"answer":', "reasoning_content": "thinking"
        })()
        finish_reason = None

    class BlockingStream:
        def __init__(self):
            self.sent = False
            self.release = threading.Event()

        def __iter__(self):
            return self

        def __next__(self):
            if not self.sent:
                self.sent = True
                return type("Chunk", (), {"choices": [Choice()]})()
            self.release.wait(1)
            raise StopIteration

    stream = BlockingStream()
    completions = type(
        "Completions", (), {"create": lambda *_args, **_kwargs: stream}
    )()
    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": completions})()}
    )()
    observation = {}
    from app.config import settings
    monkeypatch.setattr(settings, "llm_wall_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "easydep_experiment_session", "test-session")

    try:
        with (
            capture_llm_timings() as events,
            pytest.raises(StructuredLlmError, match="timed out"),
        ):
            run_with_wall_timeout(
                lambda: stream_structured_response(
                    client, [{"role": "user", "content": "x"}], Result, observation
                ),
                operation="Result",
                observation=observation,
            )
    finally:
        stream.release.set()

    event = events[0]
    assert event["eventCount"] == 1
    assert event["contentCharacters"] == len('{"answer":')
    assert event["reasoningCharacters"] == len("thinking")
    assert event["finishReasonObserved"] is False
    assert event["firstEventAt"]
    assert event["lastEventAt"]
    assert "content" not in event
    assert "reasoning" not in event
    finished = [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
        if '"event": "llmOperationFinished"' in line
    ][-1]
    assert finished["eventCount"] == 1
    assert finished["finishReasonObserved"] is False


def test_invalid_structured_output_records_bounded_content_samples_only_in_experiment(
    monkeypatch,
):
    class Result(BaseModel):
        answer: str

    invalid = '{"answer":"' + ("UC1," * 20)
    choice = type("Choice", (), {
        "delta": type("Delta", (), {
            "content": invalid, "reasoning_content": "private reasoning"
        })(),
        "finish_reason": "length",
    })()
    completions = type(
        "Completions", (),
        {"create": lambda *_args, **_kwargs: [type("Chunk", (), {"choices": [choice]})()]},
    )()
    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": completions})()}
    )()
    observation = {}
    from app.config import settings
    monkeypatch.setattr(settings, "easydep_experiment_session", "diagnostic")
    monkeypatch.setattr(settings, "llm_capture_response_content", False)
    monkeypatch.setattr(settings, "llm_failure_response_sample_chars", 16)

    with pytest.raises(Exception):
        stream_structured_response(
            client, [{"role": "user", "content": "x"}], Result, observation
        )

    assert observation["failureContentPrefix"] == invalid[:16]
    assert observation["failureContentSuffix"] == invalid[-16:]
    assert observation["failureContentSampleCharacters"] == 16
    assert observation["failureContentSampleTruncated"] is True
    assert len(observation["failureContentSha256"]) == 64
    assert "private reasoning" not in json.dumps(observation)


def test_invalid_structured_output_does_not_record_content_without_opt_in(monkeypatch):
    class Result(BaseModel):
        answer: str

    choice = type("Choice", (), {
        "delta": type("Delta", (), {"content": "{", "reasoning_content": ""})(),
        "finish_reason": "length",
    })()
    completions = type(
        "Completions", (),
        {"create": lambda *_args, **_kwargs: [type("Chunk", (), {"choices": [choice]})()]},
    )()
    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": completions})()}
    )()
    observation = {}
    from app.config import settings
    monkeypatch.setattr(settings, "llm_capture_response_content", False)
    monkeypatch.delenv("LLM_FAILURE_RESPONSE_SAMPLE_CHARS", raising=False)

    with pytest.raises(Exception):
        stream_structured_response(
            client, [{"role": "user", "content": "x"}], Result, observation
        )

    assert not any(key.startswith("failureContent") for key in observation)


def test_invalid_response_keeps_full_content_reasoning_and_validation_error(monkeypatch):
    """schema 실패 때에도 실제 NIM 응답과 거절 이유를 함께 확인할 수 있다."""

    class Result(BaseModel):
        answer: str

    invalid = '{"answer": 42}'
    choice = type("Choice", (), {
        "delta": type("Delta", (), {
            "content": invalid,
            "reasoning_content": "answer should be concise",
        })(),
        "finish_reason": "stop",
    })()
    completions = type(
        "Completions",
        (),
        {"create": lambda *_args, **_kwargs: [
            type("Chunk", (), {"choices": [choice]})()
        ]},
    )()
    client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": completions})()}
    )()
    observation = {}
    from app.config import settings
    monkeypatch.setattr(settings, "llm_capture_response_content", True)

    with pytest.raises(Exception):
        stream_structured_response(
            client, [{"role": "user", "content": "x"}], Result, observation
        )

    assert observation["responseContent"] == invalid
    assert observation["reasoningContent"] == "answer should be concise"
    assert observation["schemaValidationErrors"][0]["loc"] == ("answer",)


def test_timing_capture_propagates_to_concurrent_context_bound_workers():
    def emit(operation: str) -> None:
        record_llm_timing(operation, status="cache_hit")

    with capture_llm_timings() as events, ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(bind_context(emit), f"operation-{index}")
            for index in range(8)
        ]
        for future in futures:
            future.result()

    assert len(events) == 8
    assert {event["operation"] for event in events} == {
        f"operation-{index}" for index in range(8)
    }
    assert all(event["observationScope"] == "logicalOnly" for event in events)
    assert all(event["physicalRequest"] is False for event in events)


def test_timing_capture_isolated_between_calls():
    with capture_llm_timings() as first:
        record_llm_timing("first", status="completed")

    with capture_llm_timings() as second:
        record_llm_timing("second", status="cache_miss")

    assert [event["operation"] for event in first] == ["first"]
    assert [event["operation"] for event in second] == ["second"]
    assert first is not second


def test_timing_event_retains_digest_and_logical_physical_repair_handoff_cache_metadata():
    metadata = {
        "inputDigest": "sha256:input",
        "logicalRequest": "operation-unit",
        "physicalRequest": False,
        "repairAttempt": 1,
        "handoffOwner": "collaboration",
        "cacheKey": "unit:UC1:main:1",
    }

    with capture_llm_timings() as events:
        record_llm_timing("InteractionOperations", status="cache_hit", metadata=metadata)

    event = events[0]
    assert event["cacheStatus"] == "hit"
    for key, value in metadata.items():
        assert event[key] == value
