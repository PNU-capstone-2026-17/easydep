"""사용자 피드백을 API 엔드포인트 모델(진실의 원천)에 적용한다.

LLM은 OpenAPI 문서를 만지지 않고 구조화된 엔드포인트 모델만 편집한다. 문서는 그 뒤
결정론적 조립(openapi.build_openapi_from_model)으로 재생성되므로, 모델과 명세가
어긋나지 않고 필수 필드가 빠지지 않는다.
"""
from __future__ import annotations

from typing import Any

from app.design.services.api_spec.extractor import ApiSpecModel
from app.design.services.common.structured import parse_structured, revision_messages

API_SPEC_REVISION_SYSTEM_PROMPT = """
You edit an existing REST API model. You are given the current model (as JSON),
the use-case specification, class diagram, and sequence diagram it was derived from,
and the user's natural-language feedback.

Apply the feedback to the model and return the FULL revised model, following the
same schema. Rules:
- Change only what the feedback asks for; leave everything else intact.
- Keep the model grounded in the inputs — do not invent endpoints, fields, or
  schemas that the feedback and inputs do not support.
- Every `request_schema` and every response `schema_name` must name a schema you return.
- Every brace variable in a `path` must have a matching entry in `path_params`.
- `operation_id` values must stay unique.
- Keep REST method semantics (get read, post create, put replace, patch update,
  delete remove).
- Keep the traceability fields (source_classes / source_class / use_case_ids) accurate. Carry them over unchanged for
  elements you did not touch; update them for elements you changed; fill them
  in for elements you added. Never invent a reference — an empty list is
  honest, a made-up one is a lie the trace matrix will believe.
- Keep every endpoint's `control_binding` exact: it must name an existing BCE
  Control method, map each Control argument from an explicit HTTP request source,
  and name one outcome for every documented response status. Preserve an existing
  binding unchanged unless the feedback or a reported contract issue requires it.
- If the reported issue says that no API operation exists, add the missing
  requirement-grounded endpoints. Derive them from actor-to-system use-case
  behavior and the exact BCE Control calls in the sequence diagram; do not add
  infrastructure-only or placeholder endpoints.
Return the revised model strictly according to the provided schema. Do not include
markdown, code fences, or any prose outside the schema fields.
"""


def revise_api_spec_model(
    current_model: dict[str, Any],
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
) -> dict[str, Any]:
    """현재 모델 + 피드백 → 수정된 모델. 피드백이 없으면 원본을 그대로 둔다."""
    if not current_model or not feedback:
        return current_model or {}

    return parse_structured(
        revision_messages(
            API_SPEC_REVISION_SYSTEM_PROMPT,
            "Use Case Specification, Class Diagram and Sequence Diagram",
            context_text,
            "Current API Endpoint Model",
            current_model,
            feedback,
            targets,
        ),
        ApiSpecModel,
    )
