"""앞선 산출물 전부(시나리오·클래스·시퀀스·API·ERD)에서 배포 다이어그램 PlantUML을 생성한다."""
from __future__ import annotations

import json
from typing import Any

from app.design.services.common.llm_client import call_artifact_llm
from app.design.services.common.parsing import extract_puml


def generate_deployment_diagram_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
    api_spec: dict[str, Any],
    erd_puml: str,
) -> str:
    system_prompt = """
You generate PlantUML deployment diagrams. Return only PlantUML code from
@startuml to @enduml.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

[Sequence Diagram PlantUML]
{sequence_diagram_puml}

[API Spec JSON]
{json.dumps(api_spec, ensure_ascii=False, indent=2)}

[ERD PlantUML]
{erd_puml}

Generate a deployment diagram after the ERD has been completed.
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))
