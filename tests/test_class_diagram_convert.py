"""클래스 다이어그램: 결정론적 변환과 BCE-우선 피드백 흐름 (네트워크 불필요).

BCE→PlantUML 변환이 콘텐츠에 든 PlantUML 구조 문자를 중화해 "구성에 의해" 유효한
다이어그램을 내는지, 그리고 피드백이 PlantUML 텍스트가 아니라 BCE 모델을 편집한 뒤
같은 변환으로 재렌더되는지 확인한다.
"""
from __future__ import annotations

import app.design.nodes.class_diagram as cd_nodes
from app.design.graphs.class_diagram_graph import (
    class_diagram_feedback_graph,
    class_diagram_graph,
)
from app.design.nodes.class_diagram import (
    convert_to_class_diagram_code,
    revise_class_elements,
)
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
    def fake_revise(current_bce, feedback, scenario_text=""):
        return {
            "Classes": [
                {"className": "Renamed", "stereotype": "Entity", "fields": [], "methods": []}
            ],
            "Relationships": [],
        }

    monkeypatch.setattr(cd_nodes, "revise_bce_classes", fake_revise)

    state = {
        "extracted_bce_classes": {"Classes": [{"className": "Old"}], "Relationships": []},
        "class_diagram_feedback": "rename Old to Renamed",
        "usecase_spec": {},
    }
    revised = revise_class_elements(state)
    assert revised["extracted_bce_classes"]["Classes"][0]["className"] == "Renamed"

    merged = {**state, **revised}
    out = convert_to_class_diagram_code(merged)
    assert "class Renamed" in out["class_diagram_puml"]
    assert "class Old" not in out["class_diagram_puml"]


def test_generation_graph_has_no_repair_node():
    nodes = set(class_diagram_graph.get_graph().nodes)
    assert "extract_class_elements" in nodes
    assert "convert_to_class_diagram_code" in nodes
    assert "repair_class_diagram_syntax" not in nodes


def test_feedback_graph_edits_model_not_text():
    nodes = set(class_diagram_feedback_graph.get_graph().nodes)
    assert "revise_class_elements" in nodes
    assert "convert_to_class_diagram_code" in nodes
