"""유스케이스 시나리오와 클래스 다이어그램에서 시퀀스 다이어그램 PlantUML을 생성한다."""
from __future__ import annotations

from app.design.services.common.llm_client import call_artifact_llm
from app.design.services.common.parsing import extract_puml


def generate_sequence_diagram_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
) -> str:
    system_prompt = """
You generate PlantUML sequence diagrams from use case scenarios and class
diagrams. Return only PlantUML code from @startuml to @enduml.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

Generate the sequence diagram after the class diagram has been completed.
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))
