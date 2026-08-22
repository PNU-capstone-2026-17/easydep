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
from app.design.services.common import fields
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
    #: 이 클래스를 식별하는 **자기 필드**들(자연키). 비어 있으면 우리가 대리키를 붙인다.
    #:
    #: 이 칸이 없을 때 ERD 사상은 모든 테이블에 `{table}_id BIGINT`를 만들고, 이름이
    #: 겹치는 필드를 조용히 **버렸다.** 그래서 `Book.isbn` 같은 자연키가 산출물에서
    #: 사라졌다. 대리키를 쓰는 것 자체는 흔한 선택이지만, 그건 우리가 고른 것이므로
    #: **골랐다는 사실이 남아야 한다**(`erd/mapping.py`의 `keyOrigin`).
    identifier: list[str] = Field(default_factory=list)


class BCERelationship(BaseModel):
    source: str
    target: str
    type: str = Field(default="Association")
    #: 양 끝의 다중도. `"1"` · `"0..1"` · `"*"` · `"1..*"` 중 하나이고, **빈 문자열은
    #: "명시 안 됨"이다.**
    #:
    #: 다중도는 ERD만의 개념이 아니라 UML 클래스 다이어그램의 일부다. 이 칸이 없던 동안
    #: ERD 사상은 모든 관계를 1:N으로 **단정**했고(`erd/plantuml.py`), 그래서 M:N이
    #: 연결 테이블이 되는 경로가 아예 없었다. 관계형 사상에서 M:N → 연결 테이블은
    #: 선택이 아니다.
    #:
    #: **기본값이 `"1"`이 아니라 빈 문자열인 것이 요점이다.** 모르는 것을 그럴듯한
    #: 값으로 채우면 지어낸 값이 기본값 뒤에 숨는다. 비어 있으면 사상은 그 관계를
    #: 옮기지 않고 보류로 표시하고, 검사가 그 사실을 지적한다.
    sourceMultiplicity: str = Field(default="")
    targetMultiplicity: str = Field(default="")
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
5. Method signature derivation: write every operation as either `methodName()` or \
   `methodName(parameterName : Type, ...)`; the literal `...` is not a parameter. \
   Derive parameters from values the scenario says a caller submits, selects, \
   searches by, identifies, or supplies to the receiver. For example, email and \
   password, a keyword, a selected product id, an address, a quantity, or payment \
   information are parameters when the receiver must receive those values. Do not \
   leave a parameterless method when the scenario explicitly supplies data to it, \
   but do not turn long-lived Entity state into parameters merely because the \
   receiver can already read it. Every declared parameter needs both a name and a \
   Java/domain type. When the caller uses a produced result, declare \
   `methodName(...): ReturnType`; that declaration is the contract used by return \
   messages in the sequence diagram. Use `: void` for a command with no result. \
   In particular, Control operations that query, validate, authenticate, authorize, \
   calculate, process, create, register, select, initiate, or generate an outcome \
   must declare an explicit `: ReturnType` or `: void`; choose a non-void type when \
   the caller uses the outcome. \
   For update, delete, retire, or other operations that target an existing \
   persistent record, the Control contract must receive the record identifier \
   explicitly (for example `courseId : String`) or receive an Entity/value object \
   that contains that identifier. Do not emit a generic `process(operation, data)` \
   signature that leaves a path/resource identifier with no Control parameter; the \
   downstream API binding cannot safely implement such an operation. \
6. Field derivation: assign fields to state a class must hold — Entities \
   first; give fields to a Control or Boundary only if it must hold state \
   across steps. Do not list getters/setters as methods. Write each field as \
   `name : Type` when the text tells you the type; every emitted field must \
   include a type. When the text does not state one, choose the narrowest \
   Java/domain type supported by the field's meaning rather than omitting it. \
   This is a Java-targeted model: use `int` for ordinary \
   integral counts and `long` for wide-range integral values/identifiers; use \
   `BigDecimal` only when an exact fractional value is explicitly required. \
   Never write SQL types (`INT`, `BIGINT`, `DECIMAL`) or the non-Java type \
   `decimal` in a BCE field.
7. Identifier derivation: for each Entity, if the text names a field that \
   already identifies it (an ISBN, an account number, an email used as the \
   login), list those field names in `identifier`. Leave `identifier` empty \
   when no such field exists — a surrogate key will be added downstream, and \
   an empty list is what says "this project chose the key", not you.
8. Behavioural link derivation: connect Boundary, Control, and Entity to show \
   the flow of a use case. If a link would break one of the rules above, \
   insert the missing intermediary Control/Boundary instead of keeping it.
9. Structural association derivation — DO NOT SKIP THIS STEP. Behavioural \
   links are not data relationships. Separately from step 7, go through the \
   Entities and record how they relate to each other as data: which Entity \
   holds, owns, or refers to which. Give every Entity-to-Entity relationship \
   BOTH `sourceMultiplicity` and `targetMultiplicity`, using exactly one of \
   "1", "0..1", "*", "1..*". These become the foreign keys and join tables of \
   the ER diagram; without the multiplicities that mapping cannot be made, so \
   an Entity-to-Entity relationship with a missing multiplicity is worse than \
   useless. Never express such a relationship by naming a field `memberId` and \
   leaving the relationship out — write the relationship.
   When a relationship has its own Entity, use that Entity as the relationship \
   representation. Do not also emit a direct many-to-many relationship for the \
   same fact; choose one representation so the ERD does not create a duplicate \
   synthetic join table. This applies equally to self-referential relationships. \
   The relationship Entity must carry the endpoint references and any \
   relationship attributes explicitly required by the scenario.
10. Traceability: set `use_case_ids` on every class to the id(s) of the use \
   case(s) it was derived from.
11. Self-check before finalizing: read the Rules section again and check your \
   draft against every rule in it. Every one of them is verified by this \
   project after you answer, so a violation you leave in will be found — \
   correct it now instead.
12. Coverage check: every MainSuccessScenario step should be represented by \
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
    "System records the sign-up in the member's activity history.",
    "System sends a confirmation email to the Visitor."
  ]
}

Expected extraction (excerpt, illustrating granularity and naming only —
your actual output must follow the response schema, not this JSON text):
- SignUpForm <<Boundary>> — collects email/password from the Visitor.
  methods: submitSignUpForm(email : String, password : String)
- SignUpController <<Control>> — coordinates duplicate check, account
  creation, history recording, and confirmation email.
  methods: registerMember(email : String, password : String): Member,
           checkDuplicateEmail(email : String): boolean,
           sendConfirmationEmail(member : Member): void
- Member <<Entity>> — persistent account record.
  fields: email : String, password : String, registeredAt : DateTime
  identifier: email          (the text says the email identifies the account)
- ActivityEntry <<Entity>> — one recorded event in a member's history.
  fields: occurredAt : DateTime, kind : String
  identifier: (empty — nothing in the text identifies an entry)
- Relationship: SignUpForm -> SignUpController (Dependency)
- Relationship: SignUpController -> Member (Dependency)
- Relationship: Member -> ActivityEntry (Association),
  sourceMultiplicity "1", targetMultiplicity "*"

Note the last one. The first two are BEHAVIOURAL — they show who calls whom,
they carry no multiplicity, and they produce nothing in the ER diagram. The
third is STRUCTURAL — it says one Member has many ActivityEntries, and it is
the only kind of relationship that becomes a foreign key. An extraction that
contains only behavioural links has not finished step 8.

Multiplicity pairs and what each becomes downstream:
- "1" with "*" or "1..*"      → a foreign key on the many side
- "1" with "1" or "0..1"      → a foreign key with a uniqueness constraint
- "*" with "*"                → a join table holding both keys
- anything left empty         → NOT MAPPED AT ALL, and reported as a defect

The equivalent spellings "0..*" and "1..1" are read as "*" and "1"; anything
else ("n", "many", "0..N") is not multiplicity notation and is rejected.

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


def _normalize_field_types(result: dict[str, Any]) -> dict[str, Any]:
    """Keep parsed BCE fields in the Java type vocabulary used downstream.

    The schema deliberately leaves field strings open because they may name an
    Entity or a domain value object.  That openness previously let an LLM's
    `decimal` alias pass unchanged into the ERD fallback.  Normalize only the
    known scalar aliases; unknown declared types remain untouched.
    """
    for class_item in result.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        class_item["fields"] = [
            fields.normalize_java_field(str(field))
            for field in class_item.get("fields") or []
        ]
        class_item["methods"] = [
            fields.normalize_java_method(str(method))
            for method in class_item.get("methods") or []
        ]
    return result


def run_bce_parse(messages: list[dict[str, str]]) -> dict[str, Any]:
    """BCE 구조화 완성. 클래스 다이어그램과 ERD의 추출·수정이 공유한다.

    LLM은 항상 BCEExtractionResult 스키마로만 답하므로, 반환은 검증된 BCE dict다.
    호출 배관 자체는 다섯 산출물이 공유한다(common.structured.parse_structured).
    """
    return _normalize_field_types(parse_structured(messages, BCEExtractionResult))


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
