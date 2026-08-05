"""시퀀스 다이어그램: 결정론적 변환과 요소-우선 피드백 흐름 (네트워크 불필요).

요소→PlantUML 변환이 콘텐츠에 든 PlantUML 구조 문자를 중화해 "구성에 의해" 유효한
다이어그램을 내는지, 그리고 피드백이 PlantUML 텍스트가 아니라 요소 모델을 편집한 뒤
같은 변환으로 재렌더되는지 확인한다.
"""
from __future__ import annotations

import app.design.nodes.sequence_diagram as seq_nodes
from app.design.graphs.sequence_diagram_graph import (
    sequence_diagram_feedback_graph,
    sequence_diagram_graph,
)
from app.design.nodes.sequence_diagram import (
    convert_to_sequence_diagram_code,
    revise_sequence_elements,
)
from app.design.services.sequence_diagram.plantuml import (
    generate_plantuml_from_sequence_json,
)


def test_sequence_conversion_neutralizes_plantuml_structural_chars():
    elements = {
        "participants": [
            {"type": "actor", "label": "User {VIP}", "alias": "User!"},
            {"type": "control", "label": "Auth Service", "alias": "Auth"},
        ],
        "sequence": [
            {
                "type": "message",
                "source": "User!",
                "target": "Auth",
                "text": 'login("admin") {pass}',
            },
            {
                "type": "return_message",
                "source": "Auth",
                "target": "User!",
                "text": "token {jwt}",
            },
        ],
    }
    puml = generate_plantuml_from_sequence_json(elements)

    assert puml.startswith("@startuml")
    assert puml.rstrip().endswith("@enduml")
    assert 'actor "User (VIP)" as User_' in puml
    assert "User_ -> Auth : login('admin') (pass)" in puml
    assert "Auth --> User_ : token (jwt)" in puml


def test_empty_sequence_model_yields_empty_diagram():
    assert generate_plantuml_from_sequence_json({}) == ""
    assert generate_plantuml_from_sequence_json({"participants": [], "sequence": []}) == ""


def test_sequence_feedback_edits_elements_then_reconverts(monkeypatch):
    def fake_revise(current_elements, feedback, scenario_text="", class_diagram_puml=""):
        return {
            "participants": [
                {"type": "actor", "label": "Admin", "alias": "Admin"},
            ],
            "sequence": [],
        }

    monkeypatch.setattr(seq_nodes, "revise_seq_elements", fake_revise)

    state = {
        "extracted_sequence_elements": {
            "participants": [{"type": "actor", "label": "User", "alias": "User"}],
            "sequence": [],
        },
        "sequence_diagram_feedback": "change actor to Admin",
        "usecase_spec": {},
    }
    revised = revise_sequence_elements(state)
    assert revised["extracted_sequence_elements"]["participants"][0]["label"] == "Admin"

    merged = {**state, **revised}
    out = convert_to_sequence_diagram_code(merged)
    assert 'actor "Admin" as Admin' in out["sequence_diagram_puml"]
    assert 'actor "User"' not in out["sequence_diagram_puml"]


def test_sequence_generation_graph_structure():
    nodes = set(sequence_diagram_graph.get_graph().nodes)
    assert "extract_sequence_elements" in nodes
    assert "convert_to_sequence_diagram_code" in nodes
    assert "validate_sequence_diagram_syntax" in nodes


def test_sequence_feedback_graph_edits_model_not_text():
    nodes = set(sequence_diagram_feedback_graph.get_graph().nodes)
    assert "revise_sequence_elements" in nodes
    assert "convert_to_sequence_diagram_code" in nodes
    assert "validate_sequence_diagram_syntax" in nodes
