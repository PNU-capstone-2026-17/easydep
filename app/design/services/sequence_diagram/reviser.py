"""사용자 피드백을 시퀀스 상호작용 모델(진실의 원천)에 적용한다.

클래스 다이어그램의 리바이저와 같다: LLM은 PlantUML 텍스트를 만지지 않고 구조화된
모델만 편집하고, 다이어그램은 그 뒤 결정론적 변환으로 재렌더된다. 그래서 모델과
PlantUML이 절대 어긋나지 않고 문법 오류도 구성에 의해 방지된다.
"""
from __future__ import annotations

from typing import Any

from app.design.services.common.structured import parse_structured, revision_messages
from app.design.services.sequence_diagram.extractor import (
    SequenceDiagramCollection,
    SequenceModel,
)

SEQUENCE_REVISION_SYSTEM_PROMPT = """
You edit an existing UML sequence interaction model. You are given the current
model (as JSON), the use-case specification and class diagram it was derived from,
and the user's natural-language feedback.

Apply the feedback to the model and return the FULL revised model, following the
same schema. Rules:
- When the model contains `Diagrams`, preserve exactly one diagram for every
  use case. Edit each diagram independently and never move messages between use cases.
- Change only what the feedback asks for; leave everything else intact.
- Keep the model grounded in the specification and class diagram — do not invent
  participants or messages that the feedback and inputs do not support.
- For sync, async, and self calls, the label MUST name a method that already
  exists on the receiver class and match its complete call signature, including
  parameter declarations. Repair an invalid message by remapping or removing it;
  NEVER invent a descriptive label.
- Format a call label as `methodName(...)`, using an ASCII identifier for the
  method name and always including the parentheses. Never use a step or sequence
  number as a label; flow ordering belongs in `step_ids`.
- Every return message must have a non-empty result label equal to the return type
  declared on its corresponding receiver-class method. Remove a return for a void
  method; never invent a narrative result label. Each call can have at most one
  corresponding return; remove duplicate or hallucinated returns.
- Every sync or self call whose receiver method declares a non-void return type
  must have exactly one corresponding return. Do not delete the call merely to
  avoid supplying its grounded return.
- Async calls are fire-and-forget and cannot have return messages. If the caller
  consumes a result, change the grounded call to sync; otherwise remove the return.
- Every message's source and target must exist among the returned Participants.
- Preserve unique participant aliases and use aliases for every message endpoint.
- Preserve the BCE communication rules (Actor->Boundary, Boundary<->Control,
  Control<->Entity; never Actor->Control, Boundary->Entity, or Entity-initiated calls).
- Preserve a causal call chain: before a non-actor participant initiates a call,
  it must already have been reached by an earlier call.
- Preserve each message's outer-to-inner `fragments` path. Use the same fragment
  id for alt branches, `branch="main"` for the first branch and `branch="else"`
  for the alternative so the renderer produces one alt/else block.
- Every alt must contain both main and else branches. Use opt for a single
  conditional branch; opt and loop never have an else branch.
- Keep the traceability fields (source_class / use_case_ids / step_ids) accurate. Carry them over unchanged for
  elements you did not touch; update them for elements you changed; fill them
  in for elements you added. Never invent a reference — an empty list is
  honest, a made-up one is a lie the trace matrix will believe.
Return the revised model strictly according to the provided schema. Do not include
markdown, code fences, or any prose outside the schema fields.
"""


def revise_sequence_model(
    current_model: dict[str, Any],
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
) -> dict[str, Any]:
    """현재 모델 + 피드백 → 수정된 모델. 피드백이 없으면 원본을 그대로 둔다."""
    if not current_model or not feedback:
        return current_model or {}

    schema = (
        SequenceDiagramCollection
        if isinstance(current_model.get("Diagrams"), list)
        else SequenceModel
    )
    return parse_structured(
        revision_messages(
            SEQUENCE_REVISION_SYSTEM_PROMPT,
            "Use Case Specification and Class Diagram",
            context_text,
            "Current Sequence Interaction Model",
            current_model,
            feedback,
            targets,
        ),
        schema,
    )
