"""Provider-compatible JSON Schema contracts for LLM calls."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.llm_schema import remove_non_ascii_descriptions, strict_json_schema


class NestedPayload(BaseModel):
    """중첩 모델 설명은 LLM 스키마에 전달하면 안 된다."""

    optional_label: str | None = None


class StrictPayload(BaseModel):
    """한국어 모델 설명은 LLM 스키마에서 제거한다."""

    label: str = Field(description="English field guidance is retained.")
    nested: NestedPayload


class OpenMapPayload(BaseModel):
    values: dict[str, str]


def _object_nodes(value: Any):
    if isinstance(value, dict):
        if value.get("type") == "object":
            yield value
        for item in value.values():
            yield from _object_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _object_nodes(item)


def _descriptions(value: Any):
    if isinstance(value, dict):
        description = value.get("description")
        if isinstance(description, str):
            yield description
        for item in value.values():
            yield from _descriptions(item)
    elif isinstance(value, list):
        for item in value:
            yield from _descriptions(item)


def test_strict_json_schema_requires_every_property_and_closes_objects():
    schema = strict_json_schema(StrictPayload)

    for node in _object_nodes(schema):
        assert set(node.get("properties", ())) == set(node.get("required", ()))
        assert node["additionalProperties"] is False


def test_strict_json_schema_removes_non_ascii_descriptions_but_keeps_english_fields():
    schema = strict_json_schema(StrictPayload)

    assert list(_descriptions(schema)) == ["English field guidance is retained."]


def test_description_sanitizer_preserves_non_description_schema_semantics():
    source = {
        "type": "object",
        "description": "한국어 object description",
        "properties": {
            "value": {
                "type": "string",
                "description": "한국어 field description",
            },
            "hint": {"type": "string", "description": "English hint."},
        },
        "required": ["value"],
    }

    sanitized = remove_non_ascii_descriptions(source)

    assert sanitized == {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
            "hint": {"type": "string", "description": "English hint."},
        },
        "required": ["value"],
    }
    assert source["properties"]["value"]["description"] == "한국어 field description"


def test_strict_json_schema_rejects_open_ended_maps_locally():
    with pytest.raises(ValueError, match="open-ended object"):
        strict_json_schema(OpenMapPayload)
