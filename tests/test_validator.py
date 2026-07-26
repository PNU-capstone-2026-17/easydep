"""독립 의미 검증자(`app/requirements/agent/validator.py`)의 규율.

이 파일이 지키는 것은 판정의 내용이 아니라 **검증자를 떼어 놓은 이유**들이다:
  - 검증자는 산출물만 본다(생성 프롬프트·사용자 피드백을 모른다).
  - 규칙마다 판정을 받는다 — 훑고 넘어간 것이 드러난다(early victory).
  - 근거 없는 지적은 버리고, 그때 "깨끗하다"고 하지 않는다.
  - 판정을 못 얻은 것과 결함이 없는 것을 같은 값으로 두지 않는다.
"""
from __future__ import annotations

import json

import pytest

from app.requirements.agent import validator
from app.requirements.agent.steps import step2_usecases as s2
from app.requirements.agent.steps import step3_specifications as s3
from app.requirements.common import telemetry
from app.requirements.knowledge import rules
from app.requirements.schemas import Critique, RuleVerdict

_STAGE = rules.WRITE_SPECIFICATIONS


def _rule_ids(stage: str = _STAGE) -> list[str]:
    return [r.id for r in rules.judged_by(stage, rules.JUDGED_VALIDATOR)]


def _all(stage: str = _STAGE, violated: dict[str, str] | None = None) -> Critique:
    violated = violated or {}
    return Critique(verdicts=[
        RuleVerdict(rule_id=rid, violated=rid in violated, directive=violated.get(rid, ""))
        for rid in _rule_ids(stage)
    ])


@pytest.fixture(autouse=True)
def _validator_on(monkeypatch):
    monkeypatch.setattr(validator.settings, "enable_semantic_validator", True)


def _patch(monkeypatch, result):
    """검증자의 LLM 호출을 가로챈다. `result`가 예외면 던진다."""
    captured = {}

    def fake(schema, messages):
        captured["schema"] = schema
        captured["human"] = messages[-1].content
        captured["system"] = messages[0].content
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(validator, "invoke_structured", fake)
    return captured


def test_findings_carry_the_rule_and_its_citation(monkeypatch):
    _patch(monkeypatch, _all(violated={"spec.no-scope-creep": "drop the invented capability"}))

    review = validator.review(
        _STAGE, {"trigger": "t"}, prefix="semantic", source="spec.semantic_validator"
    )

    assert review.status == validator.OK
    assert len(review.findings) == 1
    assert "[semantic]" in review.findings[0]
    assert "spec.no-scope-creep" in review.findings[0]
    assert "우리 판단" in review.findings[0]        # 우리 규약이라는 사실이 함께 간다
    assert review.unexamined == ()


def test_skipped_rules_are_counted_not_assumed_clean(monkeypatch):
    """규칙 6개 중 2개만 판정하고 깨끗하다고 하면, 그건 리뷰가 끝난 것이 아니다.

    verification subagent의 알려진 실패 모드(early victory)다. 규칙마다 판정을 받으므로
    무엇을 안 봤는지 응답에서 드러나고, 그 사실을 저하로 남긴다.
    """
    examined = _rule_ids()[:2]
    _patch(monkeypatch, Critique(verdicts=[
        RuleVerdict(rule_id=rid, violated=False) for rid in examined
    ]))

    with telemetry.run_scope("t") as stats:
        review = validator.review(
            _STAGE, {"trigger": "t"}, prefix="semantic", source="spec.semantic_validator"
        )

    assert review.findings == []
    assert review.status == validator.OK          # 판정은 받았다(부분적으로)
    assert set(review.unexamined) == set(_rule_ids()) - set(examined)

    components = [d["component"] for d in stats.as_dict()["degradations"]]
    assert "spec.semantic_validator.unexamined_rules" in components


def test_a_verdict_on_an_unknown_rule_is_dropped(monkeypatch):
    """지식베이스에 없는 규칙을 인용한 판정은 검증자가 스스로 만든 기준이다."""
    _patch(monkeypatch, Critique(verdicts=[
        RuleVerdict(rule_id="spec.invented-rule", violated=True, directive="do something")
    ]))

    with telemetry.run_scope("t") as stats:
        review = validator.review(
            _STAGE, {"trigger": "t"}, prefix="semantic", source="spec.semantic_validator"
        )

    assert review.findings == []
    # 버리고 남은 것이 없다 → "결함 없음"이 아니라 "판정을 얻지 못함"이다.
    assert review.status == validator.UNGROUNDED
    components = [d["component"] for d in stats.as_dict()["degradations"]]
    assert "spec.semantic_validator.ungrounded_rule" in components


def test_no_verdicts_at_all_is_not_a_pass(monkeypatch):
    _patch(monkeypatch, Critique(verdicts=[]))
    review = validator.review(
        _STAGE, {"trigger": "t"}, prefix="semantic", source="spec.semantic_validator"
    )
    assert review.status == validator.UNGROUNDED


def test_a_dead_validator_is_not_a_pass(monkeypatch):
    _patch(monkeypatch, RuntimeError("NIM down"))
    review = validator.review(
        _STAGE, {"trigger": "t"}, prefix="semantic", source="spec.semantic_validator"
    )
    assert review.status == validator.FAILED
    assert review.findings == []


def test_disabled_is_not_a_pass(monkeypatch):
    monkeypatch.setattr(validator.settings, "enable_semantic_validator", False)
    called = _patch(monkeypatch, _all())
    review = validator.review(
        _STAGE, {"trigger": "t"}, prefix="semantic", source="spec.semantic_validator"
    )
    assert review.status == validator.DISABLED
    assert called == {}, "껐는데 불렀다"


def test_unvalidated_statuses_are_the_ones_that_mean_we_did_not_check():
    """`ok`·`disabled`를 '검증 못 함'에 넣으면 리포트가 거짓말을 한다(반대 방향으로)."""
    assert set(validator.UNVALIDATED) == {validator.FAILED, validator.UNGROUNDED}
    assert validator.OK not in validator.UNVALIDATED
    assert validator.DISABLED not in validator.UNVALIDATED


def test_the_validator_never_sees_the_user_feedback(monkeypatch):
    """**black-box 경계.** 검증자에게 지시를 보여주면 "지시를 따랐는가"를 보게 된다.

    우리가 물어야 하는 것은 결과물이 규칙을 지켰는지다. 그래서 생성 쪽 피드백 문구가
    검증자 프롬프트에 새지 않아야 한다.
    """
    secret = "make every step shorter and mention the audit trail"
    captured = _patch(monkeypatch, _all())

    monkeypatch.setattr(s3.settings, "enable_semantic_validator", True)
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _spec_stub())

    s3.generate_specs(
        {
            "use_cases": [{"id": "UC1", "name": "Place order", "primary_actor": "User",
                           "goal": "g", "requirement_ids": [], "nfr_ids": []}],
            "classified": [],
            "actors": [],
        },
        feedback=secret,
    )

    seen = captured["human"] + captured["system"]
    assert secret not in seen
    # 산출물은 봤어야 한다 — 아무것도 안 보여준 것과 구별한다.
    assert "records the order" in seen


def test_review_model_surfaces_what_it_could_not_examine(monkeypatch):
    """2단계 검증은 고치지 않고 표면화한다 — 무엇을 못 봤는지까지."""
    stage_rules = _rule_ids(rules.MODEL_USE_CASES)
    _patch(monkeypatch, Critique(verdicts=[]))
    monkeypatch.setattr(s2.settings, "enable_semantic_validator", True)

    out = s2.review_model({
        "actors": [{"name": "System", "description": "the app", "kind": "primary",
                    "parent_actor": None}],
        "use_cases": [{"name": "Place order", "primary_actor": "System", "level": "user_goal",
                       "goal": "g"}],
    })["model_review"]

    assert out["semantic_status"] == validator.UNGROUNDED
    assert out["unexamined_rules"] == stage_rules
    assert out["issues"] == []


def test_review_model_reports_a_violation_with_its_rule(monkeypatch):
    _patch(monkeypatch, _all(
        rules.MODEL_USE_CASES,
        {"actors.sud-is-not-an-actor": "Remove 'System' from the actor list."},
    ))
    monkeypatch.setattr(s2.settings, "enable_semantic_validator", True)

    out = s2.review_model({"actors": [], "use_cases": []})["model_review"]

    assert out["semantic_status"] == validator.OK
    assert len(out["issues"]) == 1
    assert "actors.sud-is-not-an-actor" in out["issues"][0]
    assert "p.59" in out["issues"][0]              # 책이 명시한 결함이라 인용이 붙는다
    assert out["unexamined_rules"] == []


def test_the_artifact_is_json_serialisable(monkeypatch):
    """검증자는 산출물을 JSON으로 넘긴다 — 넘길 수 없는 값이 섞이면 호출 자체가 실패한다."""
    captured = _patch(monkeypatch, _all())
    validator.review(
        _STAGE, {"trigger": "t", "steps": [1, 2]}, prefix="semantic", source="x"
    )
    body = captured["human"].split("\n", 1)[1]
    assert json.loads(body) == {"trigger": "t", "steps": [1, 2]}


def _spec_stub():
    """정적 검증을 통과하는 최소 명세(생성기 목킹용)."""
    from app.requirements.schemas import MainScenarioStep, UseCaseSpec

    return UseCaseSpec(
        preconditions=["the user is signed in"],
        trigger="The user asks to place an order",
        main_scenario=[MainScenarioStep(step_number=1, sentence="System records the order",
                                        covered_req_ids=[])],
        extensions=[],
        success_guarantee=["the order is recorded"],
        minimal_guarantee=["no partial order is kept"],
    )
