"""규칙 지식베이스의 불변식 — 규칙이 조용히 근거를 잃지 않도록.

이 파일이 지키는 것은 규칙의 **내용**이 아니라 규율이다:
  - 짐작인 규칙은 출처의 한계를 반드시 적는다.
  - 인용 좌표는 라벨이 약속한 형식을 지킨다(`cockburn-page`면 페이지가 있어야 한다).
  - 규칙과 검출기는 양방향으로 맞물린다.
  - **규칙이 아닌 것**(관찰·공학 가드)은 어디에서도 강제되지 않는다.
  - 판정하는 곳이 없는 결함 규칙은 있어도 되지만, 목록이 조용히 늘어나지는 않는다.
"""
from __future__ import annotations

import pytest

from app.requirements import prompts
from app.requirements.agent import stages
from app.requirements.knowledge import basis, detectors, rules


def test_rule_ids_are_unique():
    ids = [r.id for r in rules.RULES]
    assert len(ids) == len(set(ids))


def test_every_rule_declares_known_vocabulary():
    for rule in rules.RULES:
        assert rule.severity in rules.SEVERITIES, rule.id
        assert rule.judged_by in rules.JUDGES, rule.id
        # 등록되지 않은 evidence 라벨은 `basis_of`가 조용히 짐작으로 떨어뜨린다.
        # 조용한 강등은 규율이 아니라 사고다 — 라벨은 표에 있어야 한다.
        assert rule.evidence in basis.BASIS_OF_EVIDENCE, rule.id
        assert rule.statement.strip()
        assert rule.citation.strip()


def test_inferred_rules_state_the_limit_of_their_grounding():
    """짐작인 규칙에는 caveat이 있어야 하고, 명시된 규칙에는 없어야 한다.

    caveat은 "위반을 의심하라"가 아니라 **"이 규범을 누가 정했는가"**를 밝히는 문장이다.
    명시된 규칙에 붙으면 그 구분이 흐려진다.
    """
    for rule in rules.RULES:
        if rule.hedged:
            assert rule.caveat, f"{rule.id}: 짐작인데 출처의 한계가 없다"
        else:
            assert rule.caveat is None, f"{rule.id}: 명시된 규칙에 유보가 붙어 있다"


@pytest.mark.parametrize(
    ("evidence", "needle"),
    [("cockburn-page", "p."), ("cockburn-guideline", "Guideline"), ("cockburn-chapter", "Ch.")],
)
def test_citation_matches_what_the_label_promises(evidence, needle):
    """라벨이 좌표의 종류를 약속한다. `cockburn-page`인데 페이지가 없으면 라벨이 거짓이다."""
    for rule in rules.RULES:
        if rule.evidence == evidence:
            assert needle in rule.citation, f"{rule.id}: {evidence}인데 좌표에 {needle!r}이 없다"


def test_book_citations_name_the_book():
    for rule in rules.RULES:
        if rule.from_book:
            assert rule.citation.startswith("Writing Effective Use Cases"), rule.id


def test_book_citations_are_machine_checkable():
    """도서 인용은 로컬 사본으로 대조할 수 있어야 한다 — 페이지와 열쇠 단어가 있어야.

    좌표가 없으면 그 인용은 아무도 확인하지 않는다. 손으로 옮겨 적은 인용은 틀리고,
    실제로 두 건이 틀려 있었다(`verify_citations.py` docstring).

    열쇠 단어는 소문자여야 한다 — 대조할 때 페이지 텍스트를 소문자로 낮춘다.
    """
    for rule in rules.RULES:
        if not rule.from_book or rule.evidence == "cockburn-unpinned":
            continue
        assert rule.pages, f"{rule.id}: 도서 인용인데 대조할 페이지가 없다"
        assert rule.probe, f"{rule.id}: 도서 인용인데 대조할 열쇠 단어가 없다"
        for key in rule.probe:
            assert key == key.lower(), f"{rule.id}: 열쇠 단어가 소문자가 아니다 ({key!r})"


def test_no_book_citation_is_left_unpinned():
    """지금은 모든 도서 인용의 페이지를 확인했다(2026-07-26, 로컬 사본).

    다시 `cockburn-unpinned`이 생기면 그건 "확인하지 못한 인용이 들어왔다"는 신호다.
    라벨을 없애지는 않는다 — 정말 못 찾는 경우에 정직하게 쓸 자리가 필요하다.
    """
    unpinned = [r.id for r in rules.RULES if r.evidence == "cockburn-unpinned"]
    assert unpinned == []


def test_pages_only_where_the_citation_is_from_the_book():
    """우리 규약에 페이지가 붙어 있으면 그건 책 근거로 오해된다."""
    for rule in rules.RULES:
        if not rule.from_book:
            assert rule.pages == (), rule.id
            assert rule.probe == (), rule.id


def test_rules_and_detectors_interlock_both_ways():
    """선언만 있고 구현이 없는 검출기도, 아무 규칙도 안 쓰는 검출기도 없어야 한다."""
    declared = {r.detector for r in rules.RULES if r.detector}
    assert declared == set(detectors.SPEC_DETECTORS)

    for rule in rules.RULES:
        if rule.judged_by == rules.JUDGED_DETECTOR:
            assert rule.detector, f"{rule.id}: 검출기가 판정한다면서 이름이 없다"
        else:
            assert rule.detector is None, f"{rule.id}: 검출기 이름이 있는데 판정자가 다르다"


def test_non_normative_knowledge_is_never_enforced():
    """`NON_RULE`과 `GUIDANCE`는 판정에 쓰이지 않는다.

    "스텝 3~9개"는 책의 **관찰**이고 include 힌트 상한은 공학 가드다. 이것들이 검증
    프롬프트에 섞이면 관찰이 규칙으로 승격한다 — 그러면 모델은 개수를 맞추려고 내용을
    늘리거나 자른다.
    """
    soft = [r for r in rules.RULES if r.severity in (rules.NON_RULE, rules.GUIDANCE)]
    assert soft, "이 테스트가 아무것도 지키지 않고 있다"
    for rule in soft:
        assert rule.judged_by == rules.JUDGED_NOWHERE, rule.id
        assert rule.detector is None, rule.id

    both = prompts.SPEC_VALIDATOR_SYSTEM + prompts.RELATIONSHIP_VALIDATOR_SYSTEM
    for rule in soft:
        assert rule.id not in both, f"{rule.id}: 규칙이 아닌데 검증 프롬프트에 들어 있다"


def test_validator_prompts_are_assembled_from_the_knowledge_base():
    for stage, prompt in (
        (rules.WRITE_SPECIFICATIONS, prompts.SPEC_VALIDATOR_SYSTEM),
        (rules.DRAW_DIAGRAM, prompts.RELATIONSHIP_VALIDATOR_SYSTEM),
    ):
        judged = rules.judged_by(stage, rules.JUDGED_VALIDATOR)
        assert judged, f"{stage}: 의미 검증자가 판정할 규칙이 하나도 없다"
        for rule in judged:
            assert rule.id in prompt
            assert rule.citation in prompt
        # 결정론으로 이미 잡은 것을 다시 지적하지 않도록 이름으로 알려 준다.
        for name in rules.already_checked_names(stage):
            assert name in prompt


def test_validator_prompt_discloses_how_each_rule_is_grounded():
    """우리 규약을 책의 말처럼 내보내지 않게, 프롬프트가 성격을 밝혀야 한다.

    고지는 라벨마다 달라야 한다 — "책이 말한 적 없다"(우리 규약)와 "책의 원칙이지만
    페이지를 못 댄다"를 한 문구로 뭉개면 둘 중 하나는 거짓이 된다.
    """
    hedged = [
        r for r in rules.judged_by(rules.WRITE_SPECIFICATIONS, rules.JUDGED_VALIDATOR)
        if r.hedged
    ]
    assert hedged
    for rule in hedged:
        note = basis.prompt_note(rule.evidence)
        assert note and note in prompts.SPEC_VALIDATOR_SYSTEM, rule.id

    # 같은 프롬프트 안에 서로 다른 고지가 실제로 둘 이상 있다(뭉개지 않았다는 증거).
    notes = {basis.prompt_note(r.evidence) for r in hedged}
    assert len(notes) >= 2


def test_rule_stages_match_the_pipeline():
    """규칙의 단계 이름은 파이프라인 그룹 이름이다(`agent/stages.py`가 단일 소스).

    `knowledge`는 순환을 피해 `stages`를 import하지 않는다. 그래서 두 목록이 맞는지는
    여기서 확인한다.
    """
    assert {r.stage for r in rules.RULES} <= set(stages.GROUPS)


def test_unjudged_defects_do_not_grow_silently():
    """결함이라 적어 놓고 아무도 판정하지 않는 규칙 — 지금 있는 그대로 고정한다.

    비어 있는 것이 목표가 아니다. 2단계에는 의미 검증기가 없고, 그건 사실이다. 다만
    새 규칙이 판정자 없이 슬며시 들어오면 이 테스트가 막는다.
    """
    assert [r.id for r in rules.unjudged_defects()] == ["actors.sud-is-not-an-actor"]


def test_findings_carry_their_grounding():
    """지적 문구에 규칙 id·인용이 붙고, 짐작인 규칙은 그 사실까지 붙는다."""
    stated = rules.rule("rel.failures-stay-inline-extensions")
    assert stated.tag == "[rel.failures-stay-inline-extensions · p.109 (Ch. 8, Extensions)]"

    # 예시에서 일반화한 규칙 — 목록이 완전하지 않다는 사실이 지적에 함께 간다.
    inferred = rules.rule("spec.black-box-no-ui-mechanics")
    assert "우리 판단" in inferred.tag

    # 페이지는 확인했지만 결론이 우리 것인 규칙도 같은 표시를 받는다.
    extrapolated = rules.rule("spec.consequence-is-a-guarantee")
    assert "p.83" in extrapolated.tag and "우리 판단" in extrapolated.tag


def test_unknown_rule_id_is_visible_not_silent():
    """모르는 id를 조용히 빈 꼬리표로 넘기면 근거 없는 지적이 근거 있어 보인다."""
    assert "알 수 없는 규칙" in rules.tag_of("spec.no-such-rule")


def test_detector_findings_name_the_rule_they_came_from():
    spec = {
        "trigger": "The user clicks the button on the screen",
        "main_scenario": [{"step_number": 1, "sentence": "System records the order"}],
        "extensions": [],
        "preconditions": ["the user is authenticated"],
        "success_guarantee": ["the order is recorded"],
    }
    issues = [f.as_issue() for f in detectors.spec_findings(spec)]
    assert len(issues) == 1
    assert "UI 용어" in issues[0]                              # 기존 문구는 그대로
    assert "spec.black-box-no-ui-mechanics" in issues[0]       # 근거가 함께 간다
    assert "p.209" in issues[0]
    assert "우리 판단" in issues[0]                            # 목록은 우리 일반화다
