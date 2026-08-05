"""사용자 피드백을 API 명세 요소 모델(진실의 원천)에 적용한다.

LLM은 OpenAPI JSON/YAML을 직접 만지지 않고 구조화된 API 요소(JSON)만 편집한다.
API 명세는 그 뒤 결정론적 변환(generate_openapi_spec_from_json)으로 재생성되므로,
요소 모델과 OpenAPI 명세가 어긋나지 않으며 문법 오류도 방지된다.
"""
from __future__ import annotations

import json
from typing import Any

from app.design.services.api_spec.extractor import run_api_elements_parse


API_REVISION_SYSTEM_PROMPT = """
You edit an existing OpenAPI specification elements model expressed in JSON format.
You are given:
1. The [Use Case Specification]
2. The [Class Diagram Information]
3. The [Sequence Diagram Information]
4. The [Current API Spec Elements] (JSON)
5. The [User Feedback]

Apply the feedback to the model and return the FULL revised model following the same schema.

Rules:
- Change only what the feedback asks for; keep everything else intact.
- Keep the endpoints, request/response DTO schemas, and HTTP status codes grounded in the Use Case Spec, Class Diagram, and Sequence Diagram.
- Use camelCase for property names.
- Ensure correct HTTP status code mappings and OpenAPI structure.

Return the revised model strictly according to the provided schema. Do not include markdown code fences or prose outside the schema fields.
"""


def revise_api_spec_elements(
    current_elements: dict[str, Any],
    feedback: str,
    class_diagram_puml: str = "",
    sequence_diagram_puml: str = "",
) -> dict[str, Any]:
    """현재 API 요소 + 피드백 → 수정된 API 요소(구조화). 피드백이 없으면 원본 반환."""
    if not current_elements or not feedback:
        return current_elements or {}

    user_content = (
        "[Class Diagram Information]\n"
        f"{class_diagram_puml}\n\n"
        "[Sequence Diagram Information]\n"
        f"{sequence_diagram_puml}\n\n"
        "[Current API Spec Elements]\n"
        f"{json.dumps(current_elements, ensure_ascii=False, indent=2)}\n\n"
        "[User Feedback]\n"
        f"{feedback}"
    )
    messages = [
        {"role": "system", "content": API_REVISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return run_api_elements_parse(messages)
