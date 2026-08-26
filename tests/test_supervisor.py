"""되돌아가기(`app/requirements/agent/supervisor.py`)의 규율.

이 파일이 지키는 것:
  - 결함은 **그것을 낸 단계**로 돌아간다(라우팅 표는 지식베이스의 `Rule.owner`다).
  - 자기 루프에서 이미 포기한 단계로는 되돌리지 않고 **위로 올린다** — 그게 이 층의 존재 이유다.
  - 되돌릴 때 **결함을 지시로 함께 보낸다**(같은 프롬프트로 다시 부르면 같은 답이 온다).
  - 상한이 있다. 그리고 사이클이 **그래프 엣지로** 보인다.
"""
from __future__ import annotations

from app.requirements.agent import supervisor
from app.requirements.agent.graph import build_graph
from app.requirements.knowledge import rules


def _issue(rule_id: str, text: str = "fix it") -> str:
    """실제 파이프라인이 만드는 모양의 지적 문구(꼬리표 포함)."""
    return f"[semantic] {text} {rules.tag_of(rule_id)}"


def _state(**overrides) -> dict:
    base = {
        "use_case_specs": [],
        "relationships": {},
        "model_review": {"issues": [], "semantic_status": "ok", "unexamined_rules": []},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. 귀속 — 라우팅 표는 지식베이스다
# ---------------------------------------------------------------------------
def test_every_defect_rule_names_a_stage_we_can_go_back_to():
    """`owner`가 되돌릴 수 없는 단계를 가리키면 그 결함은 영원히 안 고쳐진다."""
    from app.requirements.agent import stages

    editable = set(stages.editable_keys())
    for rule in rules.RULES:
        if rule.severity == rules.DEFECT:
            assert rule.owner in editable, f"{rule.id}: owner={rule.owner!r}"


def test_owner_is_read_back_from_the_tag():
    assert rules.owner_of(_issue("actors.sud-is-not-an-actor")) == "actors"
    assert rules.owner_of(_issue("spec.no-scope-creep")) == "specs"
    assert rules.owner_of("규칙 꼬리표가 없는 지적") is None


def test_the_top_stage_has_nowhere_further_up():
    """맨 위 단계가 포기하면 올릴 곳이 없다 — 그 사실이 정책의 종료 조건이다."""
    assert supervisor.upstream_of("actors") is None
    assert supervisor.upstream_of("specs") == "use_cases"
    assert supervisor.upstream_of("relationships") == "specs"


# ---------------------------------------------------------------------------
# 2. 정책
# ---------------------------------------------------------------------------
def test_a_model_defect_goes_back_to_the_stage_that_made_it(monkeypatch):
    """`review_model`이 찾은 결함은 예전에 **아무도 고치지 않았다.** 이제 actors로 돌아간다."""
    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    issue = _issue("actors.sud-is-not-an-actor", "Remove 'Order Service' from the actors")

    decision = supervisor.decide(_state(model_review={"issues": [issue]}))

    assert decision.action == supervisor.REDO
    assert decision.owner == "actors"
    assert decision.escalated is False
    assert "Remove 'Order Service'" in decision.instruction   # 결함을 지시로 들려 보낸다
    assert decision.rule_ids == ("actors.sud-is-not-an-actor",)


def test_a_stage_that_gave_up_escalates_upstream(monkeypatch):
    """`repair_stopped="no_improvement"`는 "위에 원인이 있다"는 신호다.

    예전에는 그 신호를 받아 올려보낼 통로가 구조에 없었다 — 명세만 계속 다시 썼다.
    """
    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    issue = _issue("spec.remerge-re-establishes-state")
    state = _state(use_case_specs=[
        {"use_case_id": "UC1", "issues": [issue], "repair_stopped": "no_improvement"},
    ])

    decision = supervisor.decide(state)

    assert decision.owner == "use_cases"          # specs가 아니라 그 위로
    assert decision.escalated is True
    assert "could not repair" in decision.instruction   # 아래가 못 고쳤다는 사실까지 전한다


def test_a_stage_still_working_on_it_is_asked_again_itself(monkeypatch):
    """자기 루프가 아직 포기하지 않았으면 그 단계로 되돌린다(위로 올리지 않는다)."""
    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    state = _state(use_case_specs=[
        {"use_case_id": "UC1", "issues": [_issue("spec.no-scope-creep")],
         "repair_stopped": "clean"},
    ])

    decision = supervisor.decide(state)

    assert decision.owner == "specs"
    assert decision.escalated is False


def test_only_the_most_upstream_target_is_taken(monkeypatch):
    """위를 고치면 아래는 cascade로 다시 돈다 — 한 번에 여러 곳을 되돌릴 이유가 없다."""
    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    state = _state(
        model_review={"issues": [_issue("actors.sud-is-not-an-actor")]},
        use_case_specs=[{"use_case_id": "UC1", "issues": [_issue("spec.no-scope-creep")],
                         "repair_stopped": "no_improvement"}],
    )

    decision = supervisor.decide(state)

    assert decision.owner == "actors"   # use_cases(=specs의 상위)보다 더 위


def test_the_budget_stops_the_loop(monkeypatch):
    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    state = _state(model_review={"issues": [_issue("actors.sud-is-not-an-actor")]},
                   redo_rounds=1)

    decision = supervisor.decide(state)

    assert decision.action == supervisor.ADVANCE
    assert "예산 소진" in decision.reason


def test_no_defects_means_advance():
    decision = supervisor.decide(_state())
    assert decision.action == supervisor.ADVANCE


def test_an_untagged_issue_has_nowhere_to_go():
    """규칙을 못 찾은 지적은 되돌릴 단계를 모른다 — 되돌리기의 근거가 될 수 없다."""
    decision = supervisor.decide(_state(model_review={"issues": ["근거 없는 지적"]}))
    assert decision.action == supervisor.ADVANCE


def test_handoff_blockers_expand_only_as_reports_become_available():
    state = _state(
        model_review={
            "issues": ["model issue"],
            "semantic_status": "ok",
            "unexamined_rules": [],
        },
        coverage={"orphan_fr_ids": ["FR1"], "unknown_requirement_refs": []},
        spec_report={"total_issues": 1, "failed_ucs": [], "unvalidated_ucs": []},
        relationship_report={"missing_supporting_associations": ["Member -> UC1"]},
    )

    model = supervisor.blocking_issues(state, through="model")
    specs = supervisor.blocking_issues(state, through="specs")
    relationships = supervisor.blocking_issues(state)

    assert any("model issue" in issue for issue in model)
    assert not any("orphaned" in issue for issue in model)
    assert any("specification report" in issue for issue in specs)
    assert not any("supporting associations" in issue for issue in specs)
    assert any("supporting associations" in issue for issue in relationships)


# ---------------------------------------------------------------------------
# 3. 그래프 노드
# ---------------------------------------------------------------------------
def test_the_node_writes_the_marker_the_instruction_and_the_history(monkeypatch):
    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    node = supervisor.supervise_for("model_use_cases")
    issue = _issue("actors.sud-is-not-an-actor")

    out = node(_state(model_review={"issues": [issue]}))

    assert out["redo_route"] == "model_use_cases"
    assert out["stage_feedback"]["actors"]
    assert out["redo_rounds"] == 1
    assert out["redo_history"][0]["owner"] == "actors"
    assert out["redo_history"][0]["rule_ids"] == ["actors.sud-is-not-an-actor"]


def test_advancing_clears_a_stale_instruction():
    """지시를 남겨 두면 다음에 그 단계가 돌 때 낡은 지시를 다시 먹는다."""
    node = supervisor.supervise_for("model_use_cases")
    out = node(_state(stage_feedback={"actors": "옛 지시"}))
    assert out["redo_route"] == "advance"
    assert out["stage_feedback"] == {}


def test_an_unreachable_target_is_surfaced_not_crashed(monkeypatch):
    """엣지 맵에 없는 곳으로 되돌리려 하면 죽지도, 조용히 넘기지도 않는다."""
    from app.requirements.common import telemetry

    monkeypatch.setattr(supervisor.settings, "max_redo_rounds", 1)
    # 이 자리(supervise_model)에서는 write_specifications로 되돌릴 수 없다.
    node = supervisor.supervise_for("model_use_cases")
    state = _state(use_case_specs=[
        {"use_case_id": "UC1", "issues": [_issue("spec.no-scope-creep")],
         "repair_stopped": "clean"},
    ])

    with telemetry.run_scope("t") as stats:
        out = node(state)

    assert out["redo_route"] == "advance"
    components = [d["component"] for d in stats.as_dict()["degradations"]]
    assert "supervisor.unreachable_target" in components


# ---------------------------------------------------------------------------
# 4. 사이클이 그래프에 보인다
# ---------------------------------------------------------------------------
def test_the_pipeline_can_actually_go_back():
    """되돌아가기는 이 파이프라인의 구조다 — 노드 안에 숨기면 그림이 흐름을 말하지 않는다."""
    graph = build_graph(feedback_gates=False).get_graph()
    edges = {(e.source, e.target) for e in graph.edges}

    assert ("model_use_cases", "supervise_model") in edges
    assert ("write_specifications", "supervise_specs") in edges
    assert ("draw_diagram", "supervise_diagram") in edges
    # 되돌아가는 엣지 — 예전 그래프에는 위로 가는 엣지가 하나도 없었다.
    assert ("supervise_specs", "model_use_cases") in edges
    assert ("supervise_diagram", "write_specifications") in edges
    assert ("supervise_diagram", "model_use_cases") in edges


def test_a_supervisor_cannot_jump_to_a_group_that_has_not_run():
    """아직 돌지 않은 그룹으로 "되돌리면" 산출물 없이 아래가 돈다 — 되돌리기가 아니다."""
    graph = build_graph(feedback_gates=False).get_graph()
    targets = {e.target for e in graph.edges if e.source == "supervise_model"}
    assert "draw_diagram" not in targets
    assert targets == {"model_use_cases", "write_specifications"}


def test_feedback_from_a_person_wins_over_the_machine_instruction():
    """기계가 붙인 지시가 사람 지시를 덮어써서는 안 된다."""
    state = {"stage_feedback": {"actors": "기계 지시"}}
    assert supervisor.feedback_for(state, "actors", "사람 지시") == "사람 지시"
    assert supervisor.feedback_for(state, "actors") == "기계 지시"
    assert supervisor.feedback_for({}, "actors") == ""
