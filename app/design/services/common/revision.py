"""산출물 종류와 무관한 피드백/문법 수정.

PlantUML이든 JSON이든 artifact_name만 바꿔 같은 흐름으로 고친다 —
사용자 피드백을 반영하고 검증 오류를 수정한 뒤 산출물만 돌려준다.
"""
from __future__ import annotations

import json
from typing import Any

from app.design.services.common.llm_client import call_artifact_llm
from app.design.services.common.parsing import extract_json, extract_puml


def revise_puml_with_llm(
    artifact_name: str,
    current_puml: str,
    feedback: str,
    syntax_errors: list[str] | None = None,
    context: str = "",
) -> str:
    system_prompt = """
You revise PlantUML artifacts. Apply the user's natural-language feedback and
fix any PlantUML syntax errors. Return only PlantUML code from @startuml to
@enduml. Do not explain.
"""
    user_prompt = f"""
[Artifact]
{artifact_name}

[Context]
{context}

[Current PlantUML]
{current_puml}

[User Feedback]
{feedback or "(none)"}

[Syntax Errors]
{chr(10).join(syntax_errors or []) or "(none)"}
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))


def revise_json_with_llm(
    artifact_name: str,
    current_json: dict[str, Any],
    feedback: str,
    errors: list[str] | None = None,
    context: str = "",
) -> dict[str, Any]:
    system_prompt = """
You revise JSON artifacts. Apply the user's natural-language feedback and fix
validation errors. Return only a valid JSON object. Do not explain.
"""
    user_prompt = f"""
[Artifact]
{artifact_name}

[Context]
{context}

[Current JSON]
{json.dumps(current_json, ensure_ascii=False, indent=2)}

[User Feedback]
{feedback or "(none)"}

[Errors]
{chr(10).join(errors or []) or "(none)"}
"""
    return extract_json(call_artifact_llm(system_prompt, user_prompt))
