"""요구사항 structured LLM runtime의 공개 행동 계약을 검증한다.

네이티브 structured 성공과 JSON fallback을 `invoke_structured`로만 관찰한다.
이렇게 하면 prompt 문구나 private parser를 바꿔도 호출 수·계측·설정 계약은
독립적으로 보존할 수 있다. 네트워크는 사용하지 않는다.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.requirements.agent import llm as legacy_llm
from app.requirements.config import settings
from app.requirements.runtime import structured_llm, telemetry


class StructuredResult(BaseModel):
    """테스트에서 structured 경로와 JSON fallback이 공유하는 최소 schema다."""

    value: str


class FakeMessage:
    """LangChain 메시지의 공개 관찰 필드만 제공한다."""

    def __init__(
        self,
        content: str = "",
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        fingerprint: str = "",
    ) -> None:
        self.content = content
        self.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        self.response_metadata = {"system_fingerprint": fingerprint}


class FakeStructuredInvoker:
    """네이티브 structured 물리 요청을 기록하는 test double이다."""

    def __init__(self, owner: FakeLlm) -> None:
        self.owner = owner

    def invoke(self, _messages: list[Any]) -> Any:
        self.owner.native_requests += 1
        return self.owner.native_result


class FakeLlm:
    """네트워크 없이 네이티브·fallback 요청 수를 드러내는 client다."""

    def __init__(self, native_result: Any, fallback_result: FakeMessage | None = None) -> None:
        self.native_result = native_result
        self.fallback_result = fallback_result
        self.native_requests = 0
        self.json_requests = 0

    def with_structured_output(self, _schema: type[BaseModel], **_kwargs: Any) -> Any:
        return FakeStructuredInvoker(self)

    def invoke(self, _messages: list[Any]) -> FakeMessage:
        self.json_requests += 1
        assert self.fallback_result is not None
        return self.fallback_result


@pytest.fixture(autouse=True)
def fresh_client() -> Any:
    """테스트가 만든 client cache가 다음 테스트로 새지 않게 한다."""

    structured_llm.reset_llm()
    yield
    structured_llm.reset_llm()


def test_default_sampling_is_reproducible() -> None:
    """temperature와 seed의 재현성 기본값을 고정한다."""

    assert settings.temperature == 0.0
    assert settings.seed is not None


def test_client_preserves_sampling_timeout_and_retry_configuration(monkeypatch) -> None:
    """client 생성자에 전달되는 실행 상한과 표본 설정을 검증한다."""

    created: list[dict[str, Any]] = []

    def make_client(**kwargs: Any) -> object:
        created.append(kwargs)
        return object()

    monkeypatch.setattr(structured_llm, "ChatOpenAI", make_client)

    client = structured_llm.build_llm()

    assert structured_llm.build_llm() is client
    assert len(created) == 1
    assert created[0]["temperature"] == settings.temperature
    assert created[0]["seed"] == settings.seed
    assert created[0]["reasoning_effort"] == settings.requirements_reasoning_effort
    assert created[0]["max_completion_tokens"] == settings.requirements_max_completion_tokens
    assert created[0]["timeout"] == 90
    assert created[0]["max_retries"] == 2


def test_transient_provider_failures_use_two_sdk_retries_in_one_logical_call(
    monkeypatch,
) -> None:
    """두 번의 500 응답 뒤 성공할 때 SDK retry와 logical telemetry를 함께 고정한다."""

    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                500,
                headers={"retry-after-ms": "0"},
                json={"error": {"message": "transient", "type": "server_error"}},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-retry-test",
                "object": "chat.completion",
                "created": 1,
                "model": "retry-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"value":"retried"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
            request=request,
        )

    transport_client = httpx.Client(transport=httpx.MockTransport(respond))
    client = structured_llm.ChatOpenAI(
        model="retry-test",
        base_url="https://nim.invalid/v1",
        api_key=SecretStr("test-key"),
        temperature=0,
        max_retries=2,
        http_client=transport_client,
    )
    monkeypatch.setattr(structured_llm, "build_llm", lambda **_kwargs: client)

    try:
        with telemetry.run_scope("retry") as stats:
            result = structured_llm.invoke_structured(StructuredResult, [])
    finally:
        transport_client.close()

    assert result == StructuredResult(value="retried")
    assert attempts == 3
    assert stats.llm_calls == 1
    assert stats.structured_fallbacks == 0


def test_legacy_llm_imports_are_the_canonical_runtime_api() -> None:
    """기존 agent import가 독립 client cache나 다른 호출 경로를 만들지 않는다."""

    assert legacy_llm.build_llm is structured_llm.build_llm
    assert legacy_llm.invoke_structured is structured_llm.invoke_structured
    assert legacy_llm.reset_llm is structured_llm.reset_llm
    assert legacy_llm.warmup_llm is structured_llm.warmup_llm


def test_native_structured_success_uses_one_physical_and_one_logical_call(
    monkeypatch,
) -> None:
    """네이티브 성공은 fallback 없이 물리 1회·논리 1회로 종료한다."""

    raw = FakeMessage(input_tokens=11, output_tokens=5, fingerprint="fp-native")
    fake = FakeLlm(
        {"raw": raw, "parsed": StructuredResult(value="native"), "parsing_error": None}
    )
    monkeypatch.setattr(structured_llm, "build_llm", lambda **_kwargs: fake)

    with telemetry.run_scope("native") as stats:
        result = structured_llm.invoke_structured(StructuredResult, [])

    assert result == StructuredResult(value="native")
    assert (fake.native_requests, fake.json_requests) == (1, 0)
    summary = stats.as_dict()
    assert summary["llm_calls"] == 1
    assert summary["structured_fallbacks"] == 0
    assert summary["prompt_tokens"] == 11
    assert summary["completion_tokens"] == 5
    assert summary["model_fingerprints"] == ["fp-native"]
    assert summary["llm_timing_events"][0]["operation"] == "structured:StructuredResult"


def test_empty_native_result_falls_back_once_and_preserves_telemetry_shape(
    monkeypatch,
) -> None:
    """parsed 없음은 JSON 물리 요청 1회로 fallback하고 논리 호출은 늘리지 않는다."""

    native_raw = FakeMessage(input_tokens=2, output_tokens=1, fingerprint="fp-fallback")
    fallback_raw = FakeMessage(
        'prefix\n{"value": "fallback"}\nsuffix',
        input_tokens=7,
        output_tokens=4,
        fingerprint="fp-fallback",
    )
    fake = FakeLlm(
        {"raw": native_raw, "parsed": None, "parsing_error": "unavailable"},
        fallback_raw,
    )
    monkeypatch.setattr(structured_llm, "build_llm", lambda **_kwargs: fake)

    with telemetry.run_scope("fallback") as stats:
        result = structured_llm.invoke_structured(StructuredResult, [])

    assert result == StructuredResult(value="fallback")
    assert (fake.native_requests, fake.json_requests) == (1, 1)
    summary = stats.as_dict()
    assert set(summary) == {
        "name",
        "llm_calls",
        "llm_failures",
        "structured_fallbacks",
        "prompt_tokens",
        "completion_tokens",
        "llm_seconds",
        "wall_seconds",
        "degradations",
        "model_fingerprints",
        "llm_timing_events",
    }
    assert summary["llm_calls"] == 1
    assert summary["llm_failures"] == 0
    assert summary["structured_fallbacks"] == 1
    assert summary["prompt_tokens"] == 9
    assert summary["completion_tokens"] == 5
    assert summary["model_fingerprints"] == ["fp-fallback"]
    event = summary["llm_timing_events"][0]
    assert set(event) == {
        "operation",
        "startedAt",
        "finishedAt",
        "elapsedSeconds",
        "status",
        "errorType",
        "structuredFallback",
    }
    assert event["operation"] == "structured:StructuredResult"
    assert event["status"] == "completed"
    assert event["structuredFallback"] is True
