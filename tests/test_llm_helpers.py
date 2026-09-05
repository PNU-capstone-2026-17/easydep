"""요구사항 structured LLM runtime의 공개 행동 계약을 검증한다.

네이티브 structured 성공과 JSON fallback을 `invoke_structured`로만 관찰한다.
이렇게 하면 prompt 문구나 private parser를 바꿔도 호출 수·계측·설정 계약은
독립적으로 보존할 수 있다. 네트워크는 사용하지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.config import settings as llm_settings
from app.llm_profiles import profile_for
from app.requirements.config import Settings, settings
from app.requirements.runtime import structured_llm, telemetry
from app.requirements.schemas import DeploymentNeedsResult


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
        self.structured_schema: dict[str, Any] | None = None
        self.structured_options: dict[str, Any] | None = None
        self.json_messages: list[Any] | None = None

    def with_structured_output(self, schema: dict[str, Any], **_kwargs: Any) -> Any:
        self.structured_schema = schema
        self.structured_options = _kwargs
        return FakeStructuredInvoker(self)

    def invoke(self, messages: list[Any]) -> FakeMessage:
        self.json_requests += 1
        self.json_messages = messages
        assert self.fallback_result is not None
        return self.fallback_result


def _schema_descriptions(value: Any):
    if isinstance(value, dict):
        description = value.get("description")
        if isinstance(description, str):
            yield description
        for item in value.values():
            yield from _schema_descriptions(item)
    elif isinstance(value, list):
        for item in value:
            yield from _schema_descriptions(item)


@pytest.fixture(autouse=True)
def fresh_client() -> Any:
    """테스트가 만든 client cache가 다음 테스트로 새지 않게 한다."""

    structured_llm.reset_llm()
    yield
    structured_llm.reset_llm()


def test_default_sampling_matches_screened_setting() -> None:
    """코드 기본값과 환경에서 허용하는 sampling 범위를 따로 확인한다."""

    assert Settings.model_fields["temperature"].default == 0.6
    # 실제 실행값은 .env에서 바꿀 수 있다. 사용자가 선택한 0.2 같은 정상값을
    # 코드 기본값과 다르다는 이유로 실패시키지 않되, 0은 사용하지 않는다.
    assert settings.temperature >= 0.2
    assert settings.seed is not None


def test_client_preserves_sampling_timeout_and_retry_configuration(monkeypatch) -> None:
    """client 생성자에 전달되는 실행 상한과 표본 설정을 검증한다."""

    created: list[dict[str, Any]] = []

    def make_client(**kwargs: Any) -> object:
        created.append(kwargs)
        return object()

    monkeypatch.setattr(structured_llm, "ChatOpenAI", make_client)

    client = structured_llm.build_llm()
    profile = profile_for(
        llm_settings.model,
        fallback_temperature=settings.temperature,
        fallback_max_tokens=settings.requirements_max_completion_tokens,
    )

    assert structured_llm.build_llm() is client
    assert len(created) == 1
    assert created[0]["temperature"] == profile.temperature
    assert created[0]["seed"] == settings.seed
    assert created[0]["reasoning_effort"] == profile.resolve_reasoning(
        settings.requirements_reasoning_effort
    )
    assert created[0]["max_completion_tokens"] == profile.completion_limit(
        settings.requirements_max_completion_tokens
    )
    assert created[0]["timeout"] == 90
    assert created[0]["max_retries"] == llm_settings.llm_max_retries


def test_transient_provider_failures_use_two_sdk_retries_in_one_logical_call(
    monkeypatch,
) -> None:
    """두 번의 500 응답 뒤 성공할 때 SDK retry와 logical telemetry를 함께 고정한다."""

    attempts = 0
    request_payloads: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        request_payloads.append(json.loads(request.content))
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
    response_format = request_payloads[0]["response_format"]["json_schema"]
    transmitted_schema = response_format["schema"]
    assert response_format["strict"] is True
    assert transmitted_schema["additionalProperties"] is False
    assert set(transmitted_schema["properties"]) == set(
        transmitted_schema["required"]
    )
    assert "테스트에서 structured 경로" not in json.dumps(
        transmitted_schema, ensure_ascii=False
    )


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


def test_native_structured_uses_provider_safe_schema_and_validates_dict_result(
    monkeypatch,
) -> None:
    fake = FakeLlm({"raw": FakeMessage(), "parsed": {"value": "native"}, "parsing_error": None})
    monkeypatch.setattr(structured_llm, "build_llm", lambda **_kwargs: fake)

    result = structured_llm.invoke_structured(StructuredResult, [])

    assert result == StructuredResult(value="native")
    assert fake.structured_schema is not None
    assert fake.structured_schema["additionalProperties"] is False
    assert set(fake.structured_schema["properties"]) == set(fake.structured_schema["required"])
    assert "description" not in fake.structured_schema
    assert fake.structured_options == {
        "method": "json_schema",
        "include_raw": True,
        "strict": True,
    }


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
    assert fake.json_messages is not None
    assert "테스트에서 structured 경로" not in str(fake.json_messages[-1].content)


def test_invalid_native_dict_uses_the_existing_json_fallback(monkeypatch) -> None:
    fake = FakeLlm(
        {"raw": FakeMessage(), "parsed": {"wrong": "value"}, "parsing_error": None},
        FakeMessage('{"value":"fallback"}'),
    )
    monkeypatch.setattr(structured_llm, "build_llm", lambda **_kwargs: fake)

    result = structured_llm.invoke_structured(StructuredResult, [])

    assert result == StructuredResult(value="fallback")
    assert (fake.native_requests, fake.json_requests) == (1, 1)


def test_non_strict_structured_call_keeps_the_english_only_schema_boundary(
    monkeypatch,
) -> None:
    fake = FakeLlm(
        {
            "raw": FakeMessage(),
            "parsed": {"deploymentNeeds": {}},
            "parsing_error": None,
        }
    )
    monkeypatch.setattr(structured_llm, "build_llm", lambda **_kwargs: fake)

    result = structured_llm.invoke_structured(
        DeploymentNeedsResult, [], strict=False
    )

    assert result == DeploymentNeedsResult(deploymentNeeds={})
    assert fake.structured_options is not None
    assert fake.structured_options["strict"] is False
    assert fake.structured_schema is not None
    assert all(
        description.isascii()
        for description in _schema_descriptions(fake.structured_schema)
    )


def test_non_strict_mode_rejects_unapproved_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        structured_llm,
        "build_llm",
        lambda **_kwargs: pytest.fail("policy rejection must happen before client creation"),
    )

    with pytest.raises(ValueError, match="Non-strict structured output"):
        structured_llm.invoke_structured(StructuredResult, [], strict=False)
