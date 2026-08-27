"""사용자 피드백을 시퀀스 상호작용 모델(진실의 원천)에 적용한다.

클래스 다이어그램의 리바이저와 같다: LLM은 PlantUML 텍스트를 만지지 않고 구조화된
모델만 편집하고, 다이어그램은 그 뒤 결정론적 변환으로 재렌더된다. 그래서 모델과
PlantUML이 절대 어긋나지 않고 문법 오류도 구성에 의해 방지된다.
"""
from __future__ import annotations

from typing import Any

from app.design.services.common.structured import revision_messages
from app.design.services.sequence_diagram.extractor import (
    SequenceDiagramCollection,
    SequenceModel,
    normalize_sequence_contracts,
    parse_sequence_structured,
)


def _targeted_revision_model(
    current_model: dict[str, Any], targets: set[str] | None
) -> dict[str, Any]:
    """Return the smallest collection fragment an automatic repair can edit.

    ``check_node`` identifies a single affected use case before asking the LLM
    for a repair. Sending the complete collection nevertheless repeated every
    other diagram (and every pending proposal) in each repair request. Large
    applications therefore spent most of their context budget on diagrams that
    deterministic ``merge_model`` would discard anyway.

    A collection fragment is schema-valid and sufficient because the caller
    merges only the selected diagrams back into the original collection.
    Free-form user feedback has no deterministic target, so it deliberately
    continues to receive the complete model.
    """
    diagrams = current_model.get("Diagrams")
    if not targets or not isinstance(diagrams, list):
        return current_model
    return {
        "Diagrams": [
            diagram
            for diagram in diagrams
            if isinstance(diagram, dict)
            and str(diagram.get("use_case_id") or "").strip() in targets
        ],
        "class_diagram_hash": str(current_model.get("class_diagram_hash") or ""),
        # Pending proposals are workflow state, not input to a diagram repair.
        # ``merge_model`` retains the original proposals after the targeted edit.
        "MethodProposals": [],
    }

SEQUENCE_REVISION_SYSTEM_PROMPT = """
You edit an existing UML sequence interaction model. You are given the current
model (as JSON), the use-case specification and class diagram it was derived from,
and the user's natural-language feedback.

Apply the feedback to the model and return the FULL revised model, following the
same schema. Rules:
- When the model contains `Diagrams`, preserve exactly one supplied diagram for
  every use case and preserve `class_diagram_hash` exactly. Edit each diagram
  independently and never move messages between use cases. When the feedback
  says `[Scoped automatic validation repair]`, omitted use cases are outside the
  supplied fragment: return only the supplied diagrams and never recreate them.
- Preserve every `MethodProposals` entry unchanged. They are pending explicit
  user approval for class-diagram additions and are not sequence messages for
  this reviser to accept, remove, or rewrite.
- Change only what the feedback asks for; leave everything else intact. Repairs
  must advance the validation order: participants/BCE, receiver methods,
  call/return contracts, argument flow, then scenario flow. A later-phase finding
  may become visible after an earlier defect is fixed; repair it in the next
  iteration instead of undoing the earlier fix. Prefer the smallest coherent
  repair; it is valid to leave unrelated reported defects byte-for-byte unchanged.
- Infer the repair scope from the reported rule ids. If they are only flow-order,
  fragment, causal-chain, coverage, or orphan-participant findings, only reorder
  existing messages, correct fragment metadata, add grounded coverage messages,
  or remove inactive participants. Do not change otherwise valid call labels,
  endpoints, call links, returns, or argument bindings. If they are only method,
  return, or argument-contract findings, do not reorder scenario steps.
- Keep the model grounded in the specification and class diagram — do not invent
  participants or messages that the feedback and inputs do not support.
- For sync, async, and self calls, the label MUST name a method that already
  exists on the receiver class and match its complete call signature, including
  parameter declarations. Repair an invalid message by remapping or removing it;
  NEVER invent a descriptive label.
- Format a call label as `methodName(...)`, using an ASCII identifier for the
  method name and always including the parentheses. Never use a step or sequence
  number as a label; flow ordering belongs in `step_ids`.
- Preserve or repair unique `call_id` values on calls and exact `reply_to` values
  on returns. Never infer a return association from participant direction alone.
- Every return message must have a non-empty result label equal to the return type
  declared on its corresponding receiver-class method. Remove a return for a void
  method; never invent a narrative result label. Each call can have at most one
  corresponding return; remove duplicate or hallucinated returns.
- Every sync or self call whose receiver method declares a non-void return type
  must have exactly one corresponding return. Do not delete the call merely to
  avoid supplying its grounded return.
- Async calls are fire-and-forget and cannot have return messages. If the caller
  consumes a result, change the grounded call to sync; otherwise remove the return.
- Remove every `activate` and `deactivate` lifecycle event. The shared sequence
  template deliberately has no activation rectangles; express meaningful work
  with grounded sync/self calls and returns instead.
- Every message's source and target must exist among the returned Participants.
- Preserve unique participant aliases and use aliases for every message endpoint.
- Preserve the BCE communication rules (Actor->Boundary, Boundary<->Control,
  Control->Entity/Database). Never call directly between distinct Boundaries,
  never call Actor->Control/Entity/Database or Boundary->Entity/Database, and do
  not let Entity or Database participants initiate application-layer calls.
- A Boundary that is the exact target of a declared Control -> Boundary class
  dependency is an external integration gateway. A Control may call that
  gateway's declared operation even when its name is not a display/output verb.
  Conversely, repair a Boundary -> external-Boundary call by routing the same
  grounded operation through the already selected Control, and move that
  Control's return to its actor-facing Boundary after the gateway result.
- Actor->Boundary calls are input/events. Never repair actor coverage with an
  output method whose name begins display, show, render, prompt, or notify.
  For a `sequence.boundary-operation-direction` finding, inspect the current
  class diagram for a grounded input/event method on the receiver Boundary that
  semantically matches the referenced use-case step (including a method newly
  added by class reconciliation), and replace the output call with that exact
  complete signature. Preserve the step_id and message position.
- Preserve a causal call chain: before a non-actor participant initiates a call,
  it must already have been reached by an earlier call.
- Preserve each message's outer-to-inner `fragments` path. Use the same fragment
  id for alt branches, `branch="main"` for the first branch and `branch="else"`
  for the alternative so the renderer produces one alt/else block.
- Every alt must contain at least two mutually exclusive branches. The first
  branch is `main`; additional branches use stable names such as `else`,
  `conflict`, or `validation_error`. An extension represented only by its
  conditional handling messages is an opt, not a one-sided alt. Opt and loop
  never have an else branch.
- Preserve main-scenario step order and keep each extension immediately after
  the exact `branch_step` declared in the use-case specification. The numeric
  prefix in an extension label (for example `3a`) is only a label and MUST NOT
  override an explicit `branch_step` (which may be a different step). For a
  `sequence.flow-order` repair, move the complete extension interaction block
  after the last message of that main step and before the first later main-step
  message; never append it to the end of the diagram. Do not turn an
  unresolved/TODO/TBD/question step into invented behavior.
- Keep each return in the local interaction of the call named by `reply_to`.
  Do not move a return behind a later independent main-scenario call; complete
  the call and its conditional outcome before advancing the scenario.
- An extension represents the outcome of its branch step. Do not repeat that
  anchor's identical source, target, and method call just to show a failure.
  Keep the anchor call on the main path and use a grounded output operation,
  narrative step, or unresolved step for the exception. A true retry must use
  an explicit loop fragment.
- Preserve every `UnresolvedSteps` entry unless you add a grounded message for
  that exact `step_id`. An unresolved entry is a visible review result, not an
  invitation to omit the use case or silently delete its flow step.
- Keep `arguments` exactly aligned with the receiver method parameters. A
  call_result source_ref must name a preceding call_id with a compatible return
  type that returned to the source of the consuming call. Never let one participant
  consume a value returned to another participant without an explicit transfer;
  use input, state, or literal only when that source is grounded in the inputs.
- Remove participants that send and receive no messages, except the actor and
  Boundary retained solely to display an `UnresolvedSteps` review note. For every resolved step
  whose subject is the PrimaryActor or user, preserve at least one actor-originated
  call into a Boundary; do not claim coverage using only an unrelated system call
  or by reusing an earlier Boundary operation for a different main actor action.
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
    class_diagram_puml: str = "",
) -> dict[str, Any]:
    """현재 모델 + 피드백 → 수정된 모델. 피드백이 없으면 원본을 그대로 둔다."""
    if not current_model or not feedback:
        return current_model or {}

    revision_model = _targeted_revision_model(current_model, targets)
    if revision_model is not current_model:
        feedback += (
            "\n\n[Scoped automatic validation repair]\n"
            "Only the listed affected use-case diagrams are included in the current "
            "model. Other diagrams and pending method proposals are intentionally "
            "omitted and will be preserved by deterministic merge. Return exactly "
            "the supplied diagram fragment; do not recreate omitted use cases."
        )

    schema = (
        SequenceDiagramCollection
        if isinstance(revision_model.get("Diagrams"), list)
        else SequenceModel
    )
    revised = parse_sequence_structured(
        revision_messages(
            SEQUENCE_REVISION_SYSTEM_PROMPT,
            "Use Case Specification and Class Diagram",
            context_text,
            "Current Sequence Interaction Model",
            revision_model,
            feedback,
            targets,
        ),
        schema,
    )
    return (
        normalize_sequence_contracts(revised, class_diagram_puml)
        if class_diagram_puml
        else revised
    )
