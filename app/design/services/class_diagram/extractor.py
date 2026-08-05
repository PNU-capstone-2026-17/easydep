"""유스케이스 명세에서 BCE 클래스 모델을 도출한다 (LLM 구조화 출력).

**규칙은 여기 없다.** 프롬프트에 실리는 규범 문장은 `app/design/knowledge/rules.py`에서
조립된다(`rules_section`). 예전에는 통신 규칙과 자기검사 목록이 이 파일의 산문 안에만
있었고, 그래서 두 가지 일이 벌어졌다:

  1. **아무도 판정하지 않았다.** 전부 기계로 확인할 수 있는 것들인데 LLM에게 "스스로
     확인하라"고 부탁하는 것이 전부였다. 부탁이 검증인 적은 없다.
  2. **갈라질 수 있었다.** 규칙을 코드로 옮기면서 산문을 그대로 두면, 검사하는 규칙과
     쓰라고 시키는 규칙이 서로 다른 것이 된다. 요구사항 쪽에서 실제로 그렇게 갈라졌다
     (`app/requirements/knowledge/rules.py` docstring 참조).

지금은 쓰는 쪽과 판정하는 쪽이 **같은 레코드**에서 나온다. 산문으로 남은 것은 규범이
아니라 **모양**이다 — 도출 절차와 예시. 그건 규칙이 아니라 쓰는 법이라서 여기 있다.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.design.knowledge import rules
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


_PREAMBLE = """
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

## BCE Stereotype Definitions
- <<Boundary>>: Mediates interaction between an actor and the system (UI \
  screens, APIs, device/external-system interfaces).
- <<Control>>: Coordinates flow and business logic for one use case or one \
  coherent sub-flow. Does not hold long-lived business data.
- <<Entity>>: Persistent business information that outlives a single \
  use-case execution.
"""

_PROCEDURE = """
## Extraction Procedure (perform in order)
1. Textual/noun-verb analysis: go through MainSuccessScenario, Extensions, \
   and Postconditions sentence by sentence. Noun phrases are Entity/Boundary \
   candidates; verb phrases are Control method candidates.
2. Boundary derivation: for each PrimaryActor/Stakeholder, identify each \
   distinct interaction touchpoint (an input screen, a query, a \
   notification, an external call). Create one Boundary per distinct \
   interaction concern.
3. Control derivation: treat each main-flow segment and each extension \
   branch as a coordination unit. Converge to the smallest number of \
   Controls that still respects single responsibility.
4. Entity derivation: promote a noun to Entity only if it is created, read, \
   updated, deleted, or otherwise persists beyond the use case. Do not \
   promote one-off values or pure modifiers.
5. Field derivation: assign fields to state a class must hold — Entities \
   first; give fields to a Control or Boundary only if it must hold state \
   across steps. Do not list getters/setters as methods.
6. Method derivation: derive methods from actions each class performs or \
   delegates, named as verbNoun().
7. Relationship derivation: if a link would break one of the rules above, \
   insert the missing intermediary Control/Boundary instead of keeping it.
8. Traceability: set `use_case_ids` on every class to the id(s) of the use \
   case(s) it was derived from.
9. Self-check before finalizing: read the Rules section again and check your \
   draft against every rule in it. Every one of them is verified by this \
   project after you answer, so a violation you leave in will be found — \
   correct it now instead.
10. Coverage check: every MainSuccessScenario step should be represented by \
   at least one class or relationship.

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


def rules_section(stage: str = rules.CLASS_DIAGRAM) -> str:
    """규범 문장을 지식베이스에서 조립한다 — **산문으로 다시 적지 않는다.**

    두 절이 나오고, 둘째 절이 있는 이유가 첫째만큼 중요하다:

      - **Rules** — 어겨서는 안 되는 것. 각 줄이 규칙 id를 달고 가므로, 나중에 검사가
        낸 지적과 프롬프트의 어느 줄이 같은 것인지 사람이 맞춰 볼 수 있다. 짐작인
        규칙은 그 사실("this project's reading")까지 함께 간다.
      - **Not rules** — 규칙이 **아닌** 것. 과적합은 판정할 때가 아니라 쓸 때 일어난다:
        "액터당 Boundary 하나"를 목표로 알아들은 모델은 필요 없는 Boundary를 지어내거나
        필요한 것을 합친다. 그러니 이 사실을 받아야 하는 쪽은 판정자가 아니라 생성자다.
    """
    section = (
        "\n## Rules (mandatory — a violation must be corrected, not output)\n"
        "This project verifies every rule below after you answer. Where a rule is this "
        "project's reading rather than a verified source, the line says so — judge the "
        "intent in those cases, not the wording.\n\n"
        f"{rules.generation_prompt_block(stage)}\n"
    )
    not_rules = rules.non_rules_block(stage)
    if not_rules:
        section += (
            "\n## Not rules — do NOT optimise for these\n"
            "These are recorded here precisely so you do not treat them as targets.\n\n"
            f"{not_rules}\n"
        )
    return section


#: 생성 프롬프트. 규범은 지식베이스에서, 모양(절차·예시)은 산문에서 온다.
BCE_CLASS_EXTRACTION_SYSTEM_PROMPT = _PREAMBLE + rules_section() + _PROCEDURE


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
