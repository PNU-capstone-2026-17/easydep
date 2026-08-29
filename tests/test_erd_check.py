"""ERD validator와 검사 노드의 대표 성공·실패 계약."""

from __future__ import annotations

import copy
import dataclasses

import pytest

from app.design.graphs.subgraphs import ERD_SPEC
from app.design.knowledge.detectors import erd_findings
from app.design.nodes.artifact import CLEAN as STOPPED_CLEAN
from app.design.nodes.artifact import check_node, render_and_validate
from tests.design_validation_fixtures import CLEAN_STATE, ERD_CLEAN, unmapped_erd

CHECK_KEY = ERD_SPEC.check_key


def _spec_with(revise):
    return dataclasses.replace(ERD_SPEC, revise=revise, repair=revise)


def _run(spec, model):
    return check_node(spec)({**CLEAN_STATE, spec.model_key: model})


def test_erd_stage_uses_the_public_validator() -> None:
    """ERD 단계가 실제 finding 함수와 check 결과 키에 연결돼 있다."""
    assert ERD_SPEC.check_key == "erd_check"
    assert ERD_SPEC.check is erd_findings


def test_clean_erd_skips_llm_repair() -> None:
    """깨끗한 모델은 LLM을 호출하지 않고 즉시 완료한다."""
    def never(*_args, **_kwargs):
        raise AssertionError("clean ERD must not request repair")

    out = _run(_spec_with(never), ERD_CLEAN)

    assert out[CHECK_KEY]["findings"] == []
    assert out[CHECK_KEY]["repair_iters"] == 0
    assert out[CHECK_KEY]["stopped"] == STOPPED_CLEAN


def test_unmapped_relationship_is_reported_from_the_logical_model() -> None:
    """BCE 관계가 논리 데이터 모델로 옮겨지지 않으면 사상 오류로 표시한다."""
    findings = erd_findings(unmapped_erd(), CLEAN_STATE)

    mapped = [
        finding
        for finding in findings
        if finding.rule_id == "erd.relationship-mapped"
    ]
    assert len(mapped) == 1
    assert "다중도가 없어" in mapped[0].message


@pytest.mark.parametrize(
    ("source_multiplicity", "has_cycle"),
    [("1", True), ("0..1", False)],
)
def test_mandatory_self_reference_cycle_is_detected(
    source_multiplicity: str,
    has_cycle: bool,
) -> None:
    """필수 자기 참조는 막고 선택 참조는 정상 관계로 허용한다."""
    model = copy.deepcopy(ERD_CLEAN)
    model["Relationships"] = [
        {
            "source": "Order",
            "target": "Order",
            "type": "Association",
            "sourceMultiplicity": source_multiplicity,
            "targetMultiplicity": "*",
        }
    ]

    cycle_findings = [
        finding
        for finding in erd_findings(model, CLEAN_STATE)
        if "삽입 불가능한 참조 순환" in finding.message
    ]

    assert bool(cycle_findings) is has_cycle


def test_contradictory_composition_is_reported_without_dropping_relation() -> None:
    """선택 가능한 전체를 가진 Composition의 모순을 숨기지 않는다."""
    model = copy.deepcopy(ERD_CLEAN)
    model["Relationships"][1]["type"] = "Composition"
    model["Relationships"][1]["sourceMultiplicity"] = "0..1"

    rule_ids = {
        finding.rule_id for finding in erd_findings(model, CLEAN_STATE)
    }

    assert "erd.composition-owner-is-mandatory" in rule_ids
    assert "erd.relationship-mapped" not in rule_ids


def test_empty_erd_is_a_model_error() -> None:
    """Entity가 없는 결과를 렌더러 오류만으로 남기지 않고 모델 결함으로 표시한다."""
    empty = copy.deepcopy(ERD_CLEAN)
    for class_item in empty["Classes"]:
        class_item["stereotype"] = "Control"

    rendered = render_and_validate(ERD_SPEC, empty)
    findings = erd_findings(empty, CLEAN_STATE)

    assert rendered[ERD_SPEC.valid_key] is False
    assert "erd.has-entity" in {finding.rule_id for finding in findings}


def test_successful_repair_replaces_the_unmapped_model() -> None:
    """다중도를 보완한 첫 수정본이 통과하면 그 결과를 채택한다."""
    out = _run(_spec_with(lambda *_args: ERD_CLEAN), unmapped_erd())

    assert out[CHECK_KEY]["stopped"] == STOPPED_CLEAN
    assert out[CHECK_KEY]["repair_iters"] == 1
    assert out[ERD_SPEC.model_key] == ERD_CLEAN


def test_erd_finding_keeps_rule_and_mapping_source() -> None:
    """사용자가 finding에서 규칙과 사상 코드의 근거를 확인할 수 있다."""
    issue = erd_findings(unmapped_erd(), CLEAN_STATE)[0].as_issue()

    assert "erd.relationship-mapped" in issue
    assert "app/design/services/erd/mapping.py" in issue
