"""설계 규칙 등록과 대표적인 validator 동작을 확인한다."""

from __future__ import annotations

import copy

from app.design.graphs.subgraphs import DEPLOYMENT_DIAGRAM_DETECTORS, DESIGN_STAGES
from app.design.knowledge import basis, detectors, rules
from app.design.services.class_diagram.validation import diagram as class_validation
from app.design.services.sequence_diagram import validation as sequence_validation
from tests.design_validation_fixtures import CLEAN, CLEAN_STATE, ERD_CLEAN, unmapped_erd

DETECTOR_REGISTRIES = {
    rules.CLASS_DIAGRAM: class_validation.CLASS_DIAGRAM_DETECTORS,
    rules.SEQUENCE_DIAGRAM: sequence_validation.SEQUENCE_DIAGRAM_DETECTORS,
    rules.API_SPEC: detectors.API_SPEC_DETECTORS,
    rules.ERD: detectors.ERD_DETECTORS,
    rules.DEPLOYMENT_DIAGRAM: DEPLOYMENT_DIAGRAM_DETECTORS,
}


def test_clean_class_and_erd_models_have_no_findings() -> None:
    """대표 정상 모델이 클래스와 ERD 검사에서 모두 통과한다."""
    assert class_validation.class_diagram_findings(CLEAN, CLEAN_STATE) == []
    assert detectors.erd_findings(ERD_CLEAN, CLEAN_STATE) == []


def test_representative_reference_and_mapping_errors_use_expected_rules() -> None:
    """선언되지 않은 클래스 참조와 ERD 사상 실패를 각 규칙이 찾아낸다."""
    broken_class = copy.deepcopy(CLEAN)
    broken_class["Relationships"].append(
        {"source": "OrderController", "target": "GhostEntity", "type": "Dependency"}
    )

    class_rule_ids = {
        finding.rule_id
        for finding in class_validation.class_diagram_findings(
            broken_class, CLEAN_STATE
        )
    }
    erd_rule_ids = {
        finding.rule_id
        for finding in detectors.erd_findings(unmapped_erd(), CLEAN_STATE)
    }

    assert class_rule_ids == {"class.relationship-endpoints-exist"}
    assert "erd.relationship-mapped" in erd_rule_ids


def test_empty_class_model_is_valid_before_generation_starts() -> None:
    """아직 생성 전인 빈 모델을 결함으로 오인하지 않는다."""
    assert class_validation.class_diagram_findings({}, {}) == []
    assert class_validation.class_diagram_findings(
        {"Classes": [], "Relationships": []}, {}
    ) == []


def test_names_that_collide_after_rendering_are_rejected() -> None:
    """PlantUML에서 같은 이름이 되는 클래스 두 개를 미리 찾는다."""
    model = {
        "Classes": [
            {"className": "Order Item", "stereotype": "Entity"},
            {"className": "Order_Item", "stereotype": "Entity"},
        ],
        "Relationships": [],
    }

    assert {
        finding.rule_id for finding in class_validation.names_unique(model, {})
    } == {"class.names-unique"}


def test_renderer_style_stereotype_is_accepted_by_validator() -> None:
    """렌더러가 허용하는 꺾쇠와 공백을 validator도 같은 뜻으로 읽는다."""
    model = {
        "Classes": [{"className": "OrderControl", "stereotype": " <<Control>> "}],
        "Relationships": [],
    }

    assert class_validation.stereotype_is_bce(model, {}) == []


def test_entity_cannot_start_a_call_to_control() -> None:
    """Control→Entity는 허용하고 반대 방향은 BCE 방향 규칙으로 막는다."""
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
    assert [
        finding.rule_id
        for finding in class_validation.communication_rules(forbidden, {})
    ] == ["class.entity-does-not-initiate"]


def test_every_detector_rule_points_to_a_registered_detector() -> None:
    """detector로 판정한다고 선언한 규칙에 실제 함수가 연결돼 있다."""
    for stage, registry in DETECTOR_REGISTRIES.items():
        for rule in rules.judged_by(stage, rules.JUDGED_DETECTOR):
            assert rule.detector in registry, rule.id


def test_every_registered_detector_is_claimed_by_a_rule() -> None:
    """근거 규칙 없이 finding을 만드는 detector가 없도록 한다."""
    claimed = {rule.detector for rule in rules.RULES if rule.detector}
    implemented = set().union(*DETECTOR_REGISTRIES.values())

    assert implemented == claimed


def test_rule_registry_uses_real_stages_and_has_no_unjudged_defects() -> None:
    """규칙의 stage와 판정 방식이 실제 설계 파이프라인과 맞아야 한다."""
    assert {rule.stage for rule in rules.RULES} <= set(DESIGN_STAGES)
    assert rules.unjudged_defects() == ()


def test_finding_keeps_its_rule_id_and_registered_evidence() -> None:
    """finding에서 규칙과 등록된 근거 종류를 다시 찾을 수 있어야 한다."""
    broken = copy.deepcopy(CLEAN)
    broken["Relationships"].append(
        {"source": "OrderController", "target": "GhostEntity", "type": "Dependency"}
    )
    issue = class_validation.class_diagram_findings(broken, CLEAN_STATE)[0].as_issue()

    assert rules.rule_of(issue) == "class.relationship-endpoints-exist"
    for rule in rules.RULES:
        assert rule.evidence in basis.BASIS_OF_EVIDENCE, rule.id
        if rule.hedged:
            assert rule.caveat, rule.id


def test_unknown_rule_id_is_not_presented_as_grounded() -> None:
    """등록되지 않은 ID에 확인된 근거처럼 보이는 꼬리표를 붙이지 않는다."""
    assert "알 수 없는 규칙" in rules.tag_of("class.does-not-exist")
