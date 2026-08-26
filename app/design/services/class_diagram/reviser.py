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

from app.design.services.class_diagram.behavior import enrich_bce_behavior
from app.design.services.class_diagram.extractor import (
    BCE_CLASS_EXTRACTION_SYSTEM_PROMPT,
    run_domain_structure_parse,
)
from app.design.services.common.structured import focus_note

#: 수정 프롬프트. 규범은 추출과 같은 지식베이스에서, 편집하는 법만 여기 산문에서 온다.
BCE_REVISION_SYSTEM_PROMPT = BCE_CLASS_EXTRACTION_SYSTEM_PROMPT + """

Revise only the BCE domain structure in response to the supplied feedback.
Return the full Classes, referenced DataTypes, reusable typed operation
signatures, and Entity structural relationships.  Keep unaffected structure
unchanged and ground every change in the use-case specification.

Do not return methods, behavioral Dependency relationships, Collaborations,
calls, actor-entry flags, input bindings, call ids, or argument sources.
Execution collaborations are regenerated deterministically after this revision;
feedback never edits an old call tree directly.  Return only the supplied
schema.
""".strip()


def revise_bce_classes(
    current_bce: dict[str, Any],
    feedback: str,
    scenario_text: str = "",
    targets: set[str] | None = None,
) -> dict[str, Any]:
    """현재 BCE + 피드백 → 수정된 BCE(구조화). 피드백이 없으면 원본을 그대로 둔다."""
    if not current_bce or not feedback:
        return current_bce or {}

    structural_current = {
        **{
            key: value for key, value in current_bce.items()
            if key != "Collaborations"
        },
        "Relationships": [
            item for item in current_bce.get("Relationships") or []
            if isinstance(item, dict) and str(item.get("type") or "") != "Dependency"
        ],
    }
    user_content = (
        "[Use Case Specification]\n"
        f"{scenario_text}\n\n"
        "[Current BCE Class Model]\n"
        f"{json.dumps(structural_current, ensure_ascii=False, indent=2)}\n\n"
        "[User Feedback]\n"
        f"{feedback}" + focus_note(targets)
    )
    messages = [
        {"role": "system", "content": BCE_REVISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    revised = run_domain_structure_parse(messages, operation="ClassStructureRevision")
    revised["Collaborations"] = []
    stereotypes = {
        str(item.get("className") or ""): str(item.get("stereotype") or "").casefold()
        for item in revised.get("Classes") or [] if isinstance(item, dict)
    }
    revised["Relationships"] = [
        item for item in revised.get("Relationships") or []
        if isinstance(item, dict)
        and str(item.get("type") or "") != "Dependency"
        and stereotypes.get(str(item.get("source") or "")) == "entity"
        and stereotypes.get(str(item.get("target") or "")) == "entity"
    ]
    try:
        scenario = json.loads(scenario_text) if scenario_text else {}
    except json.JSONDecodeError:
        scenario = {}
    if isinstance(scenario, dict) and scenario.get("use_case_specs"):
        return enrich_bce_behavior(scenario, revised)
    return revised
