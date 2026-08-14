from __future__ import annotations

import json
import threading

import pytest
from pydantic import BaseModel

from app.design.services.common.structured import (
    StructuredLlmError,
    _stream_structured,
    capture_llm_timings,
    run_with_wall_timeout,
)


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
    assert events[0]["elapsedSeconds"] >= 0


def test_streaming_structured_output_records_progress_and_validates_schema():
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

    parsed = _stream_structured(client, [{"role": "user", "content": "x"}], Result, observation)

    assert parsed.answer == "ok"
    assert observation["transport"] == "structuredStream"
    assert observation["eventCount"] == 3
    assert observation["reasoningCharacters"] == len("thinking")
    assert observation["contentCharacters"] == len('{"answer":"ok"}')
    assert observation["ttftSeconds"] is not None
    assert observation["firstContentSeconds"] is not None
    assert observation["finishReasons"] == ["stop"]


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
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_max_completion_tokens", 8192)

    parsed = _stream_structured(
        client, [{"role": "user", "content": "x"}], Result, {}
    )

    assert parsed.answer == "ok"
    assert captured["max_completion_tokens"] == 8192


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
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_wall_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "easydep_experiment_session", "test-session")

    try:
        with (
            capture_llm_timings() as events,
            pytest.raises(StructuredLlmError, match="timed out"),
        ):
            run_with_wall_timeout(
                lambda: _stream_structured(
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
    from app.core.config import settings
    monkeypatch.setattr(settings, "easydep_experiment_session", "diagnostic")
    monkeypatch.setattr(settings, "llm_failure_response_sample_chars", 16)

    with pytest.raises(Exception):
        _stream_structured(
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
    monkeypatch.delenv("LLM_FAILURE_RESPONSE_SAMPLE_CHARS", raising=False)

    with pytest.raises(Exception):
        _stream_structured(
            client, [{"role": "user", "content": "x"}], Result, observation
        )

    assert not any(key.startswith("failureContent") for key in observation)
