"""ERD 검사 노드의 계약 (네트워크 불필요).

루프의 산술 자체는 `test_class_diagram_check.py`가 이미 고정했다. 여기서 보는 것은
**ERD에만 있는 것들**이다:

  - 판정 대상이 BCE 하나가 아니라 **BCE와 그것을 사상한 논리 데이터 모델 둘**이다
  - 빈 산출물의 귀속 — 예전에는 렌더러 탓으로 기록됐다
  - 재생성이 모델을 비워서 위반을 없애는 길이 막혀 있는가.
    `ERD_SPEC.elements`가 비어 있어 `_is_degenerate`가 그 함정을 못 막으므로,
    막는 것은 `erd.has-entity` 규칙 하나뿐이다

LLM은 스크립트 페이크로 대체한다.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from app.design.evaluation.seeded import CLEAN_STATE, ERD_CLEAN, ERD_SEEDED
from app.design.graphs.subgraphs import ERD_SPEC
from app.design.knowledge import detectors
from app.design.knowledge.detectors import erd_findings
from app.design.services.erd import mapping
from app.design.nodes.artifact import (
    CLEAN as STOPPED_CLEAN,
    NO_IMPROVEMENT,
    check_node,
    render_and_validate,
)

CHECK_KEY = ERD_SPEC.check_key
#: 다중도가 없어 사상되지 않는 관계를 심은 모델.
UNMAPPED = next(c for c in ERD_SEEDED if c.rule_id == "erd.relationship-mapped").model


def _spec_with(revise):
    return dataclasses.replace(ERD_SPEC, revise=revise)


def _run(spec, model, state=None):
    return check_node(spec)({**(state or CLEAN_STATE), spec.model_key: model})


@pytest.fixture(autouse=True)
def _two_repairs(monkeypatch):
    monkeypatch.setenv("DESIGN_MAX_REPAIR_ITERS", "2")


def test_the_erd_stage_actually_has_a_check():
    """배선이 됐는가. 이게 없으면 아래 테스트는 전부 빈 스펙을 확인하는 셈이 된다."""
    assert ERD_SPEC.check_key == "erd_check"
    assert ERD_SPEC.check is erd_findings


def test_a_clean_model_is_not_sent_to_the_llm_at_all():
    def never(*args, **kwargs):
        raise AssertionError("깨끗한 모델에 재생성을 불렀다")

    out = _run(_spec_with(never), ERD_CLEAN)
    assert out[CHECK_KEY] == {"findings": [], "repair_iters": 0, "stopped": STOPPED_CLEAN}


# ---------------------------------------------------------------------------
# 두 층을 본다
# ---------------------------------------------------------------------------
def test_the_check_judges_the_mapped_data_model_not_just_the_bce():
    """"이 관계가 테이블로 옮겨졌나"는 BCE만 봐서는 물을 수 없는 질문이다.

    BCE에는 테이블도 키도 외래키도 없다. 사상을 돌려야 비로소 판정할 것이 생긴다.
    """
    out = _run(_spec_with(lambda *a: UNMAPPED), UNMAPPED)
    issues = out[CHECK_KEY]["findings"]

    assert any("erd.relationship-mapped" in i for i in issues), issues


@pytest.mark.parametrize(
    "relationships, expected",
    [
        ([{"source": "Member", "target": "Order", "type": "Association"}], "다중도가 없어"),
        ([{"source": "Member", "target": "Order", "type": "Dependency"}], "Dependency로 이었다"),
        # 구조적이지 않은 종류는 `Dependency` 말고도 있다 — 문구가 **모델이 적은 그 종류**를
        # 말해야 한다. 한동안 전부 "Dependency로 이었다"였고, 그러면 `Realization`이라고
        # 적은 사람은 자기가 안 쓴 것을 고치라는 말을 듣는다.
        ([{"source": "Member", "target": "Order", "type": "Realization"}], "Realization로 이었다"),
        (
            [{"source": "Member", "target": "Order", "type": "Inheritance"},
             {"source": "Member", "target": "Extra", "type": "Inheritance"}],
            "부모가 둘 이상",
        ),
        (
            [{"source": "Member", "target": "Order", "type": "Inheritance"},
             {"source": "Order", "target": "Member", "type": "Inheritance"}],
            "상속이 순환",
        ),
        (
            [{"source": "Member", "target": "Order", "type": "Association",
              "sourceMultiplicity": "*", "targetMultiplicity": "*"},
             {"source": "Member", "target": "Order", "type": "Association",
              "sourceMultiplicity": "*", "targetMultiplicity": "*"}],
            "연결 테이블 이름이 같아져",
        ),
    ],
    ids=["multiplicity", "dependency", "non-structural-type", "multiple-inheritance",
         "cycle", "duplicate-junction"],
)
def test_every_unmapped_reason_reaches_the_user_in_korean(relationships, expected):
    """사상 못 한 사유마다 **사람이 읽을 말**이 붙어 있는가.

    검출기는 모르는 사유를 만나면 `str(reason)`을 그대로 내보낸다. 그러면
    `multiple-inheritance` 같은 영어 슬러그가 게이트 화면에 뜬다. 사유를 늘리면서 문구를
    빠뜨리기 쉬운 자리라 사유마다 하나씩 고정한다.
    """
    model = copy.deepcopy(ERD_CLEAN)
    model["Classes"].append(
        {"className": "Extra", "stereotype": "Entity", "description": "",
         "fields": ["x : Int"], "identifier": [], "methods": [], "use_case_ids": ["UC1"]}
    )
    model["Relationships"] = relationships

    issues = [f.as_issue() for f in erd_findings(model, CLEAN_STATE)]
    mapped = [i for i in issues if "erd.relationship-mapped" in i]

    assert mapped, issues
    assert any(expected in i for i in mapped), mapped


def test_every_unmapped_reason_the_mapping_can_emit_has_prose():
    """위의 것이 **사유를 하나씩** 고정한다면 이것은 사유를 **빠짐없이** 고정한다.

    앞의 테스트는 새 사유를 추가하면서 파라미터를 안 늘리면 조용히 통과한다 — 사유가
    늘어나는 곳(`mapping.py`)과 문구가 붙는 곳(`detectors.py`)이 다른 파일이라 실제로
    빠뜨리기 쉽다. 여기서는 사상이 낼 수 있는 사유를 전수해 대조하므로 빠뜨릴 수가 없다.

    사상 쪽 상수 이름 규약(`UNMAPPED_*`)에 기대는 것이 이 테스트의 유일한 약점인데,
    그 규약은 `mapping.py`가 "소비자가 한 곳에서 다 가져가야 한다"며 재수출까지 하는
    것이라 이미 계약이다.
    """
    emitted = {
        value
        for name, value in vars(mapping).items()
        if name.startswith("UNMAPPED_") and isinstance(value, str)
    }

    assert emitted, "사상에서 UNMAPPED_* 상수를 하나도 못 찾았다 — 이름 규약이 바뀌었나"
    assert emitted <= set(detectors.UNMAPPED_PROSE), (
        "문구 없는 사유: " + ", ".join(sorted(emitted - set(detectors.UNMAPPED_PROSE)))
    )


@pytest.mark.parametrize(
    "relationships, expected_cycle",
    [
        # 자기 참조 — "모든 주문에는 원주문이 있다". 첫 주문의 원주문이 없다.
        ([("Order", "Order", "1", "*")], True),
        # 두 표가 서로를 필수로 가리킨다 — 어느 쪽도 먼저 못 넣는다.
        ([("Member", "Order", "1", "*"), ("Order", "Member", "1", "*")], True),
        # 참조되는 끝이 선택이면 뿌리를 넣을 수 있다 — 정상이다.
        ([("Order", "Order", "0..1", "*")], False),
        # 한 방향만 필수인 사슬은 순환이 아니다.
        ([("Member", "Order", "1", "*")], False),
    ],
    ids=["self-mandatory", "mutual-mandatory", "self-optional", "one-way"],
)
def test_a_cycle_of_mandatory_references_is_reported(relationships, expected_cycle):
    """행을 하나도 넣을 수 없는 스키마를 조용히 내보내지 않는가.

    **이 검사는 4차 수정이 열어 놓은 자리를 막는다.** 그전에는 외래키가 합성일 때만
    필수였는데, 널 허용을 다중도에서 끌어오면서 `Emp "1" — "*" Emp` 같은 평범해 보이는
    모델이 필수 자기 참조가 됐다. 각 행이 존재하려면 다음 행이 이미 있어야 하므로
    첫 행이 영원히 안 들어간다.

    널 허용으로 풀어 주지 않는다 — 모델이 적은 `"1"`을 우리가 뒤집는 것이 되기 때문이다.
    드러내고, 고칠지는 재생성과 사용자가 정한다.
    """
    model = copy.deepcopy(ERD_CLEAN)
    model["Relationships"] = [
        {"source": s, "target": t, "type": "Association",
         "sourceMultiplicity": sm, "targetMultiplicity": tm}
        for s, t, sm, tm in relationships
    ]

    issues = [f.as_issue() for f in erd_findings(model, CLEAN_STATE)]
    cycle_safety_findings = [
        issue for issue in issues
        if "erd.no-mandatory-reference-cycle" in issue
        or "삽입 불가능한 참조 순환" in issue
    ]

    assert bool(cycle_safety_findings) is expected_cycle, issues


def test_one_cycle_is_one_finding_not_one_per_edge():
    """한 고리에 지적 하나만 낸다.

    간선마다 내면 한 실수가 여러 지적이 되고, 재생성이 실제로 고쳐도 위반 수가 기대만큼
    안 줄어 수정본이 `no_improvement`로 버려질 수 있다.
    """
    model = copy.deepcopy(ERD_CLEAN)
    model["Relationships"] = [
        {"source": "Member", "target": "Order", "type": "Association",
         "sourceMultiplicity": "1", "targetMultiplicity": "*"},
        {"source": "Order", "target": "Member", "type": "Association",
         "sourceMultiplicity": "1", "targetMultiplicity": "*"},
    ]

    cycle_safety_findings = [
        finding for finding in erd_findings(model, CLEAN_STATE)
        if finding.rule_id == "erd.no-mandatory-reference-cycle"
        or "삽입 불가능한 참조 순환" in finding.message
    ]

    assert len(cycle_safety_findings) == 1, [f.as_issue() for f in cycle_safety_findings]


def test_a_contradictory_composition_is_surfaced_not_silently_resolved():
    """종류와 다중도가 서로 다른 말을 하면 **고르지 않고 드러낸다.**

    합성은 *"부분은 전체 없이 존재할 수 없다"*는 선언인데 전체 쪽이 `0..1`이면 정면으로
    어긋난다. 사상이 한쪽으로 정리할 수도 있지만 **어느 쪽이 의도인지 우리가 모른다** —
    정리하면 모델이 적은 것을 우리가 덮는 것이 된다.

    그래서 모순을 가진 채 옮기고 검사가 말한다. 고치는 것은 재생성의 몫이다.
    """
    model = copy.deepcopy(ERD_CLEAN)
    model["Relationships"][1]["type"] = "Composition"
    model["Relationships"][1]["sourceMultiplicity"] = "0..1"

    issues = [f.as_issue() for f in erd_findings(model, CLEAN_STATE)]

    assert any("erd.composition-owner-is-mandatory" in i for i in issues), issues
    # 사상은 멈추지 않는다 — 관계는 그대로 옮겨진다. 드러내기만 한다.
    assert not any("erd.relationship-mapped" in i for i in issues), issues


def test_findings_carry_the_rule_and_its_basis():
    """지적이 어느 규칙에서 나왔는지, 그 근거가 확인된 것인지가 함께 간다."""
    issue = erd_findings(UNMAPPED, CLEAN_STATE)[0].as_issue()

    assert "erd.relationship-mapped" in issue
    assert "app/design/services/erd/mapping.py" in issue


# ---------------------------------------------------------------------------
# 빈 산출물의 귀속
# ---------------------------------------------------------------------------
def test_an_empty_erd_is_blamed_on_the_model_not_on_the_renderer():
    """Entity가 없으면 **모델의 결함**이라고 적힌다.

    예전에는 이것이 `erd_syntax_valid=False`로만 나타났다. 그런데 그 칸의 뜻은 "우리
    렌더러가 깨졌다"이고(`nodes/artifact.py`의 `render_node`), 그 실패에는 재생성이
    정확히 틀린 대응이다 — LLM은 우리 렌더러를 못 고친다. 원인이 모델이므로 검사가
    잡아야 귀속이 맞고, 재생성이 고칠 기회도 생긴다.
    """
    no_entity = next(c for c in ERD_SEEDED if c.rule_id == "erd.has-entity").model

    # 문법 쪽은 여전히 운다 — 빈 문자열이니까. 그러나 그건 렌더러에 대한 판정이다.
    rendered = render_and_validate(ERD_SPEC, no_entity)
    assert rendered[ERD_SPEC.valid_key] is False

    issues = _run(_spec_with(lambda *a: no_entity), no_entity)[CHECK_KEY]["findings"]
    assert any("erd.has-entity" in i for i in issues), issues


def test_a_repair_that_empties_the_model_is_never_adopted():
    """모델을 비워서 위반을 없애는 길이 막혀 있는가.

    빈 모델은 대부분의 검사를 통과한다 — 검사할 것이 없으니 위반도 없다. 클래스 쪽은
    `_is_degenerate`가 이 함정을 막는데, 그건 `spec.elements`를 세는 방식이라 ERD에는
    안 통한다(ERD는 지목 수정을 안 받아 `elements`가 비어 있다).

    막는 것은 `erd.has-entity` 하나다: 비우면 위반이 **늘어나** 위반 수가 안 줄고,
    그러면 수용 조건에서 후보가 버려진다.
    """
    out = _run(_spec_with(lambda *a: {"Classes": [], "Relationships": []}), UNMAPPED)

    assert out[CHECK_KEY]["stopped"] == NO_IMPROVEMENT
    # 버려졌으므로 원래 모델이 그대로 남는다.
    assert out[ERD_SPEC.model_key]["Classes"] == UNMAPPED["Classes"]


def test_a_repair_that_supplies_the_multiplicity_is_adopted():
    """재생성이 실제로 고칠 수 있는 지적인가 — 고칠 수 없는 지적은 예산만 태운다."""
    out = _run(_spec_with(lambda *a: ERD_CLEAN), UNMAPPED)

    assert out[CHECK_KEY]["stopped"] == STOPPED_CLEAN
    assert out[CHECK_KEY]["repair_iters"] == 1
    assert out[ERD_SPEC.model_key] == ERD_CLEAN


def test_the_repair_prompt_carries_the_rule_not_just_the_complaint():
    """재생성 지시문에 규칙 꼬리표가 실려 가는가.

    근거를 숨기고 고치라고 하면 모델은 규칙을 지키는 대신 지적 문구를 피하는 쪽으로 고친다.
    """
    seen = []

    def capture(current, feedback, state, targets):
        seen.append(feedback)
        return ERD_CLEAN

    _run(_spec_with(capture), UNMAPPED)

    assert "erd.relationship-mapped" in seen[0]
