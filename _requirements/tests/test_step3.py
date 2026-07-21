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

from app.agent.steps import step2_usecases as s2
from app.agent.steps import step3_specifications as s3
from app.agent.steps.step3_specifications import _clean, _validate_spec
from app.schemas import (
    Extension,
    ExtensionHandlingStep,
    MainScenarioStep,
    SpecCritique,
    UseCaseSpec,
)
from conftest import dataset_names, load_dataset


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
    assert "UI 용어" in joined and "분기어" in joined and "제어토큰" in joined


def test_validate_spec_flags_missing_contract():
    main = [{"step_number": 1, "sentence": "actor acts"}]
    issues = _validate_spec(_spec(main, [], preconditions=[], success_guarantee=[]))
    assert any("preconditions" in i for i in issues)
    assert any("success_guarantee" in i for i in issues)


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


def test_reflection_loop_stops_at_repair_budget(monkeypatch):
    monkeypatch.setattr(s3.settings, "max_repair_iters", 2)
    monkeypatch.setattr(s3, "invoke_structured", lambda schema, messages: _bad_spec())

    spec = s3.generate_specs({"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []})["use_case_specs"][0]

    assert spec["repair_iters"] == 2     # 예산 소진
    assert spec["issues"]                # 여전히 위반(안 고쳐짐) → 표면화


def test_semantic_validator_merges_and_drives_repair(monkeypatch):
    monkeypatch.setattr(s3.settings, "enable_semantic_validator", True)
    monkeypatch.setattr(s3.settings, "max_repair_iters", 1)

    def fake(schema, messages):
        if schema is UseCaseSpec:
            return _clean_spec()          # 정적으론 깨끗
        return SpecCritique(is_valid=False, findings=["split hidden branching in step 2"])

    monkeypatch.setattr(s3, "invoke_structured", fake)
    spec = s3.generate_specs({"use_cases": [_uc("UC1")], "classified": _CLASSIFIED, "actors": []})["use_case_specs"][0]

    assert any("[semantic]" in i for i in spec["issues"])  # 의미 결함이 병합됨
    assert spec["repair_iters"] == 1                        # 의미 결함이 재생성을 유발


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
