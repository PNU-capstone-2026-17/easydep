"""피드백 기반 재생성 테스트.

  1. load_state 라운드트립 (persist → load).
  2. apply_feedback: 의도 분류·재생성·하위 cascade·정합성 리포트 (스테이지 목킹).
  3. specs local 재생성이 형제 spec을 보존.
"""
import json

from app import feedback as fb
from app import runner
from app.schemas import FeedbackIntent


# ---------------------------------------------------------------------------
# 1. load_state 라운드트립
# ---------------------------------------------------------------------------
def test_load_state_roundtrip(tmp_path):
    input_obj = {"name": "demo", "classified": [{"id": "R1", "text": "x", "type": "FR"}]}
    state = {
        "actors": [{"name": "User", "kind": "primary", "description": "d", "parent_actor": None}],
        "use_cases": [{"id": "UC1", "name": "Log in", "primary_actor": "User",
                       "requirement_ids": ["R1"], "nfr_ids": []}],
        "coverage": {"coverage_ratio": 1.0, "orphan_fr_ids": []},
        "use_case_specs": [{"use_case_id": "UC1", "name": "Log in", "main_scenario": [], "issues": []}],
        "relationships": {"associations": [{"actor": "User", "use_case": "Log in"}],
                          "orphan_actors": [], "dropped_refs": []},
        "diagram": "@startuml\n@enduml",
    }
    run_dir = runner.persist_run(input_obj, state, dataset_name="demo", artifact_root=tmp_path)
    loaded = runner.load_state(run_dir)

    assert loaded["classified"] == input_obj["classified"]
    assert loaded["actors"][0]["name"] == "User"
    assert loaded["use_cases"][0]["id"] == "UC1"
    assert loaded["use_case_specs"][0]["use_case_id"] == "UC1"
    assert loaded["relationships"]["associations"][0]["actor"] == "User"
    assert loaded["diagram"] == "@startuml\n@enduml"


# ---------------------------------------------------------------------------
# 2. apply_feedback — 재생성 + cascade
# ---------------------------------------------------------------------------
def _install_stage_spies(monkeypatch):
    calls = []

    def spy(name, key, ret):
        def fn(state, *a, **kw):
            calls.append((name, kw.get("feedback", a[0] if a else None)))
            return {key: ret}
        return fn

    # _consistency가 읽는 coverage/relationships/use_case_specs는 타입 맞는 마커로.
    monkeypatch.setattr(fb, "identify_actors", spy("actors", "actors", "<a>"))
    monkeypatch.setattr(fb, "identify_use_cases", spy("use_cases", "use_cases", "<uc>"))
    monkeypatch.setattr(fb, "check_coverage", spy("coverage", "coverage", {"coverage_ratio": 1.0, "orphan_fr_ids": []}))
    monkeypatch.setattr(fb, "generate_specs", spy("specs", "use_case_specs", []))
    monkeypatch.setattr(fb, "identify_relationships", spy("relationships", "relationships", {"orphan_actors": [], "dropped_refs": []}))
    monkeypatch.setattr(fb, "render_diagram", spy("diagram", "diagram", "@startuml\n@enduml"))
    return calls


def test_apply_feedback_use_cases_cascades_all_downstream(monkeypatch):
    monkeypatch.setattr(
        fb, "classify_feedback",
        lambda feedback, state: FeedbackIntent(stage="use_cases", scope="broad", target_ids=[], instruction="merge cart UCs"),
    )
    calls = _install_stage_spies(monkeypatch)

    state, report = fb.apply_feedback({"classified": []}, "장바구니 유스케이스들을 하나로 합쳐줘")

    assert report["regenerated"] == "use_cases"
    assert report["cascaded"] == ["coverage", "specs", "relationships", "diagram"]
    # use_cases는 피드백 지시로 재생성, 하위는 피드백 없이
    assert ("use_cases", "merge cart UCs") in calls
    assert ("actors", None) not in calls          # 상위(actors)는 안 건드림
    assert [c[0] for c in calls] == ["use_cases", "coverage", "specs", "relationships", "diagram"]


def test_apply_feedback_relationships_only_cascades_diagram(monkeypatch):
    monkeypatch.setattr(
        fb, "classify_feedback",
        lambda feedback, state: FeedbackIntent(stage="relationships", scope="broad", target_ids=[], instruction="add include"),
    )
    calls = _install_stage_spies(monkeypatch)

    _, report = fb.apply_feedback({}, "인증을 include로 묶어줘")

    assert report["regenerated"] == "relationships"
    assert report["cascaded"] == ["diagram"]      # 관계 아래는 다이어그램뿐
    assert [c[0] for c in calls] == ["relationships", "diagram"]


def test_apply_feedback_report_consistency(monkeypatch):
    monkeypatch.setattr(
        fb, "classify_feedback",
        lambda feedback, state: FeedbackIntent(stage="relationships", scope="broad", target_ids=[], instruction="x"),
    )
    # 관계/다이어그램만 재생성; 정합성 지표는 state에서 읽음.
    monkeypatch.setattr(fb, "identify_relationships",
                        lambda state, **kw: {"relationships": {"orphan_actors": ["Ghost"], "dropped_refs": ["a->b"]}})
    monkeypatch.setattr(fb, "render_diagram", lambda state, **kw: {"diagram": "@startuml\n@enduml"})

    state = {"coverage": {"coverage_ratio": 1.0, "orphan_fr_ids": []},
             "use_case_specs": [{"issues": ["i1"]}, {"issues": []}]}
    _, report = fb.apply_feedback(state, "fb")
    c = report["consistency"]

    assert c["coverage_ratio"] == 1.0
    assert c["orphan_actors"] == ["Ghost"]
    assert c["dropped_refs"] == ["a->b"]
    assert c["spec_issues_total"] == 1


# ---------------------------------------------------------------------------
# 2b. apply_feedback_upto — 게이트용 경계 cascade + 상위 라우팅 + 클램프
# ---------------------------------------------------------------------------
def test_apply_feedback_upto_bounds_cascade_to_gate(monkeypatch):
    # use_cases 게이트: use_cases 재생성 후 coverage까지만 cascade(specs 이하는 아직 없음).
    monkeypatch.setattr(
        fb, "classify_feedback",
        lambda feedback, state: FeedbackIntent(stage="use_cases", scope="broad", target_ids=[], instruction="i"),
    )
    calls = _install_stage_spies(monkeypatch)

    intent, cascaded = fb.apply_feedback_upto({"classified": []}, "fb", up_to="coverage")

    assert intent.stage == "use_cases"
    assert cascaded == ["coverage"]
    assert [c[0] for c in calls] == ["use_cases", "coverage"]


def test_apply_feedback_upto_routes_to_upstream_actors(monkeypatch):
    # use_cases 게이트에서 '외부 액터 추가' → actors 재생성 후 use_cases·coverage cascade.
    monkeypatch.setattr(
        fb, "classify_feedback",
        lambda feedback, state: FeedbackIntent(stage="actors", scope="broad", target_ids=[], instruction="add DBMS actor"),
    )
    calls = _install_stage_spies(monkeypatch)

    intent, _ = fb.apply_feedback_upto({"classified": []}, "DBMS를 외부 액터로 추가", up_to="coverage")

    assert intent.stage == "actors"
    assert [c[0] for c in calls] == ["actors", "use_cases", "coverage"]
    assert ("actors", "add DBMS actor") in calls   # 피드백이 액터 재생성에 전달됨


def test_apply_feedback_upto_clamps_downstream_stage(monkeypatch):
    # 아직 생성 안 된 하위(relationships)를 지목하면 게이트 단계(use_cases)로 클램프.
    monkeypatch.setattr(
        fb, "classify_feedback",
        lambda feedback, state: FeedbackIntent(stage="relationships", scope="local", target_ids=["X"], instruction="i"),
    )
    calls = _install_stage_spies(monkeypatch)

    intent, cascaded = fb.apply_feedback_upto({"classified": []}, "fb", up_to="coverage")

    assert intent.stage == "use_cases" and intent.scope == "broad"
    assert [c[0] for c in calls] == ["use_cases", "coverage"]


# ---------------------------------------------------------------------------
# 3. specs local 재생성 — 형제 보존
# ---------------------------------------------------------------------------
def test_generate_specs_local_target_preserves_siblings(monkeypatch):
    from app.agent.steps import step3_specifications as s3

    # UC2만 재생성, UC1은 기존 유지.
    def fake_spec_for(uc, by_id, actors, feedback=""):
        return {"use_case_id": uc["id"], "name": uc["name"], "main_scenario": [],
                "extensions": [], "issues": [], "repair_iters": 0, "trigger": f"regen:{feedback}"}

    monkeypatch.setattr(s3, "_spec_for", fake_spec_for)
    state = {
        "classified": [{"id": "R1", "text": "x", "type": "FR"}],
        "use_cases": [{"id": "UC1", "name": "A", "primary_actor": "U", "requirement_ids": ["R1"], "nfr_ids": []},
                      {"id": "UC2", "name": "B", "primary_actor": "U", "requirement_ids": ["R1"], "nfr_ids": []}],
        "use_case_specs": [{"use_case_id": "UC1", "name": "A", "trigger": "OLD", "issues": []},
                           {"use_case_id": "UC2", "name": "B", "trigger": "OLD", "issues": []}],
    }
    out = s3.generate_specs(state, feedback="시나리오 보강", target_ids=["UC2"])
    specs = {s["use_case_id"]: s for s in out["use_case_specs"]}

    assert specs["UC1"]["trigger"] == "OLD"                # 형제 보존
    assert specs["UC2"]["trigger"] == "regen:시나리오 보강"   # 대상만 재생성
