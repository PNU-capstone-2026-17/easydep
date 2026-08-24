"""사용자 피드백을 BCE 클래스 모델(진실의 원천)에 적용한다.

LLM은 PlantUML 텍스트를 만지지 않고 구조화된 BCE만 편집한다. 다이어그램은 그 뒤
결정론적 변환(plantuml.generate_plantuml_from_bce_json)으로 재렌더되므로, BCE와
PlantUML이 절대 어긋나지 않고 문법 오류도 구성에 의해 방지된다.

**부르는 곳이 둘이다.** 사용자 피드백 게이트가 하나이고, 규칙 위반을 고치는 재생성
루프(`nodes/artifact.py`의 `check_node`)가 다른 하나다. 후자에게는 이 프롬프트가 규칙을
제대로 실어 주는 것이 특히 중요하다 — 지적받은 위반 하나를 고치면서 다른 규칙을 새로
어기면 위반 수가 안 줄고, 그러면 수정본이 통째로 버려진다(`no_improvement`).

그래서 규범 문장은 추출 프롬프트와 **같은 레코드**에서 온다(`knowledge/rules.py`).
예전에는 여기 산문으로 따로 적혀 있었고, 그 목록은 이미 추출 쪽과 미묘하게 달랐다 —
"Actor<->Boundary"처럼 우리 스키마에 액터가 없어서 판정할 수도 없는 줄이 섞여 있었다.
"""
from __future__ import annotations

import json
from typing import Any

from app.design.knowledge import rules
from app.design.services.class_diagram.extractor import rules_section, run_bce_parse
from app.design.services.common.structured import focus_note


_REVISION_PREAMBLE = """
You edit an existing analysis-level class model expressed in the
Boundary-Control-Entity (BCE) pattern. You are given the current BCE model
(as JSON), the use-case specification it was derived from, and the user's
natural-language feedback.

Apply the feedback to the model and return the FULL revised model, following
the same schema. How to edit:
- Change only what the feedback asks for; leave everything else intact.
- Keep the model grounded in the use-case specification — do not invent classes,
  fields, methods, or relationships that the feedback and spec do not support.
- Write methods as `methodName()` or `methodName(parameterName : Type, ...)`; `...`
  itself is not a parameter. Add a named, typed parameter for each value the
  specification says a caller submits, selects, searches by, identifies, or supplies
  to the receiver. Do not leave such a method parameterless, and do not invent
  parameters for Entity state the receiver already has. If a caller uses a method's
  result, declare its return type as `methodName(...): ReturnType`; sequence return
  labels are validated against this declaration. Use `: void` for commands with no
  result. Control operations that query, validate, authenticate, authorize,
  calculate, process, create, register, select, initiate, or generate an outcome
  must explicitly say either `: ReturnType` or `: void`.
- Carry `use_case_ids` over unchanged for elements you did not touch; update them
  for elements you changed; fill them in for elements you added.
- When feedback identifies a generic Control dispatcher with an `action`,
  `operation`, `command`, or `mode` parameter, replace it with the smallest set
  of separate, requirement-grounded methods for the distinct create, update,
  delete, query, or registration actions. Do not keep the dispatcher merely by
  changing its return type: its single signature cannot express exact REST and
  sequence contracts.
- Behavioural links (through a Boundary or Control) carry no multiplicity.
  Relationships between two Entities are structural and MUST carry both
  `sourceMultiplicity` and `targetMultiplicity`, each exactly one of "1",
  "0..1", "*", "1..*" (the equivalent spellings "0..*" and "1..1" are accepted
  and read as "*" and "1"). They are what the ER diagram turns into foreign keys and
  join tables; one left empty is not mapped at all. If the model you were given
  has an Entity-to-Entity relationship without them, fill them in from the
  use-case specification rather than leaving them as you found them.
- `identifier` lists the Entity's own fields that already identify it. Leave it
  empty when the specification names no such field — a surrogate key is added
  downstream, and the empty list is what records that the key was this project's
  choice rather than the specification's.
"""

_REVISION_CLOSING = """
The rules above hold for the model you return, not just for the parts you edited.
A revision that fixes what was asked but breaks a rule elsewhere will be rejected
whole, so re-read them against your full answer before returning it.

Return the revised model strictly according to the provided schema. Do not
include markdown, code fences, or any prose outside the schema fields.
"""

#: 수정 프롬프트. 규범은 추출과 같은 지식베이스에서, 편집하는 법만 여기 산문에서 온다.
BCE_REVISION_SYSTEM_PROMPT = (
    _REVISION_PREAMBLE + rules_section(rules.CLASS_DIAGRAM) + _REVISION_CLOSING
)


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
