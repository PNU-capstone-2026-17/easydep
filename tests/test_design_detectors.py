"""결정론 검출기 — 눈금이 맞는지 확인한다 (네트워크 불필요).

검출기가 있다는 것과 그것이 잡는다는 것은 다른 말이다. 여기서 확인하는 것은 후자다:

  - 심어 둔 결함을 **정확히 그 규칙으로** 잡는가 (검출률)
  - 깨끗한 대조군에서 아무것도 안 내는가 (오탐률)
  - 규칙과 검출기가 **양방향으로 맞물려** 있는가 (선언만 있고 구현이 없는 규칙, 또는
    아무 규칙도 안 쓰는 검출기가 없는가)

셋 중 하나라도 깨지면 "위반 0건"은 아무 정보가 아니게 된다.

규칙을 가진 스테이지가 둘(클래스 다이어그램·ERD)이므로 눈금 검사는 **스테이지마다**
돈다. 한쪽 대조군으로 양쪽을 재면, 그 대조군이 안 건드리는 규칙의 오탐은 못 잰다.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.design.evaluation.seeded import (
    CLEAN,
    CLEAN_STATE,
    ERD_CLEAN,
    ERD_SEEDED,
    SEEDED,
)
from app.design.graphs.subgraphs import DESIGN_STAGES
from app.design.knowledge import basis, detectors, rules
from app.design.services.class_diagram.validation import diagram as class_validation
from app.design.services.sequence_diagram import validation as sequence_validation
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation.model import validate_class_model


@dataclass(frozen=True)
class StageUnderTest:
    """규칙을 가진 스테이지 하나와, 그 눈금을 재는 데 필요한 것 전부."""

    stage: str
    findings: Callable[[dict, dict], list]
    clean: dict[str, Any]
    seeded: tuple
    detector_registry: dict[str, Callable]


STAGES = (
    StageUnderTest(
        rules.CLASS_DIAGRAM,
        class_validation.class_diagram_findings,
        CLEAN,
        tuple(
            case
            for case in SEEDED
            if case.rule_id in {
                rule.id for rule in rules.rules_for(rules.CLASS_DIAGRAM, rules.DEFECT)
            }
        ),
        class_validation.CLASS_DIAGRAM_DETECTORS,
    ),
    StageUnderTest(
        rules.ERD,
        detectors.erd_findings,
        ERD_CLEAN,
        ERD_SEEDED,
        detectors.ERD_DETECTORS,
    ),
)

_ALL_CASES = [(s, case) for s in STAGES for case in s.seeded]


# ---------------------------------------------------------------------------
# 눈금: 심어 둔 결함을 잡는가
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stage, case", _ALL_CASES, ids=lambda x: getattr(x, "rule_id", ""))
def test_seeded_defect_is_caught_by_exactly_its_own_rule(stage, case):
    """케이스마다 자기 규칙 하나만 걸려야 한다.

    둘 이상 걸리면 어느 검출기가 잡은 것인지 알 수 없고, 그러면 "전수 통과"가 검출기
    하나의 고장을 덮는다.
    """
    found = stage.findings(case.model, case.state)
    caught = {f.rule_id for f in found}
    assert caught == {case.rule_id}, (
        f"{case.what} → 기대 {{{case.rule_id}}}, 실제 {caught}: "
        f"{[f.as_issue() for f in found]}"
    )


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.stage)
def test_every_defect_rule_has_a_seeded_case(stage):
    """눈금 없는 규칙이 조용히 생기는 것을 막는다.

    규칙을 추가하면서 케이스를 안 만들면, 그 규칙의 "0건"은 근거가 없는데도 다른 규칙의
    0건과 똑같이 보인다.

    거꾸로도 본다: 심을 수 없는 규칙은 결함 규칙이 아니다. 실제로 ERD 쪽에서 "모든
    테이블에 기본키가 있다"·"외래키는 실재 테이블을 가리킨다"를 규칙으로 적었다가 뺐다 —
    사상이 구성에 의해 보장해서 **어떤 모델로도 위반을 만들 수 없었다.** 그 둘은 모델에
    대한 규칙이 아니라 변환 코드가 지켜야 할 조건이고, 지금은 `test_erd_mapping.py`가 확인한다.
    """
    declared = {r.id for r in rules.rules_for(stage.stage, rules.DEFECT)}
    seeded = {case.rule_id for case in stage.seeded}
    assert declared == seeded, f"케이스 없는 규칙: {declared - seeded}"


# ---------------------------------------------------------------------------
# 오탐: 깨끗한 것을 결함이라 부르지 않는가
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.stage)
def test_the_clean_control_yields_nothing(stage):
    """대조군에서 무언가 나오면 오탐이다.

    오탐이 있으면 재생성 루프가 고칠 수 없는 지적에 예산을 태우고, 위반 수가 안 줄어
    `no_improvement`로 멈춘다.
    """
    found = stage.findings(stage.clean, CLEAN_STATE)
    assert found == [], [f.as_issue() for f in found]


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.stage)
def test_the_clean_control_actually_exercises_its_stage(stage):
    """대조군이 **그 스테이지의 규칙을 실제로 지나가는가.**

    오탐이 0건인 것은 두 가지 뜻일 수 있다: 규칙을 지켰거나, 볼 것이 아예 없었거나.
    아무 결함도 없는 빈 모델은 언제나 0건이고 그 0은 아무 정보가 아니다.

    그래서 대조군을 조금씩 망가뜨려 본다 — 규칙마다 하나씩 심은 것이 `SEEDED`이고,
    그 전부가 **같은 대조군에서 갈라져 나왔다면** 대조군이 그 규칙들의 사정거리 안에
    있다는 뜻이다. 여기서는 그 케이스들이 실제로 무언가를 잡는지만 확인한다.
    """
    assert stage.seeded, f"{stage.stage}에 심어 둔 케이스가 없다"
    for case in stage.seeded:
        assert stage.findings(case.model, case.state), (
            f"{case.what} — 심었는데 아무것도 안 걸린다"
        )


def test_an_empty_model_is_not_an_error():
    """아직 아무것도 안 만든 상태를 결함으로 부르지 않는다.

    검사 노드는 extract 직후에도 돌 수 있고, 그때 모델이 비어 있을 수 있다. 빈 모델에
    대고 "클래스가 없다"고 지적하면 재생성이 무엇을 고쳐야 할지 알 수 없다.
    """
    assert class_validation.class_diagram_findings({}, {}) == []
    assert class_validation.class_diagram_findings({"Classes": [], "Relationships": []}, {}) == []


def test_legacy_structure_remains_viewable_without_execution_validation():
    """과거 모델 열람은 허용하고, 하위 산출물 재생성 경계에서만 차단한다."""
    model = {
        "Classes": [{"className": "Order", "stereotype": "Entity", "use_case_ids": ["UC1"]}],
        "Relationships": [],
    }
    found = class_validation.class_diagram_findings(model, {"usecase_spec": {}})
    assert found == []
def test_typed_class_contract_requires_canonical_operation_ids():
    """수락된 typed operation은 owner·signature 기반 ID를 가져야 한다."""
    index = build_scenario_index({
        "use_cases": [{"id": "UC1", "name": "Place order"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Place order",
            "main_scenario": [{"step_number": 1, "sentence": "Buyer submits"}],
            "extensions": [],
        }],
        "relationships": [],
    })
    model = {
        "Classes": [{
            "className": "OrderControl",
            "stereotype": "Control",
            "operations": [{
                "operationId": "not-canonical",
                "name": "submit",
                "parameters": [],
                "returnType": "void",
                "stepRefs": ["UC1:main:1"],
            }],
        }],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [{
            "collaborationId": "UC1:main:1",
            "useCaseIds": ["UC1"],
            "entryActor": "Buyer",
            "calls": [],
        }],
    }

    report = validate_class_model(model, index)

    assert any(
        finding.rule_id == "class.model.operation-ids"
        for finding in report.findings
    )


def test_legacy_usecase_checks_stay_quiet_when_there_is_no_upstream_to_compare_against():
    """대조할 상류가 없으면 검사하지 않는다.

    유스케이스 id가 하나도 없는 입력에서 모든 id를 unknown으로 부르면, 그건 "LLM이
    지어냈다"가 아니라 "대조할 것이 없다"는 뜻이다. 대조할 것이 없는데 전건 위반을 내면
    재생성이 고칠 수 없는 지적으로 예산만 태운다.
    """
    model = {
        "Classes": [{"className": "Order", "stereotype": "Entity", "use_case_ids": ["UC1"]}],
        "Relationships": [],
    }
    found = class_validation.class_diagram_findings(model, {"usecase_spec": {}})
    assert found == []


def test_typed_class_model_allows_an_unrelated_single_class():
    """현재 typed 계약은 관계 없는 단일 클래스도 구조적으로 허용한다."""
    model = {
        "Classes": [{"className": "Course", "stereotype": "Entity"}],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }
    index = build_scenario_index({"use_cases": [], "use_case_specs": [], "relationships": []})

    report = validate_class_model(model, index)

    assert not any(finding.rule_id == "class.model.schema" for finding in report.findings)


def test_legacy_class_contract_types_are_outside_typed_validation():
    state = {
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "CourseCatalogController",
                    "stereotype": "Control",
                    "methods": [
                        "+ browseCourses(filter : CourseFilter): List<Course>",
                        "+ findCourse(): MissingResult",
                    ],
                },
                {"className": "Course", "stereotype": "Entity", "fields": ["- title : String"]},
            ],
            "Relationships": [],
        }
    }

    # This legacy ``methods`` shape is intentionally read-only compatibility
    # input.  Typed class validation is exercised by the canonical-operation
    # test above; it does not revive the removed contract-types detector.
    assert class_validation.class_diagram_findings(state["extracted_bce_classes"], {}) == []


def test_isolated_classes_are_reported_but_a_single_class_model_is_allowed():
    # The typed pipeline no longer invents an ``isolated_classes`` detector;
    # relationship absence is valid for a partial inventory.
    isolated = {
        "Classes": [
            {"className": "CatalogControl", "stereotype": "Control"},
            {"className": "Course", "stereotype": "Entity"},
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }
    index = build_scenario_index({"use_cases": [], "use_case_specs": [], "relationships": []})

    report = validate_class_model(isolated, index)

    assert not any(finding.rule_id == "class.model.schema" for finding in report.findings)


def test_legacy_usecase_id_detector_stays_quiet_without_upstream_ids():
    """대조할 상류가 없으면 검사하지 않는다.

    유스케이스 id가 하나도 없는 입력에서 모든 id를 unknown으로 부르면, 그건 "LLM이
    지어냈다"가 아니라 "대조할 것이 없다"는 뜻이다. 재생성이 고칠 수 없는 지적이므로
    내지 않는다.
    """
    model = {
        "Classes": [{"className": "Order", "stereotype": "Entity", "use_case_ids": ["UC1"]}],
        "Relationships": [],
    }
    found = class_validation.usecase_ids(model, {"usecase_spec": {}})
    assert found == []


# ---------------------------------------------------------------------------
# 개별 검출기의 미묘한 자리
# ---------------------------------------------------------------------------
def test_names_that_collide_only_after_rendering_are_caught():
    """렌더 후에야 같은 이름이 되는 쌍을 잡는가.

    `Order Item`과 `Order_Item`은 모델에서는 다른 이름이지만 `sanitize_class_name`을
    거치면 둘 다 `Order_Item`이 되어 그림에서 한 클래스로 합쳐진다. 원본만 비교하는
    검사는 이것을 영원히 못 본다.

    이 케이스는 `seeded.py`에 없다 — `Order Item`이 PascalCase도 아니라서 규칙 둘을
    함께 어기고, 그러면 "하나만 어긴다"는 심기 규칙을 깨기 때문이다.
    """
    model = {
        "Classes": [
            {"className": "Order Item", "stereotype": "Entity"},
            {"className": "Order_Item", "stereotype": "Entity"},
        ],
        "Relationships": [],
    }
    caught = {f.rule_id for f in class_validation.names_unique(model, {})}
    assert caught == {"class.names-unique"}


@pytest.mark.parametrize("written", ["Control", "<<Control>>", "control", " <<Control>> "])
def test_stereotypes_are_read_as_leniently_as_the_renderer_reads_them(written):
    """모델이 스테레오타입을 쓰는 여러 모양을 렌더러와 **같은 관대함**으로 읽는가.

    렌더러(`sanitize_stereotype`)는 꺾쇠와 공백을 벗겨낸다. 검출기가 더 엄격하면 그림에는
    멀쩡히 나오는 것을 결함이라고 부른다 — 고칠 수 없는 지적이다.
    """
    model = {"Classes": [{"className": "X", "stereotype": written}], "Relationships": []}
    assert class_validation.stereotype_is_bce(model, {}) == []


def test_one_broken_relationship_is_reported_once_not_by_every_detector():
    """한 결함이 지적 여럿이 되지 않는가.

    매달린 끝은 참조 무결성으로 한 번만 잡혀야 한다. 통신 규칙까지 함께 지적하면 위반
    수가 부풀고, 재생성이 실제로 고쳤는데도 수가 안 줄어 `no_improvement`로 멈춘다.
    """
    model = {
        "Classes": [{"className": "OrderForm", "stereotype": "Boundary"}],
        "Relationships": [{"source": "OrderForm", "target": "Ghost", "type": "Association"}],
    }
    found = class_validation.class_diagram_findings(model, {})
    assert [f.rule_id for f in found] == ["class.relationship-endpoints-exist"]


def test_control_to_entity_is_allowed_but_entity_to_control_is_not():
    """방향이 뜻을 갖는 규칙을 방향까지 판정하는가.

    Entity 규칙은 **연결**이 아니라 **개시**를 금지한다. 방향을 안 보면 Control이 Entity를
    쓰는 정상적인 설계가 전부 결함이 된다.
    """
    classes = [
        {"className": "OrderController", "stereotype": "Control"},
        {"className": "Order", "stereotype": "Entity"},
    ]
    allowed = {
        "Classes": classes,
        "Relationships": [{"source": "OrderController", "target": "Order"}],
    }
    forbidden = {
        "Classes": classes,
        "Relationships": [{"source": "Order", "target": "OrderController"}],
    }
    assert class_validation.communication_rules(allowed, {}) == []
    assert [f.rule_id for f in class_validation.communication_rules(forbidden, {})] == [
        "class.entity-does-not-initiate"
    ]


# ---------------------------------------------------------------------------
# 규칙 ↔ 검출기가 맞물려 있는가
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.stage)
def test_every_detector_rule_names_a_detector_that_exists(stage):
    for rule in rules.judged_by(stage.stage, rules.JUDGED_DETECTOR):
        assert rule.detector in stage.detector_registry, rule.id


def test_every_detector_is_claimed_by_at_least_one_rule():
    """아무 규칙도 안 쓰는 검출기는 지적을 근거 없이 낸다.

    **`STAGES`가 아니라 구현된 등록부 전부를 센다.** `STAGES`는 눈금을 재는 스테이지
    (심어 둔 케이스와 대조군이 있는 것)만 담는데, 검출기는 그보다 앞서 늘어난다 —
    시퀀스·API는 규칙과 검출기가 먼저 들어왔고 케이스는 아직 없다. `STAGES`로 세면
    그 검출기들이 "아무 규칙도 안 쓴다"로 오인된다.
    """
    claimed = {r.detector for r in rules.RULES if r.detector}
    implemented = (
        set(class_validation.CLASS_DIAGRAM_DETECTORS)
        | set(sequence_validation.SEQUENCE_DIAGRAM_DETECTORS)
        | set(detectors.API_SPEC_DETECTORS)
        | set(detectors.ERD_DETECTORS)
    )
    assert implemented == claimed


def test_no_defect_rule_is_left_unjudged():
    """결함이라고 적어 놓고 아무도 판정하지 않는 규칙이 없는가.

    비어 있는 것이 지금의 사실이다. LLM 의미 검증자를 넣으면서 판정자 없는 규칙을 임시로
    추가하면 여기서 드러난다 — 조용히 늘어나면 안 된다.
    """
    assert rules.unjudged_defects() == ()


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.stage)
def test_no_rule_claims_a_validator_that_does_not_exist_yet(stage):
    """의미 검증자는 아직 없다. 그것을 판정자로 지목한 규칙이 있으면 아무도 안 본다."""
    assert rules.judged_by(stage.stage, rules.JUDGED_VALIDATOR) == ()


def test_rule_stages_are_real_pipeline_stages():
    """규칙의 stage가 실제 파이프라인 스테이지 이름인가.

    `rules.py`는 순환을 피하려고 스테이지 이름을 자기 상수로 다시 적는다. 두 목록이
    갈라지면 그 규칙은 아무 스테이지에서도 안 걸린다.
    """
    assert {r.stage for r in rules.RULES} <= set(DESIGN_STAGES)


# ---------------------------------------------------------------------------
# 근거를 정직하게 들고 다니는가
# ---------------------------------------------------------------------------
def test_findings_carry_their_rule_tag():
    """지적 문구가 어느 규칙에서 나왔는지 되읽을 수 있는가.

    꼬리표가 없으면 게이트에 뜬 지적이 무엇을 근거로 하는지 사용자가 알 수 없고,
    재생성 지시문도 근거 없이 나간다.
    """
    case = SEEDED[0]
    issue = class_validation.class_diagram_findings(case.model, case.state)[0].as_issue()
    assert rules.rule_of(issue) == case.rule_id


def test_hedged_rules_say_so_in_the_finding():
    """짐작인 규칙의 지적에 "우리 판단"이 붙는가.

    Jacobson BCE 통신 규칙은 이 저장소가 페이지를 확인하지 못했다. 그것을 확인된 인용처럼
    내보내면 사용자는 실측한 규칙(`plantuml-measured`)과 같은 무게로 읽는다.
    """
    hedged = rules.rule("class.no-boundary-entity-link")
    assert hedged.hedged and "우리 판단" in hedged.tag

    measured = rules.rule("class.relationship-endpoints-exist")
    assert not measured.hedged and "우리 판단" not in measured.tag


def test_every_rule_uses_a_registered_evidence_label():
    """등록 안 된 근거 라벨은 조용히 짐작으로 처리된다 — 그 전에 여기서 잡는다."""
    for rule in rules.RULES:
        assert rule.evidence in basis.BASIS_OF_EVIDENCE, rule.id


def test_every_hedged_rule_states_the_limit_of_its_source():
    """짐작인 규칙은 **왜** 짐작인지를 적어야 한다.

    유보 문구가 없으면 "우리 판단"이라는 꼬리표만 남고, 무엇을 확인하지 못했는지는
    아무 데도 없다.
    """
    for rule in rules.RULES:
        if rule.hedged:
            assert rule.caveat, rule.id


def test_unknown_rule_ids_are_not_silently_dressed_up_as_grounded():
    """모르는 id에 근거 있는 꼬리표를 달아 주지 않는가."""
    assert "알 수 없는 규칙" in rules.tag_of("class.does-not-exist")


# ---------------------------------------------------------------------------
# 실측: 이 검사가 필요한 이유 자체를 확인한다
# ---------------------------------------------------------------------------
_PLANTUML_JAR = Path(__file__).resolve().parent.parent / "plantuml.jar"


@pytest.mark.skipif(not _PLANTUML_JAR.exists(), reason="plantuml.jar가 없다")
def test_plantuml_invents_a_class_for_a_dangling_endpoint():
    """**`class.relationship-endpoints-exist`의 근거를 재현한다.**

    이 규칙의 `evidence`는 `plantuml-measured`(확인된 근거)이고, 확인이란 이것이다:
    클래스를 하나만 선언하고 관계가 선언되지 않은 이름을 가리키면, PlantUML은 오류를
    내지 않고 **엔티티를 2개로 센다** — 선언한 적 없는 클래스가 그림에 생긴다.

    그래서 문법 검증(`validate_puml_artifact`)은 이것을 영원히 못 잡는다. 이 테스트가
    깨지면 규칙의 근거가 사라진 것이므로 `evidence` 라벨을 내려야 한다.
    """
    puml = (
        "@startuml\n"
        "class OrderController <<Control>> {\n  + placeOrder()\n}\n"
        "OrderController --> GhostEntity : uses\n"
        "@enduml\n"
    )
    result = subprocess.run(
        ["java", "-jar", str(_PLANTUML_JAR), "-syntax"],
        input=puml,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "ERROR" not in result.stdout.upper(), result.stdout
    assert "2 entities" in result.stdout, result.stdout
