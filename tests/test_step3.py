"""STEP 3 노드 테스트 (유스케이스별 명세 생성, 병렬 + 구조 무결성).

  1. 결정론/목킹 — 순서 보존, 필드 매핑, 빈 입력 단락.
  2. 병렬성 증명 — threading.Barrier로 UC 스레드가 실제로 동시에 도달함을 강제.
  3. 후처리 단위 — _clean(마크다운 제거), _validate_spec(분기/복귀 참조 무결성).
  4. 라이브(RUN_LIVE_TESTS=1) — step2→step3 e2e (tests/datasets/*.json).

직접 돌려보려면:
    RUN_LIVE_TESTS=1 python -m pytest tests/test_step3.py -k live -s
"""
import os
import threading

import pytest
from conftest import dataset_names, load_dataset

from app.requirements.agent.steps import step2_usecases as s2
from app.requirements.agent.steps import step3_specifications as s3
from app.requirements.agent.steps.step3_specifications import _clean, _validate_spec
from app.requirements.common import telemetry
from app.requirements.knowledge import rules
from app.requirements.schemas import (
    Critique,
    Extension,
    ExtensionHandlingStep,
    MainScenarioStep,
    RuleVerdict,
    UseCaseSpec,
)


def _uc(uc_id, name="Do thing", actor="User", goal="g", reqs=None, nfrs=None):
    return {
        "id": uc_id, "name": name, "primary_actor": actor, "level": "user_goal",
        "goal": goal, "requirement_ids": reqs or [], "nfr_ids": nfrs or [],
    }


def _step(n, sentence="actor acts", reqs=None):
    return MainScenarioStep(step_number=n, sentence=sentence, covered_req_ids=reqs or [])


def _clean_spec(**over):
    """정적 이슈가 없는 최소 스펙(계약·문장 정상) — 반성 루프가 안 돌게."""
    base = dict(trigger="user acts", preconditions=["ready"], success_guarantee=["done"],
                main_scenario=[_step(1)])
    base.update(over)
    return UseCaseSpec(**base)


_CLASSIFIED = [
    {"id": "R1", "text": "log in", "type": "FR"},
    {"id": "R2", "text": "place order", "type": "FR"},
    {"id": "N1", "text": "encrypt data", "type": "NFR"},
]


# ---------------------------------------------------------------------------
# 1. 결정론 / 목킹
# ---------------------------------------------------------------------------
def test_generate_specs_maps_fields_and_shapes_extensions(monkeypatch):
    spec = UseCaseSpec(
        preconditions=["logged in"], trigger="user requests checkout",
        main_scenario=[
            _step(1, "User submits the order", ["R2"]),
            _step(2, "System confirms the order"),
        ],
        extensions=[
            Extension(
                label="2a", branch_step=2, condition="payment is declined",
                handling_steps=[ExtensionHandlingStep(sub_step="2a1", sentence="System shows a decline message")],
                outcome="resume", resume_at_step=2,
            )
        ],
        success_guarantee=["order saved"], minimal_guarantee=["no partial order"],
    )
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: spec)

    state = {"use_cases": [_uc("UC1", reqs=["R2"], nfrs=["N1"])], "classified": _CLASSIFIED, "actors": []}
    out = s3.generate_specs(state)
    specs = out["use_case_specs"]

    assert out["phase"] == "specs"
    s = specs[0]
    assert s["use_case_id"] == "UC1"
    # 주 시나리오는 step_number/sentence/covered_req_ids 구조
    assert s["main_scenario"][0] == {"step_number": 1, "sentence": "User submits the order", "covered_req_ids": ["R2"]}
    # 확장은 분기/종료가 구조 필드로
    ext = s["extensions"][0]
    assert ext["label"] == "2a" and ext["branch_step"] == 2
    assert ext["outcome"] == "resume" and ext["resume_at_step"] == 2
    assert ext["handling_steps"] == [{"sub_step": "2a1", "sentence": "System shows a decline message"}]
    # 참조가 유효하므로 무결성 위반 없음
    assert s["issues"] == []


def test_generate_specs_preserves_input_order(monkeypatch):
    def fake(schema, messages):
        first = messages[1].content.splitlines()[0]  # "Use case: <name>"
        return _clean_spec(trigger=first)

    monkeypatch.setattr(s3, "invoke_structured", fake)
    ucs = [_uc("UC1", name="Alpha"), _uc("UC2", name="Bravo"), _uc("UC3", name="Charlie")]
    specs = s3.generate_specs({"use_cases": ucs, "classified": _CLASSIFIED, "actors": []})["use_case_specs"]

    assert [s["use_case_id"] for s in specs] == ["UC1", "UC2", "UC3"]
    assert [s["trigger"] for s in specs] == ["Use case: Alpha", "Use case: Bravo", "Use case: Charlie"]


def test_check_specs_aggregates_report():
    state = {"use_case_specs": [
        {"use_case_id": "UC1", "issues": ["i1", "i2"], "repair_iters": 2},
        {"use_case_id": "UC2", "issues": [], "repair_iters": 1},
    ]}
    report = s3.check_specs(state)["spec_report"]
    assert report["n_specs"] == 2
    assert report["total_issues"] == 2
    assert report["issues_by_uc"] == {"UC1": ["i1", "i2"]}   # 이슈 있는 UC만
    assert report["total_repair_iters"] == 3


def test_generate_specs_empty_when_no_use_cases(monkeypatch):
    monkeypatch.setattr(
        s3, "invoke_structured",
        lambda schema, messages: pytest.fail("UC 없으면 호출되면 안 됨"),
    )
    out = s3.generate_specs({"use_cases": [], "classified": _CLASSIFIED})
    assert out["use_case_specs"] == []


# ---------------------------------------------------------------------------
# 2. 병렬성 증명 — Barrier로 동시 도달 강제
# ---------------------------------------------------------------------------
def test_generate_specs_runs_in_parallel(monkeypatch):
    n = 3
    monkeypatch.setattr(s3.settings, "spec_concurrency", n)
    barrier = threading.Barrier(n, timeout=5)

    def fake(schema, messages):
        barrier.wait()  # 순차면 무한대기 → 5초 후 BrokenBarrierError → 실패
        return _clean_spec(trigger="t")

    monkeypatch.setattr(s3, "invoke_structured", fake)
    ucs = [_uc(f"UC{i}") for i in range(1, n + 1)]
    out = s3.generate_specs({"use_cases": ucs, "classified": _CLASSIFIED, "actors": []})
    assert len(out["use_case_specs"]) == n


def test_generate_specs_reports_only_per_use_case_task_boundaries(monkeypatch):
    events = []

    def fake_spec_for(uc, _by_id, _actors, _feedback=""):
        return {"use_case_id": uc["id"], "name": uc["name"]}

    monkeypatch.setattr(s3, "_spec_for", fake_spec_for)
    ucs = [_uc("UC1", name="Browse courses"), _uc("UC2", name="Enroll")]
    with telemetry.progress_scope(
        lambda event, fields: events.append((event, fields))
    ):
        s3.generate_specs({"use_cases": ucs, "classified": _CLASSIFIED, "actors": []})

    boundaries = [item for item in events if item[0].startswith("specTask")]
    assert {
        (event, fields["useCaseId"], fields.get("status"))
        for event, fields in boundaries
    } == {
        ("specTaskStarted", "UC1", None),
        ("specTaskFinished", "UC1", "completed"),
        ("specTaskStarted", "UC2", None),
        ("specTaskFinished", "UC2", "completed"),
    }


def test_generate_specs_respects_concurrency_cap(monkeypatch):
    monkeypatch.setattr(s3.settings, "spec_concurrency", 2)
    lock = threading.Lock()
    live = {"cur": 0, "max": 0}

    def fake(schema, messages):
        with lock:
            live["cur"] += 1
            live["max"] = max(live["max"], live["cur"])
        threading.Event().wait(0.05)
        with lock:
            live["cur"] -= 1
        return _clean_spec(trigger="t")

    monkeypatch.setattr(s3, "invoke_structured", fake)
    ucs = [_uc(f"UC{i}") for i in range(1, 4)]
    s3.generate_specs({"use_cases": ucs, "classified": _CLASSIFIED, "actors": []})
    assert live["max"] == 2  # 상한 준수 (동시 최대 2)


# ---------------------------------------------------------------------------
# 3. 후처리 단위 — 새니타이저 & 무결성 검증
# ---------------------------------------------------------------------------
def test_clean_strips_markdown_and_specials():
    assert _clean("**Bold** and `code`") == "Bold and code"
    assert _clean("E‑commerce “quote”") == 'E-commerce "quote"'  # en/nbsp dash + smart quotes
    assert _clean("  spaced  ") == "spaced"


def _spec(main, exts, **over):
    base = {
        "trigger": "user acts", "preconditions": ["ok"], "success_guarantee": ["done"],
        "main_scenario": main, "extensions": exts,
    }
    base.update(over)
    return base


def test_validate_spec_flags_bad_references():
    main = [{"step_number": 1, "sentence": "actor acts"}, {"step_number": 2, "sentence": "system responds"}]
    exts = [
        {"label": "1a", "branch_step": 9, "outcome": "fail", "resume_at_step": None},       # 분기 스텝 없음
        {"label": "2a", "branch_step": 2, "outcome": "resume", "resume_at_step": None},      # resume인데 target 없음
        {"label": "2b", "branch_step": 2, "outcome": "resume", "resume_at_step": 7},         # resume target 없음
        {"label": "2c", "branch_step": 2, "outcome": "fail", "resume_at_step": 2},           # fail인데 target 설정
        {"label": "*a", "branch_step": None, "outcome": "alternate_success", "resume_at_step": None},  # 전역, 정상
    ]
    issues = _validate_spec(_spec(main, exts))
    assert len(issues) == 4  # 참조 위반 4건만(계약/lint 정상)
    for lbl in ("1a", "2a", "2b", "2c"):
        assert any(lbl in i for i in issues)
    assert not any("*a" in i for i in issues)  # 전역+정상은 위반 아님


def test_validate_spec_flags_ui_branch_control():
    main = [
        {"step_number": 1, "sentence": "User clicks the submit button"},          # UI: click, button
        {"step_number": 2, "sentence": "System proceeds if the cart is valid"},    # 분기: if
    ]
    exts = [{
        "label": "2a", "branch_step": 2, "condition": "c", "outcome": "fail", "resume_at_step": None,
        "handling_steps": [{"sub_step": "2a1", "sentence": "System shows Fail! on the screen"}],  # 제어토큰 + UI: screen
    }]
    joined = " ".join(_validate_spec(_spec(main, exts)))
    assert "UI terms" in joined and "branch word" in joined and "control token" in joined


def test_validate_spec_flags_missing_contract():
    main = [{"step_number": 1, "sentence": "actor acts"}]
    issues = _validate_spec(_spec(main, [], preconditions=[], success_guarantee=[]))
    assert not any("preconditions" in i for i in issues)
    assert any("success_guarantee" in i for i in issues)


def test_spec_snapshots_the_accepted_use_case_traceability_ids(monkeypatch):
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _clean_spec(
        main_scenario=[_step(1, reqs=["R2", "R1"])]
    ))
    accepted = _uc("UC1", reqs=["R2", "R1"], nfrs=["N1"])

    item = s3.generate_specs(
        {"use_cases": [accepted], "classified": _CLASSIFIED, "actors": []}
    )["use_case_specs"][0]

    assert item["use_case_id"] == accepted["id"]
    assert item["requirement_ids"] == accepted["requirement_ids"]
    assert item["nfr_ids"] == accepted["nfr_ids"]
    assert item["requirement_ids"] is not accepted["requirement_ids"]
    assert item["nfr_ids"] is not accepted["nfr_ids"]


def test_every_accepted_functional_requirement_must_be_covered_by_a_scenario_step(monkeypatch):
    accepted = _uc("UC1", reqs=["R1", "R2"], nfrs=["N1"])
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _clean_spec(
        main_scenario=[_step(1, reqs=["R1"])]
    ))

    item = s3.generate_specs(
        {"use_cases": [accepted], "classified": _CLASSIFIED, "actors": []}
    )["use_case_specs"][0]

    assert any(
        "accepted functional requirement 'R2' is not covered" in issue
        for issue in item["issues"]
    )
    assert not any("'N1' is not covered" in issue for issue in item["issues"])


def test_empty_minimal_guarantee_is_preserved_in_the_validator_payload():
    item = {
        "trigger": "start",
        "preconditions": ["ready"],
        "main_scenario": [],
        "extensions": [],
        "success_guarantee": ["complete"],
        "minimal_guarantee": [],
    }

    payload = s3.spec_review_payload(item)

    assert payload["minimal_guarantee"] == []


def test_scenario_refs_are_retained_and_must_belong_to_the_owning_use_case(monkeypatch):
    source = _uc("UC1", reqs=["R1"], nfrs=["N1"])
    generated = _clean_spec(main_scenario=[_step(1, reqs=["R2", "N1"])])
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: generated)

    item = s3.generate_specs(
        {"use_cases": [source], "classified": _CLASSIFIED, "actors": []}
    )["use_case_specs"][0]

    assert item["main_scenario"][0]["covered_req_ids"] == ["R2", "N1"]
    assert any(
        "spec.scenario-requirement-reference-integrity" in issue
        for issue in item["issues"]
    )


def test_specs_do_not_collide_when_use_case_names_are_the_same(monkeypatch):
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _clean_spec())
    use_cases = [
        _uc("UC1", name="same"),
        _uc("UC2", name="same"),
    ]

    specs = s3.generate_specs(
        {"use_cases": use_cases, "classified": _CLASSIFIED, "actors": []}
    )["use_case_specs"]

    assert [item["use_case_id"] for item in specs] == [item["id"] for item in use_cases]
    assert [item["requirement_ids"] for item in specs] == [
        item["requirement_ids"] for item in use_cases
    ]


# ---------------------------------------------------------------------------
# T2-1 반성 루프 (정적 driven) + 의미 검증 병합
# ---------------------------------------------------------------------------
def _bad_spec():
    # UI 용어(click/button) 포함 → 정적 위반
    return _clean_spec(main_scenario=[MainScenarioStep(step_number=1, sentence="User clicks the button")])


def test_reflection_loop_repairs_until_clean(monkeypatch):
    calls = {"n": 0}

    def fake(schema, messages):
        calls["n"] += 1
        return _bad_spec() if calls["n"] == 1 else _clean_spec()  # 첫 출력만 위반, 이후 정상

    monkeypatch.setattr(s3, "invoke_structured", fake)
    spec = s3.generate_specs({"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []})["use_case_specs"][0]

    assert spec["issues"] == []          # 재생성으로 해소
    assert spec["repair_iters"] == 1


def test_reflection_loop_gives_up_when_regeneration_does_not_help(monkeypatch):
    """나아지지 않는 재생성에 예산을 계속 쓰지 않는다.

    예전에는 결함 **개수가 늘 때만** 거절했다. 그래서 결함 3개가 다른 결함 3개로
    바뀌어도 채택하고 다음 반복까지 돌았다 — 나아진 것 없이 호출만 두 배로 썼다.
    """
    monkeypatch.setattr(s3.settings, "max_repair_iters", 2)
    calls = {"n": 0}

    def fake(schema, messages):
        calls["n"] += 1
        return _bad_spec()               # 몇 번을 물어도 같은 위반

    monkeypatch.setattr(s3, "invoke_structured", fake)
    spec = s3.generate_specs({"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []})["use_case_specs"][0]

    assert spec["issues"]                        # 여전히 위반 → 표면화
    assert spec["repair_stopped"] == "repeated_fingerprint"
    assert spec["repair_iters"] == 1             # 시도는 1회에서 멈춘다
    assert calls["n"] == 2                       # 최초 생성 + 재생성 1회뿐


def test_reflection_loop_stops_at_repair_budget(monkeypatch):
    """매번 조금씩 나아지지만 끝내 깨끗해지지 않으면 예산에서 멈춘다."""
    monkeypatch.setattr(s3.settings, "max_repair_iters", 2)
    # 위반 스텝 3개 → 2개 → 1개. 위반은 **스텝마다** 하나씩 세므로 개수가 실제로 줄고,
    # 그래서 매번 채택되어 예산이 먼저 소진된다.
    bad, good = "User clicks it", "The user submits the order"
    rounds = [
        [bad, bad, bad],
        [bad, bad, good],
        [bad, good, good],
    ]
    calls = {"n": 0}

    def fake(schema, messages):
        sentences = rounds[min(calls["n"], len(rounds) - 1)]
        calls["n"] += 1
        return _clean_spec(
            main_scenario=[_step(i, s) for i, s in enumerate(sentences, start=1)]
        )

    monkeypatch.setattr(s3, "invoke_structured", fake)
    spec = s3.generate_specs({"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []})["use_case_specs"][0]

    assert spec["repair_stopped"] == "budget"
    assert spec["repair_iters"] == 2     # 예산 소진
    assert spec["issues"]                # 줄었지만 남아 있다 → 표면화


def test_reflection_rejects_a_smaller_issue_list_with_new_keys(monkeypatch):
    initial = _clean_spec(main_scenario=[
        _step(1, "User clicks the button"),
        _step(2, "User clicks the button"),
    ])
    replacement = _clean_spec(main_scenario=[
        _step(1, "System proceeds if the request is valid"),
    ])
    results = iter([initial, replacement])
    monkeypatch.setattr(s3.settings, "max_repair_iters", 2)
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: next(results))

    item = s3.generate_specs(
        {"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []}
    )["use_case_specs"][0]

    assert item["repair_stopped"] == "no_improvement"
    assert all("spec.black-box-no-ui-mechanics" in issue for issue in item["issues"])


def test_issue_keys_distinguish_two_findings_from_the_same_semantic_rule():
    tag = s3.rules.tag_of("spec.no-scope-creep")

    keys = s3._issue_keys([
        f"[semantic] first scope finding {tag}",
        f"[semantic] second scope finding {tag}",
    ])

    assert len(keys) == 2


def test_reflection_enforces_the_local_two_attempt_cap(monkeypatch):
    rounds = [
        _clean_spec(main_scenario=[_step(index, "User clicks the button") for index in range(1, 4)]),
        _clean_spec(main_scenario=[_step(index, "User clicks the button") for index in range(1, 3)]),
        _clean_spec(main_scenario=[_step(1, "User clicks the button")]),
        _clean_spec(),
    ]
    calls = {"n": 0}

    def fake(schema, messages):
        result = rounds[min(calls["n"], len(rounds) - 1)]
        calls["n"] += 1
        return result

    monkeypatch.setattr(s3.settings, "max_repair_iters", 99)
    monkeypatch.setattr(s3, "invoke_structured", fake)

    item = s3.generate_specs(
        {"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []}
    )["use_case_specs"][0]

    assert item["repair_iters"] == 2
    assert item["repair_stopped"] == "budget"
    assert calls["n"] == 3


def test_repair_stopped_is_aggregated_in_the_report(monkeypatch):
    """왜 멈췄는지의 분포가 리포트에 있어야 부분 수정으로 바꿀 근거가 생긴다."""
    monkeypatch.setattr(s3.settings, "max_repair_iters", 1)
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _clean_spec())

    state = s3.generate_specs(
        {"use_cases": [_uc("UC1"), _uc("UC2")], "classified": _CLASSIFIED, "actors": []}
    )
    report = s3.check_specs(state)["spec_report"]
    assert report["repair_stopped"] == {"clean": 2}


def _patch_validator(monkeypatch, *, verdicts=None, error=None):
    """의미 검증자는 별도 모듈이라 **별도로** 목킹한다 — 그게 독립 검증자의 형태다.

    생성기(`s3.invoke_structured`)와 검증자(`s3.validator.invoke_structured`)가 서로 다른
    호출 지점이라는 사실이 테스트에서도 그대로 보인다.
    """
    def fake(schema, messages):
        assert schema is Critique, "검증자는 Critique만 요청한다"
        if error is not None:
            raise error
        return Critique(verdicts=list(verdicts or []))

    monkeypatch.setattr(s3.validator, "invoke_structured", fake)


#: 이 단계에서 의미 검증자가 판정해야 하는 규칙 전부. 규칙마다 한 줄씩 답해야 하므로,
#: 목킹도 전부 채워야 "빠뜨렸다"는 저하가 생기지 않는다.
_SPEC_RULE_IDS = [
    r.id for r in rules.judged_by(rules.WRITE_SPECIFICATIONS, rules.JUDGED_VALIDATOR)
]


def _all_clean(violated: dict[str, str] | None = None) -> list[RuleVerdict]:
    """규칙 전체에 대한 판정 — `violated`에 있는 것만 위반으로."""
    violated = violated or {}
    return [
        RuleVerdict(
            rule_id=rid,
            violated=rid in violated,
            directive=violated.get(rid, ""),
        )
        for rid in _SPEC_RULE_IDS
    ]


def test_semantic_validator_merges_and_drives_repair(monkeypatch):
    monkeypatch.setattr(s3.settings, "enable_semantic_validator", True)
    monkeypatch.setattr(s3.settings, "max_repair_iters", 1)

    def fake(schema, messages):
        return _clean_spec()          # 정적으론 깨끗

    monkeypatch.setattr(s3, "invoke_structured", fake)
    _patch_validator(monkeypatch, verdicts=_all_clean(
        {"spec.remerge-re-establishes-state": "re-establish the state step 3 assumes"}
    ))
    spec = s3.generate_specs({"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []})["use_case_specs"][0]

    assert any("[semantic]" in i for i in spec["issues"])  # 의미 결함이 병합됨
    # 지적이 근거를 들고 나간다 — 규칙 id와 인용 좌표가 문구에 함께 있다.
    assert any("spec.remerge-re-establishes-state" in i and "Ch. 8" in i for i in spec["issues"])
    assert spec["repair_iters"] == 1                        # 의미 결함이 재생성을 유발
    assert spec["semantic_status"] == "ok"                  # 실제로 검증을 거쳤다


def test_ungrounded_finding_is_dropped_and_not_reported_as_clean(monkeypatch):
    """검증자가 **없는 규칙**을 인용하면 그 지적은 버린다.

    버리고 나서 남은 지적이 없으면 "결함 없음"이 아니라 "확인하지 못함"이다 — 검증자가
    헛소리만 한 실행이 깨끗한 실행처럼 보이면 안 된다.
    """
    monkeypatch.setattr(s3.settings, "enable_semantic_validator", True)
    monkeypatch.setattr(s3.settings, "max_repair_iters", 1)

    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _clean_spec())
    _patch_validator(monkeypatch, verdicts=[
        RuleVerdict(rule_id="spec.made-up-rule", violated=True, directive="do something")
    ])
    spec = s3.generate_specs({"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []})["use_case_specs"][0]

    assert spec["issues"] == []                       # 근거 없는 지적은 남지 않는다
    assert spec["semantic_status"] == "ungrounded"    # 그러나 깨끗하다고 하지도 않는다
    assert spec["repair_iters"] == 0                 # 헛지적으로 재생성 예산을 태우지 않는다

    report = s3.check_specs({"use_case_specs": [spec]})["spec_report"]
    assert report["unvalidated_ucs"] == ["UC1"]      # 리포트가 확인 못 한 사실을 싣는다


def test_dead_semantic_validator_is_not_reported_as_clean(monkeypatch):
    """검증기가 죽으면 "결함 없음"이 아니라 "확인하지 못함"이어야 한다.

    예전에는 예외를 삼키고 빈 리스트를 돌려줘서, NIM이 내려가 있으면 모든 명세가
    조용히 깨끗하게 통과했다. 리포트만 보고는 구별할 방법이 없었다.
    """
    monkeypatch.setattr(s3.settings, "enable_semantic_validator", True)

    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _clean_spec())
    _patch_validator(monkeypatch, error=RuntimeError("NIM down"))
    with telemetry.run_scope("t") as stats:
        state = s3.generate_specs(
            {"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []}
        )
    spec = state["use_case_specs"][0]

    assert spec["issues"] == []                        # 정적 검증만으로는 깨끗하고
    assert spec["semantic_status"] == "failed"         # 그게 "검증했다"는 뜻은 아니다

    report = s3.check_specs(state)["spec_report"]
    assert report["unvalidated_ucs"] == ["UC1"]        # 리포트에서 구별된다

    degraded = stats.as_dict()["degradations"]
    assert [d["component"] for d in degraded] == ["spec.semantic_validator"]
    assert degraded[0]["subject"] == "UC1"


def test_one_failed_use_case_does_not_discard_its_siblings(monkeypatch):
    """UC 하나가 죽어도 이미 끝난 형제는 살아남아야 한다.

    예전에는 fut.result()의 예외가 그대로 올라가 노드 전체가 실패했고, 10개 중 9개가
    끝나 있어도 그 9개까지 버려졌다.
    """
    monkeypatch.setattr(s3.settings, "enable_semantic_validator", False)

    def fake(schema, messages):
        if "Bravo" in messages[1].content:
            raise RuntimeError("NIM 429 Too Many Requests")
        return _clean_spec(trigger=messages[1].content.splitlines()[0])

    monkeypatch.setattr(s3, "invoke_structured", fake)
    ucs = [_uc("UC1", name="Alpha"), _uc("UC2", name="Bravo"), _uc("UC3", name="Charlie")]
    with telemetry.run_scope("t") as stats:
        state = s3.generate_specs(
            {"use_cases": ucs, "classified": _CLASSIFIED, "actors": []}
        )

    specs = state["use_case_specs"]
    # 순서도 자리도 유지된다 — 실패한 UC가 목록에서 사라지지 않는다.
    assert [s["use_case_id"] for s in specs] == ["UC1", "UC2", "UC3"]
    assert [s.get("generated", True) for s in specs] == [True, False, True]
    assert specs[0]["trigger"] == "Use case: Alpha"       # 형제는 온전하다
    assert specs[2]["trigger"] == "Use case: Charlie"
    assert "NIM 429" in specs[1]["issues"][0]             # 왜 비었는지가 적혀 있다

    report = s3.check_specs(state)["spec_report"]
    assert report["failed_ucs"] == ["UC2"]
    degraded = stats.as_dict()["degradations"]
    assert [(d["component"], d["subject"]) for d in degraded] == [("spec.generate", "UC2")]


def test_disabled_semantic_validator_is_not_counted_as_failure(monkeypatch):
    """끈 것과 죽은 것은 다르다 — 끈 것은 저하가 아니다."""
    monkeypatch.setattr(s3.settings, "enable_semantic_validator", False)
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _clean_spec())

    with telemetry.run_scope("t") as stats:
        state = s3.generate_specs(
            {"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []}
        )

    assert state["use_case_specs"][0]["semantic_status"] == "disabled"
    assert s3.check_specs(state)["spec_report"]["unvalidated_ucs"] == []
    assert stats.as_dict()["degradations"] == []


# ---------------------------------------------------------------------------
# 4. 라이브 e2e — step2 → step3 (옵트인)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="라이브 NIM 테스트는 RUN_LIVE_TESTS=1 일 때만 실행",
)
@pytest.mark.parametrize("dataset_name", dataset_names())
def test_live_step3(dataset_name):
    """데이터셋 → step2(액터·UC) → step3(명세)까지 실제 NIM으로 돌린다."""
    selected = os.getenv("STEP2_DATASET")
    if selected and dataset_name not in [s.strip() for s in selected.split(",")]:
        pytest.skip(f"STEP2_DATASET={selected} 에 없는 데이터셋({dataset_name})")

    state = {"classified": load_dataset(dataset_name)["classified"]}
    state.update(s2.identify_actors(state))
    state.update(s2.identify_use_cases(state))
    state.update(s3.generate_specs(state))

    import json
    print(f"\n########## dataset: {dataset_name} — SPECS ##########")
    print(json.dumps(state["use_case_specs"], indent=2, ensure_ascii=False))

    specs = state["use_case_specs"]
    assert [s["use_case_id"] for s in specs] == [u["id"] for u in state["use_cases"]]
    for s in specs:
        assert s["main_scenario"], "주 시나리오는 최소 1스텝 이상"
        for st in s["main_scenario"]:
            assert isinstance(st["step_number"], int)
            assert st["sentence"] and "**" not in st["sentence"]  # 마크다운 제거 확인
        for ext in s["extensions"]:
            assert ext["outcome"] in ("resume", "alternate_success", "fail")
            if ext["outcome"] == "resume":
                assert ext["resume_at_step"] is not None
        # 구조 무결성 위반은 경고로 출력(LLM 비결정성이라 하드 실패는 안 시킴)
        if s["issues"]:
            print(f"  [WARN] {s['use_case_id']} issues: {s['issues']}")
