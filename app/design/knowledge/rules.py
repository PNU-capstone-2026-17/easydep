"""설계 규칙 지식베이스 — 무엇을 결함이라 부르는지, 그 근거는 무엇인지.

## 무엇이 없어서 이게 필요했나

클래스 다이어그램의 규칙은 **한 곳에만 있었다: 생성 프롬프트 산문.**
`services/class_diagram/extractor.py`의 통신 규칙(54–63행)과 자기검사 9번(91–95행)이 그것이다.
그리고 그 프롬프트는 LLM에게 *"finalize 전에 스스로 확인하라"*고 부탁한다.

**부탁이 검증인 적은 없다.** (a)~(e)와 통신 규칙은 전부 기계로 판정할 수 있는 것들인데,
지금까지 판정한 곳이 없었다. 그 사이 유일한 "검증"인 `common/validation.py`의
`validate_puml_artifact`는 문법만 보는데, 렌더러가 sanitize로 **구성에 의해** 유효한 PlantUML을
내므로 원리상 실패할 수 없다 — 코드 자신이 "트립와이어이지 수리 트리거가 아니다"라고
적어 두었다(`nodes/artifact.py`).

그래서 규칙을 **데이터로** 한 곳에 모은다. 생성 프롬프트도 검출기도 지적 문구도 여기서
파생된다. 규칙이 산문과 코드에 따로 있으면 갈라지고, 요구사항 쪽에서 실제로 갈라졌다
(`app/requirements/knowledge/rules.py` docstring §"생성 프롬프트도 여기서 조립한다").

## 심각도가 셋인 이유

`NON_RULE`이 있는 것이 핵심이다. "유스케이스당 Control 하나"와 "액터당 Boundary 하나"는
**규칙이 아니라는 사실 자체가 지켜야 할 지식**이다. 프롬프트가 이미 *"Create one Boundary
per distinct interaction concern, not automatically one per actor"*라고 경계하고 있는데,
그 경계가 산문에만 있으면 다음 사람이 관찰을 규칙으로 승격시킨다.

## 왜 요구사항 쪽 지식베이스를 import하지 않는가

`app/design`은 `app/requirements`를 **전혀 import하지 않는다**(현재 위반 0건). 그 격리를
깨지 않는다. 모양은 같지만 규칙 목록은 각 축의 선언이라 공유할 것이 아니다. 공유해야 할
것이 생기면 그때 `app/core/`로 올린다 — `requirements/common/`이 기다리고 있는 자리와 같다.

## `stage` 필드를 지금 두는 이유

지금은 `class_diagram` 규칙만 있다. 그래도 필드를 두는 것은 나머지 네 산출물(시퀀스·API·
ERD·배포)이 같은 골격을 쓰기 때문이다(`nodes/artifact.py`의 `DesignArtifactSpec`). 확장은
여기에 레코드를 더하고 스펙에 `check=`를 채우는 일이 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.design.knowledge import basis

# --- 심각도 -----------------------------------------------------------------
#: 위반이면 검증이 지적한다.
DEFECT = "defect"
#: 생성 쪽 지침. 위반을 지적하지는 않는다(판정 기준이 되기에는 무르다).
GUIDANCE = "guidance"
#: **규칙이 아니라는 기록.** 어디에서도 강제하지 않는다. 관찰이나 공학적 가드를 규칙으로
#: 승격시키지 않기 위해 둔다.
NON_RULE = "non_rule"

SEVERITIES = (DEFECT, GUIDANCE, NON_RULE)

# --- 누가 판정하는가 ---------------------------------------------------------
# `DEFECT`인데 판정하는 곳이 없는 규칙이 생길 수 있다. 그 사실을 `None` 하나로 뭉개면
# "LLM이 판정한다"와 "아무도 판정하지 않는다"가 같은 값이 된다.
#: `knowledge/detectors.py`의 결정론 검출기.
JUDGED_DETECTOR = "detector"
#: LLM 의미 검증자. **아직 없다** — 이 값을 가진 규칙은 지금 하나도 없어야 한다.
JUDGED_VALIDATOR = "validator"
#: **판정하는 곳이 없다.** 규칙은 적혀 있고 검증은 없다는 사실을 드러낸다.
JUDGED_NOWHERE = "nowhere"

JUDGES = (JUDGED_DETECTOR, JUDGED_VALIDATOR, JUDGED_NOWHERE)

# --- 스테이지 ---------------------------------------------------------------
# 이름은 `graphs/subgraphs.py`의 `DESIGN_STAGES`와 같다. import하지 않는 이유는 순환이다
# (subgraphs → services → knowledge). 두 목록이 맞는지는 테스트가 확인한다.
CLASS_DIAGRAM = "class_diagram"
SEQUENCE_DIAGRAM = "sequence_diagram"
API_SPEC = "api_spec"


@dataclass(frozen=True)
class Rule:
    """규칙 하나. **규범 문장은 우리 표현이고, 인용은 좌표다**(원문 아님)."""

    id: str
    stage: str
    severity: str
    #: 규범 문장. 생성 프롬프트에 그대로 들어가므로 영어로 쓴다.
    statement: str
    #: 확인 좌표. 책 좌표이거나, 저장소 안의 코드 위치이거나, 재현 절차다.
    citation: str
    #: 근거 라벨(`basis.BASIS_OF_EVIDENCE`에 등록돼야 한다).
    evidence: str
    #: 짐작인 규칙은 반드시 있어야 한다 — **출처의 한계**를 적는다(위반의 의심이 아니다).
    caveat: str | None = None
    #: 이 규칙을 판정하는 곳. `DEFECT`는 반드시 밝힌다(없으면 `JUDGED_NOWHERE`).
    judged_by: str = JUDGED_NOWHERE
    #: 결정론 검출기 이름(`detectors.py`에 등록). `judged_by`가 검출기일 때만 있다.
    detector: str | None = None
    #: **생성 쪽에만** 주는 보조 문구 — 예시, 쓰는 법. 규범이 아니다.
    #:
    #: 규범은 `statement` 하나뿐이다. 여기에 새 제약을 적으면 아무도 판정하지 않는 규칙이
    #: 조용히 생긴다(그 사실을 드러내려고 `JUDGED_NOWHERE`를 둔 것인데, 이 자리는 그
    #: 표시를 우회한다). 여기 적을 수 있는 것은 `statement`가 이미 말한 것을 다시 보여
    #: 주는 것뿐이다.
    generation_note: str | None = None

    @property
    def hedged(self) -> bool:
        """지적할 때 출처의 한계를 함께 밝혀야 하는가."""
        return basis.needs_hedge(self.evidence)

    @property
    def tag(self) -> str:
        """지적 문구 꼬리표. 짐작인 규칙은 그 사실이 함께 붙는다."""
        parts = [self.id, self.citation]
        if self.hedged:
            parts.append("우리 판단")
        return f"[{' · '.join(parts)}]"

    def prompt_line(self) -> str:
        """생성 프롬프트 한 줄. 근거의 성격까지 모델에게 알린다.

        고지 문구는 라벨마다 다르다(`basis.prompt_note`) — "출처를 확인 못 했다"와
        "우리가 정했다"를 한 문구로 뭉개면 둘 중 하나는 거짓이 된다.
        """
        note = basis.prompt_note(self.evidence)
        return f"- ({self.id}) {self.statement}" + (f" [{note}]" if note else "")


# ---------------------------------------------------------------------------
# 규칙 목록
# ---------------------------------------------------------------------------
RULES: tuple[Rule, ...] = (
    # --- 참조 무결성 --------------------------------------------------------
    # 이 둘이 먼저 오는 이유: 나머지 규칙은 "다이어그램이 덜 좋다"는 말이지만, 이 둘은
    # **없는 것을 있다고 말하는** 것이라 하류 산출물까지 오염시킨다.
    Rule(
        id="class.relationship-endpoints-exist",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every relationship's source and target must name a class that exists in "
            "Classes. Never reference a class you did not declare."
        ),
        # 실측(2026-08-04): 클래스 하나만 선언하고 관계가 선언되지 않은 이름을 가리키는
        # PlantUML을 `java -jar plantuml.jar -syntax`에 넣으면 **오류 없이 통과하고
        # `(2 entities)`를 보고한다** — 선언하지 않은 이름이 그림에 빈 클래스로 생긴다.
        # 재현: tests/test_design_detectors.py::test_plantuml_invents_a_class_for_a_dangling_endpoint
        citation="plantuml.jar -syntax → '(2 entities)' (실측, 재현 테스트 있음)",
        evidence="plantuml-measured",
        judged_by=JUDGED_DETECTOR,
        detector="relationship_endpoints",
        generation_note=(
            "If a relationship needs a class that is not in your list, add the class — "
            "do not leave the endpoint dangling."
        ),
    ),
    Rule(
        id="class.usecase-ids-exist",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every id in use_case_ids must appear in the input use-case specification. "
            "An empty list is honest; an invented id is not."
        ),
        # 같은 판정을 `rtm.py`가 사후 보고로 이미 한다(`unknown_refs`). 판정은 한 곳에서
        # 나와야 하므로 검출기가 `rtm.upstream_names`를 쓴다 — 두 벌이면 갈라진다.
        citation="app/design/rtm.py (unknown_refs) — 같은 판정을 공유한다",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="usecase_ids",
        generation_note=(
            "Copy ids exactly as they appear in the input. If the input carries no ids, "
            "leave the list empty."
        ),
    ),
    # --- BCE 통신 규칙 ------------------------------------------------------
    Rule(
        id="class.stereotype-is-bce",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every class must carry exactly one of the stereotypes Boundary, Control, "
            "or Entity."
        ),
        # **이 규칙이 통신 규칙들보다 먼저 온다.** 스테레오타입을 못 읽으면 아래 세 규칙이
        # 전부 무판정이 되는데, 무판정은 통과처럼 보인다. 그 조용한 실패를 막는 자리다.
        citation="Jacobson, Object-Oriented Software Engineering (1992) — BCE",
        evidence="jacobson-unpinned",
        caveat=(
            "BCE 세 분류가 전부라는 것은 이 저장소가 확인하지 못했다 — 책 사본이 없다. "
            "다만 우리 스키마와 렌더러·하류 파서가 이 셋만 다루므로, 셋 밖의 값은 "
            "적어도 우리 파이프라인에서는 판정 불가다."
        ),
        judged_by=JUDGED_DETECTOR,
        detector="stereotype_is_bce",
    ),
    Rule(
        id="class.no-boundary-entity-link",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement=(
            "A Boundary and an Entity must never be linked directly. Insert the Control "
            "that coordinates them instead."
        ),
        citation="Jacobson, Object-Oriented Software Engineering (1992) — BCE",
        evidence="jacobson-unpinned",
        caveat="BCE 통신 규칙이라고 알고 있으나 이 저장소에서 페이지를 확인하지 못했다.",
        judged_by=JUDGED_DETECTOR,
        detector="communication_rules",
    ),
    Rule(
        id="class.no-boundary-boundary-link",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Two Boundary classes must never be linked directly. A Boundary talks to an "
            "actor or to a Control."
        ),
        citation="Jacobson, Object-Oriented Software Engineering (1992) — BCE",
        evidence="jacobson-unpinned",
        caveat="BCE 통신 규칙이라고 알고 있으나 이 저장소에서 페이지를 확인하지 못했다.",
        judged_by=JUDGED_DETECTOR,
        detector="communication_rules",
    ),
    Rule(
        id="class.entity-does-not-initiate",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement=(
            "An Entity never initiates action toward a Control or a Boundary. Entity-to-"
            "Entity links are allowed; an Entity as the source of a link to a Control or "
            "Boundary is not."
        ),
        citation="Jacobson, Object-Oriented Software Engineering (1992) — BCE",
        evidence="jacobson-unpinned",
        caveat="BCE 통신 규칙이라고 알고 있으나 이 저장소에서 페이지를 확인하지 못했다.",
        judged_by=JUDGED_DETECTOR,
        detector="communication_rules",
    ),
    # --- 형태 ---------------------------------------------------------------
    Rule(
        id="class.names-unique",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement="Class names must be unique.",
        # 판정을 **sanitize 후 기준으로** 하는 것이 요점이다. `Payment Service!`와
        # `Payment_Service_`는 서로 다른 이름으로 들어오지만 렌더러가 같은 이름으로
        # 만든다 — 그림에서는 한 클래스가 되고, 그 사실을 지금까지 아무도 세지 않았다.
        citation="app/design/services/class_diagram/plantuml.py (sanitize_class_name)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="names_unique",
        generation_note=(
            "Names that differ only in punctuation or spacing collapse into one class in "
            "the rendered diagram — make them distinct words, not distinct symbols."
        ),
    ),
    Rule(
        id="class.name-pascal-case",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement="Class names must be PascalCase identifiers.",
        citation="app/design/services/class_diagram/extractor.py (자기검사 9a)",
        evidence="project-convention",
        caveat="우리 표기 규약이다. 어떤 출처가 정한 것이 아니다.",
        judged_by=JUDGED_DETECTOR,
        detector="name_pascal_case",
    ),
    # --- 그라운딩 -----------------------------------------------------------
    Rule(
        id="class.covers-use-cases",
        stage=CLASS_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every use case in the input must be claimed by at least one class through "
            "use_case_ids."
        ),
        # 유스케이스를 통째로 빠뜨리는 것은 다이어그램이 조금 부실한 것이 아니라 **설계
        # 전체에서 그 기능이 사라지는** 것이다. 뒤의 네 산출물이 전부 이걸 재료로 쓴다.
        citation="app/design/graphs/subgraphs.py (DESIGN_STAGES — 나머지 넷의 재료)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="usecase_coverage",
    ),
    # --- 시퀀스 다이어그램: 모델 참조 무결성 -------------------------------
    Rule(
        id="sequence.message-participants-exist",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement="Every sequence message source and target must name a declared participant.",
        citation="app/design/services/sequence_diagram/plantuml.py (undeclared messages are omitted)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_participants",
    ),
    Rule(
        id="sequence.message-bce-flow",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement="Sequence calls must preserve the BCE communication boundaries represented by participant kinds.",
        citation="app/design/services/sequence_diagram/extractor.py (BCE communication rules)",
        evidence="project-convention",
        caveat="이 프로젝트가 사용하는 BCE 흐름 규약이다.",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_bce_flow",
    ),
    Rule(
        id="sequence.references-exist",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement="Sequence source classes and use-case ids must exist in their upstream artifacts.",
        citation="app/design/rtm.py (traceability references)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_traceability",
    ),
    Rule(
        id="sequence.participant-classes-exist",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every non-actor sequence participant must correspond to a class declared "
            "in the class diagram. If the participant has a source_class field, that "
            "class name is checked; otherwise the participant name itself is checked."
        ),
        citation="app/design/services/sequence_diagram/plantuml.py (participant ↔ class alignment)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_participant_classes",
    ),
    Rule(
        id="sequence.message-labels-match-methods",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every sequence message label on a non-return call must name a method "
            "defined in the target class's BCE model. Comparison is at the name level: "
            "visibility, parameters, and return type are stripped before matching."
        ),
        citation="app/design/services/class_diagram/extractor.py (BCEClass.methods)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_message_methods",
    ),
    Rule(
        id="sequence.initial-message-entry",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "The initial non-return sequence message must be an Actor -> Boundary call. "
            "External interactions must enter through a boundary component."
        ),
        citation="app/design/services/sequence_diagram/extractor.py (BCE communication rules)",
        evidence="project-convention",
        caveat="Jacobson BCE 기법의 최초 진입점 규약이다.",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_initial_entry",
    ),
    Rule(
        id="sequence.unmatched-return-message",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every return-type message must be preceded by a matching call between "
            "the same pair of participants."
        ),
        citation="app/design/services/sequence_diagram/extractor.py (Return message guidelines)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_unmatched_returns",
    ),
    Rule(
        id="sequence.usecase-step-coverage",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every use case ID from the specification must be referenced by at least "
            "one sequence message through use_case_ids."
        ),
        citation="app/design/rtm.py (traceability references)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_usecase_coverage",
    ),
    Rule(
        id="sequence.fragment-condition-consistency",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "If a message declares a combined fragment group (alt, loop, opt), it "
            "must specify a condition explanation, and vice versa."
        ),
        citation="app/design/services/sequence_diagram/plantuml.py (Fragment rendering)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_fragment_condition_consistency",
    ),
    Rule(
        id="sequence.database-access-discipline",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Database participants must not be directly invoked by Actor or Boundary "
            "participants; access must be mediated through Control or Entity components."
        ),
        citation="app/design/services/sequence_diagram/extractor.py (Layered architecture rules)",
        evidence="project-convention",
        caveat="데이터 접근 계층 캡슐화 규약이다.",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_database_access_discipline",
    ),
    Rule(
        id="sequence.self-call-method-validation",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Self-calling messages (source == target) must have a non-empty label "
            "naming the internal operation being invoked."
        ),
        citation="app/design/services/sequence_diagram/plantuml.py (Self-call messages)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_self_call_method_validation",
    ),
    Rule(
        id="sequence.orphan-participant-detection",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every participant declared in the sequence model must be involved in "
            "at least one message as either source or target."
        ),
        citation="app/design/rtm.py (Participant active trace)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_orphan_participant_detection",
    ),
    Rule(
        id="sequence.duplicate-consecutive-messages",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Duplicate identical messages must not be emitted consecutively outside "
            "of explicit repetition fragments."
        ),
        citation="app/design/services/sequence_diagram/plantuml.py (Redundant message check)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_duplicate_consecutive_messages",
    ),
    Rule(
        id="sequence.message-naming-convention",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Message operation labels must follow camelCase or verbNoun() method "
            "naming conventions rather than PascalCase class names."
        ),
        citation="app/design/services/sequence_diagram/extractor.py (Operation naming rule)",
        evidence="project-convention",
        caveat="오퍼레이션 표기법 규약이다.",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_message_naming_convention",
    ),
    Rule(
        id="sequence.participant-kind-validity",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every participant kind must be one of the standard stereotypes: "
            "actor, boundary, control, entity, database."
        ),
        citation="app/design/services/sequence_diagram/extractor.py (Participant kinds)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_participant_kind_validity",
    ),
    Rule(
        id="sequence.message-type-validity",
        stage=SEQUENCE_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every message type must be one of the standard call types: "
            "sync, async, return."
        ),
        citation="app/design/services/sequence_diagram/extractor.py (Message call types)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="sequence_message_type_validity",
    ),
    # --- API 명세: 모델 참조 무결성 -----------------------------------------
    Rule(
        id="api.path-parameters-match",
        stage=API_SPEC,
        severity=DEFECT,
        statement="Every path variable must have one matching path parameter, and no extra path parameter is allowed.",
        citation="OpenAPI path template projected by app/design/services/api_spec/openapi.py",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="api_path_parameters",
    ),
    Rule(
        id="api.schema-references-exist",
        stage=API_SPEC,
        severity=DEFECT,
        statement="Request and response schema references must name schemas declared by the API model.",
        citation="app/design/services/api_spec/openapi.py (_body_schema fallback)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="api_schema_references",
    ),
    Rule(
        id="api.operation-ids-unique",
        stage=API_SPEC,
        severity=DEFECT,
        statement="Every API endpoint must have a unique operation_id.",
        citation="app/design/services/api_spec/extractor.py (operation_id self-check)",
        evidence="project-convention",
        caveat="이 프로젝트의 OpenAPI 식별자 규약이다.",
        judged_by=JUDGED_DETECTOR,
        detector="api_operation_ids",
    ),
    Rule(
        id="api.references-exist",
        stage=API_SPEC,
        severity=DEFECT,
        statement="API source classes and use-case ids must exist in their upstream artifacts.",
        citation="app/design/rtm.py (traceability references)",
        evidence="pipeline-invariant",
        judged_by=JUDGED_DETECTOR,
        detector="api_traceability",
    ),
    # --- 지침(지적하지 않는다) ----------------------------------------------
    Rule(
        id="class.control-per-use-case",
        stage=CLASS_DIAGRAM,
        severity=GUIDANCE,
        statement=(
            "Each use case is normally coordinated by at least one Control class."
        ),
        citation="Jacobson, Object-Oriented Software Engineering (1992) — BCE",
        evidence="jacobson-unpinned",
        caveat=(
            "지침이지 규칙이 아니다. 유스케이스가 순수 조회라 조정할 흐름이 없을 수 있다 "
            "— 그것을 결함으로 부를 근거가 없다."
        ),
    ),
    Rule(
        id="class.no-empty-class",
        stage=CLASS_DIAGRAM,
        severity=GUIDANCE,
        statement=(
            "A class should carry at least one field or one method. A class with neither "
            "has not been derived, only named."
        ),
        citation="app/design/services/class_diagram/extractor.py (필드·메서드 도출 5–6)",
        evidence="project-convention",
        caveat=(
            "지침이지 규칙이 아니다. 분석 수준 다이어그램에서 표식용 클래스가 정당할 수 "
            "있다."
        ),
    ),
    # --- 규칙이 아니라는 기록 ------------------------------------------------
    Rule(
        id="class.boundary-per-actor",
        stage=CLASS_DIAGRAM,
        severity=NON_RULE,
        statement=(
            "The number of Boundary classes is NOT tied to the number of actors. Create "
            "one Boundary per distinct interaction concern."
        ),
        citation="app/design/services/class_diagram/extractor.py (Boundary derivation 2)",
        evidence="engineering-guard",
        caveat=(
            "프롬프트가 명시적으로 'not automatically one per actor'라고 적어 두었다. "
            "이걸 규칙으로 승격시키면 모델이 액터 수에 맞추려고 Boundary를 지어내거나 "
            "합친다."
        ),
    ),
    Rule(
        id="class.count-bounds",
        stage=CLASS_DIAGRAM,
        severity=NON_RULE,
        statement=(
            "There is no upper or lower bound on how many classes a use case yields."
        ),
        citation="(관찰 없음 — 세어 본 적이 없다)",
        evidence="engineering-guard",
        caveat=(
            "개수 규칙을 두면 모델이 개수를 맞추려고 클래스를 지어내거나 지운다. "
            "코퍼스에서 분포를 세기 전에는 어떤 상한도 근거가 없다."
        ),
    ),
)


_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}


def rule(rule_id: str) -> Rule:
    """id로 규칙 하나. 없으면 `KeyError` — 없는 규칙을 인용하는 것은 오류다."""
    return _BY_ID[rule_id]


def known_ids() -> frozenset[str]:
    """존재하는 규칙 id 전부. 지적이 댄 인용을 대조하는 데 쓴다."""
    return frozenset(_BY_ID)


def rules_for(stage: str, severity: str | None = None) -> tuple[Rule, ...]:
    """스테이지(+심각도)로 규칙을 고른다. 선언 순서를 유지한다."""
    return tuple(
        r for r in RULES
        if r.stage == stage and (severity is None or r.severity == severity)
    )


def judged_by(stage: str, judge: str) -> tuple[Rule, ...]:
    """이 스테이지에서 그 판정자가 보는 결함 규칙들."""
    return tuple(r for r in rules_for(stage, DEFECT) if r.judged_by == judge)


def tag_of(rule_id: str) -> str:
    """지적 문구에 붙일 꼬리표. 모르는 id는 그 사실을 드러낸다.

    조용히 빈 문자열을 돌려주면 **없는 규칙을 인용한 지적이 근거 있는 지적처럼** 보인다.
    그건 이 지식베이스를 두는 이유와 반대다.
    """
    found = _BY_ID.get(rule_id)
    return found.tag if found else f"[{rule_id} · 알 수 없는 규칙]"


def rule_of(issue: str) -> str | None:
    """지적 문구가 인용한 규칙 id. 못 찾으면 None.

    꼬리표는 우리가 만든다(`Rule.tag` → `[<id> · <좌표> …]`)므로 정확히 맞춰 찾는다.
    문구를 파싱하는 대신 **아는 id로 조회**하는 방향이라, 새 규칙이 생겨도 그대로다.
    """
    for rule_id in _BY_ID:
        if f"[{rule_id} ·" in issue:
            return rule_id
    return None


def generation_prompt_block(stage: str) -> str:
    """**생성** 프롬프트가 지켜야 할 규칙 목록.

    `DEFECT` + `GUIDANCE` 전부를 담는다. 쓰는 쪽에서는 "누가 잡느냐"가 상관없다 —
    검출기가 잡을 결함도 애초에 안 쓰는 편이 낫다.
    """
    lines: list[str] = []
    for r in rules_for(stage):
        if r.severity not in (DEFECT, GUIDANCE):
            continue
        lines.append(r.prompt_line())
        if r.generation_note:
            lines.append(f"  {r.generation_note}")
    return "\n".join(lines)


def non_rules_block(stage: str) -> str:
    """**규칙이 아니라고** 적어 둔 것들 — 생성 쪽에 그 사실 그대로 준다.

    과적합은 판정할 때가 아니라 **쓸 때** 일어난다: "액터당 Boundary 하나"를 목표로
    알아들은 모델은 필요 없는 Boundary를 지어내거나 필요한 것을 합친다. 그러니 이 사실을
    받아야 하는 쪽은 판정자가 아니라 생성자다.
    """
    return "\n".join(f"- ({r.id}) {r.statement}" for r in rules_for(stage, NON_RULE))


def unjudged_defects() -> tuple[Rule, ...]:
    """결함이라고 적어 놓고 **아무도 판정하지 않는** 규칙들.

    비어 있는 것이 목표다 — 지금은 비어 있고, 테스트가 그것을 고정한다. 의미 검증자를
    넣으면서 `JUDGED_VALIDATOR` 규칙을 추가할 때 이 목록이 잠깐 늘어날 수 있는데, 그
    사실이 조용히 지나가면 안 된다.
    """
    return tuple(r for r in RULES if r.severity == DEFECT and r.judged_by == JUDGED_NOWHERE)
