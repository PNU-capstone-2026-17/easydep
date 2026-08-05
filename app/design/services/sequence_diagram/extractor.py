"""유스케이스 명세와 클래스 다이어그램에서 시퀀스 상호작용 모델을 도출한다.

클래스 다이어그램의 BCE 추출과 같은 모양이다: LLM은 PlantUML을 쓰지 않고 구조화된
상호작용 모델만 내놓는다. 다이어그램은 plantuml.generate_sequence_from_model이
결정론적으로 렌더하므로 문법 오류가 구성에 의해 방지된다.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.design.services.common.structured import parse_structured


class SequenceParticipant(BaseModel):
    name: str = Field(default="Unknown")
    #: actor | boundary | control | entity | database — BCE 스테레오타입을 그대로 잇는다.
    kind: str = Field(default="participant")
    description: str = Field(default="")
    #: 이 참가자에 해당하는 클래스 다이어그램의 클래스 이름. 액터는 비운다.
    source_class: str = Field(default="")


class SequenceMessage(BaseModel):
    source: str
    target: str
    label: str = Field(default="")
    #: sync | async | return — PlantUML 화살표 모양을 정한다.
    type: str = Field(default="sync")
    #: 이 메시지가 속한 조각(alt/loop/opt). 비어 있으면 주 흐름.
    group: str = Field(default="")
    #: 조각의 조건문("재고가 없으면" 등). group이 있을 때만 의미가 있다.
    condition: str = Field(default="")
    #: 이 메시지를 낳은 유스케이스 id.
    use_case_ids: list[str] = Field(default_factory=list)


class SequenceModel(BaseModel):
    Participants: list[SequenceParticipant] = Field(default_factory=list)
    Messages: list[SequenceMessage] = Field(default_factory=list)


SEQUENCE_EXTRACTION_SYSTEM_PROMPT = """
You are a software architect deriving a UML 2.0 sequence diagram model from a
use-case specification and the analysis-level class diagram already derived from it.

## Input
A use-case specification (UseCaseName, PrimaryActor, MainSuccessScenario,
Extensions, ...) and a class diagram in PlantUML using the Boundary-Control-Entity
(BCE) stereotypes. Ignore absent fields. Do not invent participants or messages
that the inputs do not support.

## Participants
- Derive participants from the class diagram, not from imagination. Every
  participant name must be a class that appears in the class diagram, except the
  PrimaryActor and other actors, which come from the specification.
- Set `kind` to one of: actor, boundary, control, entity, database.
  Match the class's BCE stereotype; use "actor" for the specification's actors.
- Order matters: list them left to right as the interaction reads —
  actor first, then boundary, then control, then entity.

## Messages
- Walk MainSuccessScenario step by step. Each step becomes one or more messages.
- `source` and `target` must both be participant names you listed.
- Respect the BCE communication rules: Actor->Boundary, Boundary<->Control,
  Control<->Entity. Never Actor->Control, Boundary->Entity, or Entity-initiated calls.
- `type`: "sync" for a call, "return" for a reply carrying a result, "async" for
  fire-and-forget (notifications, events).
- `label` is the operation being invoked, named as verbNoun() where it maps to a
  class method in the class diagram; otherwise a short verb phrase.
- Emit a return message only where the caller genuinely uses the result.

## Fragments (alt / loop / opt)
- Each Extensions branch becomes messages with `group` = "alt" and `condition`
  set to that branch's trigger.
- A step that repeats over a collection becomes `group` = "loop" with `condition`
  describing the iteration.
- A step that only sometimes happens becomes `group` = "opt".
- Messages with the same `group` AND the same `condition` are rendered as one
  fragment, so keep the condition text identical across a fragment's messages.
- Leave both fields empty for main-flow messages.

## Traceability
- `source_class` on each participant: the class diagram class it stands for.
  Copy the class name exactly. Leave it empty for actors — they are not classes.
- `use_case_ids` on each message: the id(s) of the use case whose step it came
  from, copied exactly from the specification (e.g. "UC1").
- **Never invent a name or an id.** An empty list is honest; a made-up
  reference is a lie the trace matrix will believe.

## Self-check before finalizing
(a) every message's source and target exist among Participants,
(b) no message violates the BCE communication rules,
(c) every MainSuccessScenario step is represented by at least one message,
(d) participants are ordered actor -> boundary -> control -> entity,
(e) every `source_class` names a class in the given class diagram, and every
    `use_case_ids` entry appears in the given specification.

Populate the response strictly according to the provided schema. Do not include
markdown, code fences, or any prose outside the schema fields.
"""


def extract_sequence_model(
    scenario_text: str,
    class_diagram_puml: str,
) -> dict[str, Any]:
    """유스케이스 명세 + 클래스 다이어그램 → 구조화된 시퀀스 상호작용 모델."""
    if not scenario_text:
        return {}

    messages = [
        {"role": "system", "content": SEQUENCE_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"[Use Case Specification]\n{scenario_text}\n\n"
                f"[Class Diagram PlantUML]\n{class_diagram_puml}"
            ),
        },
    ]
    return parse_structured(messages, SequenceModel)
