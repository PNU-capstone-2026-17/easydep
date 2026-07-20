"""app/agent/llm.py 의 순수 헬퍼 테스트 (네트워크 불필요)."""
import pytest

from app.agent.llm import _extract_json, _message_text


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
