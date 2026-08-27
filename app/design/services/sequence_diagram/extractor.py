"""유스케이스 명세와 클래스 다이어그램에서 시퀀스 상호작용 모델을 도출한다.

클래스 다이어그램의 BCE 추출과 같은 모양이다: LLM은 PlantUML을 쓰지 않고 구조화된
상호작용 모델만 내놓는다. 다이어그램은 plantuml.generate_sequence_from_model이
결정론적으로 렌더하므로 문법 오류가 구성에 의해 방지된다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.design.services.common.structured import StructuredLlmError, parse_structured
from app.design.services.sequence_diagram.methods import (
    is_complete_method_call,
    is_return_value_label,
    method_call_signature,
    method_name,
    method_return_type,
    normalize_return_type,
)


logger = logging.getLogger(__name__)


class SequenceParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    alias: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    #: actor | boundary | control | entity | database — BCE 스테레오타입을 그대로 잇는다.
    kind: Literal["actor", "boundary", "control", "entity", "database"]
    description: str
    #: 이 참가자에 해당하는 클래스 다이어그램의 클래스 이름. 액터는 비운다.
    source_class: str


class SequenceFragment(BaseModel):
    """한 메시지를 감싸는 복합 조각 경로의 한 레벨.

    같은 ``id``의 ``alt``에서 ``branch``가 ``else``로 바뀌면 PlantUML ``else``가
    생성된다. 목록을 바깥쪽부터 안쪽 순서로 적으므로 중첩 조각도 표현할 수 있다.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: Literal["alt", "opt", "loop"]
    branch: Literal["main", "else"]
    condition: str = Field(min_length=1)


class SequenceArgumentBinding(BaseModel):
    """호출 인자가 어디서 왔는지 기록하는 검증 가능한 데이터 흐름."""

    model_config = ConfigDict(extra="forbid")

    parameter: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)
    source_kind: Literal["input", "call_result", "state", "literal"]
    #: call_result이면 선행 call_id, input이면 step_id, 나머지는 설명 가능한 식별자.
    source_ref: str = Field(min_length=1)


class SequenceUnresolvedStep(BaseModel):
    """A specification step that was retained but could not be grounded.

    It is deliberately part of the persisted interaction model instead of an
    exception or an omitted message.  A missing method mapping is reviewable
    design information; dropping the step makes a partially generated diagram
    look complete.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    sentence: str = ""
    reason: str = Field(min_length=1)
    candidates: list[str] = Field(default_factory=list)


class SequenceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    label: str
    #: sync | async | return | self | activate | deactivate
    type: Literal["sync", "async", "return", "self", "activate", "deactivate"]
    #: 바깥쪽부터 안쪽 순서의 fragment 경로. 빈 목록이면 주 흐름.
    fragments: list[SequenceFragment]
    #: 이 메시지를 낳은 유스케이스 id.
    use_case_ids: list[str]
    #: ``UC1:main:2`` 또는 ``UC1:extension:3a:3a1`` 형태의 정확한 흐름 단계 참조.
    step_ids: list[str]
    #: 새 모델의 호출/반환 연결 키. 호출만 call_id, 반환만 reply_to를 채운다.
    call_id: str
    reply_to: str
    #: 호출 시그니처의 각 매개변수와 실제 값 출처. 호출 외 이벤트는 빈 목록이다.
    arguments: list[SequenceArgumentBinding]

    @field_validator("use_case_ids")
    @classmethod
    def use_case_ids_are_set_like(cls, value: list[str]) -> list[str]:
        """추적 참조는 집합 의미이므로 중복은 정보가 아니라 생성 오류다."""
        if len(value) != len(set(value)):
            raise ValueError("use_case_ids must not contain duplicates")
        return value

    @field_validator("step_ids")
    @classmethod
    def step_ids_are_set_like(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("step_ids must not contain duplicates")
        return value

    @field_validator("fragments")
    @classmethod
    def fragment_path_ids_are_unique(cls, value: list[SequenceFragment]) -> list[SequenceFragment]:
        identifiers = [fragment.id for fragment in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("fragment ids must be unique within one message path")
        return value

    @model_validator(mode="after")
    def event_shape_is_valid(self) -> "SequenceMessage":
        if self.type == "self" and self.source != self.target:
            raise ValueError("self messages require source == target")
        if self.type in {"activate", "deactivate"} and self.source != self.target:
            raise ValueError("activation events require source == target")
        if self.type in {"sync", "async", "self"}:
            label = self.label.strip()
            if not label:
                raise ValueError("call messages require a method label")
            if not is_complete_method_call(label):
                raise ValueError(
                    "call message labels must be complete method calls with a parameter list"
                )
            self.label = label
            if not self.call_id.strip() or self.reply_to.strip():
                raise ValueError("call messages require call_id and an empty reply_to")
        if self.type == "return":
            label = self.label.strip()
            # Providers occasionally omit a return label while still emitting
            # a usable reply link.  The normalizer can restore the exact type
            # from the receiver contract (or remove a void return), so do not
            # discard the whole use-case diagram for this mechanical omission.
            if label and not is_return_value_label(label):
                raise ValueError("return message labels must be return type identifiers")
            self.label = label
            if self.call_id.strip() or not self.reply_to.strip():
                raise ValueError("return messages require reply_to and an empty call_id")
        if self.type in {"activate", "deactivate"} and (
            self.call_id.strip() or self.reply_to.strip()
        ):
            raise ValueError("lifecycle events cannot carry call_id or reply_to")
        if self.type not in {"sync", "async", "self"} and self.arguments:
            raise ValueError("only call messages can carry argument bindings")
        return self


class SequenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    Participants: list[SequenceParticipant]
    Messages: list[SequenceMessage]

    @model_validator(mode="after")
    def aliases_are_unique(self) -> "SequenceModel":
        aliases = [participant.alias for participant in self.Participants]
        if len(aliases) != len(set(aliases)):
            raise ValueError("participant aliases must be unique")
        return self


class UseCaseSequenceModel(SequenceModel):
    """하나의 유스케이스만 표현하는 독립 시퀀스 다이어그램."""

    use_case_id: str = Field(min_length=1)
    use_case_name: str = ""
    #: Steps which were kept visible because no grounded receiver method could
    #: be selected.  They render as an explicit review note, never as absence.
    UnresolvedSteps: list[SequenceUnresolvedStep] = Field(default_factory=list)
    #: Conditions, outcomes, and other narrative flow steps that are explained
    #: by the surrounding interaction but do not introduce a separate receiver
    #: operation.  They retain traceability without fabricating a method call.
    NarrativeSteps: list[SequenceUnresolvedStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def messages_belong_to_this_use_case(self) -> "UseCaseSequenceModel":
        for message in self.Messages:
            if message.use_case_ids != [self.use_case_id]:
                raise ValueError(
                    "every message in a use-case sequence must reference only its use_case_id"
                )
        return self


class SequenceMethodProposal(BaseModel):
    """A user-reviewable class-method addition required by sequence repair.

    The interaction extractor must never quietly extend the class contract just
    to make a diagram look complete.  When the current contract has no suitable
    operation, reconciliation records the proposed addition here and waits for
    an explicit user approval before changing the class diagram.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    class_name: str = Field(min_length=1)
    method: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    use_case_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)


class SequenceDiagramCollection(BaseModel):
    """유스케이스별 시퀀스 다이어그램 모음."""

    model_config = ConfigDict(extra="forbid")
    Diagrams: list[UseCaseSequenceModel]
    #: 이 시퀀스가 검증된 클래스 다이어그램 버전. 추출 뒤 코드가 주입한다.
    class_diagram_hash: str = ""
    #: Class additions proposed by reconciliation but not yet approved by the
    #: user.  This is intentionally persisted with the sequence source so a
    #: page refresh cannot lose an outstanding design decision.
    MethodProposals: list[SequenceMethodProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def use_case_ids_are_unique(self) -> "SequenceDiagramCollection":
        identifiers = [diagram.use_case_id for diagram in self.Diagrams]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("sequence diagram use_case_ids must be unique")
        return self


class SequenceElementSelection(BaseModel):
    """LLM이 보완할 수 있는 최소 단위인 단계별 메서드 선택."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    receiver_class: str = Field(min_length=1)
    method: str = Field(min_length=1)


class SequenceElementSelections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[SequenceElementSelection]


class SequenceRouteSelection(BaseModel):
    """The Boundary/Control pair that owns one use-case interaction.

    A diagram with several API Boundaries is normal.  Selecting the first
    Boundary in the class model would make every later method choice look
    grounded while routing the use case through the wrong interface, so the
    semantic decision is explicitly constrained to existing dependency pairs.
    """

    model_config = ConfigDict(extra="forbid")

    boundary_class: str = Field(min_length=1)
    control_class: str = ""


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
- Include only participants that send or receive at least one message justified
  by this use case. Do not copy every class into Participants "just in case".
- Set `kind` to one of: actor, boundary, control, entity, database.
  Match the class's BCE stereotype; use "actor" for the specification's actors.
- Order matters: list them left to right as the interaction reads —
  actor first, then boundary, then control, then entity.
- `name` is the display/class name. Give every participant a unique PlantUML-safe
  `alias` and use that alias, not the display name, in message source and target.

## Flow analysis
- Fully analyze every MainSuccessScenario step and every Extensions alternate or
  exception branch. A use-case step is not automatically a receiver operation:
  conditions, outcomes, returned/displayed results, and actor decisions may be
  recorded in `NarrativeSteps` when the surrounding grounded interaction already
  explains them. Do not invent a call merely to give such a sentence a method.
- Use `UnresolvedSteps` only when a step truly requires a concrete interaction
  but no declared receiver method can represent it. Every flow step must appear
  either in a message's `step_ids`, `NarrativeSteps`, or `UnresolvedSteps`.

## Messages and receiver ownership
- `source` and `target` must both be participant aliases you listed.
- Respect the BCE communication rules: Actor->Boundary, Boundary<->Control,
  Control->Entity/Database. Never call directly between distinct Boundary objects,
  never call Actor->Control/Entity/Database or Boundary->Entity/Database, and do
  not let Entity or Database participants initiate application-layer calls.
- An external API, identity provider, or device adapter may also be modelled as
  a Boundary. A Control may call such a Boundary only when the class diagram
  declares that exact Control -> Boundary dependency; this is an outbound
  gateway call, not a UI output call. Never let an actor-facing Boundary call
  that external Boundary directly: route the call through the selected Control
  and keep the Control's return to its Boundary after the outbound call returns.
- Actor->Boundary calls represent actor input/events. An actor MUST NOT invoke
  output-oriented Boundary methods such as display*, show*, render*, prompt*, or
  notify*; those are called by a Control or another permitted system component.
- When a ``[Candidate interaction routes]`` section is supplied with the input,
  use its first Boundary/Control pair as the primary route for this use case.
  The actor entry and application work normally use that pair and its declared
  collaborators. A later pair marked ``supplementary_actor_selection`` may be
  used only for an explicit actor selection/choice/pick step when its existing
  Boundary method represents that exact step; return to the primary route for
  all other work. Do not combine routes otherwise or substitute a similarly
  named Boundary/Control from another use case.
- `type`: "sync" for a call, "return" for a reply carrying a result, "async" for
  fire-and-forget, and "self" for a call whose source and target are the same.
- Give every sync, async, and self call a unique non-empty `call_id`. Set its
  `reply_to` to "". A return sets `call_id` to "" and `reply_to` to the exact
  preceding call_id it answers.
- For every sync, async, or self call, `label` MUST be a method that already exists
  on the receiver's class in the provided class diagram. Copy its complete call
  signature including the parameter declaration, but omit visibility and return type;
  NEVER invent a method and NEVER use a descriptive phrase in its place.
- Format a call `label` as `methodName(...)`. It must start with
  an ASCII letter or underscore and contain only ASCII letters, digits, or
  underscores in the method name, and the parentheses are mandatory. Never put a
  step number or sequence number in `label`; `step_ids` carries flow ordering separately.
- Every sync or self call to a method with a non-void declared return type MUST
  have exactly one corresponding return message. Its `label` is mandatory and
  MUST exactly match the return type declared after `:` on the corresponding
  receiver-class method. Never use a narrative result label. A void method has
  no return message.
  Async calls are fire-and-forget and MUST NOT have a corresponding return; use
  sync instead when the caller consumes the declared result.
- `arguments` must contain exactly one binding per parameter declared in the
  call label, and [] for calls without parameters and for non-call events. Copy
  the parameter name and type exactly. Set `source_kind` to input, call_result,
  state, or literal. For call_result, `source_ref` is a preceding call_id whose
  declared return type equals the parameter type and whose caller is the source
  of the consuming call. A participant cannot use a result returned to another
  participant unless an explicit intervening message transfers that value. For
  input, `source_ref` is an exact step_id. Never claim a value source that the
  preceding interaction does not provide.
- Do not emit `activate` or `deactivate` events. The shared sequence template
  uses fixed lifelines without activation rectangles; show processing through
  regular sync/self calls and grounded returns instead.

## Fragments (alt / loop / opt)
- `fragments` is the outer-to-inner fragment path for a message; use [] for a
  message outside fragments. This permits nested fragments.
- Give each logical fragment a stable `id`. All branches of one fragment use the
  same id and type.
- The first branch uses `branch="main"`; an alternative branch of the same alt
  uses `branch="else"`. This is rendered as PlantUML `else`, not a second alt.
- An alt fragment MUST contain both main and else branches. Use opt, not a
  one-sided alt, when there is only one conditional branch.
- An extension shown by itself is a single conditional branch and MUST use opt.
  Use alt only when both the normal/main branch and the mutually exclusive else
  branch are represented by messages sharing the same fragment id. Repetition
  becomes loop. Do not use else for opt or loop.
- Preserve MainSuccessScenario order. Place each extension immediately after
  the main step identified by its `branch_step`, before any later main step.
- An `opt` contains only the branch-specific behavior. Never copy its anchor
  call, the normal success response, or a preceding message into the fragment
  merely to give the fragment content. A successful non-void return belongs
  after the successful path has completed, never before failure alternatives.
- Keep a return in the local interaction of the call it answers. Its `reply_to`
  call and return must both occur before the next independent main-scenario
  step; never collect returns at the end of a use case after later calls have
  already advanced the scenario.
- An extension is the outcome of its `branch_step`, not a second execution of
  the same operation. Do not repeat the anchor's identical source, target, and
  method call in an `opt` or `alt` just to express failure. A genuine retry
  must be an explicit `loop`; otherwise use a grounded output operation or a
  narrative/unresolved step for the exceptional outcome.
- When an extension explicitly says that the actor retries, re-enters, or
  re-submits the same input, reuse the already grounded Actor -> Boundary input
  operation in that `loop`. It is a repetition of the original command, not a
  new Boundary method or a semantically distinct user operation.
- If a step is explicitly unresolved (status unresolved, TODO/TBD, or a question
  asking what behavior to perform), do not invent behavior for it. Leave it for
  the validation gate to report as requiring clarification.
- If a resolved step describes an action performed by the PrimaryActor or user,
  at least one call for that step must originate from that actor and enter through
  a Boundary. A traceability id on an unrelated system call is not step coverage.
  Do not reuse a Boundary operation from an earlier, semantically different main
  actor action merely to fill a later step_id. If no existing receiver method
  represents the later action, leave it uncovered for class-method reconciliation.

## Traceability
- `source_class` on each participant: the class diagram class it stands for.
  Copy the class name exactly. Leave it empty for actors — they are not classes.
- `use_case_ids` on each message: the id(s) of the use case whose step it came
  from, copied exactly from the specification (e.g. "UC1").
- `step_ids` on each message: copy the exact flow-step reference constructed as
  `<use_case_id>:main:<step_number>` for MainSuccessScenario, or
  `<use_case_id>:extension:<extension_label>:<sub_step>` for an extension handling
  step. Activation/deactivation events inherit the step id of the call they frame.
- `use_case_ids` is a set-like reference list. Include each applicable id at
  most once; repetition adds no traceability information.
- **Never invent a name or an id.** An empty list is honest; a made-up
  reference is a lie the trace matrix will believe.

## Self-check before finalizing
(a) every message's source and target exist among Participants,
(b) no message violates the BCE communication rules,
(c) every represented main and extension handling step id is exact; use
    NarrativeSteps rather than reusing an unrelated method for a non-call step,
(d) participants are ordered actor -> boundary -> control -> entity,
(e) every `source_class` names a class in the given class diagram, and every
    `use_case_ids` and `step_ids` entry appears in the given specification,
(f) every call label already belongs to the receiver class; do not change or
    extend the class diagram to make a message valid,
(g) every non-actor message source has already been reached by an earlier call,
    so no Boundary, Control, Entity, or Database starts acting spontaneously,
(h) every non-void sync/self call has exactly one matching return, and every alt
    contains both main and else branches,
(i) call_id/reply_to links are unique and exact, every parameter has a grounded
    argument binding owned by the consuming caller, actor-led steps contain an
    actor-originated call, and main/extension steps appear in specification order.

Populate the response strictly according to the provided schema. Do not include
markdown, code fences, or any prose outside the schema fields.
"""


def parse_sequence_structured(
    messages: list[dict[str, str]],
    schema: type[BaseModel],
) -> dict[str, Any]:
    """스키마가 거부한 시퀀스 응답을 오류 근거와 함께 유계 재요청한다.

    구조화 출력 provider도 필드 간 의미 제약까지 항상 만족시키지는 않는다. 빈 return
    라벨처럼 Pydantic이 확정적으로 거부한 응답은 모델 산출물로 저장할 수 없으므로,
    동일 입력과 정확한 검증 오류를 주고 전체 모델을 다시 생성하게 한다.
    """
    from app.core.config import settings

    attempts = max(0, settings.design_max_repair_iters) + 1
    last_error: StructuredLlmError | None = None
    for attempt in range(attempts):
        retry_messages = messages
        if last_error is not None:
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "[YOUR PREVIOUS STRUCTURED OUTPUT FAILED SCHEMA VALIDATION]\n"
                        f"{str(last_error)[:6000]}\n\n"
                        "Regenerate the FULL model. Correct every listed validation "
                        "error without relaxing the sequence/class/use-case contracts."
                    ),
                },
            ]
        try:
            return parse_structured(retry_messages, schema)
        except StructuredLlmError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
    raise last_error  # pragma: no cover - attempts is always at least one


def extract_sequence_model(
    scenario_text: str,
    class_diagram_puml: str,
    route_candidates: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """유스케이스 명세 + 클래스 다이어그램 → 구조화된 시퀀스 상호작용 모델."""
    if not scenario_text:
        return {}

    route_context = ""
    if route_candidates:
        route_context = (
            "\n\n[Candidate interaction routes]\n"
            + json.dumps(route_candidates, ensure_ascii=False)
        )
    messages = [
        {"role": "system", "content": SEQUENCE_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"[Use Case Specification]\n{scenario_text}\n\n"
                f"[Class Diagram PlantUML]\n{class_diagram_puml}"
                f"{route_context}"
            ),
        },
    ]
    model = parse_sequence_structured(messages, SequenceModel)
    return normalize_sequence_contracts(model, class_diagram_puml)


def normalize_sequence_contracts(
    model: dict[str, Any], class_diagram_puml: str
) -> dict[str, Any]:
    """Repair mechanical LLM output defects without changing its chosen behavior.

    The LLM remains responsible for the semantic interaction.  This pass only
    restores facts that are already fixed by the selected class contract: a
    return answers the matching call in the reverse direction, each parameter
    has the receiver's declared type, and a one-sided alternate fragment is an
    ``opt``.  In particular, it never selects another receiver or invents a
    call to cover an unresolved use-case step.
    """
    if not isinstance(model, dict):
        return model
    classes, _ = _parse_class_catalog(class_diagram_puml)
    diagrams = model.get("Diagrams")
    targets = diagrams if isinstance(diagrams, list) else [model]
    for diagram in targets:
        if not isinstance(diagram, dict):
            continue
        messages = [item for item in diagram.get("Messages") or [] if isinstance(item, dict)]
        # A provider can emit the same call more than once while combining the
        # main path and its response.  If the trace and fragment path are
        # identical, it is not a user retry: it is one deterministic scenario
        # step duplicated in the model.  Remove only this mechanically certain
        # case; calls with different step ids or an explicit loop remain intact.
        seen_traced_calls: set[tuple[str, str, str, tuple[str, ...]]] = set()
        deduplicated: list[dict[str, Any]] = []
        for message in messages:
            if str(message.get("type") or "").lower() in {"sync", "async", "self"}:
                step_ids = tuple(
                    sorted(
                        str(step_id).strip()
                        for step_id in message.get("step_ids") or []
                        if str(step_id).strip()
                    )
                )
                fragments = message.get("fragments") or []
                key = (
                    str(message.get("source") or "").strip(),
                    str(message.get("target") or "").strip(),
                    str(message.get("label") or "").strip(),
                    step_ids,
                )
                if step_ids and not fragments and key in seen_traced_calls:
                    continue
                if step_ids and not fragments:
                    seen_traced_calls.add(key)
            deduplicated.append(message)
        messages = deduplicated
        participants = {
            _alias(str(item.get("alias") or item.get("name") or "")): item
            for item in diagram.get("Participants") or []
            if isinstance(item, dict)
        }
        participant_kinds = {
            alias: str(item.get("kind") or "").lower()
            for alias, item in participants.items()
        }
        participant_classes = {
            alias: str(item.get("source_class") or item.get("name") or "").strip()
            for alias, item in participants.items()
        }

        def declared_signature(label: str, target: str) -> str:
            """Return the exact receiver signature, or nothing when ungrounded.

            LLMs commonly omit parameter types even when they select the right
            method.  Restore that mechanical omission only for an unambiguous
            name/arity match; a placeholder such as ``self()`` has no declared
            counterpart and must never survive into the rendered diagram.
            """
            class_item = classes.get(participant_classes.get(_alias(target), ""))
            signature = method_call_signature(label)
            if class_item is None or not signature:
                return ""
            methods = class_item.get("methods", [])
            if signature in methods:
                return signature
            name = method_name(signature)
            raw_args = signature.partition("(")[2].rpartition(")")[0].strip()
            arity = 0 if not raw_args else len(raw_args.split(","))
            candidates = [
                item for item in methods
                if method_name(item) == name
                and (0 if not item.partition("(")[2].rpartition(")")[0].strip()
                     else len(item.partition("(")[2].rpartition(")")[0].split(","))) == arity
            ]
            return candidates[0] if len(candidates) == 1 else ""

        normalized_calls: list[dict[str, Any]] = []
        for message in messages:
            if str(message.get("type") or "").lower() not in {"sync", "async", "self"}:
                normalized_calls.append(message)
                continue
            signature = declared_signature(
                str(message.get("label") or ""), str(message.get("target") or "")
            )
            if not signature:
                # Keep semantic extraction honest: an invalid placeholder or
                # invented receiver method is not an interaction.  Returns to
                # it are removed below and the step is reconciled separately.
                continue
            message["label"] = signature
            normalized_calls.append(message)
        messages = normalized_calls

        def declared_return_type(call: dict[str, Any]) -> str | None:
            target = _alias(str(call.get("target") or ""))
            class_item = classes.get(participant_classes.get(target, ""))
            signature = method_call_signature(str(call.get("label") or ""))
            if class_item is None or signature not in class_item.get("methods", []):
                return None
            return class_item.get("method_returns", {}).get(signature)

        # An extension has one conditional path.  A lone ``else`` or ``alt``
        # is a rendering-model error, not evidence for an invented main path.
        fragment_branches: dict[str, set[str]] = {}
        fragment_conditions: dict[str, set[str]] = {}
        fragment_types: dict[str, set[str]] = {}
        for message in messages:
            for fragment in message.get("fragments") or []:
                if not isinstance(fragment, dict):
                    continue
                fragment_id = str(fragment.get("id") or "").strip()
                if not fragment_id:
                    continue
                fragment_conditions.setdefault(fragment_id, set()).add(
                    " ".join(str(fragment.get("condition") or "").lower().split())
                )
                fragment_types.setdefault(fragment_id, set()).add(
                    str(fragment.get("type") or "").lower()
                )
        for message in messages:
            for fragment in message.get("fragments") or []:
                if not isinstance(fragment, dict):
                    continue
                fragment_id = str(fragment.get("id") or "").strip()
                conditions = fragment_conditions.get(fragment_id, set())
                types = fragment_types.get(fragment_id, set())
                if len(conditions) > 1 and types <= {"loop", "opt"}:
                    condition = str(fragment.get("condition") or "").strip()
                    fragment["id"] = (
                        f"{fragment_id}__{_alias(condition)[:48]}"
                    )

        for message in messages:
            for fragment in message.get("fragments") or []:
                if isinstance(fragment, dict):
                    fragment_branches.setdefault(str(fragment.get("id") or ""), set()).add(
                        str(fragment.get("branch") or "")
                    )
        for message in messages:
            for fragment in message.get("fragments") or []:
                if not isinstance(fragment, dict):
                    continue
                fragment_id = str(fragment.get("id") or "")
                if fragment.get("type") == "alt" and fragment_branches.get(fragment_id) != {"main", "else"}:
                    fragment["type"] = "opt"
                    fragment["branch"] = "main"
                elif fragment.get("type") in {"opt", "loop"} and fragment.get("branch") == "else":
                    fragment["branch"] = "main"

        # PlantUML requires the first arm of an ``alt`` to be the main branch.
        # LLM output occasionally labels the first (usually failure) arm as
        # ``else`` and a later success message as ``main``.  Swapping only the
        # branch labels is mechanically safe: it preserves every participant,
        # condition, call, and step trace while restoring the already implied
        # source order.  Do not reorder messages or invent a missing branch.
        first_alt_branch: dict[str, str] = {}
        for message in messages:
            for fragment in message.get("fragments") or []:
                if not isinstance(fragment, dict) or fragment.get("type") != "alt":
                    continue
                fragment_id = str(fragment.get("id") or "").strip()
                branch = str(fragment.get("branch") or "").strip()
                if fragment_id and branch in {"main", "else"}:
                    first_alt_branch.setdefault(fragment_id, branch)
        for message in messages:
            for fragment in message.get("fragments") or []:
                if not isinstance(fragment, dict) or fragment.get("type") != "alt":
                    continue
                fragment_id = str(fragment.get("id") or "").strip()
                if first_alt_branch.get(fragment_id) != "else":
                    continue
                branch = str(fragment.get("branch") or "").strip()
                if branch == "else":
                    fragment["branch"] = "main"
                elif branch == "main":
                    fragment["branch"] = "else"

        calls: dict[str, dict[str, Any]] = {}
        call_order: list[str] = []
        call_positions: dict[str, int] = {}
        for index, message in enumerate(messages, 1):
            message_type = str(message.get("type") or "").lower()
            if message_type not in {"sync", "async", "self"}:
                continue
            call_id = f"call-{index}"
            message["call_id"] = call_id
            message["reply_to"] = ""
            calls[call_id] = message
            call_order.append(call_id)
            call_positions[call_id] = index - 1

            target = _alias(str(message.get("target") or ""))
            class_item = classes.get(participant_classes.get(target, ""))
            signature = method_call_signature(str(message.get("label") or ""))
            expected = _method_parameters(signature) if class_item and signature in class_item.get("methods", []) else {}
            first_step = next((str(value) for value in message.get("step_ids") or [] if str(value).strip()), "")
            bindings: list[dict[str, str]] = []
            for parameter, parameter_type in expected.items():
                existing = next(
                    (item for item in message.get("arguments") or []
                     if isinstance(item, dict) and str(item.get("parameter") or "") == parameter),
                    {},
                )
                source_kind = str(existing.get("source_kind") or "")
                source_ref = str(existing.get("source_ref") or "")
                source_call = calls.get(source_ref)
                if not (
                    source_kind == "call_result"
                    and source_call is not None
                    and str(source_call.get("source") or "") == str(message.get("source") or "")
                    and declared_return_type(source_call) is not None
                    and normalize_return_type(str(declared_return_type(source_call)))
                    == normalize_return_type(parameter_type)
                ):
                    # Boundary forwards actor input; internal work consumes its
                    # own state unless the model supplied a valid prior result.
                    if participant_kinds.get(_alias(str(message.get("source") or ""))) in {"actor", "boundary"} and first_step:
                        source_kind, source_ref = "input", first_step
                    else:
                        source_kind = "state"
                        source_ref = f"{message.get('source') or 'system'}:state"
                bindings.append({
                    "parameter": parameter,
                    "type": parameter_type,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                })
            message["arguments"] = bindings

        used_replies: set[str] = set()
        for message_index, message in enumerate(messages):
            if str(message.get("type") or "").lower() != "return":
                continue
            compatible_calls = [
                call_id for call_id in reversed(call_order)
                if call_id not in used_replies
                and str(calls[call_id].get("source") or "") == str(message.get("target") or "")
                and str(calls[call_id].get("target") or "") == str(message.get("source") or "")
            ]
            # A return normally answers the nearest compatible call that has
            # already occurred.  Selecting from every call in reverse order
            # could connect it to a later repeated operation, leaving a
            # reply_to reference that appears before its call after ordering.
            # Some providers still emit a reply before its call, so retain a
            # later compatible call only as a fallback for that malformed order.
            candidates = [
                call_id for call_id in compatible_calls
                if call_positions.get(call_id, -1) < message_index
            ] or compatible_calls
            if candidates:
                reply_to = candidates[0]
                message["reply_to"] = reply_to
                message["call_id"] = ""
                expected_return = declared_return_type(calls[reply_to])
                if expected_return and expected_return.lower() != "void":
                    message["label"] = expected_return
                    # A return has no independent scenario step.  Carry the
                    # linked call's trace so an extension reply cannot be
                    # sorted/validated as a stale main-path message.
                    message["step_ids"] = list(calls[reply_to].get("step_ids") or [])
                    message["use_case_ids"] = list(
                        calls[reply_to].get("use_case_ids") or []
                    )
                    used_replies.add(reply_to)
                else:
                    # A void method cannot have a return message.  Removing it
                    # is contract restoration, not a semantic choice.
                    message["_drop"] = True
            else:
                # There is no grounded call for this return.  Leave it out
                # rather than retaining a false call-return relation.
                message["_drop"] = True
        normalized_messages = [message for message in messages if not message.pop("_drop", False)]
        for call_id in call_order:
            call = calls[call_id]
            expected_return = declared_return_type(call)
            if (
                call_id not in used_replies
                and str(call.get("type") or "").lower() in {"sync", "self"}
                and expected_return
                and expected_return.lower() != "void"
            ):
                normalized_messages.append(_return_message(call, expected_return))
        ordered_messages = normalize_sequence_entry_order(
            normalize_sequence_message_order(normalized_messages), participant_kinds
        )
        diagram["Messages"] = normalize_sequence_return_order(ordered_messages)
    return normalize_sequence_participants(model, class_diagram_puml)


def normalize_sequence_message_order(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place traced extensions directly after their declared main-flow anchor.

    The LLM decides which grounded messages express a scenario.  Their
    ``step_ids`` already carry the deterministic ordering contract, however;
    letting a repair append an extension after later main steps creates a
    misleading diagram without adding any semantic information.  Keep each
    interaction intact while ordering only traceable flow groups.  A return
    inherits the group of the call it answers so it cannot drift behind a later
    independent step.
    """
    call_groups: dict[str, tuple[int, int, str]] = {}
    call_positions: dict[str, int] = {}

    def explicit_group(message: dict[str, Any]) -> tuple[int, int, str] | None:
        for step_id in message.get("step_ids") or []:
            value = str(step_id or "").strip()
            main = re.fullmatch(r"[^:]+:main:(\d+)", value)
            if main:
                return int(main.group(1)), 0, ""
            extension = re.fullmatch(r"[^:]+:extension:([^:]+):[^:]+", value)
            if extension:
                label = extension.group(1)
                match = re.match(r"(\d+)", label)
                # Global alternatives have no numeric anchor and remain after
                # the explicitly ordered scenario steps.
                return (int(match.group(1)), 1, label) if match else (10**9, 1, label)
        return None

    groups: list[tuple[int, int, str] | None] = []
    for message in messages:
        group = explicit_group(message)
        groups.append(group)
        if str(message.get("type") or "").lower() in {"sync", "async", "self"}:
            call_id = str(message.get("call_id") or "").strip()
            if call_id and group is not None:
                call_groups[call_id] = group
            if call_id:
                call_positions[call_id] = len(groups) - 1

    # Do this after every call has been indexed. A provider may place a return
    # before its call in the source array, so resolving it during the first
    # pass would miss the group's only reliable ordering evidence.
    for index, message in enumerate(messages):
        if str(message.get("type") or "").lower() == "return":
            # A reply has no independent flow step.  Providers and older
            # persisted models can attach an earlier step ID to it, but that
            # evidence describes the call it answers, not a separate action.
            # Always use the linked call's group so the reply cannot be sorted
            # ahead of that call merely because its stale step ID is earlier.
            groups[index] = call_groups.get(str(message.get("reply_to") or "").strip())

    # Untraced messages have no safe placement evidence. Preserve their local
    # position instead of guessing a flow step for them.
    if not any(group is not None for group in groups):
        return messages
    indexed = list(enumerate(messages))
    return [
        message
        for _, message in sorted(
            indexed,
            key=lambda item: (
                groups[item[0]] if groups[item[0]] is not None else (10**9, 2, ""),
                # A persisted LLM response can put a return above its call.
                # A reply has no independent scenario position, so retain its
                # call's group but never let its local order precede that call.
                max(
                    item[0],
                    call_positions.get(str(item[1].get("reply_to") or ""), item[0]),
                ) + (0.5 if str(item[1].get("type") or "").lower() == "return" else 0),
            ),
        )
    ]


def normalize_sequence_entry_order(
    messages: list[dict[str, Any]], participant_kinds: dict[str, str]
) -> list[dict[str, Any]]:
    """Put a known actor entry before an accidentally front-loaded system call.

    Step IDs order the normal scenario but a provider can omit the actor
    trigger's trace reference while still emitting a valid ``Actor → Boundary``
    message.  In that case grouping alone cannot distinguish a response from
    the actual entry and a Boundary may appear to call its Control before the
    actor reaches it.  Moving the existing entry does not select a method or
    invent an interaction; it restores the BCE causality already expressed by
    the two messages.
    """
    call_types = {"sync", "async", "self"}
    first_call_index = next(
        (
            index
            for index, message in enumerate(messages)
            if str(message.get("type") or "").lower() in call_types
        ),
        None,
    )
    if first_call_index is None:
        return messages

    def is_actor_entry(message: dict[str, Any]) -> bool:
        return (
            str(message.get("type") or "").lower() in call_types
            and participant_kinds.get(_alias(str(message.get("source") or "")), "") == "actor"
            and participant_kinds.get(_alias(str(message.get("target") or "")), "") == "boundary"
        )

    if is_actor_entry(messages[first_call_index]):
        return messages
    entry_index = next(
        (index for index, message in enumerate(messages) if is_actor_entry(message)),
        None,
    )
    if entry_index is None:
        return messages
    entry = messages[entry_index]
    return [entry, *messages[:entry_index], *messages[entry_index + 1:]]


def normalize_sequence_return_order(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Defer an early caller return until its delegated work has completed.

    Flow-step ordering gives a call and its return the same group.  That is
    normally useful, but it can put ``Boundary -> Actor`` immediately after an
    actor input even when the Boundary then delegates to a Control.  A returned
    call is still open when its receiver sends a later call, so move that outer
    return behind the delegated call's completion.  The rule uses existing
    call/return links only; it neither creates a new call nor changes a result.
    """
    call_types = {"sync", "async", "self"}
    calls: dict[str, tuple[int, dict[str, Any]]] = {}
    returns: dict[str, tuple[int, dict[str, Any]]] = {}

    for index, message in enumerate(messages):
        message_type = str(message.get("type") or "").lower()
        if message_type in call_types:
            call_id = str(message.get("call_id") or "").strip()
            if call_id:
                calls[call_id] = (index, message)
        elif message_type == "return":
            reply_to = str(message.get("reply_to") or "").strip()
            if reply_to and reply_to not in returns:
                returns[reply_to] = (index, message)

    # Each item is a delegated call that was emitted only after its caller's
    # return.  A same-direction call starts a new invocation, so it is a safe
    # boundary for the look-ahead rather than work to nest under the old call.
    late_children: dict[str, list[str]] = {}
    for call_id, (_, call) in calls.items():
        reply = returns.get(call_id)
        if reply is None:
            continue
        reply_index, _ = reply
        children: list[str] = []
        for message in messages[reply_index + 1:]:
            message_type = str(message.get("type") or "").lower()
            if message_type not in call_types:
                continue
            source = str(message.get("source") or "")
            target = str(message.get("target") or "")
            if source == str(call.get("source") or "") and target == str(call.get("target") or ""):
                break
            if source == str(call.get("target") or ""):
                child_id = str(message.get("call_id") or "").strip()
                if child_id and child_id in calls:
                    children.append(child_id)
        if children:
            late_children[call_id] = children

    def completion_key(call_id: str, seen: set[str] | None = None) -> tuple[int, int]:
        """Return the position after which a call's result may be emitted."""
        call_index, _ = calls[call_id]
        reply = returns.get(call_id)
        position = reply[0] if reply is not None else call_index
        depth = 0
        active = seen or set()
        if call_id in active:
            return position, depth
        active = {*active, call_id}
        for child_id in late_children.get(call_id, []):
            child_position, child_depth = completion_key(child_id, active)
            if child_position >= position:
                position = child_position
                depth = max(depth, child_depth + 1)
        return position, depth

    if not late_children:
        return messages

    def order_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        index, message = item
        if str(message.get("type") or "").lower() != "return":
            return index, 0, 0
        reply_to = str(message.get("reply_to") or "").strip()
        if reply_to not in calls:
            return index, 1, 0
        completion_index, depth = completion_key(reply_to)
        # A completion at the same source index belongs after the inner return
        # (or the void child call) that established it.
        return completion_index, 1, depth

    return [message for _, message in sorted(enumerate(messages), key=order_key)]


def normalize_sequence_participants(
    model: dict[str, Any], class_diagram_puml: str
) -> dict[str, Any]:
    """Align participant declarations with the messages that use them.

    LLM-produced legacy sequence models sometimes include a valid Control call
    but omit that Control from ``Participants``.  This is a representation
    mismatch, not a new interaction, so it can be repaired deterministically.
    Conversely, a participant with no messages is not part of the rendered
    interaction.  Keeping it makes an incomplete LLM fallback look like it
    contains an Entity or Control that it never actually uses.  Remove those
    inactive declarations after adding any missing valid endpoints.

    An explicitly unresolved diagram is the exception: it intentionally keeps
    its actor and Boundary as context for the rendered review note.
    """
    if not isinstance(model, dict):
        return model
    classes, _ = _parse_class_catalog(class_diagram_puml)
    diagrams = model.get("Diagrams")
    targets = diagrams if isinstance(diagrams, list) else [model]
    for diagram in targets:
        if not isinstance(diagram, dict):
            continue
        participants = diagram.setdefault("Participants", [])
        if not isinstance(participants, list):
            continue
        declared = {
            _alias(str(item.get("alias") or item.get("name") or ""))
            for item in participants
            if isinstance(item, dict)
        }
        for message in diagram.get("Messages") or []:
            if not isinstance(message, dict):
                continue
            for endpoint in (message.get("source"), message.get("target")):
                alias = _alias(str(endpoint or ""))
                item = classes.get(alias)
                if not alias or alias in declared or item is None:
                    continue
                participants.append(_participant(item))
                declared.add(alias)

        has_unresolved_steps = any(
            isinstance(item, dict)
            for item in diagram.get("UnresolvedSteps") or []
        )
        if has_unresolved_steps:
            continue
        active = {
            _alias(str(endpoint or ""))
            for message in diagram.get("Messages") or []
            if isinstance(message, dict)
            for endpoint in (message.get("source"), message.get("target"))
            if str(endpoint or "").strip()
        }
        participants[:] = [
            item
            for item in participants
            if isinstance(item, dict)
            and _alias(str(item.get("alias") or item.get("name") or "")) in active
        ]
    return model


def _raw_flow_step(text: Any, fallback: str) -> tuple[str, str]:
    value = str(text or "").strip()
    match = re.match(r"^([0-9]+[A-Za-z]?[0-9]*)\.?\s*(.*)$", value)
    return (
        (match.group(1), match.group(2).strip())
        if match
        else (fallback, value)
    )


def _normalize_raw_use_cases(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert the supported Cockburn example shape into the canonical collection.

    This adapter changes only field names and stable identifiers.  It does not invent
    behavior; unresolved prose remains unresolved and is caught by the normal detector.
    """
    summaries: list[dict[str, Any]] = []
    specifications: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        use_case_id = str(
            item.get("use_case_id")
            or item.get("id")
            or item.get("UseCaseId")
            or item.get("UseCaseID")
            or f"UC{index}"
        ).strip()
        name = str(
            item.get("name")
            or item.get("UseCaseName")
            or item.get("Name")
            or use_case_id
        ).strip()
        primary_actor = str(
            item.get("primary_actor") or item.get("PrimaryActor") or ""
        ).strip()
        main_scenario: list[dict[str, Any]] = []
        for step_index, raw_step in enumerate(item.get("MainSuccessScenario") or [], 1):
            if isinstance(raw_step, dict):
                number = raw_step.get("step", raw_step.get("step_number", step_index))
                sentence = str(
                    raw_step.get("description") or raw_step.get("sentence") or ""
                ).strip()
            else:
                number_text, sentence = _raw_flow_step(raw_step, str(step_index))
                number = int(number_text) if number_text.isdigit() else step_index
            main_scenario.append({"step_number": number, "sentence": sentence})

        extensions: list[dict[str, Any]] = []
        for extension_index, raw_extension in enumerate(item.get("Extensions") or [], 1):
            if not isinstance(raw_extension, dict):
                continue
            raw_condition = str(raw_extension.get("condition") or "").strip()
            label_match = re.match(r"^([0-9]+[A-Za-z]?|\*[A-Za-z]?)\.?\s*(.*)$", raw_condition)
            label = (
                label_match.group(1)
                if label_match
                else str(raw_extension.get("label") or f"*{extension_index}").strip()
            )
            condition = (
                label_match.group(2).strip().rstrip(":")
                if label_match
                else raw_condition.rstrip(":")
            )
            branch_match = re.match(r"^(\d+)", label)
            handling_steps: list[dict[str, str]] = []
            for action_index, action in enumerate(raw_extension.get("actions") or [], 1):
                sub_step, sentence = _raw_flow_step(action, f"{label}{action_index}")
                handling_steps.append({"sub_step": sub_step, "sentence": sentence})
            extensions.append(
                {
                    "label": label,
                    "branch_step": int(branch_match.group(1)) if branch_match else None,
                    "condition": condition,
                    "handling_steps": handling_steps,
                }
            )

        summaries.append(
            {
                "id": use_case_id,
                "name": name,
                "primary_actor": primary_actor,
            }
        )
        specifications.append(
            {
                "use_case_id": use_case_id,
                "name": name,
                "primary_actor": primary_actor,
                "preconditions": item.get("Preconditions")
                or ([item["Precondition"]] if item.get("Precondition") else []),
                "trigger": item.get("Trigger", ""),
                "main_scenario": main_scenario,
                "extensions": extensions,
                "success_guarantee": item.get("SuccessGuarantee", ""),
                "minimal_guarantee": item.get("MinimalGuarantee", ""),
            }
        )
    return {"use_cases": summaries, "use_case_specs": specifications}


def normalize_sequence_usecase_spec(usecase_spec: Any) -> dict[str, Any]:
    """Return a canonical, complete per-use-case collection or fail explicitly."""
    if not isinstance(usecase_spec, dict):
        raise ValueError(
            "sequence generation requires a structured use-case collection, not free text"
        )

    raw_items: list[dict[str, Any]] = []
    if isinstance(usecase_spec.get("UseCase"), dict):
        raw_items = [usecase_spec["UseCase"]]
    elif isinstance(usecase_spec.get("UseCases"), list):
        raw_items = [item for item in usecase_spec["UseCases"] if isinstance(item, dict)]
    elif "MainSuccessScenario" in usecase_spec:
        raw_items = [usecase_spec]
    if raw_items:
        return normalize_sequence_usecase_spec(_normalize_raw_use_cases(raw_items))

    use_cases = [
        item for item in usecase_spec.get("use_cases") or [] if isinstance(item, dict)
    ]
    specifications = [
        item
        for item in usecase_spec.get("use_case_specs") or []
        if isinstance(item, dict)
    ]
    if not specifications:
        raise ValueError(
            "sequence generation requires use_case_specs; use-case summaries alone "
            "cannot produce one complete sequence diagram per use case"
        )
    identifiers = [str(item.get("use_case_id") or "").strip() for item in specifications]
    if any(not identifier for identifier in identifiers):
        raise ValueError("every use_case_spec requires a non-empty use_case_id")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("use_case_specs contains duplicate use_case_id values")
    failed = [
        identifier
        for identifier, item in zip(identifiers, specifications)
        if item.get("generated") is False
    ]
    if failed:
        raise ValueError(
            "cannot generate sequences from use-case specifications that failed "
            f"generation: {', '.join(failed)}"
        )
    summary_ids = {
        str(item.get("id") or "").strip() for item in use_cases if item.get("id")
    }
    if summary_ids and summary_ids != set(identifiers):
        raise ValueError(
            "use_cases and use_case_specs must contain the same use-case ids: "
            f"summaries={sorted(summary_ids)}, specifications={sorted(identifiers)}"
        )
    return {**usecase_spec, "use_cases": use_cases, "use_case_specs": specifications}


_CLASS_BLOCK = re.compile(
    r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:<<\s*([^>]+?)\s*>>)?\s*\{(.*?)\}",
    re.DOTALL,
)
_DEPENDENCY = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(?:\.\.>|-->|\*--|o--)\s+"
    r"(?:\"[^\"]*\"\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_OUTPUT_METHOD_PREFIXES = (
    "display", "show", "render", "prompt", "notify", "send", "return", "respond",
)


def _parse_class_catalog(
    class_diagram_puml: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """렌더된 클래스 다이어그램에서 BCE 클래스와 실제 호출 가능한 메서드를 읽는다."""
    classes: dict[str, dict[str, Any]] = {}
    for match in _CLASS_BLOCK.finditer(class_diagram_puml or ""):
        name, stereotype, body = match.groups()
        kind = str(stereotype or "entity").strip().lower()
        if kind not in {"boundary", "control", "entity", "database"}:
            kind = "entity"
        methods: list[str] = []
        method_returns: dict[str, str | None] = {}
        for line in body.splitlines():
            signature = method_call_signature(line)
            if signature:
                methods.append(signature)
                method_returns[signature] = method_return_type(line)
        classes[name] = {
            "name": name,
            "kind": kind,
            "methods": methods,
            "method_returns": method_returns,
        }

    dependencies: dict[str, list[str]] = {}
    for source, target in _DEPENDENCY.findall(class_diagram_puml or ""):
        if source in classes and target in classes:
            dependencies.setdefault(source, []).append(target)
    return classes, dependencies


def _alias(value: str) -> str:
    alias = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not alias or not re.match(r"[A-Za-z_]", alias):
        alias = f"Actor_{alias}"
    return alias


def _method_parameters(signature: str) -> dict[str, str]:
    """Return the declared parameter contract of a normalized call signature."""
    parameters: dict[str, str] = {}
    for raw in signature.partition("(")[2].rpartition(")")[0].split(","):
        name, separator, type_name = raw.partition(":")
        if (
            separator
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip())
            and type_name.strip()
        ):
            parameters[name.strip()] = type_name.strip()
    return parameters


def _only_callable_class(
    classes: dict[str, dict[str, Any]], kind: str
) -> dict[str, Any] | None:
    """Return a class only when the structure itself makes it unambiguous.

    A use-case sentence and a method name are different semantic layers.  The
    previous lexical-overlap score treated accidental word matches as an
    architectural decision, which could select the wrong screen/controller or
    discard a valid step.  When several classes are possible, the per-use-case
    structured extractor is responsible for the semantic choice; it receives
    the full specification and the complete class-method contract.
    """
    candidates = [
        item
        for item in classes.values()
        if item["kind"] == kind and item["methods"]
    ]
    return candidates[0] if len(candidates) == 1 else None


def _route_candidates(
    classes: dict[str, dict[str, Any]],
    dependencies: dict[str, list[str]],
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Return only real Boundary -> Control routes from the BCE model.

    A Boundary without a Control is valid for a small query use case, so keep it
    as a route when it is the only Boundary.  With several Boundaries, however,
    an unlinked Boundary is not enough evidence to associate it with an
    arbitrary Control.
    """
    boundaries = sorted(
        (
            item
            for item in classes.values()
            if item["kind"] == "boundary" and item["methods"]
        ),
        key=lambda item: item["name"],
    )
    controls = sorted(
        (
            item
            for item in classes.values()
            if item["kind"] == "control" and item["methods"]
        ),
        key=lambda item: item["name"],
    )
    routes: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for boundary in boundaries:
        linked = sorted(
            (
                classes[name]
                for name in dependencies.get(boundary["name"], [])
                if name in classes
                and classes[name]["kind"] == "control"
                and classes[name]["methods"]
            ),
            key=lambda item: item["name"],
        )
        if linked:
            routes.extend((boundary, control) for control in linked)
        elif len(boundaries) == 1:
            # A one-boundary model may intentionally be boundary-only.  If it
            # has exactly one Control without an explicit dependency, preserve
            # the existing single-route behaviour rather than inventing a
            # choice from ordering.
            routes.extend(
                (boundary, control)
                for control in controls
            )
            if not controls:
                routes.append((boundary, None))
    return routes


def _select_use_case_route(
    specification: dict[str, Any],
    summary: dict[str, Any],
    classes: dict[str, dict[str, Any]],
    dependencies: dict[str, list[str]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Choose the single grounded Boundary/Control route for one use case.

    Structural uniqueness is selected locally.  When a model exposes multiple
    valid API routes we ask for one constrained semantic decision before method
    selection, instead of falling back to an unconstrained full-diagram LLM
    response or using the class declaration order.
    """
    candidates = _route_candidates(classes, dependencies)
    if not candidates:
        raise ValueError("no callable Boundary -> Control route is available")
    if len(candidates) == 1:
        return candidates[0]

    use_case_id = str(specification.get("use_case_id") or "").strip()
    actor = str(
        specification.get("primary_actor")
        or summary.get("primary_actor")
        or "User"
    ).strip()
    trigger = str(specification.get("trigger") or summary.get("trigger") or "")
    actor_steps = []
    for index, step in enumerate(specification.get("main_scenario") or []):
        if not isinstance(step, dict):
            continue
        sentence = str(step.get("sentence") or step.get("description") or "").strip()
        if _actor_requires_system_input(sentence, actor) or (
            index == 0 and _system_receives_actor_trigger(sentence, trigger, actor)
        ):
            actor_steps.append(sentence)
    payload = {
        "use_case_id": use_case_id,
        "use_case_name": str(
            specification.get("name") or summary.get("name") or use_case_id
        ).strip(),
        "primary_actor": actor,
        "trigger": trigger,
        "actor_steps": actor_steps,
        "candidates": [
            {
                "boundary_class": boundary["name"],
                "boundary_methods": _method_candidates(boundary, True),
                "control_class": control["name"] if control is not None else "",
                "control_methods": list(control.get("methods") or [])
                if control is not None
                else [],
            }
            for boundary, control in candidates
        ],
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Select the one Boundary/Control route that owns this use case. "
                "Use only an exact supplied candidate pair. Choose from the actor "
                "request and the use-case name; do not generate UML, methods, or prose."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    selected = parse_structured(messages, SequenceRouteSelection)
    choice = (
        str(selected.get("boundary_class") or "").strip(),
        str(selected.get("control_class") or "").strip(),
    )
    for boundary, control in candidates:
        if choice == (boundary["name"], control["name"] if control else ""):
            return boundary, control
    raise ValueError("semantic route selection chose a Boundary/Control pair outside the candidates")


def _method_candidates(class_item: dict[str, Any], actor_led: bool) -> list[str]:
    methods = list(class_item.get("methods") or [])
    if actor_led and class_item.get("kind") == "boundary":
        inputs = [
            method for method in methods
            if not method.lower().startswith(_OUTPUT_METHOD_PREFIXES)
        ]
        return inputs or methods
    return methods


def _pick_method(
    candidates: list[tuple[dict[str, Any], str]],
) -> tuple[dict[str, Any] | None, str]:
    """Resolve only a structurally unique method without lexical scoring.

    More than one grounded candidate needs a semantic decision.  It is handled
    by the constrained per-use-case selector below, rather than treating shared
    words between prose and identifiers as a confidence score.
    """
    if not candidates:
        raise ValueError("sequence generation requires at least one callable BCE method")
    if len(candidates) == 1:
        return candidates[0]
    return None, ""


def _flow_records(specification: dict[str, Any]) -> list[dict[str, Any]]:
    """주 흐름 직후에 해당 확장 흐름을 배치하여 검증기가 기대하는 순서를 만든다."""
    use_case_id = str(specification.get("use_case_id") or "").strip()
    by_anchor: dict[int, list[dict[str, Any]]] = {}
    trailing: list[dict[str, Any]] = []
    for extension in specification.get("extensions") or []:
        if not isinstance(extension, dict):
            continue
        anchor = extension.get("branch_step")
        if isinstance(anchor, str) and anchor.isdigit():
            anchor = int(anchor)
        elif not isinstance(anchor, int):
            label_match = re.match(r"(\d+)", str(extension.get("label") or ""))
            anchor = int(label_match.group(1)) if label_match else None
        target = by_anchor.setdefault(anchor, []) if isinstance(anchor, int) else trailing
        target.append(extension)

    records: list[dict[str, Any]] = []
    for step in specification.get("main_scenario") or []:
        if not isinstance(step, dict) or step.get("step_number") is None:
            continue
        number = int(step["step_number"])
        records.append({
            "step_id": f"{use_case_id}:main:{number}",
            "sentence": str(step.get("sentence") or step.get("description") or "").strip(),
            "fragment": None,
        })
        for extension in by_anchor.get(number, []):
            records.extend(_extension_records(use_case_id, extension))
    for extension in trailing:
        records.extend(_extension_records(use_case_id, extension))
    return records


def _extension_records(use_case_id: str, extension: dict[str, Any]) -> list[dict[str, Any]]:
    label = str(extension.get("label") or "").strip()
    condition = str(extension.get("condition") or "condition").strip().rstrip(":")
    fragment = {
        "id": f"{use_case_id}_{_alias(label)}",
        "type": "opt",
        "branch": "main",
        "condition": condition or "condition",
    }
    return [
        {
            "step_id": f"{use_case_id}:extension:{label}:{step.get('sub_step')}",
            "sentence": str(step.get("sentence") or step.get("description") or "").strip(),
            "fragment": fragment,
        }
        for step in extension.get("handling_steps") or []
        if isinstance(step, dict) and label and step.get("sub_step")
    ]


def _actor_led(sentence: str, actor_name: str) -> bool:
    lowered = sentence.lower().lstrip(" '-\"")
    system_subjects = ("system ", "system.", "the system ", "system's", "database ", "server ")
    if lowered.startswith(system_subjects):
        return False
    actor_words = [
        word
        for word in re.findall(r"[a-z0-9]+", actor_name.lower())
        if word not in {"a", "an", "the"}
    ]
    # Use the leading role word as a *bounded* alias for a multi-word actor
    # name. Requirement authors commonly write "Registrar" for the declared
    # "Registrar Staff" actor. Treating that as a system action leaves the
    # Boundary unreachable and makes every following step falsely unresolved.
    # The alias is deliberately limited to the leading word: trailing words
    # such as "staff", "user", or "administrator" are too generic.
    subjects = {actor_name.lower().strip(), "user", "the user", "actor", "the actor"}
    if len(actor_words) > 1 and len(actor_words[0]) >= 3:
        subjects.add(actor_words[0])
    return any(
        subject
        and (
            lowered == subject
            or lowered.startswith(subject + " ")
            or lowered.startswith(subject + "'")
            or lowered.startswith(subject + ",")
        )
        for subject in subjects
    )


_PASSIVE_ACTOR_OBSERVATION = re.compile(
    r"\b(?:review|reviews|reviewing|inspect|inspects|inspecting|read|reads|reading|"
    r"examine|examines|examining|view|views|viewing|confirm|confirms|confirming|"
    r"acknowledge|acknowledges|acknowledging)\b",
    re.IGNORECASE,
)
_ACTIVE_ACTOR_INPUT = re.compile(
    r"\b(?:select|selects|selecting|choose|chooses|choosing|enter|enters|entering|"
    r"submit|submits|submitting|request|requests|requesting|send|sends|sending|"
    r"provide|provides|providing|upload|uploads|uploading)\b",
    re.IGNORECASE,
)


def _actor_requires_system_input(sentence: str, actor_name: str) -> bool:
    """Whether an actor-led step needs a new Boundary operation.

    A use-case often ends with the actor reviewing or confirming data that the
    preceding system response already displayed.  Treating that passive
    observation as a fresh system input invents a BCE method solely to satisfy
    step coverage.  It remains traceable as a narrative step instead.
    """
    if not _actor_led(sentence, actor_name):
        return False
    # A sentence can combine observation and a new command (for example,
    # “reviews the list and selects a course”).  The explicit input verb wins.
    if _ACTIVE_ACTOR_INPUT.search(sentence or ""):
        return True
    return not _PASSIVE_ACTOR_OBSERVATION.search(sentence or "")


def _system_receives_actor_trigger(
    sentence: str,
    trigger: str,
    actor_name: str,
) -> bool:
    """Recognize a system-worded first step that is still an actor entry.

    Many otherwise sound use-case specifications phrase their first main step
    as "System receives the <actor>'s request".  Treating that as a spontaneous
    Control action leaves the Boundary unreached and makes every later step
    impossible to assemble.  The trigger supplies the missing actor intent, so
    this narrow normalization creates the normal Actor -> Boundary entry rather
    than inventing an application call.
    """
    normalized_sentence = re.sub(r"\s+", " ", str(sentence or "").lower()).strip()
    if not re.match(r"(?:the )?system\s+receives?\b", normalized_sentence):
        return False
    if "request" not in normalized_sentence:
        return False
    normalized_trigger = re.sub(r"[^a-z0-9]", "", str(trigger or "").lower())
    normalized_actor = re.sub(r"[^a-z0-9]", "", str(actor_name or "").lower())
    return bool(
        normalized_actor
        and normalized_actor in normalized_trigger
        and any(token in normalized_trigger for token in ("request", "submit", "initiat", "start"))
    )


def _select_uncertain_group(plans: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """의미 판단이 필요한 단계의 메서드를 한 번의 작은 LLM 요청으로 선택한다.

    응답이 실패하거나 후보 밖의 값을 고르면 그 단계는 생성하지 않는다. 선택적 보완
    때문에 전체 시퀀스 생성이 실패하거나, 후보 순서가 근거 없는 호출을 만들지 않게
    하는 경계다. 단, 그 단계 자체는 ``UnresolvedSteps``로 남아 화면에서 사라지지
    않는다.
    """
    uncertain = [plan for plan in plans if plan["requires_semantic_selection"]]
    if not uncertain:
        return {}
    candidate_map = {
        plan["step_id"]: {
            (item["class_name"], item["method"])
            for item in plan["candidates"]
        }
        for plan in uncertain
    }
    payload = [
        {
            "step_id": plan["step_id"],
            "sentence": plan["sentence"],
            "candidates": plan["candidates"],
        }
        for plan in uncertain
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "Select the single best existing receiver class and method for each flow step. "
                "Use only the supplied candidates, keep every selected step_id exact, and omit a "
                "step when none of its candidates actually represents that behavior. Do not map a "
                "generic save/process method to validation, persistence, and presentation steps just "
                "because it is the only candidate. Do not generate participants, messages, UML, or prose."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        selected = parse_structured(messages, SequenceElementSelections)
    except Exception:  # optional enrichment must never prevent deterministic generation
        logger.warning(
            "optional sequence element selection failed; leaving uncertain steps uncovered",
            exc_info=True,
        )
        return {}

    accepted: dict[str, tuple[str, str]] = {}
    for item in selected.get("selections") or []:
        step_id = str(item.get("step_id") or "").strip()
        choice = (
            str(item.get("receiver_class") or "").strip(),
            method_call_signature(str(item.get("method") or "")),
        )
        if choice in candidate_map.get(step_id, set()):
            accepted[step_id] = choice
    return accepted


def _select_uncertain_elements(plans: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """유스케이스별 의미 판단이 필요한 메서드 선택을 병렬 보완한다.

    각 작업은 하나의 유스케이스만 다루므로 LLM 응답이 서로 영향을 주지 않는다.
    작업 수는 설정으로 제한하고, 실패한 작업은 해당 단계만 미확정으로 남긴다.
    단일 유스케이스에서는 기존 호출 형태를 유지해 호출 오버헤드와 호환성을 보장한다.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        if plan["requires_semantic_selection"]:
            groups.setdefault(str(plan["use_case_id"]), []).append(plan)
    if not groups:
        return {}
    if len(groups) == 1:
        return _select_uncertain_group(next(iter(groups.values())))

    from app.core.config import settings

    worker_limit = max(1, int(getattr(settings, "design_sequence_parallelism", 4)))
    accepted: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=min(worker_limit, len(groups))) as executor:
        futures = {
            executor.submit(_select_uncertain_group, group): use_case_id
            for use_case_id, group in groups.items()
        }
        for future in as_completed(futures):
            try:
                accepted.update(future.result())
            except Exception:  # noqa: BLE001 - one use case must not cancel others
                logger.warning(
                    "parallel sequence element selection failed for use case %s",
                    futures[future],
                    exc_info=True,
                )
    return accepted


def _participant(class_item: dict[str, Any]) -> dict[str, str]:
    return {
        "name": class_item["name"],
        "alias": _alias(class_item["name"]),
        "kind": class_item["kind"],
        "description": "Derived from the class diagram",
        "source_class": class_item["name"],
    }


def _return_message(call: dict[str, Any], return_type: str) -> dict[str, Any]:
    """Build the deterministic reply for a synchronous non-void call."""
    return {
        "source": call["target"],
        "target": call["source"],
        "label": return_type.strip(),
        "type": "return",
        "fragments": list(call.get("fragments") or []),
        "use_case_ids": list(call.get("use_case_ids") or []),
        "step_ids": list(call.get("step_ids") or []),
        "call_id": "",
        "reply_to": call["call_id"],
        "arguments": [],
    }

def _message(
    source: str,
    target: str,
    method: str,
    use_case_id: str,
    step_id: str,
    fragment: dict[str, str] | None,
    call_number: int,
    return_type: str | None = None,
    message_type: Literal["sync", "async", "self"] = "sync",
) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "label": method,
        # Void operations may still be synchronous calls.  Marking every void
        # method async made ordinary Control self-calls look fire-and-forget and
        # hid the normal request-processing chain in rendered diagrams.
        "type": message_type,
        "fragments": [fragment] if fragment else [],
        "use_case_ids": [use_case_id],
        "step_ids": [step_id],
        "call_id": f"{use_case_id}-call-{call_number}",
        "reply_to": "",
        # `...` has no named contract; explicit typed parameters are added by the
        # class generator and can be grounded in the originating use-case step.
        "arguments": [
            {
                "parameter": name.strip(),
                "type": type_name.strip(),
                "source_kind": "input",
                "source_ref": step_id,
            }
            for raw in method.partition("(")[2].rpartition(")")[0].split(",")
            if (name := raw.partition(":")[0]).strip()
            and (type_name := raw.partition(":")[2]).strip()
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip())
        ],
    }


def _build_sequence_plans(
    specifications: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    classes: dict[str, dict[str, Any]],
    dependencies: dict[str, list[str]],
    routes: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] | None = None,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for specification in specifications:
        use_case_id = str(specification.get("use_case_id") or specification.get("id") or "").strip()
        summary = summaries.get(use_case_id) or summaries.get(str(specification.get("id") or "")) or {}
        use_case_name = str(specification.get("name") or summary.get("name") or use_case_id)
        actor = str(
            specification.get("primary_actor")
            or summary.get("primary_actor")
            or "User"
        ).strip()
        route = (routes or {}).get(use_case_id)
        if route is None:
            # Retained for direct helper callers and one-boundary legacy
            # inputs.  Production extraction resolves the route before it
            # reaches this function.
            boundary = _only_callable_class(classes, "boundary")
            if boundary is None:
                raise ValueError("sequence generation requires an explicit use-case route")
            linked_controls = [
                classes[name]
                for name in dependencies.get(boundary["name"], [])
                if name in classes
                and classes[name]["kind"] == "control"
                and classes[name]["methods"]
            ]
            control = linked_controls[0] if len(linked_controls) == 1 else _only_callable_class(classes, "control")
        else:
            boundary, control = route

        linked_data = (
            [
                classes[name]
                for name in dependencies.get(control["name"], [])
                if name in classes
                and classes[name]["kind"] in {"entity", "database"}
                and classes[name]["methods"]
            ]
            if control is not None
            else []
        )

        records = _flow_records(specification)
        first_main_step_id = next(
            (
                str(record.get("step_id") or "")
                for record in records
                if ":main:" in str(record.get("step_id") or "")
            ),
            "",
        )
        trigger = str(specification.get("trigger") or summary.get("trigger") or "")
        for record in records:
            actor_step = _actor_requires_system_input(record["sentence"], actor) or (
                str(record.get("step_id") or "") == first_main_step_id
                and _system_receives_actor_trigger(record["sentence"], trigger, actor)
            )
            if actor_step:
                candidate_classes = [boundary]
            else:
                # After the actor enters through the selected Boundary, all
                # use-case work stays inside its selected Control and the
                # Control's declared data collaborators.  Exposing methods on
                # unrelated Controls was the source of cross-use-case calls.
                candidate_classes = [
                    candidate
                    for candidate in [control, *linked_data]
                    if candidate is not None
                ]
            candidates = [
                (class_item, method)
                for class_item in candidate_classes
                for method in _method_candidates(class_item, actor_step)
            ]
            selected_class, selected_method = _pick_method(candidates)
            plans.append({
                **record,
                "use_case_id": use_case_id,
                "use_case_name": use_case_name,
                "actor": actor,
                "actor_led": actor_step,
                "boundary": boundary,
                "control": control,
                "selected_class": selected_class,
                "selected_method": selected_method,
                "requires_semantic_selection": selected_class is None,
                "candidates": [
                    {"class_name": item[0]["name"], "method": item[1]}
                    for item in candidates
                ],
            })
    return plans


_ACTOR_SELECTION_PATTERN = re.compile(
    r"\b(?:select|selects|selected|selecting|choose|chooses|chosen|choosing|pick|picks|picked|picking)\b",
    re.IGNORECASE,
)
_SELECTION_METHOD_PATTERN = re.compile(r"^(?:select|choose|pick)", re.IGNORECASE)


def _supplementary_actor_selection_routes(
    specification: dict[str, Any],
    actor: str,
    boundary: dict[str, Any],
    control: dict[str, Any] | None,
    route_candidates: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Expose an existing selection Boundary when the primary route lacks one.

    A use case can legitimately begin by selecting an already displayed item
    and then continue through a different Boundary's transaction route.  The
    route selector still owns the primary flow; this helper merely makes an
    explicitly declared, selection-oriented Boundary available to the semantic
    extractor.  It never picks the method or invents an operation.
    """
    has_actor_selection = any(
        _actor_requires_system_input(str(record.get("sentence") or ""), actor)
        and _ACTOR_SELECTION_PATTERN.search(str(record.get("sentence") or ""))
        for record in _flow_records(specification)
    )
    primary_has_selection = any(
        _SELECTION_METHOD_PATTERN.search(method_name(method))
        for method in boundary.get("methods") or []
    )
    selected = [(boundary, control)]
    if not has_actor_selection or primary_has_selection:
        return selected

    seen = {
        (
            str(boundary.get("name") or ""),
            str(control.get("name") or "") if control is not None else "",
        )
    }
    for candidate_boundary, candidate_control in route_candidates:
        key = (
            str(candidate_boundary.get("name") or ""),
            str(candidate_control.get("name") or "") if candidate_control is not None else "",
        )
        if key in seen or not any(
            _SELECTION_METHOD_PATTERN.search(method_name(method))
            for method in candidate_boundary.get("methods") or []
        ):
            continue
        selected.append((candidate_boundary, candidate_control))
        seen.add(key)
    return selected


def _assemble_deterministic_diagrams(
    plans: list[dict[str, Any]],
    selections: dict[str, tuple[str, str]],
    classes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    def add_message_participants(
        participants: dict[str, dict[str, str]], messages: list[dict[str, Any]]
    ) -> None:
        """Keep valid message endpoints and participant declarations in sync."""
        by_alias = {
            _alias(item["name"]): item
            for item in classes.values()
            if item.get("name")
        }
        for message in messages:
            for endpoint in (message.get("source"), message.get("target")):
                alias = _alias(str(endpoint or ""))
                item = by_alias.get(alias)
                if alias and item and alias not in participants:
                    participants[alias] = _participant(item)

    diagrams: list[dict[str, Any]] = []
    for use_case_id in dict.fromkeys(plan["use_case_id"] for plan in plans):
        use_case_plans = [plan for plan in plans if plan["use_case_id"] == use_case_id]
        first = use_case_plans[0]
        actor_alias = _alias(first["actor"])
        if actor_alias in classes:
            actor_alias += "Actor"
        participants: dict[str, dict[str, str]] = {
            actor_alias: {
                "name": first["actor"],
                "alias": actor_alias,
                "kind": "actor",
                "description": "Primary actor from the use-case specification",
                "source_class": "",
            }
        }
        messages: list[dict[str, Any]] = []
        unresolved_steps: list[dict[str, Any]] = []
        narrative_steps: list[dict[str, Any]] = []
        reached = {actor_alias}
        call_number = 0

        def mark_unresolved(plan: dict[str, Any], reason: str) -> None:
            """Keep an ungrounded specification step visible in this UC's model."""
            unresolved_steps.append(
                {
                    "step_id": plan["step_id"],
                    "sentence": plan["sentence"],
                    "reason": reason,
                    "candidates": [
                        f"{candidate['class_name']}.{candidate['method']}"
                        for candidate in plan["candidates"]
                    ],
                }
            )

        def emit_message(
            source: str,
            target: str,
            method: str,
            plan: dict[str, Any],
            return_type: str | None = None,
            message_type: Literal["sync", "async", "self"] = "sync",
        ) -> bool:
            nonlocal call_number
            call_number += 1
            call = _message(
                source,
                target,
                method,
                use_case_id,
                plan["step_id"],
                plan["fragment"],
                call_number,
                return_type,
                message_type,
            )
            messages.append(call)
            if return_type and return_type.strip().lower() != "void":
                messages.append(_return_message(call, return_type))
            return True

        for plan_index, plan in enumerate(use_case_plans):
            boundary = plan["boundary"]
            control = plan["control"]
            participants.setdefault(_alias(boundary["name"]), _participant(boundary))
            if control is not None:
                participants.setdefault(_alias(control["name"]), _participant(control))

            selected_name, selected_method = selections.get(
                plan["step_id"],
                (
                    plan["selected_class"]["name"],
                    plan["selected_method"],
                ) if plan["selected_class"] is not None else ("", ""),
            )
            # The semantic selector may fail or decline to choose.  Do not turn
            # candidate list order into a false interaction, but retain the step
            # as an explicit review item instead of making this UC disappear.
            if not selected_name or not selected_method:
                if plan["actor_led"]:
                    mark_unresolved(
                        plan,
                        "No grounded receiver method was selected from the class diagram.",
                    )
                else:
                    narrative_steps.append(
                        {
                            "step_id": plan["step_id"],
                            "sentence": plan["sentence"],
                            "reason": "Narrative outcome or condition represented by the surrounding interaction.",
                            "candidates": [
                                f"{candidate['class_name']}.{candidate['method']}"
                                for candidate in plan["candidates"]
                            ],
                        }
                    )
                continue
            selected_class = classes[selected_name]
            # An actor entry is emitted only for an actor-led use-case step.
            if plan["actor_led"]:
                target_boundary = boundary if selected_class["kind"] != "boundary" else selected_class
                entry_method = selected_method if selected_class["kind"] == "boundary" else target_boundary["methods"][0]
                boundary_alias = _alias(target_boundary["name"])
                emit_message(
                    actor_alias,
                    boundary_alias,
                    entry_method,
                    plan,
                    target_boundary.get("method_returns", {}).get(entry_method),
                )
                reached.add(boundary_alias)

                # The actor's input must reach the selected Control before it
                # can perform the following system steps.  Preserve the exact
                # Boundary method when the Control owns the same operation;
                # otherwise the next selected system step establishes the
                # Boundary -> Control hand-off.
                if control is not None and entry_method in control.get("methods", []):
                    control_alias = _alias(control["name"])
                    emit_message(
                        boundary_alias,
                        control_alias,
                        entry_method,
                        plan,
                        control.get("method_returns", {}).get(entry_method),
                    )
                    reached.add(control_alias)
                elif control is not None:
                    # The public Boundary operation and its Control operation
                    # often have different names (for example
                    # ``browseCatalog`` -> ``getCourses``).  The old assembler
                    # only forwarded when the names were identical, leaving a
                    # valid API endpoint with no Control call in the sequence.
                    # A single method on the selected Control is structurally
                    # unambiguous; forward to it without asking the LLM to
                    # invent another interaction.  Multiple methods remain an
                    # explicit unresolved choice.
                    control_methods = list(control.get("methods") or [])
                    later_control_call = any(
                        (
                            selections.get(
                                later_plan["step_id"],
                                (
                                    later_plan["selected_class"]["name"],
                                    later_plan["selected_method"],
                                )
                                if later_plan["selected_class"] is not None
                                else ("", ""),
                            )
                            == (control["name"], control_methods[0])
                        )
                        for later_plan in use_case_plans[plan_index + 1 :]
                    ) if control_methods else False
                    if len(control_methods) == 1 and not later_control_call:
                        control_alias = _alias(control["name"])
                        control_method = control_methods[0]
                        emit_message(
                            boundary_alias,
                            control_alias,
                            control_method,
                            plan,
                            control.get("method_returns", {}).get(control_method),
                        )
                        reached.add(control_alias)
                continue

            b_alias = _alias(boundary["name"])

            if selected_class["kind"] == "control":
                target = _alias(selected_class["name"])
                if target in reached:
                    # A Control already reached by the initial request performs
                    # later use-case steps as internal operations.  Repeating a
                    # Boundary -> Control call for each step collapses checks,
                    # persistence and notifications into one fake endpoint call.
                    source = target
                    message_type = "self"
                else:
                    # Boundary -> Control is the first hand-off when the actor
                    # request could not be forwarded using the same signature.
                    source = b_alias
                    message_type = "sync"
                if source not in reached:
                    mark_unresolved(
                        plan,
                        "The Boundary was not reached by a preceding actor interaction.",
                    )
                    continue
                reached.add(target)
            elif selected_class["kind"] == "boundary":
                # A non-actor-led step with a boundary-class selection can only be
                # meaningful as Control -> Boundary (output/display direction).
                # A boundary input method selected for a system step is not
                # silently rerouted to an arbitrary Control method. It remains
                # unresolved for validation/LLM repair.
                if control is not None and not selected_method.lower().startswith(_OUTPUT_METHOD_PREFIXES):
                    mark_unresolved(
                        plan,
                        "A system step selected a Boundary input operation instead of a grounded output operation.",
                    )
                    continue
                if control is not None and selected_method.lower().startswith(_OUTPUT_METHOD_PREFIXES):
                    # Control -> Boundary output direction
                    ctrl_alias = _alias(control["name"])
                    if ctrl_alias not in reached:
                        mark_unresolved(
                            plan,
                            "The Control was not reached before producing a Boundary output.",
                        )
                        continue
                    source = ctrl_alias
                    target = b_alias
                    message_type = "sync"
                else:
                    source = target = b_alias
                    message_type = "self"
                reached.add(target)
            else:
                # Control -> Entity/Database (BCE forward direction)
                if control is None:
                    source = b_alias
                    target = _alias(selected_class["name"])
                    message_type = "sync"
                    if source not in reached:
                        mark_unresolved(
                            plan,
                            "The Boundary was not reached by a preceding actor interaction.",
                        )
                        continue
                else:
                    control_alias = _alias(control["name"])
                    if control_alias not in reached:
                        mark_unresolved(
                            plan,
                            "The Control was not reached before accessing an Entity or Database.",
                        )
                        continue
                    source = control_alias
                    target = _alias(selected_class["name"])
                    message_type = "sync"
                reached.add(target)
            emit_message(
                source,
                target,
                selected_method,
                plan,
                selected_class.get("method_returns", {}).get(selected_method),
                message_type,
            )
            reached.add(target)

        coalesced: list[dict[str, Any]] = []
        for message in messages:
            if coalesced and all(
                coalesced[-1].get(field) == message.get(field)
                for field in ("source", "target", "label", "type", "fragments")
            ):
                coalesced[-1]["step_ids"] = list(
                    dict.fromkeys(
                        [*coalesced[-1]["step_ids"], *message["step_ids"]]
                    )
                )
                continue
            coalesced.append(message)
        messages = coalesced
        add_message_participants(participants, messages)

        # Only message participants are normally emitted.  An unresolved UC
        # keeps its actor and Boundary as visual context for the explicit note.
        active = {value for message in messages for value in (message["source"], message["target"])}
        if unresolved_steps:
            active.add(actor_alias)
            active.add(_alias(first["boundary"]["name"]))
        ordered = [participant for alias, participant in participants.items() if alias in active]
        diagrams.append({
            "use_case_id": use_case_id,
            "use_case_name": first["use_case_name"],
            "Participants": ordered,
            "Messages": messages,
            "UnresolvedSteps": unresolved_steps,
            "NarrativeSteps": narrative_steps,
        })
    return diagrams


def _generate_use_case_diagram(
    specification: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
    classes: dict[str, dict[str, Any]],
    dependencies: dict[str, list[str]],
    class_diagram_puml: str,
) -> dict[str, Any]:
    """Generate one interaction with selective LLM semantics and deterministic contracts.

    The former deterministic assembler selected the sole Control method for
    every system sentence.  It therefore converted validation, retrieval,
    success, and exception steps into repeated self-calls.  Class contracts are
    constraints, not enough information to decide the interaction's causal
    order. Ambiguous flows receive that semantic decision from the per-use-case
    LLM, while distinct grounded operations remain a deterministic projection.
    """
    use_case_id = str(specification.get("use_case_id") or "").strip()
    summary = summaries.get(use_case_id) or {}
    if not any(
        item["kind"] == "boundary" and item["methods"]
        for item in classes.values()
    ):
        return _generate_llm_use_case_diagram(
            specification,
            summary,
            class_diagram_puml,
            classes,
        )
    route_candidates = _route_candidates(classes, dependencies)
    # A short, constrained route decision prevents the richer interaction
    # extraction from freely choosing a valid-but-unrelated maintenance or
    # search route.  The second request still owns message semantics; it only
    # receives the one Boundary/Control path selected for this use case.
    if len(route_candidates) != 1:
        boundary, control = _select_use_case_route(
            specification, summary, classes, dependencies
        )
        extraction_routes = _supplementary_actor_selection_routes(
            specification,
            str(specification.get("primary_actor") or summary.get("primary_actor") or "User"),
            boundary,
            control,
            route_candidates,
        )
        return _generate_llm_use_case_diagram(
            specification,
            summary,
            class_diagram_puml,
            classes,
            extraction_routes,
        )

    boundary, control = _select_use_case_route(
        specification, summary, classes, dependencies
    )
    plans = _build_sequence_plans(
        [specification],
        summaries,
        classes,
        dependencies,
        {use_case_id: (boundary, control)},
    )
    # A structurally non-unique receiver is not a safe fallback to the old
    # assembler either.  Its later element-selection call can choose the same
    # method for validation, retrieval, presentation, and exception steps,
    # while this pre-selection list still contains ``None`` and misses the
    # duplicate.  Both a non-unique candidate set and a repeated unique method
    # require semantic interaction assembly.
    selected_receivers = [
        (plan["selected_class"]["name"], plan["selected_method"])
        for plan in plans
        if plan["selected_class"] is not None
    ]
    requires_semantic_assembly = (
        any(plan["requires_semantic_selection"] for plan in plans)
        or len(selected_receivers) != len(set(selected_receivers))
    )
    if requires_semantic_assembly:
        extraction_routes = _supplementary_actor_selection_routes(
            specification,
            str(specification.get("primary_actor") or summary.get("primary_actor") or "User"),
            boundary,
            control,
            route_candidates,
        )
        return _generate_llm_use_case_diagram(
            specification,
            summary,
            class_diagram_puml,
            classes,
            extraction_routes,
        )
    selections = _select_uncertain_elements(plans)
    diagrams = _assemble_deterministic_diagrams(plans, selections, classes)
    return next(
        (
            diagram
            for diagram in diagrams
            if str(diagram.get("use_case_id") or "").strip() == use_case_id
        ),
        {
            "use_case_id": use_case_id,
            "use_case_name": str(specification.get("name") or "").strip(),
            "Participants": [],
            "Messages": [],
            "UnresolvedSteps": [],
        },
    )


def _unresolved_use_case_diagram(
    specification: dict[str, Any],
    summary: dict[str, Any],
    classes: dict[str, dict[str, Any]],
    reason: str,
    route_candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Build a visible, reviewable UC result when semantic extraction fails.

    This is intentionally not a fabricated interaction.  It preserves the
    actor, an available Boundary for visual context, and every missing flow step
    as a note.  The renderer can therefore produce one card per UC while the
    design gate still reports that the mapping needs attention.
    """
    use_case_id = str(specification.get("use_case_id") or "").strip()
    actor = str(
        specification.get("primary_actor")
        or summary.get("primary_actor")
        or "User"
    ).strip()
    actor_alias = _alias(actor)
    if actor_alias in classes:
        actor_alias += "Actor"
    participants: list[dict[str, str]] = [
        {
            "name": actor,
            "alias": actor_alias,
            "kind": "actor",
            "description": "Primary actor from the use-case specification",
            "source_class": "",
        }
    ]
    route = next(iter(route_candidates or []), None)
    boundary = route[0] if route is not None else next(
        (
            item
            for item in sorted(classes.values(), key=lambda item: str(item.get("name") or ""))
            if item.get("kind") == "boundary" and item.get("methods")
        ),
        None,
    )
    if boundary is not None:
        participants.append(_participant(boundary))
    if route is not None and route[1] is not None:
        participants.append(_participant(route[1]))
    candidates = [
        f"{item['name']}.{method}"
        for item in classes.values()
        for method in item.get("methods") or []
        if item.get("kind") in {"boundary", "control", "entity", "database"}
    ]
    records = _flow_records(specification)
    unresolved = [
        {
            "step_id": record["step_id"],
            "sentence": record["sentence"],
            "reason": reason,
            "candidates": candidates,
        }
        for record in records
    ]
    if not unresolved:
        unresolved.append(
            {
                "step_id": f"{use_case_id}:specification",
                "sentence": "",
                "reason": "The use-case specification contains no numbered flow step.",
                "candidates": candidates,
            }
        )
    return {
        "use_case_id": use_case_id,
        "use_case_name": str(
            specification.get("name") or summary.get("name") or ""
        ).strip(),
        "Participants": participants,
        "Messages": [],
        "UnresolvedSteps": unresolved,
    }


def _generate_llm_use_case_diagram(
    specification: dict[str, Any],
    summary: dict[str, Any],
    class_diagram_puml: str,
    classes: dict[str, dict[str, Any]],
    route_candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Use full UC semantics only when the BCE structure itself is ambiguous."""
    use_case_id = str(specification.get("use_case_id") or "").strip()
    try:
        extracted = extract_sequence_model(
            json.dumps(
                {
                    "use_case": summary,
                    "use_case_specification": specification,
                },
                ensure_ascii=False,
                indent=2,
            ),
            class_diagram_puml,
            [
                {
                    "boundary_class": boundary["name"],
                    "control_class": control["name"] if control is not None else "",
                    "supplementary_actor_selection": index > 0,
                }
                for index, (boundary, control) in enumerate(route_candidates or [])
            ] or None,
        )
    except StructuredLlmError:
        return _unresolved_use_case_diagram(
            specification,
            summary,
            classes,
            "Sequence planning failed before a grounded interaction could be assembled.",
            route_candidates,
        )
    _drop_unknown_flow_messages(extracted, specification)
    if not extracted.get("Messages") and _flow_records(specification):
        return _unresolved_use_case_diagram(
            specification,
            summary,
            classes,
            "Semantic extraction returned no grounded interaction messages.",
        )
    _recover_explicit_actor_retries(extracted, specification, summary)
    covered_step_ids = {
        str(step_id)
        for message in extracted.get("Messages") or []
        if isinstance(message, dict)
        for step_id in message.get("step_ids") or []
        if str(step_id).strip()
    }
    existing_narrative = [
        item for item in extracted.get("NarrativeSteps") or []
        if isinstance(item, dict) and str(item.get("step_id") or "").strip()
    ]
    accounted_step_ids = covered_step_ids | {
        str(item.get("step_id") or "").strip()
        for item in [*existing_narrative, *(extracted.get("UnresolvedSteps") or [])]
        if isinstance(item, dict)
    }
    unresolved = [
        {
            "step_id": record["step_id"],
            "sentence": record["sentence"],
            "reason": (
                "No grounded class method was selected for this semantically "
                "distinct use-case step."
            ),
            "candidates": [],
        }
        for record in _flow_records(specification)
        if record["step_id"] not in accounted_step_ids
        and _actor_requires_system_input(
            record["sentence"],
            str(specification.get("primary_actor") or summary.get("primary_actor") or "User"),
        )
    ]
    narrative = [
        *existing_narrative,
        *[
            {
                "step_id": record["step_id"],
                "sentence": record["sentence"],
                "reason": "Narrative outcome or condition represented by the surrounding interaction.",
                "candidates": [],
            }
            for record in _flow_records(specification)
            if record["step_id"] not in accounted_step_ids
            and not _actor_requires_system_input(
                record["sentence"],
                str(specification.get("primary_actor") or summary.get("primary_actor") or "User"),
            )
        ],
    ]
    return {
        "use_case_id": use_case_id,
        "use_case_name": str(
            specification.get("name") or summary.get("name") or ""
        ).strip(),
        **extracted,
        "UnresolvedSteps": unresolved,
        "NarrativeSteps": narrative,
    }


def _drop_unknown_flow_messages(
    extracted: dict[str, Any], specification: dict[str, Any]
) -> None:
    """Remove LLM messages whose trace points outside the use-case spec.

    A real class method is not sufficient evidence for an interaction: a
    message must also belong to a declared main/extension step.  Keeping an
    invented step id lets a valid method (for example ``viewCourseDetails``)
    leak from a neighbouring use case into the current diagram.
    """
    known_step_ids = {
        str(record.get("step_id") or "").strip()
        for record in _flow_records(specification)
        if str(record.get("step_id") or "").strip()
    }
    if not known_step_ids or not isinstance(extracted, dict):
        return
    filtered: list[dict[str, Any]] = []
    for message in extracted.get("Messages") or []:
        if not isinstance(message, dict):
            continue
        message_steps = {
            str(step_id).strip()
            for step_id in message.get("step_ids") or []
            if str(step_id).strip()
        }
        if not message_steps or message_steps <= known_step_ids:
            filtered.append(message)
    extracted["Messages"] = filtered


_ACTOR_RETRY_PATTERN = re.compile(
    r"\b(?:re[- ]?enter(?:s|ed|ing)?|re[- ]?submit(?:s|ted|ting)?|retry(?:ing|ies)?|"
    r"try\s+again|enter\s+again|submit\s+again|revise(?:s|d|ing)?|"
    r"modify(?:s|ied|ing)?)\b",
    re.IGNORECASE,
)


def _recover_explicit_actor_retries(
    extracted: dict[str, Any], specification: dict[str, Any], summary: dict[str, Any]
) -> None:
    """Restore an omitted retry with the already grounded Boundary command.

    An extension such as "the user re-enters the credentials" repeats a prior
    input; it does not imply a new class operation.  Some semantic extraction
    responses omit that repetition to avoid duplicate messages, which then
    incorrectly turns a valid retry into a class-method proposal.  This repair
    is intentionally narrow: it applies only to explicit actor retry wording,
    reuses a preceding Actor -> Boundary call, and renders that call in a loop.
    """
    messages = extracted.get("Messages")
    if not isinstance(messages, list):
        return
    use_case_id = str(specification.get("use_case_id") or "").strip()
    actor = str(
        specification.get("primary_actor") or summary.get("primary_actor") or "User"
    ).strip()
    if not use_case_id or not actor:
        return

    participants = extracted.get("Participants") or []
    actor_aliases = {
        str(item.get("alias") or "").strip()
        for item in participants
        if isinstance(item, dict) and str(item.get("kind") or "").lower() == "actor"
    }
    boundary_aliases = {
        str(item.get("alias") or "").strip()
        for item in participants
        if isinstance(item, dict) and str(item.get("kind") or "").lower() == "boundary"
    }
    if not actor_aliases or not boundary_aliases:
        return

    covered = {
        str(step_id).strip()
        for message in messages
        if isinstance(message, dict)
        for step_id in message.get("step_ids") or []
        if str(step_id).strip()
    }
    call_ids = {
        str(message.get("call_id") or "").strip()
        for message in messages
        if isinstance(message, dict)
    }
    records = _flow_records(specification)
    for record in records:
        step_id = str(record.get("step_id") or "").strip()
        if (
            not step_id
            or step_id in covered
            or ":extension:" not in step_id
            or not _actor_requires_system_input(str(record.get("sentence") or ""), actor)
            or not _ACTOR_RETRY_PATTERN.search(str(record.get("sentence") or ""))
        ):
            continue
        anchor = re.match(rf"^{re.escape(use_case_id)}:extension:(\d+)[A-Za-z]*:", step_id)
        if anchor is None:
            continue
        anchor_step_id = f"{use_case_id}:main:{anchor.group(1)}"
        template_index = -1
        template: dict[str, Any] | None = None
        anchor_index = -1
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            step_ids = {str(value).strip() for value in message.get("step_ids") or []}
            if anchor_step_id in step_ids:
                anchor_index = index
            if (
                str(message.get("source") or "").strip() in actor_aliases
                and str(message.get("target") or "").strip() in boundary_aliases
                and str(message.get("type") or "sync").strip().lower() in {"sync", "async"}
                and method_call_signature(str(message.get("label") or ""))
                and any(
                    re.fullmatch(rf"{re.escape(use_case_id)}:main:\d+", candidate)
                    for candidate in step_ids
                )
            ):
                template_index = index
                template = message
        if template is None or template_index > anchor_index:
            continue
        fragment = dict(record.get("fragment") or {})
        if not fragment:
            continue
        fragment.update({"type": "loop", "branch": "main"})
        retry = dict(template)
        retry["fragments"] = [fragment]
        retry["use_case_ids"] = [use_case_id]
        retry["step_ids"] = [step_id]
        retry["reply_to"] = ""
        suffix = 1
        call_id = f"{use_case_id}-retry-{suffix}"
        while call_id in call_ids:
            suffix += 1
            call_id = f"{use_case_id}-retry-{suffix}"
        retry["call_id"] = call_id
        retry["arguments"] = [
            {
                **argument,
                "source_kind": "input",
                "source_ref": step_id,
            }
            for argument in template.get("arguments") or []
            if isinstance(argument, dict)
        ]
        messages.insert(anchor_index + 1, retry)
        covered.add(step_id)
        call_ids.add(call_id)


def extract_sequence_diagrams(
    usecase_spec: Any,
    class_diagram_puml: str,
) -> dict[str, Any]:
    """Generate exactly one visible result per use case.

    Structural uniqueness is resolved without an LLM.  Any semantic ambiguity
    is resolved per use case against the class-method contract; failure leaves a
    review note in that UC rather than omitting its diagram.
    """
    if not usecase_spec:
        return {}
    usecase_spec = normalize_sequence_usecase_spec(usecase_spec)

    use_cases = {
        str(item.get("id") or "").strip(): item
        for item in usecase_spec.get("use_cases") or []
        if isinstance(item, dict) and item.get("id")
    }
    specifications = [
        item
        for item in usecase_spec.get("use_case_specs") or []
        if isinstance(item, dict) and item.get("use_case_id")
    ]
    classes, dependencies = _parse_class_catalog(class_diagram_puml)
    # Each use case is independent until the final collection assembly. A worker
    # may call the LLM when structural contracts cannot decide interaction
    # semantics; deterministic code keeps the per-UC input and validation bounded.
    from app.core.config import settings

    worker_limit = max(1, int(getattr(settings, "design_sequence_parallelism", 4)))
    generated: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(worker_limit, len(specifications)))) as executor:
        futures = {
            executor.submit(
                _generate_use_case_diagram,
                specification,
                use_cases,
                classes,
                dependencies,
                class_diagram_puml,
            ): str(specification.get("use_case_id") or "").strip()
            for specification in specifications
        }
        for future in as_completed(futures):
            use_case_id = futures[future]
            try:
                generated[use_case_id] = future.result()
            except ValueError as exc:
                # Route or method selection was constrained to the class
                # contract and still could not produce an exact choice.  Do
                # not replace that safe failure with an unconstrained full
                # diagram generation that can silently select the wrong API.
                logger.warning(
                    "semantic sequence selection failed for use case %s",
                    use_case_id,
                    exc_info=True,
                )
                specification = next(
                    item for item in specifications
                    if str(item.get("use_case_id") or "").strip() == use_case_id
                )
                generated[use_case_id] = _unresolved_use_case_diagram(
                    specification,
                    use_cases.get(use_case_id, {}),
                    classes,
                    str(exc) or "Semantic Boundary/Control or method selection failed.",
                )
            except Exception:
                # A single malformed input must remain visible as a review
                # result instead of cancelling unrelated use cases.
                logger.warning(
                    "parallel sequence generation failed for use case %s",
                    use_case_id,
                    exc_info=True,
                )
                specification = next(
                    item for item in specifications
                    if str(item.get("use_case_id") or "").strip() == use_case_id
                )
                generated[use_case_id] = _unresolved_use_case_diagram(
                    specification,
                    use_cases.get(use_case_id, {}),
                    classes,
                    "Sequence planning failed before a grounded interaction could be assembled.",
                )

    diagrams = []
    for specification in specifications:
        use_case_id = str(specification.get("use_case_id") or "").strip()
        diagrams.append(
            generated.get(
                use_case_id,
                _unresolved_use_case_diagram(
                    specification,
                    use_cases.get(use_case_id, {}),
                    classes,
                    "Sequence generation did not return a result for this use case.",
                ),
            )
        )

    class_diagram_hash = hashlib.sha256(class_diagram_puml.encode("utf-8")).hexdigest()
    return SequenceDiagramCollection(
        Diagrams=diagrams,
        class_diagram_hash=class_diagram_hash,
    ).model_dump()


def reassemble_sequence_diagrams(
    current_model: dict[str, Any],
    usecase_spec: Any,
    class_diagram_puml: str,
    use_case_ids: set[str],
) -> dict[str, Any]:
    """Regenerate only the affected use-case diagrams from the class contract.

    Reconciliation is deliberately targeted: a validation finding in ``UC3``
    must not replace independently reviewed ``UC1``/``UC2`` interaction models.
    The replacement still uses the normal deterministic planner, including its
    explicit unresolved-step notes when the existing class methods cannot
    ground an action.
    """
    targets = {str(value).strip() for value in use_case_ids if str(value).strip()}
    if not targets:
        return current_model or {}

    normalized = normalize_sequence_usecase_spec(usecase_spec)
    specifications = [
        item
        for item in normalized.get("use_case_specs") or []
        if str(item.get("use_case_id") or "").strip() in targets
    ]
    if not specifications:
        return current_model or {}

    selected_ids = {
        str(item.get("use_case_id") or "").strip() for item in specifications
    }
    scoped_spec = {
        **normalized,
        "use_cases": [
            item
            for item in normalized.get("use_cases") or []
            if str(item.get("id") or "").strip() in selected_ids
        ],
        "use_case_specs": specifications,
    }
    regenerated = extract_sequence_diagrams(scoped_spec, class_diagram_puml)

    current_diagrams = (
        current_model.get("Diagrams")
        if isinstance(current_model, dict)
        and isinstance(current_model.get("Diagrams"), list)
        else None
    )
    if current_diagrams is None:
        # A legacy singleton has no independently preserved UC cards.  Move it
        # to the collection shape rather than silently discarding a regenerated
        # result.
        return regenerated

    generated_by_id = {
        str(item.get("use_case_id") or "").strip(): item
        for item in regenerated.get("Diagrams") or []
        if isinstance(item, dict)
    }
    current_by_id = {
        str(item.get("use_case_id") or "").strip(): item
        for item in current_diagrams
        if isinstance(item, dict)
    }
    ordered_ids = [
        str(item.get("use_case_id") or "").strip()
        for item in normalized.get("use_case_specs") or []
        if str(item.get("use_case_id") or "").strip()
    ]
    diagrams: list[dict[str, Any]] = []
    for use_case_id in ordered_ids:
        if use_case_id in selected_ids and use_case_id in generated_by_id:
            diagrams.append(generated_by_id[use_case_id])
        elif use_case_id in current_by_id:
            diagrams.append(current_by_id[use_case_id])

    # Preserve malformed/unknown legacy cards for the normal validator to
    # report instead of making them disappear during an unrelated repair.
    known_ordered = set(ordered_ids)
    diagrams.extend(
        item
        for item in current_diagrams
        if isinstance(item, dict)
        and str(item.get("use_case_id") or "").strip() not in known_ordered
    )
    return {
        **current_model,
        "Diagrams": diagrams,
        "class_diagram_hash": str(regenerated.get("class_diagram_hash") or ""),
        "MethodProposals": [],
    }
