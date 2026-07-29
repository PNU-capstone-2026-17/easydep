"""사용자 피드백을 BCE 클래스 모델(진실의 원천)에 적용한다.

LLM은 PlantUML 텍스트를 만지지 않고 구조화된 BCE만 편집한다. 다이어그램은 그 뒤
결정론적 변환(plantuml.generate_plantuml_from_bce_json)으로 재렌더되므로, BCE와
PlantUML이 절대 어긋나지 않고 문법 오류도 구성에 의해 방지된다.
"""
from __future__ import annotations

import json
from typing import Any

from app.design.services.class_diagram.extractor import run_bce_parse
from app.design.services.common.structured import focus_note


BCE_REVISION_SYSTEM_PROMPT = """
You edit an existing analysis-level class model expressed in the
Boundary-Control-Entity (BCE) pattern. You are given the current BCE model
(as JSON), the use-case specification it was derived from, and the user's
natural-language feedback.

Apply the feedback to the model and return the FULL revised model, following
the same schema. Rules:
- Change only what the feedback asks for; leave everything else intact.
- Keep the model grounded in the use-case specification — do not invent classes,
  fields, methods, or relationships that the feedback and spec do not support.
- Preserve the BCE communication rules (Actor<->Boundary, Boundary<->Control,
  Control<->Entity; Entity never initiates toward Control/Boundary).
- Every relationship's source and target must exist among the returned classes.
- Keep the traceability fields (use_case_ids) accurate. Carry them over unchanged for
  elements you did not touch; update them for elements you changed; fill them
  in for elements you added. Never invent a reference — an empty list is
  honest, a made-up one is a lie the trace matrix will believe.
Return the revised model strictly according to the provided schema. Do not
include markdown, code fences, or any prose outside the schema fields.
"""


def revise_bce_classes(
    current_bce: dict[str, Any],
    feedback: str,
    scenario_text: str = "",
    targets: set[str] | None = None,
) -> dict[str, Any]:
    """현재 BCE + 피드백 → 수정된 BCE(구조화). 피드백이 없으면 원본을 그대로 둔다."""
    if not current_bce or not feedback:
        return current_bce or {}

    user_content = (
        "[Use Case Specification]\n"
        f"{scenario_text}\n\n"
        "[Current BCE Class Model]\n"
        f"{json.dumps(current_bce, ensure_ascii=False, indent=2)}\n\n"
        "[User Feedback]\n"
        f"{feedback}" + focus_note(targets)
    )
    messages = [
        {"role": "system", "content": BCE_REVISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return run_bce_parse(messages)
