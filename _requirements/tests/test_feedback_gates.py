"""대화형 피드백 게이트(정적 라우팅) 테스트: gate_route 마커 + 서브그래프 토폴로지."""
from app.agent import graph as g
from app.agent import subgraphs as sg
from app.agent.steps import feedback_gates as fg


def test_gate_advances_on_empty_feedback(monkeypatch):
    monkeypatch.setattr(fg, "interrupt", lambda payload: "")   # 빈 피드백 → advance
    upd = fg.gate_use_cases({"use_cases": [{"name": "A"}]})
    assert upd["gate_route"] == "advance"


def test_requirements_gate_advances_on_empty(monkeypatch):
    monkeypatch.setattr(fg, "interrupt", lambda payload: "")
    assert fg.gate_requirements({"classified": []})["gate_route"] == "advance"


def test_requirements_gate_reclassifies_and_loops(monkeypatch):
    monkeypatch.setattr(fg, "interrupt", lambda payload: "R3 should be NFR")
    monkeypatch.setattr(fg, "classify", lambda state, feedback="": {"classified": [{"id": "R1"}], "_fb": feedback})

    upd = fg.gate_requirements({"classified": []})
    assert upd["gate_route"] == "loop"                         # 재분류 후 게이트로 루프백
    assert upd["_fb"] == "R3 should be NFR"                    # 피드백이 재분류에 전달됨


def test_gate_regenerates_and_loops_on_feedback(monkeypatch):
    # 게이트는 의도 분류 엔진(apply_feedback_upto)으로 위임한다.
    monkeypatch.setattr(fg, "interrupt", lambda payload: "merge cart use cases")
    seen = {}

    def fake_apply(state, feedback, up_to):
        seen["feedback"] = feedback
        seen["up_to"] = up_to
        state["use_cases"] = [{"id": "UC1", "name": "X"}]
        state["coverage"] = {"coverage_ratio": 1.0}
        return None, []

    monkeypatch.setattr(fg, "apply_feedback_upto", fake_apply)

    upd = fg.gate_use_cases({"use_cases": [{"name": "A"}], "classified": []})

    assert upd["gate_route"] == "loop"                         # 재생성 후 게이트로 루프백
    assert seen["feedback"] == "merge cart use cases"          # 피드백이 분류 엔진에 전달됨
    assert seen["up_to"] == "coverage"                         # use_cases 게이트는 coverage까지만 cascade
    assert upd["use_cases"] == [{"id": "UC1", "name": "X"}]
    assert upd["coverage"]["coverage_ratio"] == 1.0


def test_specs_and_relationship_gates_advance(monkeypatch):
    monkeypatch.setattr(fg, "interrupt", lambda payload: "")
    assert fg.gate_specs({"use_case_specs": []})["gate_route"] == "advance"
    assert fg.gate_relationships({"relationships": {}})["gate_route"] == "advance"


def test_relationship_gate_loops_and_rerenders(monkeypatch):
    monkeypatch.setattr(fg, "interrupt", lambda payload: "add authenticate include")

    def fake_apply(state, feedback, up_to):
        state["relationships"] = {"includes": [1]}
        state["diagram"] = "@startuml\n@enduml"
        return None, []

    monkeypatch.setattr(fg, "apply_feedback_upto", fake_apply)
    monkeypatch.setattr(fg, "check_specs", lambda state: {})
    monkeypatch.setattr(fg, "check_relationships", lambda state: {"relationship_report": {}})

    upd = fg.gate_relationships({"relationships": {}})
    assert upd["gate_route"] == "loop"
    assert upd["diagram"] == "@startuml\n@enduml"
    assert upd["relationships"] == {"includes": [1]}


_STAGE_NODES = ("refine_requirements", "model_use_cases", "write_specifications", "draw_diagram")


def test_top_graph_is_composed_of_stage_subgraphs():
    # 상위 그래프는 gated 유무와 무관하게 단계별 동작 이름의 서브그래프 노드로 구성된다(정적 계층).
    for gated in (False, True):
        nodes = g.build_graph(feedback_gates=gated).get_graph().nodes
        assert all(stage in nodes for stage in _STAGE_NODES)


def test_gate_nodes_present_only_when_gated():
    # 게이트 노드는 부모 그래프에 gated=True일 때만 존재(정적 토폴로지 분기).
    gates = ("gate_requirements", "gate_use_cases", "gate_specs", "gate_relationships")
    off = g.build_graph(feedback_gates=False).get_graph().nodes
    assert not any(gt in off for gt in gates)
    on = g.build_graph(feedback_gates=True).get_graph().nodes
    assert all(gt in on for gt in gates)


def test_stage_subgraphs_have_no_gate_nodes():
    # 게이트는 부모 레벨 전용 — 스테이지 서브그래프는 순수 작업 노드만 가진다.
    s1 = sg.build_refine_requirements().get_graph().nodes
    assert "clarify" in s1 and "classify" in s1
    assert not any(str(n).startswith("gate_") for n in s1)
