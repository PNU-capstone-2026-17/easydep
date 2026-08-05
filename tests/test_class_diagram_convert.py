"""클래스 다이어그램: 결정론적 변환과 BCE-우선 피드백 흐름 (네트워크 불필요).

BCE→PlantUML 변환이 콘텐츠에 든 PlantUML 구조 문자를 중화해 "구성에 의해" 유효한
다이어그램을 내는지, 그리고 피드백이 PlantUML 텍스트가 아니라 BCE 모델을 편집한 뒤
같은 변환으로 재렌더되는지 확인한다.
"""
from __future__ import annotations

import app.design.graphs.subgraphs as sg
from app.design.graphs.subgraphs import CLASS_DIAGRAM_SPEC, DESIGN_SUBGRAPHS
from app.design.nodes.artifact import render_node, revise_node
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json


def test_conversion_neutralizes_plantuml_structural_chars():
    bce = {
        "Classes": [
            {
                "className": "Payment Service!",
                "stereotype": "<<Control>>",
                "description": "handles pay }break",
                "fields": ["amount: {currency}"],
                "methods": ['pay(): {status}', 'quote("x")'],
            }
        ],
        "Relationships": [],
    }
    puml = generate_plantuml_from_bce_json(bce)

    # Exactly one class body: braces stay balanced despite braces in content.
    assert puml.count("{") == 1
    assert puml.count("}") == 1
    assert puml.startswith("@startuml")
    assert puml.rstrip().endswith("@enduml")
    # Class name is reduced to identifier characters.
    assert "class Payment_Service_" in puml


def test_empty_model_yields_empty_diagram():
    assert generate_plantuml_from_bce_json({}) == ""
    assert generate_plantuml_from_bce_json({"Classes": [], "Relationships": []}) == ""


def test_feedback_edits_bce_then_reconverts(monkeypatch):
    def fake_revise(current_bce, feedback, scenario_text="", targets=None):
        return {
            "Classes": [
                {"className": "Renamed", "stereotype": "Entity", "fields": [], "methods": []}
            ],
            "Relationships": [],
        }

    monkeypatch.setattr(sg, "revise_bce_classes", fake_revise)

    state = {
        "extracted_bce_classes": {"Classes": [{"className": "Old"}], "Relationships": []},
        "class_diagram_feedback": "rename Old to Renamed",
        "usecase_spec": {},
    }
    revised = revise_node(CLASS_DIAGRAM_SPEC)(state)
    assert revised["extracted_bce_classes"]["Classes"][0]["className"] == "Renamed"

    merged = {**state, **revised}
    out = render_node(CLASS_DIAGRAM_SPEC)(merged)
    assert "class Renamed" in out["class_diagram_puml"]
    assert "class Old" not in out["class_diagram_puml"]


def test_generation_graph_never_feeds_syntax_errors_back_into_the_model():
    """**문법** 수리 루프가 없다 — 검증이 마지막이고 그 뒤로 아무것도 오지 않는다.

    예전에는 LLM이 PlantUML 텍스트를 직접 쓰고, 문법 오류를 되먹여 다시 쓰게 했다.
    지금은 변환이 입력을 중화해 구성에 의해 유효한 PlantUML을 내므로 되먹일 오류가 없다.

    ⚠ 이 테스트는 예전에 "'repair'라는 이름의 노드가 없다"만 봤다. 그건 지키려던 것보다
    넓게 읽혔다 — 2026-08-04에 더해진 의미 검사 노드(`check_class_diagram`)는 문법이 아니라
    **모델의 내용**을 보고, 재생성은 산출물 텍스트가 아니라 BCE 모델을 고친다. 그래서
    여기서 보는 것을 "검증이 종점인가"로 좁혔다.
    """
    graph = DESIGN_SUBGRAPHS["class_diagram"]["generate"].get_graph()
    nodes = set(graph.nodes)
    assert "extract_class_diagram" in nodes
    assert "render_class_diagram" in nodes
    assert not any("repair" in node for node in nodes)

    # 렌더(+자기검사)에서 나가는 길은 END 하나뿐이다. 다른 노드로 돌아가면 그게 수리 루프다.
    after_render = [
        edge.target for edge in graph.edges if edge.source == "render_class_diagram"
    ]
    assert after_render == ["__end__"]


def test_feedback_graph_edits_model_not_text():
    nodes = set(DESIGN_SUBGRAPHS["class_diagram"]["feedback"].get_graph().nodes)
    assert "revise_class_diagram" in nodes
    assert "render_class_diagram" in nodes


def test_render_node_records_the_artifact_and_its_own_verdict_together():
    """렌더와 그 자기검사가 한 노드에서 함께 나온다.

    예전에는 노드가 둘이었다(`convert` → `validate`). 나눠 둔 값이 없었다 — 문법 검증은
    변환의 출력만 보고, 변환이 sanitize 로 구성에 의해 유효한 산출물을 내므로 **원리상
    실패할 수 없다.** 절대 울리지 않는 노드가 그래프에 다섯 개 떠 있는 것보다, 노드가
    자기 출력을 스스로 검사하는 편이 그림과 실제가 맞는다.
    """
    spec = CLASS_DIAGRAM_SPEC
    out = render_node(spec)(
        {spec.model_key: {"Classes": [{"className": "Order", "stereotype": "Entity"}],
                          "Relationships": []}}
    )

    assert {spec.content_key, spec.valid_key, spec.errors_key} == set(out)
    assert "class Order" in out[spec.content_key]
    assert out[spec.valid_key] is True
    assert out[spec.errors_key] == []


def test_render_and_validate_is_the_same_computation_the_graph_uses():
    """지목 수정(`cascade.py`)과 그래프가 **같은 함수**를 쓴다.

    예전에는 렌더+검증 네 줄이 세 곳에 흩어져 있었다(노드 둘, cascade 둘). 갈라지면
    경로마다 다른 값이 저장되는데, 어느 쪽이 화면에 뜨는지는 사용자가 무엇을 눌렀는지에
    달리게 된다.
    """
    from app.design.nodes.artifact import render_and_validate

    spec = CLASS_DIAGRAM_SPEC
    model = {"Classes": [{"className": "Order", "stereotype": "Entity"}], "Relationships": []}

    assert render_and_validate(spec, model) == render_node(spec)({spec.model_key: model})
