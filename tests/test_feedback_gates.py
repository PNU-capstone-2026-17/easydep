"""대화형 피드백 게이트(정적 라우팅) 테스트: gate_route 마커 + 서브그래프 토폴로지."""
from app.requirements.agent import graph as g
from app.requirements.agent import subgraphs as sg
from app.requirements.agent.steps import feedback_gates as fg
from app.requirements.schemas import FeedbackEdit


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


def test_gate_offers_the_material_for_a_structured_edit(monkeypatch):
    """게이트가 "어느 단계의 어느 항목을 고를 수 있는지"를 화면에 알려준다."""
    asked = {}
    monkeypatch.setattr(fg, "interrupt", lambda payload: asked.update(payload) or "")

    fg.gate_use_cases({"use_cases": [{"id": "UC1", "name": "A"}, {"id": "UC2", "name": "B"}]})
    assert asked["edit_stage"] == "use_cases"
    assert asked["edit_targets"] == ["UC1", "UC2"]

    asked.clear()
    fg.gate_specs({"use_case_specs": [{"use_case_id": "UC1"}]})
    assert asked["edit_stage"] == "specs"
    assert asked["edit_targets"] == ["UC1"]

    # 관계는 항목 단위로 고를 수 없다 → broad만.
    asked.clear()
    fg.gate_relationships({"relationships": {}})
    assert asked["edit_stage"] == "relationships"
    assert asked["edit_targets"] == []

    # step1은 재생성할 단계 선택지가 없다(BERT 단독 결정론).
    asked.clear()
    fg.gate_requirements({"classified": []})
    assert asked["edit_stage"] is None


def test_gate_passes_a_structured_edit_through_untouched(monkeypatch):
    """구조화 편집이 문자열로 뭉개지면 분류기가 다시 돌게 된다."""
    edit = FeedbackEdit(
        stage="use_cases", scope="local", target_ids=["UC2"], instruction="장바구니 UC를 합쳐줘"
    )
    monkeypatch.setattr(fg, "interrupt", lambda payload: edit)
    seen = {}

    def fake_apply(state, feedback, up_to):
        seen["feedback"] = feedback
        return None, []

    monkeypatch.setattr(fg, "apply_feedback_upto", fake_apply)
    upd = fg.gate_use_cases({"use_cases": [{"id": "UC1", "name": "A"}], "classified": []})

    assert upd["gate_route"] == "loop"
    assert seen["feedback"] is edit          # str()로 뭉개지 않는다


def test_structured_edit_with_a_blank_instruction_advances(monkeypatch):
    """지시가 비어 있으면 '다음 단계로'와 같다."""
    edit = FeedbackEdit(stage="use_cases", instruction="   ")
    monkeypatch.setattr(fg, "interrupt", lambda payload: edit)
    upd = fg.gate_use_cases({"use_cases": [{"id": "UC1", "name": "A"}]})
    assert upd["gate_route"] == "advance"


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
    s1 = sg.build_stage("refine_requirements").get_graph().nodes
    assert "clarify" in s1 and "classify" in s1
    assert not any(str(n).startswith("gate_") for n in s1)


# --- 되묻기의 왕복(게이트 쪽) ------------------------------------------------
def test_the_gate_carries_the_resource_questions(monkeypatch):
    """질문이 게이트 payload에 실리지 않으면 사용자에게 영영 안 보인다."""
    from app.requirements.agent.steps import feedback_gates as fg

    seen: dict = {}

    def fake_interrupt(payload):
        seen.update(payload)
        return ""            # 빈 답 = 진행

    monkeypatch.setattr(fg, "interrupt", fake_interrupt)
    state = {"classified": [], "resource_intake": {"questions": [
        {"field": "provider", "kind": "missing", "why": "w", "question": "q"},
    ]}}
    assert fg.gate_requirements(state) == {"gate_route": "advance"}  # type: ignore[arg-type]
    assert seen["resource_questions"][0]["field"] == "provider"


def test_a_resource_answer_does_not_reclassify_requirements(monkeypatch):
    """사용자는 질문에 답했을 뿐이다. 재분류를 돌리면 요구사항이 덩달아 흔들린다."""
    from app.requirements.agent.steps import feedback_gates as fg
    from app.requirements.schemas import ResourceAnswer

    def boom(*_a, **_k):
        raise AssertionError("되묻기의 답으로 classify가 돌면 안 된다")

    monkeypatch.setattr(fg, "classify", boom)
    monkeypatch.setattr(fg, "interrupt",
                        lambda _p: ResourceAnswer(answers={"provider": "aws"}))

    state = {"classified": [], "resource_answers": {"region": "Seoul"}}
    out = fg.gate_requirements(state)  # type: ignore[arg-type]

    # 관심사 커버리지를 다시 돌리지 않는 경로로 간다(입력이 안 바뀌었다).
    assert out["gate_route"] == "answers"
    # 앞서 답한 것과 **합쳐진다** — 한 칸씩 답해도 앞의 답이 사라지지 않는다.
    assert out["resource_answers"] == {"region": "Seoul", "provider": "aws"}


def test_an_all_blank_resource_answer_advances(monkeypatch):
    """모르는 칸 하나가 세션을 게이트에 영원히 묶어 두면 안 된다."""
    from app.requirements.agent.steps import feedback_gates as fg
    from app.requirements.schemas import ResourceAnswer

    monkeypatch.setattr(fg, "interrupt",
                        lambda _p: ResourceAnswer(answers={"provider": "  "}))
    assert fg.gate_requirements({"classified": []})["gate_route"] == "advance"  # type: ignore[arg-type]


def test_a_resource_answer_never_becomes_natural_language_feedback():
    """물어보지 않은 게이트로 흘러들면 pydantic 표현이 피드백 문장이 된다."""
    import pytest

    from app.requirements.agent.steps import feedback_gates as fg
    from app.requirements.schemas import ResourceAnswer

    with pytest.raises(TypeError):
        fg._as_text(ResourceAnswer(answers={"provider": "aws"}))
