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


class SequenceElementSelection(BaseModel):
    """LLM이 보완할 수 있는 최소 단위인 단계별 메서드 선택."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    receiver_class: str = Field(min_length=1)
    method: str = Field(min_length=1)


class SequenceElementSelections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[SequenceElementSelection]


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
_OUTPUT_METHOD_PREFIXES = ("display", "show", "render", "prompt", "notify")
_TOKEN_STOP_WORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or",
    "the", "to", "with", "system", "request", "student", "user", "admin",
}
_DOMAIN_WORD_VARIANTS = {
    "registration": "register",
    "authentication": "authenticate",
    "management": "manage",
    "enrollment": "enroll",
    "statistics": "statistic",
    "information": "inform",
    "details": "detail",
    "maintain": "manage",
}


def _words(value: Any) -> set[str]:
    """CamelCase와 일반 문장을 같은 비교 토큰 집합으로 정규화한다."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    result: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(token) <= 1 or token in _TOKEN_STOP_WORDS:
            continue
        result.add(token)
        if variant := _DOMAIN_WORD_VARIANTS.get(token):
            result.add(variant)
        if len(token) > 4 and token.endswith("ies"):
            result.add(token[:-3] + "y")
        elif len(token) > 4 and token.endswith("ing"):
            result.add(token[:-3])
            result.add(token[:-3] + "e")
        elif len(token) > 4 and token.endswith("ed") and not token.endswith("eed"):
            result.add(token[:-1])
            result.add(token[:-2])
        elif len(token) > 4 and token.endswith("es"):
            result.add(token[:-2])
            result.add(token[:-1])
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            result.add(token[:-1])
    return result


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
        methods = [
            signature
            for line in body.splitlines()
            if (signature := method_call_signature(line))
        ]
        classes[name] = {"name": name, "kind": kind, "methods": methods}

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


def _score_method(sentence: str, class_name: str, method: str) -> int:
    wanted = _words(sentence)
    # Parameter names are part of the contract and often disambiguate two
    # operations with the same verb (for example createSection vs adjustCapacity).
    method_words = _words(method)
    class_words = _words(class_name)
    return len(wanted & method_words) * 4 + len(wanted & class_words)


def _best_class(
    classes: dict[str, dict[str, Any]],
    kind: str,
    use_case_context: str,
    index: int,
    focus_text: str = "",
) -> dict[str, Any] | None:
    candidates = [item for item in classes.values() if item["kind"] == kind and item["methods"]]
    if not candidates:
        return None
    wanted = _words(use_case_context)
    focus_source = focus_text or use_case_context
    focus_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", focus_source.lower())
        if token not in _TOKEN_STOP_WORDS
    ]
    # The first verb identifies the use-case intent. Include a second verb for
    # names such as "Search and view ...", but do not let a shared resource noun
    # (course, section, student) decide which Boundary owns the interaction.
    if focus_tokens and focus_tokens[0] in {"manage", "maintain", "generate", "view"}:
        focus_tokens = focus_tokens[:5]
    elif len(focus_tokens) > 1 and focus_tokens[1] == "and":
        focus_tokens = focus_tokens[:3:2]
    else:
        focus_tokens = focus_tokens[:1]
    focus = _words(" ".join(focus_tokens)) or wanted
    ranked = sorted(
        candidates,
        key=lambda item: (
            len(focus & _words(item["name"])) * 50
            + len(wanted & _words(item["name"])) * 4
            + sum(sorted(
                (_score_method(use_case_context, item["name"], method) for method in item["methods"]),
                reverse=True,
            )[:3]),
            -list(classes).index(item["name"]),
        ),
        reverse=True,
    )
    best_score = (
        len(focus & _words(ranked[0]["name"])) * 50
        + len(wanted & _words(ranked[0]["name"])) * 4
        + sum(sorted(
            (_score_method(use_case_context, ranked[0]["name"], method) for method in ranked[0]["methods"]),
            reverse=True,
        )[:3])
    )
    if best_score == 0:
        return None
    return ranked[0]


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
    sentence: str,
    candidates: list[tuple[dict[str, Any], str]],
) -> tuple[dict[str, Any] | None, str, int]:
    ranked = sorted(
        candidates,
        key=lambda item: (
            _score_method(sentence, item[0]["name"], item[1]),
            -candidates.index(item),
        ),
        reverse=True,
    )
    if not ranked:
        raise ValueError("sequence generation requires at least one callable BCE method")
    best_score = _score_method(sentence, ranked[0][0]["name"], ranked[0][1])
    if best_score:
        if len(ranked) > 1 and _score_method(sentence, ranked[1][0]["name"], ranked[1][1]) == best_score:
            return None, "", 0
        return ranked[0][0], ranked[0][1], best_score
    # A lexical tie is not a rule-derived choice.  Keep the candidates for the
    # constrained LLM selector instead of letting list order invent a call.
    if len(candidates) == 1:
        return ranked[0][0], ranked[0][1], 1
    return None, "", 0


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
    subjects = {actor_name.lower().strip(), "user", "the user", "actor", "the actor"}
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


def _select_uncertain_group(plans: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """불확실한 단계의 메서드 선택만 한 번의 작은 LLM 요청으로 보완한다.

    응답이 실패하거나 후보 밖의 값을 고르면 그 단계는 생성하지 않는다. 선택적 보완
    때문에 전체 시퀀스 생성이 실패하거나, 후보 순서가 근거 없는 호출을 만들지 않게
    하는 경계다.
    """
    uncertain = [plan for plan in plans if plan["score"] == 0]
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
                "Use only the supplied candidates, keep every step_id exact, and do not generate "
                "participants, messages, UML, or prose."
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
    """유스케이스별 불확실한 메서드 선택을 병렬 보완한다.

    각 작업은 하나의 유스케이스만 다루므로 LLM 응답이 서로 영향을 주지 않는다.
    작업 수는 설정으로 제한하고, 실패한 작업은 해당 단계만 미확정으로 남긴다.
    단일 유스케이스에서는 기존 호출 형태를 유지해 호출 오버헤드와 호환성을 보장한다.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        if plan["score"] == 0:
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


def _message(
    source: str,
    target: str,
    method: str,
    use_case_id: str,
    step_id: str,
    fragment: dict[str, str] | None,
    call_number: int,
) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "label": method,
        "type": "async",
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
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for use_case_index, specification in enumerate(specifications):
        use_case_id = str(specification.get("use_case_id") or specification.get("id") or "").strip()
        summary = summaries.get(use_case_id) or summaries.get(str(specification.get("id") or "")) or {}
        use_case_name = str(specification.get("name") or summary.get("name") or use_case_id)
        use_case_context = " ".join(
            [
                use_case_name,
                *(
                    str(step.get("sentence") or step.get("description") or "")
                    for step in specification.get("main_scenario") or []
                    if isinstance(step, dict)
                ),
                *(
                    str(step.get("sentence") or step.get("description") or "")
                    for extension in specification.get("extensions") or []
                    if isinstance(extension, dict)
                    for step in extension.get("handling_steps") or []
                    if isinstance(step, dict)
                ),
            ]
        )
        actor = str(
            specification.get("primary_actor")
            or summary.get("primary_actor")
            or "User"
        ).strip()
        boundary = _best_class(
            classes, "boundary", use_case_context, use_case_index, use_case_name
        )
        if boundary is None:
            raise ValueError("sequence generation requires a Boundary class with a method")
        linked_controls = [
            classes[name]
            for name in dependencies.get(boundary["name"], [])
            if name in classes and classes[name]["kind"] == "control" and classes[name]["methods"]
        ]
        control = linked_controls[0] if linked_controls else _best_class(
            classes, "control", use_case_context, use_case_index, use_case_name
        )

        for record in _flow_records(specification):
            actor_step = _actor_led(record["sentence"], actor)
            candidate_classes = [boundary] if actor_step or control is None else [boundary, control]
            candidates = [
                (class_item, method)
                for class_item in candidate_classes
                for method in _method_candidates(class_item, actor_step)
            ]
            selected_class, selected_method, score = _pick_method(
                record["sentence"], candidates
            )
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
                "score": score,
                "candidates": [
                    {"class_name": item[0]["name"], "method": item[1]}
                    for item in candidates
                ],
            })
    return plans


def _assemble_deterministic_diagrams(
    plans: list[dict[str, Any]],
    selections: dict[str, tuple[str, str]],
    classes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
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
        emitted_operations: set[tuple[str, str, str]] = set()
        reached = {actor_alias}
        used_actor_calls: set[tuple[str, str]] = set()
        call_number = 0

        def emit_message(
            source: str,
            target: str,
            method: str,
            plan: dict[str, Any],
        ) -> bool:
            nonlocal call_number
            key = (source, target, method)
            if key in emitted_operations:
                # Flow records are interleaved at their extension anchor.  An
                # extension anchored at step 1 can therefore select the same
                # operation as the later main step 2.  The old de-duplication
                # silently kept the extension call and dropped the main step,
                # producing a false coverage finding and an incomplete happy
                # path. Prefer the main-flow occurrence while retaining the
                # historical de-duplication for equivalent branch calls.
                existing_index = next(
                    (
                        index
                        for index, message in enumerate(messages)
                        if (
                            message.get("source"),
                            message.get("target"),
                            message.get("label"),
                        ) == key
                    ),
                    None,
                )
                current_step_id = str(plan.get("step_id") or "")
                current_is_main = ":main:" in current_step_id
                existing_is_main = bool(
                    existing_index is not None
                    and any(
                        ":main:" in str(step_id)
                        for step_id in messages[existing_index].get("step_ids") or []
                    )
                )
                if current_is_main and not existing_is_main and existing_index is not None:
                    # Replace the earlier extension-only call with the main
                    # flow call at the same route.  Its branch fragment must
                    # not leak into the happy path.
                    call_number += 1
                    messages[existing_index] = _message(
                        source,
                        target,
                        method,
                        use_case_id,
                        current_step_id,
                        None,
                        call_number,
                    )
                return False
            emitted_operations.add(key)
            call_number += 1
            messages.append(_message(
                source,
                target,
                method,
                use_case_id,
                plan["step_id"],
                plan["fragment"],
                call_number,
            ))
            return True

        for plan in use_case_plans:
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
            # No deterministic rule and no valid constrained LLM choice means
            # this step remains intentionally uncovered for the validation gate.
            # Generating a call from candidate order would make a false sequence
            # appear implementable.
            if not selected_name or not selected_method:
                continue
            selected_class = classes[selected_name]
            # An actor entry is emitted only for an actor-led use-case step.  The
            # former "first step" fallback picked an arbitrary Boundary method
            # when a specification started with system behavior.
            if plan["actor_led"]:
                target_boundary = boundary if selected_class["kind"] != "boundary" else selected_class
                entry_method = selected_method if selected_class["kind"] == "boundary" else target_boundary["methods"][0]
                entry_key = (target_boundary["name"], entry_method)
                if entry_key in used_actor_calls:
                    unused = [
                        candidate["method"]
                        for candidate in plan["candidates"]
                        if candidate["class_name"] == target_boundary["name"]
                        and (target_boundary["name"], candidate["method"]) not in used_actor_calls
                    ]
                    if unused:
                        best_alt = max(
                            unused,
                            key=lambda method: _score_method(
                                plan["sentence"], target_boundary["name"], method
                            ),
                        )
                        orig_score = _score_method(plan["sentence"], target_boundary["name"], entry_method)
                        alt_score = _score_method(plan["sentence"], target_boundary["name"], best_alt)
                        if alt_score >= orig_score or orig_score == 0:
                            entry_method = best_alt
                            entry_key = (target_boundary["name"], entry_method)
                emit_message(actor_alias, _alias(target_boundary["name"]), entry_method, plan)
                used_actor_calls.add(entry_key)
                reached.add(_alias(target_boundary["name"]))
                continue

            b_alias = _alias(boundary["name"])

            if selected_class["kind"] == "control":
                # Boundary -> Control (BCE forward direction)
                source = b_alias
                target = _alias(selected_class["name"])
                if source not in reached:
                    continue
                reached.add(target)
            elif selected_class["kind"] == "boundary":
                # A non-actor-led step with a boundary-class selection can only be
                # meaningful as Control -> Boundary (output/display direction).
                # A boundary input method selected for a system step is not
                # silently rerouted to an arbitrary Control method. It remains
                # unresolved for validation/LLM repair.
                if control is not None and not selected_method.lower().startswith(_OUTPUT_METHOD_PREFIXES):
                    continue
                if control is not None and selected_method.lower().startswith(_OUTPUT_METHOD_PREFIXES):
                    # Control -> Boundary output direction
                    ctrl_alias = _alias(control["name"])
                    if ctrl_alias not in reached:
                        continue
                    source = ctrl_alias
                    target = b_alias
                else:
                    source = target = b_alias
                reached.add(target)
            else:
                # Control -> Entity/Database (BCE forward direction)
                if control is None:
                    source = b_alias
                    target = _alias(selected_class["name"])
                    if source not in reached:
                        continue
                else:
                    control_alias = _alias(control["name"])
                    if control_alias not in reached:
                        continue
                    source = control_alias
                    target = _alias(selected_class["name"])
                reached.add(target)
            emit_message(source, target, selected_method, plan)
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

        # Only participants that actually occur in a message are emitted.
        active = {value for message in messages for value in (message["source"], message["target"])}
        ordered = [participant for alias, participant in participants.items() if alias in active]
        diagrams.append({
            "use_case_id": use_case_id,
            "use_case_name": first["use_case_name"],
            "Participants": ordered,
            "Messages": messages,
        })
    return diagrams


def _generate_use_case_diagram(
    specification: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
    classes: dict[str, dict[str, Any]],
    dependencies: dict[str, list[str]],
) -> dict[str, Any]:
    """한 유스케이스의 계획·선택·조립을 독립적으로 수행한다."""
    plans = _build_sequence_plans([specification], summaries, classes, dependencies)
    selections = _select_uncertain_elements(plans)
    diagrams = _assemble_deterministic_diagrams(plans, selections, classes)
    use_case_id = str(specification.get("use_case_id") or "").strip()
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
        },
    )


def extract_sequence_diagrams(
    usecase_spec: Any,
    class_diagram_puml: str,
) -> dict[str, Any]:
    """규칙 기반으로 전체 골격을 만들고 불확실한 메서드 선택만 LLM으로 보완한다."""
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
    # Legacy or externally supplied class text may not expose a parseable BCE method
    # catalog. In that genuinely under-specified case the existing full-model LLM
    # extractor remains the last resort instead of fabricating operations.
    if not any(
        item["kind"] == "boundary" and item["methods"]
        for item in classes.values()
    ):
        diagrams = []
        for specification in specifications:
            use_case_id = str(specification.get("use_case_id") or "").strip()
            summary = use_cases.get(use_case_id, {})
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
            )
            diagrams.append(
                {
                    "use_case_id": use_case_id,
                    "use_case_name": str(
                        specification.get("name") or summary.get("name") or ""
                    ).strip(),
                    **extracted,
                }
            )
        return SequenceDiagramCollection(
            Diagrams=diagrams,
            class_diagram_hash=hashlib.sha256(
                class_diagram_puml.encode("utf-8")
            ).hexdigest(),
        ).model_dump()

    # Each use case is independent until the final collection assembly. Keep
    # the worker count bounded because a worker may perform one optional LLM
    # selection call. Results are restored to specification order below.
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
            ): str(specification.get("use_case_id") or "").strip()
            for specification in specifications
        }
        for future in as_completed(futures):
            use_case_id = futures[future]
            try:
                generated[use_case_id] = future.result()
            except Exception:
                # A single malformed/under-specified use case must remain
                # visible as an empty diagram instead of cancelling siblings.
                logger.warning(
                    "parallel sequence generation failed for use case %s",
                    use_case_id,
                    exc_info=True,
                )

    diagrams = []
    for specification in specifications:
        use_case_id = str(specification.get("use_case_id") or "").strip()
        diagrams.append(
            generated.get(
                use_case_id,
                {
                    "use_case_id": use_case_id,
                    "use_case_name": str(
                        specification.get("name")
                        or use_cases.get(use_case_id, {}).get("name")
                        or ""
                    ).strip(),
                    "Participants": [],
                    "Messages": [],
                },
            )
        )

    class_diagram_hash = hashlib.sha256(class_diagram_puml.encode("utf-8")).hexdigest()
    return SequenceDiagramCollection(
        Diagrams=diagrams,
        class_diagram_hash=class_diagram_hash,
    ).model_dump()
