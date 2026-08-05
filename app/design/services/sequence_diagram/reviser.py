"""사용자 피드백을 시퀀스 다이어그램 요소 모델(진실의 원천)에 적용한다.

LLM은 PlantUML 텍스트를 만지지 않고 구조화된 시퀀스 요소(JSON)만 편집한다.
다이어그램은 그 뒤 결정론적 변환(generate_plantuml_from_sequence_json)으로 재렌더되므로,
요소 모델과 PlantUML이 어긋나지 않으며 문법 오류도 방지된다.
"""
from __future__ import annotations

import json
from typing import Any

from app.design.services.sequence_diagram.extractor import run_sequence_parse


SEQUENCE_REVISION_SYSTEM_PROMPT = """
You edit an existing UML Sequence Diagram elements model expressed in JSON format.
You are given:
1. The [Use Case Specification]
2. The [Class Diagram Information]
3. The [Current Sequence Diagram Elements] (JSON)
4. The [User Feedback]

Apply the feedback to the model and return the FULL revised model following the same schema.

Rules:
- Change only what the feedback asks for; keep everything else intact.
- Keep the sequence diagram grounded in the Use Case Specification and Class Diagram.
- Receiver Ownership: Called methods on messages MUST exist within the Receiver's class definition in the Class Diagram.
- Return messages (`return_message`) use dashed return arrows with return values, not method calls.
- Self-messages (`self_message`) are used for internal component calls.
- Preserved combined fragments (`alt`, `opt`, `loop`).

Return the revised model strictly according to the provided schema. Do not include markdown code fences or prose outside the schema fields.
"""


def revise_sequence_elements(
    current_elements: dict[str, Any],
    feedback: str,
    scenario_text: str = "",
    class_diagram_puml: str = "",
) -> dict[str, Any]:
    """현재 시퀀스 요소 + 피드백 → 수정된 시퀀스 요소(구조화). 피드백이 없으면 원본 반환."""
    if not current_elements or not feedback:
        return current_elements or {}

    user_content = (
        "[Use Case Specification]\n"
        f"{scenario_text}\n\n"
        "[Class Diagram Information]\n"
        f"{class_diagram_puml}\n\n"
        "[Current Sequence Diagram Elements]\n"
        f"{json.dumps(current_elements, ensure_ascii=False, indent=2)}\n\n"
        "[User Feedback]\n"
        f"{feedback}"
    )
    messages = [
        {"role": "system", "content": SEQUENCE_REVISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return run_sequence_parse(messages)
