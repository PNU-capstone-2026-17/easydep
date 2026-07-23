"""유스케이스 시나리오·클래스·시퀀스 다이어그램에서 OpenAPI 3.1 명세(JSON)를 생성한다."""
from __future__ import annotations

from typing import Any

from app.design.services.common.llm_client import call_artifact_llm
from app.design.services.common.parsing import extract_json


def generate_api_spec_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> dict[str, Any]:
    system_prompt = """
You generate OpenAPI 3.1 JSON specifications. Return only a valid JSON object.
Do not wrap it in markdown.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

[Sequence Diagram PlantUML]
{sequence_diagram_puml}

Generate an OpenAPI 3.1 JSON specification after the sequence diagram has been
completed.
"""
    return extract_json(call_artifact_llm(system_prompt, user_prompt))
