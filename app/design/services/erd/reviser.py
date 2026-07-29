"""사용자 피드백을 ERD의 BCE 엔티티 모델(진실의 원천)에 적용한다.

클래스 다이어그램 reviser와 같은 원리다: LLM은 PlantUML 텍스트를 만지지 않고
구조화된 BCE만 편집하고, ERD는 그 뒤 결정론적 변환(plantuml.generate_erd_from_bce_json)
으로 재렌더된다. 그래서 모델과 다이어그램이 어긋나지 않고 문법 오류도 방지된다.

ERD는 클래스 다이어그램의 BCE와 독립된 자기 사본(erd_bce_classes)을 편집하므로,
ERD 피드백이 클래스 다이어그램을 바꾸지 않는다.
"""
from __future__ import annotations

import json
from typing import Any

from app.design.services.class_diagram.extractor import run_bce_parse
from app.design.services.common.structured import focus_note


ERD_BCE_REVISION_SYSTEM_PROMPT = """
You edit the entity model that an ERD is derived from. It is expressed in the
Boundary-Control-Entity (BCE) pattern, but only <<Entity>> classes and the
relationships between them become tables and foreign keys. You are given the
current BCE model (as JSON), the use-case specification it was derived from, and
the user's natural-language feedback about the ERD.

Apply the feedback to the model and return the FULL revised model, following the
same schema. Rules:
- Change only what the feedback asks for; leave everything else intact.
- ERD feedback usually concerns entities, their fields (which become columns),
  and entity-to-entity relationships (which become foreign keys). Add, remove,
  or rename these to satisfy the feedback.
- Keep the model grounded in the use-case specification — do not invent entities,
  fields, or relationships that the feedback and spec do not support.
- Preserve any <<Boundary>> and <<Control>> classes unchanged; they are not part
  of the ERD but must survive so the shared model stays complete.
- Every relationship's source and target must exist among the returned classes.
- Keep the traceability fields (use_case_ids) accurate. Carry them over unchanged for
  elements you did not touch; update them for elements you changed; fill them
  in for elements you added. Never invent a reference — an empty list is
  honest, a made-up one is a lie the trace matrix will believe.
Return the revised model strictly according to the provided schema. Do not
include markdown, code fences, or any prose outside the schema fields.
"""


def revise_erd_classes(
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
        "[Current Entity Model (BCE)]\n"
        f"{json.dumps(current_bce, ensure_ascii=False, indent=2)}\n\n"
        "[User Feedback on the ERD]\n"
        f"{feedback}" + focus_note(targets)
    )
    messages = [
        {"role": "system", "content": ERD_BCE_REVISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return run_bce_parse(messages)
