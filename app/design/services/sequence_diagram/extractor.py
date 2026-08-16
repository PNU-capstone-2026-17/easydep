"""유스케이스 명세와 클래스 다이어그램에서 시퀀스 상호작용 모델을 도출한다.

클래스 다이어그램의 BCE 추출과 같은 모양이다: LLM은 PlantUML을 쓰지 않고 구조화된
상호작용 모델만 내놓는다. 다이어그램은 plantuml.generate_sequence_from_model이
결정론적으로 렌더하므로 문법 오류가 구성에 의해 방지된다.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.design.services.common.structured import StructuredLlmError, parse_structured
from app.design.services.sequence_diagram.methods import (
    is_complete_method_call,
    is_return_value_label,
)


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
            if not label:
                raise ValueError("return messages require a result label")
            if not is_return_value_label(label):
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

    @model_validator(mode="after")
    def messages_belong_to_this_use_case(self) -> "UseCaseSequenceModel":
        for message in self.Messages:
            if message.use_case_ids != [self.use_case_id]:
                raise ValueError(
                    "every message in a use-case sequence must reference only its use_case_id"
                )
        return self


class SequenceDiagramCollection(BaseModel):
    """유스케이스별 시퀀스 다이어그램 모음."""

    model_config = ConfigDict(extra="forbid")
    Diagrams: list[UseCaseSequenceModel]
    #: 이 시퀀스가 검증된 클래스 다이어그램 버전. 추출 뒤 코드가 주입한다.
    class_diagram_hash: str = ""

    @model_validator(mode="after")
    def use_case_ids_are_unique(self) -> "SequenceDiagramCollection":
        identifiers = [diagram.use_case_id for diagram in self.Diagrams]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("sequence diagram use_case_ids must be unique")
        return self


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
  exception branch. Each individual main or handling step becomes one or more messages.

## Messages and receiver ownership
- `source` and `target` must both be participant aliases you listed.
- Respect the BCE communication rules: Actor->Boundary, Boundary<->Control,
  Control->Entity/Database. Never call directly between distinct Boundary objects,
  never call Actor->Control/Entity/Database or Boundary->Entity/Database, and do
  not let Entity or Database participants initiate application-layer calls.
- Actor->Boundary calls represent actor input/events. An actor MUST NOT invoke
  output-oriented Boundary methods such as display*, show*, render*, prompt*, or
  notify*; those are called by a Control or another permitted system component.
- `type`: "sync" for a call, "return" for a reply carrying a result, "async" for
  fire-and-forget, and "self" for a call whose source and target are the same.
- Give every sync, async, and self call a unique non-empty `call_id`. Set its
  `reply_to` to "". A return sets `call_id` to "" and `reply_to` to the exact
  preceding call_id it answers. Activation/deactivation set both fields to "".
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
- Use explicit `activate` and `deactivate` events only when an execution interval
  materially helps explain nested synchronous processing. Put the lifeline in both
  `source` and `target` for these events and leave `label` empty.

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
(c) every main and extension handling step id is represented by at least one message,
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
    return parse_sequence_structured(messages, SequenceModel)


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


def extract_sequence_diagrams(
    usecase_spec: Any,
    class_diagram_puml: str,
) -> dict[str, Any]:
    """각 유스케이스 명세를 독립적으로 추출하여 다이어그램 모음으로 만든다."""
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
    diagrams: list[dict[str, Any]] = []
    for specification in specifications:
        use_case_id = str(specification.get("use_case_id") or "").strip()
        summary = use_cases.get(use_case_id, {})
        use_case_name = str(
            specification.get("name") or summary.get("name") or ""
        ).strip()
        scenario = {
            "use_case": summary,
            "use_case_specification": specification,
        }
        extracted = extract_sequence_model(
            json.dumps(scenario, ensure_ascii=False, indent=2),
            class_diagram_puml,
        )
        diagrams.append(
            {
                "use_case_id": use_case_id,
                "use_case_name": use_case_name,
                **extracted,
            }
        )

    class_diagram_hash = hashlib.sha256(class_diagram_puml.encode("utf-8")).hexdigest()
    return SequenceDiagramCollection(
        Diagrams=diagrams,
        class_diagram_hash=class_diagram_hash,
    ).model_dump()
