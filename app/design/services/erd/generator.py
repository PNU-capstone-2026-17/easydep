"""유스케이스 시나리오·클래스 다이어그램·API 명세에서 ERD PlantUML을 생성한다."""
from __future__ import annotations

import json
from typing import Any

from app.design.services.common.llm_client import call_artifact_llm
from app.design.services.common.parsing import extract_puml


def generate_erd_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
    api_spec: dict[str, Any],
) -> str:
    system_prompt = """
You generate PlantUML ERD diagrams. Return only PlantUML code from @startuml to
@enduml.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

[API Spec JSON]
{json.dumps(api_spec, ensure_ascii=False, indent=2)}

Generate an ERD after the API specification has been completed.
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))
