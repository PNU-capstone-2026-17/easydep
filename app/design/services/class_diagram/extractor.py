from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.design.services.common.structured import parse_structured


class BCEClass(BaseModel):
    className: str = Field(default="UnknownClass")
    stereotype: str = Field(default="")
    description: str = Field(default="")
    fields: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    #: 이 클래스를 낳은 유스케이스 id. 추적표(app/design/rtm.py)가 이걸 모아서
    #: "이 유스케이스가 바뀌면 무엇이 영향받는가"를 답한다.
    use_case_ids: list[str] = Field(default_factory=list)


class BCERelationship(BaseModel):
    source: str
    target: str
    type: str = Field(default="Association")
    description: str = Field(default="")


class BCEExtractionResult(BaseModel):
    Classes: list[BCEClass] = Field(default_factory=list)
    Relationships: list[BCERelationship] = Field(default_factory=list)


BCE_CLASS_EXTRACTION_SYSTEM_PROMPT = """
You are a software architect with deep expertise in UML 2.0 Robustness \
Analysis and Ivar Jacobson's Boundary-Control-Entity (BCE / ECB) pattern. \
Your task is to analyze a use-case specification and derive analysis-level \
class diagram elements from it.

## Input
The use-case specification may include fields such as UseCaseName, \
PrimaryActor, Stakeholders, Preconditions, MainSuccessScenario, Extensions, \
and Postconditions. Ignore fields that are absent. Do not fabricate content \
for missing fields, and do not add any class, field, method, or relationship \
that is not grounded in the given text.

## BCE Stereotype Definitions (Jacobson, 1992)
- <<Boundary>>: Mediates interaction between an actor and the system (UI \
  screens, APIs, device/external-system interfaces).
- <<Control>>: Coordinates flow and business logic for one use case or one \
  coherent sub-flow. Does not hold long-lived business data.
- <<Entity>>: Persistent business information that outlives a single \
  use-case execution.

## Communication Rules (mandatory — a violation must be corrected, not output)
1. Actor <-> Boundary only.
2. Boundary <-> Actor or Control only. Never Boundary-Boundary or \
   Boundary-Entity directly.
3. Control <-> Boundary, Entity, or other Control.
4. Entity never initiates action toward a Control or Boundary. Entity-Entity \
   links (aggregation/composition) are allowed but must not represent \
   behavior initiation.
If applying these rules to your draft reveals an illegal link, insert the \
missing intermediary Control/Boundary instead of keeping the illegal link.

## Extraction Procedure (perform in order)
1. Textual/noun-verb analysis: go through MainSuccessScenario, Extensions, \
   and Postconditions sentence by sentence. Noun phrases are Entity/Boundary \
   candidates; verb phrases are Control method candidates.
2. Boundary derivation: for each PrimaryActor/Stakeholder, identify each \
   distinct interaction touchpoint (an input screen, a query, a \
   notification, an external call). Create one Boundary per distinct \
   interaction concern, not automatically one per actor.
3. Control derivation: treat each main-flow segment and each extension \
   branch as a coordination unit. Converge to the smallest number of \
   Controls that still respects single responsibility (usually 1, more only \
   if the logic is genuinely independent).
4. Entity derivation: promote a noun to Entity only if it is created, read, \
   updated, deleted, or otherwise persists beyond the use case. Do not \
   promote one-off values or pure modifiers.
5. Field derivation: assign fields to state a class must hold — Entities \
   first; give fields to a Control or Boundary only if it must hold state \
   across steps. Do not list getters/setters as methods.
6. Method derivation: derive methods from actions each class performs or \
   delegates, named as verbNoun().
7. Relationship derivation: apply the Communication Rules above.
8. Traceability: set `use_case_ids` on every class to the id(s) of the use \
   case(s) it was derived from. Copy the ids exactly as they appear in the \
   input (e.g. "UC1"). **Never invent an id.** If the input carries no ids, \
   leave the list empty rather than making one up — an empty list is honest, \
   a made-up id is a lie the trace matrix will believe.
9. Self-check before finalizing: (a) class names are unique and PascalCase, \
   (b) every relationship's source/target exists among the derived classes, \
   (c) no Entity is a relationship source targeting a Boundary, (d) every \
   MainSuccessScenario step is represented by at least one class or \
   relationship, (e) every use_case_ids entry appears in the input.

## Worked Example
Input (excerpt):
{
  "UseCaseName": "MemberSignUp",
  "PrimaryActor": "Visitor",
  "MainSuccessScenario": [
    "Visitor enters email and password on the sign-up form.",
    "System checks whether the email is already registered.",
    "System creates a new Member account.",
    "System sends a confirmation email to the Visitor."
  ]
}

Expected extraction (excerpt, illustrating granularity and naming only —
your actual output must follow the response schema, not this JSON text):
- SignUpForm <<Boundary>> — collects email/password from the Visitor.
  methods: submitSignUpForm()
- SignUpController <<Control>> — coordinates duplicate check, account
  creation, and confirmation email.
  methods: registerMember(), checkDuplicateEmail(), sendConfirmationEmail()
- Member <<Entity>> — persistent account record.
  fields: email, password, registeredAt
- Relationship: SignUpForm -> SignUpController (Dependency)
- Relationship: SignUpController -> Member (Dependency)

Populate the response strictly according to the provided schema. Do not \
include markdown, code fences, or any conversational text outside the \
schema fields.
"""


def run_bce_parse(messages: list[dict[str, str]]) -> dict[str, Any]:
    """BCE 구조화 완성. 클래스 다이어그램과 ERD의 추출·수정이 공유한다.

    LLM은 항상 BCEExtractionResult 스키마로만 답하므로, 반환은 검증된 BCE dict다.
    호출 배관 자체는 다섯 산출물이 공유한다(common.structured.parse_structured).
    """
    return parse_structured(messages, BCEExtractionResult)


def extract_bce_classes_from_scenario(scenario_text: str) -> dict[str, Any]:
    if not scenario_text:
        return {}

    messages = [
        {"role": "system", "content": BCE_CLASS_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Requirement Specification Scenario:\n{scenario_text}",
        },
    ]
    return run_bce_parse(messages)
