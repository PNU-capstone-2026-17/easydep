"""app/requirements/agent/llm.py — 순수 헬퍼 + 표본 설정·계측 배선 (네트워크 불필요).

`ChatOpenAI(...)` 생성 자체는 네트워크를 타지 않으므로, 어떤 파라미터로 만들어지는지는
호출 없이 확인할 수 있다. conftest가 더미 API_KEY를 넣어 둔다.
"""
import pytest

from app.requirements.agent import llm as llm_mod
from app.requirements.agent.llm import _extract_json, _message_text
from app.requirements.common import telemetry
from app.requirements.config import settings


@pytest.fixture(autouse=True)
def _fresh_client():
    """테스트가 만든 클라이언트가 다음 테스트로 새지 않게 한다."""
    llm_mod.reset_llm()
    yield
    llm_mod.reset_llm()


def test_message_text_from_string():
    assert _message_text("hello") == "hello"


def test_message_text_from_parts():
    content = [{"type": "text", "text": "foo"}, "bar", {"other": 1}]
    assert _message_text(content) == "foobar"


def test_extract_json_strips_prose_and_fences():
    raw = 'Here is the result:\n```json\n{"a": 1, "b": [2, 3]}\n```\nDone.'
    assert _extract_json(raw) == '{"a": 1, "b": [2, 3]}'


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        _extract_json("no json here")


# ---------------------------------------------------------------------------
# 표본 설정 — 설계 원칙 1번이 재현성이므로 기본값이 그걸 배신하면 안 된다.
# ---------------------------------------------------------------------------
def test_default_sampling_is_reproducible():
    """2026-07-26까지 temperature=0.2에 seed가 없어서 매 실행 결과가 달랐다."""
    assert settings.temperature == 0.0
    assert settings.seed is not None


def test_client_carries_the_configured_sampling_params():
    client = llm_mod.build_llm()
    assert client.temperature == settings.temperature
    assert client.seed == settings.seed
    assert client.reasoning_effort == settings.requirements_reasoning_effort
    assert client.max_tokens == settings.requirements_max_completion_tokens


def test_client_is_reused_within_a_process():
    """NIM 콜드 스타트를 프로세스당 1회로 묶는 캐시가 유지되는지."""
    assert llm_mod.build_llm() is llm_mod.build_llm()


def test_reset_llm_makes_changed_settings_take_effect(monkeypatch):
    """캐시 때문에 "설정은 바뀌었는데 호출은 옛 값으로" 나가는 일을 막는다."""
    first = llm_mod.build_llm()
    monkeypatch.setattr(settings, "seed", 4242)
    assert llm_mod.build_llm().seed == first.seed   # 아직 캐시된 클라이언트다
    llm_mod.reset_llm()
    assert llm_mod.build_llm().seed == 4242


# ---------------------------------------------------------------------------
# 계측 배선 — 토큰·지문을 실제로 집어오는가, 폴백 판단이 명시적인가.
# ---------------------------------------------------------------------------
class _FakeMessage:
    def __init__(self, usage=None, metadata=None):
        self.usage_metadata = usage
        self.response_metadata = metadata


class _FakeStructured:
    def __init__(self, payload):
        self._payload = payload

    def invoke(self, messages):
        return self._payload


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.kwargs = None

    def with_structured_output(self, schema, **kwargs):
        self.kwargs = kwargs
        return _FakeStructured(self._payload)


class _Parsed:
    pass


def test_native_path_records_usage_and_fingerprint():
    raw = _FakeMessage(
        usage={"input_tokens": 11, "output_tokens": 5},
        metadata={"system_fingerprint": "fp_abc"},
    )
    parsed = _Parsed()
    fake = _FakeLLM({"raw": raw, "parsed": parsed, "parsing_error": None})

    with telemetry.run_scope("t") as stats:
        with telemetry.record_llm_call("op") as call:
            got = llm_mod._native_structured(fake, _Parsed, [], call)

    assert got is parsed
    assert fake.kwargs["include_raw"] is True     # 지문·토큰을 읽으려면 필수다
    summary = stats.as_dict()
    assert summary["prompt_tokens"] == 11
    assert summary["completion_tokens"] == 5
    assert summary["model_fingerprints"] == ["fp_abc"]
    assert summary["structured_fallbacks"] == 0


def test_empty_parsed_is_a_fallback_not_a_success():
    """NIM이 간헐적으로 내는 빈 parsed가 조용히 통과하면 안 된다."""
    fake = _FakeLLM(
        {"raw": _FakeMessage(), "parsed": None, "parsing_error": "boom"}
    )
    with telemetry.run_scope("t") as stats:
        with telemetry.record_llm_call("op") as call:
            got = llm_mod._native_structured(fake, _Parsed, [], call)

    assert got is None                                   # 폴백 경로로 넘긴다
    assert "boom" in (call.fallback_reason or "")        # 사유가 남는다
    assert stats.as_dict()["structured_fallbacks"] == 1


def test_missing_fingerprint_is_not_an_error():
    """지문을 안 주는 게이트웨이 때문에 호출이 죽으면 안 된다."""
    fake = _FakeLLM({"raw": _FakeMessage(), "parsed": _Parsed(), "parsing_error": None})
    with telemetry.run_scope("t") as stats:
        with telemetry.record_llm_call("op") as call:
            llm_mod._native_structured(fake, _Parsed, [], call)
    assert stats.as_dict()["model_fingerprints"] == []
