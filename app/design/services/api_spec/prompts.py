"""API LLM에는 HTTP 설계만 맡기고 실행 연결은 코드가 채우게 한다."""
from __future__ import annotations

import json
from typing import Any

from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.normalization import interaction_context

API_SPEC_EXTRACTION_SYSTEM_PROMPT = """
Design the HTTP surface for the supplied finite interaction candidates.
Return one endpoint for each distinct interaction the application exposes and
copy its interactionId exactly. Decide only the HTTP contract: path, method,
operationId, parameters, request schema, responses, and schemas.

- Use resource-oriented absolute paths and standard HTTP method semantics.
- Path parameters must exactly match variables in braces.
- Use request bodies only for POST, PUT, and PATCH.
- Include the successful status and failures stated by the use-case extensions.
- Use Schemas only for request bodies that are not already supplied schema sources.
- Every request or response schema used by an endpoint must contain the fields needed by
  that interaction. Schema field types are JSON primitives or another supplied schema
  name; write collections as `Type[]` so the item type is explicit. Do not return a named
  schema with an empty field list when the HTTP body carries business data.
- Do not infer Control bindings, argument sources, result names, class traces, or
  use-case traces. The application derives those from the accepted collaboration.
- Do not invent an interactionId or add an endpoint without a supplied candidate.

Return only the structured response.
""".strip()

API_SPEC_REVISION_SYSTEM_PROMPT = """
Revise only the HTTP contract requested by the feedback. Keep interactionId values
grounded in the supplied candidates and return the full compact API proposal.
Every schema used by a request or response must contain its needed fields; write
collections as `Type[]` with an explicit item type.
Do not add Control bindings, argument mappings, outcomes, or trace fields; the
application derives them from the accepted class collaboration.
""".strip()


def _schema_sources(bce_model: BCEModel) -> dict[str, Any]:
    """HTTP 스키마 판단에 필요한 Entity와 데이터 타입 선언만 줄인다."""

    return {
        "entities": [
            {"name": item.class_name, "fields": list(item.fields)}
            for item in bce_model.Classes if item.stereotype == "Entity"
        ],
        "dataTypes": [item.model_dump() for item in bce_model.DataTypes],
    }


def proposal_messages(
    scenario_text: str,
    bce_model: BCEModel,
) -> list[dict[str, str]]:
    """유스케이스와 유한 interaction 후보만 API 제안 입력으로 만든다."""

    payload = {
        "useCaseSpecification": scenario_text,
        "interactionCandidates": interaction_context(bce_model),
        "schemaSources": _schema_sources(bce_model),
    }
    return [
        {"role": "system", "content": API_SPEC_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def revision_context(scenario_text: str, bce_model: BCEModel) -> str:
    """수정에도 최초 제안과 같은 작은 입력만 제공한다."""

    return json.dumps(
        {
            "useCaseSpecification": scenario_text,
            "interactionCandidates": interaction_context(bce_model),
            "schemaSources": _schema_sources(bce_model),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
