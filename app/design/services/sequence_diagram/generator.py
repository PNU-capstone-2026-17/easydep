"""유스케이스 시나리오와 클래스 다이어그램에서 시퀀스 다이어그램 PlantUML을 생성한다."""
from __future__ import annotations

from app.design.services.sequence_diagram.extractor import (
    extract_sequence_elements_from_scenario,
)
from app.design.services.sequence_diagram.plantuml import (
    generate_plantuml_from_sequence_json,
)


def generate_sequence_diagram_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
) -> str:
    elements = extract_sequence_elements_from_scenario(
        scenario_text,
        class_diagram_puml,
    )
    return generate_plantuml_from_sequence_json(elements)
