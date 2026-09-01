"""설계 모델이 사람이 보는 PlantUML로 올바르게 투영되는지만 확인한다.

구현 단계는 더 이상 이 문자열을 다시 정규식으로 해석하지 않는다. 따라서 렌더러 내부 문구와
구현 parser를 서로 묶던 검사는 제거하고, 사용자에게 제공하는 다이어그램 계약만 남긴다.
"""
from __future__ import annotations

from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.erd.plantuml import generate_erd_from_bce_json


def test_java_scalar_aliases_are_rendered_in_one_bce_vocabulary() -> None:
    model = {
        "Classes": [{
            "className": "Product",
            "stereotype": "Entity",
            "fields": ["inventoryQuantity : Integer", "price : Decimal"],
            "methods": ["reserve(quantity : Integer, unitPrice : Decimal): Decimal"],
        }],
        "Relationships": [],
    }

    class_puml = generate_plantuml_from_bce_json(model)
    erd_puml = generate_erd_from_bce_json(model)

    assert "inventoryQuantity : int" in class_puml
    assert "price : BigDecimal" in class_puml
    assert "reserve(quantity : int, unitPrice : BigDecimal): BigDecimal" in class_puml
    assert "inventoryQuantity : INT" in erd_puml
    assert "price : DECIMAL(19,4)" in erd_puml


def test_relationships_keep_their_visible_multiplicity_and_kind() -> None:
    model = {
        "Classes": [
            {"className": name, "stereotype": "Entity", "fields": [], "methods": []}
            for name in ("Member", "PremiumMember", "Order", "OrderLine")
        ],
        "Relationships": [
            {"source": "Member", "target": "Order", "type": "Association",
             "sourceMultiplicity": "1", "targetMultiplicity": "*"},
            {"source": "Order", "target": "OrderLine", "type": "Composition",
             "sourceMultiplicity": "1", "targetMultiplicity": "1..*"},
            {"source": "PremiumMember", "target": "Member", "type": "Inheritance"},
        ],
    }

    class_puml = generate_plantuml_from_bce_json(model)
    erd_puml = generate_erd_from_bce_json(model)

    assert 'Member "1" --> "*" Order' in class_puml
    assert "Member <|-- PremiumMember" in class_puml
    assert "Order ||--|{ OrderLine" in erd_puml


def test_multivalued_field_renders_a_traceable_child_table() -> None:
    puml = generate_erd_from_bce_json({
        "Classes": [{
            "className": "Student",
            "stereotype": "Entity",
            "fields": ["completedCourses : List<String>"],
            "methods": [],
        }],
        "Relationships": [],
    })

    assert 'entity "StudentCompletedCourses" as StudentCompletedCourses {' in puml
    assert (
        "' easydep:erd-origin kind=multivalued "
        "alias=StudentCompletedCourses parent=Student field=completedCourses"
    ) in puml
